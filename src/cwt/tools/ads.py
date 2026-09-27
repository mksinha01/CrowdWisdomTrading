"""Tool surface — winning ads sourcing and ranking (Story S19).

Provides:
- source_winning_ads: Run Apify Meta Ads scraper, normalise, filter to window,
  dedupe, and write winning_ads.json artifact.
- rank_winning_ads: Score and sort ads by longevity, platform breadth, and recency,
  rewriting winning_ads.json in place.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import date, timedelta
import hashlib
import logging
import math
from pathlib import Path
from typing import Any

from cwt.clients.apify import (
    build_input,
    normalise_actor_id,
    normalise_ad,
    run_actor,
)
from cwt.clients.http_cache import HttpCache
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactError, ArtifactStore
from cwt.domain.models import (
    Ad,
    AdQuery,
    AdRanking,
    AdSource,
    WinningAds,
)
from cwt.util.jsonio import now_iso
from cwt.util.paths import RunPaths

logger = logging.getLogger("cwt.tools.ads")

DEFAULT_KEYWORDS: list[str] = [
    "trading signals",
    "stock market alerts",
    "forex signals",
    "options flow",
]
DEFAULT_COUNTRIES: list[str] = ["US", "GB", "IN"]
DEFAULT_WINDOW_DAYS: int = 30

DEFAULT_WEIGHTS: dict[str, float] = {
    "active_days": 0.45,
    "is_active": 0.25,
    "platform_breadth": 0.15,
    "recency": 0.15,
}


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


def source_winning_ads(
    *,
    settings: Settings,
    paths: RunPaths,
    keywords: list[str] | None = None,
    countries: list[str] | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    max_items: int | None = None,
    cache: HttpCache | None = None,
    today: date | None = None,
) -> dict:
    """Source winning ads from Meta Ads Library via Apify.

    CALL THIS: first, on the `ads` card, once per run. Spends Apify credits — capped
    per run by `maxTotalChargeUsd`. Never call it twice in one run; the content cache
    makes a duplicate free but the board should have one ads card.

    WHEN NOT TO CALL: do not call this to "refresh" data mid-run. Do not widen
    `window_days` to get more ads — a smaller honest sample beats a larger dishonest
    one.
    """
    paths.ensure()
    ref_date = today or date.today()
    window_end = ref_date
    window_start = ref_date - timedelta(days=window_days)

    actual_keywords = keywords if (keywords is not None and len(keywords) > 0) else DEFAULT_KEYWORDS
    actual_countries = countries if (countries is not None and len(countries) > 0) else DEFAULT_COUNTRIES
    actual_max_items = max_items if max_items is not None else settings.apify_max_items

    if cache is None:
        cache_root = Path("fixtures/http") if Path("fixtures/http").exists() else paths.cache
        cache = HttpCache(root=cache_root, offline=False)

    actor_input = build_input(
        keywords=actual_keywords,
        countries=actual_countries,
        window_days=window_days,
        max_items=actual_max_items,
    )

    fallbacks_tried: list[str] = []
    raw_items: list[dict] = _run_async(
        run_actor(
            actor_id=settings.apify_ads_actor_id,
            actor_input=actor_input,
            token=settings.apify_token,
            max_charge_usd=settings.apify_max_charge_usd,
            timeout_s=settings.apify_run_timeout_seconds,
            cache=cache,
            fallbacks=settings.apify_ads_actor_fallbacks,
            fallbacks_tried=fallbacks_tried,
        )
    )

    if raw_items:
        first_item = raw_items[0]
        actor_used = str(first_item.get("_cwt_actor_id") or normalise_actor_id(settings.apify_ads_actor_id))
        fallbacks_recorded = list(first_item.get("_cwt_fallbacks_tried") or fallbacks_tried)
        run_id = str(first_item.get("_cwt_run_id") or "run_unknown")
        dataset_id = str(first_item.get("_cwt_dataset_id") or "ds_unknown")
        actual_charge_usd = float(first_item.get("_cwt_total_charge_usd", 0.0))
    else:
        actor_used = normalise_actor_id(settings.apify_ads_actor_id)
        fallbacks_recorded = list(fallbacks_tried)
        run_id = "run_none"
        dataset_id = "ds_none"
        actual_charge_usd = 0.0

    excluded_reasons = {
        "outside_window": 0,
        "no_body_text": 0,
        "duplicate_collation": 0,
        "duplicate_copy": 0,
    }
    seen_collations: set[str] = set()
    seen_body_hashes: set[str] = set()
    after_window_count = 0
    final_ads: list[Ad] = []

    for raw in raw_items:
        norm = normalise_ad(
            raw,
            actor_id=actor_used,
            charge_per_item=float(raw.get("_cwt_charge_usd", 0.0)),
            today=ref_date,
        )

        started_str = norm.get("started_running")
        ended_str = norm.get("ended_running")
        is_active = bool(norm.get("is_active", True))

        started = date.fromisoformat(started_str) if started_str else ref_date
        ended = date.fromisoformat(ended_str) if ended_str else None

        # Filter to window: started_running >= window_start or is_active
        # Ads that ended before window_start are excluded
        in_window = False
        if ended is not None and ended < window_start:
            in_window = False
        elif is_active or started >= window_start:
            in_window = True

        if not in_window:
            excluded_reasons["outside_window"] += 1
            continue

        after_window_count += 1

        collation_id = norm.get("collation_id")
        if collation_id and collation_id in seen_collations:
            excluded_reasons["duplicate_collation"] += 1
            continue

        body_text = norm.get("body_text", "").strip()
        if not body_text:
            excluded_reasons["no_body_text"] += 1
            continue

        body_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
        if body_hash in seen_body_hashes:
            excluded_reasons["duplicate_copy"] += 1
            continue

        if collation_id:
            seen_collations.add(collation_id)
        seen_body_hashes.add(body_hash)

        clean_ad = {k: v for k, v in norm.items() if not k.startswith("_cwt_")}
        final_ads.append(Ad.model_validate(clean_ad))

    warnings: list[str] = []
    if len(final_ads) < 8:
        warnings.append(
            f"fewer than 8 ads inside window ({len(final_ads)} found); window was NOT widened"
        )
    if len(final_ads) == 0:
        warnings.append("no ads found inside window")

    query = AdQuery(
        keywords=actual_keywords,
        countries=actual_countries,
        window_start=window_start,
        window_end=window_end,
        window_days=window_days,
    )
    source = AdSource(
        actor_id=actor_used,
        actor_fallbacks_tried=fallbacks_recorded,
        run_id=run_id,
        dataset_id=dataset_id,
        items_returned=len(raw_items),
        items_after_window_filter=after_window_count,
        actual_charge_usd=actual_charge_usd,
        charge_cap_usd=settings.apify_max_charge_usd,
    )
    ranking = AdRanking(
        method="unranked",
        weights={},
        excluded_reasons=excluded_reasons,
    )
    winning_ads_model = WinningAds(
        generated_at=now_iso(),
        query=query,
        source=source,
        ranking=ranking,
        ads=final_ads,
        warnings=warnings,
    )

    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    out_path = store.write("winning_ads", winning_ads_model)

    return {
        "artifact_path": str(out_path),
        "ads_found": len(raw_items),
        "after_window": after_window_count,
        "charge_usd": actual_charge_usd,
        "actor_id": actor_used,
        "warnings": warnings,
    }


def rank_winning_ads(
    *,
    settings: Settings,
    paths: RunPaths,
    top_n: int | None = None,
) -> dict:
    """Score and rank winning ads by active longevity, platform breadth, and recency.

    CALL THIS: second, on the `ads` card, immediately after `source_winning_ads`.
    Re-scores and sorts the sourced ads by active longevity, platform breadth, and
    recency.

    WHEN NOT TO CALL: do not call this before `source_winning_ads` has written
    `winning_ads.json`. Do not call this on cards downstream of `ads` — downstream
    stages consume the already-ranked artifact.
    """
    paths.ensure()
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    artifact = store.read("winning_ads")
    if not isinstance(artifact, WinningAds):
        raise ArtifactError(f"Expected WinningAds artifact, got {type(artifact).__name__}")

    method = "weighted_longevity_signal"
    weights = dict(DEFAULT_WEIGHTS)

    if not artifact.ads:
        deg_msg = "degraded: fewer than 5 ads carry usable video_duration_s (found 0)"
        if deg_msg not in artifact.warnings:
            artifact.warnings.append(deg_msg)
        artifact.ranking.method = method
        artifact.ranking.weights = weights
        out_path = store.write("winning_ads", artifact)
        return {
            "artifact_path": str(out_path),
            "ranked": 0,
            "method": method,
            "degraded": True,
        }

    # 90th percentile of active_days
    active_days_list = sorted(ad.active_days for ad in artifact.ads)
    k = (len(active_days_list) - 1) * 0.90
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        p90 = float(active_days_list[int(k)])
    else:
        p90 = float(active_days_list[f] * (c - k) + active_days_list[c] * (k - f))

    window_start = artifact.query.window_start
    window_days = max(1, artifact.query.window_days)

    scored_ads: list[Ad] = []
    usable_durations = 0

    for ad in artifact.ads:
        if ad.video_duration_s is not None and ad.video_duration_s > 0:
            usable_durations += 1

        norm_active = min(1.0, max(0.0, ad.active_days / p90)) if p90 > 0 else 0.0
        norm_active_flag = 1.0 if ad.is_active else 0.0
        norm_platform = min(1.0, max(0.0, len(ad.publisher_platforms) / 4.0))
        recency = min(1.0, max(0.0, (ad.started_running - window_start).days / window_days))

        raw_score = (
            weights["active_days"] * norm_active
            + weights["is_active"] * norm_active_flag
            + weights["platform_breadth"] * norm_platform
            + weights["recency"] * recency
        )
        score = round(min(0.9999, max(0.0, raw_score)), 4)
        scored_ads.append(ad.model_copy(update={"performance_score": score}))

    scored_ads.sort(key=lambda a: a.performance_score, reverse=True)

    degraded = usable_durations < 5
    if degraded:
        deg_msg = f"degraded: fewer than 5 ads carry usable video_duration_s (found {usable_durations})"
        if deg_msg not in artifact.warnings:
            artifact.warnings.append(deg_msg)

    artifact.ads = scored_ads
    artifact.ranking.method = method
    artifact.ranking.weights = weights

    ranked_count = len(scored_ads) if top_n is None else min(top_n, len(scored_ads))

    out_path = store.write("winning_ads", artifact)
    return {
        "artifact_path": str(out_path),
        "ranked": ranked_count,
        "method": method,
        "degraded": degraded,
    }
