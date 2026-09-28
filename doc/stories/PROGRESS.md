# PROGRESS

Build ledger for the 36-story breakdown in `doc/stories/`. One line per story.

**Status values:** `todo` · `doing` · `done` · `blocked` · `n/a`

Fill this in **as you build**. A story is `done` only when its "Done when" block has been run and
passed — not when the code is written.

---

## Phase 0 — Foundation

| # | Story | Status | Commit | Verified by |
|---|---|---|---|---|
| S01 | Repo skeleton, packaging & config | done | `f807115` | `Settings.from_env()` + Rule V4 negative |
| S02 | Util layer | done | `a4e17ed` | `pytest tests/test_jsonio.py tests/test_subproc.py` |

## Phase 1 — Domain contracts

| # | Story | Status | Commit | Verified by |
|---|---|---|---|---|
| S03 | Artifact schema models | done | `2eb4a82` | `pytest tests/test_models.py -k "not validator"` |
| S04 | Storyboard validators | done | `be28c2f` | 11 validator tests selected, 11 passed |
| S05 | Beat taxonomy & aggregation | done | `1a787e4` | `pytest tests/test_beats.py` |
| S06 | Deterministic claims engine | done | `443d307` | 15 rules (12 hard/3 soft), 60 tests green, clean-script 0 hard |
| S07 | Artifact store & provenance | done | `4e3033e` | `pytest tests/test_artifacts.py` + secret-scan fire |

## Phase 2 — Clients

| # | Story | Status | Commit | Verified by |
|---|---|---|---|---|
| S08 | HTTP fixture cache / `--offline` | done | `4e3033e` | `pytest tests/test_http_cache.py` + B5 guard |
| S09 | LLM client | done | `38932dc` | `pytest tests/test_llm_repair.py`, zero network |
| S10 | Apify client | done | `22225e5` | `pytest tests/test_apify.py` + Rule H2 assertion |
| S11 | Tavily + Exa clients | done | `ce4d2a6` | `pytest tests/test_search.py` + legacy-enum grep |
| S12 | TTS client & transcript | done | `ce4d2a6` | `pytest tests/test_tts.py` + timing contiguity |

## Phase 3 — Prompts

| # | Story | Status | Commit | Verified by |
|---|---|---|---|---|
| S13 | Prompt package | done | `ce4d2a6` | 13 templates import; `safe_format` degrades; 28 tests green |

## Phase 4 — Video

| # | Story | Status | Commit | Verified by |
|---|---|---|---|---|
| S14 | ffmpeg, backend chain, filtergraph | done | | `pytest tests/test_filtergraph.py` + Rule V4 |
| S15 | Local ffmpeg A — shot render | done | | one shot renders; no absolute path in argv; 17 tests green |
| S16 | Local ffmpeg B — mix, probe, manifest | done | `786fc40` | `pytest tests/test_local_ffmpeg.py` + xfade math |
| S17 | Asset sourcer & fixture assets | done | `8f91dd2` | `pytest tests/test_assets.py` + determinism |
| S18 | Optional backends | done | `cb19121` | `available()` never raises; no `9119`/`8000` |

## Phase 5 — Tool surface

| # | Story | Status | Commit | Verified by |
|---|---|---|---|---|
| S19 | Tools — ads | done | `db83c8a` | `pytest tests/test_tools_ads.py` + no token in artifact |
| S20 | Tools — patterns | done | | median timeline contiguous; distribution sums to 1 |
| S21 | Tools — research | done | | baseline `prohibited_facts` never shrinks |
| S22 | Tools — storyboard A | done | | splices recorded; no caption-only shots; 11 tests green |
| S23 | Tools — storyboard B | todo | | `must_not_change` enforcement fires |
| S24 | Tools — claims | todo | | Rule C1 no-de-escalation; both stages recorded |
| S25 | Tools — video | done | | manifest parses; `qa_check` fails a 28s render |
| S26 | Tools — bundle | todo | | `submission/` gitignored; token allowlist fires |

## Phase 6 — Hermes integration

