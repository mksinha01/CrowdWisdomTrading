"""Tests for local_ffmpeg backend (Story S15 Part A: shot rendering).

Rules tested:
- Rule V1: cwd is paths.render, all paths in filtergraph and args are relative
  and forward-slashed (no drive letter colons).
- Rule V2: filtergraph built from typed, clamped values only, with drawtext escaped.
- Rule V4: local_ffmpeg is the guaranteed floor backend.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cwt.config import Settings
from cwt.domain.models import Storyboard
from cwt.util.paths import RunPaths
from cwt.util.subproc import ToolNotFound, ToolResult
from cwt.video.backend import Availability, build_chain
from cwt.video.ffmpeg_bin import probe
from cwt.video.local_ffmpeg import (
    LocalFfmpegBackend,
    _escape_drawtext,
    _format_drawtext_for_filter,
    build_shot_argv,
    render_shot,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_storyboard() -> Storyboard:
    data = json.loads((FIXTURES_DIR / "storyboard.json").read_text(encoding="utf-8"))
    return Storyboard.model_validate(data)


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env()


# ===========================================================================
# 1. _escape_drawtext (Rule V2)
# ===========================================================================


def test_escape_drawtext_spec_example() -> None:
    """Must pass exact test case from S15: '1 opinion isn't a strategy: 74%'."""
    raw = "1 opinion isn't a strategy: 74%"
    escaped = _escape_drawtext(raw)
    assert escaped == r"1 opinion isn\'t a strategy\: 74\%"
    assert r"\:" in escaped
    assert r"\'" in escaped
    assert r"\%" in escaped


def test_escape_drawtext_backslash_and_special_chars() -> None:
    raw = r"50% \ 25% : isn't it"
    escaped = _escape_drawtext(raw)
    assert escaped == r"50\% \\ 25\% \: isn\'t it"


def test_format_drawtext_for_filter() -> None:
    raw = "1 opinion isn't a strategy: 74%"
    formatted = _format_drawtext_for_filter(raw)
    # Inside single-quotes, literal quote is escaped as '\''
    assert r"'\''" in formatted
    assert r"\:" in formatted
    assert r"\%" in formatted


# ===========================================================================
# 2. LocalFfmpegBackend.available()
# ===========================================================================


def test_available_in_healthy_environment() -> None:
    backend = LocalFfmpegBackend()
    avail = backend.available()
    assert isinstance(avail, Availability)
    assert avail.available is True
    assert avail.reason == ""


def test_available_when_ffmpeg_missing() -> None:
    backend = LocalFfmpegBackend()
    with patch("cwt.video.local_ffmpeg.ffmpeg_path", side_effect=ToolNotFound("ffmpeg", "hint")):
        avail = backend.available()
        assert avail.available is False
        assert "not found" in avail.reason


def test_available_when_zoompan_missing() -> None:
    backend = LocalFfmpegBackend()
    mock_res_ver = ToolResult(
        args=[], cwd=None, returncode=0, stdout="ffmpeg version 4.2.2 --enable-libx264", stderr=""
    )
    mock_res_filters = ToolResult(
        args=[], cwd=None, returncode=0, stdout="scale crop vignette", stderr=""
    )
    mock_res_encoders = ToolResult(
        args=[], cwd=None, returncode=0, stdout="libx264 aac", stderr=""
    )

    with patch("cwt.video.local_ffmpeg.run_tool") as mock_run:
        mock_run.side_effect = [mock_res_ver, mock_res_filters, mock_res_encoders]
        avail = backend.available()
        assert avail.available is False
        assert "zoompan" in avail.reason


def test_available_when_codecs_missing() -> None:
    backend = LocalFfmpegBackend()
    mock_res_ver = ToolResult(
        args=[], cwd=None, returncode=0, stdout="ffmpeg version 4.2.2", stderr=""
    )
    mock_res_filters = ToolResult(
        args=[], cwd=None, returncode=0, stdout="zoompan scale crop", stderr=""
    )
    mock_res_encoders = ToolResult(
        args=[], cwd=None, returncode=0, stdout="mp3 opus", stderr=""
    )

    with patch("cwt.video.local_ffmpeg.run_tool") as mock_run:
        mock_run.side_effect = [mock_res_ver, mock_res_filters, mock_res_encoders]
        avail = backend.available()
        assert avail.available is False
        assert "libx264" in avail.reason
        assert "aac" in avail.reason


