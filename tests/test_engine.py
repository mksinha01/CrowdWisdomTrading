"""Tests for the pipeline engine (Story S32).

Covers the frozen interface contract and the build steps:
- fully cached run returns every stage skipped and cost_usd == 0.0
- --force-stage render re-runs exactly one stage
- a stage failure stops the run and later stages are absent
- BudgetExceeded propagates out of run_pipeline
- offline mode with a missing fixture raises OfflineFixtureMissing
- the three research stages run concurrently
- _run_hermes raises the `cwt bootstrap` message when profiles are absent
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from cwt.clients.http_cache import OfflineFixtureMissing
from cwt.clients.llm import BudgetExceeded
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactStore
from cwt.engine import (
    STAGE_ORDER,
    StageResult,
    _build_cache,
    _build_client,
    _run_hermes,
    _run_local,
    _run_research_block,
    _run_stage_local,
    _stage_is_valid,
    run_pipeline,
)
from cwt.util.paths import RunPaths

FIXTURES = Path(__file__).parent / "fixtures"
REPO_FIXTURES = Path("fixtures") / "artifacts"


def _settings() -> Settings:
    return Settings.from_env()


def _seed_all_valid(paths: RunPaths) -> None:
    """Copy offline fixtures into the run dir and stub render/qa/collect outputs."""
    paths.ensure()
    if REPO_FIXTURES.is_dir():
        for src in sorted(REPO_FIXTURES.rglob("*.json")):
            rel = src.relative_to(REPO_FIXTURES)
            dst = paths.artifacts / rel
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    # Stub render outputs so render/qa/collect are all valid.
    manifest_src = paths.artifacts / "storyboard.json"
    assert manifest_src.is_file(), "storyboard fixture must be seeded first"
    sb = json.loads(manifest_src.read_text(encoding="utf-8"))
    total_dur = float(sb["meta"]["total_duration_s"])
    disc_id = sb["compliance"]["risk_disclosure_shot_id"]
    render_manifest = {
        "schema_version": 1,
        "backend_chain_configured": ["local_ffmpeg"],
        "backend_chain_tried": [
            {"backend": "local_ffmpeg", "available": True, "attempted": True,
             "succeeded": True, "elapsed_s": 1.0}
        ],
        "backend_used": "local_ffmpeg",
        "output": {
            "path": "render/final.mp4",
            "sha256": "00" * 32,
            "duration_s": total_dur,
            "width": 1080,
            "height": 1920,
            "fps": 30.0,
            "video_codec": "h264",
            "audio_codec": "aac",
        },
        "loudness": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5, "lra": 10.0},
        "argv": ["ffmpeg", "-y", "out.mp4"],
        "cwd": str(paths.render),
        "assets": [],
        "ffmpeg_version": "7.1",
        "warnings": [],
        "shots": [
            {"id": "s01", "start_s": 0.0, "duration_s": round(total_dur - 3.2, 2),
             "rendered_clip": "render/shot_s01.mp4"},
            {"id": disc_id, "start_s": round(total_dur - 3.2, 2), "duration_s": 3.2,
             "rendered_clip": f"render/shot_{disc_id}.mp4"},
        ],
    }
    (paths.artifacts / "render_manifest.json").write_text(
        json.dumps(render_manifest, indent=2), encoding="utf-8"
    )
    (paths.render / "final.mp4").write_bytes(b"\x00" * 1024)
    post = {
        "schema_version": 1,
        "stage": "post_render",
        "verdict": "pass",
        "deterministic": {"ruleset_version": "1.0.0", "findings": []},
        "llm_judge": {"model_tier": "cheap", "findings": [], "escalations": 0},
        "rewrite_instructions": [],
        "rounds_used": 0,
        "rounds_remaining": 3,
    }
    (paths.artifacts / "claims_report_post_render.json").write_text(
        json.dumps(post, indent=2), encoding="utf-8"
    )
    sub = paths.run_dir / "submission"
    sub.mkdir(parents=True, exist_ok=True)
    shutil.copy2(paths.artifacts / "storyboard.json", sub / "storyboard.json")
    shutil.copy2(paths.render / "final.mp4", sub / "final.mp4")


def test_stage_order_has_11_and_no_root() -> None:
    assert isinstance(STAGE_ORDER, tuple)
    assert len(STAGE_ORDER) == 11
    assert "root" not in STAGE_ORDER
    assert STAGE_ORDER == (
        "ads", "patterns", "res_pain", "res_unique", "res_crowd", "brief",
        "script", "compliance", "render", "qa", "collect",
    )


def test_build_helpers_contract() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        paths = RunPaths(Path(td) / "run1").ensure()
        settings = _settings()
        cache = _build_cache(paths, False)
        assert cache.offline is False
        cache_off = _build_cache(paths, True)
        assert cache_off.offline is True
        client = _build_client(settings, paths)
        assert str(client.ledger_path) == str(paths.ledger)


@pytest.mark.asyncio
async def test_fully_cached_run_returns_all_skipped_and_zero_cost(tmp_path: Path) -> None:
    paths = RunPaths(tmp_path / "run-cached").ensure()
    _seed_all_valid(paths)
    settings = _settings()
    summary = await run_pipeline(
        settings=settings, paths=paths, engine="local", offline=False, force_stage=[]
    )
    assert summary["cost_usd"] == 0.0
    assert summary["skipped"] == 11
    # §13.3's display contract: a replayed stage still counts as done, so a
    # fully-cached run reports the same "11 stages" as a fresh one.
    assert summary["done"] == 11
    assert summary["executed"] == 0
    assert summary["failed"] == 0
    assert summary["total"] == 11
    assert len(summary["stages"]) == 11
    assert all(isinstance(s, StageResult) and s.status == "skipped" for s in summary["stages"])


@pytest.mark.asyncio
async def test_force_stage_render_reruns_exactly_one_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = RunPaths(tmp_path / "run-force").ensure()
    _seed_all_valid(paths)
    settings = _settings()

    calls: list[str] = []

    def fake_synth(*, settings: Settings, paths: RunPaths) -> dict[str, Any]:
        calls.append("synth")
        return {"artifact_path": str(paths.artifacts / "voiceover.json"),
                "backend_used": "silent", "duration_s": 42.0, "words": 10,
                "chain_tried": [], "has_audio": False}

    def fake_render(*, settings: Settings, paths: RunPaths, backend: str | None = None) -> dict[str, Any]:
        calls.append("render")
        return {"artifact_path": str(paths.artifacts / "render_manifest.json"),
                "output": str(paths.render / "final.mp4"), "backend_used": "local_ffmpeg",
                "duration_s": 42.0, "width": 1080, "height": 1920,
                "chain_tried": [], "assets": 0}

    monkeypatch.setattr("cwt.tools.video.synthesize_voiceover", fake_synth)
    monkeypatch.setattr("cwt.tools.video.render_video", fake_render)

    summary = await run_pipeline(
        settings=settings, paths=paths, engine="local", offline=False,
        force_stage=["render"],
    )
    by_key = {s.key: s.status for s in summary["stages"]}
    assert by_key["render"] == "ok"
    assert calls == ["synth", "render"]
    for k in STAGE_ORDER:
        if k != "render":
            assert by_key[k] == "skipped", f"{k} should be skipped"


@pytest.mark.asyncio
async def test_stage_failure_stops_run_and_later_stages_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = RunPaths(tmp_path / "run-fail").ensure()
    settings = _settings()

    def boom(*, settings: Settings, paths: RunPaths, **kw: Any) -> dict[str, Any]:
        raise RuntimeError("apify exploded")

    monkeypatch.setattr("cwt.tools.ads.source_winning_ads", boom)
    summary = await run_pipeline(
        settings=settings, paths=paths, engine="local", offline=False, force_stage=[]
    )
    assert len(summary["stages"]) == 1
    assert summary["stages"][0].key == "ads"
    assert summary["stages"][0].status == "failed"
    assert "apify exploded" in (summary["stages"][0].error or "")


@pytest.mark.asyncio
async def test_budget_exceeded_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = RunPaths(tmp_path / "run-budget").ensure()
    settings = _settings()

    def boom(*, settings: Settings, paths: RunPaths, **kw: Any) -> dict[str, Any]:
        raise BudgetExceeded(2.5, 2.0, "patterns_extract")

    monkeypatch.setattr("cwt.tools.ads.source_winning_ads", lambda **kw: {"artifact_path": "x"})
    monkeypatch.setattr("cwt.tools.ads.rank_winning_ads", lambda **kw: {"artifact_path": "x"})
    # Seed winning_ads so patterns stage attempts to run, then blows the budget.
    import shutil as _sh

    if REPO_FIXTURES.is_dir():
        _sh.copy2(REPO_FIXTURES / "winning_ads.json", paths.artifacts / "winning_ads.json")
    monkeypatch.setattr("cwt.tools.patterns.extract_ad_patterns", boom)

    with pytest.raises(BudgetExceeded):
        await run_pipeline(
            settings=settings, paths=paths, engine="local", offline=False, force_stage=[]
        )


@pytest.mark.asyncio
async def test_offline_missing_fixture_raises(tmp_path: Path) -> None:
    paths = RunPaths(tmp_path / "run-offmiss").ensure()
    settings = _settings()
    cache = _build_cache(paths, True)
    # Empty angles dir -> research block in offline mode must raise loudly.
    with pytest.raises(OfflineFixtureMissing):
        await _run_research_block(settings, paths, None, cache, [], True)


@pytest.mark.asyncio
async def test_research_stages_run_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = RunPaths(tmp_path / "run-conc").ensure()
    settings = _settings()
    cache = _build_cache(paths, False)

    async def _fake_to_thread(fn: Any, *a: Any, **k: Any) -> Any:
        await asyncio.sleep(0.3)
        angle = k.get("angle", "pain")
        angles_dir = paths.artifacts / "angles"
        angles_dir.mkdir(parents=True, exist_ok=True)
        (angles_dir / f"{angle}.json").write_text(
            json.dumps({"angle": angle, "claims": [], "synthesis": "s",
                        "search_queries_used": []}), encoding="utf-8"
        )
        return {"angle_file": str(angles_dir / f"{angle}.json")}

    monkeypatch.setattr("cwt.engine.asyncio.to_thread", _fake_to_thread)

    t0 = time.monotonic()
    results = await _run_research_block(settings, paths, None, cache, ["res_pain", "res_unique", "res_crowd"], False)
    elapsed = time.monotonic() - t0
    assert len(results) == 3
    assert all(r.status == "ok" for r in results)
    # Sequential would take ~0.9s; concurrent must be well under the sum.
    assert elapsed < 0.3 * 3, f"research did not run concurrently (took {elapsed:.2f}s)"


@pytest.mark.asyncio
async def test_run_hermes_raises_bootstrap_message_when_profiles_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = RunPaths(tmp_path / "run-herm").ensure()
    settings = _settings()

    def _missing() -> str:
        raise RuntimeError("The `hermes` binary was not found on PATH.")

    monkeypatch.setattr("cwt.hermes.cli.hermes_bin", _missing)
    with pytest.raises(RuntimeError, match=r"cwt bootstrap"):
        await _run_hermes(settings, paths, offline=True, record_pacing=False, force_stage=[])


def test_stage_is_valid_force_bypass(tmp_path: Path) -> None:
    paths = RunPaths(tmp_path / "run-valid").ensure()
    assert _stage_is_valid("ads", paths, force=True) is False


@pytest.mark.asyncio
async def test_run_local_research_block_runs_once_not_thrice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the research block must run once (3 concurrent calls), not 3x."""
    paths = RunPaths(tmp_path / "run-once").ensure()
    settings = _settings()
    count = {"n": 0}

    def fake_angle(*, settings: Settings, paths: RunPaths, angle: str, **kw: Any) -> dict[str, Any]:
        count["n"] += 1
        angles_dir = paths.artifacts / "angles"
        angles_dir.mkdir(parents=True, exist_ok=True)
        (angles_dir / f"{angle}.json").write_text(
            json.dumps({"angle": angle, "claims": [], "synthesis": "s",
                        "search_queries_used": []}), encoding="utf-8"
        )
        return {"angle_file": str(angles_dir / f"{angle}.json"), "angle": angle}

    monkeypatch.setattr("cwt.tools.research.research_angle", fake_angle)
    # Make every other stage valid so only research executes.
    _seed_all_valid(paths)
    for angle in ("pain", "unique_data", "crowd_effect"):
        (paths.artifacts / "angles" / f"{angle}.json").unlink()
    # Keep brief invalid so the run continues past research, then stub the rest.
    (paths.artifacts / "research_brief.json").unlink(missing_ok=True)

    async def _fake_stage(key: str, *a: Any, **k: Any) -> str:
        if key in ("ads", "patterns"):
            return str(paths.artifacts / "winning_ads.json")
        return str(paths.artifacts / "storyboard.json")

    # Only count research calls; let other stages short-circuit via validity
    # except brief/script/compliance which we force to fail fast to stop the run.
    def _fake_valid(key: str, paths: RunPaths, *, force: bool) -> bool:
        if key in ("res_pain", "res_unique", "res_crowd"):
            return False
        if key == "brief":
            return False
        return True

    monkeypatch.setattr("cwt.engine._stage_is_valid", _fake_valid)
    monkeypatch.setattr(
        "cwt.tools.research.assemble_brief",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("stop after research")),
    )
    stages = await _run_local(settings, paths, offline=False, record_pacing=False, force_stage=[])
    assert count["n"] == 3, f"expected 3 research calls, got {count['n']}"
    research_keys = [s.key for s in stages if s.key.startswith("res_")]
    assert research_keys == ["res_pain", "res_unique", "res_crowd"]


