# S14 — ffmpeg resolution, backend chain, filtergraph  ⚠ LARGE

**Phase** 4 · **Depends on** S03, S04 · **Blocks** S15, S18
**Spec** `doc/video-ads-agent.md` lines **3138–3234** (§8.8), **4789–4802** (Rule V4), **4817–4863** (Rules W2–W4), **4746–4787** (Rules V1–V3)
**Context budget** ~19k (spec 5k + story 1.7k + output 11k)
**Produces** `video/__init__.py`, `video/ffmpeg_bin.py`, `video/backend.py`, `video/filtergraph.py`, `tests/test_filtergraph.py`

---

## Goal

Three things that together make the video layer trustworthy:

1. **ffmpeg resolution that never assumes PATH** — a working `ffmpeg --version` in a Git Bash shell
   does *not* mean Python's PATH can see it.
2. **A backend chain that cannot hard-fail** — validated at config load so the last element is always
   the local assembler.
3. **A pure `Shot → filter string` function**, golden-tested, requiring no ffmpeg to test.

§8.8: *"If you want to know whether the video code is real, read `tests/test_filtergraph.py`."*

## Interface contract — FROZEN

```python
# video/ffmpeg_bin.py
def ffmpeg_path() -> str:
    """$CWT_FFMPEG_BIN -> imageio_ffmpeg.get_ffmpeg_exe() -> shutil.which('ffmpeg')"""
def ffprobe_path() -> str: ...
def ffmpeg_version() -> str: ...            # parse "ffmpeg version X.Y" from -version
def probe(path: Path) -> MediaInfo: ...     # ffprobe; falls back to ffmpeg -i stderr (Rule W3)
def probe_duration_from_stderr(path: Path) -> float: ...

@dataclass(frozen=True)
class MediaInfo:
    duration_s: float; width: int; height: int; fps: float
    video_codec: str; audio_codec: str | None
    integrated_lufs: float | None; true_peak_dbtp: float | None; lra: float | None
    size_bytes: int

# video/backend.py
@dataclass(frozen=True)
class Availability:
    available: bool; reason: str = ""

class VideoBackend(Protocol):
    name: str
    def available(self) -> Availability: ...
    def render(self, storyboard: Storyboard, *, paths: RunPaths,
               voiceover: VoiceoverResult) -> RenderResult: ...

@dataclass
class RenderResult:
    ok: bool; output: Path | None; backend: str
    elapsed_s: float; error: str | None; argv: list[str]; assets: list[dict]

class VideoBackendChain:
    backends: list[VideoBackend]
    def __getitem__(self, i) -> VideoBackend: ...
    def __len__(self) -> int: ...

def build_chain(settings: Settings) -> VideoBackendChain:
    """RAISES if settings.video_backend_chain[-1] is not a guaranteed-available backend."""

# video/filtergraph.py
CINEMATIC_GRADE = "vignette=PI/5,noise=alls=7:allf=t,eq=contrast=1.06:saturation=0.94"

def clamp(value: float, low: float, high: float) -> float: ...
def build_zoompan_expr(*, move: str, intensity: float, duration_s: float, fps: int) -> str | None: ...
def build_colour_grade(palette: list[str], colour_temp_k: int, contrast: str) -> str: ...
def build_shot_filter(shot: Shot, *, input_label: str, output_label: str, fps: int,
                      width: int, height: int) -> str: ...
```

## Rules that bind this story

- **Rule W2** — never assume `ffmpeg` is on PATH. Resolution order is
  `$CWT_FFMPEG_BIN` → `imageio_ffmpeg.get_ffmpeg_exe()` → `shutil.which`. `imageio-ffmpeg` pip-installs
  a real binary; that is why it is in `requirements.txt`.
- **Rule W3** — if `ffprobe` is missing, **parse `ffmpeg -i` stderr**. Never skip verification. A
  truncated render would otherwise pass QA and ship a 4-second ad. `probe_duration_from_stderr` uses
  `r"Duration:\s*(\d+):(\d+):(\d+\.\d+)"`.
- **Rule V4** — `build_chain()` **raises at startup** if the last element is not a backend available
  in every environment. This single invariant is what makes *"the pipeline cannot hard-fail on video"*
  true **by construction rather than by hope**. `local_ffmpeg` and `fixture` qualify.
- **Rule V2** — never build a filtergraph by concatenating unvalidated values. A stray quote from the
  model silently changes the entire graph and the failure appears as a *rendering artefact*, not an
  error. Every interpolated value is type-checked by pydantic and `clamp()`ed first.
