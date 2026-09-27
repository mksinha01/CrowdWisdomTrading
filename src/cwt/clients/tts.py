"""TTS client & word-timed transcript generator (Story S12).

Architectural Derivation & Decisions (Gap G12):
The file tree lists `clients/tts.py` and `.env` configures `TTS_BACKEND_CHAIN`,
but the spec (§8) has no standalone TTS section. This module's contract is
derived from the chain semantics at spec line 278, lines 5202–5205 (voices),
Rule C3 (claims gate re-scan of spoken words), and lines 3527–3538 (QA card):

1. Rule C3 Compliance:
   The claims gate must run twice (pre-render and post-render). Post-render
   claims validation must check what was ACTUALLY spoken (e.g. edge-tts
   normalising "$29.99" to "twenty nine dollars ninety nine"). The `transcript`
   field in `VoiceoverResult` contains this actual spoken text, not merely
   the storyboard's input text.

2. Fallback Chain:
   The chain order is configurable via `settings.tts_backend_chain` (default:
   `["edge_tts", "piper", "silent"]`). Each backend is tried left-to-right:
   - `edge_tts`: Network required. Produces MP3 audio with real word boundaries
     captured from WordBoundary stream events.
   - `piper`: Offline fallback. Requires voice model at `PIPER_VOICE_PATH`.
     Invoked via `run_tool()` to produce WAV audio; word timings estimated.
   - `silent`: Always available fallback floor. Produces no audio file
     (`audio_path=None`), derives timings from `estimate_word_timings` over
     the declared duration.
   `silent` is a valid ad format (burned-in captions) per spec line 279,
   not a failure. The chain never raises when `silent` succeeds.

3. Cross-story contract with S16 (`local_ffmpeg`):
   When `audio_path is None` (silent backend used), S16 renders the video
   with burned-in captions.

4. Subprocess execution:
   External tools (piper) must run through `run_tool()` with utf-8 encoding
   per Rules W1 and W7.

5. Duration probing:
   No heavy external audio libraries used. Durations are obtained from ffprobe,
   standard library `wave` (for WAV), or backend event timings.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cwt.config import Settings
from cwt.domain.models import VOSegment
from cwt.util.subproc import run_tool

logger = logging.getLogger(__name__)

# Known voices per spec lines 5202–5205
KNOWN_EDGE_TTS_VOICES: frozenset[str] = frozenset({
    "en-US-AndrewNeural",
    "en-US-BrianNeural",
    "en-GB-RyanNeural",
    "en-US-AvaNeural",
    "en-GB-SoniaNeural",
})

KNOWN_PIPER_VOICES: frozenset[str] = frozenset({
    "en_US-ryan-high",
    "en_US-lessac-medium",
    "en_GB-alan-medium",
})


class TTSUnavailable(RuntimeError):
    """Raised when all backends in the TTS chain fail or are exhausted."""


@dataclass(frozen=True)
class WordTiming:
    """Timing for an individual word in seconds."""

    word: str
    start_s: float
    end_s: float


@dataclass
class VoiceoverResult:
    """Result of TTS voiceover synthesis."""

    audio_path: Path | None          # None on the "silent" backend
    transcript: str                  # the text AS SPOKEN — drives the post-render gate
    words: list[WordTiming]
    duration_s: float
    backend_used: str                # "edge_tts" | "piper" | "silent"
    # [{backend, available, attempted, succeeded, error?, elapsed_s}]
    chain_tried: list[dict[str, Any]]


def estimate_word_timings(
    text: str,
    duration_s: float,
    *,
    wpm: float = 210.0,
) -> list[WordTiming]:
    """Distribute duration_s across words proportional to len(word) + 1.

    Guarantees:
    - timings[0].start_s == 0.0
    - timings[-1].end_s == duration_s (within float precision)
    - Contiguous: timings[i].end_s == timings[i+1].start_s (no gaps, no overlaps)
    """
    words = text.split()
    if not words:
        return []

    if duration_s <= 0.0:
        duration_s = (len(words) / wpm) * 60.0

    weights = [len(w) + 1 for w in words]
    total_weight = sum(weights)

    timings: list[WordTiming] = []
    current_time = 0.0
    num_words = len(words)

    for i, word in enumerate(words):
        if i == num_words - 1:
            end_time = duration_s
        else:
            dur = duration_s * (weights[i] / total_weight)
            end_time = current_time + dur

        timings.append(WordTiming(word=word, start_s=current_time, end_s=end_time))
        current_time = end_time

    return timings


def _probe_duration_ffprobe(path: Path, ffprobe_bin: str = "") -> float | None:
    """Probe audio duration using ffprobe if available."""
    exe = ffprobe_bin or "ffprobe"
    try:
        res = run_tool(
            [
                exe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            timeout_s=10.0,
        )
        if res.ok and res.stdout.strip():
            val = float(res.stdout.strip())
            if val > 0:
                return val
    except Exception:
        pass
    return None


def _get_audio_duration(path: Path, ffprobe_bin: str = "") -> float | None:
    """Get audio duration from wave header (for WAV) or ffprobe."""
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    return frames / float(rate)
        except Exception:
            pass
    return _probe_duration_ffprobe(path, ffprobe_bin)


def _is_edge_tts_available(settings: Settings) -> tuple[bool, str]:
    """Check if edge-tts package is importable."""
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return False, "edge-tts package is not installed"
    return True, ""


async def _synthesize_edge_tts(
    text: str,
    out_dir: Path,
    settings: Settings,
    declared_duration_s: float,
) -> tuple[Path, str, list[WordTiming], float]:
    """Synthesize voiceover using edge-tts.

    Captures WordBoundary events for real word timings. Falls back to
    estimate_word_timings if no boundary events are yielded.
    """
    import edge_tts

    voice = settings.edge_tts_voice or "en-US-AndrewNeural"
    if voice not in KNOWN_EDGE_TTS_VOICES:
        logger.warning(
            "Unknown edge-tts voice %r; known voices: %s",
            voice,
            sorted(KNOWN_EDGE_TTS_VOICES),
        )

    out_audio = out_dir / "vo.mp3"
    words: list[WordTiming] = []
    spoken_words: list[str] = []
    audio_chunks: list[bytes] = []

    communicate = edge_tts.Communicate(text, voice)
    async for message in communicate.stream():
        msg_type = message.get("type")
        if msg_type == "audio":
            audio_chunks.append(message["data"])
        elif msg_type == "WordBoundary":
            offset = message["offset"]
            duration = message["duration"]
            start_s = offset / 1e7
            end_s = (offset + duration) / 1e7
            word_text = message.get("text", "")
            words.append(WordTiming(word=word_text, start_s=start_s, end_s=end_s))
            spoken_words.append(word_text)

    if not audio_chunks:
        raise RuntimeError("edge_tts produced no audio data")

    out_audio.write_bytes(b"".join(audio_chunks))

    duration_s: float
    if words:
        # Prefer ffprobe duration if available, else timing of the last word
        probed = _get_audio_duration(out_audio, settings.ffprobe_bin)
        duration_s = probed if probed is not None else words[-1].end_s
        # Spoken text reflects what TTS normalised (Rule C3)
        transcript = " ".join(spoken_words)
    else:
        # Fallback if stream yielded no WordBoundary events
        probed = _get_audio_duration(out_audio, settings.ffprobe_bin)
        fallback_dur = declared_duration_s if declared_duration_s > 0 else 0.0
        duration_s = probed if probed is not None else fallback_dur
        words = estimate_word_timings(text, duration_s)
        transcript = text

    return out_audio, transcript, words, duration_s


def _is_piper_available(settings: Settings) -> tuple[bool, str]:
    """Check if piper voice model and binary exist on disk."""
    voice_path = Path(settings.piper_voice_path)
    if not voice_path.is_file():
        return False, f"Piper voice model not found at {voice_path}"

    piper_exe = os.environ.get("CWT_PIPER_BIN", "piper")
    resolved = shutil.which(piper_exe)
    if not resolved and not (Path(piper_exe).is_file() and Path(piper_exe).is_absolute()):
        return False, f"Piper executable {piper_exe!r} not found in PATH"

    return True, ""


def _synthesize_piper(
    text: str,
    out_dir: Path,
    settings: Settings,
    declared_duration_s: float,
) -> tuple[Path, str, list[WordTiming], float]:
    """Synthesize voiceover using offline Piper.

    Emits WAV audio. Word timings are estimated via estimate_word_timings.
    """
    voice_path = Path(settings.piper_voice_path)
    voice_stem = voice_path.stem
    if voice_stem not in KNOWN_PIPER_VOICES:
        logger.warning(
            "Unknown piper voice model %r; known voices: %s",
            voice_stem,
            sorted(KNOWN_PIPER_VOICES),
        )

    piper_exe = os.environ.get("CWT_PIPER_BIN", "piper")
    out_audio = out_dir / "vo.wav"

    res = run_tool(
        [piper_exe, "--model", str(voice_path), "--output_file", str(out_audio)],
        input=text,
        cwd=out_dir,
        check=True,
    )
    if not out_audio.is_file() or out_audio.stat().st_size == 0:
        raise RuntimeError(f"Piper failed to produce audio file: {res.stderr}")

    duration_s = _get_audio_duration(out_audio, settings.ffprobe_bin)
    if duration_s is None or duration_s <= 0.0:
        duration_s = declared_duration_s if declared_duration_s > 0 else 0.0

    words = estimate_word_timings(text, duration_s)
    transcript = text

    return out_audio, transcript, words, duration_s


def _synthesize_silent(
    text: str,
    declared_duration_s: float,
) -> tuple[None, str, list[WordTiming], float]:
    """Fallback silent backend.

    Writes no audio file (audio_path=None). Timings are estimated across
    the storyboard's declared duration. Captions are burned in by S16.
    """
    duration_s = declared_duration_s if declared_duration_s > 0 else 0.0
    words = estimate_word_timings(text, duration_s)
    if words and duration_s <= 0.0:
        duration_s = words[-1].end_s

    transcript = text
    return None, transcript, words, duration_s


async def synthesize_voiceover(
    *,
    text: str,
    segments: list[VOSegment],
    out_dir: Path,
    settings: Settings,
) -> VoiceoverResult:
    """Synthesize voiceover audio and produce word-timed transcript.

    Tries backends according to settings.tts_backend_chain:
    1. edge_tts: real word boundaries from stream
    2. piper: offline WAV generation via run_tool
    3. silent: captions-only fallback floor (audio_path=None)
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    declared_duration_s = max((s.end_s for s in segments), default=0.0)

    effective_text = text.strip()
    if not effective_text and segments:
        effective_text = " ".join(s.text.strip() for s in segments if s.text and s.text.strip())

    chain = settings.tts_backend_chain
    chain_tried: list[dict[str, Any]] = []

    for backend in chain:
        t0 = time.monotonic()

        if backend == "edge_tts":
            avail, reason = _is_edge_tts_available(settings)
            if not avail:
                chain_tried.append({
                    "backend": "edge_tts",
                    "available": False,
                    "attempted": False,
                    "reason": reason,
                })
                continue

            try:
                audio_path, transcript, words, duration_s = await asyncio.wait_for(
                    _synthesize_edge_tts(effective_text, out_dir, settings, declared_duration_s),
                    timeout=120.0,
                )
                elapsed = time.monotonic() - t0
                chain_tried.append({
                    "backend": "edge_tts",
                    "available": True,
                    "attempted": True,
                    "succeeded": True,
                    "elapsed_s": round(elapsed, 4),
                })
                return VoiceoverResult(
                    audio_path=audio_path,
                    transcript=transcript,
                    words=words,
                    duration_s=duration_s,
                    backend_used="edge_tts",
                    chain_tried=chain_tried,
                )
            except Exception as exc:
                elapsed = time.monotonic() - t0
                logger.warning("edge_tts backend failed: %s", exc)
                chain_tried.append({
                    "backend": "edge_tts",
                    "available": True,
                    "attempted": True,
                    "succeeded": False,
                    "error": str(exc),
                    "elapsed_s": round(elapsed, 4),
                })
                continue

        elif backend == "piper":
            avail, reason = _is_piper_available(settings)
            if not avail:
                chain_tried.append({
                    "backend": "piper",
                    "available": False,
                    "attempted": False,
                    "reason": reason,
                })
                continue

            try:
                audio_path, transcript, words, duration_s = _synthesize_piper(
                    effective_text, out_dir, settings, declared_duration_s
                )
                elapsed = time.monotonic() - t0
                chain_tried.append({
                    "backend": "piper",
                    "available": True,
                    "attempted": True,
                    "succeeded": True,
                    "elapsed_s": round(elapsed, 4),
                })
                return VoiceoverResult(
                    audio_path=audio_path,
                    transcript=transcript,
                    words=words,
                    duration_s=duration_s,
                    backend_used="piper",
                    chain_tried=chain_tried,
                )
            except Exception as exc:
                elapsed = time.monotonic() - t0
                logger.warning("piper backend failed: %s", exc)
                chain_tried.append({
                    "backend": "piper",
                    "available": True,
                    "attempted": True,
                    "succeeded": False,
                    "error": str(exc),
                    "elapsed_s": round(elapsed, 4),
                })
                continue

        elif backend == "silent":
            try:
                audio_path, transcript, words, duration_s = _synthesize_silent(
                    effective_text, declared_duration_s
                )
                elapsed = time.monotonic() - t0
                chain_tried.append({
                    "backend": "silent",
                    "available": True,
                    "attempted": True,
                    "succeeded": True,
                    "elapsed_s": round(elapsed, 4),
                })
                return VoiceoverResult(
                    audio_path=audio_path,
                    transcript=transcript,
                    words=words,
                    duration_s=duration_s,
                    backend_used="silent",
                    chain_tried=chain_tried,
                )
            except Exception as exc:
                elapsed = time.monotonic() - t0
                logger.warning("silent backend failed: %s", exc)
                chain_tried.append({
                    "backend": "silent",
                    "available": True,
                    "attempted": True,
                    "succeeded": False,
                    "error": str(exc),
                    "elapsed_s": round(elapsed, 4),
                })
                continue

        else:
            chain_tried.append({
                "backend": backend,
                "available": False,
                "attempted": False,
                "reason": f"Unknown TTS backend: {backend}",
            })

    raise TTSUnavailable(
        f"All TTS backends in chain exhausted: {chain}. chain_tried: {chain_tried}"
    )


__all__ = [
    "KNOWN_EDGE_TTS_VOICES",
    "KNOWN_PIPER_VOICES",
    "TTSUnavailable",
    "VoiceoverResult",
    "WordTiming",
    "estimate_word_timings",
    "synthesize_voiceover",
]
