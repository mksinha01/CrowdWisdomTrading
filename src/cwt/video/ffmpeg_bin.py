"""ffmpeg and ffprobe binary resolution and probing.

Architecture Rules:
- Rule W2: Never assume ffmpeg is on PATH. Resolution order:
  $CWT_FFMPEG_BIN -> imageio_ffmpeg.get_ffmpeg_exe() -> shutil.which('ffmpeg').
- Rule W3: If ffprobe is missing, parse ffmpeg -i stderr — never skip verification.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from cwt.util.subproc import ToolFailed, ToolNotFound, run_tool


@dataclass(frozen=True)
class MediaInfo:
    duration_s: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str | None
    integrated_lufs: float | None
    true_peak_dbtp: float | None
    lra: float | None
    size_bytes: int
    probe_method: str = "ffprobe"
    warnings: list[str] = field(default_factory=list)


def ffmpeg_path() -> str:
    """Resolve ffmpeg binary path without assuming PATH.

    Order: $CWT_FFMPEG_BIN -> imageio_ffmpeg.get_ffmpeg_exe() -> shutil.which('ffmpeg')
    """
    if override := os.getenv("CWT_FFMPEG_BIN"):
        return override
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    if which := shutil.which("ffmpeg"):
        return which
    raise ToolNotFound(
        "ffmpeg",
        "pip install imageio-ffmpeg, or set CWT_FFMPEG_BIN to an absolute path",
    )


def ffprobe_path() -> str:
    """Resolve ffprobe binary path.

    Order: $CWT_FFPROBE_BIN -> adjacent to ffmpeg -> shutil.which('ffprobe')
    """
    if override := os.getenv("CWT_FFPROBE_BIN"):
        return override
    try:
        ff_bin = ffmpeg_path()
        ff_dir = Path(ff_bin).parent
        candidates = ["ffprobe.exe", "ffprobe"] if sys.platform == "win32" else ["ffprobe"]
        for c in candidates:
            cand_path = ff_dir / c
            if cand_path.is_file():
                return str(cand_path)
    except Exception:
        pass
    if which := shutil.which("ffprobe"):
        return which
    raise ToolNotFound(
        "ffprobe",
        "set CWT_FFPROBE_BIN to an absolute path or install ffprobe",
    )


def ffmpeg_version() -> str:
    """Parse 'ffmpeg version X.Y' from -version output."""
    exe = ffmpeg_path()
    res = run_tool([exe, "-version"], timeout_s=10)
    output = res.stdout or res.stderr
    match = re.search(r"ffmpeg version\s+([^\s,]+)", output)
    if match:
        return match.group(1)
    lines = output.splitlines()
    return lines[0].strip() if lines else "unknown"


def probe_duration_from_stderr(path: Path | str) -> float:
    """Parse duration from ffmpeg -i stderr (Rule W3 fallback)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Media file not found: {p}")
    if p.stat().st_size == 0:
        return 0.0
    res = run_tool([ffmpeg_path(), "-i", str(p)], timeout_s=30)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", res.stderr)
    if not match:
        raise RuntimeError(f"Could not determine duration of {p}")
    h, m, s = int(match[1]), int(match[2]), float(match[3])
    return h * 3600 + m * 60 + s


