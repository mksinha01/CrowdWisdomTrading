"""Tool surface — ad pattern extraction and beat sheet aggregation (Story S20).

Provides:
- extract_ad_patterns: Extract hook, pain, concept, and beat sheet per ad, aggregate
  the median timeline, and write artifacts/ad_patterns.json.
- aggregate_beat_timeline: Recompute only the aggregate block without LLM calls,
  rewriting artifacts/ad_patterns.json in place.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import statistics
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from cwt.clients.llm import (
    ArtifactValidationError,
    BudgetExceeded,
    LLMClient,
    Tier,
)
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactError, ArtifactStore
from cwt.domain.beats import (
    aggregate_beat_timeline as domain_aggregate_beat_timeline,
)
from cwt.domain.beats import (
    archetype_distribution,
    compute_durability,
    median_hook_duration,
    underused_high_durability,
)
from cwt.domain.models import (
    Ad,
    AdPattern,
    AdPatterns,
    BeatEntry,
    HookPattern,
    PatternAggregate,
    WinningAds,
    _CwtBaseModel,
)
from cwt.prompts.extract import (
    build_beat_sheet_prompt,
    build_extraction_prompt,
)
from cwt.util.paths import RunPaths

logger = logging.getLogger("cwt.tools.patterns")


class AdExtractionOutput(_CwtBaseModel):
    """Structured extraction response from AD_EXTRACTION_PROMPT."""

    hook: HookPattern
    pain: str
    concept: str
    proof_type: str = "none"


class BeatSheetOutput(_CwtBaseModel):
    """Structured decomposition response from BEAT_SHEET_PROMPT."""

    beats: list[BeatEntry]


def _run_async(coro: Any) -> Any:
    """Run an async coroutine synchronously, even if an event loop is already active."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


async def _extract_one(
    client: LLMClient,
    ad: Ad,
    *,
    sem: asyncio.Semaphore,
    on_call: Callable[[], None] | None = None,
) -> AdPattern:
    """Extract hook, pain, concept and optional beat sheet for a single ad.

    Rule A3: Every extraction goes through complete_validated.
    Rule A4: Bounded by semaphore.
    Rule A5: CHEAP tier.
    """
    async with sem:
        if on_call:
            on_call()

        ext_prompt = build_extraction_prompt(ad.body_text, ad.active_days)
        ext_res = await client.complete_validated(
            tier=Tier.CHEAP,
            messages=[{"role": "user", "content": ext_prompt}],
            schema=AdExtractionOutput,
            stage="patterns_extract",
        )

        # Allow mocks/subclasses returning AdPattern directly
        if isinstance(ext_res, AdPattern):
            if ad.video_duration_s is None or float(ad.video_duration_s) <= 0:
                return ext_res.model_copy(update={"beat_sheet": []})
            return ext_res

        if isinstance(ext_res, dict):
            ext_res = AdExtractionOutput.model_validate(ext_res)

        hook = ext_res.hook
        pain = ext_res.pain
        concept = ext_res.concept

        beat_sheet: list[BeatEntry] = []
        if ad.video_duration_s is not None and float(ad.video_duration_s) > 0:
            if on_call:
                on_call()

            bs_prompt = build_beat_sheet_prompt(ad.body_text, float(ad.video_duration_s))
            bs_res = await client.complete_validated(
                tier=Tier.CHEAP,
                messages=[{"role": "user", "content": bs_prompt}],
                schema=BeatSheetOutput,
                stage="patterns_beat_sheet",
            )
            if isinstance(bs_res, BeatSheetOutput):
                beat_sheet = bs_res.beats
            elif isinstance(bs_res, dict) and "beats" in bs_res:
                beat_sheet = [BeatEntry.model_validate(b) for b in bs_res["beats"]]

        try:
            return AdPattern(
                ad_id=getattr(ad, "ad_id", getattr(ad, "id", "")),
                hook=hook,
                pain=pain,
                concept=concept,
                beat_sheet=beat_sheet,
            )
        except ValidationError as exc:
            raise ArtifactValidationError(
                stage="patterns_extract",
                schema="AdPattern",
                error=exc,
            ) from exc


