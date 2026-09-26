# S28 — Kanban board operations

**Phase** 6 · **Depends on** S27 · **Blocks** S32
**Spec** `doc/video-ads-agent.md` lines **3582–3817** (§9.2), **4940–4969** (Rules K3, K4), **4321–4350** (§11.1), **4352–4395** (§11.2)
**Context budget** ~17k (spec 5k + story 1.7k + output 9.5k)
**Produces** `hermes/board.py`, `hermes/record.py`, `tests/test_board.py`

---

## Goal

Seed the board, wait for it, resume it, and stall-detect it. This is the layer that makes the pipeline
*watchable* — the progress table rendered here is deliberately **the thing a viewer watches** during
the demo recording (spec line 3775).

The single most likely "it hangs forever" failure is the dispatcher not running because the gateway is
down: cards sit on `ready` and nothing happens. This story detects it, nudges **once**, then fails
loudly with the fix.

## Interface contract — FROZEN

```python
# hermes/board.py
TERMINAL_BAD = {"blocked", "gave_up"}

def render_body(card: CardSpec, run_id: str, run_dir: Path, board: str) -> str: ...
def seed(run_id: str, run_dir: Path, board: str,
         spec: list[CardSpec] = DAG_SPEC) -> dict[str, str]: ...      # key -> card id
def list_cards(board: str) -> list[dict]: ...
def nudge(board: str) -> None: ...                                    # one dispatch tick

@dataclass
class RunSummary:
    cards: list[dict]
    @property
    def done(self) -> int: ...

class PipelineTimeout(RuntimeError): ...
class PipelineBlocked(RuntimeError): ...

async def wait_for_completion(board: str, *, timeout_s: int, poll_s: int = 15,
                              fail_fast: bool = False,
                              stall_threshold_s: int = 180) -> RunSummary: ...
def resume(run_id: str, board: str) -> None: ...

# ── internals ──
def _render_progress(cards: list[dict]) -> None: ...
def _dump_diagnostics(board: str, cards: list[dict]) -> None: ...

# hermes/record.py
def record_pacing_pause(stage: str, *, seconds: float = 4.5) -> None: ...
def recording_recipe() -> str: ...
```

## Rules that bind this story

- **Rule K3 — the dispatcher runs in the gateway.** If the gateway is down, nothing moves.
  `kanban.dispatch_in_gateway: true` is the default and what we rely on. Stall threshold = 3 dispatcher
  ticks (180s). **Detect, nudge ONCE, then fail loudly** with
  `"The gateway is probably not running. Start it with \`hermes gateway start\`, then \`cwt run --resume\`."`
  **NEVER force-complete a card to clear a stall. The pipeline does not fabricate progress.**
- **Rule K4** — `--idempotency-key {run_id}:{card.key}` on every create. Re-seeding after a crash
  **reuses** the existing card rather than creating a second copy of all 12. Without it, two scripts
  render and the board becomes a mess.
- **§9.2 line 3611 — `render_body` deliberately contains NO artifact paths.** Paths arrive through the
  parent chain via `kanban_show()`, so a card never has to be re-seeded when a path convention changes.
  `--parent` is a **context channel**, not merely a scheduling gate. Do not add paths to the body.
- **§9.2 line 3638** — *"A completion WITHOUT `metadata.artifact_path` is a bug: the next stage has
  nothing to read and will block."* The body says so; do not soften it.
- **§11.1 line 4349 — security, non-negotiable.** The dashboard's plugin routes are unauthenticated by
  design. **Never run `hermes dashboard --host 0.0.0.0`.** Bind to localhost. This must appear in the
  README (S36) and in `recording_recipe()`.

## Build steps

1. `render_body` — copy spec lines 3617–3644 verbatim. The four STEPs and the `ON FAILURE` block are
   the contract every worker reads. Keep the `{run_id}`, `{run_dir}`, `{board}` interpolation.
2. `seed` — copy spec lines 3647–3682:
   - iterate `topo_sort(spec)` so parents exist before children
   - `--idempotency-key f"{run_id}:{card.key}"`
   - `--parent <id>` for each parent, `--skill` for each skill, `--max-runtime`, `--max-retries`
   - `--goal --goal-max-turns 20` when `card.goal`
   - **the failure message must name the flag-diff remedy**: *"If the error names an unknown flag, your
     Hermes version differs from the one this spec targets. Run `cwt doctor` for a flag diff."*
