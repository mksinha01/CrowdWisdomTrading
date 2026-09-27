"""Local ffmpeg backend — shot rendering (Story S15, Part A).

Interface contract (FROZEN):
- LocalFfmpegBackend: guaranteed-floor video backend
- build_shot_argv: assemble ffmpeg command line for one shot
- render_shot: execute ffmpeg command line in pinned cwd

Architecture Rules:
- Rule V1: cwd is paths.render, all paths in filtergraph and args are relative
  and forward-slashed (no drive letter colons).
- Rule V2: filtergraph built from typed, clamped values only.
- Rule V4: local_ffmpeg is the guaranteed floor with no external API/network dependencies.
"""
from __future__ import annotations

import functools
import os
from pathlib import Path

from cwt.clients.tts import VoiceoverResult
from cwt.config import Settings
from cwt.domain.models import _POSITION_Y_MAP, Shot, Storyboard
from cwt.util.paths import RunPaths
from cwt.util.subproc import ToolResult, run_tool
from cwt.video.backend import Availability, RenderResult
from cwt.video.ffmpeg_bin import ffmpeg_path
from cwt.video.filtergraph import build_shot_filter

STYLE_FONTSIZE_MAP: dict[str, int] = {
    "display_black": 64,
    "caption_bold": 48,
    "url_subheading": 40,
    "subtitle": 42,
}


def _escape_drawtext(text: str) -> str:
    """Escape ':', ''', and '%' in text for ffmpeg drawtext filter (Rule V2).

    Escapes ':', ''', and '%' in the text to ensure a drawtext string with a bare
    colon or quotes does not break the filtergraph parser.
    Unit-tested against: "1 opinion isn't a strategy: 74%"
    """
    s = text.replace("\\", "\\\\")
    s = s.replace(":", r"\:")
    s = s.replace("'", r"\'")
    s = s.replace("%", r"\%")
    return s


def _format_drawtext_for_filter(text: str) -> str:
    """Format escaped text for inclusion inside single-quoted drawtext option."""
    esc = _escape_drawtext(text)
    # Inside single quotes in ffmpeg filtergraph, literal single quotes must be '\''
    return esc.replace(r"\'", r"'\''")


@functools.lru_cache(maxsize=1)
def _has_drawtext() -> bool:
    try:
        exe = ffmpeg_path()
        res = run_tool([exe, "-filters"], timeout_s=10)
        out = res.stdout or res.stderr
        return "drawtext" in out
    except Exception:
        return False


def build_shot_argv(
    shot: Shot,
    *,
    asset: Path,
    audio: Path | None,
    out_path: Path,
    settings: Settings,
    caption_text: str | None = None,
) -> list[str]:
    """Assemble ffmpeg arguments to render one shot into one clip.

    cwd must be pinned (paths.render); all paths MUST be relative (Rule V1).
    Part A renders each shot standalone without inter-shot transitions:
    transitions (transition_in / transition_out: cut | dissolve | flash_white |
    wipeleft | wiperight | zoomblur) are inter-shot and belong strictly to the
    concatenation step in S16. S16 must not re-implement shot rendering.
    """
    # Rule V1: verify no absolute path leaked
    if asset.is_absolute():
        raise ValueError(f"Rule V1 violation: asset path must be relative, got {asset}")
    if out_path.is_absolute():
        raise ValueError(f"Rule V1 violation: out_path must be relative, got {out_path}")
    if audio is not None and audio.is_absolute():
        raise ValueError(f"Rule V1 violation: audio path must be relative, got {audio}")

    asset_rel = asset.as_posix()
    out_rel = out_path.as_posix()
    audio_rel = audio.as_posix() if audio is not None else None

    # Format duration cleanly
    dur_str = f"{shot.duration_s:.3f}".rstrip("0").rstrip(".")
    if "." not in dur_str:
        dur_str = f"{shot.duration_s:.1f}"

    # Build filtergraph
    has_text = bool(shot.on_screen_text) or bool(caption_text)
    base_out_label = "graded" if has_text else "v"

    base_filter = build_shot_filter(
        shot,
        input_label="0:v",
        output_label=base_out_label,
        fps=settings.video_fps,
        width=settings.video_width,
        height=settings.video_height,
    )

    if not has_text:
        filtergraph = base_filter
    else:
        filter_parts = [base_filter]
        curr_in = base_out_label
        txt_idx = 0

        # On-screen text
        for item in shot.on_screen_text:
            txt_idx += 1
            next_out = f"v_t{txt_idx}"
            esc_txt = _format_drawtext_for_filter(item.text)
            fsize = STYLE_FONTSIZE_MAP.get(item.style, 48)
            y_ratio = _POSITION_Y_MAP.get(item.position, 0.70)

            # Relative start and end times within shot
            if item.at_s >= shot.start_s:
                st = max(0.0, item.at_s - shot.start_s)
                et = min(shot.duration_s, max(st, item.until_s - shot.start_s))
            else:
                st = max(0.0, item.at_s)
                et = min(shot.duration_s, max(st, item.until_s))

            dt_filter = (
                f"[{curr_in}]drawtext=text='{esc_txt}':"
                f"fontsize={fsize}:fontcolor=white:"
                f"box=1:boxcolor=black@0.4:boxborderw=8:"
                f"x=(w-text_w)/2:y=(h-text_h)*{y_ratio:.2f}:"
                f"enable='between(t,{st:.3f},{et:.3f})'[{next_out}]"
            )
            filter_parts.append(dt_filter)
            curr_in = next_out

        # Caption text (burned-in subtitle band when audio is None, per S12/S15)
        if caption_text:
            txt_idx += 1
            next_out = f"v_t{txt_idx}"
            esc_cap = _format_drawtext_for_filter(caption_text)
            dt_filter = (
                f"[{curr_in}]drawtext=text='{esc_cap}':"
                f"fontsize=42:fontcolor=white:"
                f"box=1:boxcolor=black@0.6:boxborderw=12:"
                f"x=(w-text_w)/2:y=h*0.82:"
                f"enable='between(t,0,{shot.duration_s:.3f})'[{next_out}]"
            )
            filter_parts.append(dt_filter)
            curr_in = next_out

        # Fix final output label to [v]
        last_filter = filter_parts[-1]
        last_filter_fixed = last_filter.rsplit("[", 1)[0] + "[v]"
        filter_parts[-1] = last_filter_fixed
        filtergraph = ";".join(filter_parts)

    argv: list[str] = [
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-t", dur_str,
        "-i", asset_rel,
    ]
    if audio_rel is not None:
        argv.extend(["-i", audio_rel])

    argv.extend(["-filter_complex", filtergraph])
    argv.extend(["-map", "[v]"])
    if audio_rel is not None:
        argv.extend(["-map", "1:a"])

    argv.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "19"])
    if audio_rel is not None:
        argv.extend(["-c:a", "aac", "-b:a", "192k"])

    argv.append("-shortest")
    if audio_rel is None:
        argv.extend(["-t", dur_str])
    argv.append(out_rel)

    return argv


