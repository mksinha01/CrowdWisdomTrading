# S31 — Profiles, skills & `cwt bootstrap`

**Phase** 6 · **Depends on** S27, S30 · **Blocks** S32
**Spec** `doc/video-ads-agent.md` lines **1912–2020** (§7.3 profiles, §7.4 skills), **1666–1720** (§7.1 `config.yaml`), **4927–4938** (Rule K2)
**Context budget** ~19k (spec 5k + story 1.9k + output 11k) — many small files
**Produces** `src/cwt/bootstrap.py`, `hermes/profiles/**` (9 dirs × 3 files), `skills/**` (8 files), `tests/test_bootstrap.py`

---

## Goal

Create the nine agents, the eight skill procedures, and the one command that installs them.

> **§7.4 line 1967.** *"This is how we get deterministic behaviour out of a stochastic worker: **the
> skill is the procedure, not the prompt**."* A skill is a Markdown file a card force-loads by name.
> The worker reads it and follows it. That is the mechanism that makes a stochastic agent reliably
> produce a schema-valid artifact.

> **G2 — `src/cwt/bootstrap.py` / `install_hermes_assets()` is called at `cli.py:3952` and never
> specified.** This story defines it.
> **G7 — 7 of 8 `SKILL.md` files.** §7.4 shows one and says the rest *"follow the identical structure"*.
> **G8 — 8 of 9 `SOUL.md` files**, plus every profile's `config.yaml` and `.env`.

## Interface contract — FROZEN

```python
# src/cwt/bootstrap.py
def install_hermes_assets(*, settings: Settings | None = None, force: bool = False) -> int:
    """Create profiles, install skills + plugin, merge config. Returns an exit code (0)."""
def merge_config(template: Path, target: Path) -> dict: ...
def create_profiles(*, hermes_home: Path, names: list[str]) -> list[str]: ...
def install_plugin(*, hermes_home: Path, source: Path) -> Path: ...
def install_skills(*, hermes_home: Path, source: Path) -> list[str]: ...
```

## The nine profiles — exact names (Rule K2)

| Profile | Role | Tier | Sees |
|---|---|---|---|
| `cwt-orchestrator` | Decomposes the goal into cards. Owns no artifacts | cheap | full kanban toolset |
| `cwt-ads-manager` | Sources winning ads; assembles the submission bundle | cheap | ads tools |
| `cwt-hook-analyst` | Extracts hooks, pains, concepts, beat sheets | cheap | pattern tools |
| `cwt-researcher` | Runs the three angles; assembles the brief | cheap | research tools |
| `cwt-script-writer` | Writes variants, hooks, rewrites | **strong** | storyboard tools |
| `cwt-creative-director` | Scores against the rubric; approves or rejects | cheap | review tools |
| `cwt-compliance` | Runs the claims gate | cheap | claims tools |
| `cwt-video-editor` | Renders through the backend chain | cheap | video tools |
| `cwt-qa` | Verifies the rendered output | cheap | probe + claims tools |

**Only `cwt-script-writer` is strong tier.** ~35 of ~40 LLM calls per run are classification and
scoring, where a mid-size model is indistinguishable and an order of magnitude cheaper (Rule A5).

> **Rule K2 is the highest-stakes detail here.** The profile name must **exactly** match the
> `assignee` string on the card. An unresolvable assignee leaves the card on `ready` **forever**,
> emits `skipped_nonspawnable`, and has **no fallback spawn**. The run appears to hang with no error.
> Note `cwt-creative-director` owns no card — it is a **reviewer** (spec line 360), so it needs a
> profile but no `DAG_SPEC` assignee.

## The eight skills

`cwt-source-winning-ads`, `cwt-extract-ad-patterns`, `cwt-research-angle`, `cwt-write-storyboard`,
`cwt-creative-review`, `cwt-claims-gate`, `cwt-render-video`, `cwt-final-qa` — each a
`skills/<name>/SKILL.md` with YAML frontmatter and exactly four sections:
**When to Use · Procedure · Pitfalls · Verification** (spec line 2019–2021).

## Rules that bind this story

- **Rule K2** — `assignee` ≡ profile name, exactly. `bootstrap` must **assert every `DAG_SPEC` assignee
  resolves** to a profile it created, and fail loudly otherwise.
- **§7.2 line 1729** — a plugin lives flat or **one category level deep**. Anything deeper is
  **silently ignored**. `install_plugin` must place files directly in `~/.hermes/plugins/cwt/`.
- **§7.1 line 1668** — `config.yaml` is a **template** merged into `~/.hermes/config.yaml`. **Merge, do
  not overwrite** — the user's other settings must survive. `kanban.review_dispatch: true` is
  **required** (spec line 1684): both our gates depend on it. `auto_decompose: false` is also required:
  *"our DAG is explicit; we do NOT want the board inventing cards."*
- **`kanban.max_in_progress: 4` must match `LLM_MAX_CONCURRENCY`** (spec line 1687, Rule A4). Four
  parallel cards each making LLM calls against a ~40 RPM per-model limit.
- **Rule A5** — `delegation.model` is the cheap model (spec line 1694): *"children run cheap."*
- **§7.4 line 1968** — the skill is the **procedure**, not the prompt. A skill that restates a prompt is
  useless; a skill that says *"call `cwt_generate_hook_candidates` once. Do NOT invent a hook
  yourself"* changes behaviour.

## Build steps