3. `wait_for_completion` — copy spec lines 3715–3771. The signature detail that matters: a **card
   signature** `tuple(sorted((id, status)))`. Progress is detected by a *change in the signature*, not
   by a card completing. That is what makes a stage moving `running → review` reset the stall timer.
   - `bad` cards with `fail_fast` raise `PipelineBlocked` immediately
   - completion is `len(done) == len(DAG_SPEC)` — **note this is 12, per S27's decision**
   - on timeout: `_dump_diagnostics` then `PipelineTimeout`
   - on first stall: `nudge()`, set `nudged=True`, reset `last_progress`
   - on second stall: `_dump_diagnostics` then `PipelineBlocked` with the gateway hint
4. `_render_progress` — copy spec lines 3774–3788. The colour map is
   `{done: green, running: cyan, ready: yellow, blocked: red, todo: dim, review: magenta}`. **This table
   is on screen during the recording** — do not route it through a logger, and do not suppress it when
   `--record-pacing` is set.
5. `_dump_diagnostics` — copy spec lines 3791–3804. For every blocked/running card, write
   `runs/_diagnostics/<card_id>.json` with `show`, `runs` and the last 20k of `log`. Then print the
   retry command. **This is the difference between a 5-minute fix and an hour of guessing.**
6. `resume` — copy spec lines 3807–3816. Unblock every `TERMINAL_BAD` card. Safe because seeding is
   idempotent and the artifact store (S07) skips stages whose inputs are unchanged, so a resumed run
   does not re-spend API credits.
7. `hermes/record.py`:
   - `record_pacing_pause(stage, seconds=4.5)` — a deliberate sleep so the recording is watchable
     rather than a blur. **Only called when `--record-pacing` is set.** 3–6s per the spec (line 4392);
     use 4.5s and print what it is waiting for.
   - `recording_recipe()` — return the recipe string from spec lines 4374–4394 verbatim, including the
     "MOMENTS WORTH CAPTURING" block. S34's PowerShell script prints it.
8. Tests (fake `kanban` via monkeypatch):
   - `seed` sends `--idempotency-key` for every card and `--parent` for every declared parent
   - re-seeding with the same `run_id` does not duplicate (fake returns the same ids)
   - `wait_for_completion` returns when all 12 are `done`
   - a signature change resets the stall timer without nudging
   - no signature change for `stall_threshold_s` → exactly **one** `nudge()` call
   - a second stall → `PipelineBlocked` whose message contains `hermes gateway start`
   - `fail_fast` raises on the first `blocked` card
   - **`render_body` output contains no file path from `paths`** — assert `"artifacts/"` is absent

## Decisions the spec leaves open

- **`nudge()` is a nudge, never a force-complete.** Stated twice in the spec (lines 3696, 4955). Add
  a module-level comment so a future "fix" for a stuck board does not become a `kanban complete` call.
- **Diagnostics path is outside `run_dir`.** The spec writes to `runs/_diagnostics/` (line 3800), while
  a run lives at `runs/<run_id>/`. Keep it there — diagnostics span runs and outliving the run they
  describe is the point. Add `runs/_diagnostics/` to `.gitignore`.
- **`poll_s=15`** with `stall_threshold_s=180` means 12 polls before a stall is declared. That matches
  "3 dispatcher ticks" at a 60s dispatch interval.
- **`RunSummary` is deliberately thin.** It carries `cards` and a `done` count; S32 computes the cost
  and output path from the artifacts, not from the board.
- **Dashboard `lane_by_profile: true`** is the single most useful setting for a demo (spec line 4346) —
  it makes the parallelism legible. Document it in `recording_recipe()`; do not set it programmatically.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_board.py -q -v      # green, no live Hermes

.venv/Scripts/python -c "
from cwt.hermes.board import render_body
from cwt.hermes.dag import DAG_SPEC
import pathlib
b = render_body(DAG_SPEC[7], '20260926-1402-a7f3', pathlib.Path('runs/x'), 'cwt-ads')
assert 'artifacts/' not in b, 'paths must arrive via kanban_show, not the body'
assert 'cwt-write-storyboard' in b and 'kanban_request_review' in b
print(b[:300])"

# live smoke (needs Hermes + gateway; skip in CI)
.venv/Scripts/python -c "
from cwt.hermes.board import seed, list_cards
ids = seed('smoke-001', __import__('pathlib').Path('runs/smoke-001'), 'cwt-ads')
print(len(ids), 'cards'); print([c['status'] for c in list_cards('cwt-ads')][:5])"
```

## Handoff

S32's `run_pipeline` calls `seed()` then `await wait_for_completion(...)` and maps
`PipelineTimeout→exit 2`, `PipelineBlocked→exit 3`. S34's recording script relies on
`_render_progress` and `record_pacing_pause` producing the on-screen moments listed in the recipe. **The
board is never mutated after seeding except by unblocking — the agents own the card states.**