def _aggregate(
    patterns: list[AdPattern],
    ads: list[Ad],
    *,
    total_duration_s: float,
    warnings: list[str] | None = None,
) -> PatternAggregate:
    """Aggregate individual ad patterns into median timeline and archetype distribution.

    Delegates arithmetic and contiguity repair to domain/beats.py.
    """
    sheets = [p.beat_sheet for p in patterns if p.beat_sheet]
    timeline, tl_diag = domain_aggregate_beat_timeline(sheets, total_duration_s=total_duration_s)
    if warnings is not None:
        for w in tl_diag.get("warnings", []):
            if w not in warnings:
                warnings.append(w)

    hooks = [p.hook for p in patterns if p.hook is not None]
    dist = archetype_distribution(hooks)
    dur_table, dur_diag = compute_durability(hooks)
    if warnings is not None:
        for w in dur_diag.get("warnings", []):
            if w not in warnings:
                warnings.append(w)

    underused = underused_high_durability(dist, dur_table)
    median_hook_s = median_hook_duration(sheets)

    return PatternAggregate(
        ad_count=len(patterns),
        archetype_distribution=dist,
        median_hook_duration_s=median_hook_s,
        median_beat_timeline=timeline,
        underused_high_durability=underused,
    )


def extract_ad_patterns(
    *,
    settings: Settings,
    paths: RunPaths,
    concurrency: int | None = None,
    client: LLMClient | None = None,
) -> dict:
    """One LLM extraction per ad (CHEAP tier, parallel), then aggregate.

    WRITES artifacts/ad_patterns.json
    RETURNS {"artifact_path", "ads_analysed", "archetypes",
             "median_hook_s", "underused_boosted", "llm_calls"}

    CALL THIS: on the `patterns` card, after `cwt_rank_winning_ads`. Makes ~24 cheap-tier LLM calls.

    WHEN NOT TO CALL: do not call it per-ad or to "top up" a partial result — it is a batch
    operation over the whole ranked set. Call `cwt_aggregate_beat_timeline` instead if you only need
    the aggregate recomputed.
    """
    paths.ensure()
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    winning_ads = store.read("winning_ads")
    if not isinstance(winning_ads, WinningAds):
        raise ArtifactError(f"Expected WinningAds artifact, got {type(winning_ads).__name__}")

    # Build step 1: Need at least 3 ads to compute a meaningful median
    if len(winning_ads.ads) < 3:
        raise ValueError(
            f"winning_ads contains {len(winning_ads.ads)} ads, but at least 3 are required "
            f"to extract patterns and compute a meaningful median timeline."
        )

    actual_concurrency = concurrency if concurrency is not None else settings.llm_max_concurrency
    if client is None:
        api_key = (
            settings.openrouter_api_key
            if settings.llm_provider == "openrouter"
            else settings.nvidia_api_key
        )
        client = LLMClient(
            provider=settings.llm_provider,
            api_key=api_key,
            model_cheap=settings.model_cheap,
            model_strong=settings.model_strong,
            fallbacks=settings.model_fallbacks,
            max_concurrency=actual_concurrency,
            max_usd=settings.max_usd,
            ledger_path=paths.ledger,
            timeout_s=settings.llm_timeout_seconds,
            repair_attempts=settings.llm_json_repair_attempts,
        )

    sem = asyncio.Semaphore(actual_concurrency)
    warnings: list[str] = []
    llm_call_count = 0

    def _on_call() -> None:
        nonlocal llm_call_count
        llm_call_count += 1

    async def _extract_ad_safe(ad: Ad) -> AdPattern | None:
        ad_id = getattr(ad, "ad_id", getattr(ad, "id", "unknown"))
        if ad.video_duration_s is None or float(ad.video_duration_s) <= 0:
            warnings.append(f"beat_sheet_skipped:no_duration:{ad_id}")

        try:
            return await _extract_one(client, ad, sem=sem, on_call=_on_call)
        except BudgetExceeded:
            # Rule A6: Budget is enforced inside client; BudgetExceeded propagates
            raise
        except (ArtifactValidationError, ValidationError, Exception) as exc:
            logger.warning("Extraction failed for ad %s: %s", ad_id, exc)
            warnings.append(f"extraction_failed:{ad_id}:{exc}")
            return None

    async def _run_batch() -> list[AdPattern]:
        tasks = [_extract_ad_safe(ad) for ad in winning_ads.ads]
        results = await asyncio.gather(*tasks)
        return [p for p in results if p is not None]

    patterns = _run_async(_run_batch())

    if not patterns:
        raise RuntimeError("All ad extractions failed; cannot construct ad_patterns.json")

    # Build step 5: Determine total_duration_s from median of ads' own durations
    durations = [
        float(ad.video_duration_s)
        for ad in winning_ads.ads
        if ad.video_duration_s is not None and float(ad.video_duration_s) > 0
    ]
    if durations:
        total_duration_s = round(float(statistics.median(durations)), 2)
    else:
        total_duration_s = float(settings.video_max_seconds)
        warnings.append(
            f"total_duration_s defaulted to {total_duration_s}s (settings.video_max_seconds): "
            f"no ad carries usable video_duration_s"
        )

    # Build step 4: Aggregate patterns
    aggregate = _aggregate(
        patterns,
        winning_ads.ads,
        total_duration_s=total_duration_s,
        warnings=warnings,
    )

    source_ads_path = store.path_for("winning_ads")
    source_ads_sha256 = store.sha256_of(source_ads_path)

    ad_patterns_model = AdPatterns(
        source_ads_sha256=source_ads_sha256,
        patterns=patterns,
        aggregate=aggregate,
        warnings=warnings,
    )

    out_path = store.write(
        "ad_patterns",
        ad_patterns_model,
        inputs={"winning_ads": source_ads_path},
    )

    return {
        "artifact_path": str(out_path),
        "ads_analysed": aggregate.ad_count,
        "archetypes": len(aggregate.archetype_distribution),
        "median_hook_s": aggregate.median_hook_duration_s,
        "underused_boosted": aggregate.underused_high_durability,
        "llm_calls": llm_call_count,
    }


