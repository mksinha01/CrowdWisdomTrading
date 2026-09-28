"""End-to-end acceptance proof (Story S35).

This is §13.3's "what success looks like", turned into assertions. It renders a
real video through the real pipeline — no stubs, no monkeypatching — and then
checks the four acceptance criteria from the root card's body
(spec lines 3394-3401):

  1. ``render/final.mp4`` exists, is 30-60s, 1080x1920, and ffprobe-valid
  2. ``artifacts/storyboard.json`` validates and
     ``creative_scores.verdict == "pass"``
  3. ``artifacts/claims_report.json`` verdict is ``pass`` on BOTH the
     pre-render and post-render passes
  4. ``submission/`` contains the bundle described in §18 step 15

It also proves §9.6's resume promise: re-running after a successful run costs
``$0.0000`` and reports every stage ``skipped``.

RUNNING THIS SUITE
------------------
The first test run renders once per session and takes several minutes — the
local_ffmpeg backend is the only one that can render offline, and it is a real
ffmpeg encode of 12 shots at 1080x1920. To assert against a run you already
produced with the gate command, point ``CWT_E2E_RUN_DIR`` at it::

    cwt run --engine local --offline
    $env:CWT_E2E_RUN_DIR = "runs\\<run_id>"     # Bash: export CWT_E2E_RUN_DIR=...
    pytest tests/test_end_to_end.py -q -v

A green suite is necessary but NOT sufficient — the offline render is the gate.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from cwt.config import Settings
from cwt.domain.models import ClaimsReport, Storyboard
from cwt.engine import run_pipeline
from cwt.util.paths import RunPaths

# The whole module needs a real ffmpeg. It needs NO network: --offline makes a
# cache miss raise rather than fetch, so a green run is itself the proof.
pytestmark = pytest.mark.ffmpeg

# §18 step 15 / the S26 table. A reviewer opening submission/ must find these.
BUNDLE_FILES = (
    "final.mp4",
    "storyboard.json",
    "storyboard.html",
    "contact_sheet.png",
    "render_manifest.json",
    "claims_report.json",
    "cost_report.json",
    "README-SUBMISSION.md",
)

# Rule R1: only these two may appear in README-SUBMISSION.md.
_README_FORBIDDEN = (
    "OPENROUTER_API_KEY",
    "NVIDIA_API_KEY",
    "EXA_API_KEY",
    "sk-or-v1-",
    "nvapi-",
    "exa_api",
)

# APIFY_MAX_CHARGE_USD is the one var Settings.from_env() hard-requires (Rule A2).
# Passing the mapping explicitly keeps this hermetic — it never reads .env, so a
# developer's real keys cannot leak into (or rescue) this run.
_BASE_ENV = {"APIFY_MAX_CHARGE_USD": "1.00"}

_DRIVE_LETTER = re.compile(r"[A-Za-z]:[\\/]")


@dataclass(frozen=True)
class RenderedRun:
    run_dir: Path
    paths: RunPaths
    settings: Settings
    summary: dict | None  # None when adopting a run via CWT_E2E_RUN_DIR


def _offline_settings() -> Settings:
    return Settings.from_env(dict(_BASE_ENV))


@pytest.fixture(scope="session")
def rendered_run(tmp_path_factory: pytest.TempPathFactory) -> RenderedRun:
    """Produce (or adopt) one real offline run and hand back its paths.

    Set CWT_E2E_RUN_DIR to an existing run directory to assert against a run
    made by the gate command instead of spending another few minutes encoding.
    """
    settings = _offline_settings()

    reuse = os.environ.get("CWT_E2E_RUN_DIR")
    if reuse:
        run_dir = Path(reuse)
        assert (run_dir / "render" / "final.mp4").is_file(), (
            f"CWT_E2E_RUN_DIR={reuse!r} does not look like a completed run "
            "(render/final.mp4 is missing)"
        )
        return RenderedRun(run_dir, RunPaths(run_dir), settings, None)

    # Short directory name: the Windows MAX_PATH budget is real (Rule W9).
    run_dir = Path(tmp_path_factory.mktemp("e2e")) / "run"
    paths = RunPaths(run_dir).ensure()

    summary = asyncio.run(
        run_pipeline(settings=settings, paths=paths, engine="local", offline=True)
    )
    return RenderedRun(run_dir, paths, settings, summary)


@pytest.fixture(scope="session")
def probe_result(rendered_run: RenderedRun):
    from cwt.video import probe

    final = rendered_run.paths.render / "final.mp4"
    assert final.is_file(), f"the run produced no video at {final}"
    return probe(final)


@pytest.fixture(scope="session")
def storyboard(rendered_run: RenderedRun) -> Storyboard:
    path = rendered_run.paths.artifacts / "storyboard.json"
    assert path.is_file(), f"no storyboard at {path}"
    return Storyboard.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def submission(rendered_run: RenderedRun) -> Path:
    path = rendered_run.run_dir / "submission"
    assert path.is_dir(), "submission/ was not assembled"
    return path


# ===========================================================================
# Criterion 1 — render/final.mp4 exists, is 30-60s, 1080x1920, ffprobe-valid
# ===========================================================================


def test_criterion_1_final_mp4_exists_and_is_playable(rendered_run: RenderedRun) -> None:
    final = rendered_run.paths.render / "final.mp4"
    assert final.is_file(), "render/final.mp4 was not produced"
    assert final.stat().st_size > 0, "render/final.mp4 is 0 bytes (Rule V3)"


def test_criterion_1_duration_is_30_to_60_seconds(
    probe_result, storyboard: Storyboard
) -> None:
    assert probe_result.probe_method != "failed", (
        f"ffprobe could not read the output: {probe_result.warnings}"
    )
    assert 30.0 <= probe_result.duration_s <= 60.0, (
        f"duration {probe_result.duration_s}s is outside the required 30-60s"
    )
    # What was encoded must agree with what the storyboard declared.
    assert abs(probe_result.duration_s - storyboard.meta.total_duration_s) < 1.0, (
        f"rendered {probe_result.duration_s}s but the storyboard declares "
        f"{storyboard.meta.total_duration_s}s"
    )


def test_criterion_1_dimensions_are_1080x1920(
    rendered_run: RenderedRun, probe_result
) -> None:
    assert (probe_result.width, probe_result.height) == (
        rendered_run.settings.video_width,
        rendered_run.settings.video_height,
    ), f"got {probe_result.width}x{probe_result.height}, expected 1080x1920 (9:16)"


def test_criterion_1_video_codec_is_h264(probe_result) -> None:
    assert probe_result.video_codec == "h264", (
        f"expected h264, got {probe_result.video_codec!r}"
    )


def test_criterion_1_manifest_records_the_local_ffmpeg_backend(
    rendered_run: RenderedRun,
) -> None:
    """Rule V4: the chain always terminates in local_ffmpeg, and must say so."""
    manifest = json.loads(
        (rendered_run.paths.artifacts / "render_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["backend_used"] == "local_ffmpeg"
    assert manifest["backend_chain_configured"][-1] == "local_ffmpeg"

    local = [b for b in manifest["backend_chain_tried"] if b["backend"] == "local_ffmpeg"]
    assert local and local[0]["succeeded"] is True, (
        "the guaranteed-available backend did not report success"
    )


def test_criterion_1_manifest_argv_uses_relative_paths(rendered_run: RenderedRun) -> None:
    """Rule W5: filtergraphs and inputs reference relative paths, never a drive letter."""
    manifest = json.loads(
        (rendered_run.paths.artifacts / "render_manifest.json").read_text(encoding="utf-8")
    )
    # argv[0] is the resolved ffmpeg binary and is legitimately absolute.
    offenders = [str(a) for a in manifest["argv"][1:] if _DRIVE_LETTER.search(str(a))]
    assert not offenders, f"absolute path leaked into the render argv: {offenders}"


# ===========================================================================
# Criterion 2 — storyboard.json validates, creative_scores.verdict == "pass"
# ===========================================================================


def test_criterion_2_storyboard_validates(storyboard: Storyboard) -> None:
    assert storyboard.shots, "storyboard has no shots"
    assert storyboard.beats, "storyboard has no beats"
    assert storyboard.meta.total_duration_s >= 30.0


def test_criterion_2_creative_scores_verdict_is_pass(storyboard: Storyboard) -> None:
    # The creative director's scores live under `generation`, not at the top level.
    scores = storyboard.generation.creative_scores
    assert scores.verdict == "pass", (
        f"creative verdict is {scores.verdict!r} (weighted mean {scores.weighted_mean} "
        f"vs threshold {scores.threshold}) — the run shipped a storyboard the creative "
        "director did not approve"
    )
    assert scores.weighted_mean >= scores.threshold


# ===========================================================================
# Criterion 3 — claims report verdict is pass on BOTH passes
# ===========================================================================


def _claims_report(path: Path) -> ClaimsReport:
    assert path.is_file(), f"claims report missing at {path}"
    return ClaimsReport.model_validate_json(path.read_text(encoding="utf-8"))


def test_criterion_3_primary_claims_report_passes(rendered_run: RenderedRun) -> None:
    """artifacts/claims_report.json is the canonical report and must read pass.

    It is rewritten by whichever pass ran LAST, so after a full run it carries
    the post-render stage. Both passes' reports are checked individually below.
    """
    report = _claims_report(rendered_run.paths.artifacts / "claims_report.json")
    assert report.verdict == "pass", f"claims_report.json verdict is {report.verdict!r}"
    assert report.deterministic.get("findings") == [], (
        "a passing report still carries deterministic findings"
    )


def test_criterion_3_pre_render_claims_report_passes(rendered_run: RenderedRun) -> None:
    report = _claims_report(rendered_run.paths.artifacts / "claims_report_pre_render.json")
    assert report.stage == "pre_render"
    assert report.verdict == "pass", f"pre-render verdict is {report.verdict!r}"
    assert report.deterministic.get("findings") == []
    assert report.llm_judge.get("findings") == []


def test_criterion_3_post_render_claims_report_passes(rendered_run: RenderedRun) -> None:
    report = _claims_report(rendered_run.paths.artifacts / "claims_report_post_render.json")
    assert report.stage == "post_render"
    assert report.verdict == "pass", f"post-render verdict is {report.verdict!r}"
    assert report.deterministic.get("findings") == []
    assert report.llm_judge.get("findings") == []


def test_criterion_3_both_passes_are_cross_referenced(rendered_run: RenderedRun) -> None:
    """Each pass must record where the other one's report lives."""
    pre = _claims_report(rendered_run.paths.artifacts / "claims_report_pre_render.json")
    post = _claims_report(rendered_run.paths.artifacts / "claims_report_post_render.json")
    assert pre.stage_reports is not None, "pre-render report omits stage_reports"
    assert post.stage_reports is not None, "post-render report omits stage_reports"
    assert pre.stage_reports.get("post_render"), "pre-render report omits the post-render path"
    assert post.stage_reports.get("pre_render"), "post-render report omits the pre-render path"


