"""Tests for doctor.py preflight and cli.py entrypoint contracts (Story S33)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cwt.cli import (
    EXIT_BLOCKED,
    EXIT_BUDGET,
    EXIT_CONFIG,
    EXIT_OK,
    EXIT_TIMEOUT,
    _banner,
    _cmd_run,
    main,
)
from cwt.clients.llm import BudgetExceeded
from cwt.config import Settings
from cwt.doctor import (
    Check,
    Report,
    _check_backend_chain,
    _check_ffmpeg,
    _check_gateway,
    _check_hermes,
    _check_llm_models,
    _check_profiles,
    _check_python,
    _check_worker_model,
    run_doctor,
)
from cwt.hermes.board import PipelineBlocked, PipelineTimeout
from cwt.hermes.cli import HermesResult
from cwt.hermes.dag import DAG_SPEC
from cwt.util.paths import RunPaths


@pytest.fixture
def dummy_settings() -> Settings:
    return Settings(
        llm_provider="openrouter",
        openrouter_api_key="test_openrouter_key",
        openrouter_base_url="https://openrouter.ai/api/v1",
        nvidia_api_key="test_nvidia_key",
        nvidia_base_url="https://integrate.api.nvidia.com/v1",
        model_cheap="google/gemini-2.5-flash",
        model_strong="anthropic/claude-sonnet-4.5",
        model_fallbacks=["meta/llama-3.3-70b-instruct", "qwen/qwen2.5-72b-instruct"],
        llm_max_concurrency=4,
        llm_json_repair_attempts=2,
        llm_timeout_seconds=120.0,
        apify_token="test_apify",
        apify_ads_actor_id="apify~facebook-ads-scraper",
        apify_ads_actor_fallbacks=["curious_coder~facebook-ads-library-scraper"],
        apify_max_items=60,
        apify_max_charge_usd=1.00,
        apify_run_timeout_seconds=900,
        tavily_api_key="test_tavily",
        exa_api_key="test_exa",
        video_backend_chain=["hyperframes", "openmontage", "local_ffmpeg"],
        ffmpeg_bin="",
        ffprobe_bin="",
        video_width=1080,
        video_height=1920,
        video_fps=30,
        video_min_seconds=30,
        video_max_seconds=60,
        video_loudness_lufs=-14.0,
        video_true_peak_dbtp=-1.5,
        tts_backend_chain=["edge_tts", "piper", "silent"],
        edge_tts_voice="en-US-AndrewNeural",
        piper_voice_path="fixtures/assets/voices/en_US-ryan-high.onnx",
        hyperframes_enabled="auto",
        openmontage_home="",
        hermes_bin="",
        hermes_min_version="0.16.0",
        board="cwt-ads",
        run_timeout_seconds=5400,
        stall_threshold_seconds=180,
        max_usd=2.00,
        claims_gate_enabled=True,
        claims_max_rewrite_rounds=3,
        creative_threshold=8.0,
        creative_max_rounds=3,
        engine_defaults_to_hermes=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Doctor Checks Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_check_python():
    check = _check_python()
    assert check.name == "python"
    if sys.version_info >= (3, 11):
        assert check.status == "ok"
    else:
        assert check.status == "fail"

    with patch("sys.version_info", (3, 10, 0)):
        failed_check = _check_python()
        assert failed_check.status == "fail"
        assert "Python 3.11+ required" in failed_check.hint


def test_check_ffmpeg_warns_when_only_ffprobe_missing():
    with patch("cwt.video.ffmpeg_bin.ffmpeg_path", return_value="C:\\bin\\ffmpeg.exe"), \
         patch("cwt.video.ffmpeg_bin.ffmpeg_version", return_value="7.1"), \
         patch("cwt.video.ffmpeg_bin.ffprobe_path", side_effect=Exception("ffprobe not found")):
        check = _check_ffmpeg()
        assert check.name == "ffmpeg"
        assert check.status == "warn"
        assert "ffprobe missing" in check.detail
        assert "Rule W3" in check.hint


def test_check_ffmpeg_fails_when_ffmpeg_missing():
    with patch("cwt.video.ffmpeg_bin.ffmpeg_path", side_effect=RuntimeError("no ffmpeg")):
        check = _check_ffmpeg()
        assert check.status == "fail"
        assert "no ffmpeg" in check.detail


def test_check_ffmpeg_ok_when_both_present():
    with patch("cwt.video.ffmpeg_bin.ffmpeg_path", return_value="C:\\bin\\ffmpeg.exe"), \
         patch("cwt.video.ffmpeg_bin.ffmpeg_version", return_value="7.1"), \
         patch("cwt.video.ffmpeg_bin.ffprobe_path", return_value="C:\\bin\\ffprobe.exe"):
        check = _check_ffmpeg()
        assert check.status == "ok"
        assert "7.1" in check.detail


def test_check_hermes_fails_when_missing():
    with patch("cwt.hermes.cli.hermes_bin", side_effect=RuntimeError("hermes not found")):
        check = _check_hermes()
        assert check.status == "fail"
        assert "hermes not found" in check.detail
        assert "install" in check.hint


def test_check_hermes_fails_when_missing_required_flags():
    with patch("cwt.hermes.cli.hermes_bin", return_value="hermes.exe"), \
         patch("cwt.hermes.cli.hermes_version", return_value="0.16.0"), \
         patch("cwt.hermes.cli.supported_flags", return_value={"--assignee", "--body"}):
        check = _check_hermes()
        assert check.status == "fail"
        assert "missing kanban flags" in check.detail


def test_check_worker_model():
    # Success
    with patch("cwt.hermes.cli.run_hermes", return_value=HermesResult(0, "google/gemini-2.5-flash\n", "")):
        check = _check_worker_model()
        assert check.status == "ok"
        assert "google/gemini-2.5-flash" in check.detail
        assert ">=64k context" in check.detail

    # Not set or warning
    with patch("cwt.hermes.cli.run_hermes", return_value=HermesResult(0, "", "")):
        check = _check_worker_model()
        assert check.status == "warn"
        assert "not set" in check.detail
        assert ">=64k context" in check.hint
        assert "In --offline mode the workers still run on this model." in check.hint


def test_check_profiles_fails_naming_missing_profile():
    # Return output missing cwt-script-writer
    output = "cwt-orchestrator\ncwt-ads-manager\ncwt-hook-analyst\ncwt-researcher\ncwt-compliance\ncwt-video-editor\ncwt-qa\n"
    with patch("cwt.hermes.cli.run_hermes", return_value=HermesResult(0, output, "")):
        check = _check_profiles()
        assert check.status == "fail"
        assert "missing profiles" in check.detail
        assert "cwt-script-writer" in check.detail
        assert "Run `cwt bootstrap` to create the nine CWT profiles." in check.hint


def test_check_profiles_passes_when_all_dag_assignees_present():
    dag_assignees = {c.assignee for c in DAG_SPEC}
    output = "\n".join(dag_assignees | {"cwt-creative-director"})
    with patch("cwt.hermes.cli.run_hermes", return_value=HermesResult(0, output, "")):
        check = _check_profiles()
        assert check.status == "ok"
        assert "9 CWT profiles found" in check.detail or "8 CWT profiles found" in check.detail


def test_check_backend_chain_fails_not_ending_in_local_ffmpeg(dummy_settings: Settings):
    bad_settings = MagicMock(spec=Settings)
    bad_settings.video_backend_chain = ["hyperframes", "openmontage"]
    check = _check_backend_chain(bad_settings)
    assert check.status == "fail"
    assert "VIDEO_BACKEND_CHAIN must end with local_ffmpeg" in check.hint


def test_check_backend_chain_ok_with_local_ffmpeg(dummy_settings: Settings):
    check = _check_backend_chain(dummy_settings)
    assert check.status == "ok"
    assert "local_ffmpeg" in check.detail


def test_check_llm_models_fails_on_unknown_slug(dummy_settings: Settings):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": [
            {"id": "google/gemini-2.5-flash"},
            {"id": "meta/llama-3.3-70b-instruct"},
            {"id": "qwen/qwen2.5-72b-instruct"},
            # anthropic/claude-sonnet-4.5 is missing!
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("os.getenv", return_value="dummy_key"), \
         patch("httpx.get", return_value=mock_resp):
        check = _check_llm_models(dummy_settings)
        assert check.status == "fail"
        assert "not available" in check.detail
        assert "anthropic/claude-sonnet-4.5" in check.detail


def test_check_llm_models_warns_when_endpoint_unreachable(dummy_settings: Settings):
    with patch("os.getenv", return_value="dummy_key"), \
         patch("httpx.get", side_effect=Exception("Network error")):
        check = _check_llm_models(dummy_settings)
        assert check.status == "warn"
        assert "could not list models: Network error" in check.detail
        assert "Model validation skipped" in check.hint


def test_check_gateway(dummy_settings: Settings):
    with patch("cwt.hermes.cli.run_hermes", return_value=HermesResult(0, "ok", "")):
        check = _check_gateway(dummy_settings)
        assert check.status == "ok"
        assert "dispatcher reachable" in check.detail

    with patch("cwt.hermes.cli.run_hermes", return_value=HermesResult(1, "", "gateway is stopped")):
        check = _check_gateway(dummy_settings)
        assert check.status == "warn"
        assert "not running" in check.detail
        assert "hermes gateway start" in check.hint


# ─────────────────────────────────────────────────────────────────────────────
# run_doctor Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_run_doctor_returns_1_when_any_check_fails(dummy_settings: Settings):
    failing_check = Check("custom_fail", "fail", "bad", "fix it")
    ok_check = Check("custom_ok", "ok", "good")

    with patch("cwt.doctor._check_python", return_value=failing_check), \
         patch("cwt.doctor._check_ffmpeg", return_value=ok_check), \
         patch("cwt.doctor._check_hermes", return_value=ok_check), \
         patch("cwt.doctor._check_worker_model", return_value=ok_check), \
         patch("cwt.doctor._check_profiles", return_value=ok_check), \
         patch("cwt.doctor._check_backend_chain", return_value=ok_check), \
         patch("cwt.doctor._check_llm_models", return_value=ok_check), \
         patch("cwt.doctor._check_gateway", return_value=ok_check):
        exit_code = run_doctor(settings=dummy_settings)
        assert exit_code == 1


def test_run_doctor_returns_0_when_all_checks_pass(dummy_settings: Settings):
    ok_check = Check("custom_ok", "ok", "good")
    warn_check = Check("custom_warn", "warn", "warning", "be careful")

    with patch("cwt.doctor._check_python", return_value=ok_check), \
         patch("cwt.doctor._check_ffmpeg", return_value=warn_check), \
         patch("cwt.doctor._check_hermes", return_value=ok_check), \
         patch("cwt.doctor._check_worker_model", return_value=ok_check), \
         patch("cwt.doctor._check_profiles", return_value=ok_check), \
         patch("cwt.doctor._check_backend_chain", return_value=ok_check), \
         patch("cwt.doctor._check_llm_models", return_value=ok_check), \
         patch("cwt.doctor._check_gateway", return_value=ok_check):
        exit_code = run_doctor(settings=dummy_settings)
        assert exit_code == 0


def test_run_doctor_json_output(capsys, dummy_settings: Settings):
    ok_check = Check("item1", "ok", "all good", "")
    warn_check = Check("item2", "warn", "missing optional", "hint text")

    with patch("cwt.doctor._check_python", return_value=ok_check), \
         patch("cwt.doctor._check_ffmpeg", return_value=warn_check), \
         patch("cwt.doctor._check_hermes", return_value=ok_check), \
         patch("cwt.doctor._check_worker_model", return_value=ok_check), \
         patch("cwt.doctor._check_profiles", return_value=ok_check), \
         patch("cwt.doctor._check_backend_chain", return_value=ok_check), \
         patch("cwt.doctor._check_llm_models", return_value=ok_check), \
         patch("cwt.doctor._check_gateway", return_value=ok_check):
        exit_code = run_doctor(json_output=True, settings=dummy_settings)
        assert exit_code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)
    assert len(data) >= 7
    for item in data:
        assert "name" in item
        assert "status" in item
        assert "detail" in item
        assert "hint" in item


# ─────────────────────────────────────────────────────────────────────────────
# CLI & Banner Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_banner_prints_not_found_and_does_not_raise(capsys, dummy_settings: Settings):
    paths = RunPaths(Path("runs/test-run"))
    with patch("cwt.video.ffmpeg_bin.ffmpeg_path", side_effect=RuntimeError("no ffmpeg")), \
         patch("cwt.hermes.cli.hermes_bin", side_effect=RuntimeError("no hermes")):
        # Must never raise
        _banner(dummy_settings, paths, offline=True, engine="hermes")

    captured = capsys.readouterr()
    assert "NOT FOUND: no ffmpeg" in captured.out
    assert "NOT FOUND: no hermes" in captured.out
    assert "=== CWT Video Ads Agent ===" in captured.out


@pytest.mark.asyncio
async def test_cmd_run_exit_codes(dummy_settings: Settings):
    args = argparse.Namespace(
        backend=None,
        run_id="test_run",
        run_dir="runs",
        resume=False,
        engine="local",
        offline=True,
        record_pacing=False,
        force_stage=[],
    )

    with patch("cwt.config.Settings.from_env", return_value=dummy_settings), \
         patch("cwt.cli._banner"):

        # 1. BudgetExceeded -> 4
        with patch("cwt.engine.run_pipeline", side_effect=BudgetExceeded(2.5, 2.0, "render")):
            code = await _cmd_run(args)
            assert code == EXIT_BUDGET

        # 2. PipelineBlocked -> 3
        with patch("cwt.engine.run_pipeline", side_effect=PipelineBlocked("blocked")):
            code = await _cmd_run(args)
            assert code == EXIT_BLOCKED

        # 3. PipelineTimeout -> 2
        with patch("cwt.engine.run_pipeline", side_effect=PipelineTimeout("timed out")):
            code = await _cmd_run(args)
            assert code == EXIT_TIMEOUT

        # 4. Unexpected / ConfigError -> 1
        with patch("cwt.engine.run_pipeline", side_effect=ValueError("bad config")):
            code = await _cmd_run(args)
            assert code == EXIT_CONFIG

        # 5. Success -> 0
        summary = {"done": 12, "cost_usd": 0.1234, "output": "runs/test_run/render/final.mp4"}
        with patch("cwt.engine.run_pipeline", new=AsyncMock(return_value=summary)):
            code = await _cmd_run(args)
            assert code == EXIT_OK


@pytest.mark.asyncio
async def test_cmd_run_resume_calls_board_resume(dummy_settings: Settings):
    args = argparse.Namespace(
        backend=None,
        run_id="test_resume",
        run_dir="runs",
        resume=True,
        engine="hermes",
        offline=False,
        record_pacing=False,
        force_stage=[],
    )

    summary = {"done": 12, "cost_usd": 0.0, "output": "runs/test_resume/render/final.mp4"}
    with patch("cwt.config.Settings.from_env", return_value=dummy_settings), \
         patch("cwt.hermes.board.resume") as mock_resume, \
         patch("cwt.cli._banner"), \
         patch("cwt.engine.run_pipeline", new=AsyncMock(return_value=summary)):
        code = await _cmd_run(args)
        assert code == EXIT_OK
        mock_resume.assert_called_once_with("test_resume", dummy_settings.board)


def test_cli_clean_subcommand(tmp_path: Path, capsys):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "20260901-0001").mkdir()
    (runs_dir / "20260902-0002").mkdir()
    (runs_dir / "20260903-0003").mkdir()
    diag_dir = runs_dir / "_diagnostics"
    diag_dir.mkdir()
    (diag_dir / "preserve.json").write_text("{}", encoding="utf-8")

    with patch("pathlib.Path.glob") as mock_glob, \
         patch("shutil.rmtree") as mock_rmtree:
        r1 = runs_dir / "20260903-0003"
        r2 = runs_dir / "20260902-0002"
        r3 = runs_dir / "20260901-0001"
        # Return in sorted order (most recent first)
        mock_glob.return_value = [r1, r2, r3]

        code = main(["clean"])
        assert code == EXIT_OK
        # r2 and r3 should be removed, r1 kept
        assert mock_rmtree.call_count == 2
        mock_rmtree.assert_any_call(r2, ignore_errors=True)
        mock_rmtree.assert_any_call(r3, ignore_errors=True)
        assert diag_dir.exists()
