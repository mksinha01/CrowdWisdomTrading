"""Tests for TTS client and word-timed transcript (Story S12).

Verifies:
- estimate_word_timings contract (proportional distribution, endpoint equality, contiguity)
- edge_tts backend streaming, real word timings, Rule C3 normalisation
- piper offline backend execution via run_tool with stdin
- silent backend fallback with burned-in captions contract (audio_path=None)
- Fallback chain mechanics and error provenance in chain_tried
- Settings-driven chain configuration
- TTSUnavailable on chain exhaustion
- No pydub / librosa dependencies
"""
from __future__ import annotations

import inspect
import sys
import wave
from dataclasses import replace
from pathlib import Path

import pytest

import cwt.clients.tts as tts_mod
from cwt.clients.tts import (
    KNOWN_EDGE_TTS_VOICES,
    KNOWN_PIPER_VOICES,
    TTSUnavailable,
    VoiceoverResult,
    WordTiming,
    estimate_word_timings,
    synthesize_voiceover,
)
from cwt.config import Settings
from cwt.domain.models import VOSegment
from cwt.util.subproc import ToolResult

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

def _make_dummy_wav(path: Path, duration_s: float = 1.5, sample_rate: int = 16000) -> None:
    """Create a minimal valid WAV file with exact duration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    num_frames = int(duration_s * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * num_frames)


class _MockCommunicateSuccess:
    """Mock edge_tts.Communicate that emits audio and WordBoundary events."""

    def __init__(self, text: str, voice: str):
        self.text = text
        self.voice = voice

    async def stream(self):
        yield {"type": "audio", "data": b"RIFF_FAKE_AUDIO_PART_1"}
        # Emit WordBoundary events for 3 words
        yield {
            "type": "WordBoundary",
            "offset": 0,
            "duration": 5000000,   # 0.5s
            "text": "Too",
        }
        yield {"type": "audio", "data": b"_PART_2"}
        yield {
            "type": "WordBoundary",
            "offset": 5000000,     # 0.5s
            "duration": 7000000,   # 0.7s -> ends at 1.2s
            "text": "many",
        }
        yield {
            "type": "WordBoundary",
            "offset": 12000000,    # 1.2s
            "duration": 10000000,  # 1.0s -> ends at 2.2s
            "text": "voices",
        }


class _MockCommunicateNormalised:
    """Mock edge_tts.Communicate that normalises text (Rule C3)."""

    def __init__(self, text: str, voice: str):
        self.text = text
        self.voice = voice

    async def stream(self):
        yield {"type": "audio", "data": b"MP3_NORMAL_AUDIO"}
        # "$29.99" normalised into spoken words
        words = ["twenty", "nine", "dollars", "ninety", "nine"]
        t = 0
        for w in words:
            yield {
                "type": "WordBoundary",
                "offset": t,
                "duration": 4000000,  # 0.4s
                "text": w,
            }
            t += 4000000


class _MockCommunicateNoBoundaries:
    """Mock edge_tts.Communicate that produces audio but no WordBoundary events."""

    def __init__(self, text: str, voice: str):
        self.text = text
        self.voice = voice

    async def stream(self):
        yield {"type": "audio", "data": b"MP3_AUDIO_ONLY"}


class _MockCommunicateError:
    """Mock edge_tts.Communicate that raises a network / API failure."""

    def __init__(self, text: str, voice: str):
        self.text = text
        self.voice = voice

    async def stream(self):
        raise ConnectionError("wss://speech.platform.bing.com connection reset")
        yield  # make it a generator


# ---------------------------------------------------------------------------
# estimate_word_timings Tests
# ---------------------------------------------------------------------------

def test_estimate_word_timings_done_when():
    """Verify Done When acceptance test from S12."""
    w = estimate_word_timings("Too many voices. Every one of them is certain.", 6.0)
    assert w[0].start_s == 0.0 and abs(w[-1].end_s - 6.0) < 1e-6
    assert all(abs(a.end_s - b.start_s) < 1e-9 for a, b in zip(w, w[1:]))
    assert len(w) == 9
    assert w[-1].word == "certain."


def test_estimate_word_timings_proportional_weights():
    """Longer words must receive proportionally longer duration."""
    w = estimate_word_timings("a supercalifragilisticexpialidocious", 10.0)
    assert len(w) == 2
    short_dur = w[0].end_s - w[0].start_s
    long_dur = w[1].end_s - w[1].start_s
    # "a" has weight 2, the long word has weight 35
    assert long_dur > short_dur * 10
    assert abs(w[-1].end_s - 10.0) < 1e-6


def test_estimate_word_timings_empty_and_whitespace():
    """Empty or whitespace-only text returns empty list."""
    assert estimate_word_timings("", 6.0) == []
    assert estimate_word_timings("    \n\t  ", 6.0) == []


def test_estimate_word_timings_zero_duration_fallback():
    """When duration_s <= 0, duration is estimated from WPM."""
    text = "one two three four five six seven"
    # 7 words at 210 WPM = (7 / 210) * 60 = 2.0s
    w = estimate_word_timings(text, 0.0, wpm=210.0)
    assert len(w) == 7
    assert abs(w[-1].end_s - 2.0) < 1e-6
    assert w[0].start_s == 0.0


def test_estimate_word_timings_single_word():
    """Single word takes entire duration."""
    w = estimate_word_timings("Trading", 3.5)
    assert len(w) == 1
    assert w[0].word == "Trading"
    assert w[0].start_s == 0.0
    assert abs(w[0].end_s - 3.5) < 1e-6


# ---------------------------------------------------------------------------
# edge_tts Backend Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edge_tts_success(monkeypatch, tmp_path):
    """edge_tts synthesizes MP3, extracts real WordBoundary timings and transcript."""
    monkeypatch.setattr("edge_tts.Communicate", _MockCommunicateSuccess)

    settings = Settings.from_env()
    segments = [VOSegment(shot_id="s01", text="Too many voices", start_s=0.0, end_s=2.5)]

    res = await synthesize_voiceover(
        text="Too many voices",
        segments=segments,
        out_dir=tmp_path,
        settings=settings,
    )

    assert isinstance(res, VoiceoverResult)
    assert res.backend_used == "edge_tts"
    assert res.audio_path == tmp_path / "vo.mp3"
    assert res.audio_path.is_file()
    assert res.audio_path.read_bytes() == b"RIFF_FAKE_AUDIO_PART_1_PART_2"

    # Rule C3: transcript contains the spoken words
    assert res.transcript == "Too many voices"
    assert len(res.words) == 3
    assert res.words[0] == WordTiming(word="Too", start_s=0.0, end_s=0.5)
    assert res.words[1] == WordTiming(word="many", start_s=0.5, end_s=1.2)
    assert res.words[2] == WordTiming(word="voices", start_s=1.2, end_s=2.2)
    assert abs(res.duration_s - 2.2) < 1e-6

    # Provenance
    assert len(res.chain_tried) == 1
    assert res.chain_tried[0]["backend"] == "edge_tts"
    assert res.chain_tried[0]["available"] is True
    assert res.chain_tried[0]["attempted"] is True
    assert res.chain_tried[0]["succeeded"] is True
    assert res.chain_tried[0]["elapsed_s"] >= 0.0


@pytest.mark.asyncio
async def test_edge_tts_rule_c3_normalisation(monkeypatch, tmp_path):
    """Rule C3: Spoken transcript must reflect TTS normalisation (e.g. $29.99)."""
    monkeypatch.setattr("edge_tts.Communicate", _MockCommunicateNormalised)

    settings = Settings.from_env()
    segments = [VOSegment(shot_id="s01", text="Only $29.99", start_s=0.0, end_s=3.0)]

    res = await synthesize_voiceover(
        text="Only $29.99",
        segments=segments,
        out_dir=tmp_path,
        settings=settings,
    )

    # Transcript must be what is ACTUALLY spoken, not the input text
    assert res.transcript == "twenty nine dollars ninety nine"
    assert res.words[0].word == "twenty"
    assert res.words[-1].word == "nine"


@pytest.mark.asyncio
async def test_edge_tts_no_word_boundaries_fallback(monkeypatch, tmp_path):
    """If edge_tts yields no WordBoundary events, fallback to estimate_word_timings."""
    monkeypatch.setattr("edge_tts.Communicate", _MockCommunicateNoBoundaries)

    settings = Settings.from_env()
    segments = [VOSegment(shot_id="s01", text="Trading signals", start_s=0.0, end_s=2.0)]

    res = await synthesize_voiceover(
        text="Trading signals",
        segments=segments,
        out_dir=tmp_path,
        settings=settings,
    )

    assert res.backend_used == "edge_tts"
    assert res.audio_path == tmp_path / "vo.mp3"
    assert res.transcript == "Trading signals"
    assert len(res.words) == 2
    assert res.words[0].start_s == 0.0
    assert abs(res.words[-1].end_s - 2.0) < 1e-6


@pytest.mark.asyncio
async def test_edge_tts_unknown_voice_warning(monkeypatch, caplog, tmp_path):
    """Warns on unknown voice per spec §15 but does not raise."""
    monkeypatch.setattr("edge_tts.Communicate", _MockCommunicateSuccess)

    base = Settings.from_env()
    settings = replace(base, edge_tts_voice="unknown-custom-voice")

    with caplog.at_level("WARNING"):
        res = await synthesize_voiceover(
            text="Hello world",
            segments=[],
            out_dir=tmp_path,
            settings=settings,
        )

    assert res.backend_used == "edge_tts"
    assert any("Unknown edge-tts voice" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Piper Offline Backend Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fallback_edge_to_piper(monkeypatch, tmp_path):
    """When edge_tts fails, chain falls back to piper."""
    monkeypatch.setattr("edge_tts.Communicate", _MockCommunicateError)

    voice_file = tmp_path / "voice.onnx"
    voice_file.write_bytes(b"FAKE_ONNX_MODEL")

    base = Settings.from_env()
    settings = replace(base, piper_voice_path=str(voice_file))

    # Mock run_tool to simulate piper CLI execution
    def mock_run_tool(args, *, cwd=None, input=None, **kwargs):
        assert "piper" in args[0]
        assert "--model" in args
        assert "--output_file" in args
        assert input == "Fallback to piper speech"
        out_wav = Path(args[args.index("--output_file") + 1])
        _make_dummy_wav(out_wav, duration_s=1.8)
        return ToolResult(args=list(args), cwd=str(cwd), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("cwt.clients.tts.run_tool", mock_run_tool)
    monkeypatch.setattr("cwt.clients.tts.shutil.which", lambda exe: f"/bin/{exe}")

    res = await synthesize_voiceover(
        text="Fallback to piper speech",
        segments=[],
        out_dir=tmp_path,
        settings=settings,
    )

    assert res.backend_used == "piper"
    assert res.audio_path == tmp_path / "vo.wav"
    assert res.audio_path.is_file()
    assert res.transcript == "Fallback to piper speech"
    assert abs(res.duration_s - 1.8) < 1e-3
    assert len(res.words) == 4
    assert res.words[0].start_s == 0.0
    assert abs(res.words[-1].end_s - 1.8) < 1e-3

    # Check chain_tried
    assert len(res.chain_tried) == 2
    assert res.chain_tried[0]["backend"] == "edge_tts"
    assert res.chain_tried[0]["succeeded"] is False
    assert "connection reset" in res.chain_tried[0]["error"]
    assert res.chain_tried[1]["backend"] == "piper"
    assert res.chain_tried[1]["succeeded"] is True


@pytest.mark.asyncio
async def test_piper_voice_absent_skips_without_error(monkeypatch, tmp_path):
    """When piper voice model is absent, it records available=False and skips."""
    monkeypatch.setattr("edge_tts.Communicate", _MockCommunicateError)

    base = Settings.from_env()
    # Path does not exist
    settings = replace(base, piper_voice_path=str(tmp_path / "nonexistent.onnx"))

    segments = [
        VOSegment(shot_id="s01", text="No piper voice available", start_s=0.0, end_s=4.0),
    ]
    res = await synthesize_voiceover(
        text="No piper voice available",
        segments=segments,
        out_dir=tmp_path,
        settings=settings,
    )

    # Falls through to silent
    assert res.backend_used == "silent"
    assert res.audio_path is None

    assert len(res.chain_tried) == 3
    # edge_tts
    assert res.chain_tried[0]["backend"] == "edge_tts"
    assert res.chain_tried[0]["attempted"] is True
    assert res.chain_tried[0]["succeeded"] is False
    # piper
    assert res.chain_tried[1]["backend"] == "piper"
    assert res.chain_tried[1]["available"] is False
    assert res.chain_tried[1]["attempted"] is False
    assert "not found" in res.chain_tried[1]["reason"]
    # silent
    assert res.chain_tried[2]["backend"] == "silent"
    assert res.chain_tried[2]["succeeded"] is True


# ---------------------------------------------------------------------------
# Silent Backend Tests (Captions Floor)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_silent_backend_floor(tmp_path):
    """Silent backend produces no audio (audio_path=None), timings over declared duration."""
    base = Settings.from_env()
    settings = replace(base, tts_backend_chain=["silent"])

    segments = [
        VOSegment(shot_id="s01", text="First shot", start_s=0.0, end_s=2.0),
        VOSegment(shot_id="s02", text="Second shot ending at five", start_s=2.0, end_s=5.0),
    ]

    res = await synthesize_voiceover(
        text="First shot Second shot ending at five",
        segments=segments,
        out_dir=tmp_path,
        settings=settings,
    )

    assert res.backend_used == "silent"
    assert res.audio_path is None
    assert abs(res.duration_s - 5.0) < 1e-6
    assert res.transcript == "First shot Second shot ending at five"
    assert len(res.words) == 7
    assert res.words[0].start_s == 0.0
    assert abs(res.words[-1].end_s - 5.0) < 1e-6
    assert all(abs(a.end_s - b.start_s) < 1e-9 for a, b in zip(res.words, res.words[1:]))

    assert len(res.chain_tried) == 1
    assert res.chain_tried[0]["backend"] == "silent"
    assert res.chain_tried[0]["succeeded"] is True


# ---------------------------------------------------------------------------
# Chain Configuration & Invariants
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chain_order_from_settings_honoured(monkeypatch, tmp_path):
    """Backend chain order is strictly governed by settings.tts_backend_chain."""
    base = Settings.from_env()
    settings = replace(base, tts_backend_chain=["silent", "edge_tts"])

    res = await synthesize_voiceover(
        text="Testing custom chain order",
        segments=[],
        out_dir=tmp_path,
        settings=settings,
    )

    # silent was first in chain, so edge_tts was never attempted
    assert res.backend_used == "silent"
    assert len(res.chain_tried) == 1
    assert res.chain_tried[0]["backend"] == "silent"


@pytest.mark.asyncio
async def test_chain_exhausted_raises_tts_unavailable(monkeypatch, tmp_path):
    """When all configured backends fail and no silent floor is present, raise TTSUnavailable."""
    monkeypatch.setattr("edge_tts.Communicate", _MockCommunicateError)

    base = Settings.from_env()
    # Configure chain without silent
    settings = replace(base, tts_backend_chain=["edge_tts"])

    with pytest.raises(TTSUnavailable, match="All TTS backends in chain exhausted"):
        await synthesize_voiceover(
            text="Failing run",
            segments=[],
            out_dir=tmp_path,
            settings=settings,
        )


@pytest.mark.asyncio
async def test_empty_text_derives_from_segments(monkeypatch, tmp_path):
    """When text is empty, segments text is joined and synthesized."""
    base = Settings.from_env()
    settings = replace(base, tts_backend_chain=["silent"])

    segments = [
        VOSegment(shot_id="s01", text="Segment one text", start_s=0.0, end_s=2.0),
        VOSegment(shot_id="s02", text="Segment two text", start_s=2.0, end_s=4.0),
    ]

    res = await synthesize_voiceover(
        text="",
        segments=segments,
        out_dir=tmp_path,
        settings=settings,
    )

    assert res.transcript == "Segment one text Segment two text"
    assert len(res.words) == 6
    assert abs(res.duration_s - 4.0) < 1e-6


# ---------------------------------------------------------------------------
# Architecture Rule & Dependency Invariants
# ---------------------------------------------------------------------------

def test_no_pydub_or_librosa_imported():
    """Spec decision: No pydub or librosa dependencies allowed."""
    src = inspect.getsource(tts_mod)
    assert "import pydub" not in src
    assert "from pydub" not in src
    assert "import librosa" not in src
    assert "from librosa" not in src
    assert "pydub" not in sys.modules
    assert "librosa" not in sys.modules


def test_known_voices_constants():
    """Known voices set matches spec lines 5202-5205."""
    assert "en-US-AndrewNeural" in KNOWN_EDGE_TTS_VOICES
    assert "en_US-ryan-high" in KNOWN_PIPER_VOICES
    assert len(KNOWN_EDGE_TTS_VOICES) == 5
    assert len(KNOWN_PIPER_VOICES) == 3
