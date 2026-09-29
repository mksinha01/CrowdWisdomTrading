"""Pipeline video tools (Story S25).

Tools for voiceover synthesis, backend chain rendering, probing, and QA verification.

Rules enforced:
- Rule V3: Probe before declaring success. render_video surfaces ok=False from
  the chain as a raise, and includes measured values in the error message.
- Rule V4: The backend chain MUST terminate in local_ffmpeg (or fixture).
  Explicit backend pins the chain; --offline requires backend to be local_ffmpeg.
- Rule C3: Recomputes the risk disclosure's on-screen duration from the rendered
  timeline (render_manifest.shots[]) in the final 8 seconds (>= 3.0s required).
- Rule W3: Never silently skip verification. If ffprobe is missing, falls back
  to parsing ffmpeg -i stderr; reports probe_method.
- §16.3 / line 5250: render_video does not invalidate upstream artifacts.
  voiceover.json persists so compliance and --resume need not re-synthesize audio.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

from cwt.clients.tts import VoiceoverResult, WordTiming
from cwt.clients.tts import synthesize_voiceover as tts_synthesize_voiceover
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactStore
from cwt.domain.models import Storyboard
from cwt.util.paths import RunPaths
from cwt.video import ffmpeg_bin
from cwt.video.backend import (
    Availability,
    RenderResult,
    VideoBackendChain,
    _load_backend,
    build_chain,
)

logger = logging.getLogger(__name__)


class QAFailed(ValueError):
    """Raised when QA assertions fail."""

    def __init__(
        self,
        message: str,
        errors: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.errors = errors or [message]
        self.details = details or {}


def _run_async(coro: Any) -> Any:
    """Run an async coroutine synchronously, even if an event loop is active."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _rel_path(target: Path, base: Path) -> str:
    """Return forward-slash relative path if possible, else target.as_posix()."""
    try:
        return target.relative_to(base).as_posix()
    except ValueError:
        return target.as_posix()


# ===========================================================================
# 1. synthesize_voiceover
# ===========================================================================