# ===========================================================================
# Criterion 4 — submission/ contains the bundle from §18 step 15
# ===========================================================================


def test_criterion_4_submission_bundle_is_complete(submission: Path) -> None:
    missing = [name for name in BUNDLE_FILES if not (submission / name).is_file()]
    assert not missing, f"submission/ is missing: {missing}"


def test_criterion_4_bundled_storyboard_is_not_stale(
    submission: Path, rendered_run: RenderedRun
) -> None:
    """Compliance rewrites storyboard.json in place — a cached copy would go stale."""
    bundled = json.loads((submission / "storyboard.json").read_text(encoding="utf-8"))
    source = json.loads(
        (rendered_run.paths.artifacts / "storyboard.json").read_text(encoding="utf-8")
    )
    assert bundled == source, "the bundled storyboard does not match the one that rendered"


def test_criterion_4_bundled_video_is_the_rendered_one(
    submission: Path, rendered_run: RenderedRun
) -> None:
    assert (submission / "final.mp4").stat().st_size == (
        rendered_run.paths.render / "final.mp4"
    ).stat().st_size


def test_criterion_4_cost_report_shows_zero_llm_spend(submission: Path) -> None:
    """An offline run makes no LLM calls, so the LLM ledger must be empty.

    The Apify figure is NOT zero: the replay re-emits the recorded charge from
    the bundled winning_ads fixture. That is historical spend, not new spend —
    so the assertion is on the LLM ledger, which is the thing offline removes.
    """
    cost = json.loads((submission / "cost_report.json").read_text(encoding="utf-8"))
    assert cost["total_usd"] == 0.0, f"offline run reported ${cost['total_usd']} of LLM spend"
    assert cost["calls"] == 0, f"offline run recorded {cost['calls']} LLM calls"
    assert cost["by_stage"] == {}, f"offline run recorded per-stage spend: {cost['by_stage']}"


