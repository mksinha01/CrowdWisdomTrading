# CWT Video Ads Agent — Implementation Story Index

> **What this is.** `doc/video-ads-agent.md` is a 5,509-line one-shot build spec. It is complete, but
> it cannot be built in one LLM session — the spec alone is ~65k tokens, and the build is ~9–10k lines
> of Python across ~90 files. This directory splits that build into **36 implementation stories**,
> each sized to fit comfortably inside one LLM context window, each independently verifiable.

---

## 1. How to use this

**One story = one session.** For story `S##`:

1. Read **only** `doc/stories/S##-*.md`. Do not read the whole spec.
2. Read **only** the spec line ranges that story names. Every story cites exact line ranges.
3. Build exactly what the story says. Do not look ahead, do not build the next story's files.
4. Run the story's **Done when** commands. A story is not finished until they pass.
5. Append a line to `doc/stories/PROGRESS.md`.

**Never read the full spec in one go.** It exceeds the working budget once you add code and test
output. The line-range citations exist precisely so you do not have to.

### Reading a story file

Every story file has the same 9 sections:

| Section | Meaning |
|---|---|
| **Header** | Phase, dependencies, blocks, spec line range, context budget, files produced |
| **Goal** | One paragraph: what exists after this story that did not before |
| **Interface contract** | Exact public names and signatures. **These are frozen** — later stories import them |
| **Rules that bind this story** | The subset of §12 rules this story can violate, with the rule ids |
| **Build steps** | Ordered, concrete |
| **Decisions the spec leaves open** | Real gaps. Decide, write the decision down, move on |
| **Done when** | Runnable verification. Copy-paste, not prose |
| **Handoff** | What the next story is allowed to assume |

---

## 2. Context budget rules

These keep each story inside one window:

| Budget | Limit |
|---|---|
| Spec slice to read | ≤ 400 lines (~5k tokens) |
| This story file | ~70–110 lines (~1.5k tokens) |
| Code to write | 250–800 LOC (~4–10k tokens) |
| Test/verification output | ≤ 2k tokens |
| **Total working set** | **≤ 25k tokens** |

**If a story's spec slice exceeds 400 lines, it has been split.** If you find yourself needing to
read a second story's file to build the first, that is a dependency bug — report it rather than
working around it.

**The 800-LOC ceiling is real.** Four stories are at it (`S03`, `S14`, `S29`, `S32`). Those stories
carry a `⚠ LARGE` marker and an explicit split point.

---

## 3. Dependency graph

Build in phase order. Within a phase, stories with no arrows between them are independent.

```
PHASE 0 — Foundation
  S01 ──┬─▶ S02
        │
PHASE 1 — Domain contracts        (needs S01, S02)
  S03 ──▶ S04 ──┬─▶ S05
                ├─▶ S06
                └─▶ S07

PHASE 2 — Clients                 (needs S02, S03, S04)
  S08 ──▶ S09 ──┬─▶ S10
                ├─▶ S11
                └─▶ S12

PHASE 3 — Prompts                 (needs S01 only — pure strings)
  S13

PHASE 4 — Video                   (needs S02, S03, S04, S14-parts ordered)
  S14 ──┬─▶ S15 ──▶ S16
        ├─▶ S17
        └─▶ S18

PHASE 5 — Tool surface            (needs PHASE 2 + 3 + 4)
  S19 ──▶ S20 ──▶ S21 ──▶ S22 ──▶ S23 ──▶ S24 ──▶ S25 ──▶ S26
  (each tool module is independent; the arrow is build order, not import order)

PHASE 6 — Hermes integration      (needs PHASE 5)
  S27 ──▶ S28
  S29 ──▶ S30        (plugin: needs S19–S26 names frozen)
  S31                (profiles/skills/bootstrap: needs S27, S29, S30)

PHASE 7 — Entry & orchestration   (needs everything above)
  S32 ──▶ S33 ──▶ S34

PHASE 8 — Proof & submission
  S35 ──▶ S36
```