def render_shot(
    shot: Shot,
    *,
    asset: Path,
    out_path: Path,
    settings: Settings,
    paths: RunPaths,
    audio: Path | None = None,
    caption_text: str | None = None,
) -> ToolResult:
    """One labelled input -> one clip. cwd pinned; every path RELATIVE (Rule V1).

    Returns the raw ToolResult — it does NOT decide success (S16 owns Rule V3).
    """
    render_dir = paths.render.resolve()
    paths.render.mkdir(parents=True, exist_ok=True)

    # Convert paths to relative to paths.render with forward slashes
    if asset.is_absolute():
        asset_rel = Path(os.path.relpath(asset.resolve(), render_dir).replace("\\", "/"))
    elif (paths.render / asset).exists():
        asset_rel = Path(asset.as_posix())
    elif asset.exists():
        asset_rel = Path(os.path.relpath(asset.resolve(), render_dir).replace("\\", "/"))
    else:
        asset_rel = Path(asset.as_posix())

    if out_path.is_absolute():
        out_rel = Path(os.path.relpath(out_path.resolve(), render_dir).replace("\\", "/"))
    else:
        out_rel = Path(out_path.as_posix())

    audio_rel: Path | None = None
    if audio is not None:
        if audio.is_absolute():
            audio_rel = Path(os.path.relpath(audio.resolve(), render_dir).replace("\\", "/"))
        elif (paths.render / audio).exists():
            audio_rel = Path(audio.as_posix())
        elif audio.exists():
            audio_rel = Path(os.path.relpath(audio.resolve(), render_dir).replace("\\", "/"))
        else:
            audio_rel = Path(audio.as_posix())

    # Ensure output destination parent directory exists
    (paths.render / out_rel).parent.mkdir(parents=True, exist_ok=True)

    argv = build_shot_argv(
        shot,
        asset=asset_rel,
        audio=audio_rel,
        out_path=out_rel,
        settings=settings,
        caption_text=caption_text,
    )

    exe = settings.ffmpeg_bin or ffmpeg_path()
    cmd = [exe, *argv[1:]]

    return run_tool(cmd, cwd=paths.render, timeout_s=300, check=False)


class LocalFfmpegBackend:
    name = "local_ffmpeg"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def available(self) -> Availability:
        """Available iff ffmpeg resolves AND has libx264 + aac + zoompan."""
        try:
            exe = self.settings.ffmpeg_bin or ffmpeg_path()
        except Exception as exc:
            return Availability(available=False, reason=f"ffmpeg binary not found: {exc}")

        try:
            res_ver = run_tool([exe, "-version"], timeout_s=10)
            ver_text = res_ver.stdout or res_ver.stderr
        except Exception as exc:
            return Availability(available=False, reason=f"ffmpeg -version failed: {exc}")

        try:
            res_filters = run_tool([exe, "-filters"], timeout_s=10)
            filters_text = res_filters.stdout or res_filters.stderr
        except Exception:
            filters_text = ""

        try:
            res_encoders = run_tool([exe, "-encoders"], timeout_s=10)
            encoders_text = res_encoders.stdout or res_encoders.stderr
        except Exception:
            encoders_text = ""

        missing: list[str] = []
        if "libx264" not in encoders_text and "libx264" not in ver_text:
            missing.append("libx264")
        if "aac" not in encoders_text and "aac" not in ver_text:
            missing.append("aac")
        if "zoompan" not in filters_text:
            missing.append("zoompan")

        if missing:
            return Availability(
                available=False,
                reason=f"ffmpeg missing required features: {', '.join(missing)}",
            )

        return Availability(available=True, reason="")

    def render(
        self,
        storyboard: Storyboard,
        *,
        paths: RunPaths,
        voiceover: VoiceoverResult,
    ) -> RenderResult:
        """Part B (Story S16) implements render().

        Part A (Story S15) renders individual shots via render_shot().
        """
        raise NotImplementedError(
            "LocalFfmpegBackend.render() is implemented in Story S16. "
            "Use render_shot() for individual shot rendering in S15."
        )