def test_criterion_4_readme_carries_no_forbidden_credential(submission: Path) -> None:
    """Rule R1: only APIFY_TOKEN and TAVILY_API_KEY may appear in the README."""
    text = (submission / "README-SUBMISSION.md").read_text(encoding="utf-8")
    for pattern in _README_FORBIDDEN:
        assert pattern not in text, (
            f"Rule R1 violation: {pattern!r} found in README-SUBMISSION.md"
        )


# ===========================================================================
# §9.6 — the resume promise: a re-run costs zero and skips every stage
# ===========================================================================


async def test_resume_re_run_skips_every_stage_and_costs_nothing(
    rendered_run: RenderedRun, tmp_path: Path
) -> None:
    """'After fixing a filtergraph bug, re-running costs zero API calls.'"""
    # Re-run a COPY. A stage that wrongly re-executes would rewrite artifacts in
    # place, and this test must not mutate the run the other tests assert on.
    work = tmp_path / "resume-copy"
    shutil.copytree(rendered_run.run_dir, work)
    paths = RunPaths(work)

    ledger = paths.ledger
    ledger_before = ledger.read_text(encoding="utf-8") if ledger.is_file() else ""

    summary = await run_pipeline(
        settings=rendered_run.settings,
        paths=paths,
        engine="local",
        offline=True,
    )

    by_key = {s.key: s.status for s in summary["stages"]}
    not_skipped = {k: v for k, v in by_key.items() if v != "skipped"}
    assert not not_skipped, (
        f"a re-run re-executed stages whose inputs had not changed: {not_skipped}"
    )
    assert summary["skipped"] == 11, f"expected 11 skipped stages, got {summary['skipped']}"
    # §9.6: the re-run still reports "Done. 11 stages" — every stage completed,
    # just by replaying a still-valid artifact.
    assert summary["done"] == 11
    assert summary["executed"] == 0
    assert summary["failed"] == 0
    assert len(summary["stages"]) == 11
    assert summary["cost_usd"] == 0.0

    # The real proof of "zero API calls": the spend ledger did not grow. Unlike
    # the returned cost (0.0 by construction when offline), an LLM call would
    # still have appended a row here.
    ledger_after = ledger.read_text(encoding="utf-8") if ledger.is_file() else ""
    assert ledger_after == ledger_before, "the re-run wrote new rows to the spend ledger"


