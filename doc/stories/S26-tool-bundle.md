# S26 — Tool surface — bundle

**Phase** 5 · **Depends on** S24, S25 · **Blocks** S32 (the `collect` card)
**Spec** `doc/video-ads-agent.md` lines **5444–5451** (the submission list), **3540–3551** (the `collect` card), **3385–3401** (root acceptance criteria), **800–808**, **5099–5100**
**Context budget** ~12k (spec 2.5k + story 1.6k + output 6.5k)
**Produces** `tools/bundle.py`, `tests/test_tools_bundle.py`

---

## Goal

Two tools that close the loop: one that proves an artifact is valid, and one that assembles the
submission bundle the brief actually asks for.

The brief's deliverables are specific (spec lines 50–52): a repo link, the Apify and Tavily tokens, and
**a video output of the hermes kanban**. `cwt_assemble_submission` produces the directory that makes
those three things easy to attach to an email.

## Interface contract — FROZEN

```python
# tools/bundle.py

def verify_artifact(*, settings: Settings, paths: RunPaths, name: str) -> dict:
    """Schema-validate one artifact. Returns {"ok": true} on success (spec line 4080).
    WRITES nothing
    RETURNS {"ok":bool,"name":str,"schema_version":int,"path":str,"error":str|None}"""

def assemble_submission(*, settings: Settings, paths: RunPaths) -> dict:
    """Build submission/.
    WRITES submission/** (see the file list below)
    RETURNS {"dir":str,"files":[...],"total_bytes":int,"missing":[...],"complete":bool}"""

# ── internals ──
def _cost_report(paths: RunPaths) -> dict:
    """Aggregate llm_ledger.jsonl into per-stage and per-service spend."""
def _readme_submission(*, settings, paths, cost, manifest) -> str: ...
```

## The bundle — exactly these files

| File | Source | Required by |
|---|---|---|
| `final.mp4` | `render/final.mp4` | brief: "a video output" |
| `storyboard.json` | `artifacts/storyboard.json` | brief: "saved and shared in json human readable format" |
| `storyboard.html` | `artifacts/storyboard.html` | §11.3 — the human-readable view |
| `contact_sheet.png` | `artifacts/contact_sheet.png` | §18 step 15 |
| `render_manifest.json` | `artifacts/render_manifest.json` | proves the ffmpeg argv and the backend chain |
| `claims_report.json` | `artifacts/claims_report*.json` | evidence the compliance gate ran and passed |
| `cost_report.json` | derived from `llm_ledger.jsonl` | §18 step 15 |
| `README-SUBMISSION.md` | generated | §18 step 15: repo link, the two API tokens, the recording recipe |

## Rules that bind this story

- **Rule R1 — the bundle is emailed and the repo is public.** `README-SUBMISSION.md` contains **the
  Apify and Tavily tokens** (the brief demands them, so the reviewer can rerun the code). It must
  **not** contain the OpenRouter/NVIDIA key, the Exa key, or any other credential. Hard-code that
  allowlist of two token names and raise if any other secret appears.
- **§13 line 5099** — the `--offline` run must work before a live one. `assemble_submission` records
  which mode produced the bundle (`offline: true|false`) and the exact command, so the reviewer can
  reproduce it.
- **Rule A1 / §5** — `verify_artifact` is the tool the *agents* call to self-check. It must return a
  JSON-serialisable dict and must not raise on an invalid artifact — it returns `{"ok": false, "error": ...}`.
  A `verify_artifact` that raises is a tool that kills the agent turn.
- **§3.0 rule 4** — no token reaches an artifact. `_cost_report` reads `llm_ledger.jsonl`, which
  contains stage/model/cost rows — safe. Do not add request URLs to it.

## Build steps

1. `verify_artifact` — `ARTIFACT_MODELS[name]` (S07), read the file, `model_validate`. Return
   `{"ok": True, "name", "schema_version", "path"}` or `{"ok": False, "error": str(exc)[:400]}`.
   An unknown `name` returns `ok: False` with the list of valid names — **never raises**.
