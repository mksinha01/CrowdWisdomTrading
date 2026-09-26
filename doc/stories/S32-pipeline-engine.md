# S32 — Pipeline engine  ⚠ LARGE · **DESIGN GAP**

**Phase** 7 · **Depends on** every story in Phases 0–6 · **Blocks** S33
**Spec** `doc/video-ads-agent.md` lines **3819–4030** (§9.3, §9.5, §9.6), plus §13 lines **5083–5097** (what a run prints)
**Context budget** ~22k (spec 4k + story 2.2k + output 13k)
**Produces** `src/cwt/engine.py`, `tests/test_engine.py`

---

## ⚠ G1 — the largest hole in the spec

`cli.py:3878` does `from cwt.engine import run_pipeline` and `cli.py:3890` calls:

```python
summary = await run_pipeline(settings=settings, paths=paths, engine=args.engine,
                             offline=args.offline, record_pacing=args.record_pacing,
                             force_stage=args.force_stage)
```

**`engine.py` does not exist anywhere in the 5,509-line spec.** There is no section describing it, no
listing in the §1 file tree, and no partial implementation. Yet it is the module that makes *"the
entire pipeline runs end to end from a single command"* true — the claim §9 opens with.

**The signature above is the only contract that exists.** Everything else in this story is derived.
Record the derivation in the module docstring so the inference is auditable.

## Goal

Wire the twelve stages into one runnable pipeline, in **two engines**:

- `engine="hermes"` — seed the kanban board, let the dispatcher drive the agents, wait for completion
- `engine="local"` — run every stage **in-process, sequentially**, calling the same `tools/*` functions
  directly. **No Hermes, no gateway, no profiles, no plugin.**

The `local` engine is not a lesser path. It is the **CI path** and the **offline quickstart**
(spec line 4047): `cwt run --engine local --offline` must render a real video on a bare machine with
no keys and no network. §13 line 5084 makes that the second thing a reviewer runs.

## Interface contract — FROZEN

```python
# src/cwt/engine.py

STAGE_ORDER: tuple[str, ...] = (
    "ads", "patterns", "res_pain", "res_unique", "res_crowd", "brief",
    "script", "compliance", "render", "qa", "collect",
)

@dataclass
class StageResult:
    key: str; status: str            # "ok" | "skipped" | "failed"
    artifact: Path | None; elapsed_s: float; error: str | None

async def run_pipeline(*, settings: Settings, paths: RunPaths, engine: str = "hermes",
                       offline: bool = False, record_pacing: bool = False,
                       force_stage: list[str] | None = None) -> dict:
    """Returns {"done": int, "skipped": int, "cost_usd": float, "output": str,
                "stages": [StageResult]}"""

# ── internals ──
async def _run_local(settings, paths, *, offline, record_pacing, force_stage) -> list[StageResult]: ...
async def _run_hermes(settings, paths, *, offline, record_pacing, force_stage) -> list[StageResult]: ...
def _build_client(settings, paths) -> LLMClient: ...
def _build_cache(paths, offline: bool) -> HttpCache: ...
def _stage_is_valid(key: str, paths: RunPaths, *, force: bool) -> bool: ...
```

## The stage table — what each stage calls

| # | key | calls | artifact produced | inputs |
|---|---|---|---|---|
| 1 | `ads` | `tools.ads.source_winning_ads` → `tools.ads.rank_winning_ads` | `winning_ads.json` | — |
| 2 | `patterns` | `tools.patterns.extract_ad_patterns` | `ad_patterns.json` | winning_ads |
| 3 | `res_pain` | `tools.research.research_angle(angle="pain")` | `angles/pain.json` | — |
| 4 | `res_unique` | `tools.research.research_angle(angle="unique_data")` | `angles/unique_data.json` | — |
| 5 | `res_crowd` | `tools.research.research_angle(angle="crowd_effect")` | `angles/crowd_effect.json` | — |
| 6 | `brief` | `tools.research.assemble_brief` | `research_brief.json` | 3 angle files |
| 7 | `script` | `generate_hook_candidates` → `write_storyboard_variant` × 3 → `judge_variants` → `score_storyboard` → `apply_rewrite` (≤3) | `storyboard.json` | brief, ad_patterns |
| 8 | `compliance` | `tools.claims.check_claims(pre_render)` → `rewrite_for_compliance` (≤3) | `claims_report_pre_render.json` | storyboard |
| 9 | `render` | `tools.video.synthesize_voiceover` → `render_video` | `render/final.mp4`, `render_manifest.json` | storyboard |
| 10 | `qa` | `tools.video.probe_media` + `qa_check` + `check_claims(post_render)` | `claims_report_post_render.json` | render outputs |
| 11 | `collect` | `tools.bundle.assemble_submission` | `submission/**` | everything |