1. **`hermes/profiles/<name>/SOUL.md`** × 9. Each has the shape of the spec's example (lines 1941–1962):
   `# You are the <Role>` → a one-line statement of what the agent is → `## What you do` →
   `## What you never do` → `## How you know you are done`.
   - The **"What you never do"** section is the load-bearing one. Derive it from the agent's risk:
     - `cwt-script-writer` — no performance statistic, no position-access implication, no shot without
       executable camera direction, never exceed the beat tolerances
     - `cwt-compliance` — **never** `kanban_complete` a card with a standing HARD finding
     - `cwt-hook-analyst` — never invent a beat sheet for an ad with no duration
     - `cwt-video-editor` — never declare success without probing the output
     - `cwt-researcher` — never state an unsourced claim
   - **"How you know you are done"** must name the terminal call: `kanban_complete` with
     `metadata.artifact_path`, or `kanban_request_review` for the script writer.
2. **`hermes/profiles/<name>/config.yaml`** × 9 — select the model tier per the table, and the toolset
   the profile "sees". Keep them minimal; `~/.hermes/config.yaml` carries the shared settings.
3. **`hermes/profiles/<name>/.env`** × 9 — the four `requires_env` keys the plugin declares, plus
   `CWT_RUN_DIR` is **injected by the dispatcher**, so do **not** put it here. Add a header comment
   saying so; a confused operator setting it by hand is a real failure mode.
4. **`skills/*/SKILL.md`** × 8 — the four sections. Write each for an **LLM reader, not a human**
   (spec line 2020). `cwt-write-storyboard` is given in full (lines 1972–2017): **copy it verbatim**.
   For the other seven, derive the Procedure from the corresponding card's `body` (S27) and the tool
   docstrings (S19–S26). Each must end with a `## Verification` naming a concrete check, e.g.
   `cwt_verify_artifact --name storyboard` returns `{"ok": true}`.
5. **`bootstrap.py`**:
   - `hermes_home()` — `~/.hermes` (respect `HERMES_HOME` if set)
   - `create_profiles()` — `hermes profile create <name>` for each of the nine; idempotent (an existing
     profile is not an error)
   - `install_skills()` — copy `skills/*/SKILL.md` into `~/.hermes/skills/`
   - `install_plugin()` — copy the four plugin files into `~/.hermes/plugins/cwt/`; **assert the depth**
     (Rule §7.2)
   - `merge_config()` — deep-merge `hermes/config.yaml` into `~/.hermes/config.yaml`, **refusing to
     clobber** a differing existing key without `force`, and printing what changed
   - **verify Rule K2**: read `DAG_SPEC` (S27), collect every `assignee`, assert each resolves against
     `hermes profile list`. Fail with the missing names.
6. Tests (fake `hermes` binary on PATH):
   - all nine profile names created; `cwt-creative-director` included though it has no card
   - **Rule K2 assertion fires** when a `DAG_SPEC` assignee has no profile
   - `merge_config` preserves an unrelated existing key
   - `merge_config` refuses to clobber a conflicting key without `force`
   - plugin install path is exactly `~/.hermes/plugins/cwt/` (depth check)
   - all 8 skill files land and parse their YAML frontmatter
   - `install_hermes_assets` is idempotent — running twice changes nothing

## Decisions the spec leaves open

- **G2 — `install_hermes_assets()` signature.** `cli.py` calls it with no arguments (spec line 3953),
  so all parameters must be optional. Returns an `int` exit code, matching `main()`'s contract.
- **G8 — profile `config.yaml` and `.env` contents** are unspecified. Keep `config.yaml` to `model` +
  `tools` + `skills.auto_load`, and `.env` to exactly the plugin's four `requires_env` keys. **Do not
  duplicate secrets management** — the worker inherits the run environment.
- **`HERMES_HOME`** is not mentioned in the spec. Respect it if set (it makes tests possible without
  touching the real `~/.hermes`), default to `~/.hermes`.
- **Idempotency.** `cwt bootstrap` runs on every fresh clone per §18 step 7. It must be safe to run
  twice — `--force` exists only for the config merge.
- **Skill frontmatter** must include `metadata.hermes.requires_toolsets: [cwt]` (spec line 1981), or the
  card's `--skill` load will not surface the toolset.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_bootstrap.py -q -v      # green, fake hermes

HERMES_HOME=$(mktemp -d) .venv/Scripts/python -c "
from cwt.bootstrap import install_hermes_assets, hermes_home
print(install_hermes_assets())          # 0
h = hermes_home()
import pathlib
print(sorted(p.name for p in (h/'plugins'/'cwt').iterdir()))
print(len(list((h/'skills').glob('*/SKILL.md'))), 'skills')
print(len(list((h/'profiles').iterdir())) if (h/'profiles').exists() else 'profiles via hermes cli')"

# Rule K2 — every DAG assignee has a profile
.venv/Scripts/python -c "
from cwt.hermes.dag import DAG_SPEC
from cwt.bootstrap import PROFILE_NAMES
missing = {c.assignee for c in DAG_SPEC} - set(PROFILE_NAMES)
assert not missing, missing
print('all', len(PROFILE_NAMES), 'profiles cover every card assignee')"
```

## Handoff

S32's `run_pipeline` assumes profiles exist when `engine == "hermes"` and errors with
*"Run \`cwt bootstrap\` first"* if they do not. S33's doctor asserts every `DAG_SPEC` assignee resolves
(Rule K2). **`cwt run --engine local` must not require any of this** — no profiles, no gateway, no
plugin — which is what makes the offline CI path work on a bare machine.
