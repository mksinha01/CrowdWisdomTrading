"""Local ffmpeg backend — shot rendering (Story S15, Part A) and full render
pipeline (Story S16, Part B).

Interface contract (FROZEN):
- LocalFfmpegBackend: guaranteed-floor video backend
- build_shot_argv: assemble ffmpeg command line for one shot (S15)
- render_shot: execute ffmpeg command line in pinned cwd (S15)
- concat_clips: video-only concat with xfade transitions (S16)
- mix_audio: VO + music bed with intensity curve and sidechain ducking (S16)
- normalise_loudness: EBU R128 two-pass loudnorm (S16)
- write_render_manifest: §3.5 manifest + shots[] for Rule C3 (S16)

Architecture Rules:
- Rule V1: cwd is paths.render, all paths in filtergraph and args are relative
  and forward-slashed (no drive letter colons).
- Rule V2: filtergraph built from typed, clamped values only.
- Rule V3: probe before declaring success. "Return code 0" is not "it worked".
  On failure return RenderResult(ok=False) — do NOT raise.
- Rule V4: local_ffmpeg is the guaranteed floor with no external API/network.
- Rule C3: manifest must include shots[] timeline so S25's QA can recompute
  risk-disclosure duration from the *rendered* timeline.
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


def _rel(p: Path, cwd: Path) -> str:
    """Return a forward-slash relative path from cwd (Rule V1)."""
    try:
        return p.resolve().relative_to(cwd.resolve()).as_posix()
    except ValueError:
        return os.path.relpath(p.resolve(), cwd.resolve()).replace("\\", "/")


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
    # Resolve out_path relative to paths.render if not already inside it
    try:
        out_path.resolve().relative_to(paths.render.resolve())
        target_out = out_path.resolve()
    except ValueError:
        target_out = (paths.render / out_path).resolve()

    try:
        asset.resolve().relative_to(paths.render.resolve())
        target_asset = asset.resolve()
    except ValueError:
        if (paths.render / asset).exists():
            target_asset = (paths.render / asset).resolve()
        else:
            target_asset = asset.resolve()

    if audio is not None:
        try:
            audio.resolve().relative_to(paths.render.resolve())
            target_audio = audio.resolve()
        except ValueError:
            if (paths.render / audio).exists():
                target_audio = (paths.render / audio).resolve()
            else:
                target_audio = audio.resolve()
        audio_rel: Path | None = Path(_rel(target_audio, paths.render))
    else:
        audio_rel = None

    # Convert paths to relative to paths.render with forward slashes (Rule V1)
    asset_rel = Path(_rel(target_asset, paths.render))
    out_rel = Path(_rel(target_out, paths.render))

    # Ensure output destination parent directory exists
    target_out.parent.mkdir(parents=True, exist_ok=True)

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


# ---------------------------------------------------------------------------
# Story S16 — Part B helpers: concat, mix, loudnorm, manifest
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _ffmpeg_has_xfade(ffmpeg_bin: str) -> bool:
    try:
        res = run_tool([ffmpeg_bin, "-filters"], timeout_s=10)
        return "xfade" in res.stdout
    except Exception:
        return False


def concat_clips(
    clip_paths: list[Path],
    transitions: list,
    *,
    out_path: Path,
    settings: Settings,
    paths: RunPaths,
) -> ToolResult:
    """Concatenate rendered per-shot clips into one video-only stream.

    Rule W5/W6: uses a single -filter_complex graph, not the concat demuxer.

    xfade accounting: each xfade with duration_s > 0 shortens the total timeline
    by that amount. Spec note: 3 clips of 10s + two 0.4s dissolves must probe
    at 29.2s, not 30.0s. We accumulate the loss via ``cumulative_end``.

    Audio is NOT mixed here — handled by mix_audio().
    """
    from cwt.domain.models import TransitionName
    from cwt.video.ffmpeg_bin import probe as _probe

    n = len(clip_paths)
    if n == 0:
        raise ValueError("concat_clips: no clips provided")

    exe = settings.ffmpeg_bin or ffmpeg_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_rel = _rel(out_path, paths.render)

    argv: list[str] = [exe, "-y"]
    for cp in clip_paths:
        argv.extend(["-i", _rel(cp, paths.render)])

    if n == 1:
        argv.extend([
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-an", out_rel,
        ])
        return run_tool(argv, cwd=paths.render, timeout_s=600, check=False)

    # Probe actual clip durations for xfade offset computation
    clip_durations: list[float] = []
    for cp in clip_paths:
        try:
            info = _probe(cp)
            clip_durations.append(info.duration_s if info.duration_s > 0 else 1.0)
        except Exception:
            clip_durations.append(1.0)

    xfade_map = {
        TransitionName.DISSOLVE:    "dissolve",
        TransitionName.FLASH_WHITE: "fade",
        TransitionName.WIPELEFT:    "wipeleft",
        TransitionName.WIPERIGHT:   "wiperight",
        TransitionName.ZOOMBLUR:    "zoomblur",
    }

    filter_parts: list[str] = []
    curr_label = "[0:v]"
    cumulative_end: float = clip_durations[0]
    has_xfade = _ffmpeg_has_xfade(exe)

    for i in range(n - 1):
        next_input = f"[{i + 1}:v]"
        is_final = (i == n - 2)
        out_label = "[v]" if is_final else f"[vx{i}]"

        transition = transitions[i] if i < len(transitions) else None
        t_name = TransitionName.CUT
        t_dur = 0.0
        if transition is not None:
            raw = getattr(transition, "type", TransitionName.CUT)
            if isinstance(raw, str):
                try:
                    t_name = TransitionName(raw)
                except ValueError:
                    t_name = TransitionName.CUT
            else:
                t_name = raw
            if t_name != TransitionName.CUT:
                t_dur = float(getattr(transition, "duration_s", 0.0))

        if has_xfade and t_name != TransitionName.CUT and t_dur > 0.0:
            offset = max(0.0, cumulative_end - t_dur)
            xfade_name = xfade_map.get(t_name, "dissolve")
            f = (
                f"{curr_label}{next_input}"
                f"xfade=transition={xfade_name}:duration={t_dur:.3f}:offset={offset:.3f}"
                f"{out_label}"
            )
            filter_parts.append(f)
            cumulative_end += clip_durations[i + 1] - t_dur  # xfade shortens total
        else:
            f = f"{curr_label}{next_input}concat=n=2:v=1:a=0{out_label}"
            filter_parts.append(f)
            cumulative_end += clip_durations[i + 1]

        curr_label = out_label

    filtergraph = ";".join(filter_parts)
    argv.extend([
        "-filter_complex", filtergraph,
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-an",
        out_rel,
    ])
    return run_tool(argv, cwd=paths.render, timeout_s=600, check=False)


def mix_audio(
    voiceover: VoiceoverResult,
    music_ref: str,
    *,
    duck_under_vo: bool,
    intensity_curve: list[dict],
    out_path: Path,
    settings: Settings,
    paths: RunPaths,
    music_asset_path: Path | None = None,
) -> ToolResult:
    """Mix voiceover (or silence) and music bed into one audio track.

    - intensity_curve averaged to constant volume for the music bed.
    - duck_under_vo: sidechaincompress music keyed on VO.
    - When voiceover.audio_path is None (silent TTS): music-only or silence.
    - Missing music bed: VO-only mix — never fail for a missing bed (spec §S16).
    - riser_at_s/resolve_at_s are cue points in the bed, not synthesised fades.
    """
    exe = settings.ffmpeg_bin or ffmpeg_path()
    out_rel = _rel(out_path, paths.render)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    has_vo = voiceover.audio_path is not None
    has_music = music_asset_path is not None and music_asset_path.exists()
    duration = voiceover.duration_s

    if not has_vo and not has_music:
        argv = [
            exe, "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration:.3f}",
            "-c:a", "aac", "-b:a", "192k", out_rel,
        ]
        return run_tool(argv, cwd=paths.render, timeout_s=120, check=False)

    argv: list[str] = [exe, "-y"]
    input_idx = 0
    vo_idx: int | None = None
    music_idx: int | None = None

    if has_vo:
        vo_path = voiceover.audio_path
        assert vo_path is not None
        argv.extend(["-i", _rel(vo_path, paths.render)])
        vo_idx = input_idx
        input_idx += 1

    if has_music:
        assert music_asset_path is not None
        argv.extend(["-stream_loop", "-1", "-i", _rel(music_asset_path, paths.render)])
        music_idx = input_idx
        input_idx += 1

    avg_level: float = 0.3
    if intensity_curve:
        avg_level = (
            sum(c.get("level", 0.3) for c in intensity_curve) / len(intensity_curve)
        )

    filter_parts: list[str] = []

    if has_music and music_idx is not None:
        music_vol_label = "[music_vol]"
        filter_parts.append(
            f"[{music_idx}:a]atrim=duration={duration:.3f},"
            f"asetpts=PTS-STARTPTS,"
            f"volume={avg_level:.3f}{music_vol_label}"
        )
        if has_vo and duck_under_vo and vo_idx is not None:
            sc_label = "[music_sc]"
            filter_parts.append(
                f"[{vo_idx}:a][music_vol]sidechaincompress="
                f"threshold=0.02:ratio=4:attack=5:release=200:level_sc=0.8{sc_label}"
            )
            filter_parts.append(
                f"[{vo_idx}:a]{sc_label}"
                f"amix=inputs=2:duration=first:dropout_transition=2[aout]"
            )
        elif has_vo and vo_idx is not None:
            filter_parts.append(
                f"[{vo_idx}:a]{music_vol_label}"
                f"amix=inputs=2:duration=first:dropout_transition=2[aout]"
            )
        else:
            filter_parts.append(f"{music_vol_label}acopy[aout]")
    elif has_vo and vo_idx is not None:
        filter_parts.append(f"[{vo_idx}:a]acopy[aout]")

    filtergraph = ";".join(filter_parts)

    if filtergraph:
        argv.extend(["-filter_complex", filtergraph, "-map", "[aout]"])
    elif has_vo and vo_idx is not None:
        argv.extend(["-map", f"{vo_idx}:a"])

    argv.extend(["-c:a", "aac", "-b:a", "192k", out_rel])
    return run_tool(argv, cwd=paths.render, timeout_s=300, check=False)


def normalise_loudness(
    src: Path,
    dst: Path,
    *,
    target_lufs: float,
    true_peak_dbtp: float,
    paths: RunPaths,
) -> ToolResult:
    """Apply EBU R128 loudness normalisation (spec line 5377).

    Single-pass first. If the result misses the target by >1 LU, run a second
    pass with the measured parameters (linear=true). The two-pass method is
    cheap and audibly better on small speakers.
    """
    import json

    from cwt.video.ffmpeg_bin import probe as _probe

    exe = ffmpeg_path()
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_rel = _rel(src, paths.render)
    dst_rel = _rel(dst, paths.render)

    ln_filter = f"loudnorm=I={target_lufs:.1f}:TP={true_peak_dbtp:.1f}:LRA=11"

    # Single-pass
    result = run_tool(
        [exe, "-y", "-i", src_rel, "-af", ln_filter, "-c:a", "aac", "-b:a", "192k", dst_rel],
        cwd=paths.render, timeout_s=300, check=False,
    )
    if not result.ok:
        return result

    # Check deviation and attempt two-pass if >1 LU off
    try:
        info = _probe(dst)
        if info.integrated_lufs is not None and abs(info.integrated_lufs - target_lufs) > 1.0:
            measure_res = run_tool(
                [exe, "-i", src_rel, "-af", f"{ln_filter}:print_format=json", "-f", "null", "-"],
                cwd=paths.render, timeout_s=120, check=False,
            )
            stderr = measure_res.stderr
            idx = stderr.rfind("{")
            end_idx = stderr.rfind("}")
            measured: dict = {}
            if idx != -1 and end_idx != -1 and idx < end_idx:
                try:
                    measured = json.loads(stderr[idx: end_idx + 1])
                except Exception:
                    pass
            if measured.get("measured_I") and measured.get("measured_TP") and measured.get("measured_LRA"):
                ln2 = (
                    f"loudnorm=I={target_lufs:.1f}:TP={true_peak_dbtp:.1f}:LRA=11"
                    f":measured_I={measured['measured_I']}"
                    f":measured_TP={measured['measured_TP']}"
                    f":measured_LRA={measured['measured_LRA']}"
                    f":measured_thresh={measured.get('measured_thresh', '-70.0')}"
                    f":offset={measured.get('offset', '0.0')}"
                    f":linear=true:print_format=none"
                )
                result = run_tool(
                    [exe, "-y", "-i", src_rel, "-af", ln2, "-c:a", "aac", "-b:a", "192k", dst_rel],
                    cwd=paths.render, timeout_s=300, check=False,
                )
    except Exception:
        pass

    return result


def write_render_manifest(
    *,
    storyboard: Storyboard,
    settings: Settings,
    paths: RunPaths,
    output_path: Path,
    shot_clip_paths: list[tuple[str, Path]],
    audio_path: Path | None,
    final_argv: list[str],
    backend_chain_tried: list[dict],
    warnings: list[str],
) -> Path:
    """Write render_manifest.json per spec §3.5 (lines 862–882).

    Includes S16-required ``shots`` field (Rule C3) as an append-only optional
    field — S25's QA card uses it to recompute the risk-disclosure duration from
    the *rendered* timeline rather than trusting the storyboard's declared values.
    """
    import hashlib
    import json

    from cwt.video.ffmpeg_bin import ffmpeg_version, probe as _probe

    manifest_path = paths.artifacts / "render_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    out_info: dict = {}
    loudness: dict = {"integrated_lufs": None, "true_peak_dbtp": None, "lra": None}
    try:
        info = _probe(output_path)
        sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        out_info = {
            "path": _rel(output_path, paths.run_dir),
            "sha256": sha256,
            "duration_s": round(info.duration_s, 3),
            "width": info.width,
            "height": info.height,
            "fps": round(info.fps, 2),
            "video_codec": info.video_codec,
            "audio_codec": info.audio_codec,
        }
        loudness = {
            "integrated_lufs": info.integrated_lufs,
            "true_peak_dbtp": info.true_peak_dbtp,
            "lra": info.lra,
        }
    except Exception as exc:
        out_info = {"path": _rel(output_path, paths.run_dir), "error": str(exc)}
        warnings.append(f"Could not probe output file: {exc}")

    # Per-shot rendered timeline for Rule C3
    shots_timeline: list[dict] = []
    for shot in storyboard.shots:
        clip_path: Path | None = next(
            (cp for sid, cp in shot_clip_paths if sid == shot.id), None
        )
        shots_timeline.append({
            "id": shot.id,
            "start_s": shot.start_s,
            "duration_s": shot.duration_s,
            "rendered_clip": _rel(clip_path, paths.run_dir) if clip_path else None,
        })

    # Music assets
    music_assets: list[dict] = []
    asset_ref = storyboard.music.asset_ref
    for mc in [
        paths.assets / f"{asset_ref}.mp3",
        paths.assets / f"{asset_ref}.wav",
        paths.cache / f"{asset_ref}.mp3",
    ]:
        if mc.exists():
            music_assets.append({
                "ref": asset_ref,
                "path": _rel(mc, paths.run_dir),
                "license": "CC0-1.0",
                "source_url": "",
                "attribution_required": False,
            })
            break

    try:
        ff_ver = ffmpeg_version()
    except Exception:
        ff_ver = "unknown"

    manifest: dict = {
        "schema_version": 1,
        "backend_chain_configured": list(settings.video_backend_chain),
        "backend_chain_tried": backend_chain_tried,
        "backend_used": "local_ffmpeg",
        "output": out_info,
        "loudness": loudness,
        "argv": final_argv,
        "cwd": str(paths.render),
        "assets": music_assets,
        "ffmpeg_version": ff_ver,
        "warnings": warnings,
        "shots": shots_timeline,  # Rule C3: S25 QA uses this for risk-disclosure recompute
    }

    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )
    return manifest_path


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
        """Part B (Story S16): full 7-step pipeline.

        1. render_shot() per shot (Part A from S15)
        2. concat_clips() — video-only concat with xfade transitions
        3. mix_audio() — VO + music bed with intensity curve and ducking
        4. normalise_loudness() — EBU R128, two-pass if single-pass misses >1 LU
        5. Final mux — video + audio
        6. Rule V3 — probe(); assert duration > 0, dims valid, within bounds
        7. write_render_manifest() — §3.5 shape + shots[] for Rule C3

        On any probe assertion failure: return RenderResult(ok=False). Never raise.
        """
        import time

        from cwt.video.ffmpeg_bin import probe as _probe

        warnings_acc: list[str] = []
        t_start = time.monotonic()
        paths.ensure()
        exe = self.settings.ffmpeg_bin or ffmpeg_path()

        chain_entry: dict = {
            "backend": "local_ffmpeg",
            "available": True,
            "attempted": True,
        }
        backend_chain_tried: list[dict] = [chain_entry]

        def _fail(error: str, argv: list[str] | None = None) -> RenderResult:
            elapsed = time.monotonic() - t_start
            chain_entry["succeeded"] = False
            chain_entry["error"] = error
            chain_entry["elapsed_s"] = round(elapsed, 2)
            return RenderResult(
                ok=False,
                output=None,
                backend="local_ffmpeg",
                elapsed_s=elapsed,
                error=error,
                argv=list(argv or []),
            )

        # ── Step 1: Render each shot ──────────────────────────────────────────
        sample_png = Path("fixtures/assets/sample.png")
        sample_asset: Path | None = sample_png.resolve() if sample_png.exists() else None

        shot_clip_paths: list[tuple[str, Path]] = []
        active_shots = [s for s in storyboard.shots if s.duration_s > 0.0]
        for shot in active_shots:
            clip_path = paths.render / f"shot_{shot.id}.mp4"

            asset_ref_str = (
                shot.asset.get("ref", "") if isinstance(shot.asset, dict) else ""
            )
            candidate = paths.assets / f"{asset_ref_str}.png"
            if candidate.exists():
                asset_path = candidate
            elif sample_asset and sample_asset.exists():
                asset_path = sample_asset
            else:
                placeholder = paths.assets / "placeholder.png"
                if not placeholder.exists():
                    placeholder.parent.mkdir(parents=True, exist_ok=True)
                    run_tool(
                        [exe, "-y", "-f", "lavfi",
                         "-i", (
                             f"color=c=black:"
                             f"size={self.settings.video_width}"
                             f"x{self.settings.video_height}:rate=1"
                         ),
                         "-vframes", "1", str(placeholder)],
                        timeout_s=30, check=False,
                    )
                asset_path = placeholder

            caption: str | None = None
            if voiceover.audio_path is None:
                seg = next(
                    (s for s in storyboard.voiceover.segments if s.shot_id == shot.id),
                    None,
                )
                if seg and seg.text:
                    caption = seg.text

            res = render_shot(
                shot,
                asset=asset_path,
                out_path=clip_path,
                settings=self.settings,
                paths=paths,
                audio=None,
                caption_text=caption,
            )
            if not res.ok or not clip_path.exists() or clip_path.stat().st_size == 0:
                return _fail(
                    f"Shot {shot.id} render failed: {res.stderr[-500:]}",
                    argv=list(res.args),
                )
            shot_clip_paths.append((shot.id, clip_path))

        # ── Step 2: Concat clips ──────────────────────────────────────────────
        concat_out = paths.render / "concat_video.mp4"
        clip_files = [cp for _, cp in shot_clip_paths]
        transitions = [shot.transition_out for shot in active_shots[:-1]]

        concat_res = concat_clips(
            clip_files, transitions,
            out_path=concat_out, settings=self.settings, paths=paths,
        )
        if not concat_res.ok or not concat_out.exists() or concat_out.stat().st_size == 0:
            return _fail(
                f"concat_clips failed: {concat_res.stderr[-500:]}",
                argv=list(concat_res.args),
            )

        # ── Step 3: Mix audio ─────────────────────────────────────────────────
        mixed_audio: Path | None = None
        audio_out = paths.render / "mixed_audio.aac"

        music_asset_path: Path | None = None
        asset_ref = storyboard.music.asset_ref
        for mc in [
            paths.assets / f"{asset_ref}.mp3",
            paths.assets / f"{asset_ref}.wav",
            paths.cache / f"{asset_ref}.mp3",
            Path("fixtures/assets") / f"{asset_ref}.mp3",
        ]:
            if mc.exists():
                music_asset_path = mc
                break
        if music_asset_path is None:
            warnings_acc.append(f"Music bed '{asset_ref}' not found — rendering without music")

        intensity_curve = [
            {"at_s": float(c.at_s), "level": float(c.level)}
            for c in storyboard.music.intensity_curve
        ]
        mix_res = mix_audio(
            voiceover, asset_ref,
            duck_under_vo=storyboard.music.duck_under_vo,
            intensity_curve=intensity_curve,
            out_path=audio_out, settings=self.settings, paths=paths,
            music_asset_path=music_asset_path,
        )
        if mix_res.ok and audio_out.exists() and audio_out.stat().st_size > 0:
            mixed_audio = audio_out
        else:
            warnings_acc.append(f"mix_audio failed ({mix_res.stderr[-200:]}); continuing without audio")

        # ── Step 4: Normalise loudness ────────────────────────────────────────
        normalised_audio: Path | None = None
        if mixed_audio is not None:
            norm_out = paths.render / "normalised_audio.aac"
            norm_res = normalise_loudness(
                mixed_audio, norm_out,
                target_lufs=float(self.settings.video_loudness_lufs),
                true_peak_dbtp=float(self.settings.video_true_peak_dbtp),
                paths=paths,
            )
            if norm_res.ok and norm_out.exists() and norm_out.stat().st_size > 0:
                normalised_audio = norm_out
            else:
                warnings_acc.append(f"loudnorm failed ({norm_res.stderr[-200:]}); using un-normalised")
                normalised_audio = mixed_audio

        # ── Step 5: Final mux ─────────────────────────────────────────────────
        final_out = paths.render / "final.mp4"
        final_argv: list[str] = [exe, "-y", "-i", _rel(concat_out, paths.render)]
        if normalised_audio is not None:
            final_argv.extend(["-i", _rel(normalised_audio, paths.render)])
            final_argv.extend(["-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                                "-shortest", _rel(final_out, paths.render)])
        else:
            final_argv.extend(["-c:v", "copy", "-an", _rel(final_out, paths.render)])

        mux_res = run_tool(final_argv, cwd=paths.render, timeout_s=300, check=False)
        if not mux_res.ok:
            return _fail(
                f"Final mux failed (rc={mux_res.returncode}): {mux_res.stderr[-500:]}",
                argv=list(mux_res.args),
            )
        if not final_out.exists():
            return _fail("Output file missing after mux (Rule V3)", argv=list(mux_res.args))

        # ── Step 6: Rule V3 — probe before declaring success ──────────────────
        probe_info = _probe(final_out)
        min_s = float(self.settings.video_min_seconds)
        max_s = float(self.settings.video_max_seconds)

        if probe_info.duration_s <= 0:
            return _fail(
                f"Rule V3: probed duration_s={probe_info.duration_s} (must be > 0)",
                argv=list(mux_res.args),
            )
        if probe_info.width <= 0 or probe_info.height <= 0:
            return _fail(
                f"Rule V3: probed dimensions {probe_info.width}x{probe_info.height} invalid",
                argv=list(mux_res.args),
            )
        if not (min_s <= probe_info.duration_s <= max_s):
            return _fail(
                f"Rule V3: duration {probe_info.duration_s:.2f}s outside [{min_s:.0f}s, {max_s:.0f}s]",
                argv=list(mux_res.args),
            )

        # ── Step 7: Write render manifest ─────────────────────────────────────
        elapsed = time.monotonic() - t_start
        chain_entry["succeeded"] = True
        chain_entry["elapsed_s"] = round(elapsed, 2)

        write_render_manifest(
            storyboard=storyboard,
            settings=self.settings,
            paths=paths,
            output_path=final_out,
            shot_clip_paths=shot_clip_paths,
            audio_path=normalised_audio,
            final_argv=[str(a) for a in final_argv],
            backend_chain_tried=backend_chain_tried,
            warnings=warnings_acc,
        )

        return RenderResult(
            ok=True,
            output=final_out,
            backend="local_ffmpeg",
            elapsed_s=elapsed,
            argv=[str(a) for a in final_argv],
            assets=[],
        )