# ===========================================================================
# 3. build_shot_argv (Rule V1, Quality settings, and Audio options)
# ===========================================================================


def test_build_shot_argv_golden_done_when(
    sample_storyboard: Storyboard, settings: Settings
) -> None:
    """The canonical verification snippet from S15."""
    s01 = sample_storyboard.shots[0]
    argv = build_shot_argv(
        s01,
        asset=Path("assets/s01.png"),
        audio=None,
        out_path=Path("s01.mp4"),
        settings=settings,
    )

    # Rule V1: no drive letters / absolute paths leaked into argv
    v1_ok = all(":" not in a or a.startswith("-") or "=" in a for a in argv)
    assert v1_ok, "absolute path leaked (Rule V1)"
    assert "libx264" in argv and "-shortest" in argv
    assert "-crf" in argv
    crf_idx = argv.index("-crf")
    assert argv[crf_idx + 1] == "19"
    assert "-preset" in argv
    preset_idx = argv.index("-preset")
    assert argv[preset_idx + 1] == "medium"

    # Audio must NOT be mapped when audio is None
    assert "-map" in argv
    assert "1:a" not in argv
    assert "aac" not in argv


def test_build_shot_argv_with_audio(sample_storyboard: Storyboard, settings: Settings) -> None:
    s01 = sample_storyboard.shots[0]
    argv = build_shot_argv(
        s01,
        asset=Path("assets/s01.png"),
        audio=Path("assets/vo_s01.mp3"),
        out_path=Path("s01.mp4"),
        settings=settings,
    )

    assert "-i" in argv
    indices = [i for i, x in enumerate(argv) if x == "-i"]
    assert len(indices) == 2
    assert argv[indices[0] + 1] == "assets/s01.png"
    assert argv[indices[1] + 1] == "assets/vo_s01.mp3"

    assert "1:a" in argv
    assert "-c:a" in argv
    assert "aac" in argv
    assert "-b:a" in argv
    assert "192k" in argv


def test_build_shot_argv_rejects_absolute_paths(
    sample_storyboard: Storyboard, settings: Settings
) -> None:
    s01 = sample_storyboard.shots[0]
    is_win = Path("C:/").exists()
    abs_path = Path("C:/runs/run1/assets/s01.png") if is_win else Path("/runs/run1/assets/s01.png")

    with pytest.raises(ValueError, match="Rule V1 violation"):
        build_shot_argv(
            s01, asset=abs_path, audio=None, out_path=Path("s01.mp4"), settings=settings
        )

    with pytest.raises(ValueError, match="Rule V1 violation"):
        build_shot_argv(
            s01, asset=Path("assets/s01.png"), audio=None, out_path=abs_path, settings=settings
        )

    with pytest.raises(ValueError, match="Rule V1 violation"):
        build_shot_argv(
            s01,
            asset=Path("assets/s01.png"),
            audio=abs_path,
            out_path=Path("s01.mp4"),
            settings=settings,
        )


def test_build_shot_argv_on_screen_text(sample_storyboard: Storyboard, settings: Settings) -> None:
    """Shot 2 has on_screen_text: 'TOO MANY VOICES.'."""
    s02 = sample_storyboard.shots[1]
    assert len(s02.on_screen_text) > 0
    argv = build_shot_argv(
        s02,
        asset=Path("assets/s02.png"),
        audio=None,
        out_path=Path("s02.mp4"),
        settings=settings,
    )

    fc_idx = argv.index("-filter_complex")
    fg = argv[fc_idx + 1]
    assert "drawtext" in fg
    assert "TOO MANY VOICES." in fg
    assert "[v]" in fg


def test_build_shot_argv_caption_text(sample_storyboard: Storyboard, settings: Settings) -> None:
    """Captions when audio_path is None (silent TTS path, S12)."""
    s01 = sample_storyboard.shots[0]
    argv = build_shot_argv(
        s01,
        asset=Path("assets/s01.png"),
        audio=None,
        out_path=Path("s01.mp4"),
        settings=settings,
        caption_text="1 opinion isn't a strategy: 74%",
    )

    fc_idx = argv.index("-filter_complex")
    fg = argv[fc_idx + 1]
    assert "drawtext" in fg
    assert r"1 opinion isn" in fg
    assert r"\:" in fg
    assert r"\%" in fg
    assert "[v]" in fg


