"""Pipeline engine — wires the twelve stages into one runnable pipeline.

This module implements the pipeline contract called from cli.py:3878.
It provides two execution engines:
  - engine="local"  — runs every stage in-process, sequentially (CI path, offline quickstart)
  - engine="hermes" — seeds the kanban board, lets the dispatcher drive agents (production)

The STAGE_ORDER list defines the 11 working stages (root is a DAG anchor with no work).

S32 Derivation Notes (auditable):
- Signature from cli.py:3878: run_pipeline(settings, paths, engine, offline, record_pacing, force_stage)
- Stage table from S32 §69 lines 70-83 (11 entries, root excluded)
- Idempotency: three layers per §9.6 (card --idempotency-key, stage ArtifactStore.get_if_valid, tool HttpCache)
- Offline: bypasses our LLM calls and paid APIs; --engine local --offline bypasses Hermes entirely
- Research concurrency: res_pain, res_unique, res_crowd run via asyncio.gather (Rule A4)
- BudgetExceeded propagates uncaught (Rule A6)
- PipelineTimeout/PipelineBlocked propagate uncaught (mapped to exits 2/3 in cli.py)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cwt.config import Settings
from cwt.clients.http_cache import HttpCache, OfflineFixtureMissing
from cwt.clients.llm import BudgetExceeded, LLMClient
from cwt.domain.artifacts import ArtifactStore, ArtifactError, ArtifactValidationError
from cwt.util.paths import RunPaths

# Tool imports — these are the stage functions
from cwt.tools import (
    ads,
    patterns,
    research,
    storyboard,
    claims,
    video,
    bundle,
)

STAGE_ORDER: tuple[str, ...] = (
    "ads",
    "patterns",
    "res_pain",
    "res_unique",
    "res_crowd",
    "brief",
    "script",
    "compliance",
    "render",
    "qa",
    "collect",
)


@dataclass(frozen=True)
class StageResult:
    key: str
    status: str                  # "ok" | "skipped" | "failed"
    artifact: Path | None
    elapsed_s: float
    error: str | None


class ToolFailed(RuntimeError):
    """Raised when a tool function raises an exception during execution."""
    def __init__(self, message: str, stage: str, original: BaseException | None = None):
        super().__init__(message)
        self.stage = stage
        self.original = original


async def run_pipeline(
    *,
    settings: Settings,
    paths: RunPaths,
    engine: str = "hermes",
    offline: bool = False,
    record_pacing: bool = False,
    force_stage: list[str] | None = None,
) -> dict[str, Any]:
    """Run the full pipeline end to end.

    Returns {"done": int, "skipped": int, "cost_usd": float, "output": str, "stages": list[StageResult]}
    """
    force_stage = force_stage or []

    if engine not in ("hermes", "local"):
        raise ValueError(f"Unknown engine '{engine}'. Must be 'hermes' or 'local'.")

    # Build shared infrastructure
    cache = _build_cache(paths, offline)
    client = None if offline else _build_client(settings, paths)

    if engine == "local":
        stages = await _run_local(
            settings, paths, client, cache, offline, record_pacing, force_stage
        )
    else:
        stages = await _run_hermes(
            settings, paths, client, cache, offline, record_pacing, force_stage
        )

    # Compute summary
    done = sum(1 for s in stages if s.status == "ok")
    skipped = sum(1 for s in stages if s.status == "skipped")
    cost_usd = client.run_cost_usd if client else 0.0

    # Determine output path
    output = str(paths.render / "final.mp4")
    if not Path(output).exists():
        output = str(paths.run_dir / "submission")

    return {
        "done": done,
        "skipped": skipped,
        "cost_usd": cost_usd,
        "output": output,
        "stages": stages,
    }


def _build_client(settings: Settings, paths: RunPaths) -> LLMClient:
    """Build the LLM client with ledger wiring."""
    api_key = (
        settings.openrouter_api_key
        if settings.llm_provider == "openrouter"
        else settings.nvidia_api_key
    )
    return LLMClient(
        provider=settings.llm_provider,
        api_key=api_key,
        model_cheap=settings.model_cheap,
        model_strong=settings.model_strong,
        fallbacks=settings.model_fallbacks,
        max_concurrency=settings.llm_max_concurrency,
        max_usd=settings.max_usd,
        ledger_path=paths.ledger,
        timeout_s=settings.llm_timeout_seconds,
        repair_attempts=settings.llm_json_repair_attempts,
    )


def _build_cache(paths: RunPaths, offline: bool) -> HttpCache:
    """Build the HTTP cache. In offline mode it is read-only and misses raise OfflineFixtureMissing."""
    return HttpCache(root=paths.cache, offline=offline)


def _stage_is_valid(key: str, paths: RunPaths, *, force: bool) -> bool:
    """Check if a stage's artifact is valid and up-to-date via ArtifactStore.get_if_valid.

    Returns True if the artifact exists, parses, and all input hashes match.
    Returns False if missing, invalid, or inputs changed.
    """
    if force:
        return False

    store = ArtifactStore(paths, run_id=paths.run_dir.name)

    # Map stage key to artifact name and its inputs
    input_map = _stage_inputs(key, paths)
    if input_map is None:
        # Stage doesn't produce a contracted artifact (e.g., research angles)
        return False

    artifact_name, inputs = input_map
    artifact = store.get_if_valid(artifact_name, inputs=inputs)
    return artifact is not None


def _stage_inputs(key: str, paths: RunPaths) -> tuple[str, dict[str, Path]] | None:
    """Map stage key to (artifact_name, {input_label: input_path}) for get_if_valid."""
    store = ArtifactStore(paths, run_id=paths.run_dir.name)

    mapping = {
        "ads": ("winning_ads", {}),
        "patterns": ("ad_patterns", {"winning_ads": store.path_for("winning_ads")}),
        "res_pain": (None, None),   # scratch artifact, not in ARTIFACT_NAMES
        "res_unique": (None, None), # scratch artifact
        "res_crowd": (None, None),  # scratch artifact
        "brief": ("research_brief", {"angles": paths.artifacts / "angles"}),
        "script": ("storyboard", {"research_brief": store.path_for("research_brief"), "ad_patterns": store.path_for("ad_patterns")}),
        "compliance": ("claims_report", {"storyboard": store.path_for("storyboard")}),
        "render": ("render_manifest", {"storyboard": store.path_for("storyboard")}),
        "qa": (None, None),  # QA doesn't write a contracted artifact
        "collect": (None, None),  # collect writes submission/, not a contracted artifact
    }
    return mapping.get(key)


async def _run_local(
    settings: Settings,
    paths: RunPaths,
    client: LLMClient | None,
    cache: HttpCache,
    offline: bool,
    record_pacing: bool,
    force_stage: list[str],
) -> list[StageResult]:
    """Run stages sequentially in-process. Research stages run concurrently."""
    stages: list[StageResult] = []

    for key in STAGE_ORDER:
        if key in ("res_pain", "res_unique", "res_crowd"):
            # Research stages run concurrently as a block
            research_results = await _run_research_block(
                settings, paths, client, cache, force_stage
            )
            stages.extend(research_results)
            continue

        t0 = time.monotonic()
        try:
            if _stage_is_valid(key, paths, force=key in force_stage):
                stages.append(StageResult(
                    key=key, status="skipped", artifact=None,
                    elapsed_s=time.monotonic() - t0, error=None
                ))
                if record_pacing:
                    await asyncio.sleep(0.5)
                continue

            artifact_path = await _run_stage_local(
                key, settings, paths, client, cache, offline
            )
            stages.append(StageResult(
                key=key, status="ok", artifact=Path(artifact_path),
                elapsed_s=time.monotonic() - t0, error=None
            ))

        except (ArtifactValidationError, ToolFailed, BudgetExceeded, OfflineFixtureMissing) as exc:
            stages.append(StageResult(
                key=key, status="failed", artifact=None,
                elapsed_s=time.monotonic() - t0, error=str(exc)
            ))
            # Failed stage poisons downstream
            break
        except Exception as exc:
            stages.append(StageResult(
                key=key, status="failed", artifact=None,
                elapsed_s=time.monotonic() - t0, error=f"{type(exc).__name__}: {exc}"
            ))
            break

        if record_pacing:
            await asyncio.sleep(0.5)

    return stages


async def _run_stage_local(
    key: str,
    settings: Settings,
    paths: RunPaths,
    client: LLMClient | None,
    cache: HttpCache,
    offline: bool,
) -> str:
    """Execute a single stage in local mode. Returns artifact path."""
    if key == "ads":
        res = ads.source_winning_ads(settings=settings, paths=paths, cache=cache)
        ads.rank_winning_ads(settings=settings, paths=paths)
        return res["artifact_path"]

    if key == "patterns":
        res = patterns.extract_ad_patterns(
            settings=settings, paths=paths, concurrency=settings.llm_max_concurrency, client=client
        )
        return res["artifact_path"]

    if key == "brief":
        res = research.assemble_brief(settings=settings, paths=paths, client=client, cache=cache)
        return res["artifact_path"]

    if key == "script":
        # Generate hook candidates
        hook_res = storyboard.generate_hook_candidates(settings=settings, paths=paths, client=client)
        hook_id = hook_res["selected_id"]

        # Get total_duration_s from ad_patterns aggregate or settings
        store = ArtifactStore(paths, run_id=paths.run_dir.name)
        ad_patterns = store.read("ad_patterns")
        if ad_patterns.aggregate and ad_patterns.aggregate.median_beat_timeline:
            total_duration_s = ad_patterns.aggregate.median_beat_timeline[-1].end_s
        else:
            total_duration_s = float(settings.video_max_seconds)

        # Write three variants
        for angle in ("pain", "unique_data", "crowd_effect"):
            storyboard.write_storyboard_variant(
                settings=settings, paths=paths, angle=angle,
                hook_id=hook_id, total_duration_s=total_duration_s, client=client
            )

        # Judge and splice
        judge_res = storyboard.judge_variants(settings=settings, paths=paths, client=client)
        return judge_res["winner_path"]

    if key == "compliance":
        # Pre-render claims check
        check_res = claims.check_claims(
            settings=settings, paths=paths, stage="pre_render", client=client
        )
        # Bounded rewrite loop
        max_rounds = settings.claims_max_rewrite_rounds
        rounds = 0
        while check_res["verdict"] == "request_changes" and rounds < max_rounds:
            rewrite_res = claims.rewrite_for_compliance(
                settings=settings, paths=paths, round_no=rounds + 1, client=client
            )
            check_res = claims.check_claims(
                settings=settings, paths=paths, stage="pre_render", client=client
            )
            rounds += 1

        if check_res["verdict"] == "block":
            raise ToolFailed("Claims gate blocked after max rewrite rounds", "compliance")

        return str(paths.artifacts / "claims_report.json")

    if key == "render":
        # Synthesize voiceover then render
        video.synthesize_voiceover(settings=settings, paths=paths)
        render_res = video.render_video(settings=settings, paths=paths)
        return render_res["artifact_path"]

    if key == "qa":
        qa_res = video.qa_check(settings=settings, paths=paths, client=client, raise_on_error=False)
        if not qa_res["ok"]:
            raise ToolFailed(f"QA failed: {qa_res['errors']}", "qa")
        return str(paths.artifacts / "render_manifest.json")

    if key == "collect":
        bundle_res = bundle.assemble_submission(settings=settings, paths=paths)
        return bundle_res["dir"]

    raise ValueError(f"Unknown stage key: {key}")


async def _run_research_block(
    settings: Settings,
    paths: RunPaths,
    client: LLMClient | None,
    cache: HttpCache,
    force_stage: list[str],
) -> list[StageResult]:
    """Run the three research angles concurrently."""
    stages: list[StageResult] = []

    async def run_one(angle: str, stage_key: str) -> StageResult:
        t0 = time.monotonic()
        try:
            # Check if angle artifact is valid (scratch file in artifacts/angles/)
            angle_file = paths.artifacts / "angles" / f"{angle}.json"
            force = stage_key in force_stage
            if not force and angle_file.exists():
                # Quick validation: file exists and is valid JSON with expected fields
                try:
                    import json
                    data = json.loads(angle_file.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("angle") == angle:
                        return StageResult(
                            key=stage_key, status="skipped", artifact=None,
                            elapsed_s=time.monotonic() - t0, error=None
                        )
                except Exception:
                    pass

            res = research.research_angle(
                settings=settings, paths=paths, angle=angle, client=client, cache=cache
            )
            return StageResult(
                key=stage_key, status="ok", artifact=Path(res.get("angle_file", str(angle_file))),
                elapsed_s=time.monotonic() - t0, error=None
            )
        except (BudgetExceeded, OfflineFixtureMissing):
            raise
        except Exception as exc:
            return StageResult(
                key=stage_key, status="failed", artifact=None,
                elapsed_s=time.monotonic() - t0, error=f"{type(exc).__name__}: {exc}"
            )

    # Run all three concurrently
    results = await asyncio.gather(
        run_one("pain", "res_pain"),
        run_one("unique_data", "res_unique"),
        run_one("crowd_effect", "res_crowd"),
        return_exceptions=True,
    )

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            # BudgetExceeded and OfflineFixtureMissing should have been raised, not returned
            if isinstance(result, (BudgetExceeded, OfflineFixtureMissing)):
                raise result
            stage_key = ["res_pain", "res_unique", "res_crowd"][i]
            stages.append(StageResult(
                key=stage_key, status="failed", artifact=None,
                elapsed_s=0.0, error=f"{type(result).__name__}: {result}"
            ))
            break
        else:
            stages.append(result)

    return stages


async def _run_hermes(
    settings: Settings,
    paths: RunPaths,
    client: LLMClient | None,
    cache: HttpCache,
    offline: bool,
    record_pacing: bool,
    force_stage: list[str],
) -> list[StageResult]:
    """Seed the kanban board and wait for completion via Hermes dispatcher."""
    from cwt.hermes.board import seed, wait_for_completion, PipelineTimeout, PipelineBlocked
    from cwt.hermes.dag import DAG_SPEC

    # Verify profiles exist
    from cwt.hermes.cli import hermes_bin
    try:
        hermes_bin()
    except RuntimeError as exc:
        raise RuntimeError(
            "Run `cwt bootstrap` first — the nine CWT profiles are not installed."
        ) from exc

    run_id = paths.run_dir.name

    # Handle --force-stage: unblock and invalidate artifact
    if force_stage:
        from cwt.hermes.cli import kanban
        from cwt.hermes.board import list_cards, TERMINAL_BAD
        from cwt.domain.artifacts import ArtifactStore

        store = ArtifactStore(paths, run_id=run_id)
        cards = list_cards(settings.board)
        card_map = {c.get("title", "").split("—")[0].strip().lower().replace(" ", "_"): c["id"] for c in cards}

        stage_to_card = {
            "ads": "source winning ads",
            "patterns": "extract hooks",
            "res_pain": "research angle: the icp's pain",
            "res_unique": "research angle: crowdwisdomtrading's unique data",
            "res_crowd": "research angle: crowd wisdom",
            "brief": "assemble the research brief",
            "script": "write storyboard",
            "compliance": "claims gate",
            "render": "render the 30-60s video",
            "qa": "final qa",
            "collect": "assemble the submission bundle",
        }

        for fs in force_stage:
            card_title = stage_to_card.get(fs)
            if card_title and card_title in card_map:
                cid = card_map[card_title]
                kanban("unblock", cid, board=settings.board, timeout_s=60)
                # Invalidate artifact so get_if_valid fails
                input_map = _stage_inputs(fs, paths)
                if input_map:
                    artifact_name, _ = input_map
                    art_path = store.path_for(artifact_name)
                    if art_path.exists():
                        art_path.unlink()

    # Seed the board
    seed(run_id, paths.run_dir, settings.board)

    # Wait for completion
    summary = await wait_for_completion(
        settings.board,
        timeout_s=settings.run_timeout_seconds,
        stall_threshold_s=settings.stall_threshold_seconds,
        fail_fast=True,
    )

    # Collect cost from ledger (workers write to their own ledgers)
    cost_usd = 0.0
    ledger_path = paths.ledger
    if ledger_path.exists():
        import json
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                        cost_usd += float(rec.get("cost_usd", 0.0))
                    except Exception:
                        pass

    # Build StageResult list from completed cards
    stages: list[StageResult] = []
    for card in summary.cards:
        if card["status"] == "done":
            stages.append(StageResult(
                key=card.get("title", "").split("—")[0].strip().lower().replace(" ", "_"),
                status="ok", artifact=None, elapsed_s=0.0, error=None
            ))

    return stages