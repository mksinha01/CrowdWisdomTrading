# S16 — Local ffmpeg backend B — mix, concat, probe, manifest

**Phase** 4 · **Depends on** S15 · **Blocks** S25 (`tools/video.py`)
**Spec** `doc/video-ads-agent.md` lines **862–882** (`render_manifest.json`), **4774–4787** (Rule V3), **5374–5378** (loudnorm), **3527–3538** (QA card), **5099–5100** (§13 offline-first)
**Context budget** ~17k (spec 3.5k + story 1.7k + output 10k)
**Produces** `video/local_ffmpeg.py` (part B: `render()`)

---

## Goal

Assemble the rendered clips into one 30–60s vertical ad, mix the audio to broadcast loudness, **prove
the file is real**, and write the manifest that makes the whole render auditable.

The governing rule here is V3: *"ffmpeg exits 0 having written a 0-byte or truncated file when the
last frame is dropped or a filter silently no-ops. 'Return code 0' is not 'it worked'."*

## Interface contract — FROZEN

```python
# video/local_ffmpeg.py  (part B — same class as S15)
class LocalFfmpegBackend:
    def render(self, storyboard: Storyboard, *, paths: RunPaths,
               voiceover: VoiceoverResult) -> RenderResult: ...

# ── part B internals ──
def concat_clips(clip_paths: list[Path], transitions: list[Transition], *,
                 out_path: Path, settings: Settings, paths: RunPaths) -> ToolResult: ...
def mix_audio(voiceover: VoiceoverResult, music: Music, *, out_path: Path,
              settings: Settings, paths: RunPaths) -> ToolResult: ...
def normalise_loudness(src: Path, dst: Path, *, target_lufs: float, true_peak_dbtp: float,
                       paths: RunPaths) -> ToolResult: ...
def write_render_manifest(...) -> Path: ...
```

## Rules that bind this story

- **Rule V3 — probe before declaring success.**
  ```
  run_tool([ffmpeg, ...], check=True)
  probe = ffprobe(out)
  assert probe.duration_s > 0 and probe.width > 0 and probe.height > 0
  assert settings.video_min_seconds <= probe.duration_s <= settings.video_max_seconds
  ```
  Exit code 0 is not proof. The assertion is the proof.
- **Rule V1** — `cwd=paths.render`, all paths relative. The concat step is where an absolute path is
  most tempting and most fatal.
- **Rule W5/W6** — prefer a **single `-filter_complex` concat graph** over the concat demuxer. If you
  must use the demuxer, the filelist needs posix paths, single quotes, `newline="\n"`, utf-8.
- **Rule C3** — the risk disclosure's on-screen duration is **recomputed from the rendered timeline**,
  not trusted from the storyboard's declared value. That recomputation happens in S25's QA pass; this
  story must make it *possible* by recording per-shot start times in the manifest.
- **Rule V4** — this backend is the chain's guaranteed floor. It must not depend on network, node, or
  any API key.

## Build steps

1. `concat_clips` — build one `filter_complex` graph:
   `[0:v][1:v]...concat=n=N:v=1:a=0[v]` for video. Video-only concat is correct here because audio is
   mixed separately (step 2) — do **not** try to concat audio per-clip.
   - Apply `transition_out` between adjacent clips. `cut` is a hard join. `dissolve`/`flash_white`/
     `wipe*`/`zoomblur` use `xfade` with the declared `duration_s`. **`xfade` shortens the total
     duration by the transition duration** — accumulate the loss and subtract it from the target, or
     the ad comes out under `VIDEO_MIN_SECONDS` and validator 3 fails on a file that was correct.
2. `mix_audio` — voiceover (or silence when `audio_path is None`) plus the music bed from
   `storyboard.music`, with the `intensity_curve` applied via `volume` expressions and `duck_under_vo`
   implemented as a sidechain compressor keyed on the VO. `riser_at_s`/`resolve_at_s` are cue points
   in the bed asset, not fades to synthesise.
