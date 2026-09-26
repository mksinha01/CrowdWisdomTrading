# S33 — CLI entrypoint & doctor

**Phase** 7 · **Depends on** S32 · **Blocks** S34, S35
**Spec** `doc/video-ads-agent.md` lines **3819–3965** (§9.3), **4038–4057** (§10.1), **4114–4312** (§10.3), **5207–5208** (§15 exit codes)
**Context budget** ~19k (spec 6k + story 1.8k + output 10k)
**Produces** `src/cwt/cli.py`, `src/cwt/doctor.py`, `tests/test_doctor.py`

---

## Goal

Two modules that make the system operable: the argument surface, and the preflight that must pass
**before anything spends money**.

> **§10.3.** *"A doctor that says 'something is wrong' is useless; one that names the missing flag, the
> bad model slug and the exact install command is the difference between a five-minute fix and an hour
> of guessing."*

## Interface contract — FROZEN

```python
# src/cwt/cli.py
EXIT_OK, EXIT_CONFIG, EXIT_TIMEOUT, EXIT_BLOCKED, EXIT_BUDGET = 0, 1, 2, 3, 4

def _banner(settings: Settings, paths: RunPaths, offline: bool, engine: str) -> None: ...
async def _cmd_run(args) -> int: ...
def main(argv: list[str] | None = None) -> int: ...

# src/cwt/doctor.py
REQUIRED_KANBAN_FLAGS = {"--assignee","--body","--parent","--idempotency-key","--workspace",
                         "--priority","--max-runtime","--max-retries","--skill","--json"}

@dataclass
class Check:
    name: str; status: str          # "ok" | "warn" | "fail"
    detail: str = ""; hint: str = ""

@dataclass
class Report:
    checks: list[Check]
    @property
    def failed(self) -> list[Check]: ...

def _check_python() -> Check: ...
def _check_ffmpeg() -> Check: ...
def _check_hermes() -> Check: ...
def _check_worker_model() -> Check: ...
def _check_llm_models(settings) -> Check: ...
def _check_gateway(settings) -> Check: ...
def _check_backend_chain(settings) -> Check: ...
def _check_profiles() -> Check: ...          # addition — Rule K2
def run_doctor(json_output: bool = False) -> int: ...
```

## Exit codes — the contract a reviewer reads without a log

| Code | Meaning | Raised by |
|---|---|---|
| 0 | success | — |
| 1 | configuration or preflight error | `ConfigError`, anything unmapped |
| 2 | pipeline timeout | `PipelineTimeout` |
| 3 | pipeline blocked (a card gave up) | `PipelineBlocked` |
| 4 | LLM budget exceeded | `BudgetExceeded` |

## Rules that bind this story

- **§9.3 lines 3885–3887** — the config echo prints **before doing anything**. It is *"the first
  debugging tool in production and it costs nothing."* Include: run_id, run_dir, engine, offline,
  provider + both model slugs, the video chain, resolved ffmpeg with version, resolved hermes with
  version, board, budget. **A `NOT FOUND` entry is printed in red, not raised** — the banner's job is
  to report, and `doctor` is what fails.
- **§10.3 line 4226** — `_check_llm_models` validates the slugs against `/v1/models`. *"Model slugs
  drift. A spec that hardcodes a slug it cannot verify is a spec that breaks in three weeks. This
  converts 'mysterious 404 mid-run' into 'clear preflight error before any spend'."*
- **§10.3 line 4204** — `_check_worker_model` exists to prevent the most likely reviewer confusion:
  **our `LLM_MODEL_*` vars do not configure the Hermes workers.** Warn loudly and say so.
- **Rule K2** — every `DAG_SPEC` assignee must resolve to a profile. Doctor checks it (spec line 4937).
- **B3 — `settings.engine_defaults_to_hermes`** is read at spec line 4291 and never defined in
  `.env.example`. S01 defined it. Use it here.
- **Rule V4** — `_check_backend_chain` **fails** if the chain's last element is not guaranteed
  available. It is a fail, not a warn.
- **Warnings are not failures.** `_check_ffmpeg` warns when `ffprobe` is missing (verification
  degrades but does not stop); `_check_llm_models` warns when the models endpoint is unreachable
  (the slugs cannot be checked, but the run may still work). `_check_gateway` warns.

## Build steps