@pytest.mark.asyncio
async def test_offline_render_stage_forces_silent_tts_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (S35): the offline render stage must force the silent TTS backend.

    ``edge_tts`` is a network backend, so an offline run must not reach for it.
    The original code expressed that as ``settings.model_copy(...)`` — but
    ``Settings`` is a frozen *dataclass*, not a pydantic model. It raised
    ``AttributeError: 'Settings' object has no attribute 'model_copy'``, the
    render stage failed, and ``cwt run --engine local --offline`` exited 0
    having produced no video at all. Exactly what the offline gate exists to catch.
    """
    paths = RunPaths(tmp_path / "run-silent-tts").ensure()
    settings = _settings()
    cache = _build_cache(paths, True)

    seen: list[list[str]] = []

    def fake_synth(*, settings: Settings, paths: RunPaths) -> dict[str, Any]:
        seen.append(list(settings.tts_backend_chain))
        return {
            "artifact_path": str(paths.artifacts / "voiceover.json"),
            "backend_used": "silent",
            "duration_s": 42.0,
            "words": 10,
            "chain_tried": [],
            "has_audio": False,
        }

    def fake_render(
        *, settings: Settings, paths: RunPaths, backend: str | None = None
    ) -> dict[str, Any]:
        return {
            "artifact_path": str(paths.artifacts / "render_manifest.json"),
            "output": str(paths.render / "final.mp4"),
            "backend_used": "local_ffmpeg",
            "duration_s": 42.0,
            "width": 1080,
            "height": 1920,
            "chain_tried": [],
            "assets": 0,
        }

    monkeypatch.setattr("cwt.tools.video.synthesize_voiceover", fake_synth)
    monkeypatch.setattr("cwt.tools.video.render_video", fake_render)

    await _run_stage_local("render", settings, paths, None, cache, True)
    assert seen == [["silent"]], "offline render did not pin the TTS chain to silent"

    # The live path must leave the operator's configured chain untouched.
    seen.clear()
    await _run_stage_local("render", settings, paths, None, cache, False)
    assert seen == [list(settings.tts_backend_chain)]
    assert settings.tts_backend_chain != ["silent"]
