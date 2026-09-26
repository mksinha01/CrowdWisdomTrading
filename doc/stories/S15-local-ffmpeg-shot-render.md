# S15 — Local ffmpeg backend A — shot render

**Phase** 4 · **Depends on** S14 · **Blocks** S16
**Spec** `doc/video-ads-agent.md` lines **5265–5372** (§17 ffmpeg snippets), **623–785** (§3.4 shot grammar), **4746–4757** (Rule V1)
**Context budget** ~16k (spec 4k + story 1.6k + output 9k)
**Produces** `video/local_ffmpeg.py` (part A: asset → clip)

---

## Goal

Turn one `Shot` plus its resolved asset into one rendered clip. This is the guaranteed floor — the
backend that always works, in every environment, with no API key and no network.

**Part A renders shots. Part B (S16) mixes audio, concatenates, probes and writes the manifest.**
Split point is marked at the bottom of this file.

## Interface contract — FROZEN

```python
# video/local_ffmpeg.py

class LocalFfmpegBackend:
    name = "local_ffmpeg"

    def available(self) -> Availability:
        """Available iff ffmpeg resolves AND has libx264 + aac + zoompan."""
        # parse `ffmpeg -version` / `-filters`; reason names what is missing

    def render(self, storyboard: Storyboard, *, paths: RunPaths,
               voiceover: VoiceoverResult) -> RenderResult: ...

# ── part A internals ──
def render_shot(shot: Shot, *, asset: Path, out_path: Path, settings: Settings,
                paths: RunPaths) -> ToolResult:
    """One labelled input -> one clip. cwd pinned; every path RELATIVE (Rule V1)."""

def build_shot_argv(shot: Shot, *, asset: Path, audio: Path | None, out_path: Path,
                    settings: Settings) -> list[str]: ...
```

## Rules that bind this story

- **Rule V1** — `cwd` is the artifact directory and **every path inside a filtergraph is relative,
  forward-slashed**. ffmpeg's parser reads the colon in `C:\...` as an option separator, producing
  `Unable to parse option value "\runs\..."`. Pass `cwd=paths.shot_dir` to `run_tool` and reference
  `assets/s01.png`, never an absolute path.
- **Rule V2** — the filtergraph is assembled from **typed, clamped values only**, via
  `build_shot_filter` (S14). Never interpolate model text directly. `build_zoompan_expr` takes
  `intensity` as a float and returns a *computed* expression.
- **Rule W5/W6** — prefer **one `-filter_complex` graph** over the concat demuxer. The demuxer's
  filelist needs posix paths, single quotes, LF and utf-8; a single graph avoids the whole class.
- **Rule W1/W7** — `run_tool()` only, utf-8, list args.
- **`-shortest`** — a still image has no natural end. Without `-shortest` (or an explicit `-t`) the
  clip runs to the audio length or forever.

## Build steps

1. `available()` — resolve ffmpeg, then check the build reports `libx264`, `aac`, `zoompan`. A missing
   `zoompan` is a real failure: every camera move silently becomes a static frame. Return
   `Availability(False, reason)` naming what is absent — S18's chain relies on this to skip cleanly.
2. `build_shot_argv` — one invocation per shot, exactly the shape at spec lines 5356–5371:
   ```
   ffmpeg -y
     -loop 1 -t {shot.duration_s} -i {asset_relative}
     [-i {vo_segment_relative}]                     # when the beat carries narration
     -filter_complex "[0:v]{build_shot_filter(shot, input_label='0:v', output_label='v', ...)}"
     -map "[v]" [-map "1:a"]
     -c:v libx264 -preset medium -crf 19
     [-c:a aac -b:a 192k]
     -shortest
     out.mp4
   ```
   `-crf 19` is a quality decision, not a default — do not "optimise" it to 23.
3. **Asset kind handling.** `shot.asset.kind` is one of `generated_chart`, `internal`, `cc0`. A
   `generated_chart` is drawn by S17 into a PNG at the same resolution; a `cc0` still comes from
   `fixtures/assets`. Either way this function receives a **resolved file path** and does not care
   which. That separation is deliberate — keep it.
4. **On-screen text.** `shot.on_screen_text[]` carries `at_s`/`until_s`. Render via a `drawtext`
   filter chained after the grade, using a bundled font (never a system font — CI has none). Escape
   `:`, `'`, and `%` in the text: a `drawtext` string with a bare colon breaks the filter. **This
   escaping is Rule V2 territory** — write a `_escape_drawtext(text: str) -> str` helper and unit-test
   it against `"1 opinion isn't a strategy: 74%"`.
5. **Transitions.** `transition_in`/`transition_out` are `cut | dissolve | flash_white | wipeleft |
   wiperight | zoomblur`. **Part A renders each shot standalone and does not apply transitions** —
   they are inter-shot and belong to the concatenation step in S16. Record that boundary in a comment
   so S16 does not re-implement shot rendering.
6. **Captions when `audio_path is None`** (the `silent` TTS path, S12): burn the VO segment text in
   as a subtitle band via `drawtext`, styled from the brand palette. Note the cross-reference to S12.
7. `render_shot` returns the raw `ToolResult` — it does **not** decide success. S16 owns Rule V3.

## Decisions the spec leaves open

- **`-preset medium`** is specified. Keep it. On a slow machine the render is ~90s for 42s of video;
  that is acceptable and matches the spec's own `elapsed_s: 96.3` example (line 869).
- **`paths.shot_dir` does not exist** on `RunPaths` (S02 defines `render`, not `render/shots`).
  Use `paths.render` as `cwd` and write clips as `render/s01.mp4`. **Do not add a property to
  `RunPaths`** — S02 is frozen and the flattening keeps paths short (Rule W9).
- **Font bundling.** Not specified. Use `Pillow` (already a dependency) to generate the text as a PNG
  overlay when `drawtext` is unavailable, and record the fallback in `render_manifest.warnings`. Prefer
  `drawtext` when `ffmpeg -filters` lists it.
- **Per-shot audio.** Only segments with non-empty `text` get an `-i` audio input. A shot with no
  narration must not get a silent audio stream — that breaks the concat in S16.

## Done when

```bash
.venv/Scripts/python -c "
from cwt.video.local_ffmpeg import LocalFfmpegBackend
print(LocalFfmpegBackend().available())

from cwt.domain.models import Storyboard
from cwt.video.local_ffmpeg import build_shot_argv
from cwt.config import Settings
import json, pathlib
sb = Storyboard.model_validate(json.loads(pathlib.Path('tests/fixtures/storyboard.json').read_text()))
argv = build_shot_argv(sb.shots[0], asset=pathlib.Path('assets/s01.png'), audio=None,
                       out_path=pathlib.Path('s01.mp4'), settings=Settings.from_env())
assert all(':' not in a or a.startswith('-') or '=' in a for a in argv), 'absolute path leaked (Rule V1)'
assert 'libx264' in argv and '-shortest' in argv
print(' '.join(argv[:14]))"

# real render of one shot (needs ffmpeg; ~3s)
.venv/Scripts/python -c "
from cwt.video.local_ffmpeg import render_shot, LocalFfmpegBackend
from cwt.video.ffmpeg_bin import probe ... # render fixtures/assets/sample.png for 2s and probe it"
```

## Handoff

S16 imports `render_shot` and `build_shot_argv` and **must not** re-implement shot rendering. S17 must
produce PNG assets at exactly `video_width × video_height` — a mismatched asset is handled by the
`scale`+`crop` at the head of the filter chain, but it letterboxes and the grade will look wrong.