**Critical path:** S01 → S02 → S03 → S04 → S09 → S19 → S20 → S22 → S24 → S26 → S29 → S30 → S32 → S33 → S35.

---

## 4. The 36 stories

| # | Story | Phase | Files produced | Deps | Spec lines | ⚠ |
|---|---|---|---|---|---|---|
| S01 | Repo skeleton, packaging & config | 0 | `pyproject.toml`, `requirements*.txt`, `.env.example`, `.gitignore`, `.gitattributes`, `Makefile`, `Dockerfile`, `src/cwt/__init__.py`, `src/cwt/config.py` | — | 56–364, 886–973 | |
| S02 | Util layer | 0 | `util/jsonio.py`, `util/paths.py`, `util/retry.py`, `util/subproc.py` | S01 | 2029–2148, 3236–3334 | |
| S03 | Artifact schema models | 1 | `domain/models.py` (models half) | S02 | 367–884 | ⚠ LARGE |
| S04 | Storyboard validators | 1 | `domain/models.py` (validators half), `tests/test_models.py` | S03 | 623–785 | |
| S05 | Beat taxonomy & aggregation | 1 | `domain/beats.py` | S04 | 4468–4497 | |
| S06 | Deterministic claims engine | 1 | `domain/claims.py`, `tests/test_claims.py` | S04 | 2621–2810 | |
| S07 | Artifact store & provenance | 1 | `domain/artifacts.py`, `tests/test_artifacts.py` | S04 | 374–405 | |
| S08 | HTTP fixture cache / `--offline` | 2 | `clients/http_cache.py`, `tests/test_http_cache.py` | S02 | 2521–2619 | |
| S09 | LLM client | 2 | `clients/llm.py`, `tests/test_llm_repair.py` | S02, S03 | 2812–3136 | |
| S10 | Apify / Meta Ads Library client | 2 | `clients/apify.py` | S08, S09 | 2237–2402 | |
| S11 | Tavily + Exa clients | 2 | `clients/tavily.py`, `clients/exa.py` | S08 | 2404–2519 | |
| S12 | TTS client & transcript | 2 | `clients/tts.py` | S02 | §2 lines 277–282 | |
| S13 | Prompt package | 3 | `prompts/*.py` (6 files) | S01 | 1059–1657 | |
| S14 | ffmpeg resolution, backend chain, filtergraph | 4 | `video/ffmpeg_bin.py`, `video/backend.py`, `video/filtergraph.py`, `tests/test_filtergraph.py` | S03, S04 | 3138–3234, §12 V1–V4 | ⚠ LARGE |
| S15 | Local ffmpeg backend A — shot render | 4 | `video/local_ffmpeg.py` (part A) | S14 | 5261–5372 | |
| S16 | Local ffmpeg backend B — mix, probe, manifest | 4 | `video/local_ffmpeg.py` (part B) | S15 | 862–882 | |
| S17 | Asset sourcer & fixture assets | 4 | `video/assets.py`, `fixtures/assets/**` | S08 | §1 lines 167–169 | |
| S18 | Optional backends — HyperFrames, OpenMontage | 4 | `video/hyperframes.py`, `video/openmontage.py` | S14 | §5 lines 284–291 | |
| S19 | Tool surface — ads | 5 | `tools/__init__.py`, `tools/ads.py` | S07, S10, S13 | 4059–4082 | |
| S20 | Tool surface — patterns | 5 | `tools/patterns.py` | S19 | 474–523 | |
| S21 | Tool surface — research | 5 | `tools/research.py` | S19 | 525–621 | |
| S22 | Tool surface — storyboard A | 5 | `tools/storyboard.py` (part A) | S21 | 623–785 | |
| S23 | Tool surface — storyboard B | 5 | `tools/storyboard.py` (part B), `tools/storyboard.html` template | S22 | 4397–4466 | |
| S24 | Tool surface — claims | 5 | `tools/claims.py` | S06, S22 | 826–860 | |
| S25 | Tool surface — video | 5 | `tools/video.py` | S16, S17, S12 | 862–882 | |
| S26 | Tool surface — bundle | 5 | `tools/bundle.py` | S24, S25 | §18 lines 5444–5451 | |
| S27 | Hermes CLI wrapper & DAG | 6 | `hermes/cli.py`, `hermes/dag.py`, `tests/test_dag.py` | S02 | 2150–2235, 3343–3580 | |
| S28 | Kanban board operations | 6 | `hermes/board.py`, `hermes/record.py` | S27 | 3582–3817 | |
| S29 | Plugin manifest & tool schemas | 6 | `hermes/plugins/cwt/plugin.yaml`, `schemas.py` | S19–S26 | 1722–1784, 4059–4082 | ⚠ LARGE |
| S30 | Plugin registration & handlers | 6 | `hermes/plugins/cwt/__init__.py`, `handlers.py` | S29 | 1786–1910 | |
| S31 | Profiles, skills & `cwt bootstrap` | 6 | `hermes/profiles/**` (27 files), `skills/**` (8 files), `src/cwt/bootstrap.py` | S27, S30 | 1912–2020 | |
| S32 | Pipeline engine | 7 | `src/cwt/engine.py` | all of P0–P6 | 3819–4030 | ⚠ LARGE · **DESIGN GAP** |
| S33 | CLI entrypoint & doctor | 7 | `src/cwt/cli.py`, `src/cwt/doctor.py` | S32 | 3819–3965, 4114–4312 | |
| S34 | Scripts & fixture recording | 7 | `scripts/*` (5 files), `fixtures/http/**` | S33 | 975–1036, 4352–4395 | |
| S35 | End-to-end proof & test suite | 8 | `tests/**` completion | S34 | 5384–5097 | |
| S36 | README, docs & submission bundle | 8 | `README.md`, `NOTICE`, `LICENSE`, `README-SUBMISSION.md` | S35 | 5003–5100, 5399–5458 | |

