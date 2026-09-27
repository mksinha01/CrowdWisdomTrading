"""Tests for filtergraph generation, ffmpeg binary resolution, and video backend chain.

Story S14 — ffmpeg resolution, backend chain, filtergraph.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cwt.config import Settings
from cwt.domain.models import Camera, Lighting, Shot, Storyboard
from cwt.util.subproc import ToolFailed, ToolNotFound, ToolResult
from cwt.video import (
    CINEMATIC_GRADE,
    Availability,
    MediaInfo,
    RenderResult,
    VideoBackendChain,
    build_chain,
    build_colour_grade,
    build_shot_filter,
    build_zoompan_expr,
    clamp,
    ffmpeg_path,
    ffmpeg_version,
    ffprobe_path,
    probe,
    probe_duration_from_stderr,
)
from cwt.video.ffmpeg_bin import _measure_loudness

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ===========================================================================
# Part 1: Pure filtergraph functions & Golden Tests (No ffmpeg needed)
# ===========================================================================


def test_clamp() -> None:
    assert clamp(5.0, 0.0, 10.0) == 5.0
    assert clamp(-2.0, 0.0, 10.0) == 0.0
    assert clamp(15.0, 0.0, 10.0) == 10.0


def test_cinematic_grade_constant() -> None:
    assert CINEMATIC_GRADE == "vignette=PI/5,noise=alls=7:allf=t,eq=contrast=1.06:saturation=0.94"


def test_build_zoompan_expr_golden() -> None:
    """Golden tests from S14 spec."""
    # push_in golden test
    z_push = build_zoompan_expr(move="push_in", intensity=0.6, duration_s=4.0, fps=30)
    assert z_push == "min(zoom+0.001500,1.35)"

    # static returns None
    assert build_zoompan_expr(move="static", intensity=0.5, duration_s=4.0, fps=30) is None

    # whip_pan returns None (handled by crop x-sweep, not zoompan)
    assert build_zoompan_expr(move="whip_pan", intensity=0.5, duration_s=4.0, fps=30) is None

    # unknown move returns None
    assert build_zoompan_expr(move="unknown", intensity=0.5, duration_s=4.0, fps=30) is None


def test_build_zoompan_expr_pull_out() -> None:
    z_pull = build_zoompan_expr(move="pull_out", intensity=0.5, duration_s=3.0, fps=30)
    # rate = 0.30 * 0.5 / 3.0 / 30 = 0.15 / 90 = 0.001667
    assert z_pull == "if(lte(zoom,1.0),1.35,max(1.001,zoom-0.001667))"


def test_build_zoompan_expr_intensity_clamping() -> None:
    # intensity clamped to 0.0 -> rate 0.000000
    z_min = build_zoompan_expr(move="push_in", intensity=-1.0, duration_s=2.0, fps=30)
    assert z_min == "min(zoom+0.000000,1.35)"

    # intensity clamped to 1.0
    z_max = build_zoompan_expr(move="push_in", intensity=5.0, duration_s=2.0, fps=30)
    # rate = 0.30 * 1.0 / 2.0 / 30 = 0.005000
    assert z_max == "min(zoom+0.005000,1.35)"


def test_build_colour_grade_golden() -> None:
    """Golden tests from S14 spec."""
    palette = ["#050505", "#1a1a1a", "#ef4444", "#22d3ee"]

    # 6500K: no colorbalance at exactly 6500K
    grade_6500 = build_colour_grade(palette, 6500, "extreme")
    assert grade_6500 == "eq=contrast=1.18:saturation=0.88"

    # 7000K: colorbalance plus eq=contrast=1.10:saturation=0.92
    grade_7000 = build_colour_grade(palette, 7000, "high")
    assert grade_7000 == "colorbalance=rs=-0.0062:bs=0.0062,eq=contrast=1.10:saturation=0.92"


def test_build_colour_grade_edge_cases() -> None:
    # Empty or short palette returns empty string
    assert build_colour_grade([], 6500, "high") == ""
    assert build_colour_grade(["#000000"], 6500, "high") == ""

    # Warmer temperature (below 6500K): rs is positive, bs is negative
    palette = ["#050505", "#1a1a1a", "#ef4444", "#22d3ee"]
    grade_warm = build_colour_grade(palette, 5000, "high")
    assert "colorbalance=rs=0.0185:bs=-0.0185" in grade_warm
    assert "eq=contrast=1.10:saturation=0.92" in grade_warm

    # Normal / default contrast adds no eq filter
    grade_normal = build_colour_grade(palette, 6500, "medium")
    assert grade_normal == ""


def test_build_shot_filter_s01_and_s02_golden() -> None:
    """Byte-exact golden test for §3.4 s01 and s02 shots."""
    sb_data = json.loads((FIXTURES_DIR / "storyboard.json").read_text(encoding="utf-8"))
    sb = Storyboard.model_validate(sb_data)

    s01 = sb.shots[0]
    assert s01.id == "s01"
    f01 = build_shot_filter(s01, input_label="0:v", output_label="v0", fps=30, width=1080, height=1920)
    expected_s01 = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "eq=contrast=1.18:saturation=0.88,"
        "vignette=PI/5,noise=alls=7:allf=t,eq=contrast=1.06:saturation=0.94[v0]"
    )
    assert f01 == expected_s01

    s02 = sb.shots[1]
    assert s02.id == "s02"
    f02 = build_shot_filter(s02, input_label="v0", output_label="v1", fps=30, width=1080, height=1920)
    expected_s02 = (
        "[v0]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "zoompan=z='min(zoom+0.035000,1.35)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30,"
        "colorbalance=rs=-0.0062:bs=0.0062,eq=contrast=1.10:saturation=0.92,"
        "vignette=PI/5,noise=alls=7:allf=t,eq=contrast=1.06:saturation=0.94[v1]"
    )
    assert f02 == expected_s02


def test_build_shot_filter_whip_pan() -> None:
    """Test whip_pan camera move creates crop x-sweep expression."""
    sb_data = json.loads((FIXTURES_DIR / "storyboard.json").read_text(encoding="utf-8"))
    sb = Storyboard.model_validate(sb_data)
    shot = sb.shots[0]
    whip_shot = shot.model_copy(
        update={
            "camera": Camera(move="whip_pan", intensity=0.5, lens_mm=50, depth_of_field="deep", stabilisation="locked"),
            "duration_s": 2.0,
        }
    )
    f = build_shot_filter(whip_shot, input_label="in", output_label="out", fps=30, width=1080, height=1920)
    assert "zoompan" not in f
    # sweep = 0.5 * 1080 * 0.35 = 189.0; sweep / width = 0.175
    assert "crop=1080:1920:x='(iw-1080)*t/2.000*0.175':y=0" in f
    assert f.endswith(f"{CINEMATIC_GRADE}[out]")


def test_all_fixture_shots_filtergraph_rules() -> None:
    """Assert rules V1, V2, W5 for every shot in fixture storyboard."""
    sb_data = json.loads((FIXTURES_DIR / "storyboard.json").read_text(encoding="utf-8"))
    sb = Storyboard.model_validate(sb_data)

    for i, shot in enumerate(sb.shots):
        in_label = f"in_{i}"
        out_label = f"out_{i}"
        filter_str = build_shot_filter(shot, input_label=in_label, output_label=out_label, fps=30, width=1080, height=1920)

        # Rule V1 / W5: labelled input and output
        assert filter_str.startswith(f"[{in_label}]")
        assert filter_str.endswith(f"[{out_label}]")

        # CINEMATIC_GRADE ends the chain in every shot
        assert filter_str.endswith(f"{CINEMATIC_GRADE}[{out_label}]")

        # Rule V1 / W5: no backslashes
        assert "\\" not in filter_str


# ===========================================================================
# Part 2: Backend chain & Rule V4
# ===========================================================================


def test_availability_and_render_result() -> None:
    avail = Availability(available=True, reason="ok")
    assert avail.available is True
    assert avail.reason == "ok"

    res = RenderResult(
        ok=True,
        output=Path("render/final.mp4"),
        backend="local_ffmpeg",
        elapsed_s=12.5,
    )
    assert res.ok is True
    assert res.error is None
    assert res.argv == []
    assert res.assets == []


def test_backend_chain_construction() -> None:
    s = Settings.from_env()
    chain = build_chain(s)
    assert isinstance(chain, VideoBackendChain)
    assert len(chain) == 3
    assert [b.name for b in chain] == ["hyperframes", "openmontage", "local_ffmpeg"]
    assert chain[0].name == "hyperframes"
    assert chain[1].name == "openmontage"
    assert chain[2].name == "local_ffmpeg"


def test_backend_chain_rule_v4_enforcement() -> None:
    """Rule V4: build_chain() raises at startup if last element is not guaranteed-available."""
    s = Settings.from_env()

    # s.with_backend_chain enforcing V4
    with pytest.raises(Exception) as exc_info:
        s.with_backend_chain(["hyperframes", "openmontage"])
    assert "Rule V4" in str(exc_info.value)

    # settings object directly with invalid chain
    invalid_settings = replace(s, video_backend_chain=["hyperframes", "openmontage"])
    with pytest.raises(ValueError) as exc_info:
        build_chain(invalid_settings)
    assert "Rule V4 violation" in str(exc_info.value)

    # Empty chain
    empty_settings = replace(s, video_backend_chain=[])
    with pytest.raises(ValueError) as exc_info:
        build_chain(empty_settings)
    assert "Rule V4 violation" in str(exc_info.value)

    # Tail of 'fixture' is accepted per forward-compatibility allowance
    fixture_settings = replace(s, video_backend_chain=["fixture"])
    fixture_chain = build_chain(fixture_settings)
    assert len(fixture_chain) == 1
    assert fixture_chain[0].name == "fixture"


def test_backend_chain_unknown_backend() -> None:
    s = Settings.from_env()
    bad_settings = replace(s, video_backend_chain=["nonexistent_backend", "local_ffmpeg"])
    with pytest.raises(ValueError) as exc_info:
        build_chain(bad_settings)
    assert "Unknown video backend" in str(exc_info.value)


# ===========================================================================
# Part 3: ffmpeg_bin (resolution, probing, loudness)
# ===========================================================================


def test_ffmpeg_path_resolution() -> None:
    """Rule W2: never assumes PATH."""
    path = ffmpeg_path()
    assert Path(path).exists()

    # Test override
    with patch.dict("os.environ", {"CWT_FFMPEG_BIN": path}):
        assert ffmpeg_path() == path


def test_ffmpeg_version_parsing() -> None:
    ver = ffmpeg_version()
    assert isinstance(ver, str)
    assert len(ver) > 0
    # On this environment, it's 4.2.2 or higher
    assert ver[0].isdigit() or ver.startswith("n")


def test_ffprobe_path_override() -> None:
    with patch.dict("os.environ", {"CWT_FFPROBE_BIN": "/custom/ffprobe"}):
        assert ffprobe_path() == "/custom/ffprobe"


def test_probe_nonexistent_file() -> None:
    with pytest.raises(FileNotFoundError):
        probe(Path("nonexistent_media_file_12345.mp4"))


def test_probe_zero_byte_file(tmp_path: Path) -> None:
    """Decision: probe() on a 0-byte file returns MediaInfo with zeros — does NOT raise."""
    empty_file = tmp_path / "empty.mp4"
    empty_file.write_bytes(b"")

    info = probe(empty_file)
    assert isinstance(info, MediaInfo)
    assert info.duration_s == 0.0
    assert info.width == 0
    assert info.height == 0
    assert info.size_bytes == 0
    assert info.video_codec == ""
    assert info.audio_codec is None
    assert info.integrated_lufs is None
    assert "File is 0 bytes" in info.warnings


def test_probe_duration_from_stderr_mocked(tmp_path: Path) -> None:
    """Rule W3: parse Duration line from ffmpeg -i stderr."""
    sample_file = tmp_path / "sample.mp4"
    sample_file.write_bytes(b"fake video data")

    fake_stderr = (
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'sample.mp4':\n"
        "  Duration: 00:00:42.50, start: 0.000000, bitrate: 1500 kb/s\n"
        "    Stream #0:0(und): Video: h264, 1080x1920, 30 fps\n"
    )

    with patch("cwt.video.ffmpeg_bin.run_tool") as mock_run:
        mock_run.return_value = ToolResult(
            args=["ffmpeg", "-i", str(sample_file)],
            cwd=None,
            returncode=1,
            stdout="",
            stderr=fake_stderr,
        )
        duration = probe_duration_from_stderr(sample_file)
        assert duration == 42.5


def test_probe_fallback_to_stderr_when_ffprobe_missing(tmp_path: Path) -> None:
    """Rule W3: when ffprobe is missing, probe() falls back to stderr and records warning."""
    sample_file = tmp_path / "sample.mp4"
    sample_file.write_bytes(b"data" * 100)

    fake_stderr = (
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'sample.mp4':\n"
        "  Duration: 00:00:30.00, start: 0.000000, bitrate: 2000 kb/s\n"
        "    Stream #0:0(und): Video: h264 (High), yuv420p, 1080x1920 [SAR 1:1 DAR 9:16], 30 fps\n"
        "    Stream #0:1(und): Audio: aac (LC), 48000 Hz, stereo, 192 kb/s\n"
        "At least one output file must be specified\n"
    )

    fake_loudnorm_stderr = """