def aggregate_beat_timeline(*, settings: Settings, paths: RunPaths) -> dict:
    """Recompute only the aggregate block. Cheap, no LLM.

    REWRITES artifacts/ad_patterns.json in place.

    CALL THIS: when you only need the aggregate block recomputed without re-spending
    LLM calls — useful when a tolerance looks wrong during verification.

    WHEN NOT TO CALL: do not call this before `extract_ad_patterns` has written
    `ad_patterns.json`. Do not call this if you need to extract new ad patterns from winning ads.
    """
    paths.ensure()
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    ad_patterns = store.read("ad_patterns")
    if not isinstance(ad_patterns, AdPatterns):
        raise ArtifactError(f"Expected AdPatterns artifact, got {type(ad_patterns).__name__}")

    ads: list[Ad] = []
    source_ads_path = store.path_for("winning_ads")
    inputs = None
    if source_ads_path.is_file():
        try:
            winning_ads = store.read("winning_ads")
            if isinstance(winning_ads, WinningAds):
                ads = winning_ads.ads
                inputs = {"winning_ads": source_ads_path}
        except Exception as exc:
            logger.warning("Could not read winning_ads for duration reference: %s", exc)

    durations = [
        float(ad.video_duration_s)
        for ad in ads
        if ad.video_duration_s is not None and float(ad.video_duration_s) > 0
    ]
    warnings = list(ad_patterns.warnings)
    if durations:
        total_duration_s = round(float(statistics.median(durations)), 2)
    else:
        total_duration_s = float(settings.video_max_seconds)
        default_warn = (
            f"total_duration_s defaulted to {total_duration_s}s (settings.video_max_seconds): "
            f"no ad carries usable video_duration_s"
        )
        if default_warn not in warnings:
            warnings.append(default_warn)

    new_aggregate = _aggregate(
        ad_patterns.patterns,
        ads,
        total_duration_s=total_duration_s,
        warnings=warnings,
    )

    ad_patterns.aggregate = new_aggregate
    ad_patterns.warnings = warnings

    out_path = store.write("ad_patterns", ad_patterns, inputs=inputs)

    return {
        "artifact_path": str(out_path),
        "ads_analysed": new_aggregate.ad_count,
        "archetypes": len(new_aggregate.archetype_distribution),
        "median_hook_s": new_aggregate.median_hook_duration_s,
        "underused_boosted": new_aggregate.underused_high_durability,
        "llm_calls": 0,
    }