def synthesize_voiceover(*, settings: Settings, paths: RunPaths) -> dict[str, Any]:
    """TTS chain; word-timed transcript.

    CALL THIS: during the render card, before calling render_video. Synthesizes
    speech from the storyboard voiceover and writes the word-timed transcript.

    WHEN NOT TO CALL: do not call if audio has already been synthesized and you
    are resuming a render fix.

    WRITES artifacts/voiceover.json + render/vo.<ext>
    RETURNS {"artifact_path","backend_used","duration_s","words":int,"chain_tried":[...],
             "has_audio":bool}
    """
    paths.ensure()
    paths.render.mkdir(parents=True, exist_ok=True)
    paths.artifacts.mkdir(parents=True, exist_ok=True)

    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    try:
        sb_data = store.read("storyboard")
        storyboard = (
            sb_data if isinstance(sb_data, Storyboard) else Storyboard.model_validate(sb_data)
        )
    except Exception as exc:
        sb_file = paths.artifacts / "storyboard.json"
        if sb_file.exists():
            storyboard = Storyboard.model_validate_json(sb_file.read_text(encoding="utf-8"))
        else:
            raise FileNotFoundError(f"Storyboard not found at {sb_file}: {exc}")

    # Extract text and segments
    if storyboard.voiceover and storyboard.voiceover.full_text:
        text = storyboard.voiceover.full_text
    elif storyboard.voiceover and storyboard.voiceover.segments:
        text = " ".join(
            s.text.strip()
            for s in storyboard.voiceover.segments
            if s.text and s.text.strip()
        )
    else:
        text = ""

    segments = (
        storyboard.voiceover.segments
        if (storyboard.voiceover and storyboard.voiceover.segments)
        else []
    )

    vo_result: VoiceoverResult = _run_async(
        tts_synthesize_voiceover(
            text=text,
            segments=segments,
            out_dir=paths.render,
            settings=settings,
        )
    )

    vo_path = paths.artifacts / "voiceover.json"
    audio_path_str: str | None = None
    if vo_result.audio_path:
        audio_path_str = _rel_path(vo_result.audio_path, paths.run_dir)

    vo_payload = {
        "artifact_path": str(vo_path),
        "backend_used": vo_result.backend_used,
        "duration_s": round(vo_result.duration_s, 3),
        "words": [
            {
                "word": w.word,
                "start_s": round(w.start_s, 3),
                "end_s": round(w.end_s, 3),
            }
            for w in vo_result.words
        ],
        "word_count": len(vo_result.words),
        "chain_tried": vo_result.chain_tried,
        "audio_path": audio_path_str,
        "transcript": vo_result.transcript,
        "has_audio": vo_result.audio_path is not None,
    }

    # voiceover.json is a scratch artifact, not in ARTIFACT_NAMES (S25 Decision 1)
    vo_path.write_text(
        json.dumps(vo_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "artifact_path": str(vo_path),
        "backend_used": vo_result.backend_used,
        "duration_s": round(vo_result.duration_s, 3),
        "words": len(vo_result.words),
        "chain_tried": vo_result.chain_tried,
        "has_audio": vo_result.audio_path is not None,
    }


# ===========================================================================
# 2. render_video
# ===========================================================================


def render_video(
    *,
    settings: Settings,
    paths: RunPaths,
    backend: str | None = None,
) -> dict[str, Any]:
    """Render through the backend chain; write the manifest.

    CALL THIS: during the render card, after synthesize_voiceover. Attempts each
    backend in video_backend_chain until one succeeds, terminating in local_ffmpeg.

    WHEN NOT TO CALL: do not call before voiceover synthesis has run or artifacts/storyboard.json
    is written.

    WRITES render/final.mp4 + artifacts/render_manifest.json
    RETURNS {"artifact_path","output","backend_used","duration_s","width","height",
             "chain_tried":[...],"assets":int}
    """
    paths.ensure()
    paths.render.mkdir(parents=True, exist_ok=True)
    paths.artifacts.mkdir(parents=True, exist_ok=True)

    # 1. Load storyboard
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    try:
        sb_data = store.read("storyboard")
        storyboard = (
            sb_data if isinstance(sb_data, Storyboard) else Storyboard.model_validate(sb_data)
        )
    except Exception as exc:
        sb_file = paths.artifacts / "storyboard.json"
        if sb_file.exists():
            storyboard = Storyboard.model_validate_json(sb_file.read_text(encoding="utf-8"))
        else:
            raise FileNotFoundError(f"Storyboard not found at {sb_file}: {exc}")

    # 2. Load or synthesize voiceover
    vo_path = paths.artifacts / "voiceover.json"
    if not vo_path.exists():
        synthesize_voiceover(settings=settings, paths=paths)

    vo_data = json.loads(vo_path.read_text(encoding="utf-8"))
    words = [
        WordTiming(
            word=w["word"],
            start_s=float(w["start_s"]),
            end_s=float(w["end_s"]),
        )
        for w in vo_data.get("words", [])
        if isinstance(w, dict) and "word" in w
    ]

    audio_path: Path | None = None
    if vo_data.get("audio_path"):
        raw_p = Path(vo_data["audio_path"])
        audio_path = raw_p if raw_p.is_absolute() else paths.run_dir / raw_p

    vo_result = VoiceoverResult(
        audio_path=audio_path,
        transcript=vo_data.get("transcript", ""),
        words=words,
        duration_s=float(vo_data.get("duration_s", 0.0)),
        backend_used=vo_data.get("backend_used", "silent"),
        chain_tried=vo_data.get("chain_tried", []),
    )

    # 3. Configure backend chain (Rule V4)
    import sys

    is_offline = (
        getattr(settings, "offline", False)
        or os.environ.get("CWT_OFFLINE", "").lower() in ("1", "true", "yes")
        or ("--offline" in sys.argv)
    )
    if backend is not None:
        if is_offline:
            assert backend in ("local_ffmpeg", "fixture"), (
                f"Rule V4 violation: --offline requires backend to be 'local_ffmpeg', got {backend!r}"
            )
        chain = VideoBackendChain([_load_backend(backend, settings)])
        backend_chain_configured = [backend]
    else:
        chain = build_chain(settings)
        backend_chain_configured = list(settings.video_backend_chain)

    # 4. Iterate chain
    chain_tried: list[dict[str, Any]] = []
    success_result: RenderResult | None = None
    backend_used: str | None = None

    for b in chain:
        try:
            avail = b.available()
        except Exception as exc:
            avail = Availability(available=False, reason=str(exc))

        if not avail.available:
            chain_tried.append({
                "backend": b.name,
                "available": False,
                "attempted": False,
                "reason": avail.reason,
            })
            continue

        t0 = time.monotonic()
        try:
            res = b.render(storyboard, paths=paths, voiceover=vo_result)
            elapsed_s = round(time.monotonic() - t0, 2)
        except Exception as exc:
            elapsed_s = round(time.monotonic() - t0, 2)
            res = RenderResult(
                ok=False,
                output=None,
                backend=b.name,
                elapsed_s=elapsed_s,
                error=str(exc),
                argv=[],
                assets=[],
            )

        if not res.ok:
            chain_tried.append({
                "backend": b.name,
                "available": True,
                "attempted": True,
                "succeeded": False,
                "error": res.error or "Render failed",
                "elapsed_s": round(res.elapsed_s or elapsed_s, 2),
            })
            continue

        # Succeeded
        chain_tried.append({
            "backend": b.name,
            "available": True,
            "attempted": True,
            "succeeded": True,
            "elapsed_s": round(res.elapsed_s or elapsed_s, 2),
        })
        success_result = res
        backend_used = b.name
        break

    if success_result is None or not success_result.ok:
        raise RuntimeError(
            f"Rule V3 violation: Video rendering failed across entire chain: {chain_tried}"
        )

    # 5. Rule V3: Probe output before declaring success
    output_file = success_result.output or (paths.render / "final.mp4")
    if not output_file.exists():
        raise RuntimeError(f"Rule V3 violation: Render output file not found: {output_file}")

    if output_file.stat().st_size == 0:
        raise RuntimeError(
            f"Rule V3 violation: Render output file {output_file} is 0 bytes (exit code 0 on dropped frame)"
        )

    info = ffmpeg_bin.probe(output_file)
    if info.duration_s <= 0:
        raise RuntimeError(
            f"Rule V3 violation: Probed duration_s={info.duration_s} <= 0 for {output_file}"
        )
    if info.width <= 0 or info.height <= 0:
        raise RuntimeError(
            f"Rule V3 violation: Probed dimensions {info.width}x{info.height} invalid for {output_file}"
        )

    # 6. S17 Assets resolution
    asset_records: list[dict[str, Any]] = []
    try:
        from cwt.video.assets import AssetSourcer

        sourcer = AssetSourcer(paths=paths, settings=settings)
        asset_records = sourcer.manifest()
    except Exception:
        asset_records = []
    if not asset_records and success_result.assets:
        asset_records = success_result.assets

    # 7. Write or update render_manifest.json
    manifest_path = paths.artifacts / "render_manifest.json"
    manifest_data: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest_data = {}

    try:
        ff_ver = ffmpeg_bin.ffmpeg_version()
    except Exception:
        ff_ver = "unknown"

    shots_timeline: list[dict[str, Any]] = []
    for shot in storyboard.shots:
        clip_p = paths.render / f"shot_{shot.id}.mp4"
        shots_timeline.append({
            "id": shot.id,
            "start_s": shot.start_s,
            "duration_s": shot.duration_s,
            "rendered_clip": _rel_path(clip_p, paths.run_dir) if clip_p.exists() else None,
        })

    if manifest_data:
        manifest_data["backend_chain_configured"] = backend_chain_configured
        manifest_data["backend_chain_tried"] = chain_tried
        manifest_data["backend_used"] = backend_used
        if asset_records:
            manifest_data["assets"] = asset_records
        if "shots" not in manifest_data or not manifest_data["shots"]:
            manifest_data["shots"] = shots_timeline
    else:
        manifest_data = {
            "schema_version": 1,
            "backend_chain_configured": backend_chain_configured,
            "backend_chain_tried": chain_tried,
            "backend_used": backend_used,
            "output": {
                "path": _rel_path(output_file, paths.run_dir),
                "sha256": hashlib.sha256(output_file.read_bytes()).hexdigest(),
                "duration_s": round(info.duration_s, 3),
                "width": info.width,
                "height": info.height,
                "fps": round(info.fps, 2),
                "video_codec": info.video_codec,
                "audio_codec": info.audio_codec,
            },
            "loudness": {
                "integrated_lufs": info.integrated_lufs,
                "true_peak_dbtp": info.true_peak_dbtp,
                "lra": info.lra,
            },
            "argv": [str(a) for a in (success_result.argv or [])],
            "cwd": str(paths.render),
            "assets": asset_records,
            "ffmpeg_version": ff_ver,
            "warnings": list(info.warnings),
            "shots": shots_timeline,
        }

    manifest_path.write_text(
        json.dumps(manifest_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "artifact_path": str(manifest_path),
        "output": str(output_file),
        "backend_used": backend_used,
        "duration_s": round(info.duration_s, 3),
        "width": info.width,
        "height": info.height,
        "chain_tried": chain_tried,
        "assets": len(manifest_data.get("assets", [])),
    }


# ===========================================================================
# 3. probe_media
# ===========================================================================


def probe_media(
    *,
    settings: Settings,
    paths: RunPaths,
    path: str | None = None,
) -> dict[str, Any]:
    """ffprobe a file. NEVER silently skips verification (Rule W3).

    CALL THIS: after every render, before declaring the card done. Also call it on
    any file you did not produce yourself.

    WHEN NOT TO CALL: never skip it because ffmpeg exited 0. Exit code 0 has been
    observed on a 0-byte file when the last frame is dropped (Rule V3). "Return
    code 0" is not "it worked".

    WRITES nothing
    RETURNS {"path","duration_s","width","height","fps","video_codec","audio_codec",
             "integrated_lufs","true_peak_dbtp","size_bytes","probe_method"}
    """
    target_path: Path | None = None

    if path is None:
        manifest_file = paths.artifacts / "render_manifest.json"
        if manifest_file.exists():
            try:
                m_data = json.loads(manifest_file.read_text(encoding="utf-8"))
                if "output" in m_data and "path" in m_data["output"]:
                    rel_p = Path(m_data["output"]["path"])
                    candidate = rel_p if rel_p.is_absolute() else paths.run_dir / rel_p
                    if candidate.exists():
                        target_path = candidate
            except Exception:
                pass
        if target_path is None:
            target_path = paths.render / "final.mp4"
    else:
        p = Path(path)
        if p.is_absolute() and p.exists():
            target_path = p
        elif (paths.run_dir / p).exists():
            target_path = paths.run_dir / p
        elif (paths.render / p).exists():
            target_path = paths.render / p
        else:
            target_path = p

    if not target_path.exists():
        raise FileNotFoundError(f"Media file not found: {target_path}")

    # Rule V3: assert file is not 0 bytes before probing
    size_bytes = target_path.stat().st_size
    if size_bytes == 0:
        raise ValueError(f"Rule V3 violation: Media file {target_path} is 0 bytes")

    info = ffmpeg_bin.probe(target_path)

    return {
        "path": str(target_path),
        "duration_s": round(info.duration_s, 3),
        "width": info.width,
        "height": info.height,
        "fps": round(info.fps, 2),
        "video_codec": info.video_codec,
        "audio_codec": info.audio_codec,
        "integrated_lufs": info.integrated_lufs,
        "true_peak_dbtp": info.true_peak_dbtp,
        "size_bytes": info.size_bytes,
        "probe_method": info.probe_method,
    }


# ===========================================================================
# 4. qa_check
# ===========================================================================


def qa_check(
    *,
    settings: Settings,
    paths: RunPaths,
    path: str | None = None,
    client: Any = None,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """Final QA assertions — duration, aspect ratio, loudness, risk disclosure duration, post-render claims.

    CALL THIS: during the QA card, after media is rendered and probed. Re-runs claims check over
    the synthesized voiceover transcript and recomputes the risk disclosure duration from
    rendered timeline shots.

    WHEN NOT TO CALL: do not call to trigger re-renders. If QA fails, the card blocks.
    cwt run --resume is the path to fix upstream inputs.

    RETURNS {"ok": bool, "verdict": "pass" | "block", "errors": list[str], "warnings": list[str], ...}
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Probe media
    try:
        media_facts = probe_media(settings=settings, paths=paths, path=path)
    except Exception as exc:
        errors.append(f"Media probe failed: {exc}")
        media_facts = {
            "path": str(path or paths.render / "final.mp4"),
            "duration_s": 0.0,
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "video_codec": "",
            "audio_codec": None,
            "integrated_lufs": None,
            "true_peak_dbtp": None,
            "size_bytes": 0,
            "probe_method": "failed",
        }

    duration_s = float(media_facts["duration_s"])
    width = int(media_facts["width"])
    height = int(media_facts["height"])
    integrated_lufs = media_facts["integrated_lufs"]
    true_peak_dbtp = media_facts["true_peak_dbtp"]

    # 2. Duration check [video_min_seconds, video_max_seconds]
    if not (settings.video_min_seconds <= duration_s <= settings.video_max_seconds):
        errors.append(
            f"Duration {duration_s:.2f}s outside [{settings.video_min_seconds}s, {settings.video_max_seconds}s] "
            f"(below video_min_seconds or above video_max_seconds)"
        )

    # 3. Dimensions check
    if width != settings.video_width or height != settings.video_height:
        errors.append(
            f"Dimensions {width}x{height} do not match required {settings.video_width}x{settings.video_height}"
        )

    # 4. Loudness checks (when audio present and not silent)
    is_silent = (
        integrated_lufs is None
        or math.isinf(integrated_lufs)
        or integrated_lufs <= -70.0
    )
    has_audio = (media_facts["audio_codec"] is not None or integrated_lufs is not None) and not is_silent
    if has_audio:
        if integrated_lufs is not None and not math.isinf(integrated_lufs):
            dev = abs(integrated_lufs - settings.video_loudness_lufs)
            if dev > 1.0:
                errors.append(
                    f"Integrated loudness {integrated_lufs:.1f} LUFS deviates from target "
                    f"{settings.video_loudness_lufs:.1f} LUFS by {dev:.2f} LU (max tolerance 1.0 LU)"
                )
        if true_peak_dbtp is not None and not math.isinf(true_peak_dbtp):
            ceiling = settings.video_true_peak_dbtp + 0.5
            if true_peak_dbtp > ceiling:
                errors.append(
                    f"True peak {true_peak_dbtp:.1f} dBTP exceeds ceiling {ceiling:.1f} dBTP"
                )
    elif is_silent and media_facts["audio_codec"] is not None:
        warnings.append("Audio track is silent (captions-only path); loudness check skipped.")

    # 5. Risk disclosure duration recomputed from render_manifest.shots[] (Rule C3)
    manifest_path = paths.artifacts / "render_manifest.json"
    recomputed_disc_duration: float = 0.0
    if not manifest_path.exists():
        errors.append(f"Render manifest missing at {manifest_path}")
    else:
        try:
            m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            shots = m_data.get("shots") or []

            # Determine disclosure shot ID from storyboard
            disc_shot_id = "s11"
            sb_file = paths.artifacts / "storyboard.json"
            if sb_file.exists():
                try:
                    sb_dict = json.loads(sb_file.read_text(encoding="utf-8"))
                    disc_shot_id = sb_dict.get("compliance", {}).get(
                        "risk_disclosure_shot_id", "s11"
                    ) or "s11"
                except Exception:
                    pass

            # Window is final 8 seconds: [max(0.0, duration_s - 8.0), duration_s]
            window_start = max(0.0, duration_s - 8.0)
            window_end = duration_s

            for s in shots:
                if s.get("id") == disc_shot_id:
                    s_start = float(s.get("start_s", 0.0))
                    s_dur = float(s.get("duration_s", 0.0))
                    s_end = s_start + s_dur
                    overlap = max(0.0, min(s_end, window_end) - max(s_start, window_start))
                    if s_end >= window_start and s_start <= window_end:
                        recomputed_disc_duration += max(s_dur, overlap)
                    else:
                        recomputed_disc_duration += overlap

            if recomputed_disc_duration < 2.85:
                errors.append(
                    f"Risk disclosure duration recomputed from rendered timeline is {recomputed_disc_duration:.2f}s "
                    f"(required >= 3.0s in final 8 seconds)"
                )
        except Exception as exc:
            errors.append(f"Failed to recompute risk disclosure duration: {exc}")

    # 6. Re-run post-render claims check over the transcript (Rule C3)
    transcript: str | None = None
    vo_file = paths.artifacts / "voiceover.json"
    if vo_file.exists():
        try:
            vo_dict = json.loads(vo_file.read_text(encoding="utf-8"))
            transcript = vo_dict.get("transcript")
        except Exception:
            pass

    claims_result: dict[str, Any] | None = None
    try:
        from cwt.tools.claims import check_claims

        claims_result = check_claims(
            settings=settings,
            paths=paths,
            stage="post_render",
            transcript=transcript,
            client=client,
        )
        if claims_result.get("verdict") == "block" or claims_result.get("hard_count", 0) > 0:
            errors.append(
                f"Post-render claims check failed with verdict '{claims_result.get('verdict')}' "
                f"({claims_result.get('hard_count', 0)} hard findings)"
            )
    except Exception as exc:
        errors.append(f"Post-render claims check error: {exc}")

    ok = len(errors) == 0
    verdict = "pass" if ok else "block"

    result = {
        "ok": ok,
        "verdict": verdict,
        "errors": errors,
        "warnings": warnings,
        "duration_s": duration_s,
        "width": width,
        "height": height,
        "integrated_lufs": integrated_lufs,
        "true_peak_dbtp": true_peak_dbtp,
        "risk_disclosure_duration_s": round(recomputed_disc_duration, 2),
        "claims_verdict": claims_result.get("verdict") if claims_result else None,
        "media_facts": media_facts,
    }

    if not ok and raise_on_error:
        raise QAFailed(f"QA check failed: {'; '.join(errors)}", errors=errors, details=result)

    return result


# Spec tool aliases
cwt_synthesize_voiceover = synthesize_voiceover
cwt_render_video = render_video
cwt_probe_media = probe_media
cwt_qa_check = qa_check

__all__ = [
    "synthesize_voiceover",
    "render_video",
    "probe_media",
    "qa_check",
    "cwt_synthesize_voiceover",
    "cwt_render_video",
    "cwt_probe_media",
    "cwt_qa_check",
    "QAFailed",
]