[Parsed_loudnorm_0 @ 000001]
{
    "input_i" : "-24.10",
    "input_tp" : "-1.50",
    "input_lra" : "6.20",
    "input_thresh" : "-34.10"
}
"""

    def mock_run_tool(args, **kwargs):
        if "-af" in args and "loudnorm=print_format=json" in args:
            return ToolResult(args=args, cwd=None, returncode=0, stdout="", stderr=fake_loudnorm_stderr)
        return ToolResult(args=args, cwd=None, returncode=1, stdout="", stderr=fake_stderr)

    with patch("cwt.video.ffmpeg_bin.ffprobe_path", side_effect=ToolNotFound("ffprobe", "mocked missing")):
        with patch("cwt.video.ffmpeg_bin.run_tool", side_effect=mock_run_tool):
            info = probe(sample_file)
            assert info.probe_method == "ffmpeg_stderr"
            assert info.duration_s == 30.0
            assert info.width == 1080
            assert info.height == 1920
            assert info.fps == 30.0
            assert info.video_codec == "h264"
            assert info.audio_codec == "aac"
            assert info.integrated_lufs == -24.10
            assert info.true_peak_dbtp == -1.50
            assert info.lra == 6.20
            assert any("ffprobe unavailable" in w for w in info.warnings)


def test_measure_loudness_malformed(tmp_path: Path) -> None:
    sample_file = tmp_path / "dummy.mp4"
    sample_file.write_bytes(b"data")

    with patch("cwt.video.ffmpeg_bin.run_tool") as mock_run:
        # Malformed stderr without JSON
        mock_run.return_value = ToolResult(args=[], cwd=None, returncode=0, stdout="", stderr="invalid loudnorm output")
        i, tp, lra = _measure_loudness(sample_file)
        assert i is None
        assert tp is None
        assert lra is None