# ===========================================================================
# 4. render_shot Execution & Rule V1 cwd pinning
# ===========================================================================


def test_render_shot_real_ffmpeg(
    tmp_path: Path, sample_storyboard: Storyboard, settings: Settings
) -> None:
    """Real render of one shot from fixtures/assets/sample.png, then probe it."""
    paths = RunPaths(tmp_path / "run_test").ensure()
    sample_asset = Path("fixtures/assets/sample.png").resolve()
    assert sample_asset.exists(), "fixtures/assets/sample.png should exist"

    s01 = sample_storyboard.shots[0]
    # Set a short 1.0s duration for fast test execution
    test_shot = s01.model_copy(update={"duration_s": 1.0})
    out_path = Path("s01.mp4")

    res = render_shot(
        test_shot,
        asset=sample_asset,
        out_path=out_path,
        settings=settings,
        paths=paths,
    )

    assert isinstance(res, ToolResult)
    assert res.ok is True, f"FFmpeg failed with stderr: {res.stderr}"
    assert res.cwd == str(paths.render)

    rendered_file = paths.render / out_path
    assert rendered_file.exists()
    assert rendered_file.stat().st_size > 0

    info = probe(rendered_file)
    assert info.duration_s == pytest.approx(1.0, abs=0.2)
    assert info.width == 1080
    assert info.height == 1920
    assert info.video_codec in ("h264", "libx264")
    assert info.audio_codec is None


def test_render_shot_with_on_screen_text(
    tmp_path: Path, sample_storyboard: Storyboard, settings: Settings
) -> None:
    """Render shot with drawtext overlay."""
    paths = RunPaths(tmp_path / "run_text_test").ensure()
    sample_asset = Path("fixtures/assets/sample.png").resolve()

    s02 = sample_storyboard.shots[1]
    test_shot = s02.model_copy(update={"duration_s": 1.0})
    out_path = Path("s02.mp4")

    res = render_shot(
        test_shot,
        asset=sample_asset,
        out_path=out_path,
        settings=settings,
        paths=paths,
    )

    assert res.ok is True, f"FFmpeg with text failed: {res.stderr}"
    rendered_file = paths.render / out_path
    assert rendered_file.exists()
    info = probe(rendered_file)
    assert info.duration_s == pytest.approx(1.0, abs=0.2)


def test_render_shot_does_not_decide_success(
    tmp_path: Path, sample_storyboard: Storyboard, settings: Settings
) -> None:
    """Step 7: render_shot returns raw ToolResult and does NOT raise or decide success."""
    paths = RunPaths(tmp_path / "run_fail_test").ensure()
    non_existent_asset = Path("fixtures/assets/does_not_exist_12345.png")

    s01 = sample_storyboard.shots[0]
    res = render_shot(
        s01,
        asset=non_existent_asset,
        out_path=Path("s01.mp4"),
        settings=settings,
        paths=paths,
    )

    # ToolResult returned with ok=False, no exception raised
    assert isinstance(res, ToolResult)
    assert res.ok is False
    assert res.returncode != 0


# ===========================================================================
# 5. LocalFfmpegBackend contract & chain integration
# ===========================================================================


def test_local_ffmpeg_backend_contract() -> None:
    backend = LocalFfmpegBackend()
    assert backend.name == "local_ffmpeg"
    assert backend.available().available is True

    # render() is Part B (S16) and raises NotImplementedError in S15
    mock_obj = Storyboard.model_validate(
        json.loads((FIXTURES_DIR / "storyboard.json").read_text(encoding="utf-8"))
    )
    with pytest.raises(NotImplementedError, match="Story S16"):
        backend.render(
            mock_obj,
            paths=RunPaths(Path("runs/_stub")),
            voiceover=None,  # type: ignore[arg-type]
        )


def test_backend_chain_loads_local_ffmpeg() -> None:
    s = Settings.from_env()
    chain = build_chain(s)
    backend_names = [b.name for b in chain]
    assert "local_ffmpeg" in backend_names
    assert backend_names[-1] == "local_ffmpeg"
    local_backend = chain[-1]
    assert isinstance(local_backend, LocalFfmpegBackend)
    assert local_backend.available().available is True
