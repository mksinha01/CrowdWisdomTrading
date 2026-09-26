# S17 — Asset sourcer & fixture assets

**Phase** 4 · **Depends on** S08 · **Blocks** S25, S35 (offline proof)
**Spec** `doc/video-ads-agent.md` lines **167–169** (file tree), **876–878** (`render_manifest.assets`), **5099–5100** (§13), **5248–5257** (§16.3)
**Context budget** ~14k (spec 2.5k + story 1.6k + output 9k) — **G10: fixture set is not enumerated**
**Produces** `video/assets.py`, `fixtures/assets/**` (12 stills, 3 clips, 1 music bed, 1 piper voice), `tests/test_assets.py`

---

## Goal

Resolve `Shot.asset` into a real file on disk, with a licence record, and — critically — make
`cwt run --engine local --offline` render a **real, watchable 42-second video with no API keys at
all**. That offline run is not a nicety: it is the submission's contract with the reviewer
(spec line 5456).

> **G10.** The file tree says *"12 CC0 stills, 3 CC0 clips, 1 CC0 music bed, 1 Piper voice"* and
> enumerates none of them. This story defines the manifest of assets and how each is obtained.

## Interface contract — FROZEN

```python
# video/assets.py

ASSET_LICENCES = ("CC0-1.0", "MIT", "internal")

@dataclass(frozen=True)
class AssetRecord:
    ref: str; path: Path; license: str; source_url: str
    attribution_required: bool; kind: str; sha256: str

class AssetSourcer:
    def __init__(self, *, paths: RunPaths, settings: Settings, cache: HttpCache): ...

    def resolve(self, asset: AssetRef, shot: Shot) -> AssetRecord:
        """AssetRef -> a file on disk. Deterministic and idempotent."""

    def generate(self, shot: Shot, out_path: Path) -> AssetRecord:
        """kind == 'generated_chart': draw the shot's described image with Pillow."""

    def manifest(self) -> list[dict]:
        """The render_manifest.assets[] block. One entry per distinct ref used."""
```

Three `AssetRef.kind` values, three strategies:

| `kind` | Strategy | Network |
|---|---|---|
| `internal` | `fixtures/assets/stills/<ref>.png` — bundled | none |
| `cc0` | bundled if present, else fetch-from-allowlist into `paths.cache`, content-hash keyed | first time only |
| `generated_chart` | drawn locally by Pillow from the shot description + palette | none |

## Rules that bind this story

- **§5.4 / §13** — `cwt run --engine local --offline` must produce a real video with **zero API spend
  and no network**. Every asset the fixture storyboard references must therefore be **bundled**, not
  fetched. This is the binding constraint on the whole story.
- **Rule R1** — the allowlist below is a licence control, not a convenience. Never source an asset from
  outside it, and never record an asset without a `license` and a `source_url`.
- **§3.5 line 877** — every asset entry in `render_manifest` carries `ref, path, license, source_url,
  attribution_required`. `attribution_required` must be accurate, not defaulted to `false`.
- **Rule W9** — `paths.cache` holds content-hash-keyed files. Keep the layout shallow; use
  `long_path()` at the `mkdir` site.

## Build steps

1. **Define the fixture manifest** (the G10 deliverable). Write `fixtures/assets/MANIFEST.json` with
   one record per asset. The 12 stills map to the six `SUBJECTS` (two each), sized **exactly**
   `1080×1920`:

   | ref | subject | kind |
   |---|---|---|
   | `candle_proliferation`, `single_cyan_line`, `red_candle_wall` | `abstract_market_data` | `generated_chart` |
   | `trader_silhouette_dusk`, `trader_at_desk` | `trader_silhouette` | `generated_chart` |
   | `chart_zoom_clean`, `chart_grid_dense` | `chart_detail` | `generated_chart` |
   | `city_night_aerial`, `city_canary_wharf` | `city_night` | `generated_chart` |
   | `screen_glow_blue`, `screen_glow_amber` | `screen_glow` | `generated_chart` |
   | `type_card_black`, `type_card_cyan` | `typography_card` | `generated_chart` |
   | `tension_bed_90bpm` | — | `cc0` music |
   | `synth_note_clean`, `riser_cut_to_silence`, `impact_low` | — | `cc0` SFX |

   Generating the stills with Pillow rather than downloading them is the decision that makes the
   offline run work in a sandbox with no network **and** removes every licence question. Record it.