1. `cli.py` — copy spec lines 3831–3964 verbatim. Subcommands: `run`, `doctor`, `seed`, `status`,
   `bootstrap`, `clean`. `run` flags:
   `--engine {hermes,local}`, `--offline`, `--backend`, `--run-id`, `--run-dir`, `--resume`,
   `--record-pacing`, `--force-stage` (repeatable), `--fail-fast`, `--timeout`.
2. `_banner` — copy spec lines 3849–3874. Resolve ffmpeg and hermes **inside try/except** and print
   `NOT FOUND: {exc}` in red. **The banner must never raise.**
3. `_cmd_run` — copy spec lines 3877–3901. The exception→exit mapping is
   `{"PipelineTimeout": 2, "PipelineBlocked": 3, "BudgetExceeded": 4}` with `.get(name, EXIT_CONFIG)`.
   Print `Done. {done} stages, ${cost:.4f}, output: {output}`.
   - `--resume` calls `hermes.board.resume(run_id, board)` (S28) **before** `run_pipeline`
   - `--backend X` calls `settings.with_backend_chain([X])`
4. `doctor.py` — copy spec lines 4124–4311 verbatim, then:
   - **add `_check_profiles()`** (Rule K2): `hermes profile list`, compare against
     `{c.assignee for c in DAG_SPEC}`. Fail on a missing profile with the hint
     *"Run `cwt bootstrap` to create the nine CWT profiles."*
   - `_check_gateway` — keep the spec's `dry-run` probe and the `hermes gateway start` hint.
     **Only run it when `settings.engine_defaults_to_hermes`** (spec line 4291).
5. **`_check_worker_model` must print the warning text from §9.4**, not a generic one:
   > *Run `hermes model` and pick a model with >=64k context. In `--offline` mode the workers still
   > run on this model.*
6. Order the checks so cheap local ones run first: python → ffmpeg → hermes → worker model →
   profiles → backend chain → llm models → gateway.
7. Tests:
   - `run_doctor` returns 1 when any check fails, 0 otherwise
   - `--json` output is a parseable array of `{name, status, detail, hint}`
   - `_check_ffmpeg` returns `warn` (not `fail`) when only `ffprobe` is missing
   - `_check_backend_chain` **fails** when `Settings` reports a chain not ending in `local_ffmpeg`
   - `_check_llm_models` fails on an unknown slug and warns when the endpoint is unreachable
   - `_check_profiles` fails naming the missing profile
   - `_banner` prints `NOT FOUND` and does not raise when both binaries are absent
   - the exit-code map: a fake `BudgetExceeded` through `_cmd_run` returns 4

## Decisions the spec leaves open

- **`_check_profiles` is an addition.** Rule K2 states doctor *"asserts every DAG_SPEC assignee
  resolves against `hermes profile list`"* (spec line 4937) but no such check appears in the §10.3
  code. Without it, the single most confusing failure mode (a card stuck on `ready` forever, no error)
  reaches the reviewer. Add it.
- **Check ordering is unspecified.** Local-first ordering means a missing Python fails in 50ms rather
  than after a 30s network timeout.
- **`clean` deletes all but the most recent `runs/20*` directory** (spec line 3955). Keep the
  `ignore_errors=True`. Never delete `runs/_diagnostics/` — that is what the operator needs after a failure.
- **`cwt status` uses `_render_progress`** from S28, so the board view is identical in the CLI and
  during a run. Do not write a second renderer.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_doctor.py -q -v      # green

.venv/Scripts/cwt doctor
# -> OK   python           3.11.x
#    OK   ffmpeg           C:\...\ffmpeg-win-x86_64-v7.1.exe (7.1)
#    OK   hermes           v0.16.0 at C:\Users\...\hermes.cmd
#    OK   worker model     google/gemini-2.5-flash  (verify >=64k context)
#    OK   profiles         9 CWT profiles found
#    OK   backend chain    OK hyperframes ... / -- openmontage ... / OK local_ffmpeg ...
#    OK   llm models       4 slugs verified
#    OK   gateway          dispatcher reachable
#    All checks passed.

.venv/Scripts/cwt doctor --json | python -c "import sys,json; print(len(json.load(sys.stdin)), 'checks')"
.venv/Scripts/cwt run --help
.venv/Scripts/cwt status
```

## Handoff

S34's scripts call `cwt doctor` and `cwt run`. S35 verifies the exit-code contract on a clean clone.
**`cwt run` never calls `doctor` itself** — the operator runs it, per §18 step 9, *"before spending
anything"*. The one exception is the gateway check, which the banner's `engine=hermes` path implies.
