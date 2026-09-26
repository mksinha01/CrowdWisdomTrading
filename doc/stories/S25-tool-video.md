# S25 — Tool surface — video

**Phase** 5 · **Depends on** S16, S17, S12, S24 · **Blocks** S26
**Spec** `doc/video-ads-agent.md` lines **862–882** (`render_manifest.json`), **3526–3538** (the `qa` card), **3510–3525** (the `render` card), **4865–4878** (Rules W5–W6)
**Context budget** ~15k (spec 3k + story 1.7k + output 8.5k)
**Produces** `tools/video.py`, `tests/test_tools_video.py`

---

## Goal

Three tools: synthesize the voiceover, render through the backend chain, and probe the result. The
third one is the least glamorous and the most important — *"never silently skip verification"*
(spec line 4079).

## Interface contract — FROZEN

```python
# tools/video.py

def synthesize_voiceover(*, settings: Settings, paths: RunPaths) -> dict:
    """TTS chain; word-timed transcript.
    WRITES artifacts/voiceover.json + render/vo.<ext>
    RETURNS {"artifact_path","backend_used","duration_s","words":int,"chain_tried":[...],
             "has_audio":bool}"""

def render_video(*, settings: Settings, paths: RunPaths, backend: str | None = None) -> dict:
    """Render through the backend chain; write the manifest.
    WRITES render/final.mp4 + artifacts/render_manifest.json
    RETURNS {"artifact_path","output","backend_used","duration_s","width","height",
             "chain_tried":[...],"assets":int}"""

def probe_media(*, settings: Settings, paths: RunPaths, path: str | None = None) -> dict:
    """ffprobe a file. NEVER silently skips verification (Rule W3).
    WRITES nothing
    RETURNS {"path","duration_s","width","height","fps","video_codec","audio_codec",
             "integrated_lufs","true_peak_dbtp","size_bytes","probe_method"}"""
```

## Rules that bind this story

- **Rule V3** — probe before declaring success. `render_video` must surface a `ok=False` from the chain
  as a raise, and must include the measured values in the message.
- **Rule V4** — the chain always terminates in `local_ffmpeg`. `render_video` with an explicit
  `backend` argument is for debugging and CI (`--backend local_ffmpeg`), and pins the chain to one entry.
- **Rule C3** — `probe_media` is what makes the **post-render** compliance pass possible, and what
  recomputes the risk disclosure's on-screen duration from the **rendered timeline** rather than the
  storyboard's declared value.
- **Rule W3** — if `ffprobe` is missing, fall back to parsing `ffmpeg -i` stderr. **Never return
  `True` from a verification you could not perform.** Report `probe_method` as `"ffprobe"` or
  `"ffmpeg_stderr"` so a reader can see which ran.
- **§16.3 line 5250** — `cwt run --resume` after a render fix must cost **$0.00** because every upstream
  stage is a cache hit. `render_video` must therefore be the *only* stage that touches the render dir,
  and must not invalidate anything upstream.

## Build steps

1. `synthesize_voiceover` — read `storyboard.json`, call `synthesize_voiceover` from `clients/tts.py`
   (S12) over `voiceover.full_text` with the storyboard's `segments`. Persist a small
   `voiceover.json` alongside (`backend_used`, `duration_s`, word count, `chain_tried`,
   `audio_path`, and the **transcript**) — S24's post-render pass reads the transcript from here, and
   `--resume` must be able to re-run the compliance card without re-synthesizing audio.
   - if `backend_used == "silent"`, `has_audio` is `False` and the ad renders with burned-in captions.
     **This is a supported path, not a failure** (spec line 279).
2. `render_video` — build the chain via `build_chain(settings)` (S14), construct the
   `VoiceoverResult` from step 1's artifact, and call `render()` down the chain:
   - iterate; record each attempt into `chain_tried` with
     `{backend, available, attempted, succeeded, error?, elapsed_s}` — **the same shape** as S12's TTS
     `chain_tried` and as the spec's own manifest example (lines 866–870)
   - an unavailable backend is recorded with `attempted: false` and its `reason`, **never** as an error
   - on `ok=True`, stop. On the chain exhausted (possible only if `local_ffmpeg` also failed) —
     **raise**, because a failure there is a real failure (spec line 3522)
   - `--backend X` pins the chain to `[X]` for CI; assert the pinned name is `local_ffmpeg` when
     `--offline` is set, so an offline run cannot accidentally need node
   - write `render_manifest.json` from `RenderResult` plus the asset manifest (S17)
