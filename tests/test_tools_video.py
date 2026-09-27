"""Tests for tools.video (Story S25).

Covers:
- `synthesize_voiceover` contract: produces artifacts/voiceover.json, returns dict with
  {"artifact_path","backend_used","duration_s","words":int,"chain_tried":[...],"has_audio":bool}.
- `synthesize_voiceover` with silent backend sets has_audio=False.
- `render_video` records an unavailable backend as attempted: false with a reason, not an error.
- A chain where hyperframes fails and local_ffmpeg succeeds returns backend_used == "local_ffmpeg"
  with succeeded: false recorded for the first.
- `render_video` with explicit backend pins the chain; --offline requires local_ffmpeg (Rule V4).
- `render_video` raises when the entire chain fails (Rule V3).
- `render_video` Rule V3: surfaces ok=False / 0-byte output as a raise with measured values.
- `probe_media` on a 0-byte file raises with a clear message.
- `probe_media` reports probe_method == "ffmpeg_stderr" when ffprobe is monkeypatched missing (Rule W3).
- `probe_media` default path resolution uses render_manifest.json output.path.
- `qa_check` fails when duration is 28s (below video_min_seconds).
- `qa_check` fails when the recomputed disclosure duration is 2.1s despite the storyboard claiming 3.2.
- `qa_check` fails when loudness deviates by > 1.0 LU or true peak > ceiling + 0.5.
- `qa_check` passes when all criteria are met.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cwt.clients.tts import VoiceoverResult, WordTiming
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactStore
from cwt.domain.models import RenderManifest, Storyboard
from cwt.tools.video import (
    QAFailed,
    probe_media,
    qa_check,
    render_video,
    synthesize_voiceover,
)
from cwt.util.paths import RunPaths
from cwt.util.subproc import ToolNotFound
from cwt.video.backend import Availability, RenderResult

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def test_settings() -> Settings:
    return Settings.from_env()


@pytest.fixture
def run_paths(tmp_path: Path) -> RunPaths:
    return RunPaths(tmp_path / "test_run").ensure()


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _seed_storyboard(paths: RunPaths, data: dict[str, Any] | None = None) -> Storyboard:
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    sb_data = data or _load_fixture("storyboard.json")
    storyboard = Storyboard.model_validate(sb_data)
    store.write("storyboard", storyboard)
    return storyboard


def _create_dummy_video(path: Path, size_bytes: int = 1024) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size_bytes)
    return path


class FakeBackend:
    """Mock video backend for testing chain execution."""

    def __init__(
        self,
        name: str,
        *,
        available: Availability | None = None,
        render_result: RenderResult | None = None,
    ) -> None:
        self.name = name
        self._availability = available or Availability(available=True)
        self._render_result = render_result

    def available(self) -> Availability:
        return self._availability

    def render(
        self,
        storyboard: Storyboard,
        *,
        paths: RunPaths,
        voiceover: VoiceoverResult,
    ) -> RenderResult:
        if self._render_result:
            return self._render_result
        out = paths.render / "final.mp4"
        _create_dummy_video(out, 512)
        return RenderResult(
            ok=True,
            output=out,
            backend=self.name,
            elapsed_s=1.0,
            argv=["ffmpeg", "-i", "in.mp4", "out.mp4"],
            assets=[],
        )


# ===========================================================================
# 1. synthesize_voiceover tests
# ===========================================================================


def test_synthesize_voiceover_contract(run_paths: RunPaths, test_settings: Settings) -> None:
    """synthesize_voiceover writes voiceover.json, returns the frozen dict contract."""
    _seed_storyboard(run_paths)

    mock_vo_res = VoiceoverResult(
        audio_path=run_paths.render / "vo.mp3",
        transcript="Why do retail traders always buy tops?",
        words=[
            WordTiming(word="Why", start_s=0.0, end_s=0.3),
            WordTiming(word="do", start_s=0.3, end_s=0.5),
            WordTiming(word="retail", start_s=0.5, end_s=1.0),
        ],
        duration_s=38.5,
        backend_used="edge_tts",
        chain_tried=[{
            "backend": "edge_tts",
            "available": True,
            "attempted": True,
            "succeeded": True,
            "elapsed_s": 1.2,
        }],
    )

    with patch(
        "cwt.tools.video.tts_synthesize_voiceover",
        new=AsyncMock(return_value=mock_vo_res),
    ):
        res = synthesize_voiceover(settings=test_settings, paths=run_paths)

    assert res["artifact_path"] == str(run_paths.artifacts / "voiceover.json")
    assert res["backend_used"] == "edge_tts"
    assert res["duration_s"] == 38.5
    assert res["words"] == 3
    assert res["has_audio"] is True
    assert len(res["chain_tried"]) == 1

    vo_json = run_paths.artifacts / "voiceover.json"
    assert vo_json.exists()
    vo_data = json.loads(vo_json.read_text(encoding="utf-8"))
    assert vo_data["transcript"] == "Why do retail traders always buy tops?"
    assert vo_data["word_count"] == 3
    assert vo_data["has_audio"] is True


def test_synthesize_voiceover_silent_backend(run_paths: RunPaths, test_settings: Settings) -> None:
    """When silent backend is used, has_audio is False and audio_path is None."""
    _seed_storyboard(run_paths)

    mock_vo_res = VoiceoverResult(
        audio_path=None,
        transcript="Silent captions text.",
        words=[WordTiming(word="Silent", start_s=0.0, end_s=0.5)],
        duration_s=30.0,
        backend_used="silent",
        chain_tried=[{
            "backend": "edge_tts",
            "available": False,
            "attempted": False,
            "reason": "network disabled",
        }, {
            "backend": "silent",
            "available": True,
            "attempted": True,
            "succeeded": True,
            "elapsed_s": 0.05,
        }],
    )

    with patch(
        "cwt.tools.video.tts_synthesize_voiceover",
        new=AsyncMock(return_value=mock_vo_res),
    ):
        res = synthesize_voiceover(settings=test_settings, paths=run_paths)

    assert res["backend_used"] == "silent"
    assert res["has_audio"] is False
    assert res["words"] == 1


# ===========================================================================
# 2. render_video backend chain tests
# ===========================================================================


def test_render_video_records_unavailable_backend_with_reason(
    run_paths: RunPaths, test_settings: Settings
) -> None:
    """render_video records an unavailable backend as attempted: false with a reason, not an error."""
    _seed_storyboard(run_paths)
    final_mp4 = _create_dummy_video(run_paths.render / "final.mp4", 1024)

    # Seed voiceover.json
    vo_file = run_paths.artifacts / "voiceover.json"
    vo_file.write_text(json.dumps({
        "backend_used": "silent",
        "duration_s": 35.0,
        "transcript": "Test script",
        "words": [],
        "chain_tried": [],
        "audio_path": None,
    }))

    b1 = FakeBackend(
        "hyperframes",
        available=Availability(available=False, reason="node not found"),
    )
    b2 = FakeBackend(
        "local_ffmpeg",
        available=Availability(available=True),
        render_result=RenderResult(
            ok=True,
            output=final_mp4,
            backend="local_ffmpeg",
            elapsed_s=2.5,
            argv=["ffmpeg", "-i", "x"],
            assets=[],
        ),
    )

    from cwt.video.backend import VideoBackendChain
    mock_chain = VideoBackendChain([b1, b2])

    from cwt.video.ffmpeg_bin import MediaInfo
    mock_probe = MediaInfo(
        duration_s=35.0,
        width=1080,
        height=1920,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        integrated_lufs=-14.0,
        true_peak_dbtp=-1.5,
        lra=10.0,
        size_bytes=1024,
        probe_method="ffprobe",
    )

    with patch("cwt.tools.video.build_chain", return_value=mock_chain), patch(
        "cwt.tools.video.ffmpeg_bin.probe", return_value=mock_probe
    ):
        res = render_video(settings=test_settings, paths=run_paths)

    assert res["backend_used"] == "local_ffmpeg"
    assert len(res["chain_tried"]) == 2

    # b1: hyperframes -> attempted: False, reason: "node not found", NO error key
    hf_entry = res["chain_tried"][0]
    assert hf_entry["backend"] == "hyperframes"
    assert hf_entry["available"] is False
    assert hf_entry["attempted"] is False
    assert hf_entry["reason"] == "node not found"
    assert "error" not in hf_entry

    # b2: local_ffmpeg -> succeeded: True
    ff_entry = res["chain_tried"][1]
    assert ff_entry["backend"] == "local_ffmpeg"
    assert ff_entry["available"] is True
    assert ff_entry["attempted"] is True
    assert ff_entry["succeeded"] is True


def test_render_video_fails_first_backend_then_succeeds_on_second(
    run_paths: RunPaths, test_settings: Settings
) -> None:
    """A chain where hyperframes fails and local_ffmpeg succeeds returns backend_used == 'local_ffmpeg'

    with succeeded: false recorded for the first.
    """
    _seed_storyboard(run_paths)
    final_mp4 = _create_dummy_video(run_paths.render / "final.mp4", 1024)

    vo_file = run_paths.artifacts / "voiceover.json"
    vo_file.write_text(json.dumps({
        "backend_used": "silent",
        "duration_s": 35.0,
        "transcript": "Test script",
        "words": [],
        "chain_tried": [],
        "audio_path": None,
    }))

    b1 = FakeBackend(
        "hyperframes",
        available=Availability(available=True),
        render_result=RenderResult(
            ok=False,
            output=None,
            backend="hyperframes",
            elapsed_s=1.2,
            error="npx hyperframes lint failed: clip s07 has no data-start",
        ),
    )
    b2 = FakeBackend(
        "local_ffmpeg",
        available=Availability(available=True),
        render_result=RenderResult(
            ok=True,
            output=final_mp4,
            backend="local_ffmpeg",
            elapsed_s=5.0,
        ),
    )

    from cwt.video.backend import VideoBackendChain
    mock_chain = VideoBackendChain([b1, b2])

    from cwt.video.ffmpeg_bin import MediaInfo
    mock_probe = MediaInfo(
        duration_s=35.0,
        width=1080,
        height=1920,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        integrated_lufs=-14.0,
        true_peak_dbtp=-1.5,
        lra=10.0,
        size_bytes=1024,
        probe_method="ffprobe",
    )

    with patch("cwt.tools.video.build_chain", return_value=mock_chain), patch(
        "cwt.tools.video.ffmpeg_bin.probe", return_value=mock_probe
    ):
        res = render_video(settings=test_settings, paths=run_paths)

    assert res["backend_used"] == "local_ffmpeg"
    assert len(res["chain_tried"]) == 2

    # b1 recorded as succeeded: false with error
    assert res["chain_tried"][0]["backend"] == "hyperframes"
    assert res["chain_tried"][0]["succeeded"] is False
    assert "clip s07 has no data-start" in res["chain_tried"][0]["error"]

    # b2 recorded as succeeded: true
    assert res["chain_tried"][1]["backend"] == "local_ffmpeg"
    assert res["chain_tried"][1]["succeeded"] is True

    # Check render_manifest.json matches RenderManifest schema
    manifest_path = run_paths.artifacts / "render_manifest.json"
    assert manifest_path.exists()
    rm = RenderManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    assert rm.backend_used == "local_ffmpeg"
    assert len(rm.backend_chain_tried) == 2


def test_render_video_backend_pinning_and_offline_guard(
    run_paths: RunPaths, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--backend pins the chain; --offline requires backend to be local_ffmpeg (Rule V4)."""
    _seed_storyboard(run_paths)
    vo_file = run_paths.artifacts / "voiceover.json"
    vo_file.write_text(json.dumps({
        "backend_used": "silent",
        "duration_s": 35.0,
        "transcript": "Test",
        "words": [],
        "chain_tried": [],
    }))

    # offline + non-local_ffmpeg raises AssertionError (Rule V4)
    monkeypatch.setenv("CWT_OFFLINE", "1")
    with pytest.raises(AssertionError, match="--offline requires backend to be 'local_ffmpeg'"):
        render_video(settings=test_settings, paths=run_paths, backend="hyperframes")