2. `_cost_report` — read `llm_ledger.jsonl` (one JSON object per line, written by S09's `_record`).
   Aggregate:
   - `by_stage`: sum `cost_usd` grouped by `stage`
   - `by_tier`: `cheap` vs `strong` totals and call counts
   - `by_model`: totals per model slug
   - `total_usd`, `calls`, `run_id`
   Plus the **external services** block, which the ledger does not cover:
   - `apify_usd` from `winning_ads.source.actual_charge_usd`
   - `tavily_credits` = `2 * advanced_searches` (spec line 252: advanced costs 2 credits)
   - `exa_searches` = count of cached Exa fixtures
   Mark free-tier consumption as `"free_tier": true` and `"usd": 0.00` — §16.1 is explicit that
   Tavily and Exa free tiers mean $0.00, and claiming otherwise misreports the run.
   Cross-check the total against §16.2's worked example (~$0.24–0.70). A total outside that band means
   the ledger is wrong, and the report says so in a `warning`.
3. `_readme_submission` — a Markdown file with, in this order:
   - the repo link
   - **the two tokens** (Apify, Tavily), read from `settings`, with a one-line note that Apify's free
     tier is $5/month and expires
   - the exact command that produced this bundle, and whether it was offline
   - the recording recipe pointer (`scripts/record_kanban_video.ps1`)
   - the five-step "how to rerun this in one command" block:
     `./scripts/bootstrap.ps1` → fill `.env` → `hermes model` → `cwt bootstrap` →
     `cwt run --engine local --offline`
   - a line stating that the offline run needs **no keys at all** and renders the bundled fixture ad
4. `assemble_submission`:
   - create `submission/`, copy each file in the table
   - **regenerate `storyboard.html`** by calling S23's tool if `artifacts/storyboard.json` is newer than
     `storyboard.html`. The compliance card overwrites the storyboard in place (S24), so a cached HTML
     can describe a script that no longer exists. This is the most likely quiet bug in the bundle.
   - collect `missing[]` for any source absent, and set `complete: bool(missing == [])`
   - **do not fail on a missing optional file.** `contact_sheet.png` and `cost_report.json` are
     nice-to-haves; `final.mp4` and `storyboard.json` are not. Report which is which.
5. Docstrings — `assemble_submission`:
   > *CALL THIS: last, on the `collect` card, after QA passed. It is the final step of the run.*
   >
   > *WHEN NOT TO CALL: never before QA. A bundle containing a video that failed the loudness or
   > disclosure check is worse than no bundle — it looks finished.*
6. Tests:
   - `verify_artifact` on a valid fixture → `{"ok": True}`; on a corrupted one → `{"ok": False}` with
     an error, **no raise**; on an unknown name → `ok: False` listing valid names
   - `_cost_report` sums a synthetic 10-line ledger correctly
   - **the token allowlist**: a `README-SUBMISSION.md` containing `OPENROUTER_API_KEY` or `EXA_API_KEY`
     raises
   - a stale `storyboard.html` is regenerated (touch the JSON newer, assert the mtime advances)
   - `complete` is `False` when `final.mp4` is absent, and the run does not raise

## Decisions the spec leaves open

- **`contact_sheet.png` is generated here if absent** by calling S23's `make_contact_sheet`, rather
  than being reported missing. It is cheap, offline (Pillow only), and its absence from the bundle is
  a visible gap in the email.
- **Tokens in `README-SUBMISSION.md` are intentional and required** by the brief. This is the one
  place credentials are written to disk — and it is a file the operator emails, not one that ships in
  the repo. Add `submission/` to `.gitignore` so the token file cannot be committed by accident. This
  is the single most important line in this story.
- **Cost band check.** §16.2's ≈$0.24–0.70 is the reference. A run far outside it is not necessarily
  wrong (a failed-and-retried run costs more), so warn rather than fail.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_tools_bundle.py -q -v      # green

.venv/Scripts/python -c "
import json, pathlib
from cwt.config import Settings
from cwt.util.paths import RunPaths
from cwt import tools
p = RunPaths(pathlib.Path('runs/_s26')).ensure()
print(tools.bundle.verify_artifact(settings=Settings.from_env(), paths=p, name='storyboard'))
print(tools.bundle.verify_artifact(settings=Settings.from_env(), paths=p, name='nope'))
r = tools.bundle.assemble_submission(settings=Settings.from_env(), paths=p)
print(r['complete'], r['missing'])
for f in r['files']: print(' ', f)"

grep -n 'submission/' .gitignore     # must be present — the token file must never be committed
```

## Handoff

S32's `run_pipeline` calls `assemble_submission` as the final stage and returns its `dir` in the
summary. S36 writes the top-level `README.md`, whose quickstart mirrors the `README-SUBMISSION.md`
rerun block — **keep the two in sync**, and make the README the longer document.