def _measure_loudness(path: Path) -> tuple[float | None, float | None, float | None]:
    """Measure loudness via loudnorm filter on ffmpeg stderr JSON.

    Returns (integrated_lufs, true_peak_dbtp, lra) or (None, None, None).
    Never fabricates a LUFS value.
    """
    try:
        res = run_tool(
            [ffmpeg_path(), "-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
            timeout_s=30,
        )
        stderr = res.stderr
        idx = stderr.rfind("{")
        end = stderr.rfind("}")
        if idx != -1 and end != -1 and idx < end:
            data = json.loads(stderr[idx : end + 1])
            if "input_i" in data and "input_tp" in data and "input_lra" in data:
                return float(data["input_i"]), float(data["input_tp"]), float(data["input_lra"])
    except Exception:
        pass
    return None, None, None


def probe(path: Path | str) -> MediaInfo:
    """Probe media file via ffprobe; falls back to ffmpeg -i stderr (Rule W3).

    On 0-byte file: returns MediaInfo with duration_s=0.0, width=0, height=0
    without raising.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Media file not found: {p}")

    size_bytes = p.stat().st_size
    if size_bytes == 0:
        return MediaInfo(
            duration_s=0.0,
            width=0,
            height=0,
            fps=0.0,
            video_codec="",
            audio_codec=None,
            integrated_lufs=None,
            true_peak_dbtp=None,
            lra=None,
            size_bytes=0,
            probe_method="ffprobe",
            warnings=["File is 0 bytes"],
        )

    warnings: list[str] = []
    probe_method = "ffprobe"

    try:
        probe_bin = ffprobe_path()
        res = run_tool(
            [probe_bin, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(p)],
            timeout_s=30,
        )
        if not res.ok:
            raise ToolFailed(res)
        data = json.loads(res.stdout)
        fmt = data.get("format", {})
        duration_s = float(fmt.get("duration", 0.0))
        size_bytes = int(fmt.get("size", size_bytes))

        video_codec = ""
        width = 0
        height = 0
        fps = 0.0
        audio_codec = None

        for stream in data.get("streams", []):
            codec_type = stream.get("codec_type")
            if codec_type == "video" and not video_codec:
                video_codec = stream.get("codec_name", "")
                width = int(stream.get("width", 0))
                height = int(stream.get("height", 0))
                rate_str = stream.get("r_frame_rate", "") or stream.get("avg_frame_rate", "")
                if "/" in rate_str:
                    num, den = rate_str.split("/", 1)
                    den_f = float(den)
                    fps = float(num) / den_f if den_f != 0 else 0.0
                elif rate_str:
                    try:
                        fps = float(rate_str)
                    except ValueError:
                        fps = 0.0
            elif codec_type == "audio" and audio_codec is None:
                audio_codec = stream.get("codec_name", None)

    except Exception as exc:
        # Fall back to parsing ffmpeg -i stderr (Rule W3)
        probe_method = "ffmpeg_stderr"
        warnings.append(f"ffprobe unavailable ({exc}); fell back to ffmpeg -i stderr")

        duration_s = probe_duration_from_stderr(p)
        res = run_tool([ffmpeg_path(), "-i", str(p)], timeout_s=30)
        stderr = res.stderr

        video_codec = ""
        width = 0
        height = 0
        fps = 0.0
        audio_codec = None

        v_match = re.search(r"Stream #.*?Video:\s*([a-zA-Z0-9_-]+).*?,\s*(\d+)x(\d+)", stderr)
        if v_match:
            video_codec = v_match.group(1)
            width = int(v_match.group(2))
            height = int(v_match.group(3))
            fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", stderr)
            if fps_match:
                fps = float(fps_match.group(1))
            else:
                tbr_match = re.search(r"(\d+(?:\.\d+)?)\s*tbr", stderr)
                if tbr_match:
                    fps = float(tbr_match.group(1))

        a_match = re.search(r"Stream #.*?Audio:\s*([a-zA-Z0-9_-]+)", stderr)
        if a_match:
            audio_codec = a_match.group(1)

    integrated_lufs = None
    true_peak_dbtp = None
    lra = None
    if audio_codec:
        integrated_lufs, true_peak_dbtp, lra = _measure_loudness(p)

    return MediaInfo(
        duration_s=duration_s,
        width=width,
        height=height,
        fps=fps,
        video_codec=video_codec,
        audio_codec=audio_codec,
        integrated_lufs=integrated_lufs,
        true_peak_dbtp=true_peak_dbtp,
        lra=lra,
        size_bytes=size_bytes,
        probe_method=probe_method,
        warnings=warnings,
    )
