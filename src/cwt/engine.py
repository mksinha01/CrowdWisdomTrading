"""Pipeline engine — wires the eleven stages into one runnable pipeline.

S32 — Pipeline engine. The largest hole in the spec (G1): ``cli.py`` calls
``from cwt.engine import run_pipeline`` but no ``engine.py`` section exists in
the 5,509-line spec. This module is derived from:

- ``cli.py`` call site (spec §9.3, lines 3878-3892): the ``run_pipeline``
  signature is the only contract that exists.
- Stage table (S32 story, 11 entries; ``root`` is a DAG anchor with no work,
  spec line 3401 — it is not a stage. ``STAGE_ORDER`` has 11 entries; the DAG
  has 12 cards. The banner says "11 stages", matching spec line 4006
  ``Done. 11 stages``).
- §9.5 output contract: ``Done. {done} stages, ${cost:.4f}, output: {output}``.
- §9.6 three idempotent layers: card ``--idempotency-key`` (S28 ``seed``),
  stage ``ArtifactStore.get_if_valid`` (S07), tool ``HttpCache`` (S08).
- Rule A6: ``BudgetExceeded`` propagates out of ``run_pipeline`` uncaught
  (``cli.py`` maps it to exit 4).
- Rule A4 / §7.1: ``kanban.max_in_progress: 4`` matches
  ``LLM_MAX_CONCURRENCY``; the three research stages run concurrently via
  ``asyncio.gather`` in both engines (the money shot for the recording).
- Rule C2: when the compliance stage blocks, the run blocks. Do not
  continue to render.
- ``--offline`` bypasses our LLM calls and all three paid APIs — it does NOT
  remove the Hermes worker's own model calls. ``--engine local --offline``
  bypasses Hermes entirely and costs literally nothing.

Offline replay (decision, auditable): the fixture set includes the *artifacts*
the stages produce (``fixtures/artifacts/<name>.json`` plus ``angles/`` and
``variants/``), not just HTTP responses. On an offline run the engine seeds
``runs/<run_id>/artifacts/`` from those fixtures (everything except
render outputs), so ``_stage_is_valid`` returns True for every LLM/paid-API
stage and ``--engine local --offline`` is effectively a replay that still
renders a real video via ``local_ffmpeg`` + silent TTS (no keys, no network).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from cwt.config import Settings
from cwt.clients.http_cache import HttpCache, OfflineFixtureMissing
from cwt.clients.llm import ArtifactValidationError, BudgetExceeded, LLMClient
from cwt.domain.artifacts import ArtifactStore
from cwt.util.paths import RunPaths

from cwt.tools import ads, bundle, claims, patterns, research, storyboard, video

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

_RESEARCH_KEYS = ("res_pain", "res_unique", "res_crowd")
_ANGLE_FOR_KEY = {
    "res_pain": "pain",
    "res_unique": "unique_data",
    "res_crowd": "crowd_effect",
}


@dataclass(frozen=True)
class StageResult:
    key: str
    status: str  # "ok" | "skipped" | "failed"
    artifact: Path | None
    elapsed_s: float
    error: str | None


class ToolFailed(RuntimeError):
    """Raised when a tool function fails during stage execution."""

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
) -> dict:
    """Run the full pipeline end to end.

    Returns {"done": int, "executed": int, "skipped": int, "failed": int,
             "total": int, "cost_usd": float, "output": str,
             "stages": [StageResult]}.

    ``done`` is ``executed + skipped`` — the §13.3 display contract. A stage
    replayed from a valid artifact counts as done; only ``failed`` stages do not.
    """
    force_stage = list(force_stage or [])
    if engine not in ("hermes", "local"):
        raise ValueError(f"Unknown engine '{engine}'. Must be 'hermes' or 'local'.")

    paths.ensure()

    if offline:
        _seed_offline_fixtures(paths)

    if engine == "local":
        stages = await _run_local(
            settings, paths, offline=offline, record_pacing=record_pacing, force_stage=force_stage
        )
    else:
        stages = await _run_hermes(
            settings, paths, offline=offline, record_pacing=record_pacing, force_stage=force_stage
        )

    executed = sum(1 for s in stages if s.status == "ok")
    skipped = sum(1 for s in stages if s.status == "skipped")
    failed = sum(1 for s in stages if s.status == "failed")
    # "done" counts every stage that COMPLETED — executed or replayed from a
    # valid artifact. The display contract is §13.3's "Done. 11 stages", which
    # must read the same for a fresh offline run and for an all-skipped resume
    # (§9.6: "Done. 11 stages, $0.0000 (every stage skipped)").
    done = executed + skipped
    cost_usd = _ledger_cost_usd(paths, offline=offline)

    output = str(paths.render / "final.mp4")
    if not Path(output).exists():
        output = str(paths.run_dir / "submission")

    return {
        "done": done,
        "executed": executed,
        "skipped": skipped,
        "failed": failed,
        "total": len(STAGE_ORDER),
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
    """Build the HTTP cache. In --offline mode it is read-only and a miss raises."""
    return HttpCache(root=paths.cache, offline=offline)


def _stage_is_valid(key: str, paths: RunPaths, *, force: bool) -> bool:
    """Check if a stage's artifact is valid via ArtifactStore.get_if_valid.

    Returns True if the artifact exists, parses, and all input hashes match.
    Returns False if missing, invalid, or inputs changed. Never raises.
    """
    if force:
        return False
    try:
        return _stage_is_valid_inner(key, paths)
    except Exception:
        return False


def _stage_is_valid_inner(key: str, paths: RunPaths) -> bool:
    store = ArtifactStore(paths, run_id=paths.run_dir.name)

    # ── scratch research angles (not in ARTIFACT_NAMES) ──
    if key in _RESEARCH_KEYS:
        angle = _ANGLE_FOR_KEY[key]
        angle_file = paths.artifacts / "angles" / f"{angle}.json"
        if not angle_file.is_file():
            return False
        try:
            data = json.loads(angle_file.read_text(encoding="utf-8"))
            return isinstance(data, dict) and data.get("angle") == angle
        except Exception:
            return False

    # ── contracted artifacts ──
    mapping = _stage_inputs(key, paths)
    if mapping is not None:
        artifact_name, inputs = mapping
        # Normal path: provenance-backed validity.
        try:
            if store.get_if_valid(artifact_name, inputs=inputs) is not None:
                return True
        except Exception:
            pass
        # Fallback for offline-seeded fixtures (no provenance record yet):
        # if the file exists and validates but provenance has no record for
        # it, treat as valid (seeded replay). If provenance HAS a record but
        # inputs mismatched, get_if_valid already returned None -> re-run.
        try:
            store.read(artifact_name)
        except Exception:
            return False
        try:
            prov_file = paths.provenance
            if not prov_file.is_file():
                return True
            records = json.loads(prov_file.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                return True
            has_record = any(
                isinstance(r, dict) and r.get("name") == artifact_name for r in records
            )
            # No record -> seeded fixture -> valid. Record exists but
            # get_if_valid failed -> inputs changed -> invalid.
            return not has_record
        except Exception:
            return False

    # ── qa: valid if the post-render claims report exists ──
    if key == "qa":
        post = paths.artifacts / "claims_report_post_render.json"
        if post.is_file():
            try:
                data = json.loads(post.read_text(encoding="utf-8"))
                return isinstance(data, dict) and data.get("stage") == "post_render"
            except Exception:
                return False
        return False

    # ── collect: valid if the submission bundle exists ──
    if key == "collect":
        final_mp4 = paths.run_dir / "submission" / "final.mp4"
        sb_json = paths.run_dir / "submission" / "storyboard.json"
        return final_mp4.is_file() and sb_json.is_file()

    return False


def _stage_inputs(key: str, paths: RunPaths) -> tuple[str, dict[str, Path]] | None:
    """Map stage key to (artifact_name, {input_label: input_path}) for get_if_valid."""
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    mapping: dict[str, tuple[str, dict[str, Path]] | None] = {
        "ads": ("winning_ads", {}),
        "patterns": ("ad_patterns", {"winning_ads": store.path_for("winning_ads")}),
        "brief": (
            "research_brief",
            {
                "pain": paths.artifacts / "angles" / "pain.json",
                "unique_data": paths.artifacts / "angles" / "unique_data.json",
                "crowd_effect": paths.artifacts / "angles" / "crowd_effect.json",
            },
        ),
        "script": (
            "storyboard",
            {
                "research_brief": store.path_for("research_brief"),
                "ad_patterns": store.path_for("ad_patterns"),
            },
        ),
        "compliance": ("claims_report", {"storyboard": store.path_for("storyboard")}),
        "render": ("render_manifest", {"storyboard": store.path_for("storyboard")}),
    }
    return mapping.get(key)


async def _run_local(
    settings: Settings,
    paths: RunPaths,
    *,
    offline: bool,
    record_pacing: bool,
    force_stage: list[str] | None = None,
) -> list[StageResult]:
    """Run every stage in-process. Research stages run concurrently via asyncio.gather."""
    force_stage = list(force_stage or [])
    cache = _build_cache(paths, offline)
    client: LLMClient | None = None if offline else _build_client(settings, paths)

    stages: list[StageResult] = []
    idx = 0
    order = list(STAGE_ORDER)
    while idx < len(order):
        key = order[idx]
        # ── research block: run all three concurrently, once ──
        if key in _RESEARCH_KEYS:
            block_keys = [k for k in order[idx:] if k in _RESEARCH_KEYS]
            # Only handle the contiguous block starting here.
            contiguous: list[str] = []
            j = idx
            while j < len(order) and order[j] in _RESEARCH_KEYS:
                contiguous.append(order[j])
                j += 1
            results = await _run_research_block(
                settings, paths, client, cache, force_stage, offline
            )
            # Keep only the contiguous slice (normally all three).
            stages.extend(results)
            if any(r.status == "failed" for r in results):
                break
            # Budget/offline errors propagate (raised inside the block).
            idx = j
            await _maybe_pace("research", record_pacing)
            continue

        t0 = time.monotonic()
        try:
            if _stage_is_valid(key, paths, force=key in force_stage):
                stages.append(
                    StageResult(
                        key=key,
                        status="skipped",
                        artifact=None,
                        elapsed_s=time.monotonic() - t0,
                        error=None,
                    )
                )
                await _maybe_pace(key, record_pacing)
                idx += 1
                continue

            artifact_path = await _run_stage_local(key, settings, paths, client, cache, offline)
            art: Path | None = None
            if artifact_path:
                try:
                    art = Path(artifact_path)
                except Exception:
                    art = None
            stages.append(
                StageResult(
                    key=key, status="ok", artifact=art, elapsed_s=time.monotonic() - t0, error=None
                )
            )
        except (ArtifactValidationError, ToolFailed) as exc:
            stages.append(
                StageResult(
                    key=key,
                    status="failed",
                    artifact=None,
                    elapsed_s=time.monotonic() - t0,
                    error=str(exc),
                )
            )
            break
        except (BudgetExceeded, OfflineFixtureMissing):
            raise
        except Exception as exc:
            stages.append(
                StageResult(
                    key=key,
                    status="failed",
                    artifact=None,
                    elapsed_s=time.monotonic() - t0,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            break

        await _maybe_pace(key, record_pacing)
        idx += 1

    _ = block_keys if "block_keys" in dir() else None
    return stages


async def _run_stage_local(
    key: str,
    settings: Settings,
    paths: RunPaths,
    client: LLMClient | None,
    cache: HttpCache,
    offline: bool,
) -> str:
    """Execute a single stage in local mode. Returns artifact path (or dir for collect)."""
    if key == "ads":
        res = ads.source_winning_ads(settings=settings, paths=paths, cache=cache)
        ads.rank_winning_ads(settings=settings, paths=paths)
        return str(res["artifact_path"])

    if key == "patterns":
        res = patterns.extract_ad_patterns(
            settings=settings,
            paths=paths,
            concurrency=settings.llm_max_concurrency,
            client=client,
        )
        return str(res["artifact_path"])

    if key == "brief":
        res = research.assemble_brief(settings=settings, paths=paths, client=client, cache=cache)
        return str(res["artifact_path"])

    if key == "script":
        return await _run_script_stage(settings, paths, client)

    if key == "compliance":
        return await _run_compliance_stage(settings, paths, client)

    if key == "render":
        # Offline must not reach for edge_tts (it is a network backend) or for
        # a Piper voice file that may not be on disk. Settings is a frozen
        # dataclass, so this is dataclasses.replace — NOT pydantic model_copy.
        tts_settings = (
            replace(settings, tts_backend_chain=["silent"]) if offline else settings
        )
        video.synthesize_voiceover(settings=tts_settings, paths=paths)
        render_res = video.render_video(settings=settings, paths=paths)
        return str(render_res["artifact_path"])

    if key == "qa":
        qa_res = video.qa_check(settings=settings, paths=paths, client=client, raise_on_error=False)
        if not qa_res.get("ok"):
            raise ToolFailed(f"QA failed: {qa_res.get('errors')}", "qa")
        return str(paths.artifacts / "claims_report_post_render.json")

    if key == "collect":
        bundle_res = bundle.assemble_submission(settings=settings, paths=paths)
        return str(bundle_res["dir"])

    raise ValueError(f"Unknown stage key: {key}")


async def _run_script_stage(
    settings: Settings, paths: RunPaths, client: LLMClient | None
) -> str:
    """hook candidates -> 3 variants -> judge/splice -> creative review loop (<= max rounds)."""
    hook_res = storyboard.generate_hook_candidates(settings=settings, paths=paths, client=client)
    hook_id = hook_res["selected_id"]

    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    ad_pat = store.read("ad_patterns")
    median_tl = getattr(getattr(ad_pat, "aggregate", None), "median_beat_timeline", None)
    if median_tl:
        total_duration_s = float(median_tl[-1].end_s)
    else:
        total_duration_s = float(settings.video_max_seconds)

    for angle in ("pain", "unique_data", "crowd_effect"):
        storyboard.write_storyboard_variant(
            settings=settings,
            paths=paths,
            angle=angle,
            hook_id=hook_id,
            total_duration_s=total_duration_s,
            client=client,
        )

    judge_res = storyboard.judge_variants(settings=settings, paths=paths, client=client)
    winner_path = str(judge_res["winner_path"])

    # Creative-director review loop: score -> apply_rewrite (<= creative_max_rounds).
    max_rounds = int(settings.creative_max_rounds)
    for _ in range(max_rounds):
        score_res = storyboard.score_storyboard(settings=settings, paths=paths, client=client)
        if score_res.get("verdict") == "pass":
            break
        storyboard.apply_rewrite(settings=settings, paths=paths, client=client)
    else:
        # Final score after exhausting rounds is recorded; do not force-pass.
        pass

    return winner_path


async def _run_compliance_stage(
    settings: Settings, paths: RunPaths, client: LLMClient | None
) -> str:
    """Pre-render claims check + bounded rewrite. Rule C2: block stops the run."""
    check_res = claims.check_claims(
        settings=settings, paths=paths, stage="pre_render", client=client
    )
    max_rounds = int(settings.claims_max_rewrite_rounds)
    rounds = 0
    while check_res.get("verdict") == "request_changes" and rounds < max_rounds:
        claims.rewrite_for_compliance(
            settings=settings, paths=paths, round_no=rounds + 1, client=client
        )
        check_res = claims.check_claims(
            settings=settings, paths=paths, stage="pre_render", client=client
        )
        rounds += 1

    if check_res.get("verdict") == "block":
        raise ToolFailed(
            f"Claims gate blocked: {check_res.get('rewrite_instructions')}", "compliance"
        )
    return str(paths.artifacts / "claims_report.json")


async def _run_research_block(
    settings: Settings,
    paths: RunPaths,
    client: LLMClient | None,
    cache: HttpCache,
    force_stage: list[str],
    offline: bool = False,
) -> list[StageResult]:
    """Run the three research angles concurrently via asyncio.gather."""

    async def run_one(angle: str, stage_key: str) -> StageResult:
        t0 = time.monotonic()
        try:
            if _stage_is_valid(stage_key, paths, force=stage_key in force_stage):
                return StageResult(
                    key=stage_key,
                    status="skipped",
                    artifact=None,
                    elapsed_s=time.monotonic() - t0,
                    error=None,
                )
            # In offline mode with no cached angle artifact, there is nothing
            # to call (LLM + paid APIs are removed). Surface a loud miss.
            if offline:
                raise OfflineFixtureMissing(
                    f"No fixture for research angle '{angle}'\n"
                    f"Expected: {paths.artifacts / 'angles' / f'{angle}.json'}\n"
                    f"Offline mode replays fixtures/artifacts/ — re-run online once to record them."
                )
            res = await asyncio.to_thread(
                research.research_angle,
                settings=settings,
                paths=paths,
                angle=angle,
                client=client,
                cache=cache,
            )
            angle_file = res.get("angle_file", str(paths.artifacts / "angles" / f"{angle}.json"))
            return StageResult(
                key=stage_key,
                status="ok",
                artifact=Path(angle_file),
                elapsed_s=time.monotonic() - t0,
                error=None,
            )
        except (BudgetExceeded, OfflineFixtureMissing):
            raise
        except Exception as exc:
            return StageResult(
                key=stage_key,
                status="failed",
                artifact=None,
                elapsed_s=time.monotonic() - t0,
                error=f"{type(exc).__name__}: {exc}",
            )

    results = await asyncio.gather(
        run_one("pain", "res_pain"),
        run_one("unique_data", "res_unique"),
        run_one("crowd_effect", "res_crowd"),
    )
    return list(results)


async def _run_hermes(
    settings: Settings,
    paths: RunPaths,
    *,
    offline: bool,
    record_pacing: bool,
    force_stage: list[str] | None = None,
) -> list[StageResult]:
    """Seed the kanban board and wait for completion via the Hermes dispatcher."""
    from cwt.hermes.board import (
        PipelineBlocked,
        PipelineTimeout,
        seed,
        wait_for_completion,
    )

    force_stage = list(force_stage or [])

    # Before seeding, assert the profiles exist. The binary check is the
    # portable proxy: without the binary there are no profiles either.
    try:
        from cwt.hermes.cli import hermes_bin

        hermes_bin()
    except Exception as exc:
        raise RuntimeError(
            "Run `cwt bootstrap` first — the nine CWT profiles are not installed."
        ) from exc

    run_id = paths.run_dir.name

    if force_stage:
        _hermes_force_invalidate(settings, paths, force_stage)

    seed(run_id, paths.run_dir, settings.board)

    summary = await wait_for_completion(
        settings.board,
        timeout_s=settings.run_timeout_seconds,
        stall_threshold_s=settings.stall_threshold_seconds,
        fail_fast=True,
    )
    _ = (PipelineBlocked, PipelineTimeout)

    await _maybe_pace("hermes", record_pacing)

    stages: list[StageResult] = []
    for card in summary.cards:
        if card.get("status") == "done":
            title = str(card.get("title", ""))
            stages.append(
                StageResult(key=_card_title_to_stage(title), status="ok", artifact=None,
                            elapsed_s=0.0, error=None)
            )
    return stages


def _card_title_to_stage(title: str) -> str:
    t = title.lower()
    if "winning ads" in t or t.startswith("source"):
        return "ads"
    if "hook" in t or "beat sheet" in t:
        return "patterns"
    if "icp" in t or "pain" in t:
        return "res_pain"
    if "unique data" in t:
        return "res_unique"
    if "crowd wisdom" in t or "forecasting" in t:
        return "res_crowd"
    if "brief" in t:
        return "brief"
    if "storyboard" in t:
        return "script"
    if "claims" in t:
        return "compliance"
    if "render" in t or "video" in t:
        return "render"
    if "qa" in t:
        return "qa"
    if "submission" in t or "bundle" in t:
        return "collect"
    return title.split("—")[0].strip().lower().replace(" ", "_")[:32]


def _hermes_force_invalidate(
    settings: Settings, paths: RunPaths, force_stage: list[str]
) -> None:
    """--force-stage X in the hermes engine: unblock + invalidate artifact.

    Semantics differ from local (re-run the stage): here we unblock the card
    and delete its artifact so get_if_valid fails on the next run.
    """
    from cwt.hermes.board import list_cards
    from cwt.hermes.cli import kanban

    try:
        cards = list_cards(settings.board)
    except Exception:
        cards = []
    card_map = {
        str(c.get("title", "")): str(c.get("id", ""))
        for c in cards if isinstance(c, dict)
    }

    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    for fs in force_stage:
        for title, cid in card_map.items():
            if _card_title_to_stage(title) == fs:
                try:
                    kanban("unblock", cid, board=settings.board, timeout_s=60)
                except Exception:
                    pass
        mapping = _stage_inputs(fs, paths)
        if mapping is not None:
            artifact_name, _ = mapping
            try:
                art_path = store.path_for(artifact_name)
                if art_path.exists():
                    art_path.unlink()
            except Exception:
                pass
        if fs in _RESEARCH_KEYS:
            angle = _ANGLE_FOR_KEY[fs]
            try:
                (paths.artifacts / "angles" / f"{angle}.json").unlink(missing_ok=True)
            except Exception:
                pass


def _ledger_cost_usd(paths: RunPaths, *, offline: bool = False) -> float:
    """Aggregate runs/<id>/llm_ledger.jsonl. Offline (or missing ledger) -> 0.0."""
    if offline:
        return 0.0
    ledger_path = paths.ledger
    if not ledger_path.exists():
        return 0.0
    total = 0.0
    try:
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    total += float(rec.get("cost_usd", 0.0))
                except Exception:
                    continue
    except Exception:
        return 0.0
    return round(total, 6)


async def _maybe_pace(key: str, record_pacing: bool) -> None:
    """record_pacing -> record_pacing_pause(key) after each stage."""
    if not record_pacing:
        return
    os.environ["CWT_RECORD_PACING"] = "1"
    try:
        from cwt.hermes.record import record_pacing_pause

        await asyncio.to_thread(record_pacing_pause, key)
    except Exception:
        await asyncio.sleep(0.5)


def _seed_offline_fixtures(paths: RunPaths) -> None:
    """Copy fixtures/artifacts/* into runs/<run_id>/artifacts/ (no overwrite).

    Copies everything except render outputs (render_manifest, voiceover,
    claims_report_post_render, provenance) so early LLM/paid-API stages are
    skipped as replays while render/qa/collect still execute for real.
    """
    candidates: list[Path] = []
    cwd_fixtures = Path("fixtures") / "artifacts"
    if cwd_fixtures.is_dir():
        candidates.append(cwd_fixtures)
    try:
        repo_root = Path(__file__).resolve().parents[2]
        repo_fixtures = repo_root / "fixtures" / "artifacts"
        if repo_fixtures.is_dir() and repo_fixtures not in candidates:
            candidates.append(repo_fixtures)
    except Exception:
        pass

    skip_names = {
        "render_manifest.json",
        "voiceover.json",
        "claims_report_post_render.json",
        "provenance.json",
    }

    seeded = False
    for src_root in candidates:
        for src in sorted(src_root.rglob("*.json")):
            try:
                rel = src.relative_to(src_root)
            except ValueError:
                continue
            if src.name in skip_names:
                continue
            # Angles and variants live under subdirs; keep the tree.
            dst = paths.artifacts / rel
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            seeded = True
        # Also copy contact_sheet / storyboard.html if present (optional).
        for extra in ("contact_sheet.png", "storyboard.html"):
            src_e = src_root / extra
            if src_e.is_file():
                dst_e = paths.artifacts / extra
                if not dst_e.exists():
                    shutil.copy2(src_e, dst_e)
        if seeded:
            break

    # Fallback: tests/fixtures holds the 8 contracted artifacts for unit tests.
    # Seed those too when fixtures/artifacts/ is absent (dev checkouts).
    if not seeded:
        test_fixtures: list[Path] = []
        for cand in (Path("tests") / "fixtures",):
            if cand.is_dir():
                test_fixtures.append(cand)
        try:
            repo_root = Path(__file__).resolve().parents[2]
            tf = repo_root / "tests" / "fixtures"
            if tf.is_dir() and tf not in test_fixtures:
                test_fixtures.append(tf)
        except Exception:
            pass
        for tf_root in test_fixtures:
            for src in sorted(tf_root.glob("*.json")):
                if src.name in skip_names:
                    continue
                dst = paths.artifacts / src.name
                if dst.exists():
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