---

## 5. Gap register — things the spec does NOT specify

The spec claims to be complete. It is not. These are the real holes. **Each has a story that owns
resolving it.** Do not stall on a gap — decide it in the owning story, write the decision into that
story file under "Decisions", and move on.

| # | Gap | Impact | Owner |
|---|---|---|---|
| G1 | `src/cwt/engine.py` / `run_pipeline()` is **called** by `cli.py:3878` but never specified anywhere | Blocks the entire pipeline. This is the single largest hole | **S32** |
| G2 | `src/cwt/bootstrap.py` / `install_hermes_assets()` called at `cli.py:3952`, never specified | `cwt bootstrap` cannot work without it | **S31** |
| G3 | `hermes/plugins/cwt/schemas.py` — 19 schemas referenced by name only, never shown | Plugin cannot register tools without it | **S29** |
| G4 | `handlers.py` shows 1 of 19 handlers ("…Nineteen in total") | 18 handlers must be derived from §10.2 | **S30** |
| G5 | **WOW-1 … WOW-6** are cited as rules (`WOW-3` splice, `WOW-5` archetype boost, `WOW-6` grade) but never defined | The "wow" requirements are unstated. Must be reconstructed | **S22/S23** |
| G6 | `scripts/scrub_fixtures.py` referenced in `.gitignore:339`, never specified | Token-scrubbing of fixtures is unowned | **S34** |
| G7 | 7 of 8 `SKILL.md` files: "follow the identical structure", no content | Agent procedures are unspecified | **S31** |
| G8 | 8 of 9 profile `SOUL.md` + every profile `config.yaml`/`.env` | Persona behaviour unspecified | **S31** |
| G9 | `tools/storyboard.py`: `_render_storyboard_html` / `_make_contact_sheet` named, only the HTML template shown | Report that two functions are unimplemented | **S23** |
| G10 | `fixtures/assets`: "12 CC0 stills, 3 CC0 clips, 1 music bed, 1 Piper voice" — never enumerated | Offline mode has no assets | **S17** |
| G11 | `domain/models.py` `Shot` field set only inferable from the §3.4 example JSON | Model drift between S03 and S22 | **S03** |
| G12 | `clients/tts.py` is in the file tree but has no section | TTS chain semantics unspecified | **S12** |
| G13 | `prompts/__init__.py` imports `safe_format` from itself but `extract.py` uses it unimported | Circular-import / NameError bug in the spec as written | **S13** |

