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
| S34 | Scripts & fixture recording | todo | | `scrub_fixtures.py --check`; no `0.0.0.0` |

## Phase 8 — Proof & submission

| # | Story | Status | Commit | Verified by |
|---|---|---|---|---|
| S35 | End-to-end proof & test suite | todo | | clean clone, `env -i`, offline render |
| S36 | README, docs & submission | todo | | no secrets in public docs; bundle complete |

---

## Recorded verifications

Paste the **actual output** of each phase gate here. This is the audit trail.

### S35 — the clean-clone offline run (the submission acceptance test)

```
# to be filled in: git clone → bootstrap → env -i cwt run --engine local --offline
```

### S35 — the `--resume` zero-cost promise

```
# to be filled in: cwt run --resume → all stages skipped, $0.0000
```

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
| G6 | `scrub_fixtures.py` referenced, never specified | S34 | ☐ |
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
