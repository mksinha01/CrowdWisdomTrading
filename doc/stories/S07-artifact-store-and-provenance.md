# S07 — Artifact store & provenance

**Phase** 1 · **Depends on** S04 · **Blocks** S19–S26 (every tool writes through it)
**Spec** `doc/video-ads-agent.md` lines **374–405** (§3.0 conventions), **4020–4029** (§9.6 resume layers), **787–882** (§3.5)
**Context budget** ~12k (spec 3.5k + story 1.5k + output 6.5k)
**Produces** `domain/artifacts.py`, `tests/test_artifacts.py`

---

## Goal

One class through which every artifact is read and written. It owns three things nothing else should:
the `schema_version` gate, the input-hash provenance log, and the `get_if_valid()` check that makes
`--resume` skip work instead of re-spending API credits.

§9.6 names three idempotent resume layers. This story implements the **stage** layer:
*"A stage whose inputs are unchanged returns `skipped` instantly."*

## Interface contract — FROZEN

```python
# domain/artifacts.py

ARTIFACT_NAMES: tuple[str, ...] = (
    "winning_ads", "ad_patterns", "research_brief", "storyboard",
    "hook_candidates", "review_verdict", "claims_report", "render_manifest",
)

# name -> model class. The single registry; nothing else maps names to models.
ARTIFACT_MODELS: dict[str, type[ArtifactBase]] = {...}

class ArtifactError(RuntimeError): ...

@dataclass(frozen=True)
class Provenance:
    name: str; sha256: str; inputs: dict[str, str]; written_at: str; run_id: str

class ArtifactStore:
    def __init__(self, paths: RunPaths, *, run_id: str): ...

    # ── write ──
    def write(self, name: str, model: ArtifactBase, *, inputs: dict[str, Path] | None = None) -> Path:
        """Validate, write utf-8/LF via jsonio, then append a Provenance record."""

    # ── read ──
    def read(self, name: str) -> ArtifactBase: ...
    def path_for(self, name: str) -> Path: ...          # artifacts/<name>.json

    # ── the resume mechanism ──
    def input_hashes(self, inputs: dict[str, Path]) -> dict[str, str]: ...
    def get_if_valid(self, name: str, *, inputs: dict[str, Path] | None = None
                     ) -> ArtifactBase | None:
        """Return the artifact iff it exists, parses, and every input hash matches
        provenance. Otherwise None. NEVER raises for a missing/corrupt artifact."""

    def sha256_of(self, path: Path) -> str: ...

def sha256_file(path: Path) -> str: ...
```

## Rules that bind this story

- **§3.0 rule 1** — `schema_version` is mandatory and an integer. A reader seeing a **higher** version
  than it knows raises `ArtifactVersionError` and refuses to parse. Never silently misinterpret.
- **§3.0 rule 2** — evolution is append-only. `get_if_valid` must return `None` (not raise) for an
  artifact written by an older schema, so `--resume` regenerates it rather than crashing.
- **§3.0 rule 3** — every write goes through `util/jsonio.write_json`. No `Path.write_text` anywhere.
- **§3.0 rule 4** — artifacts are run-scoped and world-readable on disk, and **API tokens never enter
  an artifact**. `write()` must scan the serialised payload for `?token=` / `apify_api_` / `sk-or-v1-`
  / `nvapi-` / `tvly-` and **raise** if found. This is a hard gate, not a warning.
- **Rule R1** — the submission includes the repo publicly. The scan above is the belt; S10's `_scrub`
  is the braces.

## Build steps

1. Constants and `ARTIFACT_MODELS` registry. Import every model from `models.py` (S03).
2. `sha256_file` — read in 64 KiB chunks, return lowercase hex.
3. `write()`:
   a. `model.model_validate(model.model_dump())` — a cheap self-check that the object is coherent.
   b. Serialise; **run the secret scan**; raise `ArtifactError` naming the offending artifact and the
      matched prefix (never the value).
   c. `write_json(self.path_for(name), payload)`.
   d. Build a `Provenance` with `{label: sha256_file(p) for label, p in inputs.items()}` and append
      it to `artifacts/provenance.json` (a JSON list, appended atomically: read → append → write).
4. `get_if_valid()`:
   - missing file → `None`
   - `ArtifactVersionError` → `None` (older/newer schema; regenerate)
   - any other `ValidationError` → `None`
   - `inputs` given → compare each hash to the latest provenance record for `name`; any mismatch →
     `None`
   - else return the model
5. `read()` — strict. Raises on missing or invalid. Used by stages that have already passed
   `get_if_valid`.
6. Tests:
   - round-trip all 8 artifact names using §3 fixtures
   - `get_if_valid` returns `None` when an input file's content changes (rewrite the input, re-check)
   - `get_if_valid` returns the artifact when nothing changed
   - **the secret scan fires**: write a `winning_ads` payload with an `ad_library_url` of
     `https://api.apify.com/v2/acts/x/runs?token=apify_api_secret` → must raise
   - a `schema_version: 99` artifact on disk → `get_if_valid` returns `None`, `read()` raises

## Decisions the spec leaves open

- **`provenance.json` is a list, not an object.** §3.0 rule 5 says "appends… the sha256 of every input",
  implying history. Keep the full append-only history — it is what makes a stage's skip decision
  auditable. Read-modify-write is fine: runs are single-writer.
- **Concurrency.** Stages run in parallel (three research cards). Two writers to `provenance.json` can
  interleave. Guard with a module-level `threading.Lock` **and** accept that cross-process writes are
  last-wins. Note it in a comment; do not build a file lock.
- **The secret-scan patterns** are not enumerated in the spec. Use the five prefixes above plus a
  generic `[?&]token=`. Add `scripts/scrub_fixtures.py` (S34) as the offline counterpart.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_artifacts.py -q      # green

.venv/Scripts/python -c "
from cwt.util.paths import RunPaths; from cwt.domain.artifacts import ArtifactStore
from pathlib import Path; import tempfile, json
d = Path(tempfile.mkdtemp())/'r'; paths = RunPaths(d).ensure()
s = ArtifactStore(paths, run_id='20260926-1402-a7f3')
print(s.get_if_valid('storyboard'))    # None — not written yet
# write §3 fixture, then:
#   get_if_valid -> model   (inputs unchanged)
#   touch/rewrite an input  -> None
"
```

## Handoff

Every tool in Phase 5 writes through `ArtifactStore.write()` and reads through
`get_if_valid()`. `cwt_verify_artifact` (S26) is a thin wrapper over `read()` + a schema name. The
`--force-stage` flag (S32) is implemented as *ignore* `get_if_valid`, so nothing about the store
changes when it is used. **A tool must never construct an artifact path by hand.**