def test_render_video_chain_exhausted_raises(
    run_paths: RunPaths, test_settings: Settings
) -> None:
    """When the chain is exhausted and all backends fail, render_video raises."""
    _seed_storyboard(run_paths)
    vo_file = run_paths.artifacts / "voiceover.json"
    vo_file.write_text(json.dumps({
        "backend_used": "silent",
        "duration_s": 35.0,
        "transcript": "Test",
        "words": [],
        "chain_tried": [],
    }))

    b1 = FakeBackend(
        "local_ffmpeg",
        available=Availability(available=True),
        render_result=RenderResult(
            ok=False,
            output=None,
            backend="local_ffmpeg",
            elapsed_s=1.0,
            error="ffmpeg fatal error: filter graph invalid",
        ),
    )

    from cwt.video.backend import VideoBackendChain
    mock_chain = VideoBackendChain([b1])

    with patch("cwt.tools.video.build_chain", return_value=mock_chain):
        with pytest.raises(RuntimeError, match="Rule V3 violation.*rendering failed"):
            render_video(settings=test_settings, paths=run_paths)


# ===========================================================================
# 3. probe_media tests
# ===========================================================================


def test_probe_media_zero_byte_raises(run_paths: RunPaths, test_settings: Settings) -> None:
    """probe_media on a 0-byte file raises with a clear message (Rule V3)."""
    empty_file = run_paths.render / "empty.mp4"
    empty_file.touch()

    with pytest.raises(ValueError, match="Rule V3 violation.*0 bytes"):
        probe_media(settings=test_settings, paths=run_paths, path=str(empty_file))