3. `normalise_loudness` — `loudnorm=I={target}:TP={peak}:LRA=11` on the **mixed** audio (spec line
   5377). Targets come from `settings.video_loudness_lufs` / `video_true_peak_dbtp`. Use two-pass
   loudnorm if the single pass misses target by more than 1 LU; record which pass ran.
4. Final mux — video from step 1, audio from step 3, `-shortest`, `-c:v libx264 -crf 19 -c:a aac -b:a 192k`.
5. **Rule V3 block** — `check=True`, then `probe()`. On any assertion failure return
   `RenderResult(ok=False, error=..., argv=argv)` with the actual measured values in the message.
   **Do not raise** — the chain tries the next backend, and `local_ffmpeg` is the last one.
6. `write_render_manifest` — the full §3.5 shape (spec lines 862–882):
   `backend_chain_configured`, `backend_chain_tried`, `backend_used`, `output` (path, sha256, duration,
   w/h, fps, codecs), `loudness` (integrated_lufs, true_peak_dbtp, lra), `argv`, `cwd`, `assets` (with
   licences), `ffmpeg_version`, `warnings`.
   **Add one field not in the spec:** `shots: [{id, start_s, duration_s, rendered_clip}]` — S25's QA
   card needs the rendered timeline to recompute the risk-disclosure duration (Rule C3). Add it as an
   optional field so §3.5's example still validates.
7. `render()` orchestration: resolve VO → render each shot (S15) → concat → mix → loudnorm → mux →
   probe → manifest. Collect per-step `elapsed_s` for `backend_chain_tried`.
8. **Captions path.** When `voiceover.audio_path is None` (the `silent` TTS backend), S15 already burned
   captions in per shot. Here, just skip the audio mix and pass `-an`, then assert the probe shows no
   audio stream. The ad must still be watchable.

## Decisions the spec leaves open

- **`shots` in the manifest** (above) is a deliberate, append-only addition required by Rule C3.
  Mark it optional in `RenderManifest` (S03) so the §3.5 fixture still parses.
- **Music asset.** `storyboard.music.asset_ref` points into `fixtures/assets` (S17). If the referenced
  bed is missing, mix VO only and add a warning — **never fail the render for a missing music bed**.
- **`xfade` duration accounting** is the subtlest bug in this story. Write a test: 3 clips of 10s with
  two 0.4s dissolves must probe at 29.2s, not 30.0s.
- **Two-pass loudnorm** — the spec shows only the single-pass string. Add the second pass guarded by a
  tolerance check; it is cheap and the difference shows on small speakers.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_local_ffmpeg.py -q -v     # green

# real end-to-end render from a fixture storyboard, offline
.venv/Scripts/python -c "
import asyncio, json, pathlib
from cwt.domain.models import Storyboard
from cwt.util.paths import RunPaths
from cwt.video.local_ffmpeg import LocalFfmpegBackend
from cwt.clients.tts import VoiceoverResult
sb = Storyboard.model_validate(json.loads(pathlib.Path('tests/fixtures/storyboard.json').read_text()))
paths = RunPaths(pathlib.Path('runs/_s16_test')).ensure()
vo = VoiceoverResult(audio_path=None, transcript=sb.voiceover.full_text, words=[],
                     duration_s=sb.meta.total_duration_s, backend_used='silent', chain_tried=[])
r = LocalFfmpegBackend().render(sb, paths=paths, voiceover=vo)
print(r.ok, r.output, r.error)
from cwt.video.ffmpeg_bin import probe
print(probe(r.output))"
# -> ok True; duration within [30,60]; 1080x1920; aac present (or absent on the silent path)

# negative proof of Rule V3
.venv/Scripts/python -c "
# monkeypatch probe to return duration_s=0 -> render() must return ok=False, NOT raise
"
```

## Handoff

S25's `cwt_render_video` calls `VideoBackendChain.render()` and writes the manifest it returns. S25's
`cwt_probe_media` independently re-probes the output for QA — it must agree with the manifest, and a
disagreement is a bug in one of them. `render_manifest.shots[]` is the input to the QA card's
risk-disclosure duration recomputation.
