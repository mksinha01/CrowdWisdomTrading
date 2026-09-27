"""Shot -> ffmpeg filter string. PURE. No ffmpeg needed to test it.

This module is the reason the video layer is trustworthy: it is the only
non-trivial part of the renderer, and it is a pure function with a golden test
suite. If you want to know whether the video code is real, read
tests/test_filtergraph.py.

CONTRACT: every returned fragment operates on a labelled input and writes a
labelled output. Paths inside fragments are RELATIVE and use forward slashes —
never a drive letter, never a backslash (Rule W5).
"""
from __future__ import annotations

from cwt.domain.models import Shot

# The cinematic grade. Ten lines of filtergraph is the entire difference between
# "an AI slideshow" and "a movie trailer" — see WOW-6.
CINEMATIC_GRADE = "vignette=PI/5,noise=alls=7:allf=t,eq=contrast=1.06:saturation=0.94"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_zoompan_expr(*, move: str, intensity: float, duration_s: float, fps: int) -> str | None:
    """Compute the zoompan z-expression. Returns None for moves that do not zoom.

    A `push_in` is NOT decoration — intensity 0.6 over 4 seconds at 30fps becomes
    a concrete per-frame zoom rate. That is what makes the camera direction
    executable rather than descriptive.
    """
    intensity = clamp(intensity, 0.0, 1.0)
    frames = max(1, int(duration_s * fps))
    if move == "static":
        return None
    if move == "push_in":
        rate = 0.30 * intensity / max(duration_s, 0.1) / fps
        return f"min(zoom+{rate:.6f},1.35)"
    if move == "pull_out":
        rate = 0.30 * intensity / max(duration_s, 0.1) / fps
        return f"if(lte(zoom,1.0),1.35,max(1.001,zoom-{rate:.6f}))"
    if move == "whip_pan":
        # Whip pans are handled by crop x-sweep, not zoompan. See build_shot_filter.
        return None
    return None


def build_colour_grade(palette: list[str], colour_temp_k: int, contrast: str) -> str:
    """Derive a colour balance from the shot's declared palette.

    Values are validated upstream by the pydantic model (hex, 4 entries), so this
    function interpolates only from a closed, type-checked set — never from raw
    model output. That is the difference between a filtergraph and a string
    concatenation bug (Rule V2).
    """
    if not palette or len(palette) < 2:
        return ""
    temp_shift = clamp((colour_temp_k - 6500) / 6500.0, -1.0, 1.0)
    parts = []
    if abs(temp_shift) > 0.02:
        parts.append(f"colorbalance=rs={-temp_shift * 0.08:.4f}:bs={temp_shift * 0.08:.4f}")
    if contrast == "extreme":
        parts.append("eq=contrast=1.18:saturation=0.88")
    elif contrast == "high":
        parts.append("eq=contrast=1.10:saturation=0.92")
    return ",".join(parts)


def build_shot_filter(shot: Shot, *, input_label: str, output_label: str, fps: int,
                      width: int, height: int) -> str:
    """Full filter chain for one shot, from the shot's own declared properties."""
    chain: list[str] = [f"[{input_label}]scale={width}:{height}:force_original_aspect_ratio=increase",
                        f"crop={width}:{height}"]

    zoom = build_zoompan_expr(move=shot.camera.move, intensity=shot.camera.intensity,
                              duration_s=shot.duration_s, fps=fps)
    if zoom:
        frames = max(1, int(shot.duration_s * fps))
        chain.append(
            f"zoompan=z='{zoom}':d={frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
        )

    if shot.camera.move == "whip_pan":
        sweep = clamp(shot.camera.intensity, 0.1, 1.0) * width * 0.35
        chain.append(f"crop={width}:{height}:x='(iw-{width})*t/{max(shot.duration_s, 0.1):.3f}*{sweep / max(width, 1):.3f}':y=0")

    grade = build_colour_grade(shot.palette, shot.lighting.colour_temp_k, shot.lighting.contrast)
    if grade:
        chain.append(grade)
    chain.append(CINEMATIC_GRADE)

    return f"{','.join(chain)}[{output_label}]"