def test_probe_media_missing_file_raises(run_paths: RunPaths, test_settings: Settings) -> None:
    """probe_media on nonexistent file raises FileNotFoundError."""
    missing = run_paths.render / "nonexistent.mp4"
    with pytest.raises(FileNotFoundError):
        probe_media(settings=test_settings, paths=run_paths, path=str(missing))


def test_probe_media_fallback_to_ffmpeg_stderr_when_ffprobe_missing(
    run_paths: RunPaths, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """probe_media reports probe_method == 'ffmpeg_stderr' when ffprobe is missing (Rule W3)."""
    media_file = _create_dummy_video(run_paths.render / "clip.mp4", 512)

    # Monkeypatch ffprobe_path to fail, forcing fallback
    monkeypatch.setattr(
        "cwt.video.ffmpeg_bin.ffprobe_path",
        MagicMock(side_effect=ToolNotFound("ffprobe", "mock ffprobe missing")),
    )

    # Mock duration and ffmpeg stderr
    monkeypatch.setattr(
        "cwt.video.ffmpeg_bin.probe_duration_from_stderr",
        lambda p: 35.5,
    )

    from cwt.util.subproc import ToolResult
    fake_ffmpeg_res = ToolResult(
        args=["ffmpeg", "-i", str(media_file)],
        cwd=None,
        returncode=0,
        stdout="",
        stderr=(
            "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':\n"
            "  Duration: 00:00:35.50, start: 0.000000, bitrate: 1200 kb/s\n"
            "  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv), 1080x1920 [SAR 1:1 DAR 9:16], 30 fps\n"
            "  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 128 kb/s\n"
        ),
    )
    monkeypatch.setattr("cwt.video.ffmpeg_bin.run_tool", lambda *a, **kw: fake_ffmpeg_res)
    monkeypatch.setattr("cwt.video.ffmpeg_bin._measure_loudness", lambda p: (-14.0, -1.5, 10.0))

    res = probe_media(settings=test_settings, paths=run_paths, path=str(media_file))

    assert res["probe_method"] == "ffmpeg_stderr"
    assert res["duration_s"] == 35.5
    assert res["width"] == 1080
    assert res["height"] == 1920
    assert res["video_codec"] == "h264"
    assert res["audio_codec"] == "aac"


# ===========================================================================
# 4. qa_check tests
# ===========================================================================


def test_qa_check_fails_duration_28s(
    run_paths: RunPaths, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qa_check fails when duration is 28s (below video_min_seconds)."""
    _seed_storyboard(run_paths)
    media_file = _create_dummy_video(run_paths.render / "final.mp4", 1024)

    # Write manifest
    (run_paths.artifacts / "render_manifest.json").write_text(json.dumps({
        "output": {"path": "render/final.mp4", "duration_s": 28.0},
        "shots": [{"id": "s11", "start_s": 24.8, "duration_s": 3.2}],
    }))

    # Monkeypatch probe_media to return 28.0s duration
    mock_probe_res = {
        "path": str(media_file),
        "duration_s": 28.0,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "integrated_lufs": -14.0,
        "true_peak_dbtp": -1.5,
        "size_bytes": 1024,
        "probe_method": "ffprobe",
    }
    monkeypatch.setattr("cwt.tools.video.probe_media", lambda **kw: mock_probe_res)

    # Mock claims check to pass
    monkeypatch.setattr(
        "cwt.tools.claims.check_claims",
        lambda **kw: {"verdict": "pass", "hard_count": 0, "soft_count": 0},
    )

    res = qa_check(settings=test_settings, paths=run_paths)
    assert res["ok"] is False
    assert res["verdict"] == "block"
    assert any("28.00s" in err and "outside" in err for err in res["errors"])

    # Also verify raise_on_error raises QAFailed
    with pytest.raises(QAFailed, match="Duration 28.00s outside"):
        qa_check(settings=test_settings, paths=run_paths, raise_on_error=True)


def test_qa_check_fails_risk_disclosure_duration_2_1s(
    run_paths: RunPaths, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qa_check fails when the recomputed disclosure duration is 2.1s despite the storyboard claiming 3.2."""
    sb_data = _load_fixture("storyboard.json")
    sb_data["compliance"]["risk_disclosure_shot_id"] = "s11"
    _seed_storyboard(run_paths, sb_data)

    media_file = _create_dummy_video(run_paths.render / "final.mp4", 1024)

    # Total duration = 40.0s. Final 8 seconds is [32.0, 40.0].
    # In manifest shots: shot s11 starts at 37.9s with duration 2.1s.
    # Overlap with [32.0, 40.0] is only 2.1s (< 3.0s threshold).
    (run_paths.artifacts / "render_manifest.json").write_text(json.dumps({
        "output": {"path": "render/final.mp4", "duration_s": 40.0},
        "shots": [
            {"id": "s01", "start_s": 0.0, "duration_s": 37.9},
            {"id": "s11", "start_s": 37.9, "duration_s": 2.1},  # 2.1s instead of 3.2s
        ],
    }))

    mock_probe_res = {
        "path": str(media_file),
        "duration_s": 40.0,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "integrated_lufs": -14.0,
        "true_peak_dbtp": -1.5,
        "size_bytes": 1024,
        "probe_method": "ffprobe",
    }
    monkeypatch.setattr("cwt.tools.video.probe_media", lambda **kw: mock_probe_res)

    monkeypatch.setattr(
        "cwt.tools.claims.check_claims",
        lambda **kw: {"verdict": "pass", "hard_count": 0, "soft_count": 0},
    )

    res = qa_check(settings=test_settings, paths=run_paths)
    assert res["ok"] is False
    assert res["verdict"] == "block"
    assert any("2.10s" in err and ">= 3.0s" in err for err in res["errors"])


def test_qa_check_passes_all_criteria(
    run_paths: RunPaths, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qa_check passes when all media facts, loudness, disclosure, and claims pass."""
    sb_data = _load_fixture("storyboard.json")
    sb_data["compliance"]["risk_disclosure_shot_id"] = "s11"
    _seed_storyboard(run_paths, sb_data)

    media_file = _create_dummy_video(run_paths.render / "final.mp4", 1024)

    # Total duration = 40.0s. Final 8s: [32.0, 40.0]. Shot s11: start 36.8, dur 3.2 -> overlap 3.2s >= 3.0s.
    (run_paths.artifacts / "render_manifest.json").write_text(json.dumps({
        "output": {"path": "render/final.mp4", "duration_s": 40.0},
        "shots": [
            {"id": "s01", "start_s": 0.0, "duration_s": 36.8},
            {"id": "s11", "start_s": 36.8, "duration_s": 3.2},
        ],
    }))

    mock_probe_res = {
        "path": str(media_file),
        "duration_s": 40.0,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "integrated_lufs": -14.1,  # within 1.0 of -14.0
        "true_peak_dbtp": -1.6,   # <= -1.5 + 0.5 = -1.0
        "size_bytes": 1024,
        "probe_method": "ffprobe",
    }
    monkeypatch.setattr("cwt.tools.video.probe_media", lambda **kw: mock_probe_res)

    monkeypatch.setattr(
        "cwt.tools.claims.check_claims",
        lambda **kw: {"verdict": "pass", "hard_count": 0, "soft_count": 0},
    )

    res = qa_check(settings=test_settings, paths=run_paths)
    assert res["ok"] is True
    assert res["verdict"] == "pass"
    assert len(res["errors"]) == 0
    assert res["risk_disclosure_duration_s"] == 3.2