`root` is a DAG anchor with no work — it completes immediately (spec line 3401). **It is not a stage.**

## Rules that bind this story

- **Rule A6** — `BudgetExceeded` propagates out of `run_pipeline` **uncaught**. `cli.py` maps it to
  exit 4. Do not catch it here to "finish gracefully" — a run that crosses its cap has already failed.
- **§9.6 — three idempotent layers.** This story implements and honours all three:
  1. **Card** — `--idempotency-key` (S28's `seed`)
  2. **Stage** — `ArtifactStore.get_if_valid(name, inputs=...)` (S07)
  3. **Tool** — `HttpCache` (S08)
  The practical consequence: *after fixing a filtergraph bug, re-running costs zero API calls and about
  90 seconds, because only the render stage's inputs changed.* **That promise is made by this file.**
- **`--offline` bypasses our LLM calls and all three paid APIs — it does NOT remove the Hermes worker's
  own model calls** (spec line 5254). `--engine local --offline` bypasses Hermes entirely and costs
  literally nothing. The two flags do different things; the banner must say so.
- **Rule A4 / §7.1** — `kanban.max_in_progress: 4` matches `LLM_MAX_CONCURRENCY`. In the `hermes`
  engine the three research stages run **concurrently** — that is the money shot for the recording
  (spec line 3387). In the `local` engine, **run them concurrently too** with `asyncio.gather`, or the
  offline run takes three times as long for no reason.
- **§9.5 line 4014** — the offline run prints `Done. 11 stages, $0.0000`. **This is 12 stages per S27's
  count.** Fix the prose; keep the format.
- **Rule C2** — when the compliance stage blocks, **the run blocks**. Do not continue to render.

## Build steps

1. `run_pipeline` — dispatch on `engine`, then common tail:
   - build `HttpCache(paths.cache, offline=offline)`
   - build `LLMClient` from settings, **unless `offline`** — in offline mode our LLM calls are removed
     entirely. If `offline` is set and a stage nonetheless needs an LLM, that stage must read its
     cached artifact instead. A missing fixture in offline mode is `OfflineFixtureMissing`, loud and
     actionable (S08).
   - `_build_client` must pass `ledger_path=paths.ledger`
   - sum `cost_usd` from `client.run_cost_usd`, which is `0.0` in offline mode
2. `_run_local` — for `key` in `STAGE_ORDER`:
   - `_stage_is_valid(key, paths, force=key in force_stage)` → if valid, append
     `StageResult(status="skipped")` and continue. **This is what makes `--resume` cost $0.00.**
   - run the stage; catch `ArtifactValidationError` / `ToolFailed` → `status="failed"`, then **stop**
     (a failed stage poisons everything downstream)
   - let `BudgetExceeded` and `OfflineFixtureMissing` propagate
   - `record_pacing` → `record_pacing_pause(key)` after each stage
   - **`res_pain`, `res_unique`, `res_crowd` run via `asyncio.gather`** and are reported as one
     contiguous block in `stages`
3. `_run_hermes` — `seed(run_id, paths.run_dir, settings.board)` (S28), then
   `await wait_for_completion(settings.board, timeout_s=settings.run_timeout_seconds,
   stall_threshold_s=settings.stall_threshold_seconds)`. Map `PipelineTimeout` and `PipelineBlocked`
   through uncaught — `cli.py` maps them to exits 2 and 3.
   - **before seeding**, assert the profiles exist; if not, raise with
     `"Run \`cwt bootstrap\` first — the nine CWT profiles are not installed."`
   - `--force-stage X` in this engine means **unblock and re-run card X**: call `kanban unblock` for it
     and delete its artifact so `get_if_valid` fails. Document that the semantics differ between engines.
   - collect `cost_usd` from `paths.ledger` after the run, since the workers' LLM calls happen in
     separate processes.
4. `_build_cache` — `HttpCache(paths.cache, offline=offline)`. In `--offline` mode the cache is
   **read-only** and a miss raises. Never silently fall back to live.
5. **The summary** — `{"done": len(ok), "skipped": n, "cost_usd": float, "output": str}`. `output` is
   `render/final.mp4` when it exists, else `submission/`. `cli.py` prints
   `Done. {done} stages, ${cost:.4f}, output: {output}`.
6. Tests:
   - a fully cached run returns every stage `skipped` and `cost_usd == 0.0`
   - `--force-stage render` re-runs exactly one stage
   - a stage failure stops the run and later stages are absent from `stages`
   - `BudgetExceeded` from a stage propagates out of `run_pipeline`
   - offline mode with a missing fixture raises `OfflineFixtureMissing`
   - the three research stages run concurrently (assert wall-clock < sum of individual durations)
   - `_run_hermes` raises the `cwt bootstrap` message when profiles are absent

## Decisions the spec leaves open — all of them, in one place

- **G1: the module itself.** Derive it from `cli.py`'s call site plus §9.5's output contract.
- **Stage list is 11, not 12.** `root` is a DAG anchor only. `STAGE_ORDER` has 11 entries; the DAG has
  12 cards. The banner says **"11 stages"**, which matches the spec's `Done. 11 stages` line — so the
  spec's "11 cards" prose was probably counting stages, not cards. **Note this reconciliation.**
- **`--force-stage` differs by engine.** `local` = re-run the stage; `hermes` = unblock + invalidate.
  The spec gives the flag but only the `local` semantics are implementable cleanly.
- **Offline + LLM.** The spec says offline removes our LLM calls. Implement as: `_build_client` is
  called, but every stage's LLM call site is guarded by `if offline: read the cached artifact`. The
  cleanest expression is to make each `tools/*` function accept the client and to pass a
  `NullLLMClient` that raises `OfflineFixtureMissing` on use — but that is invasive across six stories.
  **Chosen approach:** the fixture set includes the *artifacts* the stages produce, not just HTTP
  responses, so `_stage_is_valid` returns `True` for every stage and `--engine local --offline` is
  effectively a replay. Ship `fixtures/artifacts/<name>.json` alongside the HTTP fixtures. This is the
  only approach that makes "renders a real 42-second video with no API keys at all" literally true.
- **`cost_usd` in the hermes engine** comes from the ledger, not the client — the workers are separate
  processes and each writes to its own ledger. Aggregate `runs/<id>/llm_ledger.jsonl`.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_engine.py -q -v      # green

# THE GATE. This is the submission requirement (§13, §18 step 10).
.venv/Scripts/cwt run --engine local --offline
# -> === CWT Video Ads Agent ===
#    ... engine local / offline true / cost $0.0000
#    Done. 11 stages, $0.0000, output: runs/<id>/render/final.mp4

.venv/Scripts/python -c "
from cwt.video.ffmpeg_bin import probe; import pathlib, glob
p = sorted(glob.glob('runs/*/render/final.mp4'), key=lambda x: pathlib.Path(x).stat().st_mtime)[-1]
m = probe(p); print(m)
assert 30 <= m.duration_s <= 60 and m.width == 1080 and m.height == 1920, 'the ad is wrong'"

# and a clean-clone check, no keys in env:
env -i .venv/Scripts/python -m cwt run --engine local --offline     # must still render
```

## Handoff

S33's `cli.py` is a thin wrapper: parse args → `_banner` → `await run_pipeline(...)` → map exceptions to
exit codes. **All pipeline logic lives here.** S35's end-to-end proof is this story's `Done when` run
against a clean clone.