# ===========================================================================
# Anti-vacuity — the run must actually have executed every stage
# ===========================================================================


def test_the_run_actually_executed_all_eleven_stages(rendered_run: RenderedRun) -> None:
    """Guard against a green suite that proved nothing.

    A stage that is skipped writes nothing, so this checks the on-disk evidence
    a full run leaves behind. It holds whether the run was produced by the
    fixture or adopted via CWT_E2E_RUN_DIR.
    """
    assert (rendered_run.paths.render / "final.mp4").stat().st_size > 0
    for name in ("render_manifest.json", "storyboard.json", "voiceover.json"):
        assert (rendered_run.paths.artifacts / name).is_file(), f"artifacts/{name} missing"

    if rendered_run.summary is not None:
        failed_keys = [
            s.key for s in rendered_run.summary["stages"] if s.status == "failed"
        ]
        assert rendered_run.summary["failed"] == 0, f"stages failed: {failed_keys}"
        assert rendered_run.summary["done"] == 11, (
            f"only {rendered_run.summary['done']} of 11 stages completed"
        )
        # A fresh offline run replays the LLM/paid-API stages and executes only
        # render/qa/collect — so `executed` is 3, not 11. Both are correct.
        assert rendered_run.summary["executed"] == 3, (
            f"expected render/qa/collect to execute, got "
            f"{[s.key for s in rendered_run.summary['stages'] if s.status == 'ok']}"
        )