### Spec bugs to fix while building (not gaps — defects)

| # | Defect | Location | Fix in |
|---|---|---|---|
| B1 | `prompts/extract.py` calls `safe_format(...)` but never imports it; `__init__.py` defines it and imports *from* `extract` | spec 1077–1080, 1165 | S13 — put `safe_format` in a new `prompts/_format.py`, import from both |
| B2 | `clients/llm.py` references `ArtifactValidationError` at line 3066 but defines it at 3115 — fine in Python, but `append_jsonl`/`now_iso` are called at 3102 with no import shown | spec 3099–3107 | S09 — import from `util.jsonio` |
| B3 | `doctor.py` `_check_gateway` uses `settings` but `run_doctor` only appends it when `settings.engine_defaults_to_hermes` — a field never defined in `.env.example` | spec 4251, 4291 | S33 — define on `Settings` in S01 |
| B4 | `domain/claims.py` `scan_prohibited_facts` is called by the spec's own `prohibited_facts` contract but no tool wires it | spec 2783, 826–842 | S24 — `cwt_check_claims` must call it |
| B5 | `http_cache.request_cache_key` hashes the **raw** URL, which for Apify contains `?token=` | spec 2557 | S08 — callers must pass a token-scrubbed URL; assert it |

---

## 6. Verification protocol

**Per story.** The story's "Done when" block. No story is complete on assertion alone.

**Per phase.** After the last story of each phase:

| Phase | Gate |
|---|---|
| 0 | `python -c "from cwt.config import Settings; Settings.from_env()"` succeeds; `pytest tests/ -q` runs (0 tests is fine) |
| 1 | `pytest tests/test_models.py tests/test_claims.py tests/test_artifacts.py -q` green |
| 2 | `pytest tests/test_llm_repair.py tests/test_http_cache.py -q` green; no test makes a live call |
| 3 | `python -c "from cwt.prompts import *; print(len(AD_EXTRACTION_PROMPT))"` |
| 4 | `pytest tests/test_filtergraph.py -q` green; `ffmpeg_path()` resolves |
| 5 | Every tool importable; every tool returns a JSON string, never raises |
| 6 | `cwt doctor` — all green |
| 7 | `cwt run --engine local --offline` produces `render/final.mp4` |
| 8 | Clean-clone offline run green; `submission/` complete |

**The governing invariant, from §13:** *"The `--offline` run must work before you attempt a live
one."* Phase 8's gate is the submission requirement — a reviewer must be able to rerun the code
without spending their credits.

---

## 7. Rules that apply to EVERY story

Non-negotiable, from §12. Every story file re-lists the subset it can violate, but these hold always:

- **W1** — `shell=False`; external commands take a list, through `util/subproc.run_tool()` only.
- **W7** — utf-8 everywhere; `encoding="utf-8", errors="replace"` + `PYTHONUTF8=1` on every child.
- **R1** — credentials never reach an artifact or a log line. Scrub `?token=` at the boundary.
- **A1** — a Hermes tool handler **never raises**. Always returns a JSON string.
- **A6** — budget is enforced *inside* the client, on the call that crosses the cap.
- **C1/C2** — a hard compliance finding is final. The LLM judge may escalate, never de-escalate.
- **K2** — a card `assignee` must exactly match a Hermes profile name.

---

## 8. Deliberate non-goals

Stated so no story drifts into them:

- **No web application.** The Hermes Kanban dashboard is the UI (§11). `storyboard.html` is the only
  bespoke page, and it is one templated file with no build step.
- **No SQL database.** Artifacts are JSON on disk; Hermes owns `kanban.db` and we never touch it.
- **No Apify SDK.** httpx against the documented REST API, so the contract is visible in our code.
- **No `moviepy`.** Filtergraph strings are written directly — pure and golden-testable.
- **No fifth video backend.** `VIDEO_BACKEND_CHAIN` must end in `local_ffmpeg`.