2. `generate()` — draw from the brand palette (`#050505` background, `#22d3ee` primary accent,
   `#fb923c`/`#fbbf24`/`#818cf8`/`#fb7185` secondaries). Each subject has its own drawing routine:
   - `abstract_market_data` — procedural candlesticks with a seeded RNG from `shot.id` so the render
     is **deterministic and reproducible** across runs
   - `typography_card` — `shot.on_screen_text` rendered large, using a bundled font
   - `chart_detail` / `city_night` / `screen_glow` / `trader_silhouette` — gradients, grids, glow,
     and silhouette shapes respectively
   Seed everything from `hash(shot.id)` so two runs of the same storyboard produce byte-identical PNGs.
3. `resolve()` — dispatch on `ref` and `kind`. `internal`/`cc0` bundled → return the `AssetRecord`
   immediately. `cc0` missing → fetch through `HttpCache` from the allowlist:
   `cdn.pixabay.com`, `freesound.org`, `upload.wikimedia.org`, `opengameart.org`. **Any other host
   raises** — this is a licence control.
4. Content-hash cache write: `<sha256>.<ext>` under `paths.cache`, plus a sidecar `<sha256>.license.json`
   carrying `license`/`source_url`/`attribution_required` so the manifest can be rebuilt without the
   network on a later run.
5. Music + SFX: generate them too. A 90-second tension bed and three short SFX are synthesizable with
   `ffmpeg`'s `sine`/`anoisesrc` filters — no download, no licence. Write a `_synth_audio()` helper
   using `run_tool`. **A silence fallback is acceptable** if synthesis fails; record it in warnings.
6. Piper voice: `fixtures/assets/voices/en_US-ryan-high.onnx` is a ~60 MB binary. **Do not commit it.**
   Add a `scripts/fetch_piper_voice.py` (S34) and make `piper` simply report `available=False` when
   the file is absent — the TTS chain (S12) falls through to `silent`, which is a supported path.
7. Tests: `resolve()` is idempotent; two calls with the same seed produce identical SHA-256; a
   non-allowlisted host raises; every manifest entry has a non-empty `license` and `source_url`;
   `generate()` output is exactly `1080×1920`.

## Decisions the spec leaves open

- **Generate rather than download.** The spec's `/env` block sets `HYPERFRAMES_ENABLED=auto` and
  bundles fixtures, but never says where assets come from. Generating them is the only choice that
  satisfies both "no network offline" and "no licence risk". Recorded as the story's primary decision.
- **`trader_silhouette` is a rendered shape, not a photograph.** A real photo of a person would need a
  model release for a commercial ad. Abstract silhouettes avoid the issue entirely — and the brand is
  a dark-terminal aesthetic anyway, so it costs nothing creatively.
- **Deterministic RNG.** `random.Random(int(hashlib.sha256(shot.id.encode()).hexdigest()[:8], 16))`.
  Never `random.seed()` globally — three renders run concurrently.
- **SFX naming** must match the `sfx[].ref` values used in `storyboard.json` (spec line 684:
  `synth_note_clean`, `riser_cut_to_silence`). S22 must only emit refs that exist here; add a test
  asserting the fixture storyboard's refs all resolve.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_assets.py -q -v      # green

.venv/Scripts/python -c "
from cwt.video.assets import AssetSourcer
from cwt.util.paths import RunPaths
from cwt.config import Settings
from cwt.clients.http_cache import HttpCache
import pathlib
p = RunPaths(pathlib.Path('runs/_s17')).ensure()
s = AssetSourcer(paths=p, settings=Settings.from_env(), cache=HttpCache(p.cache, offline=True))
recs = s.manifest()
print(len(recs), 'assets')
assert all(r['license'] and r['source_url'] for r in recs)
print('sizes:', {r['ref']: (r['path'].stat().st_size) for r in recs[:3]})"

# offline determinism
.venv/Scripts/python -c "
# generate twice with the same shot.id -> identical sha256"
```

## Handoff

S25 passes an `AssetSourcer` into the render and puts `manifest()` into `render_manifest.assets[]`.
S22 must only emit `asset.ref` and `sfx[].ref` values that exist in this manifest — **a storyboard
referencing an unknown asset is a render failure, so add a validator or a check in S22**. S35's offline
proof depends entirely on this story having bundled everything.
