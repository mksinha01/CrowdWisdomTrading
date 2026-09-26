# S18 — Optional backends — HyperFrames & OpenMontage

**Phase** 4 · **Depends on** S14 · **Blocks** S25 (chain assembly only)
**Spec** `doc/video-ads-agent.md` lines **284–291** (`.env` blocks), **866–870** (`backend_chain_tried`), **4971–4983** (Rule P1), **4789–4802** (Rule V4)
**Context budget** ~13k (spec 2k + story 1.6k + output 8k)
**Produces** `video/hyperframes.py`, `video/openmontage.py`, `tests/test_optional_backends.py`

---

## Goal

Two **optional** backends that sit in front of `local_ffmpeg` in the chain. Their entire job is to be
tried, to fail gracefully when their prerequisites are absent, and to be recorded honestly in
`render_manifest.backend_chain_tried`.

> The spec is blunt about this (line 290): *"This backend may simply not work."* That is an acceptable
> outcome. What is **not** acceptable is a backend that raises, blocks, or reports success it did not
> achieve — because `local_ffmpeg` is behind it and will produce a real ad.

## Interface contract — FROZEN

```python
# video/hyperframes.py
class HyperFramesBackend:
    name = "hyperframes"
    def available(self) -> Availability:
        """Requires node >= 22, npx on PATH, and a resolvable chrome.
        HYPERFRAMES_ENABLED=0 -> unavailable. 'auto' -> probe. '1' -> require."""
    def render(self, storyboard: Storyboard, *, paths: RunPaths,
               voiceover: VoiceoverResult) -> RenderResult: ...
    def to_composition(self, storyboard: Storyboard) -> str:
        """Storyboard -> the HTML/CSS composition HyperFrames lints and renders."""

# video/openmontage.py
class OpenMontageBackend:
    name = "openmontage"
    def available(self) -> Availability:
        """Requires OPENMONTAGE_HOME set and the path to exist. Almost always False."""
    def render(self, storyboard: Storyboard, *, paths: RunPaths,
               voiceover: VoiceoverResult) -> RenderResult: ...
```

## Rules that bind this story

- **Rule V4** — neither backend may be the chain's last element. `build_chain()` (S14) enforces this;
  do not work around it.
- **Rule P1 — port 9119 belongs to the Kanban dashboard.** A video backend that starts a preview
  server on a fixed port will collide with the dashboard, which is *the thing being recorded*. Any
  preview server binds port **0** and the actual port is read from its stdout. `8000` is also reserved.
- **Rule W4** — `npx` on Windows is a `.cmd` shim. Passing the bare name raises
  `[WinError 193] %1 is not a valid Win32 application`. Go through `run_tool()`, which resolves via
  `shutil.which()`.
- **Rule W1/W7** — `run_tool()` only, list args, utf-8.
- **AGPL-3.0 (OpenMontage)** — we invoke it as an **external process** and consume only its output
  file. We **never vendor its source**. The `NOTICE` file (S36) must state this reasoning.
- **Neither backend may raise.** On any failure return `RenderResult(ok=False, error=...)`. The chain
  reads `ok` and moves on.

## Build steps

1. `hyperframes.py`:
   - `available()` — `shutil.which("node")` → parse `node --version` → require `>= 22`. Then
     `shutil.which("npx")`. Then attempt `npx hyperframes --version` with a 30s timeout. Each failure
     returns a specific `reason`: `"node not found"`, `"node v20.11.0 < required v22"`,
     `"npx hyperframes not installed"`.
   - `to_composition()` — emit a self-contained HTML file with the brand palette, each `Shot` as a
     timed `<section>`, camera moves as CSS transforms driven by `shot.camera`, and the VO as a
     `<track>`. This is real work and it is the only creative upside of the backend: CSS-authored
     cinematic compositions look different from ffmpeg stills.
   - `render()` — write the composition into `paths.render`, then
     `run_tool(["npx", "hyperframes", "render", "composition.html", "-o", "hf.mp4"], cwd=paths.render, timeout_s=900)`.
     Validate the output with `probe()` before returning `ok=True` (Rule V3 applies here too).
   - **Every failure path returns `ok=False` with the stderr tail in `error`.** HyperFrames lint
     failures (e.g. *"clip s07 has no data-start"*, spec line 867) are expected and normal.
2. `openmontage.py`:
   - `available()` — `OPENMONTAGE_HOME` set and `Path(home).exists()`. Usually `Availability(False,
     "OPENMONTAGE_HOME not set (optional; not required)")` — that exact reason string appears in the
     spec's own manifest example (line 868). Use it verbatim.
   - `render()` — invoke the external tool per whatever CLI it exposes, consume only the output file.
     **Read `$OPENMONTAGE_HOME/README*` at render time** to discover the invocation rather than
     hardcoding a guess; if no recognisable entrypoint is found, return `ok=False` with
     `"could not determine an invocation; see NOTICE for the AGPL boundary"`. Add a
     `.gitignore` entry for the vendored-checkout path.
3. Wire both into `build_chain()` (S14) in `VIDEO_BACKEND_CHAIN` order, with `local_ffmpeg` last.
4. Tests — all offline, all mocked:
   - `available()` returns a reason string, never raises, when node/npx/home are absent
   - `render()` returns `ok=False` (not raises) when the underlying tool exits non-zero
   - `render()` returns `ok=False` when the tool exits 0 but produces a 0-byte file (Rule V3)
   - `to_composition()` on the §3.4 fixture produces HTML containing all 12 shot ids and no absolute paths
   - **port guard**: assert the module contains no literal `9119` or `8000`

## Decisions the spec leaves open

- **`HYPERFRAMES_ENABLED=auto` semantics.** `auto` = probe and use if available; `0` = never; `1` =
  require (and then a failure is a real failure). Only `auto` and `0` are safe for a demo — document
  that `1` makes the offline quickstart non-deterministic.
- **`npx` cold start.** The first `npx hyperframes` call may spend 30–60s downloading the package. Do
  the `--version` probe in `doctor` (S33), not inside `render()`, so the cost is paid once and surfaced
  before the run rather than mid-render.
- **`leronx.org` is not in the chain.** Spec line 4801: *"leronx.org is not in the chain at all because
  it has no API."* Do not add it, and do not add a stub.
- **Both backends are best-effort by design.** Their value is the `backend_chain_tried` record proving
  the chain was real. Never make the pipeline depend on either succeeding.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_optional_backends.py -q -v      # green, offline

.venv/Scripts/python -c "
from cwt.video.hyperframes import HyperFramesBackend
from cwt.video.openmontage import OpenMontageBackend
for b in (HyperFramesBackend(), OpenMontageBackend()):
    a = b.available()
    print(b.name, a.available, '|', a.reason)      # never raises

from cwt.config import Settings
from cwt.video.backend import build_chain
print([b.name for b in build_chain(Settings.from_env())])
# -> ['hyperframes', 'openmontage', 'local_ffmpeg']"

grep -nE '\b(9119|8000)\b' src/cwt/video/hyperframes.py src/cwt/video/openmontage.py
# -> no matches (Rule P1)
```

## Handoff

S25 renders through the whole chain and writes `backend_chain_tried` from the per-backend
`RenderResult`s. S33's `doctor` reports each backend's `Availability.reason` in the
`backend chain` check so the operator sees, before spending anything, exactly which backends will be
skipped. **Neither backend is required for any other story.**