3. `probe_media`:
   - default `path` is the manifest's `output.path` (or `render/final.mp4`)
   - call `ffmpeg_bin.probe()` (S14) — never a bare `ffprobe`
   - **assert the file is not 0 bytes** before probing; a 0-byte file produces a confusing ffprobe error
   - return `probe_method` so a degraded verification is visible
4. **QA assertions** (the `qa` card's job, spec lines 3528–3537) — put them in a function
   `qa_check(...)` that S27's card calls, not inline in `probe_media`:
   - duration within `[video_min_seconds, video_max_seconds]`
   - `width == video_width` and `height == video_height`
   - `abs(integrated_lufs - video_loudness_lufs) <= 1.0` when audio is present
   - `true_peak_dbtp <= video_true_peak_dbtp + 0.5`
   - **risk disclosure duration recomputed from `render_manifest.shots[]`** (the field S16 added):
     sum the on-screen time of the disclosure shot within the final 8 seconds, and require `>= 3.0s`.
     This is Rule C3's second half.
   - a re-run of `cwt_check_claims(stage="post_render")` over the transcript
5. Docstrings — `probe_media`:
   > *CALL THIS: after every render, before declaring the card done. Also call it on any file you did
   > not produce yourself.*
   >
   > *WHEN NOT TO CALL: never skip it because ffmpeg exited 0. Exit code 0 has been observed on a
   > 0-byte file when the last frame is dropped (Rule V3). "Return code 0" is not "it worked".*
6. Tests:
   - `render_video` records an unavailable backend as `attempted: false` with a reason, not an error
   - a chain where `hyperframes` fails and `local_ffmpeg` succeeds returns `backend_used == "local_ffmpeg"`
     with `succeeded: false` recorded for the first
   - `probe_media` on a 0-byte file raises with a clear message
   - `probe_media` reports `probe_method == "ffmpeg_stderr"` when ffprobe is monkeypatched missing
   - `qa_check` fails when duration is 28s (below `video_min_seconds`)
   - `qa_check` fails when the recomputed disclosure duration is 2.1s despite the storyboard claiming 3.2

## Decisions the spec leaves open

- **`voiceover.json` is a scratch artifact**, not in `ARTIFACT_NAMES`. It exists so the post-render
  compliance pass and `--resume` do not need to re-synthesize audio. Document it as such.
- **Loudness assertion tolerance** is not specified. `±1.0 LU` for integrated and `+0.5 dBTP` for true
  peak are reasonable given single-pass `loudnorm`; S16's two-pass path should land inside them.
- **`qa_check` lives here, not in S24.** It needs media facts and claims findings in one place, and
  this module already imports both.
- **Never re-render to "fix" a QA failure.** If QA fails, the card blocks and `cwt run --resume` is the
  path — that keeps the $0.00-resume promise intact.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_tools_video.py -q -v      # green

.venv/Scripts/python -c "
import json, pathlib
from cwt.domain.models import RenderManifest
m = RenderManifest.model_validate(json.loads(pathlib.Path('runs/_s25/artifacts/render_manifest.json').read_text()))
print(m.backend_used, m.output['duration_s'], m.output['width'], 'x', m.output['height'])
print(m.loudness)
for t in m.backend_chain_tried: print(' ', t['backend'], t.get('available'), t.get('succeeded'), t.get('reason',''))
assert m.output['duration_s'] > 0
assert m.backend_chain_configured[-1] == 'local_ffmpeg'"

.venv/Scripts/python -c "
from cwt.video.ffmpeg_bin import probe
import pathlib
p = pathlib.Path('runs/_s25/render/final.mp4')
assert p.stat().st_size > 0
print(probe(p))"
```

## Handoff

S26's `cwt_assemble_submission` copies `render_manifest.json`, `final.mp4` and the claims reports into
`submission/`. S34's recording recipe captures the render card finishing and `final.mp4` appearing on
disk (spec line 3390). **If QA fails, the `qa` card blocks — it never completes, and it never re-renders.**
