# CWT Video Ads Agent — Complete Manual

> This document explains every moving part of the **CrowdWisdomTrading Video Ads Agent** system — what each piece does, how it connects to the others, and how to operate it manually step by step.

---

## Table of Contents

1. [What the System Is](#1-what-the-system-is)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Directory Structure](#3-directory-structure)
4. [Environment & Configuration (`.env` / `config.py`)](#4-environment--configuration)
5. [The `cwt` CLI — All Commands](#5-the-cwt-cli--all-commands)
6. [Bootstrap (`cwt bootstrap`)](#6-bootstrap-cwt-bootstrap)
7. [Preflight Diagnostics (`cwt doctor`)](#7-preflight-diagnostics-cwt-doctor)
8. [The Pipeline Engine (`engine.py`)](#8-the-pipeline-engine-enginepy)
9. [The 12 Pipeline Stages (DAG)](#9-the-12-pipeline-stages-dag)
10. [The Nine Hermes Agent Profiles](#10-the-nine-hermes-agent-profiles)
11. [The Tools Layer](#11-the-tools-layer)
12. [Clients Layer (External APIs)](#12-clients-layer-external-apis)
13. [Video Rendering Pipeline](#13-video-rendering-pipeline)
14. [Compliance Gate](#14-compliance-gate)
15. [Hermes Board Operations](#15-hermes-board-operations)
16. [Offline Mode & Fixtures](#16-offline-mode--fixtures)
17. [The Submission Bundle](#17-the-submission-bundle)
18. [Scripts & Helpers](#18-scripts--helpers)
19. [Run Directory Structure](#19-run-directory-structure)
20. [Cost Accounting](#20-cost-accounting)
21. [Troubleshooting](#21-troubleshooting)
22. [End-to-End Walkthrough](#22-end-to-end-walkthrough)

---

## 1. What the System Is

CWT is a **multi-agent autonomous pipeline** that produces a **30–60 second cinematic vertical video ad** (1080×1920, H.264/AAC) for [crowdwisdomtrading.com](https://crowdwisdomtrading.com/).

The system:
- Scrapes winning competitor ads from the **Meta Ads Library** via Apify
- Extracts hook archetypes, emotional beats, and beat-sheet timelines
- Runs **tri-angle market research** in parallel (Tavily + Exa)
- Writes 3 storyboard variants, scores them, and iterates with a Creative Director
- Runs a **deterministic financial compliance audit** before any pixel is rendered
- Renders a full video with **synthetic voiceover** through a multi-tier backend chain
- Runs post-render **audio/video QA** (loudness, duration, aspect ratio, claims re-check)
- Assembles everything into a **submission bundle**

The entire pipeline is coordinated by nine **specialized Hermes AI agents** operating on an interactive Kanban board.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  cwt CLI  (src/cwt/cli.py)                                      │
│   cwt run │ cwt doctor │ cwt bootstrap │ cwt status │ cwt seed   │
└───────────────────────┬────────────────────────────────────────-┘
                        │
              ┌─────────▼──────────┐
              │  engine.py          │  ← Orchestrates the 11 stages
              │  (run_pipeline)     │
              └─────────┬──────────┘
               ┌────────┴─────────┐
               │                  │
        ┌──────▼──────┐    ┌──────▼──────────────────┐
        │ local engine │    │ hermes engine            │
        │ (asyncio)    │    │ (Kanban board + agents)  │
        └──────┬──────┘    └──────────────────────────┘
               │
   ┌───────────┼────────────────────────┐
   │      Tools Layer                   │
   │  ads │ patterns │ research         │
   │  storyboard │ claims │ video │ bundle │
   └───────────┬────────────────────────┘
               │
   ┌───────────┼────────────────────────┐
   │      Clients Layer                 │
   │  LLMClient │ ApifyClient           │
   │  TavilyClient │ ExaClient          │
   │  TTSClient │ HttpCache             │
   └───────────┬────────────────────────┘
               │
   ┌───────────▼────────────────────────┐
   │  Video Backend Chain               │
   │  HyperFrames → OpenMontage → local_ffmpeg │
   └────────────────────────────────────┘
```

---

## 3. Directory Structure

```
CrowdWisdomTrading/
├── src/cwt/                  ← Python package (installed as "cwt")
│   ├── cli.py                ← Entry point for all `cwt` commands
│   ├── engine.py             ← Pipeline orchestrator (the brain)
│   ├── config.py             ← ONLY module allowed to read env vars
│   ├── bootstrap.py          ← Creates Hermes profiles, skills, plugin
│   ├── doctor.py             ← Preflight health checker
│   ├── tools/                ← High-level stage logic
│   │   ├── ads.py            ← Source & rank winning ads
│   │   ├── patterns.py       ← Extract hook archetypes & beat sheets
│   │   ├── research.py       ← Run research angles + assemble brief
│   │   ├── storyboard.py     ← Generate variants, judge, rewrite
│   │   ├── claims.py         ← Compliance checking & rewriting
│   │   ├── video.py          ← TTS synthesis + render + QA
│   │   └── bundle.py         ← Submission bundle assembler
│   ├── clients/              ← External API wrappers
│   │   ├── llm.py            ← LLMClient (OpenRouter / NVIDIA)
│   │   ├── apify.py          ← ApifyClient (Meta Ads Library)
│   │   ├── tavily.py         ← TavilyClient (web search)
│   │   ├── exa.py            ← ExaClient (neural search)
│   │   ├── tts.py            ← TTSClient (edge_tts / piper / silent)
│   │   └── http_cache.py     ← Filesystem HTTP fixture cache
│   ├── hermes/               ← Hermes agent orchestration
│   │   ├── dag.py            ← DAG_SPEC: the 12 card definitions
│   │   ├── board.py          ← Kanban CRUD + wait_for_completion
│   │   ├── cli.py            ← hermes binary wrapper
│   │   └── plugins/          ← Native Hermes plugin (cwt:* tools)
│   ├── video/                ← FFmpeg rendering pipeline
│   │   ├── local_ffmpeg.py   ← Primary render backend
│   │   ├── filtergraph.py    ← FFmpeg filter graph builder
│   │   ├── assets.py         ← Asset resolution (stock, solid, text)
│   │   ├── backend.py        ← Backend chain builder
│   │   ├── ffmpeg_bin.py     ← FFmpeg binary discovery
│   │   ├── hyperframes.py    ← HyperFrames backend (optional)
│   │   └── openmontage.py    ← OpenMontage backend (optional)
│   ├── domain/               ← Pydantic schemas & ArtifactStore
│   ├── prompts/              ← All LLM prompt templates
│   └── util/                 ← Shared utilities (paths, json, subproc)
│
├── hermes/                   ← Hermes asset definitions
│   ├── config.yaml           ← Hermes gateway config (kanban settings)
│   ├── profiles/             ← 9 agent persona directories
│   └── plugins/              ← cwt Hermes plugin
│
├── fixtures/                 ← Pre-recorded test data
│   ├── http/                 ← Recorded HTTP responses (Apify, search)
│   ├── artifacts/            ← Staged artifacts for offline replay
│   └── assets/               ← Stock video clips, voice models
│
├── runs/                     ← Pipeline run outputs (auto-created)
│   └── <run_id>/
│       ├── artifacts/        ← Stage JSON artifacts
│       ├── render/           ← final.mp4 + voiceover
│       ├── llm_ledger.jsonl  ← Per-call LLM cost log
│       └── submission/       ← Submission bundle
│
├── submission/               ← Final submission (latest run)
├── scripts/                  ← Helper scripts
├── doc/stories/              ← 36 implementation story files
├── .env.example              ← Template for environment variables
├── .env                      ← Your secrets (gitignored)
├── pyproject.toml            ← Package definition + entry point
└── Makefile                  ← Dev shortcuts
```

---

## 4. Environment & Configuration

**File:** [`src/cwt/config.py`](file:///c:/SSD%20WINDOW/code/CrowdWisdomTrading/src/cwt/config.py)

### 4.1 The Rules

- `config.py` is the **only** module in the entire codebase permitted to read environment variables or load `.env`.
- **Settings precedence** (highest first):
  1. Runtime shell/Docker environment variables
  2. `.env` file in the project root
  3. Defaults baked into `config.py`
- Hermes has its **own separate** config chain (`~/.hermes/config.yaml`) — the `.env` file does **not** configure the Hermes worker model.

### 4.2 Setting up `.env`

```bash
cp .env.example .env
# Edit .env and fill in your API keys
```

### 4.3 Key Configuration Variables

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `openrouter` | LLM gateway: `openrouter` or `nvidia` |
| `OPENROUTER_API_KEY` | *(required)* | OpenRouter key from openrouter.ai/keys |
| `LLM_MODEL_CHEAP` | `google/gemini-2.5-flash` | Model for ~35 extraction/scoring calls per run |
| `LLM_MODEL_STRONG` | `anthropic/claude-sonnet-4.5` | Model for ~5 creative writing calls per run |
| `LLM_MAX_CONCURRENCY` | `4` | Parallel LLM calls (safe for 40 RPM free tier) |
| `APIFY_TOKEN` | *(required)* | Apify token from console.apify.com |
| `APIFY_ADS_ACTOR_ID` | `apify~facebook-ads-scraper` | ⚠️ **Tilde not slash** in the actor ID |
| `APIFY_MAX_CHARGE_USD` | `1.00` | *(required)* Hard spend cap per run |
| `TAVILY_API_KEY` | *(required)* | Tavily key (free tier: 1,000 credits/month) |
| `EXA_API_KEY` | *(required)* | Exa key ($10 free credit) |
| `VIDEO_BACKEND_CHAIN` | `hyperframes,openmontage,local_ffmpeg` | ⚠️ **MUST end with** `local_ffmpeg` |
| `VIDEO_WIDTH` / `VIDEO_HEIGHT` | `1080` / `1920` | Vertical format |
| `VIDEO_FPS` | `30` | Frame rate |
| `VIDEO_MIN_SECONDS` / `VIDEO_MAX_SECONDS` | `30` / `60` | Duration range |
| `TTS_BACKEND_CHAIN` | `edge_tts,piper,silent` | Voiceover engine chain |
| `CWT_BOARD` | `cwt-ads` | Hermes board name |
| `CWT_MAX_USD` | `2.00` | Total pipeline budget guard |
| `CLAIMS_GATE_ENABLED` | `1` | Set to 0 only for local testing |
| `CLAIMS_MAX_REWRITE_ROUNDS` | `3` | Max compliance rewrite attempts |
| `CREATIVE_MAX_ROUNDS` | `3` | Max creative director review rounds |

### 4.4 Invariants Enforced at Load Time

1. **Rule A2** — `APIFY_MAX_CHARGE_USD` is **required** and must be > 0. The config load fails with a clear error if missing.
2. **Rule V4** — `VIDEO_BACKEND_CHAIN` must end with `local_ffmpeg`. Config load fails if not.
3. **Rule C2** — If `CLAIMS_GATE_ENABLED=0`, a loud warning is printed: *"output unshippable"*.

---

## 5. The `cwt` CLI — All Commands

**File:** [`src/cwt/cli.py`](file:///c:/SSD%20WINDOW/code/CrowdWisdomTrading/src/cwt/cli.py)

The `cwt` command is the single entry point for all operations. It is installed as a script via `pyproject.toml`.

### Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Configuration or preflight error |
| `2` | Pipeline timeout |
| `3` | Pipeline blocked (a card gave up) |
| `4` | LLM budget exceeded |

### 5.1 `cwt run` — Run the full pipeline

```bash
cwt run [OPTIONS]
```

| Flag | Purpose |
|---|---|
| `--engine hermes\|local` | **hermes**: Real Kanban board with AI agents. **local**: In-process Python (no Hermes needed). Default: `hermes` |
| `--offline` | Replay recorded fixtures. Zero API spend. Zero network. |
| `--backend <name>` | Pin a single video backend (e.g. `--backend local_ffmpeg`) |
| `--run-id <id>` | Reuse an existing run directory |
| `--resume` | Unblock blocked cards and continue a prior run |
| `--record-pacing` | Insert deliberate pauses between stages for dashboard recording |
| `--force-stage <key>` | Re-run a specific stage even if its artifact is valid. Repeatable |
| `--timeout <seconds>` | Override the default 5400s pipeline timeout |

**Examples:**
```bash
# Safe zero-cost first run (proves the render works)
cwt run --engine local --offline

# Live run with full AI agents
cwt run

# Resume a stalled live run
cwt run --resume

# Force re-render only (skip all LLM stages)
cwt run --force-stage render --force-stage qa --force-stage collect
```

### 5.2 `cwt doctor` — Preflight diagnostics

```bash
cwt doctor [--json]
```

Runs 7–8 checks and prints a colored report. All checks must be green before spending money.

### 5.3 `cwt bootstrap` — Install Hermes assets

```bash
cwt bootstrap
```

Creates the 9 agent profiles, installs 8 skills, and registers the native CWT Hermes plugin. Safe to run multiple times (idempotent).

### 5.4 `cwt seed` — Create kanban cards without running

```bash
cwt seed --run-id <run_id>
```

Seeds the board with all 12 cards for manual inspection. Useful for debugging the DAG topology.

### 5.5 `cwt status` — Show live board state

```bash
cwt status
```

Prints a Rich table of all cards with their current status (done/running/ready/blocked).

### 5.6 `cwt clean` — Remove old run directories

```bash
cwt clean
```

Deletes all run directories in `runs/` except the most recent one.

---

## 6. Bootstrap (`cwt bootstrap`)

**File:** [`src/cwt/bootstrap.py`](file:///c:/SSD%20WINDOW/code/CrowdWisdomTrading/src/cwt/bootstrap.py)

`cwt bootstrap` installs everything Hermes needs to run the pipeline. It does three things:

### 6.1 Creates 9 Agent Profiles

For each agent, it calls `hermes profile create <name>` with:
- A **system prompt** ("soul") tailored to that agent's role
- The **tool namespaces** they are permitted to call
- The **model tier** (cheap or strong)

| Profile | Toolsets | Tier |
|---|---|---|
| `cwt-orchestrator` | `kanban` | cheap |
| `cwt-ads-manager` | `kanban`, `cwt:ads` | cheap |
| `cwt-hook-analyst` | `kanban`, `cwt:patterns` | cheap |
| `cwt-researcher` | `kanban`, `cwt:research` | cheap |
| `cwt-script-writer` | `kanban`, `cwt:storyboard` | **strong** |
| `cwt-creative-director` | `kanban`, `cwt:review` | cheap |
| `cwt-compliance` | `kanban`, `cwt:claims` | cheap |
| `cwt-video-editor` | `kanban`, `cwt:video` | cheap |
| `cwt-qa` | `kanban`, `cwt:probe`, `cwt:claims` | cheap |

### 6.2 Installs 8 Skills

Each skill is a markdown file that gives the agent a step-by-step recipe for its primary task:

| Skill | Used by |
|---|---|
| `cwt-source-winning-ads` | ads-manager |
| `cwt-extract-ad-patterns` | hook-analyst |
| `cwt-research-angle` | researcher |
| `cwt-write-storyboard` | script-writer |
| `cwt-creative-review` | creative-director |
| `cwt-claims-gate` | compliance |
| `cwt-render-video` | video-editor |
| `cwt-final-qa` | qa |

### 6.3 Registers the CWT Plugin

Installs the native `cwt` Hermes plugin, which exposes all `cwt_*` Python functions as Hermes tools (the `cwt:ads`, `cwt:patterns`, `cwt:research`, etc. namespaces).

### 6.4 Rule K2 Verification

After setup, verifies that **every card assignee in `DAG_SPEC` has a matching installed profile**. This is the "Assignee name must match profile name exactly" invariant — a mismatch causes cards to sit on `ready` forever.

---

## 7. Preflight Diagnostics (`cwt doctor`)

**File:** [`src/cwt/doctor.py`](file:///c:/SSD%20WINDOW/code/CrowdWisdomTrading/src/cwt/doctor.py)

Runs before any money is spent. Every check either passes (OK), warns (WARN), or fails with an **actionable fix message**.

### 7.1 Checks Performed

| Check | What it verifies | Failure action |
|---|---|---|
| **python** | Python ≥ 3.11 | Fail with install hint |
| **ffmpeg** | FFmpeg binary found + ffprobe available | Warn if ffprobe missing, fail if ffmpeg missing |
| **hermes** | Hermes binary on PATH + all required kanban flags present | Fail with install URL |
| **worker model** | Hermes worker model is set (`hermes config get model`) | Warn with `hermes model` instruction |
| **profiles** | All 9 CWT profiles exist (`hermes profile list`) | Fail with `cwt bootstrap` hint |
| **backend chain** | Backend chain probed for availability | Shows per-backend OK/-- lines |
| **llm models** | LLM slugs validated against `/v1/models` API | Fail if any slug missing |
| **gateway** | Hermes dispatcher reachable (hermes engine only) | Warn with `hermes gateway start` hint |

### 7.2 Example Output (All Green)

```
OK   python           3.12.7
OK   ffmpeg           C:\...\ffmpeg.exe (9.0.1)
OK   hermes           vHermes Agent v0.21.5
OK   worker model     anthropic/claude-opus-4.6  (verify >=64k context)
OK   profiles         18 CWT profiles found
OK   backend chain    -- hyperframes    npx unavailable
                      -- openmontage    OPENMONTAGE_HOME not set
                      OK local_ffmpeg
OK   llm models       4 slugs verified
OK   gateway          dispatcher reachable

All checks passed.
```

---

## 8. The Pipeline Engine (`engine.py`)

**File:** [`src/cwt/engine.py`](file:///c:/SSD%20WINDOW/code/CrowdWisdomTrading/src/cwt/engine.py)

`run_pipeline()` is the master orchestrator. It is called by `cwt run` and returns a summary dict.

### 8.1 Two Execution Engines

#### Local Engine (`--engine local`)
- Runs all stages **in-process** as Python function calls
- Research stages (`res_pain`, `res_unique`, `res_crowd`) run **concurrently** via `asyncio.gather`
- No Hermes binary required
- Used for offline testing and CI

#### Hermes Engine (`--engine hermes`, default)
- **Seeds** the Kanban board with 12 cards
- **Polls** the board every 15 seconds via `wait_for_completion`
- Real AI agents pick up cards, call tools, and complete them
- Provides the live Kanban dashboard at `http://localhost:8080`

### 8.2 Three Layers of Idempotency

The pipeline never re-spends money for work already done:

1. **Card idempotency key** — `{run_id}:{card_key}` prevents duplicate cards on resume
2. **ArtifactStore.get_if_valid** — Checks if a stage's JSON artifact exists and its input hashes match; if yes, the stage is skipped
3. **HttpCache** — Records HTTP responses to disk; replays them on retry/offline runs

### 8.3 Stage Result States

| Status | Meaning |
|---|---|
| `ok` | Stage executed and produced an artifact |
| `skipped` | Valid artifact already exists; stage bypassed |
| `failed` | Stage raised an error; pipeline stops |

### 8.4 Budget Guard

`BudgetExceeded` is raised by `LLMClient` when cumulative spend exceeds `CWT_MAX_USD`. It propagates uncaught out of `run_pipeline` → `cli.py` maps it to exit code `4`.

### 8.5 Stall Detection

In Hermes mode, if no card changes status for `CWT_STALL_THRESHOLD_SECONDS` (default: 180s):
1. The dispatcher is **nudged** once (one dispatch tick)
2. If still no progress, `PipelineBlocked` is raised with a clear fix message

---

## 9. The 12 Pipeline Stages (DAG)

**File:** [`src/cwt/hermes/dag.py`](file:///c:/SSD%20WINDOW/code/CrowdWisdomTrading/src/cwt/hermes/dag.py)

The pipeline topology is a Directed Acyclic Graph (DAG). It is the **single source of truth** — every topology change is one edit to `DAG_SPEC`.

### 9.1 DAG Topology

```
                                    ┌──▶ res_pain ───┐
root ──▶ ads ──▶ patterns ──────────┼──▶ res_unique ──┼──▶ brief ──▶ script ──▶ compliance ──▶ render ──▶ qa ──▶ collect
                                    └──▶ res_crowd ───┘
                                      (concurrent)
```

### 9.2 Stage-by-Stage Details

#### `root` (Anchor card)
- **Assignee:** `cwt-orchestrator`
- **Purpose:** DAG anchor. Establishes the acceptance criteria. Completes immediately.
- **Acceptance criteria:** All 4 must hold:
  1. `render/final.mp4` exists, is 30–60s, 1080×1920, ffprobe-valid
  2. `storyboard.json` validates and `creative_scores.verdict == 'pass'`
  3. `claims_report.json` verdict == 'pass' on BOTH pre- and post-render passes
  4. `submission/` contains the complete bundle

#### `ads` (Source winning ads)
- **Assignee:** `cwt-ads-manager`
- **Tool:** `ads.source_winning_ads()` → Apify actor scrape → `winning_ads.json`
- **Then:** `ads.rank_winning_ads()` → sorts by longevity signal (days running)
- **Constraint:** Window ≤ 30 days. Spend cap enforced. Minimum 8 ads (warn if fewer, never widen).
- **Max runtime:** 20 minutes, 2 retries

#### `patterns` (Extract ad patterns)
- **Assignee:** `cwt-hook-analyst`
- **Tool:** `patterns.extract_ad_patterns()` → LLM extracts hooks, pain points, beat sheets → `ad_patterns.json`
- **Key output:** `aggregate.median_beat_timeline` — the median beat structure used to validate storyboard timing
- **Max runtime:** 20 minutes

#### `res_pain`, `res_unique`, `res_crowd` (Research — concurrent)
- **Assignee:** `cwt-researcher` (all three)
- **Run concurrently** via `asyncio.gather` (the "money shot" for the Kanban recording)
- Three angles:
  - **pain** — ICP's trading frustrations (Tavily search)
  - **unique_data** — CrowdWisdomTrading's verifiable differentiators + **populates `prohibited_facts`**
  - **crowd_effect** — Academic/market evidence for crowd wisdom vs single-expert forecasting
- Outputs: `angles/pain.json`, `angles/unique_data.json`, `angles/crowd_effect.json`
- **Max runtime:** 12 minutes each

#### `brief` (Assemble research brief)
- **Assignee:** `cwt-researcher`
- **Tool:** `research.assemble_brief()` → Selects (not summarizes) the strongest material → `research_brief.json`
- **Parents:** All three research angles must complete first
- **Max runtime:** 8 minutes

#### `script` (Write storyboard)
- **Assignee:** `cwt-script-writer` (runs on **strong** model)
- **Steps:**
  1. `storyboard.generate_hook_candidates()` — LLM generates hook options
  2. `storyboard.write_storyboard_variant()` × 3 — one per research angle
  3. `storyboard.judge_variants()` — LLM picks the winner
  4. Creative director review loop (up to 3 rounds):
     - `storyboard.score_storyboard()` — scores against rubric (threshold: 8.0/10)
     - `storyboard.apply_rewrite()` — if below threshold
- **Terminal action:** `kanban_request_review` with `cwt-creative-director` as reviewer
- **Max runtime:** 45 minutes, 2 retries

#### `compliance` (Claims gate)
- **Assignee:** `cwt-compliance`
- **Tool:** `claims.check_claims()` → deterministic policy engine
- If violations found: `claims.rewrite_for_compliance()` → re-check (up to 3 rounds)
- If still blocked after 3 rounds: `PipelineBlocked` — the run stops. Never silently passes.
- Outputs: `claims_report.json`
- **Max runtime:** 15 minutes

#### `render` (Video rendering)
- **Assignee:** `cwt-video-editor`
- **Steps:**
  1. `video.synthesize_voiceover()` — TTS through backend chain
  2. `video.render_video()` — FFmpeg through backend chain → `render/final.mp4`
- In `--offline` mode, TTS is forced to `silent` backend
- Output validated with `ffprobe` (Rule V3: exit 0 does not mean success)
- **Max runtime:** 60 minutes, 1 retry

#### `qa` (Final QA)
- **Assignee:** `cwt-qa`
- **Checks:**
  - Duration: 30–60 seconds
  - Aspect ratio: 1080×1920
  - Loudness: −14 LUFS / −1.5 dBTP (broadcast standard)
  - Post-render claims re-check on the **actual TTS transcript** (not the storyboard)
  - Risk disclosure on-screen duration: ≥ 3.0 seconds
- Outputs: `claims_report_post_render.json`
- **Max runtime:** 10 minutes

#### `collect` (Assemble submission)
- **Assignee:** `cwt-ads-manager`
- **Tool:** `bundle.assemble_submission()` → copies everything into `submission/`
- **Produces:** `final.mp4`, `storyboard.json`, `storyboard.html`, `contact_sheet.png`, `render_manifest.json`, `claims_report.json`, `cost_report.json`, `README-SUBMISSION.md`
- **Max runtime:** 5 minutes

---

## 10. The Nine Hermes Agent Profiles

Each agent is a **Hermes profile** — a named AI worker with a system prompt (soul), tool access, and model tier assignment. Profiles live in `hermes/profiles/`.

### Why only the script-writer uses the strong model

Approximately 35 of 40 LLM calls per run are classification, scoring, or extraction tasks. Mid-tier models handle these at 1/10th the cost. The strong frontier model is reserved exclusively for **creative narrative scriptwriting** because that is where quality matters most (Rule A5).

### What agents can see

Every agent reads its card body which contains:
- `RUN_ID` — the unique run identifier
- `RUN_DIR` — absolute path to the run's output directory
- `BOARD` — the Hermes board name
- Instructions to call `kanban_show()` to find parent artifact paths
- The pinned skill name to follow step-by-step

Agents **never hard-code artifact paths** — they discover them through `kanban_show()` reading parent metadata.

---

## 11. The Tools Layer

**Directory:** [`src/cwt/tools/`](file:///c:/SSD%20WINDOW/code/CrowdWisdomTrading/src/cwt/tools)

These are the Python functions that do actual work. They are called both by the **local engine** directly and exposed as **Hermes tools** (via the cwt plugin) for AI agents to call.

### `ads.py` — Ad sourcing

| Function | What it does |
|---|---|
| `source_winning_ads(settings, paths, cache)` | Calls Apify actor to scrape Meta Ads Library. Writes `winning_ads.json` |
| `rank_winning_ads(settings, paths)` | Sorts by longevity (days running). Updates `winning_ads.json` |

### `patterns.py` — Pattern extraction

| Function | What it does |
|---|---|
| `extract_ad_patterns(settings, paths, concurrency, client)` | LLM extracts hook archetypes, pain points, emotions, and beat sheets from each ad. Aggregates median beat timeline. Writes `ad_patterns.json` |

### `research.py` — Market research

| Function | What it does |
|---|---|
| `research_angle(settings, paths, angle, client, cache)` | Runs Tavily + Exa searches for one angle. LLM synthesizes findings. Writes `angles/{angle}.json` |
| `assemble_brief(settings, paths, client, cache)` | Combines all three angles into `research_brief.json`. Includes `prohibited_facts` list |

### `storyboard.py` — Scriptwriting (largest file: 60KB)

| Function | What it does |
|---|---|
| `generate_hook_candidates(settings, paths, client)` | LLM generates 5 hook options. Returns selected hook ID |
| `write_storyboard_variant(settings, paths, angle, hook_id, ...)` | LLM writes a full storyboard for one research angle. Writes to `variants/{angle}.json` |
| `judge_variants(settings, paths, client)` | LLM judges all 3 variants against rubric. Selects winner. Copies to `storyboard.json` |
| `score_storyboard(settings, paths, client)` | Creative Director scores the storyboard (0–10). Returns verdict pass/fail |
| `apply_rewrite(settings, paths, client)` | Applies Creative Director's rewrite instructions. Updates `storyboard.json` in place |

### `claims.py` — Compliance checking (23KB)

| Function | What it does |
|---|---|
| `check_claims(settings, paths, stage, client)` | Deterministic policy engine: checks for prohibited claims. Returns verdict: pass/request_changes/block |
| `rewrite_for_compliance(settings, paths, round_no, client)` | Applies deterministic rewrite instructions. Updates `storyboard.json` |

### `video.py` — Video production

| Function | What it does |
|---|---|
| `synthesize_voiceover(settings, paths)` | TTS through backend chain. Writes `artifacts/voiceover.json` + audio files |
| `render_video(settings, paths)` | FFmpeg through backend chain. Writes `render/final.mp4` |
| `qa_check(settings, paths, client, raise_on_error)` | Post-render QA: duration, aspect, loudness, claims re-check |

### `bundle.py` — Submission assembler (26KB)

| Function | What it does |
|---|---|
| `assemble_submission(settings, paths)` | Copies all artifacts into `runs/<id>/submission/`. Generates `contact_sheet.png` from storyboard frames. Renders `storyboard.html`. Computes `cost_report.json` |

---

## 12. Clients Layer (External APIs)

**Directory:** [`src/cwt/clients/`](file:///c:/SSD%20WINDOW/code/CrowdWisdomTrading/src/cwt/clients)

### 12.1 `LLMClient` (`llm.py`)

OpenAI-compatible client supporting OpenRouter and NVIDIA NIM.

**Key features:**
- **Tier routing:** `tier="cheap"` or `tier="strong"` selects model
- **Concurrency limiting:** `asyncio.Semaphore` with `LLM_MAX_CONCURRENCY`
- **Budget guard:** Tracks cumulative spend via `llm_ledger.jsonl`; raises `BudgetExceeded` when `max_usd` is exceeded
- **JSON repair:** On JSON parse failure, retries up to `LLM_JSON_REPAIR_ATTEMPTS` times with a repair prompt
- **Fallback models:** On failure, falls through `model_fallbacks` list
- **Offline mode:** In `--offline`, returns fixture responses without making network calls

### 12.2 `ApifyClient` (`apify.py`)

Wraps the Apify API for running the `facebook-ads-scraper` actor.

**Key features:**
- Actor ID uses **tilde syntax** (`apify~facebook-ads-scraper`), not slash (common mistake)
- `maxTotalChargeUsd` enforces the hard spend cap on Apify's side
- Fallback actor IDs tried in order if primary fails
- HTTP responses cached to `cache/apify/` for replay

### 12.3 `TavilyClient` (`tavily.py`) and `ExaClient` (`exa.py`)

Web search clients for the research angles.

- **Tavily:** `search_depth=advanced` costs 2 credits. Used for recent news and ICP pain research
- **Exa:** Neural/semantic search. Used for crowd wisdom academic evidence
- Both cache responses in `cache/` for replay

### 12.4 `TTSClient` (`tts.py`)

Text-to-speech with a backend chain:

| Backend | Description |
|---|---|
| `edge_tts` | Microsoft Edge TTS (online, free). Voice: `en-US-AndrewNeural` |
| `piper` | Local neural TTS. Requires `en_US-ryan-high.onnx` voice model |
| `silent` | Generates a silent audio file. Used in `--offline` mode |

### 12.5 `HttpCache` (`http_cache.py`)

Filesystem HTTP fixture cache.

- Stores serialized HTTP responses keyed by URL + payload hash
- In **read-write** mode: cache miss → real HTTP request → store response
- In **offline** mode: cache miss → raises `OfflineFixtureMissing` immediately

---

## 13. Video Rendering Pipeline

**Directory:** [`src/cwt/video/`](file:///c:/SSD%20WINDOW/code/CrowdWisdomTrading/src/cwt/video)

### 13.1 Backend Chain

The `VIDEO_BACKEND_CHAIN` is tried left to right. The last element **must** be `local_ffmpeg` — this is a hard invariant enforced at config load time.

| Backend | Availability | Notes |
|---|---|---|
| `hyperframes` | Requires Node.js + `npx hyperframes` | Optional premium backend |
| `openmontage` | Requires `OPENMONTAGE_HOME` set | Optional AGPL backend |
| `local_ffmpeg` | Always available (bundled via `imageio-ffmpeg`) | **The anchor — never fails** |

### 13.2 Local FFmpeg Rendering (`local_ffmpeg.py`, 35KB)

The primary render path. Given `storyboard.json`, it:

1. **Resolves assets** for each shot (stock clip, gradient, solid color, or text overlay)
2. **Synthesizes voiceover** via `TTSClient`
3. **Builds a filtergraph** for each shot (zoompan, text burn-in, color overlays)
4. **Renders each shot** individually to a temporary clip
5. **Concatenates** all shots + mixes audio (voiceover + background music)
6. **Applies loudness normalization** (−14 LUFS, −1.5 dBTP) via a two-pass `loudnorm` filter
7. **Validates** output with `ffprobe` (non-zero size, valid streams, correct duration)

### 13.3 Asset Resolution (`assets.py`, 29KB)

For each shot in the storyboard, the asset sourcer:

1. Checks `fixtures/assets/` for pre-downloaded stock clips
2. Falls back to colored gradients matching the shot's mood/palette
3. Generates text-overlay frames for title cards
4. Ensures the risk disclaimer appears for ≥ 3.0 seconds

### 13.4 Filtergraph Builder (`filtergraph.py`)

Builds FFmpeg `-filter_complex` strings for:
- `zoompan` — slow Ken Burns zoom effect
- `drawtext` — on-screen text at specified timing
- `overlay` — combining video tracks
- `amix` — audio mixing with volume balancing

### 13.5 Output Validation (Rule V3)

FFmpeg can exit with code 0 even when the output file is 0 bytes (final frame dropped). After every render, `ffprobe` validates:
- File size > 0
- Video stream present
- Duration within 30–60 seconds range
- Aspect ratio matches 1080×1920

---

## 14. Compliance Gate

The compliance engine is **deterministic-first**: it does not rely on LLM judgment for hard rules.

### 14.1 Why Deterministic?

Advertising financial services has strict legal requirements. Meta and Google ban:
- Guaranteed return claims
- Copy-trading implications ("we'll copy their trades")
- Account access implications ("we manage your account")
- Unsubstantiated win rates or accuracy statistics

### 14.2 The Three Hard Rules

These directly encode CrowdWisdomTrading's published FAQ:

| Rule | Blocked pattern | Reason |
|---|---|---|
| `position_access_implication` | Any copy-trading or position-following claim | *"We only know what traders share. We don't have access to their positions."* |
| `copy_trading_implication` | Automated trade execution | Same — the product is signal, not execution |
| `managed_accounts_implication` | Account management claim | Same |

### 14.3 Performance Claim Blocking

Unsubstantiated win rates or accuracy figures are **hard-blocked**. The public track-record endpoint was offline at build time, so any number would be unverifiable and a regulatory liability.

### 14.4 The Better Angle

The product's genuine differentiator is **transparency of process**: every trade call publishes entry, target, stop loss, rationale, and chart publicly. A process claim is verifiable, defensible, and distinctive.

> *"The guardrail should produce a better ad, not merely a safer one."*

### 14.5 Risk Disclosure Verification

- Pre-render: Storyboard must include a risk disclaimer shot with declared duration ≥ 3.0s
- Post-render: QA re-verifies the actual rendered timeline for disclaimer duration

### 14.6 Two-Pass Claims Check

1. **Pre-render pass** (compliance stage): Checks `storyboard.json` voiceover scripts
2. **Post-render pass** (QA stage): Checks the **actual TTS transcript** — TTS normalization can change what is said

---

## 15. Hermes Board Operations

**File:** [`src/cwt/hermes/board.py`](file:///c:/SSD%20WINDOW/code/CrowdWisdomTrading/src/cwt/hermes/board.py)

### 15.1 Seeding the Board

`seed(run_id, run_dir, board)` creates all 12 cards in **topological order** using Kahn's algorithm. Each card gets:
- `--idempotency-key {run_id}:{card_key}` — prevents duplicates on resume
- `--parent {parent_card_id}` — enforces DAG ordering
- `--skill {skill_name}` — pins the recipe for the agent
- `--assignee {profile_name}` — must exactly match an installed profile

### 15.2 Card Body Contract

Every card body contains exactly:
```
RUN_ID: <id>
RUN_DIR: <absolute path>
BOARD: <board name>

STEP 1 — Call kanban_show()...
STEP 2 — Follow the pinned skill...
STEP 3 — Do work by calling cwt_* tools...
STEP 4 — Call kanban_complete with metadata...

ON FAILURE — Call kanban_block...

{card-specific body}
```

### 15.3 Waiting for Completion

`wait_for_completion()` polls every 15 seconds. Progress is tracked by a **card signature** `tuple(sorted((id, status)))`:
- Any status change (e.g., `running → review`) resets the stall timer
- After `stall_threshold_s` (default: 180s) with no change: dispatcher nudged once
- After a second stall: `PipelineBlocked` raised with retry command

### 15.4 Live Progress Display

```
╔══════════════════╗
║   CWT Pipeline    ║
╠══════════════════╣
║ Card  │ Stage    │ Assignee          │ Status   ║
║ #001  │ Source a │ cwt-ads-manager   │ done     ║
║ #002  │ Extract  │ cwt-hook-analyst  │ running  ║
║ #003  │ Research │ cwt-researcher    │ ready    ║
╚══════════════════╝
```

Colors: `done`=green, `running`=cyan, `ready`=yellow, `blocked`=red, `review`=magenta.

### 15.5 Diagnostics on Failure

On `PipelineBlocked` or timeout, `_dump_diagnostics()` writes per-card JSON to `runs/_diagnostics/{card_id}.json` with full show, runs, and log output. Always prints: `To retry: cwt run --resume`.

---

## 16. Offline Mode & Fixtures

**Directory:** [`fixtures/`](file:///c:/SSD%20WINDOW/code/CrowdWisdomTrading/fixtures)

### 16.1 What Fixtures Contain

```
fixtures/
├── http/         ← Recorded HTTP responses (Apify JSON, Tavily results, Exa results)
├── artifacts/    ← Pre-built stage artifacts (storyboard.json, claims_report.json, etc.)
│   ├── angles/   ← Research angle JSON files
│   └── variants/ ← Storyboard variant JSON files
└── assets/       ← Stock video clips, Piper voice model (.onnx)
```

### 16.2 How Offline Mode Works

1. `_seed_offline_fixtures()` copies `fixtures/artifacts/*` → `runs/<run_id>/artifacts/` (no overwrite)
2. For each stage, `_stage_is_valid()` checks if the artifact already exists → if yes, status = `skipped`
3. All LLM/paid-API stages are skipped (artifacts already present)
4. Only `render`, `qa`, and `collect` actually execute (they don't have fixture artifacts)
5. The render uses the **silent TTS backend** (no network, no voice model required)

### 16.3 What --offline does NOT skip

- FFmpeg video rendering (still runs for real, proving the pipeline works end-to-end)
- Audio mixing (uses silent TTS audio)
- QA checks (runs against the rendered output)
- Submission bundle assembly

### 16.4 The Offline Path First (§13)

```bash
# Correct order:
./scripts/bootstrap.ps1          # Set up venv + deps
hermes model                     # Configure Hermes worker model
cwt run --engine local --offline # Render a real 42s ad for $0.00
```

**Expected output:**
```
=== CWT Video Ads Agent ===
run_id       20260928-0946-d6df
engine       local
offline      True
Done. 11 stages, $0.0000, output: runs\20260928-0946-d6df\render\final.mp4
```

---

## 17. The Submission Bundle

**Directory:** [`submission/`](file:///c:/SSD%20WINDOW/code/CrowdWisdomTrading/submission)

The `collect` stage assembles the final submission package.

### 17.1 Bundle Contents

| File | Description |
|---|---|
| `final.mp4` | The 30–60s, 1080×1920 H.264/AAC video ad |
| `storyboard.json` | Complete storyboard with all shot data, scores, and compliance report |
| `storyboard.html` | Human-readable storyboard viewer |
| `contact_sheet.png` | Grid of keyframes from each shot |
| `render_manifest.json` | Backend used, encoding params, asset list, timing |
| `claims_report.json` | Pre- and post-render compliance verdict |
| `cost_report.json` | LLM costs by model/stage + external API costs |
| `README-SUBMISSION.md` | API tokens for reviewer + recording recipe |

### 17.2 Security Note

The `submission/` directory is **gitignored** — it may contain API tokens in `README-SUBMISSION.md` for reviewers. Never commit it.

### 17.3 Cost Report Structure (`cost_report.json`)

```json
{
  "by_stage": { "ads": { "usd": 0.01 }, ... },
  "by_tier": {
    "cheap": { "usd": 0.12, "calls": 35 },
    "strong": { "usd": 0.18, "calls": 5 }
  },
  "external": {
    "apify": { "usd": 0.24 },
    "tavily": { "free_tier": true, "usd": 0.0 },
    "exa": { "free_tier": true, "usd": 0.0 }
  },
  "grand_total_usd": 0.36
}
```

**Expected cost per live run:** ~$0.24–0.70 (Apify scraping ~$0.24, LLM creative calls ~$0.12–0.46)

---

## 18. Scripts & Helpers

**Directory:** [`scripts/`](file:///c:/SSD%20WINDOW/code/CrowdWisdomTrading/scripts)

### `bootstrap.ps1` / `bootstrap.sh`

One-time machine setup (run before `cwt bootstrap`):
- Creates `.venv` and installs Python dependencies
- Copies `.env.example` → `.env` if not present
- Verifies FFmpeg is available

### `record_kanban_video.ps1`

Recording recipe for the dashboard demo video:
1. Start screen recorder focused on `http://localhost:8080`
2. Launch: `cwt run --offline --record-pacing`
3. `--record-pacing` inserts deliberate pauses between stage transitions so card movements are visible
4. Record for 5–10 minutes, speed up 4–8× in post-production

### `fetch_piper_voice.py`

Downloads the Piper TTS voice model (`en_US-ryan-high.onnx`) to `fixtures/assets/voices/`.

### `record_fixtures.py`

Runs a live pipeline and records all HTTP responses to `fixtures/http/`. Used to refresh fixtures when APIs change.

### `scrub_fixtures.py`

Removes API tokens, PII, and other sensitive data from recorded fixtures before committing.

---

## 19. Run Directory Structure

Each `cwt run` creates a timestamped directory under `runs/`:

```
runs/
└── 20260928-0946-d6df/          ← run_id (timestamp + random hex)
    ├── artifacts/               ← All stage JSON outputs
    │   ├── winning_ads.json     ← ads stage
    │   ├── ad_patterns.json     ← patterns stage
    │   ├── angles/
    │   │   ├── pain.json        ← res_pain stage
    │   │   ├── unique_data.json ← res_unique stage
    │   │   └── crowd_effect.json← res_crowd stage
    │   ├── research_brief.json  ← brief stage
    │   ├── variants/
    │   │   ├── pain.json        ← storyboard variant 1
    │   │   ├── unique_data.json ← storyboard variant 2
    │   │   └── crowd_effect.json← storyboard variant 3
    │   ├── storyboard.json      ← script stage (final storyboard)
    │   ├── claims_report.json   ← compliance stage
    │   ├── voiceover.json       ← render stage (TTS manifest)
    │   ├── render_manifest.json ← render stage (video manifest)
    │   ├── claims_report_post_render.json ← qa stage
    │   └── provenance.json      ← Input hash records for idempotency
    ├── render/
    │   ├── final.mp4            ← The finished video
    │   └── audio/               ← Voiceover + music segments
    ├── llm_ledger.jsonl         ← One line per LLM call: model, tokens, cost
    ├── cache/                   ← HTTP fixture cache for this run
    └── submission/              ← Bundle copy (same as root submission/)
```

---

## 20. Cost Accounting

### 20.1 LLM Ledger

Every LLM call writes a line to `runs/<id>/llm_ledger.jsonl`:
```json
{"stage": "patterns", "model": "google/gemini-2.5-flash", "tier": "cheap", "prompt_tokens": 1200, "completion_tokens": 400, "cost_usd": 0.0012, "ts": "2026-09-28T07:12:34Z"}
```

### 20.2 Budget Guards

- `CWT_MAX_USD` (default: $2.00) — overall pipeline budget; raises `BudgetExceeded` if exceeded
- `APIFY_MAX_CHARGE_USD` (default: $1.00) — Apify-side hard cap; the actor run enforces this

### 20.3 Typical Cost Breakdown

| Service | Typical Cost | Notes |
|---|---|---|
| Apify (Meta Ads scrape) | ~$0.24 | 60 ads × ~$0.004/ad |
| Tavily | $0.00 | Free tier: 1,000 credits/month |
| Exa | $0.00 | $10 free credit |
| LLM cheap tier (35 calls) | ~$0.05–0.15 | Gemini Flash rates |
| LLM strong tier (5 calls) | ~$0.07–0.30 | Claude Sonnet rates |
| **Total** | **~$0.36–0.70** | |

---

## 21. Troubleshooting

| Symptom | Root Cause | Fix |
|---|---|---|
| `hermes: not recognized` | Hermes not on PATH | Run the install script; restart shell |
| `Apify returns 404` | Actor ID uses slash instead of tilde | Use `apify~facebook-ads-scraper` (tilde, not slash) |
| `Exa returns 400` | Deprecated API params | Current search types: `instant\|fast\|auto\|deep-lite\|deep\|deep-reasoning` |
| Card stuck on `ready` forever | Assignee doesn't match profile name | Run `cwt bootstrap` |
| Board never advances | Gateway not running | Run `hermes gateway start` |
| `[WinError 193] not a valid Win32 application` | Calling bare `hermes` or `npx` on Windows | Resolved in `util/subproc.py` via `shutil.which()` to `.cmd` shim |
| Render finishes, output 0 bytes | FFmpeg exits 0 on dropped frame (Rule V3) | Fixed — pipeline validates with `ffprobe` before marking done |
| Blank Hermes dashboard | Bound to `0.0.0.0` or gateway down | Never bind to `0.0.0.0`; use localhost only |
| `BudgetExceeded` exit 4 | LLM spend > `CWT_MAX_USD` | Increase `CWT_MAX_USD` or check for runaway loops |
| `OfflineFixtureMissing` | Missing fixture for a research angle | Run `cwt run` (live) once to record fixtures |
| Script stage never approves | Creative score below 8.0 after 3 rounds | `kanban_block` is the correct outcome; check storyboard themes |
| Compliance blocks after 3 rounds | Hard claim that can't be rewritten | Correct — unsubstantiated claims must block, not silently pass |

---

## 22. End-to-End Walkthrough

### Phase 1: One-Time Setup

```powershell
# 1. Clone repository
git clone <repo>
cd CrowdWisdomTrading

# 2. Install Hermes (machine-wide, one-time)
iex (irm https://hermes-agent.nousresearch.com/install.ps1)

# 3. Set up Python environment + install CWT
./scripts/bootstrap.ps1

# 4. Activate virtual environment
.venv/Scripts/Activate.ps1

# 5. Fill in API keys
notepad .env

# 6. Set Hermes worker model (NOT the same as LLM_MODEL_*)
hermes model
# → Pick a model with ≥64k context (e.g., anthropic/claude-opus-4.6)

# 7. Install CWT profiles, skills, and plugin
cwt bootstrap
# Expected: "8 Skills installed", "Plugin installed: cwt", "9 profiles found"

# 8. Start the Kanban dispatcher
hermes gateway start
```

### Phase 2: Verify Before Spending

```powershell
# 9. Run preflight diagnostics — ALL must be green
cwt doctor
```

### Phase 3: Offline Proof (Zero Cost)

```powershell
# 10. Render a real video with no API calls
cwt run --engine local --offline
# Expected: "Done. 11 stages, $0.0000, output: runs\...\render\final.mp4"
# Verify: 42s, 1080x1920, H.264/AAC
```

### Phase 4: Live Run

```powershell
# 11. Run the full live pipeline
cwt run
# Watch the dashboard at http://localhost:8080
# Expected duration: 30–90 minutes
# Expected cost: ~$0.36–0.70
```

### Phase 5: Inspect Results

```powershell
# 12. Check submission bundle
ls submission/
# final.mp4, storyboard.json, storyboard.html, contact_sheet.png
# render_manifest.json, claims_report.json, cost_report.json, README-SUBMISSION.md

# 13. Play the video
start submission/final.mp4

# 14. View storyboard
start submission/storyboard.html
```

### Phase 6: Recording Demo (Optional)

```powershell
# Start screen recorder on http://localhost:8080

# Run with deliberate pacing
cwt run --offline --record-pacing

# Speed up 4-8× in post-production
```

---

> **Key Invariants to Never Break:**
> - `VIDEO_BACKEND_CHAIN` must end with `local_ffmpeg` (Rule V4)
> - `APIFY_MAX_CHARGE_USD` must be set and > 0 (Rule A2)
> - Compliance gate must never silently pass a blocked script (Rule C2)
> - Dashboard must only bind to localhost, never 0.0.0.0 (Rule S1)
> - Never call `kanban_complete` to clear a stall — it fabricates progress (Rule K3/K4)
> - Validate render output with `ffprobe` before marking done (Rule V3)
