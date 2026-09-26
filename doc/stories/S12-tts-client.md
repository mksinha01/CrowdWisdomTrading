# S12 — TTS client & word-timed transcript

**Phase** 2 · **Depends on** S02 · **Blocks** S25 (`tools/video.py`), S24 (post-render claims)
**Spec** `doc/video-ads-agent.md` lines **277–282** (`.env` TTS block), **5202–5205** (§15 voices), **4731–4744** (Rule C3), **181** (file tree)
**Context budget** ~10k (spec 2k + story 1.6k + output 6k) — **G12: this module has no spec section**
**Produces** `clients/tts.py`, `tests/test_tts.py`

---

## Goal

Turn the storyboard's voiceover into an audio file **and a word-timed transcript**. The transcript is
not a nicety: Rule C3 requires the claims gate to run a second time over *what is actually spoken*,
because TTS normalisation changes the text and a line added during render would otherwise bypass the
gate entirely.

> **G12.** The file tree lists `clients/tts.py` and `.env` configures `TTS_BACKEND_CHAIN`, but §8 has
> no TTS section. The contract below is **derived** from the chain semantics stated at spec line 278
> and the QA card's requirements at spec lines 3527–3538. Record this story's decisions in the file's
> module docstring so the derivation is auditable.

## Interface contract — NEW (derived — freeze it here)

```python
# clients/tts.py

class TTSUnavailable(RuntimeError): ...

@dataclass(frozen=True)
class WordTiming:
    word: str; start_s: float; end_s: float

@dataclass
class VoiceoverResult:
    audio_path: Path | None          # None on the "silent" backend
    transcript: str                  # the text AS SPOKEN — drives the post-render gate
    words: list[WordTiming]
    duration_s: float
    backend_used: str                # "edge_tts" | "piper" | "silent"
    chain_tried: list[dict]          # [{backend, available, attempted, succeeded, error?, elapsed_s}]

async def synthesize_voiceover(*, text: str, segments: list[VOSegment], out_dir: Path,
                               settings: Settings) -> VoiceoverResult: ...

def estimate_word_timings(text: str, duration_s: float, *, wpm: float = 210.0) -> list[WordTiming]: ...
```

Backend chain — tried left to right, **the last element must always succeed**:

| # | Backend | Available when | Produces |
|---|---|---|---|
| 1 | `edge_tts` | network up, `edge-tts` importable | MP3 + real word boundaries |
| 2 | `piper` | `PIPER_VOICE_PATH` exists on disk | WAV, offline |
| 3 | `silent` | **always** | no audio; captions only |

## Rules that bind this story

- **Rule C3** — the transcript must reflect **what is actually spoken**. If the TTS backend
  normalises `$29.99` to "twenty nine dollars ninety nine", that is what the post-render claims scan
  sees. Do not write the storyboard's `full_text` into `transcript` and call it done.
- **§2 line 279** — the captions-only path *"still yields a watchable, comprehensible ad."* `silent`
  is a legitimate success, not a degraded failure. The chain does not raise when it reaches `silent`.
- **Rule W1 / W7** — `edge-tts` and `piper` are invoked through `run_tool()`; utf-8 on every child.
- **The chain is a config value** (`TTS_BACKEND_CHAIN`). Never hardcode the order.

## Build steps

1. `estimate_word_timings` — distribute `duration_s` across words proportional to
   `len(word) + 1` (a crude syllable proxy). Guarantee: `timings[-1].end_s == duration_s`, no gaps, no
   overlaps. This is the fallback used by backends 2 and 3.
2. `edge_tts` backend — `edge_tts.Communicate(text, voice).save(path)`. Capture `WordBoundary` events
   for real timings; fall back to `estimate_word_timings` if the stream yields none. Wrap in
   `asyncio.wait_for(..., timeout=120)`.
3. `piper` backend — `run_tool([piper_exe, "--model", voice_path, "--output_file", out], cwd=...)`
   with `text` on stdin. Piper emits **WAV**; do not assume MP3 downstream.
4. `silent` backend — write no file, set `audio_path=None`, derive timings from `estimate_word_timings`
   over the storyboard's declared `total_duration_s`. The video backend (S16) then renders captions
   burned in.
5. Each backend is wrapped: `except Exception as exc: chain_tried.append({...error: str(exc)}); continue`.
   **Never raise from backends 1 or 2** — only the whole-chain-exhausted case raises, and it cannot
   happen because `silent` always works.
6. `transcript` construction — concatenate the per-segment text **as the backend rendered it**. For
   `edge_tts`, prefer its returned text; for the others, use the input text unchanged.
7. Tests: chain fallback (monkeypatch backend 1 to raise → backend 2 used → `backend_used == "piper"`);
   all-fail → `silent`; timing monotonicity and endpoint equality; the chain from `Settings` is
   honoured rather than hardcoded.

## Decisions the spec leaves open

- **`VoiceoverResult.chain_tried`** mirrors `render_manifest.backend_chain_tried` (spec line 866)
  deliberately — the same reporting shape for both fallback chains. Keep the key names identical:
  `backend, available, attempted, succeeded, error?, elapsed_s`.
- **`silent` and captions.** The spec says the captions-only path is watchable, but never says who
  burns the captions in. **Assign it to S16** (`local_ffmpeg`), driven by `audio_path is None`. Note
  the cross-story dependency in both files.
- **Voice list** (spec lines 5202–5205): edge-tts `en-US-AndrewNeural` (default), `en-US-BrianNeural`,
  `en-GB-RyanNeural`, `en-US-AvaNeural`, `en-GB-SoniaNeural`; piper `en_US-ryan-high`,
  `en_US-lessac-medium`, `en_GB-alan-medium`. Validate `settings.edge_tts_voice` against the list and
  warn on an unknown voice rather than failing mid-render.
- **No `pydub`/`librosa`.** Durations come from ffprobe (S14) or from the backend's own timings.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_tts.py -q -v      # green, no network

.venv/Scripts/python -c "
from cwt.clients.tts import estimate_word_timings
w = estimate_word_timings('Too many voices. Every one of them is certain.', 6.0)
assert w[0].start_s == 0.0 and abs(w[-1].end_s - 6.0) < 1e-6
assert all(abs(a.end_s - b.start_s) < 1e-9 for a,b in zip(w, w[1:]))
print(len(w), 'words, contiguous, ends at', w[-1].end_s)"
```

## Handoff

S25's `cwt_synthesize_voiceover` calls this and persists `VoiceoverResult` alongside the render
manifest (audio path, `backend_used`, `chain_tried`, and the word timings). S24's **post-render**
`cwt_check_claims` consumes `VoiceoverResult.transcript` — not the storyboard text. S16 reads
`audio_path is None` to decide whether to burn in captions.