- **Rule V1 / W5** — every returned fragment operates on a **labelled** input and writes a **labelled**
  output, and paths inside fragments are **relative with forward slashes**. Never a drive letter,
  never a backslash. ffmpeg's filter parser reads the colon in `C:\...` as an option separator.

## Build steps

1. `ffmpeg_bin.py` — resolution chain, `ffmpeg_version()`, and `probe()`.
   `probe()` tries ffprobe first; on `ToolNotFound` falls back to stderr parsing **and sets a
   `warnings` entry** so `doctor` can report degraded verification. Loudness comes from
   `ffmpeg -i ... -af loudnorm=print_format=json -f null -` stderr JSON; return `None` on failure —
   never fabricate a LUFS value.
2. `backend.py` — `Availability`, the `VideoBackend` Protocol, `RenderResult`, `VideoBackendChain`.
   `build_chain` imports the four backends lazily (S15/S18) and raises if
   `chain[-1].name not in ("local_ffmpeg","fixture")`.
3. `filtergraph.py` — copy spec lines 3152–3233 **verbatim**:
   - `CINEMATIC_GRADE` (line 3158) — *"ten lines of filtergraph is the entire difference between 'an AI
     slideshow' and 'a movie trailer'"*
   - `build_zoompan_expr` (3165–3185): `static` and `whip_pan` return `None`; `push_in` uses
     `min(zoom+rate,1.35)`; `pull_out` uses `if(lte(zoom,1.0),1.35,max(1.001,zoom-rate))`
   - `build_colour_grade` (3188–3206): `temp_shift = clamp((k-6500)/6500, -1, 1)`, `extreme` → `1.18/0.88`,
     `high` → `1.10/0.92`
   - `build_shot_filter` (3209–3233): scale → crop → zoompan → whip-pan x-sweep → grade → `CINEMATIC_GRADE`
4. **Golden tests.** Freeze the exact strings:
   - `build_zoompan_expr(move="push_in", intensity=0.6, duration_s=4.0, fps=30)` →
     `"min(zoom+0.001500,1.35)"`
   - `build_zoompan_expr(move="static", ...)` → `None`
   - `build_shot_filter(...)` for a §3.4 `s01` and `s02` shot → byte-exact expected string
   - `build_colour_grade(["#050505","#1a1a1a","#ef4444","#22d3ee"], 6500, "extreme")` →
     `"eq=contrast=1.18:saturation=0.88"` (no `colorbalance` at exactly 6500K)
   - `build_colour_grade(..., 7000, "high")` → `colorbalance` **plus** `eq=contrast=1.10:saturation=0.92`
   - every fragment contains no `\` and no `:` outside a filter argument
5. Assert `CINEMATIC_GRADE` ends the chain in every `build_shot_filter` output.

## Decisions the spec leaves open

- **`MathDomainError` risk in `build_colour_grade`.** `temp_shift` is not clamped by the spec's own
  code path before the `-temp_shift * 0.08` interpolation. It *is* clamped at line 3198 — keep that.
- **`whip_pan`'s crop x-expression** (spec line 3226) mixes `t` with a computed sweep fraction. Copy
  it literally; do not simplify. It is golden-tested and a "cleanup" changes the render.
- **`probe()` return on a 0-byte file.** Return `MediaInfo` with `duration_s=0.0`, `width=0`,
  `height=0` — do **not** raise. S15's Rule V3 assertion is what turns those zeros into a failure, and
  it needs to see them.
- **`fixture` backend.** `doctor` (spec line 4267) accepts `fixture` as a valid chain tail but no such
  backend is ever specified. **Do not build one** — treat its appearance in the doctor check as a
  forward-compatibility allowance.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_filtergraph.py -q -v     # green, no ffmpeg needed

.venv/Scripts/python -c "
from cwt.video.ffmpeg_bin import ffmpeg_path, ffmpeg_version
print(ffmpeg_path(), ffmpeg_version())

from cwt.config import Settings
from cwt.video.backend import build_chain
s = Settings.from_env()
print([b.name for b in build_chain(s)])
try:
    build_chain(s.with_backend_chain(['hyperframes','openmontage']))
    print('FAIL — Rule V4 not enforced')
except Exception as e: print('V4 ok:', str(e)[:70])"
```

## Handoff

S15 uses `build_shot_filter`, `ffmpeg_path`, `probe` and `RenderResult`. S18 implements the two optional
backends against the `VideoBackend` Protocol. **`build_shot_filter` is golden-tested — any change to it
requires updating the golden strings in the same commit, and that diff should be reviewed as a visual
change to the ad, not a refactor.**
