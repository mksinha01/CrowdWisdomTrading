# S27 — Hermes CLI wrapper & DAG

**Phase** 6 · **Depends on** S02 · **Blocks** S28, S31, S32
**Spec** `doc/video-ads-agent.md` lines **2150–2235** (§8.2), **3343–3580** (§9.1), **4588–4615** (Rule A1), **4927–4938** (Rule K2), **4958–4969** (Rule K4)
**Context budget** ~17k (spec 5k + story 1.7k + output 9.5k)
**Produces** `hermes/__init__.py`, `hermes/cli.py`, `hermes/dag.py`, `tests/test_dag.py`

---

## Goal

Two modules: the **only** place the `hermes` binary is invoked, and the card graph **as data**.

§9.1: *"Every topology claim in this document is one edit to this list."* The DAG is 11 cards. Making
it data rather than a sequence of imperative `kanban create` calls is what makes the topology
reviewable, topologically sortable, and resumable.

## Interface contract — FROZEN

```python
# hermes/cli.py
DEFAULT_TIMEOUT = 120.0

@dataclass(frozen=True)
class HermesResult:
    returncode: int; stdout: str; stderr: str
    @property
    def ok(self) -> bool: ...
    def json(self): ...

@lru_cache(maxsize=1)
def hermes_bin() -> str: ...            # $HERMES_BIN -> shutil.which("hermes") -> RuntimeError w/ install cmd
def run_hermes(args: list[str], *, timeout_s: float = DEFAULT_TIMEOUT,
               check: bool = False) -> HermesResult: ...
def hermes_version() -> str: ...
def kanban(*args: str, board: str, timeout_s: float = DEFAULT_TIMEOUT) -> HermesResult: ...
def supported_flags() -> set[str]: ...  # parses `hermes kanban create --help`

# hermes/dag.py
@dataclass(frozen=True)
class CardSpec:
    key: str; title: str; assignee: str
    parents: tuple[str, ...] = (); skills: tuple[str, ...] = ()
    max_runtime: str | None = None; max_retries: int | None = None
    goal: bool = False; body: str = ""

DAG_SPEC: list[CardSpec] = [...]        # 11 cards
def topo_sort(spec: list[CardSpec] = DAG_SPEC) -> list[CardSpec]: ...
```

## The DAG — 11 cards, exact parent wiring

| key | assignee | parents | skills | max_runtime | retries |
|---|---|---|---|---|---|
| `root` | `cwt-orchestrator` | — | `cwt-source-winning-ads` | 10m | — |
| `ads` | `cwt-ads-manager` | `root` | `cwt-source-winning-ads` | 20m | 2 |
| `patterns` | `cwt-hook-analyst` | `ads` | `cwt-extract-ad-patterns` | 20m | — |
| `res_pain` | `cwt-researcher` | `patterns` | `cwt-research-angle` | 12m | — |
| `res_unique` | `cwt-researcher` | `patterns` | `cwt-research-angle` | 12m | — |
| `res_crowd` | `cwt-researcher` | `patterns` | `cwt-research-angle` | 12m | — |
| `brief` | `cwt-researcher` | `res_pain`,`res_unique`,`res_crowd` | `cwt-research-angle` | 8m | — |
| `script` | `cwt-script-writer` | `brief` | `cwt-write-storyboard` | 45m | 2 |
| `compliance` | `cwt-compliance` | `script` | `cwt-claims-gate` | 15m | — |
| `render` | `cwt-video-editor` | `compliance` | `cwt-render-video` | 60m | 1 |
| `qa` | `cwt-qa` | `render` | `cwt-final-qa` | 10m | — |
| `collect` | `cwt-ads-manager` | `qa` | — | 5m | — |

`root` carries `goal=True` and the four acceptance criteria (spec lines 3394–3402). **That is 12 rows
because `collect` is the twelfth — the spec says "11 cards" at line 3341 but `DAG_SPEC` defines 12.**
Count them: root, ads, patterns, res_pain, res_unique, res_crowd, brief, script, compliance, render,
qa, collect. **12.** The spec's own prose and its own `render_body` output (line 3995, *"Seeding 11
cards"*) disagree with its own data structure. **Build 12 and fix the prose** — `wait_for_completion`
compares `len(done) == len(DAG_SPEC)`, so 12 is self-consistent.

## Rules that bind this story

- **Rule A1 — Hermes has no Python SDK.** `from hermes import Agent` does not work; pip installs are
  explicitly unsupported upstream. Two integration paths, both used: a **native plugin** (S29/S30) and
  **the CLI, always through `run_hermes()`**. One file to fix when Hermes drifts.
- **Rule A1 corollary** — a tool handler that raises kills the agent turn, not just the call. That is
  S30's concern, but it is why nothing here may raise into a worker.