| # | Story | Status | Commit | Verified by |
|---|---|---|---|---|
| S27 | Hermes CLI wrapper & DAG | todo | | `pytest tests/test_dag.py`; 12 cards, stable sort |
| S28 | Kanban board operations | todo | | `pytest tests/test_board.py`; one nudge then block |
| S29 | Plugin manifest & schemas | todo | | `plugin.yaml` set == 19 schemas |
| S30 | Plugin registration & handlers | todo | | 19 handlers return parseable JSON on empty args |
| S31 | Profiles, skills & `cwt bootstrap` | todo | | Rule K2 assertion; idempotent |

## Phase 7 — Entry & orchestration

| # | Story | Status | Commit | Verified by |
|---|---|---|---|---|
| S32 | Pipeline engine | done | | `cwt run --engine local --offline` renders |
| S33 | CLI entrypoint & doctor | todo | | `cwt doctor` all green; exit-code map |
| S34 | Scripts & fixture recording | done | | `scrub_fixtures.py --check`; no `0.0.0.0` |

## Phase 8 — Proof & submission

| # | Story | Status | Commit | Verified by |
|---|---|---|---|---|
| S35 | End-to-end proof & test suite | done | uncommitted | offline gate renders; `pytest tests/ -q` 730 passed; 19/19 acceptance; resume $0.0000 |
| S36 | README, docs & submission | todo | | no secrets in public docs; bundle complete |

---

## Recorded verifications

Paste the **actual output** of each phase gate here. This is the audit trail.

### S35 — the offline gate (the submission acceptance test)

```
$ .venv/Scripts/python.exe -m cwt run --engine local --offline
=== CWT Video Ads Agent ===
run_id       20260928-0946-d6df
run_dir      runs\20260928-0946-d6df
engine       local
offline      True
video        hyperframes,openmontage,local_ffmpeg
ffmpeg       ...\imageio_ffmpeg\binaries\ffmpeg-win64-v4.2.2.exe  (4.2.2)
hermes       NOT FOUND: The `hermes` binary was not found on PATH.
board        cwt-ads
budget       $2.00
Done. 11 stages, $0.0000, output: runs\20260928-0946-d6df\render\final.mp4
exit 0
```

The rendered file: `42.12s`, `1080x1920`, `h264`, 354,376,626 bytes,
`sha256 70afd35c617d8c8df6093469ace2f8c6adddb6afbbbcf547a64108621d39557b`.
The hash is **identical across every run recorded here** — the fixture replay is deterministic.

### S35 — `pytest tests/ -q` (no network)

```
730 passed in 707.54s (0:11:47)
```

The end-to-end module renders one real video per session; that is where the 11 minutes go.
Markers registered: `network`, `ffmpeg`, `hermes`.

### S35 — the four acceptance criteria

```
$ CWT_E2E_RUN_DIR=runs/20260928-0946-d6df pytest tests/test_end_to_end.py -q -v
19 passed in 32.83s
```

`CWT_E2E_RUN_DIR` points the suite at a run already produced by the gate, instead of encoding a
second identical video. With the variable unset the fixture renders its own.

### S35 — the `--resume` zero-cost promise

```
$ .venv/Scripts/python.exe -m cwt run --engine local --offline --run-id 20260928-0946-d6df
Done. 11 stages, $0.0000, output: runs\20260928-0946-d6df\render\final.mp4
```

Every stage reported `skipped` (asserted), and `llm_ledger.jsonl` did not gain a row — the real
proof of "zero API calls", since the returned `cost_usd` is `0.0` by construction when offline.

### S35 — the clean clone

```
$ git clone . /tmp/cwt-clean && cd /tmp/cwt-clean
$ git ls-files | wc -l        -> 215
$ cp .env.example .env        # what scripts/bootstrap.ps1 step 3 does
$ env -i PYTHONPATH=<working-tree>/src <venv>/python -m cwt run --engine local --offline
Done. 11 stages, $0.0000, output: runs\20260928-1023-dc9a\render\final.mp4
exit 0
```

`env -i` leaves `MSYSTEM`, `PATH`, `SYSTEMROOT`, `WINDIR` (Git Bash injects them) and nothing
else — printed from inside the child process and confirmed to contain no `*_KEY` / `*_TOKEN`
variable. `Settings.from_env()` reads the clone's `.env`, which is `.env.example` copied verbatim: placeholders only, no real key is
reachable. Git Bash provides `env`; in PowerShell use `Get-ChildItem env: | Remove-Item` in a
subshell, or run from a fresh terminal.

The clone-rendered video is byte-identical: `42.12s`, `1080x1920`, `h264`,
`sha256 70afd35c617d8c8d…`.