- **Rule K2 — the card `assignee` must exactly match a Hermes profile name.** An unresolvable assignee
  leaves the card on `ready` **forever**, emits `skipped_nonspawnable`, and has no fallback spawn. The
  run appears to hang with no error. Every `assignee` above must be created by S31's `cwt bootstrap`.
- **Rule K4 — `--idempotency-key` is what makes `--resume` safe.** It is applied in S28's `seed()`,
  not here, but `CardSpec.key` is the second half of that key.
- **Rule W4** — `hermes` on Windows is a `.cmd` shim. `hermes_bin()` uses `shutil.which()`; `run_hermes`
  goes through `run_tool()` (S02), which resolves it.
- **Rule W1** — `run_hermes` raises `TypeError` on a `str` argument. Shell strings are banned.
- **§9.1 line 3351 — why the review is a card and not a loop.** `kanban_complete` **ends** the card,
  which would start the render and skip compliance entirely. Compliance is therefore its own card.

## Build steps

1. `hermes/cli.py` — copy spec lines 2172–2234 verbatim. The `hermes_bin()` error message must carry
  both install commands (Windows `iex (irm ...)` and Linux `curl ... | bash`) — a doctor that says
  "something is wrong" is useless.
2. `supported_flags()` — parse `--` tokens from `kanban create --help`, stripping trailing punctuation
   and any `=value`. S33's doctor compares this against `REQUIRED_KANBAN_FLAGS` and fails loudly,
   converting "mysterious silent misbehaviour after a Hermes upgrade" into "clear preflight error
   naming the missing flag".
3. `hermes/dag.py` — transcribe `CardSpec` and `DAG_SPEC` from spec lines 3373–3552, **extended to 12
   cards** with `collect` (spec lines 3540–3551). Copy every `body` string verbatim — those bodies are
   the contract each worker reads, and they contain the acceptance criteria.
4. `topo_sort` — copy spec lines 3555–3579 (Kahn's algorithm). The `ready.sort()` after each insertion
   is what makes the order **deterministic and reproducible across resumes**, which matters because
   `CardSpec.key` feeds the idempotency key. Keep it.
5. Tests (`tests/test_dag.py`):
   - `len(DAG_SPEC) == 12` — and a comment recording the spec's "11 cards" prose as a known defect
   - every `parents` entry names a card that exists
   - `topo_sort` returns all 12 in an order where every parent precedes its child
   - `topo_sort` is **stable** — calling it twice yields identical key order
   - `topo_sort` raises on a cycle and on an unknown parent
   - every `assignee` matches `^cwt-(orchestrator|ads-manager|hook-analyst|researcher|script-writer|creative-director|compliance|video-editor|qa)$` —
     the same nine names S31 creates profiles for (Rule K2)
   - every `skills` entry is one of the eight in S31

## Decisions the spec leaves open

- **12 cards, not 11.** Above. The spec's prose (line 3341, 3349, *"Seeding 11 cards"* at 3995) conflicts
  with its own `DAG_SPEC`. The data structure wins: `wait_for_completion` compares against
  `len(DAG_SPEC)`, so the count must match the list, whatever it is. **Fix the two prose mentions in
  S36's README and in S32's banner output.**
- **`cwt-creative-director` owns no card.** It is a **reviewer**, not an assignee (spec line 3360):
  `t_script --request_review--> cwt-creative-director`. So 8 of the 9 profiles appear as `assignee`s
  and `creative-director` appears only as a reviewer. **The assignee test above must allow that** —
  build the check as "every assignee has a profile", not "every profile has a card".
- **`record.py` is S28's.** It is a sibling module; do not create it here.
- **`hermes/config.yaml`** (spec §7.1) is written into the repo by S01's tree but **merged into
  `~/.hermes/config.yaml` by S31's `cwt bootstrap`**. Do not touch it here.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_dag.py -q -v      # green

.venv/Scripts/python -c "
from cwt.hermes.dag import DAG_SPEC, topo_sort
print(len(DAG_SPEC), 'cards')
order = [c.key for c in topo_sort()]
print(' -> '.join(order))
assert order.index('brief') > order.index('res_crowd')
assert order.index('compliance') > order.index('script')
assert order.index('render') > order.index('compliance')
assert [c.key for c in topo_sort()] == order, 'topo_sort is not stable'"

.venv/Scripts/python -c "
from cwt.hermes.cli import hermes_bin, hermes_version, supported_flags
print(hermes_bin(), hermes_version())
print(sorted(supported_flags()))"   # RuntimeError with the install commands if Hermes is absent — that is correct
```

## Handoff

S28 imports `DAG_SPEC`, `CardSpec` and `topo_sort` for `seed()`. S31 creates a profile for every
`assignee` and a skill for every `skills` entry. S33's doctor asserts every `DAG_SPEC` assignee resolves
against `hermes profile list` (Rule K2). **All Hermes coupling lives in `src/cwt/hermes/` — a Hermes
version change is a contained fix in these two files.**