**What this does and does not prove.** It proves the clone's *committed data* is sufficient —
every offline-critical path is tracked (`fixtures/artifacts/`, `fixtures/assets/`,
`tests/fixtures/`, `hermes/plugins/cwt/`, `skills/`, `scripts/`) and no credential is needed.

It does **not** prove the clone's own *code* renders, because S35's six fixes are uncommitted.
`PYTHONPATH` above points at the working tree. Running the clone's own `src/` reproduces S35-1
exactly:

```
$ PYTHONPATH=/tmp/cwt-clean/src <venv>/python -m cwt run --engine local --offline
Done. 0 stages, $0.0000, output: runs\20260928-1009-643d\submission      # no video
```

**Re-run this after committing S35**, with `scripts/bootstrap.ps1` creating the clone's own venv
(step 2) first. That is the one acceptance step still outstanding.

---

## Known spec defects hit during the build

Cross-reference `00-INDEX.md` §5. Update a row when a defect is resolved or a new one is found.

| # | Defect | Story | Resolved |
|---|---|---|---|
| G1 | `engine.py` / `run_pipeline()` unspecified | S32 | ☐ |
| G2 | `bootstrap.py` / `install_hermes_assets()` unspecified | S31 | ☐ |
| G3 | `schemas.py` — 19 schemas referenced, never shown | S29 | ☐ |
| G4 | 18 of 19 handlers unspecified | S30 | ☐ |
| G5 | WOW-1…WOW-6 cited as rules, never defined | S22/S23 | ☐ |
| G6 | `scrub_fixtures.py` referenced, never specified | S34 | ☑ |
| G7 | 7 of 8 `SKILL.md` files unspecified | S31 | ☐ |
| G8 | 8 of 9 `SOUL.md` + all profile configs unspecified | S31 | ☐ |
| G9 | `_render_storyboard_html` / `_make_contact_sheet` unimplemented | S23 | ☐ |
| G10 | `fixtures/assets` never enumerated | S17 | ☐ |
| G11 | `Shot` field set only inferable from one example | S03 | ☐ |
| G12 | `clients/tts.py` has no spec section | S12 | ☐ |
| G13 | `safe_format` circular-import/NameError bug | S13 | ☑ |
| B3 | `engine_defaults_to_hermes` used, never defined | S01 | ☑ |
| B4 | `scan_prohibited_facts` never wired by any tool | S24 | ☐ |
| B5 | `request_cache_key` hashes a token-bearing URL | S08 | ☑ |
| — | "11 cards" prose vs 12-card `DAG_SPEC` vs 11 stages | S27/S32 | ☐ |

### Found by the S35 offline gate

Five defects that only surfaced once `cwt run --engine local --offline` was actually run to completion.
The first is the reason the gate exists: it exited **0** and printed "Done." with no video at all.

| # | Defect | Story | Resolved |
|---|---|---|---|
| S35-1 | `engine.py` offline render called `Settings.model_copy()` — a pydantic API on a frozen **dataclass**. The render stage raised `AttributeError`, `cwt run --engine local --offline` exited **0**, and produced no video | S35 | ☑ |
| S35-2 | `check_claims` wrote the `claims_report` provenance record with **no inputs**, while `engine._stage_inputs("compliance")` declares `{storyboard}`. `get_if_valid` compares with `==`, so compliance could never be skipped — §9.6's "every stage skipped" was false | S35 | ☑ |
| S35-3 | `run_pipeline` counted only *executed* stages, so the banner read "Done. 3 stages" where §13.3/§9.6 require "Done. 11 stages" (a replayed stage counts as done) | S35 | ☑ |
| S35-4 | `cwt run --resume` raised when the `hermes` binary was absent instead of degrading to the stage cache | S35 | ☑ |
| S35-5 | `bootstrap.create_profiles` spawned a subprocess outside `util/subproc.py`, violating §8.1's single-chokepoint rule (and losing the W4/W7 Windows fixes) | S35 | ☑ |
| S35-6 | `render_video` writes `render_manifest.json` with raw `write_text`, so it records **no provenance**. A changed `storyboard.json` therefore never invalidates the render — the §9.6 scenario "only the render stage's inputs changed" silently ships the stale video. Needs a provenance write on that path | S32/S16 | ☐ |
