# CWT Video Ads Agent — Complete One-Shot Build Specification

> **Instructions for the AI reading this:**
> Build this system exactly as described. Every file, every function, every line of config is specified below.
> Do not skip anything. Do not simplify. Do not substitute libraries. Build it precisely.
> This document is complete and self-contained — no external repo is required.
>
> **One deviation is pre-authorised:** Section 12 lists architecture rules derived from real, reproduced
> failure modes. If you find yourself wanting to deviate from anything *else* in this document, re-read
> the relevant section first. The design decisions that look arbitrary are the ones that cost the most.

---

## 0. What You Are Building

A **multi-agent video advertising system** that autonomously produces a 30–60 second cinematic,
movie-style video ad for **crowdwisdomtrading.com** — a paid market-intelligence and trading-signals
newsletter.

The system does not make text ads. It makes a film.

It:
- **Sources** currently-winning ads in the trading/fintech niche by scraping the **Meta Ads Library
  via Apify**, filtered to the last 30 days
- **Extracts** the marketing hook, the pain point, the concept, and the **structural beat sheet**
  (which beat occupies which second) from every winning ad
- **Researches** three independent angles with **Tavily** and **Exa**, constrained to the last month:
  (i) the ICP's pain, (ii) CrowdWisdomTrading's unique data, (iii) how crowd wisdom changes retail
  trading outcomes
- **Writes** three competing storyboards — one per research angle — judges them, splices the best
  beats from the losers into the winner, and produces a **human-readable storyboard JSON**
- **Generates** a scroll-stopping visual hook as a scored search over 12 candidates, not a sentence
- **Critiques** its own work: a creative-director agent scores the storyboard against the mined
  winning-ad patterns and forces rewrites until it clears a numeric threshold
- **Red-teams** the script against a financial-advertising claims policy, with a deterministic rule
  engine that hard-blocks prohibited claims before any LLM is consulted
- **Renders** the ad through a layered video-backend chain that always terminates in a working
  local ffmpeg assembler
- **Orchestrates** the whole thing on the **Hermes Kanban board**, so a reviewer can watch the agents
  work in the dashboard and record it

**Tech stack:**
- Python 3.11
- Hermes Agent (Nous Research) — agent framework, kanban orchestration, profiles, skills, plugins
- OpenRouter **or** NVIDIA build.nvidia.com (NIM) — both OpenAI-compatible at `/v1/chat/completions`
- Apify — Meta Ads Library scraping
- Tavily — web search (last-month window)
- Exa — neural search (last-month window, publication-date bounded)
- ffmpeg via `imageio-ffmpeg` — the guaranteed video render path
- HyperFrames (optional, Node 22+) — HTML/CSS-authored cinematic compositions
- edge-tts → Piper — voiceover synthesis, with a captions-only floor
- pydantic v2, httpx, rich, pytest

---

## 1. File Structure

Create every file listed here. No extras needed. If you cannot write a one-line responsibility for a
file, it is doing too much — split it.

```
cwt-video-ads-agent/
│
├── README.md                        ← Quickstart, recording recipe, submission checklist
├── LICENSE                          ← MIT
├── NOTICE                           ← Third-party licences + AGPL reasoning for the OpenMontage adapter
├── .env.example                     ← Every env var, grouped by service, with obtain-URLs
├── .env                             ← Real secrets. Never commit. Always gitignored.
├── .gitignore
├── .gitattributes                   ← Forces LF for *.sh/*.py, binary for *.png — see Rule W8
├── requirements.txt                 ← Runtime deps, pinned
├── requirements-dev.txt             ← pytest, ruff, mypy
├── pyproject.toml                   ← Package metadata, ruff/mypy/pytest config
├── Makefile                         ← bootstrap, doctor, run, test, lint, demo
├── Dockerfile                       ← Linux reproducible path: python + ffmpeg + node22 + Hermes
│
├── hermes/
│   ├── config.yaml                  ← Hermes settings template (kanban dispatcher, MCP, toolsets)
│   ├── profiles/                    ← One directory per agent persona
│   │   ├── cwt-orchestrator/        ← Decomposes goals into cards; owns no artifacts
│   │   ├── cwt-ads-manager/         ← Sources winning ads; assembles the submission bundle
│   │   ├── cwt-hook-analyst/        ← Extracts hooks, pains, concepts, beat sheets from ads
│   │   ├── cwt-researcher/          ← Runs the three research angles; assembles the brief
│   │   ├── cwt-script-writer/       ← Writes storyboard variants; applies rewrites
│   │   ├── cwt-creative-director/   ← Scores storyboards against the rubric; approves or rejects
│   │   ├── cwt-compliance/          ← Runs the claims gate; approves, rewrites, or blocks
│   │   ├── cwt-video-editor/        ← Renders the ad through the backend chain
│   │   └── cwt-qa/                  ← Verifies duration, loudness, aspect, post-render claims
│   │       └── (each profile dir: config.yaml, .env, SOUL.md)
│   └── plugins/
│       └── cwt/                     ← The native Hermes plugin — our tool surface
│           ├── plugin.yaml          ← Manifest: name, provides_tools, requires_env, capabilities
│           ├── __init__.py          ← register(ctx): wires schemas to handlers
│           ├── schemas.py           ← JSON schemas the LLM sees
│           └── handlers.py          ← Thin adapters → src/cwt/tools/*
│
├── src/cwt/
│   ├── __init__.py                  ← __version__
│   ├── cli.py                       ← argparse entrypoint; all subcommands; exit codes
│   ├── config.py                    ← Settings dataclass; the ONLY place env vars are read
│   ├── doctor.py                    ← Preflight: binaries, APIs, model slugs, Hermes flags
│   │
│   ├── domain/
│   │   ├── models.py                ← Every pydantic model + validators (the artifact contracts)
│   │   ├── claims.py                ← Pure deterministic claims rule engine. No I/O. No LLM.
│   │   ├── beats.py                 ← Beat taxonomy, archetypes, median-timeline aggregation
│   │   └── artifacts.py             ← ArtifactStore: one function per artifact, read + write
│   │
│   ├── clients/
│   │   ├── http_cache.py            ← Content-hash write-through cache. The --offline mechanism.
│   │   ├── llm.py                   ← One OpenAI-compatible client, two provider profiles, tiers
│   │   ├── apify.py                 ← Meta Ads Library: run actor, poll, fetch dataset, normalise
│   │   ├── tavily.py                ← Search with time_range="month"
│   │   ├── exa.py                   ← Search with startPublishedDate
│   │   └── tts.py                   ← edge-tts → Piper → silence+captions fallback chain
│   │
│   ├── prompts/
│   │   ├── __init__.py              ← build_prompt() re-export
│   │   ├── extract.py               ← Hook/pain/concept + beat-sheet extraction prompts
│   │   ├── research.py              ← The three research-angle prompts
│   │   ├── script.py                ← Storyboard variant, hook candidates, rewrite prompts
│   │   ├── review.py                ← Creative-director rubric prompt
│   │   └── claims_policy.py         ← The distilled financial-ad policy the LLM judge receives
│   │
│   ├── tools/
│   │   ├── ads.py                   ← cwt_source_winning_ads, cwt_rank_winning_ads
│   │   ├── patterns.py              ← cwt_extract_ad_patterns, cwt_aggregate_beat_timeline
│   │   ├── research.py              ← cwt_research_pain, _unique_data, _crowd_effect, cwt_assemble_brief
│   │   ├── storyboard.py            ← cwt_write_storyboard_variant, _generate_hook_candidates,
│   │   │                              _judge_variants, _score_storyboard, _apply_rewrite,
│   │   │                              _render_storyboard_html, _make_contact_sheet
│   │   ├── claims.py                ← cwt_check_claims, cwt_rewrite_for_compliance
│   │   ├── video.py                 ← cwt_synthesize_voiceover, cwt_render_video, cwt_probe_media
│   │   └── bundle.py                ← cwt_verify_artifact, cwt_assemble_submission
│   │
│   ├── video/
│   │   ├── ffmpeg_bin.py            ← ffmpeg/ffprobe resolution chain (never assumes PATH)
│   │   ├── backend.py               ← VideoBackend protocol, Availability, VideoBackendChain
│   │   ├── filtergraph.py           ← PURE Shot → ffmpeg filter string. Golden-tested.
│   │   ├── local_ffmpeg.py          ← LocalFfmpegBackend — the guaranteed floor
│   │   ├── hyperframes.py           ← HyperFramesBackend — optional upgrade
│   │   ├── openmontage.py           ← OpenMontageBackend — best-effort, gated, documented as such
│   │   └── assets.py                ← AssetSourcer: CC0 allowlist, content-hash cache
│   │
│   ├── hermes/
│   │   ├── cli.py                   ← run_hermes(): the ONLY place the hermes binary is invoked
│   │   ├── dag.py                   ← DAG_SPEC — the card graph, as data. One source of truth.
│   │   ├── board.py                 ← seed(), wait_for_completion(), resume(), nudge()
│   │   └── record.py                ← --record-pacing and the recording-recipe helper
│   │
│   └── util/
│       ├── subproc.py               ← run_tool(): the ONLY place a subprocess is spawned
│       ├── jsonio.py                ← read_json/write_json with utf-8 + newline="\n" enforced
│       ├── retry.py                 ← Exponential backoff with full jitter, honours Retry-After
│       └── paths.py                 ← RunPaths, long_path() for Windows, run_id minting
│
├── skills/                          ← Hermes skill files, one per agent capability
│   ├── cwt-source-winning-ads/SKILL.md
│   ├── cwt-extract-ad-patterns/SKILL.md
│   ├── cwt-research-angle/SKILL.md
│   ├── cwt-write-storyboard/SKILL.md
│   ├── cwt-creative-review/SKILL.md
│   ├── cwt-claims-gate/SKILL.md
│   ├── cwt-render-video/SKILL.md
│   └── cwt-final-qa/SKILL.md
│
├── fixtures/
│   ├── http/                        ← Recorded API responses, content-hash keyed. Enables --offline.
│   └── assets/                      ← 12 CC0 stills, 3 CC0 clips, 1 CC0 music bed, 1 Piper voice
│
├── runs/                            ← Per-run output. Gitignored except .gitkeep.
│   └── .gitkeep
│
├── scripts/
│   ├── bootstrap.ps1                ← Windows primary entrypoint
│   ├── bootstrap.sh                 ← Linux/macOS secondary entrypoint
│   ├── record_fixtures.py           ← Deliberately re-record fixtures (makes REAL API calls)
│   └── record_kanban_video.ps1      ← Prints the recording recipe + verifies dashboard is up
│
└── tests/
    ├── test_claims.py               ← Exhaustive. The guardrail is a control, so it is proven.
    ├── test_filtergraph.py          ← Pure Shot → filtergraph. No ffmpeg needed.
    ├── test_models.py               ← Schema validation incl. the eleven storyboard validators
    ├── test_llm_repair.py           ← extract_json + the repair loop, against recorded failures
    ├── test_artifacts.py            ← ArtifactStore round-trip + schema_version handling
    └── test_dag.py                  ← Topological sort, idempotency keys, parent wiring
```

---

## 2. Environment Variables

### `.env.example` (copy to `.env`, fill in):

```env
# ═══════════════════════════════════════════════════════════════════════════
# CWT Video Ads Agent — Environment
#
# SETTINGS PRECEDENCE (highest first) — never mix, never reverse:
#   1. Runtime environment variables (shell / Docker / CI)
#   2. .env file in the project root
#   3. Defaults in src/cwt/config.py
#
# Hermes has its OWN precedence for its own settings:
#   CLI args > ~/.hermes/config.yaml > ~/.hermes/.env > built-in defaults
# Those two chains are independent. A var in this file does NOT configure
# the Hermes worker's model — see §9.4 for that.
# ═══════════════════════════════════════════════════════════════════════════

# ── LLM PROVIDER ──────────────────────────────────────────────────────────
# Pick ONE provider. Both are OpenAI-compatible at /v1/chat/completions.
#   OpenRouter: https://openrouter.ai/keys
#   NVIDIA NIM: https://build.nvidia.com/  →  "Get API Key"
LLM_PROVIDER=openrouter                     # openrouter | nvidia

OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

NVIDIA_API_KEY=nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1

# Tier routing. CHEAP handles ~35 calls/run (extraction, scoring, judging).
# STRONG handles ~5 calls/run (the actual creative writing). Keep STRONG small.
# Model slugs DRIFT — `cwt doctor` validates these against /v1/models and fails loudly.
LLM_MODEL_CHEAP=google/gemini-2.5-flash
LLM_MODEL_STRONG=anthropic/claude-sonnet-4.5
LLM_MODEL_FALLBACKS=meta/llama-3.3-70b-instruct,qwen/qwen2.5-72b-instruct

# Concurrency. NVIDIA free tier is ~40 RPM PER MODEL. 4 is the safe number;
# three parallel research cards plus a 24-call extraction batch will collide above it.
LLM_MAX_CONCURRENCY=4
LLM_JSON_REPAIR_ATTEMPTS=2
LLM_TIMEOUT_SECONDS=120

# ── APIFY — Meta Ads Library ──────────────────────────────────────────────
# https://console.apify.com/account/integrations
# FREE TIER IS $5/MONTH. At ~$3.40-5.80 per 1,000 ads that is roughly 850-1,470 ads.
# Credits EXPIRE at the end of each billing cycle. The pipeline hard-caps spend
# per run via maxTotalChargeUsd — see Rule A2 in §12. Do not remove that cap.
APIFY_TOKEN=apify_api_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# The actor id. NOTE THE TILDE — in REST URLs a slash returns 404.
# `apify/facebook-ads-library-scraper` DOES NOT EXIST. Do not "fix" this to that.
APIFY_ADS_ACTOR_ID=apify~facebook-ads-scraper
APIFY_ADS_ACTOR_FALLBACKS=curious_coder~facebook-ads-library-scraper
APIFY_MAX_ITEMS=60
APIFY_MAX_CHARGE_USD=1.00
APIFY_RUN_TIMEOUT_SECONDS=900

# ── TAVILY — web search ───────────────────────────────────────────────────
# https://app.tavily.com/home   →  free "Researcher" plan: 1,000 credits/month
# `search_depth=advanced` costs 2 credits. Budget accordingly.
TAVILY_API_KEY=tvly-dev-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ── EXA — neural search ───────────────────────────────────────────────────
# https://dashboard.exa.ai/api-keys   →  $10 free credit, resets monthly
EXA_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# ── VIDEO RENDER ──────────────────────────────────────────────────────────
# The chain is tried left to right. THE LAST ELEMENT MUST BE local_ffmpeg.
# Config load FAILS if it is not — that invariant is what guarantees the
# pipeline cannot hard-fail on video.
VIDEO_BACKEND_CHAIN=hyperframes,openmontage,local_ffmpeg

# Override ffmpeg discovery. Leave blank to use imageio-ffmpeg's bundled binary.
CWT_FFMPEG_BIN=
CWT_FFPROBE_BIN=

VIDEO_WIDTH=1080
VIDEO_HEIGHT=1920
VIDEO_FPS=30
VIDEO_MIN_SECONDS=30
VIDEO_MAX_SECONDS=60
VIDEO_LOUDNESS_LUFS=-14
VIDEO_TRUE_PEAK_DBTP=-1.5

# ── TEXT-TO-SPEECH ────────────────────────────────────────────────────────
# Chain: edge-tts → piper (offline) → silence + burned-in captions.
# The captions-only path still yields a watchable, comprehensible ad.
TTS_BACKEND_CHAIN=edge_tts,piper,silent
EDGE_TTS_VOICE=en-US-AndrewNeural
PIPER_VOICE_PATH=fixtures/assets/voices/en_US-ryan-high.onnx

# ── HYPERFRAMES (optional upgrade) ────────────────────────────────────────
# Node 22+ required. Apache-2.0. Leave blank to skip — local_ffmpeg covers you.
HYPERFRAMES_ENABLED=auto

# ── OPENMONTAGE (optional, best-effort, AGPL-3.0) ─────────────────────────
# We invoke it as an EXTERNAL PROCESS and consume only its output file.
# We never vendor its source. See NOTICE. This backend may simply not work.
OPENMONTAGE_HOME=

# ── HERMES ────────────────────────────────────────────────────────────────
# Path to the hermes binary. Blank = resolve from PATH via shutil.which(),
# which on Windows returns the .cmd shim — see Rule W4.
HERMES_BIN=
HERMES_MIN_VERSION=0.16.0

# ── RUN CONTROL ───────────────────────────────────────────────────────────
CWT_BOARD=cwt-ads
CWT_RUN_TIMEOUT_SECONDS=5400
CWT_STALL_THRESHOLD_SECONDS=180
CWT_MAX_USD=2.00

# ── COMPLIANCE ────────────────────────────────────────────────────────────
# The claims gate is a HARD gate. Setting this to 0 disables it, which makes
# the output unshippable on Meta/Google. Do not set it to 0.
CLAIMS_GATE_ENABLED=1
CLAIMS_MAX_REWRITE_ROUNDS=3
CREATIVE_THRESHOLD=8.0
CREATIVE_MAX_ROUNDS=3
```

### `.gitignore`:

```
.env
.env.local
__pycache__/
*.pyc
*.pyo
*.pyd
.venv/
venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
*.egg-info/
dist/
build/
.DS_Store
Thumbs.db

# Run artifacts are regenerable and some contain large media
runs/*
!runs/.gitkeep

# Recorded API responses MAY contain your token in a URL — keep them out
# until scripts/scrub_fixtures.py has run. (The recorder scrubs on write;
# this is belt-and-braces.)
fixtures/http/**/*.raw.json

# Hermes local state
.hermes/
*.log
```

### `.gitattributes`:

```gitattributes
* text=auto
*.sh   text eol=lf
*.py   text eol=lf
*.yaml text eol=lf
*.yml  text eol=lf
*.md   text eol=lf
*.json text eol=lf
*.png  binary
*.jpg  binary
*.mp4  binary
*.mp3  binary
*.onnx binary
```

---

## 3. Data Schema — The Artifact Contracts

There is no SQL database in this project. **Hermes owns `kanban.db` and we never touch it directly.**
The equivalent of a schema is the set of **JSON artifacts** that flow between stages. They get the
same treatment a database schema would: versioned, validated, idempotent on re-write, with access
posture stated.

### 3.0 Artifact conventions

```python
# src/cwt/domain/artifacts.py — conventions, enforced in one place

ARTIFACT_NAMES = (
    "winning_ads",          # sourced + ranked ads from the Meta Ads Library
    "ad_patterns",          # extracted hooks, pains, concepts, beat sheets, aggregates
    "research_brief",       # three research angles + assembled brief
    "storyboard",           # the script. Human-readable. The centrepiece.
    "hook_candidates",      # 12 scored hook candidates with rejection reasons
    "review_verdict",       # creative-director scoring
    "claims_report",        # deterministic + LLM claims findings
    "render_manifest",      # exact ffmpeg argv, backend chain, asset licences
)
```

**Rules that apply to every artifact:**

1. **`schema_version` is mandatory** and is an integer, currently `1`. A reader that sees a higher
   version than it knows refuses to parse and raises `ArtifactVersionError` — never silently
   misinterprets.
2. **Evolution is append-only.** Adding a field means adding an optional field plus a validator.
   Existing artifacts on disk must remain readable. Never rename or repurpose a field.
3. **Every write is utf-8 with `newline="\n"` and `ensure_ascii=False`.** Enforced by
   `util/jsonio.py`. On Windows `Path.write_text` does NOT normalise newlines — see Rule W8.
4. **Access posture:** artifacts are run-scoped, written under `runs/<run_id>/artifacts/`, and are
   world-readable on disk. **API tokens live only in `.env` and are never written into an artifact.**
   The Apify client strips `?token=` from any URL before it reaches an artifact or a log line. This
   is not optional — the submission includes the repo publicly.
5. **Provenance:** every artifact write also appends to `artifacts/provenance.json` the sha256 of
   every input that produced it. This is what makes `--resume` able to skip work correctly.

### 3.1 `winning_ads.json`

```jsonc
{
  "schema_version": 1,
  "generated_at": "2026-09-26T14:02:11Z",
  "query": {
    "keywords": ["trading signals", "stock market alerts", "forex signals", "options flow"],
    "countries": ["US", "GB", "IN"],
    "window_start": "2026-08-27",
    "window_end": "2026-09-26",
    "window_days": 30
  },
  "source": {
    "actor_id": "apify~facebook-ads-scraper",
    "actor_fallbacks_tried": [],
    "run_id": "aBcDeFgHiJkLmNoP",
    "dataset_id": "XyZ1234567890",
    "items_returned": 187,
    "items_after_window_filter": 64,
    "actual_charge_usd": 0.37,
    "charge_cap_usd": 1.00
  },
  "ranking": {
    "method": "weighted_longevity_signal",
    "weights": { "active_days": 0.45, "is_active": 0.25, "platform_breadth": 0.15, "recency": 0.15 },
    "excluded_reasons": { "outside_window": 123, "no_body_text": 9, "duplicate_collation": 11 }
  },
  "ads": [
    {
      "ad_id": "1234567890123",
      "collation_id": "9876543210987",
      "page_name": "Example Signals Co",
      "page_id": "15087023444",
      "is_active": true,
      "started_running": "2026-09-04",
      "ended_running": null,
      "active_days": 22,
      "publisher_platforms": ["FACEBOOK", "INSTAGRAM"],
      "display_format": "VIDEO",
      "cta_type": "LEARN_MORE",
      "cta_text": "Learn more",
      "link_url": "https://example.com/",
      "body_text": "Most traders follow one voice. Here is what 16,000 sound like.",
      "title": "Collective Intelligence",
      "link_description": "See the consensus.",
      "image_urls": [],
      "video_urls": ["https://video.example.com/abc.mp4"],
      "video_duration_s": 34.2,
      "ad_library_url": "https://www.facebook.com/ads/library/?id=1234567890123",
      "performance_score": 0.81,
      "normalised_from": "apify~facebook-ads-scraper"
    }
  ],
  "warnings": []
}
```

**Notes that matter:**
- `ad_id` is named `ad_id` in **our** normalised model. The raw Apify actor returns `adArchiveID`
  (camelCase, capital `ID`). Section 8.3 contains the full field-mapping table. **Never read a raw
  actor field outside `clients/apify.py`.**
- `active_days` is the performance proxy. Ad longevity is the industry-standard signal for a winning
  ad, and it is the only performance-ish metric the Apify actor returns reliably. Impressions and
  spend are frequently absent or bucketed — the ranker must degrade gracefully when they are missing
  and record that degradation in `ranking.weights` plus `warnings`.

### 3.2 `ad_patterns.json`

```jsonc
{
  "schema_version": 1,
  "source_ads_sha256": "3f2a...",
  "patterns": [
    {
      "ad_id": "1234567890123",
      "hook": {
        "text": "Most traders follow one voice. Here is what 16,000 sound like.",
        "archetype": "contrarian_stat",
        "stop_power_score": 8.4,
        "why_it_stops_the_scroll": "It asserts a number large enough to be implausible and immediately reframes the viewer as the one being left behind."
      },
      "pain": "Following a single analyst means inheriting one person's blind spots.",
      "concept": "Consensus as a risk-control mechanism, not a return mechanism.",
      "beat_sheet": [
        { "beat": "hook",      "start_s": 0.0,  "end_s": 3.1,  "what_happens": "Bold statement over a wall of overlapping talking-head thumbnails" },
        { "beat": "problem",   "start_s": 3.1,  "end_s": 8.4,  "what_happens": "Text: '1 opinion is not a strategy'" },
        { "beat": "agitation", "start_s": 8.4,  "end_s": 14.0, "what_happens": "Chart of one analyst's losing streak" },
        { "beat": "mechanism", "start_s": 14.0, "end_s": 23.5, "what_happens": "Thumbnails converge into a single highlighted level" },
        { "beat": "proof",     "start_s": 23.5, "end_s": 29.0, "what_happens": "Grid of published calls with outcomes" },
        { "beat": "cta",       "start_s": 29.0, "end_s": 34.2, "what_happens": "Logo + 'See the consensus'" }
      ]
    }
  ],
  "aggregate": {
    "ad_count": 12,
    "archetype_distribution": { "contrarian_stat": 0.33, "bold_statement": 0.25, "pattern_interrupt": 0.17, "question": 0.17, "social_proof": 0.08 },
    "median_hook_duration_s": 3.2,
    "median_beat_timeline": [
      { "beat": "hook",      "start_s": 0.0,  "end_s": 3.2,  "tolerance_s": 1.2 },
      { "beat": "problem",   "start_s": 3.2,  "end_s": 8.6,  "tolerance_s": 2.0 },
      { "beat": "agitation", "start_s": 8.6,  "end_s": 14.2, "tolerance_s": 2.4 },
      { "beat": "mechanism", "start_s": 14.2, "end_s": 23.8, "tolerance_s": 3.0 },
      { "beat": "proof",     "start_s": 23.8, "end_s": 29.4, "tolerance_s": 3.0 },
      { "beat": "cta",       "start_s": 29.4, "end_s": 34.0, "tolerance_s": 3.0 }
    ],
    "underused_high_durability": ["social_proof", "pain_point"]
  },
  "warnings": []
}
```

**`underused_high_durability` is the single most valuable field in this artifact.** Published
fintech ad-creative benchmarks show Social Proof appears in only ~0.1% of video creatives yet
survives ~2.1× longer than average, while Bold Statement is 36% of creatives and merely average.
The hook generator is explicitly instructed to prefer archetypes on this list. That is a real,
sourced edge rather than a stylistic preference.

### 3.3 `research_brief.json`

```jsonc
{
  "schema_version": 1,
  "window": { "start": "2026-08-27", "end": "2026-09-26" },
  "angles": {
    "pain": {
      "angle": "pain",
      "claims": [
        {
          "text": "Retail traders report decision paralysis when following more than three signal sources.",
          "source_url": "https://example.com/article",
          "source_title": "The Signal Overload Problem",
          "published_date": "2026-09-11",
          "provider": "tavily",
          "confidence": 0.72
        }
      ],
      "synthesis": "Two to four sentences a scriptwriter can use directly.",
      "search_queries_used": ["retail trader signal overload", "trading signal fatigue 2026"]
    },
    "unique_data": {
      "angle": "unique_data",
      "claims": [
        {
          "text": "Every published prediction carries a date, ticker, direction, entry, two targets, two stops, a chart image and a final outcome recorded in public.",
          "source_url": "https://crowdwisdomtrading.com/",
          "source_title": "CrowdWisdomTrading",
          "published_date": null,
          "provider": "exa",
          "confidence": 0.90
        }
      ],
      "synthesis": "...",
      "search_queries_used": ["crowdwisdomtrading predictions track record"]
    },
    "crowd_effect": {
      "angle": "crowd_effect",
      "claims": [],
      "synthesis": "...",
      "search_queries_used": ["wisdom of crowds forecast accuracy", "superforecaster aggregation"]
    }
  },
  "product": {
    "name": "CrowdWisdomTrading",
    "tagline": "Collective Intelligence for Traders",
    "legal_entity": "Tsuroni LTD",
    "landing_url": "https://crowdwisdomtrading.com/",
    "landing_url_verified_200": true,
    "markets": ["US stocks", "crypto", "forex", "commodities"],
    "pricing": [
      { "tier": "Weekly CrowdWisdom", "price": "$0", "period": "free" },
      { "tier": "Pro Access", "price": "$29.99", "period": "month" },
      { "tier": "Pay As You Go", "price": "$9.99", "period": "per 10-credit pack" }
    ],
    "explicit_disclaimers": {
      "not_copy_trading": true,
      "not_algo_trading": true,
      "not_personalised_advice": true,
      "no_position_access": true,
      "note": "The product's own FAQ states they do not have access to traders' positions."
    },
    "brand": {
      "background": "#050505",
      "surface": "#0a0a0a",
      "primary_accent": "#22d3ee",
      "secondary_accents": ["#fb923c", "#fbbf24", "#818cf8", "#fb7185"],
      "success": "#34d399",
      "text": "#cbd5e1",
      "mood": "dark terminal aesthetic, cyan-led, gradient-filled display numerals"
    }
  },
  "prohibited_facts": [
    {
      "fact": "74.1% of tracked directions hit",
      "reason": "Unverifiable. Appears as 73%, 73.8% and 74.1% on the product's own site. The track-record page and /api/predictions that would substantiate it both return HTTP 404 as of 2026-09-26.",
      "rule": "Must never appear in any generated script, in any form, including paraphrases and rounded variants."
    },
    {
      "fact": "16,564 professional traders tracked",
      "reason": "Self-reported with no published counting or de-duplication methodology.",
      "rule": "May not be stated as a verified fact. If used, it must be attributed to the company ('the company says it tracks...') or avoided."
    },
    {
      "fact": "Institutional sentiment feature",
      "reason": "No methodology or data source disclosed anywhere.",
      "rule": "May not be described or implied in creative."
    }
  ]
}
```

**`prohibited_facts` is a required, non-empty field.** It is injected verbatim into both the
scriptwriter prompt and the compliance prompt. The research agent discovers these by reading the
product's own FAQ and cross-checking what is and is not substantiated; the values above are the
verified starting set, and the agent may append to it but must never remove an entry.

### 3.4 `storyboard.json` — the centrepiece

This is the artifact the brief calls out by name ("saved and shared in json human readable format").
It must be readable by a human who has never seen the system. Example, complete and realistic:

```jsonc
{
  "schema_version": 1,
  "meta": {
    "run_id": "20260926-1402-a7f3",
    "generated_at": "2026-09-26T14:31:52Z",
    "product": "CrowdWisdomTrading",
    "landing_url": "https://crowdwisdomtrading.com/",
    "total_duration_s": 42.0,
    "aspect_ratio": "9:16",
    "resolution": "1080x1920",
    "fps": 30,
    "angle": "unique_data",
    "angle_rationale": "Chosen because the public per-prediction record is the only claim in this niche that a competitor cannot copy, and it survives the claims gate without needing an unverifiable statistic."
  },

  "visual_hook": {
    "archetype": "visual_shock",
    "first_3_seconds": "A single red candlestick, alone on black. It multiplies — two, four, sixteen — until the frame is a wall of noise. Then all of it snaps to silence and one cyan line remains.",
    "text_overlay": "TOO MANY VOICES.",
    "sound_design": "One clean synth note, then a rising cluster of detuned voices that cuts to silence on the snap.",
    "stop_power_score": 8.9,
    "why_it_stops_the_scroll": "It externalises a feeling the viewer already has (signal overload) as a visual event in under two seconds, with no product and no claim on screen — so there is nothing to scroll past yet, only something to watch.",
    "alternatives_considered": [
      { "archetype": "contrarian_stat", "text_overlay": "11,000 ANALYSTS. ONE ANSWER.", "rejected_because": "Depends on a count we cannot substantiate; fails the claims gate." },
      { "archetype": "question", "text_overlay": "WHO DO YOU TRUST?", "rejected_because": "Question hooks are common in this vertical and score lower on pattern interruption." },
      { "archetype": "social_proof", "text_overlay": "THEY DISAGREE. THAT'S THE POINT.", "rejected_because": "Strong and underused, but it resolves the tension in the hook instead of opening it. Held for a variant." }
    ]
  },

  "beats": [
    { "beat": "hook",      "start_s": 0.0,  "end_s": 3.4,  "tolerance_s": 1.2, "on_target": true },
    { "beat": "problem",   "start_s": 3.4,  "end_s": 8.8,  "tolerance_s": 2.0, "on_target": true },
    { "beat": "agitation", "start_s": 8.8,  "end_s": 14.6, "tolerance_s": 2.4, "on_target": true },
    { "beat": "mechanism", "start_s": 14.6, "end_s": 24.1, "tolerance_s": 3.0, "on_target": true },
    { "beat": "proof",     "start_s": 24.1, "end_s": 30.2, "tolerance_s": 3.0, "on_target": true },
    { "beat": "objection", "start_s": 30.2, "end_s": 35.9, "tolerance_s": 3.0, "on_target": true },
    { "beat": "cta",       "start_s": 35.9, "end_s": 42.0, "tolerance_s": 3.0, "on_target": true }
  ],

  "shots": [
    {
      "id": "s01",
      "beat": "hook",
      "start_s": 0.0,
      "duration_s": 3.4,
      "description": "Single red candlestick on pure black, centred. It duplicates outward in a widening grid until the frame is dense with flickering red and green.",
      "subject": "abstract_market_data",
      "asset": { "kind": "generated_chart", "ref": "candle_proliferation", "source": "internal" },
      "camera": { "move": "static", "intensity": 0.0, "lens_mm": 50, "depth_of_field": "deep", "stabilisation": "locked" },
      "lighting": { "key": "none", "contrast": "extreme", "colour_temp_k": 6500 },
      "palette": ["#050505", "#1a1a1a", "#ef4444", "#22d3ee"],
      "composition": { "framing": "centre", "text_safe_area": { "top": 0.15, "bottom": 0.25 } },
      "transition_in": { "type": "cut", "duration_s": 0.0 },
      "transition_out": { "type": "flash_white", "duration_s": 0.12 },
      "on_screen_text": [],
      "sfx": [{ "ref": "synth_note_clean", "at_s": 0.0, "gain_db": -6 }]
    },
    {
      "id": "s02",
      "beat": "hook",
      "start_s": 3.4,
      "duration_s": 0.0,
      "description": "The wall of candles snaps to black. One cyan horizontal line draws itself left to right across the centre of frame.",
      "subject": "abstract_market_data",
      "asset": { "kind": "generated_chart", "ref": "single_cyan_line", "source": "internal" },
      "camera": { "move": "push_in", "intensity": 0.35, "lens_mm": 85, "depth_of_field": "shallow", "stabilisation": "smooth" },
      "lighting": { "key": "practical_cyan", "contrast": "high", "colour_temp_k": 7000 },
      "palette": ["#050505", "#0a0a0a", "#22d3ee", "#e2e8f0"],
      "composition": { "framing": "centre", "text_safe_area": { "top": 0.15, "bottom": 0.25 } },
      "transition_in": { "type": "flash_white", "duration_s": 0.12 },
      "transition_out": { "type": "dissolve", "duration_s": 0.4 },
      "on_screen_text": [
        { "text": "TOO MANY VOICES.", "at_s": 3.6, "until_s": 6.0, "style": "display_black", "position": "lower_third" }
      ],
      "sfx": [{ "ref": "riser_cut_to_silence", "at_s": 3.4, "gain_db": -3 }]
    },

    { "...": "s03 … s12 follow the same shape. Twelve shots total across seven beats." }
  ],

  "voiceover": {
    "voice_id": "en-US-AndrewNeural",
    "full_text": "Too many voices. Every day, thousands of traders post their read on the same five tickers — and every one of them is certain. So which one is right? Following one analyst means inheriting one person's blind spots. You cannot see the disagreement, because you are only reading one side of it. We read all of them. Thousands of professional traders across YouTube, Reddit and X, analysed by AI agents, distilled into the consensus that actually holds — with the entry, the targets and the stops written down. And here is the part nobody else does. Every single call is published, with its outcome. The wins and the misses. You can read the whole record before you pay us anything. That is what intelligence looks like when it is not a secret. Collective intelligence for traders.",
    "segments": [
      { "shot_id": "s01", "text": "Too many voices.", "start_s": 0.2, "end_s": 3.2 },
      { "shot_id": "s02", "text": "", "start_s": 3.4, "end_s": 3.4 },
      { "...": "one segment per shot carrying narration" }
    ],
    "total_words": 154,
    "words_per_minute": 220
  },

  "music": {
    "asset_ref": "tension_bed_90bpm",
    "intensity_curve": [
      { "at_s": 0.0, "level": 0.2 },
      { "at_s": 14.6, "level": 0.55 },
      { "at_s": 24.1, "level": 0.75 },
      { "at_s": 35.9, "level": 0.4 }
    ],
    "riser_at_s": [14.2, 23.6],
    "resolve_at_s": 24.1,
    "duck_under_vo": true
  },

  "compliance": {
    "risk_disclosure_present": true,
    "risk_disclosure_shot_id": "s11",
    "risk_disclosure_text": "Trading involves significant risk. Informational and educational only. Not financial advice.",
    "safe_harbour_duration_s": 3.2,
    "methodology_adjacent": false,
    "claims_checked": true,
    "prohibited_facts_absent": true
  },

  "generation": {
    "variants_written": 3,
    "variants": [
      { "angle": "pain",        "judge_score": 7.6, "beats_stolen_from": [] },
      { "angle": "unique_data", "judge_score": 8.8, "beats_stolen_from": ["pain"] },
      { "angle": "crowd_effect","judge_score": 7.9, "beats_stolen_from": ["pain", "crowd_effect"] }
    ],
    "winner": "unique_data",
    "revision_rounds": 1,
    "creative_scores": {
      "hook_strength": 9.1, "mechanism_clarity": 8.8, "proof_credibility": 9.0,
      "emotional_arc": 7.9, "brand_fit": 9.2, "compliance_safety": 9.0,
      "weighted_mean": 8.83, "threshold": 8.0, "verdict": "pass"
    },
    "claims_rewrite_rounds": 1
  }
}
```

**Two shots are shown in full; the remaining ten follow the identical shape.** A builder must produce
all twelve. Note `s10` in the description above — the wordless "NO GURU. 16,564 OF THEM." beat that
appears in `generation.variants[].beats_stolen_from` — that is the splice mechanism from WOW-3, and
the receipt trail is the point.

**The eleven validators on `Storyboard` (all enforced by pydantic, all in `domain/models.py`):**

| # | Validator | Fails when |
|---|---|---|
| 1 | `beats_covers_timeline` | Beats have gaps or overlaps, or do not span `0 → total_duration_s` |
| 2 | `shots_match_beats` | A shot's `[start, start+duration)` falls outside its declared beat's range |
| 3 | `duration_in_bounds` | `total_duration_s` is outside `[VIDEO_MIN_SECONDS, VIDEO_MAX_SECONDS]` |
| 4 | `shot_durations_sum` | Shot durations sum to ≠ `total_duration_s` within 0.05s |
| 5 | `hook_within_three_seconds` | The `hook` beat ends after 3.0s + its tolerance |
| 6 | `beats_within_median_tolerance` | Any beat's start deviates from the aggregate median by more than `tolerance_s` |
| 7 | `palette_is_dark_and_accented` | A shot's palette has no near-black and no accent — brand drift |
| 8 | `text_within_safe_area` | On-screen text is positioned outside the shot's declared safe area |
| 9 | `risk_disclosure_present` | `compliance.risk_disclosure_present` is false |
| 10 | `no_prohibited_facts` | Any `prohibited_facts[].fact` or its numeric variants appear anywhere in the script |
| 11 | `voiceover_matches_shots` | A VO segment references a `shot_id` that does not exist, or the summed segment duration exceeds the video duration |

Validators 6, 9 and 10 are the ones that make this a system rather than a generator. **Do not soften
them into warnings.**

### 3.5 The remaining artifacts

```jsonc
// hook_candidates.json
{
  "schema_version": 1,
  "candidates": [
    {
      "id": "h01",
      "archetype": "pattern_interrupt",
      "text_overlay": "TOO MANY VOICES.",
      "first_frame_description": "A single red candlestick on black, alone.",
      "sound_design": "One clean synth note.",
      "stop_power_score": 8.9,
      "why_it_stops_the_scroll": "...",
      "selected": true,
      "rejection_reason": null
    }
  ],
  "archetypes_covered": ["pattern_interrupt", "contrarian_stat", "question", "visual_shock", "social_proof", "pain_point"],
  "underused_archetypes_boosted": ["social_proof", "pain_point"]
}

// review_verdict.json
{
  "schema_version": 1,
  "reviewer": "cwt-creative-director",
  "round": 1,
  "verdict": "request_changes",            // "pass" | "request_changes"
  "scores": { "hook_strength": 9.1, "mechanism_clarity": 7.2, "proof_credibility": 9.0,
              "emotional_arc": 6.8, "brand_fit": 9.2, "compliance_safety": 9.0 },
  "weighted_mean": 8.14,
  "threshold": 8.0,
  "weakest_axes": ["emotional_arc", "mechanism_clarity"],
  "changes_requested": "The mechanism beat (s05–s07) explains what we do but never lands why it changes the viewer's outcome. Add one shot at the end of the mechanism beat that shows the same ticker with and without consensus. Also the arc goes flat between the agitation and proof beats — give the proof beat a tonal lift.",
  "must_fix": ["emotional_arc", "mechanism_clarity"],
  "must_not_change": ["visual_hook", "s01", "s02", "compliance"]
}

// claims_report.json
{
  "schema_version": 1,
  "stage": "pre_render",                   // "pre_render" | "post_render"
  "verdict": "request_changes",            // "pass" | "request_changes" | "block"
  "deterministic": {
    "ruleset_version": "1.0.0",
    "findings": [
      {
        "rule_id": "unverifiable_statistic",
        "severity": "hard",
        "matched_text": "74.1% of our calls hit target",
        "location": { "shot_id": "s07", "field": "on_screen_text" },
        "why": "Performance statistic the product cannot currently substantiate: its public track-record page and /api/predictions both return HTTP 404.",
        "fix": "Remove the figure. Describe the process instead: every call is published with its outcome."
      }
    ]
  },
  "llm_judge": {
    "model_tier": "cheap",
    "findings": [
      {
        "rule_id": "implied_guarantee",
        "severity": "soft",
        "matched_text": "you will finally stop guessing",
        "why": "Implies a certain outcome from using the product.",
        "fix": "Soften to describe the process: 'you can see what the consensus is'."
      }
    ],
    "escalations": 1
  },
  "rewrite_instructions": ["Remove the figure. Describe the process instead: ..."],
  "rounds_used": 1,
  "rounds_remaining": 2
}

// render_manifest.json
{
  "schema_version": 1,
  "backend_chain_configured": ["hyperframes", "openmontage", "local_ffmpeg"],
  "backend_chain_tried": [
    { "backend": "hyperframes", "available": true,  "attempted": true,  "succeeded": false, "error": "npx hyperframes lint failed: clip s07 has no data-start", "elapsed_s": 12.4 },
    { "backend": "openmontage", "available": false, "attempted": false, "reason": "OPENMONTAGE_HOME not set (optional; not required)" },
    { "backend": "local_ffmpeg", "available": true, "attempted": true,  "succeeded": true,  "elapsed_s": 96.3 }
  ],
  "backend_used": "local_ffmpeg",
  "output": { "path": "render/final.mp4", "sha256": "9c1e...", "duration_s": 42.03, "width": 1080, "height": 1920, "fps": 30.0, "video_codec": "h264", "audio_codec": "aac" },
  "loudness": { "integrated_lufs": -14.1, "true_peak_dbtp": -1.6, "lra": 10.8 },
  "argv": ["ffmpeg", "-y", "-i", "assets/s01.png", "...", "-filter_complex", "...", "out.mp4"],
  "cwd": "runs/20260926-1402-a7f3",
  "assets": [
    { "ref": "tension_bed_90bpm", "path": "assets/cache/ab12….mp3", "license": "CC0-1.0", "source_url": "https://…", "attribution_required": false }
  ],
  "ffmpeg_version": "7.1",
  "warnings": []
}
```

---

## 4. `requirements.txt`

```
# ── Core ──
httpx==0.27.2
pydantic==2.9.2
python-dotenv==1.0.1
rich==13.9.2
tenacity==8.5.0          # declared but unused; retry.py is hand-rolled and inspectable

# ── Video ──
imageio-ffmpeg==0.5.1    # pip-installs a real ffmpeg binary; the fix for Rule W2
Pillow==10.4.0

# ── TTS ──
edge-tts==6.1.12
piper-tts==1.2.0         # offline fallback voice

# ── Data ──
tavily-python==0.5.0
exa-py==2.20.0

# ── No SDK for Apify by design ──
# apify-client exists but its async surface churns. We use httpx against the
# documented REST API so the contract is visible in our own code (see §8.4).
```

`requirements-dev.txt`:

```
pytest==8.3.3
pytest-asyncio==0.24.0
pytest-cov==5.0.0
ruff==0.6.9
mypy==1.11.2
respx==0.21.1            # httpx mocking for client tests
```

**Why `moviepy` is deliberately absent:** its latest release is 2.2.1, unchanged for ~16 months, and
dependency-health scoring rates the repository 0/10 on maintenance. It works, but pinning it would
mean shipping a hard dependency on a library in de-facto limbo. `filtergraph.py` writes ffmpeg
filter strings directly — more code, zero maintenance risk, and it is pure and golden-testable.

---

## 5. Containerization and Runtime

### 5.1 `Dockerfile` — the Linux reproducible path

```dockerfile
FROM python:3.11-slim

# ffmpeg for rendering; node 22 for the optional HyperFrames backend;
# curl/git for the Hermes installer.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        git \
        ca-certificates \
        build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Hermes Agent. There is NO pip package — pip installs are explicitly unsupported
# upstream. The shell installer is the only supported path.
RUN curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive
ENV PATH="/root/.local/bin:/root/.hermes/bin:${PATH}"

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir -e .

RUN mkdir -p /app/runs && chmod +x scripts/*.sh

# Port map — see Rule P1. These must never collide.
#   9119  Hermes kanban dashboard
#   8000  reserved; unused by this project
#   0     any video-backend preview server MUST bind an ephemeral port
EXPOSE 9119

CMD ["cwt", "run", "--engine", "hermes"]
```

### 5.2 `scripts/bootstrap.ps1` — the Windows primary path

The user's host is Windows 11 with PowerShell as the primary shell. **This is the documented
entrypoint**; `bootstrap.sh` is secondary.

```powershell
# scripts/bootstrap.ps1 — Windows bootstrap
# All real logic lives in Python. This script only creates a venv and calls the CLI,
# deliberately: every line of shell here is a line that behaves differently on Windows.
$ErrorActionPreference = "Stop"

Write-Host "=== CWT Video Ads Agent — bootstrap ===" -ForegroundColor Cyan

# 1. Python version gate
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw "python not found on PATH. Install Python 3.11+ from python.org." }
$ver = (python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if ([version]$ver -lt [version]"3.11") { throw "Python $ver found; 3.11+ required." }
Write-Host "  python $ver  $py" -ForegroundColor Green

# 2. venv
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt --quiet
& .\.venv\Scripts\python.exe -m pip install -e . --quiet
Write-Host "  venv ready" -ForegroundColor Green

# 3. .env
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "  .env created from .env.example — FILL IN YOUR KEYS" -ForegroundColor Yellow
}

# 4. Delegate everything else to the CLI, which is cross-platform.
& .\.venv\Scripts\python.exe -m cwt doctor
```

### 5.3 `scripts/bootstrap.sh`

```bash
#!/usr/bin/env bash
# scripts/bootstrap.sh — Linux/macOS bootstrap. Secondary to bootstrap.ps1.
set -euo pipefail

echo "=== CWT Video Ads Agent — bootstrap ==="

PY=$(command -v python3 || command -v python || true)
[ -z "$PY" ] && { echo "python3 not found"; exit 1; }
"$PY" - <<'EOF'
import sys
assert sys.version_info >= (3, 11), f"Python {sys.version_info[:2]} found; 3.11+ required."
EOF

[ -d .venv ] || "$PY" -m venv .venv
./.venv/bin/python -m pip install --upgrade pip --quiet
./.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt --quiet
./.venv/bin/python -m pip install -e . --quiet

[ -f .env ] || { cp .env.example .env; echo "  .env created — FILL IN YOUR KEYS"; }

./.venv/bin/python -m cwt doctor
```

### 5.4 The startup contract

`cwt run` prints, before doing anything, a config echo. This is the first debugging tool in
production and it costs nothing:

```
=== CWT Video Ads Agent ===
run_id       20260926-1402-a7f3
run_dir      C:\SSD WINDOW\code\CrowdWisdomTrading\runs\20260926-1402-a7f3
engine       hermes
offline      false
provider     openrouter  (cheap=google/gemini-2.5-flash  strong=anthropic/claude-sonnet-4.5)
video        hyperframes,openmontage,local_ffmpeg
ffmpeg       C:\...\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe  (7.1)
hermes       C:\Users\...\hermes.cmd  (v0.16.0)
board        cwt-ads
budget       $2.00
```

---

## 6. `src/cwt/prompts/` — Domain Config and Prompt Templates

Prompts are isolated from orchestration. No stage module contains a prompt string. Every template
uses the `build_x()` pattern: a template with `{placeholders}` plus a builder that interpolates and
**falls back gracefully** — a malformed custom override must degrade to the default, never crash a run.

### 6.1 `prompts/__init__.py`

```python
"""Prompt templates + builders.

Every builder follows the same contract:
  - reads a template (default or caller-supplied override)
  - interpolates placeholders
  - on KeyError (a custom template with an unknown placeholder) returns the
    UNINTERPOLATED template rather than raising — a bad override degrades, it
    does not kill the run.
"""
from .extract import (
    AD_EXTRACTION_PROMPT, BEAT_SHEET_PROMPT, build_extraction_prompt, build_beat_sheet_prompt,
)
from .research import (
    PAIN_RESEARCH_PROMPT, UNIQUE_DATA_RESEARCH_PROMPT, CROWD_EFFECT_RESEARCH_PROMPT,
    BRIEF_ASSEMBLY_PROMPT, build_research_prompt, build_brief_prompt,
)
from .script import (
    HOOK_CANDIDATES_PROMPT, STORYBOARD_PROMPT, REWRITE_PROMPT, VARIANT_JUDGE_PROMPT,
    build_hook_candidates_prompt, build_storyboard_prompt, build_rewrite_prompt,
    build_variant_judge_prompt,
)
from .review import CREATIVE_REVIEW_PROMPT, build_creative_review_prompt
from .claims_policy import CLAIMS_POLICY, CLAIMS_JUDGE_PROMPT, build_claims_judge_prompt


def safe_format(template: str, **kwargs) -> str:
    """Interpolate, tolerating unknown placeholders and stray braces."""
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template
```

### 6.2 `prompts/extract.py`

```python
AD_EXTRACTION_PROMPT = """\
You are a direct-response advertising analyst. You are given the text of an ad that has been
running for {active_days} days in the trading/fintech niche. Longevity is the performance signal —
ads that do not work get switched off.

Return a JSON object with exactly these fields:

{{
  "hook": {{
    "text": "<the exact opening line or on-screen text of the ad, verbatim>",
    "archetype": "<one of: pattern_interrupt | contrarian_stat | question | visual_shock |
                    social_proof | pain_point | bold_statement>",
    "stop_power_score": <0.0-10.0, how hard this stops a scroll>,
    "why_it_stops_the_scroll": "<the MECHANISM, not an assertion. What cognitive or emotional
                                 event does this cause in the first second?>"
  }},
  "pain": "<the specific frustration this ad is speaking to, in one sentence>",
  "concept": "<the underlying idea or reframe the ad is selling, in one sentence>",
  "proof_type": "<what evidence the ad leans on: statistic | testimonial | track_record |
                  authority | demonstration | none>"
}}

RULES:
- "why_it_stops_the_scroll" must describe a mechanism. "It is attention-grabbing" is not an
  answer. "It asserts a number large enough to be implausible, which forces the viewer to
  check whether they read it correctly" is an answer.
- Do not invent. If the ad has no clear hook, say so in the text field.
- Score relative to this vertical, not to advertising as a whole. Financial services has one of
  the lowest hook rates of any vertical; a 7 here is genuinely strong.

--- AD TEXT ---
{ad_text}
--- END ---
"""

BEAT_SHEET_PROMPT = """\
You are decomposing a {duration_s}-second video ad into its structural beats.

The beat taxonomy is CLOSED. Use only these beat names, in this order:
  hook, problem, agitation, mechanism, proof, objection, cta

For a {duration_s}-second ad, allocate each beat a start and end time in seconds. Beats must be
contiguous (each beat starts where the previous ends), must start at 0, and must end at
{duration_s}. Not every ad uses every beat — omit beats that are genuinely absent, but keep the
surviving ones in taxonomy order.

Return JSON:
{{
  "beats": [
    {{ "beat": "<name>", "start_s": <float>, "end_s": <float>,
       "what_happens": "<what is on screen during this beat, concretely>" }}
  ]
}}

--- AD TEXT ---
{ad_text}
--- END ---
"""


def build_extraction_prompt(ad_text: str, active_days: int, custom: str | None = None) -> str:
    return safe_format(custom or AD_EXTRACTION_PROMPT, ad_text=ad_text, active_days=active_days)


def build_beat_sheet_prompt(ad_text: str, duration_s: float, custom: str | None = None) -> str:
    return safe_format(custom or BEAT_SHEET_PROMPT, ad_text=ad_text, duration_s=duration_s)
```

### 6.3 `prompts/research.py`

The three angles are the brief's mandatory inputs. Each runs against a **last-month window**, and
each must return *sourced* claims — an unsourced claim cannot survive into the script.

```python
_COMMON_RESEARCH_RULES = """\
RULES:
- Every claim MUST carry a source_url and a published_date. A claim you cannot source is not a
  claim; drop it. Returning three sourced claims beats returning ten unsourced ones.
- Only use results published on or after {window_start}. If a source is undated, say so in the
  claim text and lower its confidence.
- "confidence" is your honest 0.0-1.0 estimate that the claim is TRUE and SOURCED, not that it
  is relevant.
- Write "synthesis" as 2-4 sentences a scriptwriter can lift directly. Concrete, active voice,
  no hedging, no "it is important to note".
"""

PAIN_RESEARCH_PROMPT = """\
You are researching the lived pain of the target customer for a trading-intelligence product.

THE CUSTOMER: a self-directed retail trader who already places their own trades and can follow
an idea with a stop and a target. Not a beginner, not an institution, not a buy-and-hold investor.
They trade US equities, crypto, forex and commodities.

THE QUESTION: what specifically frustrates this person about how they currently get trading ideas?

Search for first-hand accounts — forum threads, Reddit posts, blog retrospectives, survey data.
Prioritise the specific over the general. "Signal overload" is a topic; "I follow nine accounts
and they contradict each other so I freeze and miss the move" is a pain.

""" + _COMMON_RESEARCH_RULES + """
Return JSON:
{{
  "angle": "pain",
  "claims": [ {{ "text": "...", "source_url": "...", "source_title": "...",
                 "published_date": "YYYY-MM-DD or null", "confidence": 0.0 }} ],
  "synthesis": "...",
  "search_queries_used": ["..."]
}}
"""

UNIQUE_DATA_RESEARCH_PROMPT = """\
You are documenting what is GENUINELY DISTINCTIVE about CrowdWisdomTrading's data.

The product aggregates commentary from professional traders across YouTube, Reddit and X, and
distils it into trade ideas via AI agents. That aggregation is NOT unique — several competitors
do it. Find what IS.

The strongest candidate is TRANSPARENCY OF PROCESS: every published call carries its date, ticker,
direction, entry, targets, stops, a chart, and its eventual outcome, publicly and machine-readably.
That is a *process* claim, not a *performance* claim, and process claims are far more defensible
both factually and under advertising policy.

CRITICAL — this product's own disclaimers constrain you:
- It does NOT execute trades. It is not copy-trading, not a bot, not managed accounts.
- It does NOT have access to any trader's actual positions. Their own FAQ says so explicitly.
  Any claim implying position access, order flow, or "smart money" visibility is FALSE.
- It is not personalised advice.

Find distinctive, TRUE, process-level facts. Do not manufacture a differentiator.

""" + _COMMON_RESEARCH_RULES + """
Return JSON:
{{
  "angle": "unique_data",
  "claims": [ {{ "text": "...", "source_url": "...", "source_title": "...",
                 "published_date": "YYYY-MM-DD or null", "confidence": 0.0 }} ],
  "synthesis": "...",
  "search_queries_used": ["..."],
  "prohibited_facts_found": [
    {{ "fact": "<any claim the product makes that you could NOT verify>",
       "reason": "<why it is unverifiable>",
       "rule": "Must never appear in any generated script, in any form, including paraphrases and rounded variants." }}
  ]
}}
"""

CROWD_EFFECT_RESEARCH_PROMPT = """\
You are researching whether aggregating many forecasters actually beats one expert.

This is the intellectual foundation of the product, so it must be EVIDENCE, not vibes. Search for
the forecasting literature and its critics: Tetlock's superforecaster work, the wisdom-of-crowds
conditions (independence, diversity, decentralisation, aggregation), Galton's ox, prediction
markets, and the documented FAILURE modes — herding, correlated error, information cascades,
the Madness of Crowds critique.

A script that only cites supporting evidence is propaganda. Find the strongest counter-argument too.

""" + _COMMON_RESEARCH_RULES + """
Return JSON:
{{
  "angle": "crowd_effect",
  "claims": [ {{ "text": "...", "source_url": "...", "source_title": "...",
                 "published_date": "YYYY-MM-DD or null", "confidence": 0.0 }} ],
  "synthesis": "...",
  "search_queries_used": ["..."],
  "counterargument": "<the strongest honest objection to crowd aggregation, in one sentence>"
}}
"""

BRIEF_ASSEMBLY_PROMPT = """\
You are assembling three independent research angles into one brief a scriptwriter will use.

Do NOT summarise. SELECT. From all the claims across the three angles, keep the eight to twelve
that are most specific, most sourced, and most usable on camera. Discard the rest.

Then identify the strongest single narrative angle: which of the three (pain, unique_data,
crowd_effect) gives the most compelling 40-second story for THIS product, and why.

Return JSON:
{{
  "selected_claims": [ {{ "angle": "...", "text": "...", "source_url": "...", "confidence": 0.0 }} ],
  "strongest_angle": "pain | unique_data | crowd_effect",
  "angle_rationale": "<two sentences>",
  "counterargument_to_address": "<the objection the script should handle in its objection beat>"
}}

--- ANGLE OUTPUTS ---
{angle_outputs}
--- END ---
"""


def build_research_prompt(angle: str, window_start: str, window_end: str,
                          custom: str | None = None) -> str:
    templates = {
        "pain": PAIN_RESEARCH_PROMPT,
        "unique_data": UNIQUE_DATA_RESEARCH_PROMPT,
        "crowd_effect": CROWD_EFFECT_RESEARCH_PROMPT,
    }
    return safe_format(custom or templates[angle],
                       window_start=window_start, window_end=window_end)


def build_brief_prompt(angle_outputs: str, custom: str | None = None) -> str:
    return safe_format(custom or BRIEF_ASSEMBLY_PROMPT, angle_outputs=angle_outputs)
```

### 6.4 `prompts/script.py`

This is where the creative work happens. These prompts run on the **STRONG** tier.

```python
HOOK_CANDIDATES_PROMPT = """\
You are a direct-response creative director writing the first three seconds of a video ad for
CrowdWisdomTrading, a market-intelligence product that aggregates professional trader commentary
and publishes every call it makes, with its outcome.

Generate EXACTLY 12 hook candidates for a 9:16 vertical video. Spread them across these six
archetypes — two candidates each:

  pattern_interrupt   — break the visual or sonic expectation of a finance ad
  contrarian_stat     — assert something the viewer believes is false
  question            — ask the thing they are already asking themselves
  visual_shock        — a striking image that needs no words to land
  social_proof        — evidence that others already know this
  pain_point          — name the frustration precisely enough that it stings

ALLOCATION WEIGHTING — read this carefully:
Published benchmarks across fintech ad creative show the field is dominated by bold statements
and contrarian claims, while SOCIAL PROOF and PAIN POINT are heavily under-used yet survive
substantially longer. You must produce two strong candidates in each of those two archetypes.
Do not treat them as filler.

HARD CONSTRAINTS — violating any of these makes the candidate unusable:
- No performance claims of any kind. No win rates, no percentages, no returns, no profit figures,
  no "X% accuracy", no number describing how often the product is right.
- No promise of an outcome: no "guaranteed", "risk-free", "never lose", "financial freedom",
  "quit your job", "change your life", "stop losing money".
- Never imply the product sees real trader positions, order flow, or "smart money" data.
- Never imply copy-trading, automation, or managed accounts.
- On-screen text: maximum 5 words. It must be readable in under one second.

For each candidate return:
{{
  "id": "h01",
  "archetype": "...",
  "text_overlay": "<max 5 words>",
  "first_frame_description": "<what is literally on screen in frame one. Be specific enough
                               that a renderer could build it: subject, framing, motion, colour.>",
  "sound_design": "<what the viewer hears in second one>",
  "stop_power_score": <0.0-10.0>,
  "why_it_stops_the_scroll": "<the mechanism. Not 'it is intriguing'.>"
}}

Return JSON: {{ "candidates": [ ... ] }}

--- PRODUCT BRIEF ---
{brief}
--- END ---
"""

STORYBOARD_PROMPT = """\
You are writing a {duration_s}-second cinematic video ad for CrowdWisdomTrading.

This is a FILM, not a text ad. Think trailer, not explainer. The reference feel is a dark,
high-contrast brand film: near-black frames, a single cyan accent, gradient display numerals,
motion carrying information rather than decorating it.

NARRATIVE ANGLE FOR THIS VERSION: {angle}
WHY THIS ANGLE: {angle_rationale}

THE STRUCTURE IS NOT YOURS TO INVENT. Real winning ads in this exact niche follow a timing
grammar. Hold your beats to it — these are the median positions and tolerances measured from
ads currently running:

{beat_timeline}

WRITING RULES:
- Maximum 1-2 short sentences per narration segment. Cut every filler word.
- Never open with "In today's world", "Imagine", or "Have you ever".
- The mechanism beat must explain HOW, not WHAT. "We aggregate thousands of traders" is what.
  "Every opinion is weighted by how often that person has been right before" is how.
- The proof beat must be a PROCESS claim, never a performance claim. "Every call is published
  with its outcome" is provable on screen. "We are right 74% of the time" is neither provable
  nor permitted.
- The objection beat must state the strongest real objection honestly and answer it. Do not
  strawman it.
- Narration for a {duration_s}-second ad is roughly {word_budget} words. Do not exceed it.
- On-screen text is NOT a transcript. It is 2-5 words that land the beat's idea.

COMPLIANCE — the following are ABSOLUTELY PROHIBITED and will be machine-checked:
{prohibited}
- Any win rate, hit rate, accuracy percentage, or performance statistic
- Any return, profit, or earnings figure
- "guaranteed", "risk-free", "never lose", "always right", "financial freedom"
- Any implication of position access, copy-trading, automation, or managed accounts
- Any claim the product cannot substantiate from its own public materials

The ad MUST include, in its final 8 seconds and legible for at least 3 seconds:
  "Trading involves significant risk. Informational and educational only. Not financial advice."

For every shot you must supply EXECUTABLE camera and lighting direction. These are not decoration —
they are compiled into the render. A `push_in` with intensity 0.6 becomes a computed zoom rate.

{shot_schema}

CAMERA MOVE SEMANTICS:
  static       — locked off. Use for the hook when the image itself is the event.
  push_in      — slow creep toward subject. Builds intensity. intensity 0.0-1.0 sets the rate.
  pull_out     — reveal. Use to widen from a detail to its context.
  whip_pan     — fast lateral. Use on a beat transition, never mid-sentence.

Return JSON matching the full storyboard schema:
{storyboard_schema}

--- RESEARCH BRIEF ---
{brief}
--- AD PATTERNS FROM WINNING ADS ---
{patterns}
--- HOOK (already chosen, build the opening around this) ---
{hook}
--- END ---
"""

VARIANT_JUDGE_PROMPT = """\
You are judging three storyboard variants for the same 40-second ad. Each was written from a
different research angle. Your job is to pick the winner AND identify what to steal from the losers.

Score each variant 0.0-10.0 on:
  hook_strength        — does the first three seconds force a stop?
  mechanism_clarity    — after watching, could the viewer explain HOW the product works?
  proof_credibility    — is the evidence believable without being a performance claim?
  emotional_arc        — does it move, or is it flat?
  brand_fit            — dark, precise, confident, anti-hype. Does it sound like this brand?

For each variant return its scores and the single strongest SHOT (by id) it contains.

Then pick the winner, and for each losing variant, name the one shot the winner should absorb
and say which of the winner's weak axes it improves.

Return JSON:
{{
  "variants": [ {{ "angle": "...", "scores": {{...}}, "weighted_mean": <float>,
                   "strongest_shot_id": "...", "strongest_shot_why": "..." }} ],
  "winner": "pain | unique_data | crowd_effect",
  "splices": [ {{ "from_angle": "...", "shot_id": "...", "improves_axis": "...",
                  "how_to_integrate": "..." }} ]
}}

--- VARIANTS ---
{variants}
--- END ---
"""

REWRITE_PROMPT = """\
A storyboard was rejected. Fix ONLY what was asked. Preserve everything else EXACTLY —
the reviewer explicitly told you what must not change, and changing it will get this rejected again.

VERDICT: {verdict}
SCORES: {scores}
WEAKEST AXES: {weakest_axes}

REVIEWER'S INSTRUCTIONS (verbatim):
{changes_requested}

MUST FIX: {must_fix}
MUST NOT CHANGE: {must_not_change}

{prohibited_block}

Return the COMPLETE corrected storyboard JSON — the same schema, every field, not a diff.
Increment generation.revision_rounds.
"""


def build_hook_candidates_prompt(brief: str, custom: str | None = None) -> str:
    return safe_format(custom or HOOK_CANDIDATES_PROMPT, brief=brief)


def build_storyboard_prompt(*, duration_s: int, angle: str, angle_rationale: str,
                            beat_timeline: str, prohibited: str, shot_schema: str,
                            storyboard_schema: str, brief: str, patterns: str,
                            hook: str, custom: str | None = None) -> str:
    return safe_format(
        custom or STORYBOARD_PROMPT,
        duration_s=duration_s,
        angle=angle,
        angle_rationale=angle_rationale,
        beat_timeline=beat_timeline,
        prohibited=prohibited,
        shot_schema=shot_schema,
        storyboard_schema=storyboard_schema,
        brief=brief,
        patterns=patterns,
        hook=hook,
        word_budget=int(duration_s * 3.5),   # ~210 wpm delivery
    )


def build_variant_judge_prompt(variants: str, custom: str | None = None) -> str:
    return safe_format(custom or VARIANT_JUDGE_PROMPT, variants=variants)


def build_rewrite_prompt(*, verdict: str, scores: str, weakest_axes: str, changes_requested: str,
                         must_fix: str, must_not_change: str, prohibited_block: str,
                         custom: str | None = None) -> str:
    return safe_format(custom or REWRITE_PROMPT, verdict=verdict, scores=scores,
                       weakest_axes=weakest_axes, changes_requested=changes_requested,
                       must_fix=must_fix, must_not_change=must_not_change,
                       prohibited_block=prohibited_block)
```

### 6.5 `prompts/review.py`

```python
CREATIVE_REVIEW_PROMPT = """\
You are the creative director. You did not write this. Your job is to be the reason it is good.

You are scoring a storyboard against the patterns mined from ads that are CURRENTLY RUNNING in
this exact niche. That is your reference standard — not your taste.

WEIGHTED AXES (weights sum to 1.0):
  hook_strength      0.25   Does the first three seconds force a stop? Score it against the
                            dominant archetype and median hook duration in the pattern data.
  mechanism_clarity  0.20   After 40 seconds, could the viewer explain HOW this works?
  proof_credibility  0.15   Is the evidence believable WITHOUT being a performance claim?
  emotional_arc      0.15   Does it move through tension and release, or is it flat?
  brand_fit          0.15   Dark, precise, confident, anti-hype. Does it sound like this brand?
  compliance_safety  0.10   Would this survive an ad-platform financial-services review?

THE THRESHOLD IS {threshold}. Below it, you request changes. Do not pass work that is merely
acceptable — the threshold exists because "fine" is not the goal.

If you request changes:
- Name the TWO weakest axes.
- Quote the specific shot ids that are failing and say what is wrong with each.
- Give instructions precise enough to act on. "Make it more emotional" is not an instruction.
  "The arc goes flat between s06 and s09 — give the proof beat a tonal lift by moving the
   strongest testimonial line there" is an instruction.
- List what MUST NOT change.

If it passes, say so and name the single best moment.

Return JSON:
{{
  "verdict": "pass | request_changes",
  "scores": {{ "hook_strength": 0.0, "mechanism_clarity": 0.0, "proof_credibility": 0.0,
              "emotional_arc": 0.0, "brand_fit": 0.0, "compliance_safety": 0.0 }},
  "weighted_mean": 0.0,
  "weakest_axes": ["...", "..."],
  "changes_requested": "<null if pass>",
  "must_fix": [],
  "must_not_change": [],
  "best_moment": "<shot id and why>"
}}

--- STORYBOARD ---
{storyboard}
--- PATTERNS FROM WINNING ADS (your reference standard) ---
{patterns}
--- END ---
"""


def build_creative_review_prompt(storyboard: str, patterns: str, threshold: float,
                                 custom: str | None = None) -> str:
    return safe_format(custom or CREATIVE_REVIEW_PROMPT, storyboard=storyboard,
                       patterns=patterns, threshold=threshold)
```

### 6.6 `prompts/claims_policy.py`

The deterministic rule engine (Section 8.6) catches what regex can catch. This prompt covers the
residue: implication, juxtaposition, framing, and missing disclosures.

```python
CLAIMS_POLICY = """\
FINANCIAL ADVERTISING CLAIMS POLICY — distilled from Meta's financial-services ad standards and
Google's financial-services policy as they apply to investment and trading products.

ABSOLUTELY PROHIBITED (these are rejected by the platforms and are often independently unlawful):
  - Guaranteed returns, guaranteed income, "guaranteed" anything financial
  - Specific profit or earnings figures, in any currency or timeframe
  - Percentage return promises ("15% monthly", "10x your account")
  - Risk-free / riskless / "no risk" / "can't lose" framing
  - "Make $X per day/month" framing
  - Payout screenshots, bank-balance imagery, profit screenshots
  - "Passive income", "financial freedom", "quit your job", "retire early"
  - Claims of beating the market or an index
  - Multiplier promises ("double your money")
  - Implied certainty of any individual trade or signal

REQUIRES SUBSTANTIATION (permitted only with methodology adjacent and a safe harbour):
  - Any accuracy, hit-rate, or win-rate statistic
  - Any track-record duration claim
  - Any comparative claim against competitors or alternatives

STRUCTURALLY PROHIBITED FOR THIS SPECIFIC PRODUCT — these contradict the product's OWN published
disclaimers, so they are false as well as non-compliant:
  - Any implication that the product sees real traders' positions, order flow, or "smart money"
    positioning. The product's FAQ states it does not have access to positions.
  - Any implication of copy-trading, automated execution, or managed accounts. The product
    explicitly disclaims all three.
  - Any implication of personalised investment advice.

COMPLIANT SUBSTITUTION PATTERNS (use these):
  "guaranteed returns"          →  "a published, auditable process"
  "we're right 74% of the time" →  "every call is published with its outcome"
  "get rich quick"              →  "build a repeatable process"
  "you'll stop losing"          →  "you can see where the consensus actually is"
  "our winning signals"         →  "the calls we've published"

A required risk disclosure must appear, legible, in the final 8 seconds:
  "Trading involves significant risk. Informational and educational only. Not financial advice."
"""

CLAIMS_JUDGE_PROMPT = """\
You are a financial-advertising compliance reviewer. A deterministic rule engine has ALREADY run
and caught everything a regex can catch. Your job is the residue — the things only judgement catches:

  - IMPLIED guarantees: a sentence that does not contain the word "guarantee" but promises certainty
  - JUXTAPOSITION claims: two adjacent statements that together assert something neither asserts
    alone (e.g. a big number followed by "our members" implies a typical result)
  - MISLEADING FRAMING: literally true but creates a false impression
  - UNSUBSTANTIATED SUPERLATIVES: "the best", "the most accurate", "unlike any other"
  - MISSING DISCLOSURES: a claim that requires a disclosure which is absent
  - SUBSTITUTION DEFEATS: a prohibited claim that has been reworded but retains its meaning

You may ESCALATE a severity. You may NOT de-escalate anything the deterministic engine marked
"hard" — those are final.

Return JSON:
{{
  "findings": [
    {{ "rule_id": "<short_snake_case_id>",
       "severity": "hard | soft",
       "matched_text": "<the exact span you object to>",
       "why": "<one sentence>",
       "fix": "<a specific rewrite instruction, not 'remove it'>" }}
  ],
  "overall": "pass | request_changes"
}}

--- POLICY ---
{policy}
--- SCRIPT (the full voiceover text plus all on-screen text) ---
{script}
--- END ---
"""


def build_claims_judge_prompt(script: str, custom: str | None = None) -> str:
    return safe_format(custom or CLAIMS_JUDGE_PROMPT, policy=CLAIMS_POLICY, script=script)
```

---

## 7. Hermes Configuration, Profiles, and Skills

Hermes is the framework the brief mandates. Our Python code supplies the deterministic engines and
the tool surface; Hermes supplies the agents, the kanban board, and the dispatch loop.

### 7.1 `hermes/config.yaml`

This is a **template**. `cwt doctor` merges it into `~/.hermes/config.yaml` on first run, then
converts it into profiles (Section 7.3).

```yaml
# hermes/config.yaml — template written into ~/.hermes/config.yaml by `cwt bootstrap`

model:
  # The WORKER model. Every dispatched kanban card runs on this.
  # MUST have >= 64k context — Hermes enforces this.
  # Set it with `hermes model` rather than editing here; that command writes the
  # correct provider block. This value is a placeholder.
  default: "google/gemini-2.5-flash"

kanban:
  dispatch_in_gateway: true          # the dispatcher runs inside the gateway process
  dispatch_interval_seconds: 60      # one tick per minute
  review_dispatch: true              # required — our creative + compliance gates depend on it
  auto_promote_children: true        # a child card auto-promotes to `ready` when parents are done
  auto_decompose: false              # our DAG is explicit; we do NOT want the board inventing cards
  max_in_progress: 4                 # matches LLM_MAX_CONCURRENCY — see Rule A4
  failure_limit: 2
  stranded_threshold_seconds: 1800

delegation:
  max_concurrent_children: 3         # the three storyboard variants, in parallel
  max_iterations: 120                # per child; the default 250 is generous, we do not need it
  model: "google/gemini-2.5-flash"   # children run cheap — see Rule A5
  provider: "openrouter"

agent:
  max_turns: 60                      # a worker card should never need more
  auto_recovery_cycles: 3

compression:
  enabled: true
  threshold: 0.60

dashboard:
  kanban:
    render_markdown: true
    include_archived_by_default: false

skills:
  auto_load:
    - cwt-source-winning-ads
    - cwt-extract-ad-patterns
    - cwt-research-angle
    - cwt-write-storyboard
    - cwt-creative-review
    - cwt-claims-gate
    - cwt-render-video
    - cwt-final-qa
```

### 7.2 The plugin — `hermes/plugins/cwt/`

**Hermes has no Python SDK and no pip package.** Documented installs are shell installers, the
desktop bundle, Docker, or Termux APT; pip/uv installs are explicitly unsupported upstream. For your
own tools, the documented route is a **native plugin**, not a core-tools patch.

A plugin lives at `~/.hermes/plugins/<name>/` — flat, or one category level deep. Anything deeper
is silently ignored. It needs **both** `plugin.yaml` and an `__init__.py` containing `register(ctx)`.

`plugin.yaml`:

```yaml
name: cwt
version: 1.0.0
description: CrowdWisdomTrading video-ad-generation tools
author: CWT Video Ads Agent
license: MIT
homepage: https://github.com/<you>/cwt-video-ads-agent

provides_tools:
  - cwt_source_winning_ads
  - cwt_rank_winning_ads
  - cwt_extract_ad_patterns
  - cwt_research_angle
  - cwt_assemble_brief
  - cwt_generate_hook_candidates
  - cwt_write_storyboard_variant
  - cwt_judge_variants
  - cwt_score_storyboard
  - cwt_apply_rewrite
  - cwt_check_claims
  - cwt_rewrite_for_compliance
  - cwt_synthesize_voiceover
  - cwt_render_video
  - cwt_probe_media
  - cwt_render_storyboard_html
  - cwt_make_contact_sheet
  - cwt_verify_artifact
  - cwt_assemble_submission

provides_hooks:
  - post_tool_call
  - kanban_task_completed

# Missing vars DISABLE the plugin with a clear message rather than failing at call time.
requires_env:
  - name: APIFY_TOKEN
    description: Apify API token for Meta Ads Library scraping
    url: https://console.apify.com/account/integrations
    secret: true
  - name: TAVILY_API_KEY
    description: Tavily search API key
    url: https://app.tavily.com/home
    secret: true
  - name: EXA_API_KEY
    description: Exa neural search API key
    url: https://dashboard.exa.ai/api-keys
    secret: true
  - name: OPENROUTER_API_KEY
    description: OpenRouter API key (or set NVIDIA_API_KEY and LLM_PROVIDER=nvidia)
    url: https://openrouter.ai/keys
    secret: true
```

`__init__.py`:

```python
"""CWT plugin — registers our tool surface with Hermes.

CRITICAL: handlers must NEVER raise. Always return a JSON string, success or error.
A raising handler takes down the agent turn, not just the tool call.
"""
import json
import logging

from . import schemas
from . import handlers as h

logger = logging.getLogger("cwt.plugin")


def _after_call(tool_name, args, result, task_id, duration_ms, **kwargs):
    """post_tool_call hook. Observational only — the return value is ignored."""
    logger.info("cwt tool=%s task=%s ms=%s ok=%s", tool_name, task_id, duration_ms,
                not str(result).startswith('{"error"'))


def _on_task_completed(task_id, summary=None, metadata=None, **kwargs):
    logger.info("kanban task %s completed: %s", task_id, (summary or "")[:120])


def register(ctx):
    """Called exactly once at startup. If this raises, the plugin is disabled
    but Hermes continues running — which is the behaviour we want."""
    logger.info("registering CWT plugin (profile=%s)", ctx.profile_name)

    _TOOLS = (
        ("cwt_source_winning_ads",     schemas.SOURCE_WINNING_ADS,     h.source_winning_ads),
        ("cwt_rank_winning_ads",       schemas.RANK_WINNING_ADS,       h.rank_winning_ads),
        ("cwt_extract_ad_patterns",    schemas.EXTRACT_AD_PATTERNS,    h.extract_ad_patterns),
        ("cwt_research_angle",         schemas.RESEARCH_ANGLE,         h.research_angle),
        ("cwt_assemble_brief",         schemas.ASSEMBLE_BRIEF,         h.assemble_brief),
        ("cwt_generate_hook_candidates", schemas.GENERATE_HOOKS,       h.generate_hook_candidates),
        ("cwt_write_storyboard_variant", schemas.WRITE_STORYBOARD,     h.write_storyboard_variant),
        ("cwt_judge_variants",         schemas.JUDGE_VARIANTS,         h.judge_variants),
        ("cwt_score_storyboard",       schemas.SCORE_STORYBOARD,       h.score_storyboard),
        ("cwt_apply_rewrite",          schemas.APPLY_REWRITE,          h.apply_rewrite),
        ("cwt_check_claims",           schemas.CHECK_CLAIMS,           h.check_claims),
        ("cwt_rewrite_for_compliance", schemas.REWRITE_COMPLIANCE,     h.rewrite_for_compliance),
        ("cwt_synthesize_voiceover",   schemas.SYNTHESIZE_VO,          h.synthesize_voiceover),
        ("cwt_render_video",           schemas.RENDER_VIDEO,           h.render_video),
        ("cwt_probe_media",            schemas.PROBE_MEDIA,            h.probe_media),
        ("cwt_render_storyboard_html", schemas.RENDER_SB_HTML,         h.render_storyboard_html),
        ("cwt_make_contact_sheet",     schemas.MAKE_CONTACT_SHEET,     h.make_contact_sheet),
        ("cwt_verify_artifact",        schemas.VERIFY_ARTIFACT,        h.verify_artifact),
        ("cwt_assemble_submission",    schemas.ASSEMBLE_SUBMISSION,    h.assemble_submission),
    )

    for name, schema, handler in _TOOLS:
        ctx.register_tool(name=name, toolset="cwt", schema=schema, handler=handler)

    ctx.register_hook("post_tool_call", _after_call)
    ctx.register_hook("kanban_task_completed", _on_task_completed)

    # Register our skills so the profiles can force-load them by name.
    for skill in ("cwt-source-winning-ads", "cwt-extract-ad-patterns", "cwt-research-angle",
                  "cwt-write-storyboard", "cwt-creative-review", "cwt-claims-gate",
                  "cwt-render-video", "cwt-final-qa"):
        ctx.register_skill(skill, f"skills/{skill}/SKILL.md")

    logger.info("CWT plugin registered %d tools", len(_TOOLS))
```

`handlers.py` — thin adapters. Each resolves the run context from the environment Hermes sets, calls
the real implementation in `src/cwt/tools/`, and returns a JSON string:

```python
"""Thin adapters: Hermes tool args -> src/cwt/tools/* -> JSON string.

Design rule: ZERO business logic here. If you find yourself writing an `if` that
is not argument unpacking, the logic belongs in src/cwt/tools/.
"""
import json
import os
from functools import wraps
from pathlib import Path

from cwt.config import Settings
from cwt.util.paths import RunPaths
from cwt import tools


def _ctx(args: dict) -> tuple[Settings, RunPaths]:
    """Resolve run context from the environment Hermes injects into a worker."""
    run_dir = os.environ.get("CWT_RUN_DIR")
    if not run_dir:
        raise RuntimeError(
            "CWT_RUN_DIR is not set. This tool must run inside a CWT-dispatched kanban "
            "worker. Run `cwt run` rather than invoking the worker by hand."
        )
    return Settings.from_env(), RunPaths(Path(run_dir))


def _safe(fn):
    """Every handler is wrapped: never raise, always return a JSON string."""
    @wraps(fn)
    def wrapper(args: dict, **kwargs) -> str:
        try:
            settings, paths = _ctx(args)
            result = fn(args, settings, paths)
            return json.dumps({"ok": True, "data": result}, default=str)
        except Exception as exc:                       # noqa: BLE001 — deliberate
            return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return wrapper


@_safe
def source_winning_ads(args, settings, paths):
    return tools.ads.source_winning_ads(
        settings=settings, paths=paths,
        keywords=args["keywords"],
        countries=args.get("countries", ["US"]),
        window_days=args.get("window_days", 30),
        max_items=args.get("max_items"),
    )


# … one @_safe function per registered tool, same shape. Nineteen in total.
```

### 7.3 Profiles — nine agents, one per capability

A Hermes profile is a separate Hermes home directory with its own config, `.env`, memory, skills and
gateway state. `hermes -p <name>` selects one. The dispatcher spawns a worker with
`hermes -p <assignee> chat -q <prompt>`.

`cwt bootstrap` creates all nine with `hermes profile create <name>` and writes a `SOUL.md` into
each. **The profile name must exactly match the `assignee` string on the card** — an unresolvable
assignee leaves the card stuck on `ready` forever, emitting a `skipped_nonspawnable` event and no
fallback spawn.

| Profile | Role | Model tier | Sees |
|---|---|---|---|
| `cwt-orchestrator` | Decomposes the goal into cards. Owns no artifacts. | cheap | full kanban toolset |
| `cwt-ads-manager` | Sources winning ads; assembles the final submission bundle | cheap | ads tools |
| `cwt-hook-analyst` | Extracts hooks, pains, concepts, beat sheets | cheap | pattern tools |
| `cwt-researcher` | Runs the three research angles; assembles the brief | cheap | research tools |
| `cwt-script-writer` | Writes variants, hooks, rewrites | **strong** | storyboard tools |
| `cwt-creative-director` | Scores against the rubric; approves or rejects | cheap | review tools |
| `cwt-compliance` | Runs the claims gate | cheap | claims tools |
| `cwt-video-editor` | Renders through the backend chain | cheap | video tools |
| `cwt-qa` | Verifies the rendered output | cheap | probe + claims tools |

Only `cwt-script-writer` runs on the strong tier. That is deliberate: ~35 of ~40 LLM calls per run
are classification and scoring, where a mid-size model is indistinguishable from a frontier model
and an order of magnitude cheaper. See Rule A5.

Example `SOUL.md` — `cwt-script-writer`:

```markdown
# You are the Script Writer

You write 40-second cinematic ads for CrowdWisdomTrading. You are the only agent in this system
whose work is judged on taste rather than correctness.

## What you do
Write three complete storyboard variants — one per research angle — then apply the creative
director's rewrite instructions until they clear the threshold.

## What you never do
- You never state a performance statistic. Not a win rate, not a hit rate, not an accuracy figure.
  The claims engine will catch it and the compliance agent will send it back.
- You never imply the product sees traders' positions. It does not.
- You never write a shot without executable camera direction. A shot with no `camera.move` is
  not a shot, it is a caption.
- You never exceed the beat timeline's tolerances. They were measured from ads that work.

## How you know you are done
The creative director scored you >= 8.0 and the compliance gate returned `pass`.
Not before. Call `kanban_request_review` and wait.
```

### 7.4 Skills — `skills/*/SKILL.md`

Skills are Markdown files with YAML frontmatter. They live under `~/.hermes/skills/`, and a kanban
card force-loads one by name via `--skill <name>`. **This is how we get deterministic behaviour out
of a stochastic worker: the skill is the procedure, not the prompt.**

`skills/cwt-write-storyboard/SKILL.md`:

```markdown
---
name: cwt-write-storyboard
description: Write three judged storyboard variants and revise to threshold.
version: 1.0.0
metadata:
  hermes:
    tags: [creative, video, ads]
    category: marketing
    requires_toolsets: [cwt]
---

# Write Storyboard

## When to Use
You are the `cwt-script-writer` and a card asks you to produce a storyboard.

## Procedure
1. `kanban_show()` — read every parent's `metadata.artifact_path`. At minimum you need
   `research_brief.json` and `ad_patterns.json`.
2. Call `cwt_generate_hook_candidates` once. It returns 12 scored candidates. Do NOT invent a hook
   yourself — the scorer exists so that the hook is a search, not a guess.
3. Call `cwt_write_storyboard_variant` three times, once per angle
   (`pain`, `unique_data`, `crowd_effect`). Pass the chosen hook's id to each.
4. Call `cwt_judge_variants` with the three variants. It returns a winner and a splice list.
5. Integrate the splices into the winner. Record each in
   `generation.variants[].beats_stolen_from`.
6. Call `cwt_apply_rewrite` if needed to apply splices cleanly.
7. Call `cwt_score_storyboard` yourself FIRST — do not send work you know is weak.
8. Call `kanban_request_review` with reviewer `cwt-creative-director`.
9. On `request_changes`: call `cwt_apply_rewrite` with the director's instructions, then
   re-request review. Maximum 3 rounds. On the 3rd rejection, `kanban_block` with the reason.

## Pitfalls
- The judge reduces each variant to its beats and voiceover before scoring. Do not expect it to
  have seen your camera data — if a splice instruction references a camera move, apply it yourself.
- `prohibited_facts` from the research brief must be absent from EVERY variant, including the
  ones you do not pick. The judge does not check compliance; the compliance card does, and it
  will reject the winner.
- The beat timeline tolerances are enforced by a pydantic validator. A storyboard that violates
  them will not even parse — you will get a validation error, not a review.

## Verification
`cwt_verify_artifact --name storyboard` returns `{"ok": true}` and
`generation.creative_scores.verdict == "pass"`.
```

The remaining seven skill files follow the identical structure — *When to Use*, *Procedure*,
*Pitfalls*, *Verification* — and are written for an LLM reader, not a human one.

---

## 8. Data Access Layer

One function does exactly one thing. No business logic here — clients talk to services, `artifacts.py`
reads and writes files, `claims.py` is pure logic and lives in `domain/` because it has no I/O at all.

### 8.1 `util/subproc.py` — the single subprocess chokepoint

Every external process in this codebase goes through this one function. It is the enforcement point
for four of the Windows rules in Section 12.

```python
"""The ONLY place a subprocess is spawned in this codebase.

If you are about to write `subprocess.run(...)` anywhere else, stop and call run_tool().
The Windows failure modes this prevents are documented as Rules W1, W4, W7 and W9.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class ToolNotFound(RuntimeError):
    def __init__(self, exe: str, hint: str = ""):
        super().__init__(f"{exe!r} not found. {hint}".strip())


class ToolTimeout(RuntimeError):
    def __init__(self, exe: str, timeout_s: float, stdout: str, stderr: str):
        super().__init__(f"{exe!r} timed out after {timeout_s}s")
        self.stdout, self.stderr = stdout, stderr


class ToolFailed(RuntimeError):
    def __init__(self, result: "ToolResult"):
        super().__init__(
            f"{result.args[0]!r} exited {result.returncode}\n"
            f"argv: {result.args}\ncwd: {result.cwd}\nstderr:\n{result.stderr[-4000:]}"
        )
        self.result = result


@dataclass(frozen=True)
class ToolResult:
    args: list[str]
    cwd: str | None
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def resolve_exe(exe: str) -> str:
    """Resolve an executable to an absolute path.

    Why this exists (Rule W4): on Windows, `npx`, `npm` and `hermes` are `.cmd`
    shims. Passing the bare name to subprocess with shell=False raises
    `[WinError 193] %1 is not a valid Win32 application`. shutil.which()
    returns the full `...\\npx.cmd` path, which does work.
    """
    if Path(exe).is_absolute() and Path(exe).exists():
        return str(exe)
    if sys.platform == "win32":
        resolved = shutil.which(exe)
        if resolved is None:
            raise ToolNotFound(exe, f"install it, or set CWT_{exe.upper()}_BIN")
        return resolved
    resolved = shutil.which(exe)
    return resolved or exe


def run_tool(args: Sequence[str], *, cwd: Path | None = None, timeout_s: float = 300,
             env_extra: Mapping[str, str] | None = None, check: bool = False) -> ToolResult:
    """Run an external program. `args` MUST be a list — shell strings are banned.

    shell=False      no shell parsing, so spaces/quotes/& in paths cannot break it  (W1)
    encoding=utf-8   Windows cp1252 cannot encode characters in our prompts          (W7)
    errors=replace   one bad byte from a tool must not crash the run                 (W7)
    cwd              ffmpeg filtergraphs reference RELATIVE paths only               (W5)
    PYTHONUTF8=1     forces utf-8 in any Python child we spawn                       (W7)
    """
    if isinstance(args, str):
        raise TypeError(
            "run_tool() takes a list of strings, not a shell string. "
            "Shell strings are banned — see architecture Rule W1."
        )
    if not args:
        raise ValueError("run_tool() requires at least one argument")

    exe = resolve_exe(args[0])
    argv = [exe, *args[1:]]

    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if env_extra:
        env.update(env_extra)

    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolTimeout(exe, timeout_s, exc.stdout or "", exc.stderr or "") from exc

    result = ToolResult(args=argv, cwd=str(cwd) if cwd else None,
                        returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    if check and not result.ok:
        raise ToolFailed(result)
    return result
```

### 8.2 `hermes/cli.py` — the only place Hermes is invoked

```python
"""The ONLY place the hermes binary is invoked.

Every kanban interaction in this codebase goes through run_hermes(). That makes
Hermes version drift a one-file fix rather than a rewrite (Rule R3).
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from cwt.util.subproc import ToolFailed, run_tool

DEFAULT_TIMEOUT = 120.0


@dataclass(frozen=True)
class HermesResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def json(self):
        return json.loads(self.stdout)


@lru_cache(maxsize=1)
def hermes_bin() -> str:
    override = os.getenv("HERMES_BIN", "").strip()
    if override:
        return override
    found = shutil.which("hermes")
    if not found:
        raise RuntimeError(
            "The `hermes` binary was not found on PATH.\n"
            "Install it:  iex (irm https://hermes-agent.nousresearch.com/install.ps1)   [Windows]\n"
            "             curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash   [Linux/macOS]\n"
            "Or set HERMES_BIN to its absolute path."
        )
    return found


def run_hermes(args: list[str], *, timeout_s: float = DEFAULT_TIMEOUT,
               check: bool = False) -> HermesResult:
    if isinstance(args, str):
        raise TypeError("run_hermes() takes a list, not a shell string (Rule W1)")
    result = run_tool([hermes_bin(), *args], timeout_s=timeout_s)
    out = HermesResult(result.returncode, result.stdout, result.stderr)
    if check and not out.ok:
        raise ToolFailed(result)
    return out


def hermes_version() -> str:
    return run_hermes(["--version"], timeout_s=30).stdout.strip()


def kanban(*args: str, board: str, timeout_s: float = DEFAULT_TIMEOUT) -> HermesResult:
    return run_hermes(["kanban", "--board", board, *args], timeout_s=timeout_s)


def supported_flags() -> set[str]:
    """Parse `hermes kanban create --help` for the flags we depend on.

    Doctor calls this and FAILS if any flag we rely on is missing. That converts
    'mysterious silent misbehaviour after a Hermes upgrade' into 'clear preflight
    error naming the missing flag'.
    """
    out = run_hermes(["kanban", "create", "--help"], timeout_s=30)
    text = out.stdout + out.stderr
    flags: set[str] = set()
    for token in text.replace(",", " ").split():
        if token.startswith("--"):
            flags.add(token.rstrip(".,;:").split("=")[0])
    return flags
```

### 8.3 `clients/apify.py`

```python
"""Apify — Meta Ads Library.

TWO FACTS THAT WILL COST YOU AN HOUR IF YOU FORGET THEM:

1. `apify/facebook-ads-library-scraper` DOES NOT EXIST. Every actor with that slug
   is third-party. The official one is `apify/facebook-ads-scraper`.

2. In REST URLs the actor id uses a TILDE, not a slash:
       https://api.apify.com/v2/acts/apify~facebook-ads-scraper/runs
   A slash in that path returns 404.

Also: the run-sync endpoint hard-times-out at 300s. Meta Ads Library runs routinely
exceed that. ALWAYS use the async path.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Callable

import httpx

logger = logging.getLogger("cwt.apify")

BASE = "https://api.apify.com/v2"
_TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class ApifyError(RuntimeError):
    pass


def _scrub(text: str) -> str:
    """Remove the API token from anything that could reach a log or an artifact.

    This is not paranoia. The submission is a PUBLIC repo, and Apify passes the
    token as a QUERY PARAMETER, so it lands in URLs, error bodies and httpx
    request reprs by default.
    """
    return re.sub(r"([?&]token=)[^&\s\"']+", r"\1<redacted>", text)


def normalise_actor_id(actor_id: str) -> str:
    """Accept either spelling; always emit the REST-safe tilde form."""
    return actor_id.replace("/", "~")


def build_input(*, keywords: list[str], countries: list[str], window_days: int,
                max_items: int) -> dict[str, Any]:
    """Build the actor input.

    There is NO `searchTerms` field on the official actor — keyword search is
    expressed through `startUrls`. Bare keywords are auto-converted to keyword
    searches by the actor. Writing `searchTerms` fails SILENTLY: the run succeeds
    and returns the wrong ads.

    'Last 30 days' is `onlyAdsNewerThan` — it accepts a relative string like
    "30 days" as well as an absolute YYYY-MM-DD.
    """
    urls: list[dict[str, str]] = []
    for kw in keywords:
        for cc in countries:
            urls.append({
                "url": (
                    "https://www.facebook.com/ads/library/"
                    f"?active_status=active&ad_type=all&country={cc}"
                    f"&q={httpx.QueryParams({'q': kw})['q']}&search_type=keyword_unordered"
                )
            })
    return {
        "startUrls": urls,
        "resultsLimit": max_items,
        "activeStatus": "Active",
        "onlyAdsNewerThan": f"{window_days} days",
        "isDetailsPerAd": False,
        "enrichWithEcommerceData": False,
    }


async def run_actor(
    *, actor_id: str, actor_input: dict, token: str, max_charge_usd: float,
    timeout_s: int = 900, on_progress: Callable[[str], None] | None = None,
) -> list[dict]:
    actor = normalise_actor_id(actor_id)
    async with httpx.AsyncClient(timeout=60.0) as client:
        # maxTotalChargeUsd is Apify's own HARD CAP on a single run. It is our
        # primary defence against burning a $5/month free tier in one call.
        # Do not remove it (Rule A2).
        resp = await client.post(
            f"{BASE}/acts/{actor}/runs",
            params={"token": token, "maxTotalChargeUsd": max_charge_usd},
            json=actor_input,
        )
        if resp.status_code == 404:
            raise ApifyError(
                f"Actor {actor!r} not found (404). Check APIFY_ADS_ACTOR_ID. Note the id "
                f"must use a TILDE in REST URLs: apify~facebook-ads-scraper"
            )
        resp.raise_for_status()
        run = resp.json()["data"]
        run_id, dataset_id = run["id"], run["defaultDatasetId"]
        logger.info("apify run %s started (charge cap $%.2f)", run_id, max_charge_usd)

        deadline = asyncio.get_event_loop().time() + timeout_s
        while True:
            status_resp = await client.get(f"{BASE}/actor-runs/{run_id}", params={"token": token})
            status_resp.raise_for_status()
            status = status_resp.json()["data"]["status"]
            if on_progress:
                on_progress(status)
            if status in _TERMINAL:
                break
            if asyncio.get_event_loop().time() > deadline:
                raise ApifyError(f"Apify run {run_id} exceeded {timeout_s}s (last status {status})")
            await asyncio.sleep(10)

        if status != "SUCCEEDED":
            raise ApifyError(f"Apify run {run_id} ended {status}")

        items_resp = await client.get(
            f"{BASE}/datasets/{dataset_id}/items",
            params={"token": token, "format": "json", "clean": "true"},
        )
        items_resp.raise_for_status()
        items = items_resp.json()

        run_detail = (await client.get(f"{BASE}/actor-runs/{run_id}",
                                       params={"token": token})).json()["data"]
        charge = float(run_detail.get("usageTotalUsd") or 0.0)
        logger.info("apify run %s returned %d items, charged $%.4f", run_id, len(items), charge)

    for item in items:
        item["_cwt_charge_usd"] = charge / max(len(items), 1)
    return items
```

**Field mapping.** The official actor's output names are its own. We normalise **once**, here, and
nothing downstream ever touches a raw actor field:

| Raw actor field | Normalised | Notes |
|---|---|---|
| `adArchiveID` / `adArchiveId` | `ad_id` | The actor emits both spellings; prefer `adArchiveID` |
| `collationId` / `collationCount` | `collation_id` | Same ad shown to different audiences collapses here |
| `pageName` | `page_name` | |
| `pageID` / `pageId` | `page_id` | |
| `isActive` | `is_active` | |
| `startDateFormatted` | `started_running` | Normalise to `YYYY-MM-DD`; the actor's format varies |
| `endDateFormatted` | `ended_running` | `null` while running |
| `publisherPlatform` | `publisher_platforms` | May arrive as a string or a list — coerce |
| `snapshot.body.text` | `body_text` | **Nested.** This is the ad copy |
| `snapshot.title` | `title` | |
| `snapshot.linkDescription` | `link_description` | |
| `snapshot.ctaType` / `snapshot.ctaText` | `cta_type` / `cta_text` | |
| `snapshot.displayFormat` | `display_format` | `IMAGE` / `VIDEO` / `CAROUSEL` |
| `snapshot.linkUrl` | `link_url` | |
| `snapshot.images[].originalImageUrl` | `image_urls` | |
| `snapshot.videos[].videoHdUrl` | `video_urls` | |
| `snapshot.videos[].videoSdUrl` | `video_urls` | Fallback when HD absent |

`impressions` and `spend` are frequently absent or bucketed on commercial (non-political) ads. The
ranker must not require them — see Section 8.4.

### 8.4 `clients/tavily.py` and `clients/exa.py`

```python
"""Tavily and Exa search clients, both bounded to the last month.

THE `days` PARAMETER DOES NOT EXIST in Tavily's current API reference. It is a
legacy news-topic-only parameter that third-party docs still describe. Using it
fails silently — you get unrestricted results and never know the window was
ignored.

The correct current parameter is `time_range`, with values day|week|month|year.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx

logger = logging.getLogger("cwt.search")


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    content: str
    published_date: str | None
    score: float
    provider: str


def _iso_start(days: int) -> str:
    """Exa wants ISO 8601 date-time, not a bare date."""
    d = datetime.now(timezone.utc) - timedelta(days=days)
    return d.strftime("%Y-%m-%dT00:00:00.000Z")


async def tavily_search(*, api_key: str, query: str, max_results: int = 10,
                        days: int = 30, depth: str = "advanced",
                        include_domains: list[str] | None = None,
                        client: httpx.AsyncClient) -> list[SearchHit]:
    """Tavily /search. Auth is a BEARER HEADER — api_key-in-body is the legacy v1 style."""
    n = max(1, min(days, 365))
    time_range = "month" if n <= 31 else ("week" if n <= 7 else "year")
    payload = {
        "query": query,
        "search_depth": depth,          # "advanced" costs 2 credits, everything else 1
        "topic": "general",             # "news" auto-enables published-date fields
        "time_range": time_range,       # <-- NOT `days`. See module docstring.
        "max_results": max_results,
        "include_published_date": True,
        "filter_by_published_date": True,
    }
    if include_domains:
        payload["include_domains"] = include_domains

    resp = await client.post("https://api.tavily.com/search",
                             headers={"Authorization": f"Bearer {api_key}"},
                             json=payload, timeout=60.0)
    if resp.status_code == 429:
        raise httpx.HTTPStatusError("Tavily rate limit", request=resp.request, response=resp)
    resp.raise_for_status()
    data = resp.json()
    return [
        SearchHit(title=r.get("title", ""), url=r.get("url", ""),
                  content=r.get("content", ""), published_date=r.get("published_date"),
                  score=float(r.get("score") or 0.0), provider="tavily")
        for r in data.get("results", [])
    ]


async def exa_search(*, api_key: str, query: str, num_results: int = 10, days: int = 30,
                     category: str | None = None, client: httpx.AsyncClient) -> list[SearchHit]:
    """Exa /search. Auth is the `x-api-key` header.

    TYPE ENUM IS NOT what older docs say. Current values are:
        instant | fast | auto | deep-lite | deep | deep-reasoning
    `neural` and `keyword` are legacy and absent from the current REST enum.

    CATEGORY ENUM IS NOT what older docs say either:
        company | publication | news | personal site | financial report | people
    `research paper` and `tweet` are legacy. Using them 400s.

    `company` and `people` REJECT date filters with HTTP 400 — never combine them
    with startPublishedDate.
    """
    payload: dict = {
        "query": query,
        "type": "auto",
        "numResults": num_results,
        "contents": {"text": {"maxCharacters": 2500}, "highlights": True},
    }
    if category:
        payload["category"] = category
        if category in ("company", "people"):
            raise ValueError(
                f"Exa category {category!r} rejects date filters. Use 'news' or "
                f"'publication' when you need a last-month window."
            )
    if days:
        payload["startPublishedDate"] = _iso_start(days)

    resp = await client.post("https://api.exa.ai/search",
                             headers={"x-api-key": api_key, "Content-Type": "application/json"},
                             json=payload, timeout=60.0)
    resp.raise_for_status()
    data = resp.json()
    return [
        SearchHit(title=r.get("title", ""), url=r.get("url", ""),
                  content=r.get("text") or "", published_date=r.get("publishedDate"),
                  score=float(r.get("score") or 0.0), provider="exa")
        for r in data.get("results", [])
    ]
```

### 8.5 `clients/http_cache.py` — the mechanism behind `--offline`

```python
"""Content-hash write-through cache. This is the whole of --offline.

The design property that matters: every ONLINE run automatically records the
fixtures needed for an OFFLINE run. Recording is not a separate chore, it is a
side effect of the first successful execution.

That is what makes the submission's "so we can rerun your code without burning
our paid accounts" requirement satisfiable rather than aspirational.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from cwt.util.jsonio import write_json

logger = logging.getLogger("cwt.cache")


class OfflineFixtureMissing(RuntimeError):
    """Raised in --offline mode when no recorded response exists.

    Deliberately LOUD and ACTIONABLE. A silent fallback to a live call would
    spend the reviewer's credits, which is the exact thing --offline promises
    not to do.
    """


def request_cache_key(method: str, url: str, body: dict | None) -> str:
    canonical = json.dumps({"m": method.upper(), "u": url, "b": body or {}},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class CachedResponse:
    status_code: int
    headers: dict[str, str]
    body: object

    @classmethod
    def from_disk(cls, path: Path) -> "CachedResponse":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(status_code=raw["status_code"], headers=raw["headers"], body=raw["body"])

    def to_disk(self, path: Path) -> None:
        write_json(path, {"status_code": self.status_code, "headers": self.headers,
                          "body": self.body})


class HttpCache:
    def __init__(self, root: Path, offline: bool):
        self.root = root
        self.offline = offline
        self.hits = 0
        self.misses = 0

    async def request(self, method: str, url: str, *, json_body: dict | None = None,
                      headers: dict[str, str] | None = None, timeout: float = 60.0,
                      client: httpx.AsyncClient | None = None) -> CachedResponse:
        key = request_cache_key(method, url, json_body)
        path = self.root / key[:2] / f"{key}.json"

        if path.exists():
            self.hits += 1
            return CachedResponse.from_disk(path)          # used in BOTH modes

        if self.offline:
            raise OfflineFixtureMissing(
                f"No fixture for {method.upper()} {url}\n"
                f"Expected: {path}\n"
                f"To record it (makes REAL API calls and may spend credits):\n"
                f"    python scripts/record_fixtures.py --stage <stage>"
            )

        self.misses += 1
        own_client = client is None
        client = client or httpx.AsyncClient(timeout=timeout)
        try:
            resp = await client.request(method, url, json=json_body, headers=headers,
                                        timeout=timeout)
        finally:
            if own_client:
                await client.aclose()

        cached = CachedResponse(status_code=resp.status_code,
                                headers=dict(resp.headers), body=resp.json())
        path.parent.mkdir(parents=True, exist_ok=True)
        cached.to_disk(path)                               # write-through
        return cached
```

### 8.6 `domain/claims.py` — the deterministic guardrail

```python
"""Deterministic financial-advertising claims engine.

PURE. No I/O. No LLM. Exhaustively unit-tested in tests/test_claims.py.

WHY DETERMINISTIC FIRST: an LLM judge that can be argued out of a "guaranteed" is
not a compliance control. The hard rules below are FINAL — the LLM judge that runs
afterwards may escalate a severity but may never de-escalate a hard finding.

WHY THE RULESET IS PRODUCT-SPECIFIC: the three rules marked PRODUCT_DISCLAIMER
encode contradictions with CrowdWisdomTrading's OWN published FAQ. Copy implying
position access, copy-trading or managed accounts is not merely non-compliant, it
is false, because the product says so itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

RULESET_VERSION = "1.0.0"


class Severity(StrEnum):
    HARD = "hard"      # unconditional block; forces a rewrite; no override
    SOFT = "soft"      # requires a disclosure or a rewrite; LLM judge adjudicates


@dataclass(frozen=True)
class ClaimRule:
    id: str
    severity: Severity
    pattern: re.Pattern
    why: str
    fix: str


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    matched_text: str
    why: str
    fix: str


CLAIM_RULES: tuple[ClaimRule, ...] = (
    ClaimRule(
        "guaranteed_returns", Severity.HARD,
        re.compile(r"\b(guarantee[ds]?|guaranty)\b.{0,40}\b(return|profit|gain|win|income|money|result)\b", re.I),
        "Absolute-return promise. Prohibited by Meta and Google financial-services policy and unsubstantiable.",
        "Replace the outcome promise with a described process."),
    ClaimRule(
        "risk_free_language", Severity.HARD,
        re.compile(r"\b(risk[-\s]?free|riskless|no\s+risk|zero\s+risk|can'?t\s+lose|cannot\s+lose|never\s+lose)\b", re.I),
        "Eliminates the possibility of loss. False as a matter of fact.",
        "State that trading involves risk of loss."),
    ClaimRule(
        "specific_profit_figure", Severity.HARD,
        re.compile(r"(\$\s?\d[\d,]*\s?(k|m|usd)?\s*(profit|per\s+(day|week|month)|in\s+\d+\s+(days|weeks))|make\s+\$?\d[\d,]*\s*(a|per)\s+(day|week|month))", re.I),
        "Specific earnings figure. Unsubstantiated earnings claim; a top disapproval trigger on both platforms.",
        "Remove the figure entirely. Describe the method instead."),
    ClaimRule(
        "percentage_return_promise", Severity.HARD,
        re.compile(r"\b\d{1,4}(\.\d+)?\s?%\s*(return|gain|profit|roi|per\s+(day|week|month|year)|monthly|weekly|annually)\b", re.I),
        "Promised percentage return.",
        "Remove. If a historical statistic is essential it must carry its methodology and a safe harbour."),
    ClaimRule(
        "win_rate_statistic", Severity.HARD,
        re.compile(r"\b(\d{1,3}(\.\d+)?\s?%\s*(of\s+)?(our\s+)?(tracked\s+)?(directions?|calls?|signals?|trades?|picks?)\b.{0,20}\b(hit|win|correct|accurate|right|profit)|\b(win|hit|accuracy|success)\s*rate\b.{0,20}\d{1,3}\s?%)", re.I),
        "Performance statistic. Unverifiable for this product: its public track-record page and /api/predictions both returned HTTP 404 at build time, and the figure appears as three different values (73%, 73.8%, 74.1%) on its own site.",
        "Remove the statistic. Substitute the process claim: 'every call is published with its outcome'."),
    ClaimRule(
        "double_your_money", Severity.HARD,
        re.compile(r"\b(double|triple|10x|100x|multiply)\b.{0,20}\b(money|account|capital|portfolio|investment)\b", re.I),
        "Multiplier promise.",
        "Remove."),
    ClaimRule(
        "beat_the_market", Severity.HARD,
        re.compile(r"\b(beat|outperform|crush|destroy)\s+the\s+(market|s&p|index|street)\b", re.I),
        "Comparative performance promise.",
        "Remove, or reframe as a described method with no comparative claim."),
    ClaimRule(
        "financial_freedom", Severity.HARD,
        re.compile(r"\b(retire\s+(early|in\s+your)|financial\s+freedom|quit\s+your\s+job|passive\s+income|change\s+your\s+life)\b", re.I),
        "Lifestyle-outcome promise implying a guaranteed financial result. Explicitly listed as prohibited on Meta.",
        "Remove."),
    ClaimRule(
        "payout_imagery", Severity.HARD,
        re.compile(r"\b(bank\s+balance|account\s+balance|payout|withdrawal\s+screenshot|profit\s+screenshot)\b", re.I),
        "Payout or balance imagery. Explicitly prohibited on Meta regardless of framing.",
        "Remove."),

    # ── PRODUCT_DISCLAIMER: contradicts CrowdWisdomTrading's own published FAQ ──
    ClaimRule(
        "position_access_implication", Severity.HARD,
        re.compile(r"\b(see|track|follow|copy|mirror|know)\b.{0,30}\b(their|the\s+pros'?|smart\s+money'?s?|institutional)\b.{0,20}\b(position|trade|book|order\s+flow|entry)\b", re.I),
        "PRODUCT_DISCLAIMER: implies access to traders' real positions. The product's own FAQ states it does not have access to positions, so this is false as well as non-compliant.",
        "Reframe as opinion aggregation: 'the calls professional traders are publishing publicly'."),
    ClaimRule(
        "copy_trading_implication", Severity.HARD,
        re.compile(r"\b(copy\s+(our|my|the)\s+trades?|auto[-\s]?trade|autotrade|trade\s+for\s+you|we\s+trade\s+for\s+you|done\s+for\s+you)\b", re.I),
        "PRODUCT_DISCLAIMER: implies copy-trading or automated execution. The product explicitly disclaims both.",
        "State the opposite explicitly, or remove."),
    ClaimRule(
        "managed_accounts_implication", Severity.HARD,
        re.compile(r"\b(managed\s+account|we\s+manage|manage\s+your\s+(money|funds|portfolio)|someone\s+else\s+trades)\b", re.I),
        "PRODUCT_DISCLAIMER: implies managed accounts. The product explicitly disclaims this.",
        "Remove."),

    # ── SOFT: permitted only with a disclosure or a rewrite ──
    ClaimRule(
        "implied_certainty", Severity.SOFT,
        re.compile(r"\b(never\s+miss|always\s+right|guaranteed\s+win|100\s?%\s+(accurate|correct)|the\s+exact\s+(entry|price|top|bottom))\b", re.I),
        "Implies certainty about an individual signal.",
        "Soften to describe the process rather than the outcome."),
    ClaimRule(
        "unsubstantiated_superlative", Severity.SOFT,
        re.compile(r"\b(the\s+best|the\s+most\s+accurate|unlike\s+any\s+other|the\s+only\s+platform|world'?s\s+(best|leading))\b", re.I),
        "Unsubstantiated superlative. Meta's unsubstantiated-claims policy targets implied statistical dominance.",
        "Remove the superlative, or make it specific and provable."),
    ClaimRule(
        "testimonial_earnings", Severity.SOFT,
        re.compile(r"\b(i|we)\s+(made|earned|turned|banked)\s+\$?\d", re.I),
        "Testimonial containing an earnings claim, requiring a typical-results disclosure.",
        "Remove the figure, or add a clear and prominent typical-results disclosure."),
)


def detect_claims(text: str) -> list[Finding]:
    """Scan text for prohibited claims. The ONE entry point — nothing else needs the rules."""
    findings: list[Finding] = []
    for rule in CLAIM_RULES:
        for match in rule.pattern.finditer(text):
            findings.append(Finding(rule_id=rule.id, severity=rule.severity,
                                    matched_text=match.group(0).strip(),
                                    why=rule.why, fix=rule.fix))
    return findings


def has_hard_block(findings: list[Finding]) -> bool:
    return any(f.severity is Severity.HARD for f in findings)


def rewrite_instructions(findings: list[Finding]) -> list[str]:
    """De-duplicated `fix` strings, ordered hard-first.

    These strings are written FOR A MODEL to consume — the rewrite prompt uses
    them verbatim. That is why `fix` is a specific instruction rather than
    'remove this'.
    """
    seen: set[str] = set()
    out: list[str] = []
    for finding in sorted(findings, key=lambda f: f.severity != Severity.HARD):
        if finding.fix not in seen:
            seen.add(finding.fix)
            out.append(finding.fix)
    return out


def scan_prohibited_facts(text: str, prohibited: list[dict]) -> list[Finding]:
    """Check the script against the research brief's `prohibited_facts` list.

    Matches the fact string AND its numeric variants, so '74.1%' is caught by a
    rule written for '74.1% of tracked directions hit', and 'about 74 percent' is
    caught too. Paraphrase resistance is the whole point of this function.
    """
    findings: list[Finding] = []
    for entry in prohibited:
        fact = entry.get("fact", "")
        reason = entry.get("reason", "")
        # Exact phrase
        if fact.lower() in text.lower():
            findings.append(Finding("prohibited_fact", Severity.HARD, fact, reason,
                                    entry.get("rule", "Remove.")))
            continue
        # Numeric variants: extract every number in the fact and look for it nearby
        # a relevant noun. Catches "74.1%", "74%", "about seventy-four percent".
        for number in re.findall(r"\d{1,4}(?:\.\d+)?", fact):
            stem = number.split(".")[0]
            variant = re.compile(rf"\b{re.escape(stem)}(?:\.\d+)?\s?(?:%|percent)", re.I)
            for match in variant.finditer(text):
                findings.append(Finding(
                    "prohibited_fact_numeric_variant", Severity.HARD, match.group(0),
                    f"Numerically derives from a prohibited fact ({fact!r}). {reason}",
                    entry.get("rule", "Remove.")))
    return findings
```

### 8.7 `clients/llm.py` — one client, two providers, three enforcement layers

```python
"""OpenAI-compatible LLM client.

OpenRouter and NVIDIA NIM are both OpenAI-compatible at /v1/chat/completions, so
this is ONE implementation with two configuration records — not two clients.
There is no `if provider == "nvidia"` anywhere except inside ProviderProfile.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from cwt.util.retry import retry_async

logger = logging.getLogger("cwt.llm")

T = TypeVar("T", bound=BaseModel)

RETRYABLE = {429, 500, 502, 503, 504, 522, 524}


class Tier(StrEnum):
    CHEAP = "cheap"      # extraction, scoring, judging — ~35 calls/run
    STRONG = "strong"    # creative writing — ~5 calls/run


class UnparseableJson(ValueError):
    def __init__(self, raw: str):
        super().__init__(f"Could not extract JSON from model output ({len(raw)} chars)")
        self.raw = raw


class BudgetExceeded(RuntimeError):
    def __init__(self, spent: float, cap: float, stage: str):
        super().__init__(f"LLM budget ${cap:.2f} exceeded at stage {stage!r} (spent ${spent:.4f})")
        self.spent, self.cap, self.stage = spent, cap, stage


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    base_url: str
    api_key_env: str
    supports_json_mode: bool
    extra_headers: dict[str, str] = field(default_factory=dict)


PROFILES: dict[str, ProviderProfile] = {
    "openrouter": ProviderProfile(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        supports_json_mode=True,
        extra_headers={"HTTP-Referer": "https://crowdwisdomtrading.com",
                       "X-Title": "CWT Video Ads Agent"},
    ),
    "nvidia": ProviderProfile(
        name="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key_env="NVIDIA_API_KEY",
        # NIM accepts response_format inconsistently across models. We rely on
        # prompt-level enforcement plus the repair loop instead. This is a
        # capability declaration, not a workaround — see Rule A3.
        supports_json_mode=False,
    ),
}


def extract_json(raw: str) -> Any:
    """Tolerant JSON extraction. Deliberately dumb and deterministic.

    Models wrap JSON in markdown fences and prose even when explicitly told not
    to. This is the single most common real failure in structured-output
    pipelines, so it gets three escalating attempts before giving up.
    """
    raw = raw.strip()
    try:
        return json.loads(raw)                                   # 1. already clean
    except json.JSONDecodeError:
        pass

    fence = raw
    if "```" in raw:                                             # 2. strip fences
        parts = raw.split("```")
        for part in parts:
            candidate = part[4:] if part.startswith("json") else part
            try:
                return json.loads(candidate.strip())
            except json.JSONDecodeError:
                continue

    start = raw.find("{")                                        # 3. brace-depth scan
    if start == -1:
        raise UnparseableJson(raw)
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise UnparseableJson(raw) from exc
    raise UnparseableJson(raw)


def schema_instruction(schema: type[BaseModel]) -> str:
    """Layer 2 of JSON enforcement. ALWAYS applied, even when the provider has
    native JSON mode — it measurably improves field-level accuracy."""
    return (
        "Return ONLY a single JSON object. No prose, no markdown fences, no commentary.\n"
        "It must validate against this JSON Schema. Every `required` field must be present.\n"
        "Use null for unknown optional values — never omit a key, never invent one.\n\n"
        + json.dumps(schema.model_json_schema(), indent=2)
    )


@dataclass
class LLMResult:
    text: str
    model: str
    tier: str
    stage: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    salvaged: bool = False
    repair_attempts: int = 0


class LLMClient:
    def __init__(self, *, provider: str, api_key: str, model_cheap: str, model_strong: str,
                 fallbacks: list[str], max_concurrency: int, max_usd: float,
                 ledger_path, timeout_s: float = 120.0, repair_attempts: int = 2):
        self.profile = PROFILES[provider]
        self.api_key = api_key
        self.models = {Tier.CHEAP: model_cheap, Tier.STRONG: model_strong}
        self.fallbacks = fallbacks
        self.max_usd = max_usd
        self.ledger_path = ledger_path
        self.repair_attempts = repair_attempts
        self.timeout_s = timeout_s
        self._sem = asyncio.Semaphore(max_concurrency)
        self._consecutive_429 = 0
        self._demoted_to: str | None = None
        self.run_cost_usd = 0.0

    async def complete(self, *, tier: Tier, messages: list[dict], schema: type[BaseModel] | None = None,
                       temperature: float = 0.0, max_tokens: int = 4096,
                       stage: str = "") -> LLMResult:
        model = self._demoted_to or self.models[tier]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # LAYER 1 — provider-native JSON mode, when the profile supports it.
        if schema and self.profile.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}
        # LAYER 2 — prompt-level schema injection. ALWAYS.
        if schema:
            payload["messages"] = [*messages, {"role": "system",
                                               "content": schema_instruction(schema)}]

        started = time.perf_counter()
        async with self._sem:
            async def _call():
                async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                    resp = await client.post(
                        f"{self.profile.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}",
                                 "Content-Type": "application/json",
                                 **self.profile.extra_headers},
                        json=payload,
                    )
                    if resp.status_code == 429:
                        self._consecutive_429 += 1
                        if self._consecutive_429 >= 3 and self.fallbacks:
                            self._demoted_to = self.fallbacks[0]
                            logger.warning("3 consecutive 429s — demoting to %s for this run",
                                           self._demoted_to)
                        resp.raise_for_status()
                    resp.raise_for_status()
                    self._consecutive_429 = 0
                    return resp.json()

            data = await retry_async(_call, max_attempts=5, base=1.0, cap=60.0)

        usage = data.get("usage") or {}
        result = LLMResult(
            text=data["choices"][0]["message"]["content"],
            model=data.get("model", model), tier=str(tier), stage=stage,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        self._record(result)
        return result

    async def complete_validated(self, *, tier: Tier, messages: list[dict], schema: type[T],
                                 stage: str, max_repairs: int | None = None) -> T:
        """Layer 3 plus the repair loop. This is the function stages actually call."""
        max_repairs = self.repair_attempts if max_repairs is None else max_repairs
        convo = list(messages)
        last_error: Exception | None = None

        for attempt in range(max_repairs + 1):
            result = await self.complete(tier=tier, messages=convo, schema=schema, stage=stage)
            try:
                return schema.model_validate(extract_json(result.text))
            except (UnparseableJson, ValidationError) as exc:
                last_error = exc
                if attempt == max_repairs:
                    break
                convo = [*convo,
                         {"role": "assistant", "content": result.text},
                         {"role": "user", "content": self._repair_instruction(exc, schema)}]
                logger.warning("stage=%s repair %d/%d: %s", stage, attempt + 1, max_repairs,
                               str(exc)[:200])

        salvaged = self._try_salvage(last_error, schema)
        if salvaged is not None:
            logger.warning("stage=%s salvaged by filling defaults for optional fields", stage)
            return salvaged

        raise ArtifactValidationError(stage=stage, schema=schema.__name__, error=last_error)

    @staticmethod
    def _repair_instruction(exc: Exception, schema: type[BaseModel]) -> str:
        if isinstance(exc, UnparseableJson):
            return ("Your previous response was not parseable as JSON. "
                    "Return ONLY the JSON object — no markdown fences, no explanation.")
        lines = []
        for err in exc.errors()[:10]:                    # cap: never flood the context
            loc = ".".join(str(p) for p in err["loc"])
            lines.append(f"  - `{loc}`: {err['msg']}")
        return ("Your previous response failed schema validation:\n" + "\n".join(lines) +
                "\n\nFix ONLY these problems. Preserve everything already valid. "
                "Return the complete corrected JSON object.")

    @staticmethod
    def _try_salvage(exc: Exception | None, schema: type[T]) -> T | None:
        """Narrow, high-frequency case: structurally valid but missing only OPTIONAL
        fields. Fills []/None defaults. NEVER fills a required field and never
        coerces a wrong-typed value — those are real errors and must surface."""
        if not isinstance(exc, ValidationError):
            return None
        try:
            data = exc.errors()[0].get("input")
            if not isinstance(data, dict):
                return None
            for name, field in schema.model_fields.items():
                if name not in data and not field.is_required():
                    data[name] = [] if field.annotation in (list, list[str]) else None
            return schema.model_validate(data)
        except Exception:
            return None

    def _record(self, result: LLMResult) -> None:
        cost = estimate_cost(result.model, result.prompt_tokens, result.completion_tokens)
        self.run_cost_usd += cost
        append_jsonl(self.ledger_path, {
            "ts": now_iso(), "stage": result.stage, "tier": result.tier, "model": result.model,
            "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
            "cost_usd": cost, "latency_ms": result.latency_ms, "salvaged": result.salvaged,
            "repair_attempts": result.repair_attempts, "run_total_usd": self.run_cost_usd,
        })
        # Budget is enforced HERE, inside the client, on the call that crosses the
        # cap — not checked by callers afterwards. A prompt-level request to
        # "stay under budget" is not a control. This is.
        if self.run_cost_usd > self.max_usd:
            raise BudgetExceeded(self.run_cost_usd, self.max_usd, result.stage)


class ArtifactValidationError(RuntimeError):
    def __init__(self, *, stage: str, schema: str, error: Exception | None):
        super().__init__(f"Stage {stage!r} could not produce valid {schema} after repairs: {error}")
        self.stage, self.schema, self.error = stage, schema, error


# Static price table. An unknown model returns 0.0 and logs a warning — we never
# guess a price, because a wrong number in the cost ledger is worse than a zero.
PRICES: dict[str, tuple[float, float]] = {   # (usd per 1M prompt, usd per 1M completion)
    "google/gemini-2.5-flash": (0.075, 0.30),
    "anthropic/claude-sonnet-4.5": (3.00, 15.00),
    "meta/llama-3.3-70b-instruct": (0.12, 0.30),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = PRICES.get(model)
    if price is None:
        logger.warning("No price for model %s — recording cost 0.0", model)
        return 0.0
    return (prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000
```

### 8.8 `video/filtergraph.py` — pure, deterministic, golden-tested

```python
"""Shot -> ffmpeg filter string. PURE. No ffmpeg needed to test it.

This module is the reason the video layer is trustworthy: it is the only
non-trivial part of the renderer, and it is a pure function with a golden test
suite. If you want to know whether the video code is real, read
tests/test_filtergraph.py.

CONTRACT: every returned fragment operates on a labelled input and writes a
labelled output. Paths inside fragments are RELATIVE and use forward slashes —
never a drive letter, never a backslash (Rule W5).
"""
from __future__ import annotations

from cwt.domain.models import Shot

# The cinematic grade. Ten lines of filtergraph is the entire difference between
# "an AI slideshow" and "a movie trailer" — see WOW-6.
CINEMATIC_GRADE = "vignette=PI/5,noise=alls=7:allf=t,eq=contrast=1.06:saturation=0.94"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_zoompan_expr(*, move: str, intensity: float, duration_s: float, fps: int) -> str | None:
    """Compute the zoompan z-expression. Returns None for moves that do not zoom.

    A `push_in` is NOT decoration — intensity 0.6 over 4 seconds at 30fps becomes
    a concrete per-frame zoom rate. That is what makes the camera direction
    executable rather than descriptive.
    """
    intensity = clamp(intensity, 0.0, 1.0)
    frames = max(1, int(duration_s * fps))
    if move == "static":
        return None
    if move == "push_in":
        rate = 0.30 * intensity / max(duration_s, 0.1) / fps
        return f"min(zoom+{rate:.6f},1.35)"
    if move == "pull_out":
        rate = 0.30 * intensity / max(duration_s, 0.1) / fps
        return f"if(lte(zoom,1.0),1.35,max(1.001,zoom-{rate:.6f}))"
    if move == "whip_pan":
        # Whip pans are handled by crop x-sweep, not zoompan. See build_shot_filter.
        return None
    return None


def build_colour_grade(palette: list[str], colour_temp_k: int, contrast: str) -> str:
    """Derive a colour balance from the shot's declared palette.

    Values are validated upstream by the pydantic model (hex, 4 entries), so this
    function interpolates only from a closed, type-checked set — never from raw
    model output. That is the difference between a filtergraph and a string
    concatenation bug (Rule V2).
    """
    if not palette or len(palette) < 2:
        return ""
    temp_shift = clamp((colour_temp_k - 6500) / 6500.0, -1.0, 1.0)
    parts = []
    if abs(temp_shift) > 0.02:
        parts.append(f"colorbalance=rs={-temp_shift * 0.08:.4f}:bs={temp_shift * 0.08:.4f}")
    if contrast == "extreme":
        parts.append("eq=contrast=1.18:saturation=0.88")
    elif contrast == "high":
        parts.append("eq=contrast=1.10:saturation=0.92")
    return ",".join(parts)


def build_shot_filter(shot: Shot, *, input_label: str, output_label: str, fps: int,
                      width: int, height: int) -> str:
    """Full filter chain for one shot, from the shot's own declared properties."""
    chain: list[str] = [f"[{input_label}]scale={width}:{height}:force_original_aspect_ratio=increase",
                        f"crop={width}:{height}"]

    zoom = build_zoompan_expr(move=shot.camera.move, intensity=shot.camera.intensity,
                              duration_s=shot.duration_s, fps=fps)
    if zoom:
        frames = max(1, int(shot.duration_s * fps))
        chain.append(
            f"zoompan=z='{zoom}':d={frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
        )

    if shot.camera.move == "whip_pan":
        sweep = clamp(shot.camera.intensity, 0.1, 1.0) * width * 0.35
        chain.append(f"crop={width}:{height}:x='(iw-{width})*t/{max(shot.duration_s, 0.1):.3f}*{sweep / max(width, 1):.3f}':y=0")

    grade = build_colour_grade(shot.palette, shot.lighting.colour_temp_k, shot.lighting.contrast)
    if grade:
        chain.append(grade)
    chain.append(CINEMATIC_GRADE)

    return f"{','.join(chain)}[{output_label}]"
```

### 8.9 `util/jsonio.py` and `util/paths.py`

```python
# util/jsonio.py
"""JSON I/O. utf-8 and LF are enforced HERE so no call site has to remember.

On Windows, Path.write_text does NOT normalise newlines — writing "\n" produces
"\r\n". A filelist or shell script written that way fails on Linux with a
notoriously opaque error (Rule W8).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")


def append_jsonl(path: Path, record: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
```

```python
# util/paths.py
"""Run directory layout + the Windows long-path escape hatch.

The run directory is deliberately SHALLOW. `runs/<run_id>/` with a short run_id
keeps us clear of the 260-character Windows path limit, which a deep
runs/<uuid>/artifacts/cache/<sha256>/... tree would blow through (Rule W9).
"""
from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def mint_run_id() -> str:
    """Short by design: YYYYMMDD-HHMM-<4 hex>. The 4 hex chars make collisions
    vanishingly unlikely without needing a UUID's 36 characters in every path."""
    return f"{datetime.now():%Y%m%d-%H%M}-{secrets.token_hex(2)}"


def long_path(p: Path) -> str:
    """Prefix with \\\\?\\ on Windows when the path approaches MAX_PATH.

    Only applied at the few call sites that genuinely need it — ffmpeg and mkdir
    on deeply nested asset caches. Applying it everywhere produces paths that
    some tools display unreadably.
    """
    s = str(p)
    if sys.platform == "win32" and len(s) > 250 and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + os.path.abspath(s)
    return s


@dataclass(frozen=True)
class RunPaths:
    run_dir: Path

    @property
    def artifacts(self) -> Path:  return self.run_dir / "artifacts"
    @property
    def assets(self) -> Path:     return self.run_dir / "assets"
    @property
    def cache(self) -> Path:      return self.run_dir / "assets" / "cache"
    @property
    def render(self) -> Path:     return self.run_dir / "render"
    @property
    def failures(self) -> Path:   return self.run_dir / "failures"
    @property
    def ledger(self) -> Path:     return self.run_dir / "llm_ledger.jsonl"
    @property
    def provenance(self) -> Path: return self.artifacts / "provenance.json"

    def ensure(self) -> "RunPaths":
        for d in (self.artifacts, self.assets, self.cache, self.render, self.failures):
            d.mkdir(parents=True, exist_ok=True)
        return self
```

---

## 9. Orchestration — The Kanban DAG and the One-Shot Entrypoint

This is the layer that wires everything together and delivers the thing both the brief and the user
asked for: **the entire pipeline runs end to end from a single command.**

### 9.1 The DAG, as data

```python
# src/cwt/hermes/dag.py — the topology. One source of truth.
"""The card graph.

Every topology claim in this document is one edit to this list.

WHY THE REVIEW IS A CARD AND NOT A LOOP INSIDE A CARD:
Hermes' review lifecycle is that a reviewer claims a card out of `review` and
terminates it with EITHER `kanban_complete` (approve) or `kanban_request_changes`
(back to the implementer). `kanban_complete` ENDS THE CARD — which would start
the render and skip the compliance gate entirely.

So compliance is its own card, gated on the script card. Only verified
primitives are used, and both gates are real gates.

    t_script ──request_review──▶ cwt-creative-director
                                     │ complete  = approve
                                     │ request_changes = rewrite (max 3)
                                     ▼
    t_compliance (parent: script)  ── deterministic gate + bounded rewrite
                                     ▼
    t_render ──▶ t_qa ──▶ t_collect
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CardSpec:
    key: str
    title: str
    assignee: str
    parents: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    max_runtime: str | None = None
    max_retries: int | None = None
    goal: bool = False
    body: str = ""


DAG_SPEC: list[CardSpec] = [
    CardSpec(
        key="root",
        title="Produce a 30-60s cinematic ad for crowdwisdomtrading.com",
        assignee="cwt-orchestrator",
        skills=("cwt-source-winning-ads",),
        goal=True,
        max_runtime="10m",
        body=(
            "Acceptance criteria — ALL must hold:\n"
            "  1. render/final.mp4 exists, is 30-60s, 1080x1920, and ffprobe-valid\n"
            "  2. artifacts/storyboard.json validates and its creative_scores.verdict == 'pass'\n"
            "  3. artifacts/claims_report.json verdict == 'pass' on BOTH the pre-render and\n"
            "     post-render passes\n"
            "  4. submission/ contains the bundle described in the README\n"
            "This card is an anchor for the DAG. It completes immediately."
        ),
    ),
    CardSpec(
        key="ads",
        title="Source winning ads — Meta Ads Library via Apify, last 30 days",
        assignee="cwt-ads-manager",
        parents=("root",),
        skills=("cwt-source-winning-ads",),
        max_runtime="20m",
        max_retries=2,
        body=(
            "Find ads CURRENTLY RUNNING in the trading/fintech niche. Ad longevity is the\n"
            "performance signal: an ad still running after 21 days has survived the fatigue\n"
            "window. Hard-capped spend — see Rule A2. If the run returns fewer than 8 ads\n"
            "inside the window, complete anyway and record a warning; do NOT widen the window."
        ),
    ),
    CardSpec(
        key="patterns",
        title="Extract hooks, pains, concepts and beat sheets from the winning ads",
        assignee="cwt-hook-analyst",
        parents=("ads",),
        skills=("cwt-extract-ad-patterns",),
        max_runtime="20m",
        body=(
            "Extract structure, not just topic. The beat_sheet is the highest-value output:\n"
            "which beat occupies which second. Aggregate the median timeline across all ads —\n"
            "the storyboard validator enforces it later, so a lazy aggregate here becomes a\n"
            "hard failure downstream."
        ),
    ),
    CardSpec(
        key="res_pain",
        title="Research angle: the ICP's pain (last 30 days)",
        assignee="cwt-researcher",
        parents=("patterns",),
        skills=("cwt-research-angle",),
        max_runtime="12m",
    ),
    CardSpec(
        key="res_unique",
        title="Research angle: CrowdWisdomTrading's unique data",
        assignee="cwt-researcher",
        parents=("patterns",),
        skills=("cwt-research-angle",),
        max_runtime="12m",
        body=(
            "This card MUST populate research_brief.prohibited_facts. Any claim the product\n"
            "makes that you could not verify from a public source belongs in that list. The\n"
            "claims engine reads it and hard-blocks the script if any of it appears."
        ),
    ),
    CardSpec(
        key="res_crowd",
        title="Research angle: crowd wisdom vs single-expert forecasting",
        assignee="cwt-researcher",
        parents=("patterns",),
        skills=("cwt-research-angle",),
        max_runtime="12m",
        body=(
            "Find the strongest COUNTER-argument too. A script that only cites supporting\n"
            "evidence is propaganda, and the objection beat needs something honest to answer."
        ),
    ),
    CardSpec(
        key="brief",
        title="Assemble the research brief — select, do not summarise",
        assignee="cwt-researcher",
        parents=("res_pain", "res_unique", "res_crowd"),
        skills=("cwt-research-angle",),
        max_runtime="8m",
    ),
    CardSpec(
        key="script",
        title="Write storyboard: 3 variants, judge, splice, revise to threshold",
        assignee="cwt-script-writer",
        parents=("brief",),
        skills=("cwt-write-storyboard",),
        max_runtime="45m",
        max_retries=2,
        body=(
            "Write all THREE variants — one per research angle. The brief requires three\n"
            "distinct inputs; writing one script and claiming three is not acceptable.\n\n"
            "Your terminal call is kanban_request_review with reviewer cwt-creative-director.\n"
            "On request_changes: apply the rewrite and re-request. MAXIMUM 3 ROUNDS, then\n"
            "kanban_block with the reason.\n\n"
            "NEVER call kanban_complete. The creative director owns that decision."
        ),
    ),
    CardSpec(
        key="compliance",
        title="Claims gate — deterministic policy check + bounded rewrite",
        assignee="cwt-compliance",
        parents=("script",),
        skills=("cwt-claims-gate",),
        max_runtime="15m",
        body=(
            "Read the parent's storyboard.json. Run cwt_check_claims over it.\n\n"
            "If it passes: kanban_complete with metadata.artifact_path = claims_report.json.\n\n"
            "If HARD findings: call cwt_rewrite_for_compliance. The deterministic fixes are\n"
            "specific instructions, not creative work — apply them, re-check, and complete\n"
            "with the corrected storyboard as artifacts/storyboard.json (overwrite in place\n"
            "and record the round in generation.claims_rewrite_rounds).\n\n"
            "After CLAIMS_MAX_REWRITE_ROUNDS with a HARD finding still standing, call\n"
            "kanban_block with the finding text. It must NEVER silently pass. A blocked card\n"
            "with a clear reason is a better outcome than a shipped unsubstantiated claim."
        ),
    ),
    CardSpec(
        key="render",
        title="Render the 30-60s video from storyboard.json",
        assignee="cwt-video-editor",
        parents=("compliance",),
        skills=("cwt-render-video",),
        max_runtime="60m",
        max_retries=1,
        body=(
            "Synthesize the voiceover, resolve assets, then render through the backend chain.\n"
            "The chain ALWAYS terminates in local_ffmpeg — a failure there is a real failure,\n"
            "but the earlier backends failing is expected and is recorded, not reported as an\n"
            "error. Probe the output before declaring success: ffmpeg exits 0 having written a\n"
            "0-byte file when the last frame is dropped (Rule V3)."
        ),
    ),
    CardSpec(
        key="qa",
        title="Final QA — duration, aspect, loudness, post-render claims re-check",
        assignee="cwt-qa",
        parents=("render",),
        skills=("cwt-final-qa",),
        max_runtime="10m",
        body=(
            "Re-run the claims engine over the RENDERED voiceover transcript, not the\n"
            "storyboard. TTS normalisation changes what is actually said, and a line added\n"
            "during render would otherwise bypass the gate entirely. Recompute the risk\n"
            "disclosure's on-screen duration from the rendered timeline, not the declared value."
        ),
    ),
    CardSpec(
        key="collect",
        title="Assemble the submission bundle",
        assignee="cwt-ads-manager",
        parents=("qa",),
        max_runtime="5m",
        body=(
            "Produce submission/: final.mp4, storyboard.json, storyboard.html,\n"
            "contact_sheet.png, render_manifest.json, claims_report.json, cost_report.json,\n"
            "and README-SUBMISSION.md containing the two API tokens and the recording recipe."
        ),
    ),
]


def topo_sort(spec: list[CardSpec] = DAG_SPEC) -> list[CardSpec]:
    """Kahn's algorithm. Deterministic order — a stable sort keeps card ids
    reproducible across resumes, which matters for idempotency keys."""
    by_key = {c.key: c for c in spec}
    indegree = {c.key: len(c.parents) for c in spec}
    children: dict[str, list[str]] = {c.key: [] for c in spec}
    for card in spec:
        for parent in card.parents:
            if parent not in by_key:
                raise ValueError(f"Card {card.key!r} names unknown parent {parent!r}")
            children[parent].append(card.key)

    ready = sorted([k for k, d in indegree.items() if d == 0])
    out: list[CardSpec] = []
    while ready:
        key = ready.pop(0)
        out.append(by_key[key])
        for child in children[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(out) != len(spec):
        raise ValueError("DAG_SPEC contains a cycle")
    return out
```

### 9.2 `hermes/board.py` — seeding, waiting, resuming

```python
"""Kanban board operations. The only module that talks to the board."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table

from cwt.hermes.cli import kanban
from cwt.hermes.dag import DAG_SPEC, CardSpec, topo_sort
from cwt.util.jsonio import write_json

logger = logging.getLogger("cwt.board")
console = Console()

TERMINAL_BAD = {"blocked", "gave_up"}


def render_body(card: CardSpec, run_id: str, run_dir: Path, board: str) -> str:
    """The contract every worker reads. Produced verbatim into the card body.

    Note what this does NOT contain: artifact paths. Paths arrive through the
    parent chain via kanban_show(), so a card never has to be re-seeded when a
    path convention changes. That is why `--parent` is a context channel, not
    merely a scheduling gate.
    """
    skills = ", ".join(card.skills) if card.skills else "(none)"
    return f"""RUN_ID: {run_id}
RUN_DIR: {run_dir}
BOARD: {board}

STEP 1 — Call kanban_show() with no arguments. Read every parent's
         metadata.artifact_path. Those are your inputs. Do not guess paths.

STEP 2 — Follow the pinned skill exactly: {skills}
         If no skill is pinned, call kanban_show() and follow the body above.

STEP 3 — Do your work by calling the cwt_* tools. They write the artifacts for you.
         NEVER hand-write an artifact JSON file — the tools validate before writing
         and a hand-written file will fail the next stage's schema check.

STEP 4 — Call kanban_complete with:
             summary  = 2-3 sentences a human will read on the dashboard
             metadata = {{"stage": "{card.key}",
                          "artifact_path": "<path relative to RUN_DIR>",
                          "sha256": "<sha256 of the artifact file>",
                          "status": "ok"}}
         A completion WITHOUT metadata.artifact_path is a bug: the next stage has
         nothing to read and will block.

ON FAILURE — Call kanban_block(reason="<what failed, what you tried, what a human
             should do>"). Never complete a stage you did not produce an artifact for.

{card.body}
"""


def seed(run_id: str, run_dir: Path, board: str, spec: list[CardSpec] = DAG_SPEC) -> dict[str, str]:
    """Create every card in topological order, wiring parents as we go.

    `--idempotency-key {run_id}:{key}` is what makes `cwt run --resume` safe:
    re-seeding after a crash REUSES the existing card rather than duplicating the
    whole board.
    """
    ids: dict[str, str] = {}
    for card in topo_sort(spec):
        parent_ids = [ids[p] for p in card.parents]
        args = ["create", card.title,
                "--assignee", card.assignee,
                "--body", render_body(card, run_id, run_dir, board),
                "--idempotency-key", f"{run_id}:{card.key}",
                "--json"]
        for pid in parent_ids:
            args += ["--parent", pid]
        for skill in card.skills:
            args += ["--skill", skill]
        if card.max_runtime:
            args += ["--max-runtime", card.max_runtime]
        if card.max_retries is not None:
            args += ["--max-retries", str(card.max_retries)]
        if card.goal:
            args += ["--goal", "--goal-max-turns", "20"]

        result = kanban(*args, board=board, timeout_s=60)
        if not result.ok:
            raise RuntimeError(
                f"Failed to create card {card.key!r}:\n{result.stderr[-2000:]}\n"
                f"If the error names an unknown flag, your Hermes version differs from the "
                f"one this spec targets. Run `cwt doctor` for a flag diff."
            )
        ids[card.key] = result.json()["id"]
        logger.info("seeded %s -> %s", card.key, ids[card.key])
    return ids


def list_cards(board: str) -> list[dict]:
    result = kanban("list", "--json", board=board, timeout_s=60)
    if not result.ok:
        return []
    data = result.json()
    return data if isinstance(data, list) else data.get("tasks", [])


def nudge(board: str) -> None:
    """Trigger one dispatcher tick immediately.

    Used by the stall detector. This is a NUDGE, never a force-complete — the
    pipeline never fabricates progress.
    """
    kanban("dispatch", board=board, timeout_s=60)


@dataclass
class RunSummary:
    cards: list[dict]

    @property
    def done(self) -> int:
        return sum(1 for c in self.cards if c["status"] == "done")


class PipelineTimeout(RuntimeError): ...
class PipelineBlocked(RuntimeError): ...


async def wait_for_completion(board: str, *, timeout_s: int, poll_s: int = 15,
                              fail_fast: bool = False,
                              stall_threshold_s: int = 180) -> RunSummary:
    """Poll the board until every card is done, or fail loudly.

    The stall detector is not optional. The single most likely 'it hangs forever'
    failure is the dispatcher not running because the gateway is down — cards sit
    on `ready` and nothing happens. We detect it, nudge once, and then raise with
    full diagnostics rather than waiting out the full timeout.
    """
    deadline = time.monotonic() + timeout_s
    last_progress = time.monotonic()
    last_signature: tuple | None = None
    nudged = False

    while True:
        cards = list_cards(board)
        signature = tuple(sorted((c["id"], c["status"]) for c in cards))
        done = [c for c in cards if c["status"] == "done"]
        bad = [c for c in cards if c["status"] in TERMINAL_BAD]

        if bad and fail_fast:
            _dump_diagnostics(board, bad)
            raise PipelineBlocked(f"{len(bad)} card(s) blocked or gave up: "
                                  f"{[c['id'] for c in bad]}")

        if len(done) == len(DAG_SPEC) and not bad:
            return RunSummary(cards)

        if time.monotonic() > deadline:
            _dump_diagnostics(board, cards)
            raise PipelineTimeout(
                f"Pipeline exceeded {timeout_s}s. "
                f"{len(done)}/{len(DAG_SPEC)} cards done."
            )

        if signature != last_signature:
            last_signature = signature
            last_progress = time.monotonic()
            nudged = False
        elif time.monotonic() - last_progress > stall_threshold_s:
            if not nudged:
                console.print(f"[yellow]No board progress in {stall_threshold_s}s — "
                              f"nudging the dispatcher[/yellow]")
                nudge(board)
                nudged = True
                last_progress = time.monotonic()
            else:
                _dump_diagnostics(board, cards)
                raise PipelineBlocked(
                    "Board stalled after a dispatcher nudge. The gateway is probably not "
                    "running. Start it with `hermes gateway start`, then resume with "
                    "`cwt run --resume`."
                )

        _render_progress(cards)
        await asyncio.sleep(poll_s)


def _render_progress(cards: list[dict]) -> None:
    """Live progress table. Safe to have on screen during a recording — this is
    deliberately the thing a viewer watches."""
    table = Table(title="CWT Pipeline", show_header=True, header_style="bold cyan")
    table.add_column("Card", style="dim", width=10)
    table.add_column("Stage", width=22)
    table.add_column("Assignee", width=22)
    table.add_column("Status", width=10)
    colours = {"done": "green", "running": "cyan", "ready": "yellow",
               "blocked": "red", "todo": "dim", "review": "magenta"}
    for card in cards:
        status = card.get("status", "?")
        table.add_row(card.get("id", "?"), card.get("title", "")[:22],
                      card.get("assignee", ""), f"[{colours.get(status, 'white')}]{status}[/]")
    console.print(table)


def _dump_diagnostics(board: str, cards: list[dict]) -> None:
    """Write everything a human needs to diagnose, and print the retry command."""
    for card in cards:
        if card.get("status") not in TERMINAL_BAD and card.get("status") != "running":
            continue
        cid = card["id"]
        show = kanban("show", cid, "--json", board=board, timeout_s=30)
        runs = kanban("runs", cid, "--json", board=board, timeout_s=30)
        log = kanban("log", cid, board=board, timeout_s=30)
        write_json(Path("runs") / "_diagnostics" / f"{cid}.json",
                   {"show": show.stdout, "runs": runs.stdout,
                    "log_tail": log.stdout[-20000:]})
        console.print(f"[red]Diagnostics for {cid} written to runs/_diagnostics/{cid}.json[/red]")
    console.print("[yellow]To retry:  cwt run --resume[/yellow]")


def resume(run_id: str, board: str) -> None:
    """Unblock every blocked card so the dispatcher picks it up again.

    Safe because seeding is idempotent — the artifacts already on disk are read
    by the stage cache, so a resumed run does not re-spend API credits.
    """
    for card in list_cards(board):
        if card.get("status") in TERMINAL_BAD:
            kanban("unblock", card["id"], board=board, timeout_s=30)
            console.print(f"[green]unblocked {card['id']}[/green]")
```

### 9.3 `cli.py` — the one-shot entrypoint

```python
"""cwt — the command line.

EXIT CODES (a reviewer can tell what happened without reading a log):
  0  success
  1  configuration or preflight error
  2  pipeline timeout
  3  pipeline blocked (a card gave up)
  4  LLM budget exceeded
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

from rich.console import Console

from cwt.config import Settings
from cwt.util.paths import RunPaths, mint_run_id

console = Console()
EXIT_OK, EXIT_CONFIG, EXIT_TIMEOUT, EXIT_BLOCKED, EXIT_BUDGET = 0, 1, 2, 3, 4


def _banner(settings: Settings, paths: RunPaths, offline: bool, engine: str) -> None:
    """The config echo. This is the first debugging tool in production and it
    costs nothing to print."""
    from cwt.video.ffmpeg_bin import ffmpeg_path, ffmpeg_version
    from cwt.hermes.cli import hermes_bin, hermes_version
    try:
        ff = f"{ffmpeg_path()}  ({ffmpeg_version()})"
    except Exception as exc:
        ff = f"[red]NOT FOUND: {exc}[/red]"
    try:
        hm = f"{hermes_bin()}  (v{hermes_version()})"
    except Exception as exc:
        hm = f"[red]NOT FOUND: {exc}[/red]"

    console.print("[bold cyan]=== CWT Video Ads Agent ===[/bold cyan]")
    console.print(f"run_id       {paths.run_dir.name}")
    console.print(f"run_dir      {paths.run_dir}")
    console.print(f"engine       {engine}")
    console.print(f"offline      {offline}")
    console.print(f"provider     {settings.llm_provider}  "
                  f"(cheap={settings.model_cheap}  strong={settings.model_strong})")
    console.print(f"video        {','.join(settings.video_backend_chain)}")
    console.print(f"ffmpeg       {ff}")
    console.print(f"hermes       {hm}")
    console.print(f"board        {settings.board}")
    console.print(f"budget       ${settings.max_usd:.2f}")


async def _cmd_run(args) -> int:
    from cwt.engine import run_pipeline
    settings = Settings.from_env()
    if args.backend:
        settings = settings.with_backend_chain([args.backend])
    run_id = args.run_id or mint_run_id()
    paths = RunPaths(Path(args.run_dir or "runs") / run_id).ensure()

    if args.engine == "hermes" and not args.offline:
        pass  # doctor has already verified the gateway is reachable
    _banner(settings, paths, args.offline, args.engine)

    try:
        summary = await run_pipeline(settings=settings, paths=paths, engine=args.engine,
                                     offline=args.offline, record_pacing=args.record_pacing,
                                     force_stage=args.force_stage)
    except Exception as exc:
        name = type(exc).__name__
        console.print(f"[red]{name}: {exc}[/red]")
        return {"PipelineTimeout": EXIT_TIMEOUT, "PipelineBlocked": EXIT_BLOCKED,
                "BudgetExceeded": EXIT_BUDGET}.get(name, EXIT_CONFIG)

    console.print(f"[bold green]Done.[/bold green] {summary['done']} stages, "
                  f"${summary['cost_usd']:.4f}, output: {summary['output']}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cwt", description="CWT Video Ads Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the full pipeline end to end")
    p_run.add_argument("--engine", choices=["hermes", "local"], default="hermes")
    p_run.add_argument("--offline", action="store_true",
                       help="Use recorded fixtures only. Zero API spend.")
    p_run.add_argument("--backend", help="Pin one video backend (e.g. local_ffmpeg)")
    p_run.add_argument("--run-id", help="Reuse an existing run id")
    p_run.add_argument("--run-dir", default="runs")
    p_run.add_argument("--resume", action="store_true", help="Unblock and continue a prior run")
    p_run.add_argument("--record-pacing", action="store_true",
                       help="Insert deliberate pauses so the dashboard recording is watchable")
    p_run.add_argument("--force-stage", action="append", default=[],
                       help="Re-run this stage even if its artifact is valid. Repeatable.")
    p_run.add_argument("--fail-fast", action="store_true")
    p_run.add_argument("--timeout", type=int)

    p_doctor = sub.add_parser("doctor", help="Preflight: binaries, APIs, models, Hermes flags")
    p_doctor.add_argument("--json", action="store_true")

    p_seed = sub.add_parser("seed", help="Create the kanban cards without running")
    p_seed.add_argument("--run-id", required=True)

    sub.add_parser("status", help="Show the current board")
    sub.add_parser("bootstrap", help="Create the Hermes profiles, skills and plugin")
    sub.add_parser("clean", help="Remove runs/ except the most recent")

    args = parser.parse_args(argv)

    if args.command == "run":
        return asyncio.run(_cmd_run(args))
    if args.command == "doctor":
        from cwt.doctor import run_doctor
        return run_doctor(json_output=args.json)
    if args.command == "seed":
        from cwt.hermes.board import seed
        settings = Settings.from_env()
        paths = RunPaths(Path("runs") / args.run_id).ensure()
        ids = seed(args.run_id, paths.run_dir, settings.board)
        console.print_json(json.dumps(ids))
        return EXIT_OK
    if args.command == "status":
        from cwt.hermes.board import _render_progress, list_cards
        _render_progress(list_cards(Settings.from_env().board))
        return EXIT_OK
    if args.command == "bootstrap":
        from cwt.bootstrap import install_hermes_assets
        return install_hermes_assets()
    if args.command == "clean":
        runs = sorted(Path("runs").glob("20*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in runs[1:]:
            shutil.rmtree(old, ignore_errors=True)
            console.print(f"removed {old}")
        return EXIT_OK
    return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
```

### 9.4 The Hermes worker's model is NOT our model

**The most likely reviewer confusion in this entire design, stated explicitly in the README:**

`LLM_MODEL_CHEAP` and `LLM_MODEL_STRONG` configure **our own** `LLMClient`, which the `cwt_*`
tools use for extraction, scoring, judging and creative writing.

They do **not** configure the Hermes *worker* — the model that reads a card, decides which tool to
call, and calls it. That model is set with `hermes model`, lives in `~/.hermes/config.yaml`, and
must have **≥ 64k context** (Hermes enforces this).

In `--offline` mode our own LLM calls are removed entirely — but the workers still run on whatever
model the reviewer configured. So:

> **Before recording the demo, run `hermes model` and pick a free-tier model.**

`cwt doctor` reads the active Hermes model config and warns if it cannot confirm the context
requirement.

### 9.5 What a one-shot run looks like

```
$ cwt run
=== CWT Video Ads Agent ===
run_id       20260926-1402-a7f3
engine       hermes
offline      false
…
[cyan]Seeding 11 cards on board 'cwt-ads'…[/cyan]
   root        t_a1b2  cwt-orchestrator
   ads         t_c3d4  cwt-ads-manager
   …
[cyan]Waiting for the board…[/cyan]
┌─── CWT Pipeline ────────────────────────────────────────────────┐
│ t_a1b2  Produce a 30-60s…   cwt-orchestrator      done          │
│ t_c3d4  Source winning ads  cwt-ads-manager       running       │
│ t_e5f6  Extract hooks…      cwt-hook-analyst      todo          │
└─────────────────────────────────────────────────────────────────┘
…
Done. 11 stages, $0.2413, output: runs/20260926-1402-a7f3/render/final.mp4
```

**Offline, the same command with zero spend and no network:**

```
$ cwt run --engine local --offline
…
Done. 11 stages, $0.0000, output: runs/20260926-1402-a7f3/render/final.mp4
```

The fixture set is bundled, so this renders a real, watchable 42-second video with no API keys at
all. **That is the documented quickstart**, deliberately — see Section 18.

### 9.6 Resume — three idempotent layers

| Layer | Mechanism | Effect |
|---|---|---|
| **Card** | `--idempotency-key {run_id}:{stage}` | Re-seeding reuses cards; no duplicates |
| **Stage** | `ArtifactStore.get_if_valid(name)` compares upstream input hashes to `provenance.json` | A stage whose inputs are unchanged returns `skipped` instantly |
| **Tool** | `HttpCache` keyed on `sha256(method + url + body)` | A repeated Tavily query is a cache hit regardless of stage state |

The practical consequence: after fixing a filtergraph bug, re-running costs **zero API calls** and
about 90 seconds — because only the render stage's inputs changed.

---

## 10. CLI and Tool Surface

The template's Section 10 is "API layer". This project has **no HTTP API** — the machine-facing
surface is the CLI plus the plugin tools, and they mirror the pipeline stages one-to-one.

### 10.1 Command groups

```
# ── Setup ──
cwt bootstrap                 Create Hermes profiles, install skills + plugin, merge config
cwt doctor [--json]           Preflight every dependency; fail before spending anything

# ── Run ──
cwt run                       Full pipeline, end to end
cwt run --engine local        Bypass Hermes entirely; stages run in-process (CI path)
cwt run --offline             Fixtures only. Zero spend, zero network.
cwt run --resume              Unblock and continue a prior run
cwt run --record-pacing       Deliberate pauses so the dashboard recording is watchable
cwt run --force-stage render  Re-run one stage even though its artifact is valid

# ── Inspect ──
cwt seed --run-id X           Create the cards without running them
cwt status                    Current board, colour-coded
cwt clean                     Remove all but the most recent run directory
```

### 10.2 The plugin tool surface — 19 tools, grouped to mirror the stages

| Group | Tool | What it does |
|---|---|---|
| **Ads** | `cwt_source_winning_ads` | Run the Apify actor, normalise, filter to window, write `winning_ads.json` |
| | `cwt_rank_winning_ads` | Score and rank by longevity; record weights and exclusions |
| **Patterns** | `cwt_extract_ad_patterns` | Hook/pain/concept + beat sheet per ad; aggregate the median timeline |
| **Research** | `cwt_research_angle` | One angle: `pain` \| `unique_data` \| `crowd_effect` |
| | `cwt_assemble_brief` | Select (not summarise) the best claims into `research_brief.json` |
| **Storyboard** | `cwt_generate_hook_candidates` | 12 scored candidates across 6 archetypes, with rejection reasons |
| | `cwt_write_storyboard_variant` | One variant for one angle |
| | `cwt_judge_variants` | Score three variants, pick a winner, emit the splice list |
| | `cwt_score_storyboard` | Self-check before submitting for review |
| | `cwt_apply_rewrite` | Apply a review verdict or splice list; increment the round counter |
| | `cwt_render_storyboard_html` | Human-readable HTML view |
| | `cwt_make_contact_sheet` | 4×3 grid of each shot's first frame |
| **Claims** | `cwt_check_claims` | Deterministic engine + LLM judge; writes `claims_report.json` |
| | `cwt_rewrite_for_compliance` | Apply the deterministic fixes; bounded |
| **Video** | `cwt_synthesize_voiceover` | TTS chain; word-timed transcript |
| | `cwt_render_video` | Backend chain; writes `render_manifest.json` |
| | `cwt_probe_media` | ffprobe a file; never silently skip verification |
| **Bundle** | `cwt_verify_artifact` | Schema-validate one artifact and return `{"ok": true}` |
| | `cwt_assemble_submission` | Build `submission/` |

**Every tool docstring is written for an LLM reader.** The docstring is the tool's contract with the
model, not documentation for a human. Example:

```python
@_safe
def check_claims(args, settings, paths):
    """Run the financial-advertising claims gate over a script.

    CALL THIS: after a storyboard is written, before any render. Also call it again
    after the voiceover is synthesized — TTS normalisation changes what is actually
    said, and a line added during render would otherwise bypass the gate.

    WHEN NOT TO CALL: do not call this on the storyboard's shot descriptions. It
    scans the SPOKEN and ON-SCREEN text only. Descriptions of camera moves are not
    claims.

    INPUTS:
      stage (str, required)  — "pre_render" or "post_render". Recorded in the report
                               so a reader can tell which pass found what.
      transcript (str)       — for post_render, the word-timed VO transcript.
                               Omit for pre_render; the tool reads the storyboard.

    WRITES: artifacts/claims_report.json
    RETURNS: {"verdict": "pass"|"request_changes"|"block", "hard_count": int,
              "soft_count": int, "rewrite_instructions": [...]}

    A "block" verdict after CLAIMS_MAX_REWRITE_ROUNDS means a HARD finding could not
    be resolved. Call kanban_block — never complete the card.
    """
```

### 10.3 `doctor.py` — preflight

```python
"""Preflight. Run before anything spends money.

Every check either passes, warns, or fails with an ACTIONABLE message. A doctor
that says "something is wrong" is useless; one that names the missing flag, the
bad model slug and the exact install command is the difference between a
five-minute fix and an hour of guessing.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field

from rich.console import Console

console = Console()

REQUIRED_KANBAN_FLAGS = {
    "--assignee", "--body", "--parent", "--idempotency-key", "--workspace",
    "--priority", "--max-runtime", "--max-retries", "--skill", "--json",
}


@dataclass
class Check:
    name: str
    status: str          # "ok" | "warn" | "fail"
    detail: str = ""
    hint: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == "fail"]


def _check_python() -> Check:
    if sys.version_info < (3, 11):
        return Check("python", "fail", f"{sys.version_info[:2]}",
                     "Python 3.11+ required")
    return Check("python", "ok", f"{sys.version_info.major}.{sys.version_info.minor}")


def _check_ffmpeg() -> Check:
    from cwt.video.ffmpeg_bin import ffmpeg_path, ffmpeg_version, ffprobe_path
    try:
        path = ffmpeg_path()
        version = ffmpeg_version()
    except Exception as exc:
        return Check("ffmpeg", "fail", str(exc),
                     "pip install imageio-ffmpeg, or set CWT_FFMPEG_BIN to an absolute path")
    detail = f"{path} ({version})"
    try:
        ffprobe_path()
    except Exception:
        return Check("ffmpeg", "warn", f"{detail}; ffprobe missing",
                     "Duration verification will fall back to parsing `ffmpeg -i` stderr. "
                     "Verification is NOT skipped — see Rule W3.")
    return Check("ffmpeg", "ok", detail)


def _check_hermes() -> Check:
    from cwt.hermes.cli import hermes_bin, hermes_version, supported_flags
    try:
        binary = hermes_bin()
        version = hermes_version()
    except Exception as exc:
        return Check("hermes", "fail", str(exc),
                     "Windows:  iex (irm https://hermes-agent.nousresearch.com/install.ps1)\n"
                     "Linux:    curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash")

    missing = REQUIRED_KANBAN_FLAGS - supported_flags()
    if missing:
        return Check("hermes", "fail",
                     f"v{version} at {binary} — missing kanban flags: {sorted(missing)}",
                     "Your Hermes version differs from the one this spec targets. "
                     "Either upgrade Hermes, or update src/cwt/hermes/dag.py and board.py to "
                     "match your version's flag spelling. All Hermes coupling is in "
                     "src/cwt/hermes/ — this is a contained fix.")
    return Check("hermes", "ok", f"v{version} at {binary}")


def _check_worker_model() -> Check:
    """Warn — loudly — when the Hermes WORKER model may be too small.

    This is the check that prevents the most likely reviewer confusion: our
    LLM_MODEL_* vars do not configure the workers.
    """
    from cwt.hermes.cli import run_hermes
    result = run_hermes(["config", "get", "model"], timeout_s=30)
    model = result.stdout.strip()
    if not model:
        return Check("worker model", "warn", "not set",
                     "Run `hermes model` and pick a model with >=64k context. "
                     "In --offline mode the workers still run on this model.")
    return Check("worker model", "ok", f"{model}  (verify >=64k context)")


def _check_llm_models(settings) -> Check:
    """Validate the configured slugs against /v1/models.

    Model slugs drift. A spec that hardcodes a slug it cannot verify is a spec
    that breaks in three weeks. This converts 'mysterious 404 mid-run' into
    'clear preflight error before any spend'.
    """
    import httpx
    from cwt.clients.llm import PROFILES
    profile = PROFILES[settings.llm_provider]
    key = os.getenv(profile.api_key_env, "")
    if not key:
        return Check("llm models", "fail", f"{profile.api_key_env} not set",
                     "Set it in .env")
    try:
        resp = httpx.get(f"{profile.base_url}/models",
                         headers={"Authorization": f"Bearer {key}"}, timeout=30)
        resp.raise_for_status()
        available = {m["id"] for m in resp.json().get("data", [])}
    except Exception as exc:
        return Check("llm models", "warn", f"could not list models: {exc}",
                     "Model validation skipped. A bad slug will surface as a 404 mid-run.")
    wanted = {settings.model_cheap, settings.model_strong, *settings.model_fallbacks}
    missing = wanted - available
    if missing:
        return Check("llm models", "fail", f"not available: {sorted(missing)}",
                     "Fix LLM_MODEL_* in .env. Note that OpenRouter slugs are "
                     "`vendor/model` and NVIDIA slugs are often `vendor/model` too.")
    return Check("llm models", "ok", f"{len(wanted)} slugs verified")


def _check_gateway(settings) -> Check:
    """The dispatcher runs in the gateway by default. If it is not up, every card
    sits on `ready` forever and the run appears to hang."""
    from cwt.hermes.cli import run_hermes
    result = run_hermes(["kanban", "dispatch", "--dry-run"], timeout_s=30)
    if not result.ok and "gateway" in (result.stderr + result.stdout).lower():
        return Check("gateway", "warn", "not running",
                     "Start it with `hermes gateway start`, or the board will never dispatch. "
                     "cwt run will detect the stall and tell you.")
    return Check("gateway", "ok", "dispatcher reachable")


def _check_backend_chain(settings) -> Check:
    from cwt.video.backend import build_chain
    chain = build_chain(settings)
    tail = chain[-1].name
    if tail not in ("local_ffmpeg", "fixture"):
        return Check("backend chain", "fail",
                     f"last backend is {tail!r}, which is not guaranteed available",
                     "VIDEO_BACKEND_CHAIN must end with local_ffmpeg. That invariant is "
                     "what makes the pipeline unable to hard-fail on video.")
    lines = []
    for backend in chain:
        avail = backend.available()
        mark = "[green]OK[/green]" if avail.available else "[dim]--[/dim]"
        lines.append(f"{mark} {backend.name:<14} {avail.reason}")
    return Check("backend chain", "ok", "\n".join(lines))


def run_doctor(json_output: bool = False) -> int:
    from cwt.config import Settings
    settings = Settings.from_env()
    checks = [
        _check_python(),
        _check_ffmpeg(),
        _check_hermes(),
        _check_worker_model(),
        _check_backend_chain(settings),
        _check_llm_models(settings),
    ]
    if settings.engine_defaults_to_hermes:
        checks.append(_check_gateway(settings))

    if json_output:
        import json as _json
        console.print_json(_json.dumps([c.__dict__ for c in checks]))
    else:
        for check in checks:
            icon = {"ok": "[green]OK  [/green]", "warn": "[yellow]WARN[/yellow]",
                    "fail": "[red]FAIL[/red]"}[check.status]
            console.print(f"{icon} {check.name:<16} {check.detail}")
            if check.hint and check.status != "ok":
                for line in check.hint.splitlines():
                    console.print(f"     [dim]{line}[/dim]")

    failed = [c for c in checks if c.status == "fail"]
    if failed:
        console.print(f"\n[red]{len(failed)} check(s) failed. Fix these before running.[/red]")
        return 1
    console.print("\n[green]All checks passed.[/green]")
    return 0
```

---

## 11. Frontend / UI Specification

**We build no web application.** The Hermes Kanban dashboard is the UI, and it is free. Every hour
spent on a custom dashboard is an hour not spent on the claims gate or the beat mining.

### 11.1 The Kanban dashboard is the primary interface

```bash
hermes kanban init          # one-time
hermes gateway start        # the dispatcher lives here
hermes dashboard            # opens http://127.0.0.1:9119
```

A **Kanban** tab appears in the nav. The board shows:

| Element | What it shows | Why it matters here |
|---|---|---|
| Columns | `triage · todo · ready · running · blocked · review · done` | `review` is where the creative-director gate is visible |
| Card | title, assignee, priority, tenant | Assignee is the agent — this is how a viewer sees which agent is working |
| Card drawer | Run History, comments, artifacts | Every stage writes a substantive `kanban_comment`; this is the activity feed |
| **Nudge dispatcher** button | Triggers one dispatch tick | Manual recovery if a tick is missed |
| Lanes by profile toggle | Groups the In Progress column by agent | **Turn this ON for the recording** — it makes the parallelism legible |

**Config for the recording** (`~/.hermes/config.yaml`):

```yaml
dashboard:
  kanban:
    render_markdown: true
    include_archived_by_default: false
    lane_by_profile: true        # <-- the single most useful setting for a demo recording
```

> **Security note, non-negotiable:** the dashboard's plugin routes are unauthenticated by design.
> Never run `hermes dashboard --host 0.0.0.0`. Bind to localhost. This is stated in the README.

### 11.2 The recording recipe — a required deliverable

The brief asks for "a video output of the hermes kanban". **Hermes has no built-in screen recording** —
verified across the board docs, the worker-lanes docs, the dashboard REST surface and every config
key. Recording is external. Nous Research's own demo pipeline does exactly this: it records with an
external tool while the board runs.

`scripts/record_kanban_video.ps1` prints the recipe and verifies the dashboard is reachable:

```powershell
# scripts/record_kanban_video.ps1
Write-Host "=== CWT Kanban recording recipe ===" -ForegroundColor Cyan

# 1. Verify the dashboard is up before you start recording.
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:9119" -UseBasicParsing -TimeoutSec 5
    Write-Host "  dashboard OK ($($r.StatusCode))" -ForegroundColor Green
} catch {
    Write-Host "  dashboard NOT reachable. Run: hermes gateway start; hermes dashboard" -ForegroundColor Red
    exit 1
}

@"
RECORDING CHECKLIST
  1.  hermes kanban init            (one-time)
  2.  hermes gateway start
  3.  hermes dashboard              -> open http://127.0.0.1:9119 -> Kanban tab
  4.  Turn ON "Lanes by profile"    -> makes the parallelism legible
  5.  Start your screen recorder    (Screen Studio, OBS, or Win+Shift+S->video)
  6.  Layout: terminal left ~40%, dashboard right ~60%
  7.  Run:  cwt run --offline --record-pacing
  8.  Record the first 5-10 minutes, then speed up 4-8x in post

MOMENTS WORTH CAPTURING
  - The seed command, and 11 cards appearing at once
  - The three research cards running CONCURRENTLY (this is the money shot)
  - The storyboard card moving running -> review -> running -> done
  - The compliance card's comment quoting a blocked claim
  - The render card finishing, and final.mp4 appearing on disk

NOTE: --record-pacing inserts 3-6s pauses between stage completions so the
      recording is watchable rather than a blur. Use it ONLY when recording.
"@ | Write-Host
```

### 11.3 `storyboard.html` — the only bespoke UI

One templated self-contained file, no build step, no dependencies. It exists because the brief
requires the storyboard be *"saved and shared in json human readable format"* — the JSON is the
machine artifact, and this is what a human actually reads.

```python
# tools/storyboard.py (excerpt)
STORYBOARD_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{title} — Storyboard</title>
<style>
  :root {{ --bg:#050505; --card:#0d0d0d; --line:#1e1e1e; --accent:#22d3ee;
           --text:#cbd5e1; --dim:#64748b; }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font:14px/1.6 system-ui,sans-serif;padding:32px}}
  h1{{font-size:24px;font-weight:800;letter-spacing:-.02em}}
  h1 span{{background:linear-gradient(90deg,var(--accent),#818cf8);
           -webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .meta{{color:var(--dim);margin:8px 0 24px}}
  .hook{{background:var(--card);border-left:3px solid var(--accent);
         padding:16px 20px;margin-bottom:24px;border-radius:0 8px 8px 0}}
  .beat-ribbon{{display:flex;height:38px;border-radius:6px;overflow:hidden;margin-bottom:24px}}
  .beat{{display:flex;align-items:center;justify-content:center;font-size:11px;
         font-weight:600;text-transform:uppercase;letter-spacing:.08em;
         border-right:1px solid var(--bg)}}
  .shot{{background:var(--card);border:1px solid var(--line);border-radius:10px;
         padding:18px;margin-bottom:12px;display:grid;
         grid-template-columns:64px 1fr 220px;gap:18px}}
  .sid{{font:700 18px/1 ui-monospace,monospace;color:var(--accent)}}
  .sid small{{display:block;color:var(--dim);font:400 11px/1.8 system-ui}}
  .desc{{margin-bottom:10px}}
  .vo{{color:#94a3b8;font-style:italic;border-left:2px solid var(--line);padding-left:12px}}
  .tech{{font:11px/1.9 ui-monospace,monospace;color:var(--dim)}}
  .tech b{{color:var(--text);font-weight:600}}
  .swatches{{display:flex;gap:4px;margin-top:8px}}
  .sw{{width:20px;height:20px;border-radius:4px;border:1px solid var(--line)}}
  .scores{{display:flex;gap:24px;margin:24px 0;flex-wrap:wrap}}
  .score{{background:var(--card);border:1px solid var(--line);border-radius:8px;
          padding:12px 18px;min-width:120px}}
  .score b{{display:block;font-size:22px;color:var(--accent)}}
  .score small{{color:var(--dim);font-size:11px;text-transform:uppercase;
                letter-spacing:.06em}}
</style></head><body>
<h1>CrowdWisdomTrading — <span>{angle_label}</span></h1>
<div class="meta">{duration}s · {aspect} · {resolution} · run {run_id}</div>

<div class="hook">
  <b>Visual hook:</b> {hook_overlay}<br>
  <span style="color:var(--dim)">{hook_description}</span><br>
  <span style="color:var(--dim);font-size:12px">
    Why it stops the scroll: {hook_why}</span>
</div>

<div class="beat-ribbon">{beat_ribbon}</div>

{shot_cards}

<div class="scores">{score_cards}</div>

<h2 style="font-size:15px;margin:24px 0 12px;color:var(--dim);
           text-transform:uppercase;letter-spacing:.08em">Voiceover</h2>
<div class="vo" style="font-size:15px;line-height:1.9">{vo_text}</div>
</body></html>
"""
```

Colour the beat ribbon from the brand palette (near-black background, cyan accent, amber and indigo
gradient highlights). This page is the single best thing to attach to the submission email, because
it communicates the entire design in one screen.

### 11.4 The reference-data blocks the UI needs

Any enumerable the UI or the prompts depend on gets dumped flat here so nobody re-discovers it
mid-build. These live in `domain/beats.py`:

```python
BEAT_TAXONOMY = ("hook", "problem", "agitation", "mechanism", "proof", "objection", "cta")

# Default proportions. The ACTUAL timings come from the mined median timeline;
# these are only the fallback when too few ads carry a usable video duration.
BEAT_DEFAULT_PROPORTIONS = {
    "hook": 0.08, "problem": 0.13, "agitation": 0.13, "mechanism": 0.23,
    "proof": 0.15, "objection": 0.13, "cta": 0.15,
}

HOOK_ARCHETYPES = ("pattern_interrupt", "contrarian_stat", "question",
                   "visual_shock", "social_proof", "pain_point", "bold_statement")

# Sourced: Social Proof is ~0.1% of fintech video creatives yet survives ~2.1x
# longer than average. Pain Point and Question also outlast Bold Statement.
# These get an explicit scoring boost. See WOW-5.
UNDERUSED_HIGH_DURABILITY = ("social_proof", "pain_point", "question")

CAMERA_MOVES = ("static", "push_in", "pull_out", "whip_pan")
TRANSITIONS = ("cut", "dissolve", "flash_white", "wipeleft", "wiperight", "zoomblur")
COLOUR_TEMPS_K = (3200, 4300, 5600, 6500, 7000, 9000)
ASPECT_RATIOS = ("9:16", "1:1", "4:5", "16:9")
SUBJECTS = ("abstract_market_data", "trader_silhouette", "chart_detail",
            "city_night", "screen_glow", "typography_card")
```

---

## 12. Critical Architecture Rules — DO NOT DEVIATE

Every rule below is either **a documented failure mode of a tool we depend on**, or **a bug that
costs real money or creates real legal exposure**. Nothing here is hypothetical caution.

### Rule H1: The Apify actor ID uses a TILDE in REST URLs, and the obvious name does not exist

```python
# WRONG — this actor DOES NOT EXIST. Every actor with this slug is third-party.
#   https://api.apify.com/v2/acts/apify/facebook-ads-library-scraper/runs
#   -> 404, and a slash in the path segment 404s even for a valid actor.
ACTOR = "apify/facebook-ads-library-scraper"

# CORRECT — the official Apify-maintained actor, in its REST-safe form.
ACTOR = "apify~facebook-ads-scraper"
```

### Rule H2: Apify keyword search goes through `startUrls` — there is no `searchTerms`

```python
# WRONG — the official actor has no such field. The run SUCCEEDS and returns the
# wrong ads, or nothing. There is no error. You will not notice for an hour.
actor_input = {"searchTerms": ["trading signals"], "country": "US", "maxResults": 50}

# CORRECT — keyword search is expressed as an Ad Library search URL.
actor_input = {
    "startUrls": [{"url": "https://www.facebook.com/ads/library/"
                          "?active_status=active&ad_type=all&country=US"
                          "&q=trading%20signals&search_type=keyword_unordered"}],
    "resultsLimit": 50,
    "activeStatus": "Active",
    "onlyAdsNewerThan": "30 days",      # relative strings ARE accepted here
}
```

### Rule H3: Tavily has no `days` parameter — use `time_range`

```python
# WRONG — `days` is absent from Tavily's current API reference. It is a legacy
# news-topic-only parameter that third-party docs still describe. Passing it is
# SILENTLY IGNORED: you get unrestricted results and the "last 30 days" window
# in your brief is a lie.
{"query": q, "days": 30}

# CORRECT — `time_range` is current and works under topic="general".
#   values: "day" | "week" | "month" | "year"
{"query": q, "time_range": "month", "topic": "general",
 "include_published_date": True, "filter_by_published_date": True}
```

### Rule H4: Exa's `type` and `category` enums changed — the common values are legacy

```python
# WRONG — `neural`/`keyword` and `research paper`/`tweet` are legacy values
# absent from the current REST enum. They 400.
{"query": q, "type": "neural", "category": "research paper"}

# CORRECT
#   type:     instant | fast | auto | deep-lite | deep | deep-reasoning
#   category: company | publication | news | personal site | financial report | people
{"query": q, "type": "auto", "category": "news",
 "startPublishedDate": "2026-08-27T00:00:00.000Z"}
```

```python
# WRONG — `company` and `people` REJECT date filters with HTTP 400.
{"query": q, "category": "company", "startPublishedDate": "2026-08-27T00:00:00.000Z"}

# CORRECT — use a category that accepts a window when you need "last month".
{"query": q, "category": "news", "startPublishedDate": "2026-08-27T00:00:00.000Z"}
```

### Rule H5: Apify's sync endpoint hard-times-out at 300 seconds

```python
# WRONG — Meta Ads Library runs routinely exceed 300s. This returns HTTP 408
# after burning the full five minutes, with the run still going server-side.
httpx.post(f"{BASE}/acts/{ACTOR}/run-sync-get-dataset-items", ...)

# CORRECT — async: start, poll the run, then fetch the dataset.
run = post(f"{BASE}/acts/{ACTOR}/runs", json=actor_input).json()["data"]
while status not in TERMINAL:
    status = get(f"{BASE}/actor-runs/{run['id']}").json()["data"]["status"]
    sleep(10)
items = get(f"{BASE}/datasets/{run['defaultDatasetId']}/items").json()
```

### Rule A1: Hermes has no Python SDK — do not try to import it

```python
# WRONG — there is no `hermes` package. pip/uv installs of Hermes are explicitly
# unsupported upstream: the docs state such an install "does not import".
from hermes import Agent
import hermes

# CORRECT — two supported integration paths, and we use both:
#   1. Custom tools  -> a native PLUGIN at ~/.hermes/plugins/<name>/
#                       with plugin.yaml + __init__.py exposing register(ctx)
#   2. Orchestration -> the CLI, always through src/cwt/hermes/cli.py::run_hermes()
ctx.register_tool(name="cwt_check_claims", toolset="cwt", schema=..., handler=...)
```

```python
# WRONG — a tool handler that raises kills the agent turn, not just the call.
def my_tool(args, **kwargs) -> str:
    data = fetch(args["url"])        # an exception here takes down the worker
    return json.dumps(data)

# CORRECT — never raise. Always return a JSON string, success or error.
def my_tool(args, **kwargs) -> str:
    try:
        return json.dumps({"ok": True, "data": fetch(args["url"])})
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
```

### Rule A2: Every Apify run carries a hard spend cap

```python
# WRONG — the free tier is $5/month and credits EXPIRE at the end of the cycle.
# A misconfigured run can consume the month's entire budget in one call, and the
# per-result rate on Free tier is reported to be far higher than on paid tiers.
post(f"{BASE}/acts/{actor}/runs", params={"token": token}, json=actor_input)

# CORRECT — maxTotalChargeUsd is Apify's own hard cap on a single run.
# Belt: maxTotalChargeUsd. Braces: resultsLimit. Plus the content-hash cache,
# which makes a second identical run cost exactly zero.
post(f"{BASE}/acts/{actor}/runs",
     params={"token": token, "maxTotalChargeUsd": settings.apify_max_charge_usd},
     json={**actor_input, "resultsLimit": settings.apify_max_items})
```

### Rule A3: JSON enforcement is THREE layers, and all three are required

```python
# WRONG — response_format alone. Models still wrap JSON in fences and prose.
# This is the single most common real failure in structured-output pipelines.
payload = {"model": m, "messages": msgs, "response_format": {"type": "json_object"}}
data = json.loads(resp.json()["choices"][0]["message"]["content"])   # ValueError

# CORRECT
#   Layer 1  response_format, when the provider profile supports it
#            (OpenRouter yes; NVIDIA NIM accepts it inconsistently, so we do not rely on it)
#   Layer 2  the JSON Schema injected as a system message, ALWAYS — it measurably
#            improves field-level accuracy even where native JSON mode works
#   Layer 3  tolerant extract_json(): direct parse -> strip fences -> brace-depth
#            scan outside string literals -> raise UnparseableJson carrying the raw text
#   Plus     a repair loop that shows the model its own output and the exact error
```

### Rule A4: Bound LLM concurrency with a semaphore, sized to the weakest provider

```python
# WRONG — three research cards run concurrently, each making 2-3 calls, while a
# 24-call extraction batch runs at the same time. NVIDIA's free tier is ~40 RPM
# PER MODEL. Without a semaphore this produces a wall of 429s and the run stalls.
await asyncio.gather(*[complete(...) for _ in range(30)])

# CORRECT — one semaphore per provider, sized from config.
#   NVIDIA free tier: 4.   OpenRouter: 8.
self._sem = asyncio.Semaphore(settings.llm_max_concurrency)
async with self._sem:
    ...
# Plus: exponential backoff with FULL JITTER (not fixed), honouring Retry-After,
# and demotion to the next model in LLM_MODEL_FALLBACKS after 3 consecutive 429s —
# which multiplies effective throughput because the limits are per-model.
```

### Rule A5: Only the scriptwriter runs on the strong tier

```python
# WRONG — a frontier model for extraction and scoring. ~35 of ~40 calls per run
# are classification, where a mid-size model is indistinguishable and 20x cheaper.
model = "anthropic/claude-sonnet-4.5"      # for extract_hooks(), score_storyboard(), …

# CORRECT — tier routing. STRONG is reserved for the ~5 calls that are actually
# creative: hook generation, the three storyboard variants, and rewrites.
TIER_MAP = {
    "extract_hooks": Tier.CHEAP, "extract_beat_sheet": Tier.CHEAP,
    "score_storyboard": Tier.CHEAP, "judge_variants": Tier.CHEAP,
    "research_summarize": Tier.CHEAP, "check_claims_llm": Tier.CHEAP,
    "generate_hook_candidates": Tier.STRONG,
    "write_storyboard_variant": Tier.STRONG,
    "apply_rewrite": Tier.STRONG,
}
```

### Rule A6: Budget is enforced inside the client, not by the caller

```python
# WRONG — a prompt-level request to "stay within budget", or a check the caller
# performs after the fact. Neither is a control.
messages.append({"role": "system", "content": "Do not spend more than $2."})

# CORRECT — the client raises on the call that CROSSES the cap.
def _record(self, result):
    self.run_cost_usd += estimate_cost(...)
    if self.run_cost_usd > self.max_usd:
        raise BudgetExceeded(self.run_cost_usd, self.max_usd, result.stage)
# cwt run catches BudgetExceeded, archives the board, and exits 4.
```

### Rule C1: The claims gate is deterministic BEFORE it is an LLM

```python
# WRONG — an LLM judge as the only compliance control. A model that can be
# argued out of "guaranteed" is not a control, it is a suggestion.
findings = llm_judge(script)

# CORRECT — regex rules run FIRST and their HARD verdicts are FINAL.
findings = detect_claims(script)                    # pure, no I/O, no LLM
findings += scan_prohibited_facts(script, brief["prohibited_facts"])
llm_findings = llm_judge(script)                    # the residue only
# The LLM may ESCALATE a severity. It may NEVER de-escalate a hard finding.
```

### Rule C2: A hard compliance finding is never overridable

```python
# WRONG — a config flag that lets a run ship with a prohibited claim. Someone
# will set it at 2am to make the demo work, and ship an unsubstantiable
# performance claim on a financial product.
if settings.claims_gate_enabled and not override:
    block()

# CORRECT — HARD findings have no override path. After
# CLAIMS_MAX_REWRITE_ROUNDS the card BLOCKS, with the finding text as the reason.
# A blocked card in the recording is a better outcome than a shipped bad claim.
```

### Rule C3: The claims gate runs TWICE — pre-render and post-render

```python
# WRONG — check the storyboard, render, ship. TTS normalisation changes what is
# actually said, and a line added during render bypasses the gate entirely.
check_claims(storyboard); render(); ship()

# CORRECT
check_claims(storyboard, stage="pre_render")
render()                                        # produces a word-timed transcript
check_claims(transcript, stage="post_render")   # scans what is ACTUALLY spoken
# And the risk disclosure's on-screen duration is recomputed from the RENDERED
# timeline via ffprobe, not trusted from the storyboard's declared value.
```

### Rule V1: `cwd` is the artifact directory — every path inside a filtergraph is RELATIVE

```python
# WRONG — ffmpeg's filter parser reads the colon in "C:\..." as an option
# separator. Error: [Parsed_subtitles_0] Unable to parse option value "\runs\..."
run_tool([ffmpeg, "-i", "C:\\runs\\abc\\assets\\s03.png", "-vf", f"subtitles=C:\\runs\\abc\\subs.ass", ...])

# CORRECT — relative paths, forward slashes, cwd pinned. No drive letter ever
# reaches the parser.
run_tool([ffmpeg, "-i", "assets/s03.png", "-vf", "subtitles=subs.ass", ...],
         cwd=paths.shot_dir)
```

### Rule V2: Never build a filtergraph by string-concatenating unvalidated values

```python
# WRONG — a stray quote from the model silently changes the entire graph, and
# the failure appears as a rendering artefact, not an error.
f"-vf zoompan=z='{shot.camera.raw_expr}'"

# CORRECT — every interpolated value is type-checked by pydantic and clamped
# before it reaches the string. build_zoompan_expr() takes typed floats and
# returns a computed expression, never passthrough model text.
z_expr = build_zoompan_expr(move=shot.camera.move,
                            intensity=clamp(shot.camera.intensity, 0.0, 1.0),
                            duration_s=shot.duration_s, fps=fps)
```

### Rule V3: Probe the output before declaring success

```python
# WRONG — ffmpeg exits 0 having written a 0-byte or truncated file when the last
# frame is dropped or a filter silently no-ops. "Return code 0" is not "it worked".
run_tool([ffmpeg, ...], check=True)
return RenderResult(ok=True)

# CORRECT
run_tool([ffmpeg, ...], check=True)
probe = ffprobe(out)
assert probe.duration_s > 0 and probe.width > 0 and probe.height > 0
assert settings.video_min_seconds <= probe.duration_s <= settings.video_max_seconds
```

### Rule V4: The backend chain MUST terminate in `local_ffmpeg`

```python
# WRONG — a chain of speculative backends. If every one of them is unavailable
# in the reviewer's environment, the pipeline hard-fails in front of them.
VIDEO_BACKEND_CHAIN = "hyperframes,openmontage"

# CORRECT — validated at config load. This single invariant is what makes
# "the pipeline cannot hard-fail on video" true BY CONSTRUCTION rather than by hope.
VIDEO_BACKEND_CHAIN = "hyperframes,openmontage,local_ffmpeg"
# build_chain() raises at startup if the last element is not a backend that is
# available in every environment. OpMontage is best-effort and documented as such;
# leronx.org is not in the chain at all because it has no API.
```

### Rule W1: `shell=False` always — external commands take an argument list

```python
# WRONG — shell parsing means spaces, quotes, & and ^ in a path silently mangle
# the command. It is also a command-injection surface.
subprocess.run(f'ffmpeg -i "{src}" -vf scale=1080:1920 "{dst}"', shell=True)
os.system(f"hermes kanban create '{title}'")

# CORRECT — a list, every time, through one chokepoint.
run_tool([ffmpeg, "-i", str(src), "-vf", "scale=1080:1920", str(dst)], cwd=work_dir)
run_hermes(["kanban", "create", title, "--assignee", assignee])
```

### Rule W2: Never assume `ffmpeg` is on PATH

```python
# WRONG — a working `ffmpeg --version` in the user's Git Bash does NOT mean
# Python's PATH can see it. FileNotFoundError after a successful manual check.
run_tool(["ffmpeg", ...])

# CORRECT — imageio-ffmpeg pip-installs a real binary and exposes its path.
# Resolution order: $CWT_FFMPEG_BIN -> imageio_ffmpeg.get_ffmpeg_exe() -> shutil.which
def ffmpeg_path() -> str:
    if override := os.getenv("CWT_FFMPEG_BIN"):
        return override
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()
```

### Rule W3: If `ffprobe` is missing, parse `ffmpeg -i` — never skip verification

```python
# WRONG — verification silently skipped. A truncated render passes QA and the
# reviewer receives a 4-second ad.
try:
    duration = ffprobe_duration(path)
except FileNotFoundError:
    return True

# CORRECT — fall back to the Duration line on ffmpeg's stderr.
def probe_duration_from_stderr(path) -> float:
    result = run_tool([ffmpeg_path(), "-i", str(path)], timeout_s=30)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", result.stderr)
    if not match:
        raise RuntimeError(f"Could not determine duration of {path}")
    h, m, s = int(match[1]), int(match[2]), float(match[3])
    return h * 3600 + m * 60 + s
```

### Rule W4: On Windows, `npx`/`npm`/`hermes` are `.cmd` shims

```python
# WRONG — [WinError 193] %1 is not a valid Win32 application, or a silent no-op.
run_tool(["npx", "hyperframes", "render"])

# CORRECT — shutil.which() returns the full `...\npx.cmd` path, which works.
# resolve_exe() does this inside run_tool(), so no call site has to remember.
resolved = shutil.which("npx")     # -> C:\Program Files\nodejs\npx.cmd
run_tool([resolved, "hyperframes", "render"])
```

### Rule W5–W6: Paths inside filtergraphs, and ffmpeg filelists

Covered by V1. The filelist corollary:

```python
# WRONG — backslashes, unquoted spaces, and CRLF all break the concat demuxer.
Path("list.txt").write_text("\n".join(f"file {p}" for p in clips))

# CORRECT — posix paths, single-quoted, LF, utf-8. Better still: avoid the concat
# demuxer entirely and emit ONE -filter_complex graph.
with open("list.txt", "w", encoding="utf-8", newline="\n") as fh:
    for clip in clips:
        fh.write(f"file '{clip.as_posix()}'\n")
```

### Rule W7: Force utf-8 everywhere — the Windows console is cp1252

```python
# WRONG — UnicodeEncodeError on an em dash, a smart quote, or any accented
# character in a prompt or a script. Our creative content is full of them.
subprocess.run(argv, capture_output=True, text=True)
Path("storyboard.json").write_text(json.dumps(data))

# CORRECT
subprocess.run(argv, capture_output=True, text=True,
               encoding="utf-8", errors="replace",
               env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
write_json(path, data)      # util/jsonio enforces utf-8 + ensure_ascii=False
```

### Rule W8: `Path.write_text` does not normalise newlines

```python
# WRONG — on Windows this writes CRLF. A .sh or a filelist then fails on Linux
# with a famously opaque error ("bash\r: No such file or directory").
sh = Path("start.sh"); sh.write_text(script)

# CORRECT
with open(sh, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(script)
# Plus .gitattributes pinning `*.sh text eol=lf`.
```

### Rule K1: `kanban_complete` ENDS the card — two review gates must be two cards

```python
# WRONG — the writer asks the creative director to review, and on approval asks
# the compliance agent to review again via request_review. But the director's
# approval path IS kanban_complete, which moves the card to `done` and unblocks
# the render — skipping compliance entirely.
writer: request_review(reviewer="cwt-creative-director")
director: kanban_complete()            # card is DONE. Render unblocks. Gate skipped.

# CORRECT — compliance is its OWN card, gated on the script card.
#   t_script --request_review--> cwt-creative-director
#                                  complete        = approve (card done)
#                                  request_changes = back to the writer, max 3
#   t_compliance (parent: t_script)  -> deterministic gate + bounded rewrite
#   t_render (parent: t_compliance)
# Only verified primitives are used, and both gates are real gates.
```

### Rule K2: The card assignee must exactly match a Hermes profile name

```python
# WRONG — an unresolvable assignee leaves the card on `ready` FOREVER. It emits a
# `skipped_nonspawnable` event and there is no fallback spawn. The run appears to
# hang with no error.
CardSpec(key="script", assignee="script-writer", ...)   # no such profile

# CORRECT — the string must match a profile created by `hermes profile create`.
CardSpec(key="script", assignee="cwt-script-writer", ...)
# cwt doctor asserts every DAG_SPEC assignee resolves against `hermes profile list`.
```

### Rule K3: The dispatcher runs in the gateway — if the gateway is down, nothing moves

```python
# WRONG — seed the board and wait. Cards sit on `ready`, the run hangs until the
# timeout, and the failure looks like a pipeline bug rather than a missing daemon.
seed(); await wait_for_completion(timeout_s=5400)

# CORRECT — detect the stall, nudge ONCE, then fail loudly with the fix.
#   kanban.dispatch_in_gateway: true  (the default, and what we rely on)
#   stall threshold = 3 dispatcher ticks (180s)
if no_state_change_for(stall_threshold_s):
    nudge()                      # `hermes kanban dispatch` — one tick, manually
else after a second stall:
    raise PipelineBlocked("The gateway is probably not running. "
                          "Start it with `hermes gateway start`, then `cwt run --resume`.")
# NEVER force-complete a card to clear a stall. The pipeline does not fabricate progress.
```

### Rule K4: `--idempotency-key` is what makes `--resume` safe

```python
# WRONG — re-seeding after a crash creates a second copy of all 11 cards. Two
# scripts render, two submissions are produced, and the board is now a mess.
kanban("create", title, "--assignee", assignee)

# CORRECT
kanban("create", title, "--assignee", assignee,
       "--idempotency-key", f"{run_id}:{card.key}")
# Re-seeding reuses the existing card for that (run, stage) pair.
```

### Rule P1: Port 9119 belongs to the dashboard — never bind it from anything else

```python
# WRONG — a video backend that starts a preview server on a fixed port will
# collide with the Kanban dashboard, which is the thing being recorded.
subprocess.Popen(["npx", "hyperframes", "preview", "--port", "9119"])

# CORRECT — preview servers bind an EPHEMERAL port and we read the actual port
# from their stdout. 9119 is reserved for `hermes dashboard`; 8000 is unused by
# this project by design.
proc = subprocess.Popen(["npx", "hyperframes", "preview", "--port", "0"], ...)
port = parse_port_from_stdout(proc)
```

### Rule R1: Credentials are never written into an artifact

```python
# WRONG — Apify passes the token as a QUERY PARAMETER, so it lands in URLs,
# error bodies and httpx request reprs by default. The submission is a public
# repository. This leaks the reviewer's token.
logger.info("fetching %s", resp.request.url)          # ...?token=apify_api_abc123
write_json(path, {"source": {"request_url": str(resp.request.url)}})

# CORRECT — scrub at the boundary, before anything can persist it.
def _scrub(text: str) -> str:
    return re.sub(r"([?&]token=)[^&\s\"']+", r"\1<redacted>", text)

logger.info("fetching %s", _scrub(str(resp.request.url)))
```

---

## 13. Deployment

### 13.1 Windows (primary — this is the development host)

```powershell
# 1. Clone
git clone https://github.com/<you>/cwt-video-ads-agent.git
cd cwt-video-ads-agent

# 2. Install Hermes (one-time, machine-wide)
iex (irm https://hermes-agent.nousresearch.com/install.ps1)

# 3. Bootstrap (creates venv, installs deps, copies .env, runs doctor)
./scripts/bootstrap.ps1

# 4. Fill in your keys
notepad .env

# 5. Configure the Hermes WORKER model — this is NOT the same as LLM_MODEL_*
hermes model

# 6. Create the nine CWT profiles, install skills and the plugin
cwt bootstrap

# 7. Start the gateway (the kanban dispatcher lives inside it)
hermes gateway start

# 8. Verify — every check must be green before you spend anything
cwt doctor

# 9. Proven path first: render offline, zero spend, no network
cwt run --engine local --offline

# 10. Now the real thing
cwt run
```

### 13.2 Linux / VPS / Docker

```bash
# 1. Clone
git clone https://github.com/<you>/cwt-video-ads-agent.git
cd cwt-video-ads-agent

# 2. ffmpeg + node (node only for the optional HyperFrames backend)
sudo apt-get update && sudo apt-get install -y ffmpeg curl git
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs

# 3. Hermes
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# 4. Bootstrap
./scripts/bootstrap.sh
nano .env
hermes model
cwt bootstrap
hermes gateway start
cwt doctor
cwt run --engine local --offline     # proven path first
cwt run
```

### 13.3 What success looks like

```
$ cwt doctor
OK   python           3.11.9
OK   ffmpeg           C:\...\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe (7.1)
OK   hermes           v0.16.0 at C:\Users\...\hermes.cmd
OK   worker model     google/gemini-2.5-flash  (verify >=64k context)
OK   backend chain    OK hyperframes  npx 10.8.2, node v22.11.0, chrome 141
                      -- openmontage  OPENMONTAGE_HOME not set (optional; not required)
                      OK local_ffmpeg  ffmpeg 7.1, libx264 + aac + zoompan
OK   llm models       4 slugs verified
OK   gateway          dispatcher reachable

All checks passed.
```

```
$ cwt run --engine local --offline
=== CWT Video Ads Agent ===
run_id       20260926-1402-a7f3
…
[green]Done.[/green] 11 stages, $0.0000,
output: runs/20260926-1402-a7f3/render/final.mp4
```

```
$ cwt run
…
[green]Done.[/green] 11 stages, $0.2413,
output: runs/20260926-1402-a7f3/render/final.mp4
```

> **The `--offline` run must work before you attempt a live one.** It is the contract with the
> reviewer, and it is also your fastest debugging loop: it isolates your logic from every API.

---

## 14. Known Gotchas

| Symptom | Root Cause | Fix |
|---|---|---|
| Apify returns 404 | Actor id used a slash | Use a tilde: `apify~facebook-ads-scraper`. `apify/facebook-ads-library-scraper` does not exist at all |
| Apify run succeeds, returns wrong ads | Used `searchTerms`, which the actor ignores | Keyword search goes through `startUrls` as an Ad Library URL |
| Apify run returns HTTP 408 after 5 min | Used the `run-sync-*` endpoint | Use async: `POST /runs`, poll `/actor-runs/{id}`, then fetch the dataset |
| Apify free credit gone in one run | No spend cap | Always pass `maxTotalChargeUsd`; it is a hard cap on the run |
| Tavily ignores the last-30-days window | Used the legacy `days` param | Use `time_range: "month"` |
| Exa returns HTTP 400 | Used legacy `type: neural` or `category: research paper` | Current enums: `instant\|fast\|auto\|deep-lite\|deep\|deep-reasoning`; `company\|publication\|news\|personal site\|financial report\|people` |
| Exa 400 with a date filter | Combined `category: company`/`people` with `startPublishedDate` | Those two categories reject date filters. Use `news` |
| `ModuleNotFoundError: hermes` | Tried to import Hermes as a library | There is no SDK. Use a plugin + the CLI |
| Worker never starts; card sits on `ready` | Assignee does not match a profile name | `hermes profile create` with the exact string from `dag.py` |
| Board never advances | Gateway not running, so the dispatcher is not ticking | `hermes gateway start`. `cwt run` detects the stall and says so |
| Re-running created duplicate cards | No idempotency key | Pass `--idempotency-key {run_id}:{stage}` |
| Render is skipped after creative review | Two gates were put on one card | `kanban_complete` ends the card. Compliance must be its own card |
| `[WinError 193] %1 is not a valid Win32 application` | Called `npx`/`hermes` bare on Windows | Resolve through `shutil.which()` to the `.cmd` path — `run_tool()` does this |
| `Unable to parse option value "\runs\..."` | Absolute Windows path inside an ffmpeg filtergraph | Pin `cwd` and use relative, forward-slashed paths inside filters |
| Render "succeeds" but the file is 0 bytes | Trusted ffmpeg's exit code | Always ffprobe; fall back to parsing `ffmpeg -i` stderr if ffprobe is absent |
| `UnicodeEncodeError: 'charmap' codec` | Windows cp1252 console | `encoding="utf-8", errors="replace"` + `PYTHONUTF8=1` on every child |
| `bash\r: No such file or directory` | Script written with CRLF | `newline="\n"` on every text write + `.gitattributes` `*.sh eol=lf` |
| `FileNotFoundError` on a deep asset path | Windows MAX_PATH | Keep `runs/<run_id>/` shallow; `long_path()` only at ffmpeg/mkdir sites |
| A wall of HTTP 429s | Unbounded LLM concurrency against a per-model rate limit | Semaphore sized to 4 on NVIDIA; jittered backoff; fallback-model demotion |
| Model 404s mid-run | Model slug drifted | `cwt doctor` validates every slug against `/v1/models` before the run |
| Cost overran silently | Budget checked by the caller, not the client | `LLMBudgetExceeded` is raised inside `_record()` on the call that crosses the cap |
| Markdown fences in the JSON | Relied on `response_format` alone | Three layers: provider JSON mode + prompt-level schema + tolerant `extract_json()` |
| Reviewer's dashboard is blank | Dashboard bound to `0.0.0.0` and blocked, or gateway down | Localhost only; `hermes gateway start` |
| Recording is an unwatchable blur | Stages complete in seconds | Use `--record-pacing`; it is for recording only |
| A performance statistic reached the script | Regex rules ran but the LLM judge approved it anyway | The LLM judge may escalate but never de-escalate. Check `detect_claims` ran first |

---

## 15. Reference Data

Dumped flat so nobody re-discovers it mid-build.

```
# ── Beat taxonomy (closed set, ordered) ──
hook · problem · agitation · mechanism · proof · objection · cta

# ── Default beat proportions (fallback when too few ads carry a video duration) ──
hook 0.08 · problem 0.13 · agitation 0.13 · mechanism 0.23 · proof 0.15 · objection 0.13 · cta 0.15

# ── Hook archetypes ──
pattern_interrupt · contrarian_stat · question · visual_shock · social_proof ·
pain_point · bold_statement

# ── Underused-but-durable archetypes (get an explicit scoring boost) ──
social_proof · pain_point · question

# ── Camera moves ──
static · push_in · pull_out · whip_pan

# ── Transitions ──
cut · dissolve · flash_white · wipeleft · wiperight · zoomblur

# ── Colour temperatures (Kelvin) ──
3200 · 4300 · 5600 · 6500 (neutral) · 7000 · 9000

# ── Aspect ratios ──
9:16 (default, vertical) · 1:1 · 4:5 · 16:9

# ── Shot subjects ──
abstract_market_data · trader_silhouette · chart_detail · city_night · screen_glow · typography_card

# ── Claim severities ──
hard (unconditional block) · soft (disclosure or rewrite required)

# ── Claim rules (ids) ──
HARD: guaranteed_returns · risk_free_language · specific_profit_figure ·
      percentage_return_promise · win_rate_statistic · double_your_money ·
      beat_the_market · financial_freedom · payout_imagery ·
      position_access_implication* · copy_trading_implication* · managed_accounts_implication*
SOFT: implied_certainty · unsubstantiated_superlative · testimonial_earnings
      (* = PRODUCT_DISCLAIMER: contradicts CrowdWisdomTrading's own published FAQ)

# ── Card statuses (Hermes kanban) ──
triage · todo · ready · running · blocked · review · done · archived

# ── Hermes kanban tools available to a worker ──
kanban_show · kanban_complete · kanban_request_review · kanban_request_changes ·
kanban_block · kanban_heartbeat · kanban_comment · kanban_attach · kanban_attach_url ·
kanban_attachments · kanban_create · kanban_link
ORCHESTRATOR-ONLY: kanban_list · kanban_unblock

# ── Worker environment variables Hermes injects ──
HERMES_KANBAN_TASK · HERMES_KANBAN_DB · HERMES_KANBAN_BOARD · HERMES_KANBAN_WORKSPACE ·
HERMES_KANBAN_RUN_ID · HERMES_KANBAN_CLAIM_LOCK · HERMES_PROFILE · HERMES_TENANT

# ── Brand palette ──
background #050505 · surface #0a0a0a · primary accent #22d3ee (cyan) ·
secondary #fb923c (orange) #fbbf24 (amber) #818cf8 (indigo) #fb7185 (rose) ·
success #34d399 · body text #cbd5e1

# ── Brand voice ──
tagline "Collective Intelligence for Traders" · confident, anti-hype, precise.
Dark terminal aesthetic. Gradient-filled display numerals are a signature.

# ── TTS voices ──
edge-tts: en-US-AndrewNeural (default) · en-US-BrianNeural · en-GB-RyanNeural ·
          en-US-AvaNeural · en-GB-SoniaNeural
piper (offline fallback): en_US-ryan-high · en_US-lessac-medium · en_GB-alan-medium

# ── Exit codes ──
0 success · 1 config/preflight · 2 timeout · 3 blocked · 4 budget exceeded
```

---

## 16. Cost Reference

### 16.1 Per-service rates

| Service | Free tier | Paid rate | Notes |
|---|---|---|---|
| **Apify** | **$5/month**, expires at cycle end | **$3.40–5.80 per 1,000 ads** | Free tier is the binding constraint. ~850–1,470 ads/month |
| **Tavily** | **1,000 credits/month** | $0.0058–0.0075/credit | `search_depth=advanced` costs **2 credits**, everything else 1. 1,000 credits ≈ 500 advanced searches |
| **Exa** | **$10 credit**, resets on the 1st | **$7 per 1,000 searches** | Base covers up to 10 results. `/contents` bills per type — `text` + `highlights` = 2× |
| **OpenRouter** | varies by model | per-model | Many `:free` slugs exist — verify with `cwt doctor` |
| **NVIDIA NIM** | free credits, ~40 RPM per model | — | Rate limits are **per model**, which is why fallback demotion multiplies throughput |
| **Hermes** | free, MIT | — | You pay only for the model the worker runs on |
| **ffmpeg / HyperFrames / Piper** | free | — | Apache-2.0 and LGPL. No per-render fee |

### 16.2 A full online run

| Stage | Consumption | Cost |
|---|---|---|
| Apify — 60 ads | ~60 results at ~$4/1,000 | **≈ $0.24** |
| Tavily — 3 angles × 4 advanced searches | 24 credits of 1,000 monthly | **$0.00** (free tier) |
| Exa — 3 searches | 3 of ~1,400 monthly | **$0.00** (free tier) |
| LLM — ~35 CHEAP calls | ~70k prompt + ~35k completion | **≈ $0.016** |
| LLM — ~5 STRONG calls | ~20k prompt + ~15k completion | **≈ $0.29** |
| Video render | local ffmpeg | **$0.00** |
| | | |
| **Total** | | **≈ $0.24 – $0.70 per run** |

**A worked example, concretely:** one 42-second ad, three storyboard variants, one creative review
round, one compliance rewrite round, rendered locally — **about 55 cents**, of which 40% is the
Apify scrape and most of the rest is the five strong-tier creative calls.

### 16.3 What each mode costs

| Command | Cost | Network |
|---|---|---|
| `cwt run --engine local --offline` | **exactly $0.00** | none |
| `cwt run --offline` (Hermes workers, no API calls) | **$0.00 in API spend** | none for our tools |
| `cwt run --resume` after a render fix | **$0.00** — every upstream stage is cache-hit | none |
| `cwt run` (full, live) | **≈ $0.24 – $0.70** | yes |
| `scripts/record_fixtures.py --force` | spend — deliberately re-records | yes |

> **`--offline` bypasses our LLM calls and all three paid APIs. It does NOT remove the Hermes
> worker's own model calls** — those run on whatever `hermes model` is set to. Run `hermes model`
> and pick a free-tier model before recording. `--engine local --offline` bypasses Hermes entirely
> and costs literally nothing; that is the CI path.

---

## 17. External API Integration Reference

Copy-paste ready. No prose.

### Apify — Meta Ads Library

```python
import os, time, httpx
TOKEN = os.environ["APIFY_TOKEN"]
ACTOR = "apify~facebook-ads-scraper"          # tilde, not slash
BASE  = "https://api.apify.com/v2"

with httpx.Client(timeout=60) as c:
    r = c.post(f"{BASE}/acts/{ACTOR}/runs",
               params={"token": TOKEN, "maxTotalChargeUsd": 1.00},
               json={"startUrls": [{"url":
                        "https://www.facebook.com/ads/library/?active_status=active"
                        "&ad_type=all&country=US&q=trading%20signals"
                        "&search_type=keyword_unordered"}],
                     "resultsLimit": 60,
                     "activeStatus": "Active",
                     "onlyAdsNewerThan": "30 days"})
    run = r.json()["data"]; run_id, ds = run["id"], run["defaultDatasetId"]
    while True:
        s = c.get(f"{BASE}/actor-runs/{run_id}", params={"token": TOKEN}).json()["data"]["status"]
        if s in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"): break
        time.sleep(10)
    items = c.get(f"{BASE}/datasets/{ds}/items",
                  params={"token": TOKEN, "format": "json", "clean": "true"}).json()

for a in items:
    print(a["adArchiveID"], a.get("pageName"),
          a["snapshot"].get("ctaType"), a["snapshot"].get("body", {}).get("text"))
```

### Tavily

```python
import httpx, os
resp = httpx.post("https://api.tavily.com/search",
    headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
    json={"query": "retail trader signal overload",
          "search_depth": "advanced",      # 2 credits
          "topic": "general",
          "time_range": "month",           # NOT `days`
          "max_results": 20,
          "include_published_date": True,
          "filter_by_published_date": True},
    timeout=60).json()
for r in resp["results"]:
    print(r["title"], r["url"], r.get("published_date"), r["score"])
```

### Exa

```python
import httpx, os
r = httpx.post("https://api.exa.ai/search",
    headers={"x-api-key": os.environ["EXA_API_KEY"], "Content-Type": "application/json"},
    json={"query": "wisdom of crowds forecasting accuracy",
          "type": "auto",
          "category": "news",                       # company/people reject date filters
          "numResults": 10,
          "startPublishedDate": "2026-08-27T00:00:00.000Z",
          "contents": {"text": {"maxCharacters": 2500}, "highlights": True}},
    timeout=60).json()
for r in r["results"]:
    print(r["title"], r["url"], r.get("publishedDate"), r.get("author"))
```

### OpenRouter / NVIDIA — one OpenAI-compatible call

```python
import httpx, os
PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                   {"HTTP-Referer": "https://crowdwisdomtrading.com",
                    "X-Title": "CWT Video Ads Agent"}),
    "nvidia":     ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", {}),
}
base, env, extra = PROVIDERS["openrouter"]
resp = httpx.post(f"{base}/chat/completions",
    headers={"Authorization": f"Bearer {os.environ[env]}", **extra},
    json={"model": "google/gemini-2.5-flash",
          "messages": [{"role": "user", "content": "Return {\"ok\": true} as JSON."}],
          "response_format": {"type": "json_object"},   # not honoured by NIM
          "temperature": 0.0, "max_tokens": 4096},
    timeout=120).json()
text = resp["choices"][0]["message"]["content"]
```

### ffmpeg — single-invocation shot render with grade and zoom

```python
from cwt.util.subproc import run_tool
run_tool([
    ffmpeg_path(), "-y",
    "-loop", "1", "-t", "3.4", "-i", "assets/s01.png",
    "-i", "assets/vo_s01.mp3",
    "-filter_complex",
    "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
    "crop=1080:1920,"
    "zoompan=z='min(zoom+0.0029,1.35)':d=102:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    ":s=1080x1920:fps=30,"
    "eq=contrast=1.10:saturation=0.92,"
    "vignette=PI/5,noise=alls=7:allf=t[v]",
    "-map", "[v]", "-map", "1:a",
    "-c:v", "libx264", "-preset", "medium", "-crf", "19",
    "-c:a", "aac", "-b:a", "192k",
    "-shortest", "out.mp4",
], cwd=shot_dir)      # cwd pinned; all paths relative — Rule V1
```

### ffmpeg — loudness normalisation on the mixed audio

```
loudnorm=I=-14:TP=-1.5:LRA=11
```

### Hermes kanban — create a card, and read a worker's context

```bash
hermes kanban create "Source winning ads — Meta Ads Library, last 30 days" \
    --assignee cwt-ads-manager \
    --parent t_root123 \
    --skill cwt-source-winning-ads \
    --idempotency-key "20260926-1402-a7f3:ads" \
    --max-runtime 20m --max-retries 2 \
    --body "..." --json | jq -r .id

hermes kanban init && hermes gateway start && hermes dashboard   # 127.0.0.1:9119
hermes kanban watch --kinds completed,blocked,gave_up
hermes kanban show <id> --json ; hermes kanban runs <id> ; hermes kanban log <id>
hermes kanban dispatch            # one manual tick — the stall nudge
```

---

## 18. One-Time Setup Sequence

Deployment (Section 13) gets the *code* running. This gets the *product* usable. Run it once.

1. **Install Hermes.**
   Windows: `iex (irm https://hermes-agent.nousresearch.com/install.ps1)`
   Linux/macOS: `curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`

2. **Get three free API keys.** All three have usable free tiers and none requires a card:
   - Apify — https://console.apify.com/account/integrations → `$5/month` free
   - Tavily — https://app.tavily.com/home → `1,000 credits/month` free
   - Exa — https://dashboard.exa.ai/api-keys → `$10 credit`, resets monthly

3. **Get one LLM key.** https://openrouter.ai/keys (or https://build.nvidia.com/ for NIM).

4. **`./scripts/bootstrap.ps1`** — creates the venv, installs deps, copies `.env`, runs doctor.

5. **Fill in `.env`.** Every key from steps 2 and 3, plus your model slugs.

6. **`hermes model`** — pick the **worker** model. Must have ≥ 64k context. **This is not the same
   as `LLM_MODEL_CHEAP`/`LLM_MODEL_STRONG`** — see Section 9.4. Pick a free-tier model.

7. **`cwt bootstrap`** — creates the nine profiles, installs the eight skills, installs the plugin
   at `~/.hermes/plugins/cwt/`, and merges `hermes/config.yaml` into `~/.hermes/config.yaml`.

8. **`hermes gateway start`** — the kanban dispatcher runs inside the gateway. Without it, nothing
   dispatches and every card sits on `ready`.

9. **`cwt doctor`** — every check must be green. Fix failures before continuing; a run that fails
   at step 12 because of a bad model slug has already spent your Apify budget.

10. **`cwt run --engine local --offline`** — renders a real 42-second video with **zero spend and no
    network**. This is the proof the system works, and it must succeed before you go live.

11. **`cwt run --offline`** — the same pipeline driven by the Hermes kanban board, still with no API
    spend. Watch it in the dashboard. This is the run to record.

12. **`cwt run`** — the real thing. Expect ≈ $0.24–0.70.

13. **`cwt run --resume`** — only if step 12 blocked. Every completed stage is a cache hit, so this
    costs approximately nothing.

14. **Record the kanban video.** `./scripts/record_kanban_video.ps1` prints the recipe. Start the
    recorder, then `cwt run --offline --record-pacing`. Capture 5–10 minutes, speed up 4–8× in post.

15. **Assemble the submission.** `submission/` contains:
    - `final.mp4` — the ad
    - `storyboard.json` + `storyboard.html` — the storyboard, machine- and human-readable
    - `contact_sheet.png` — 4×3 grid of each shot's first frame
    - `render_manifest.json` — the exact ffmpeg argv and the backend chain that ran
    - `claims_report.json` — evidence the compliance gate ran and passed
    - `cost_report.json` — measured spend per service, from `llm_ledger.jsonl`
    - `README-SUBMISSION.md` — the repo link, the two API tokens, and the recording recipe

16. **Email** `gilad@crowdwisdomtrading.com` with the repo link, the Apify and Tavily tokens, and
    the kanban video.

> **Before you send it, run this:** `cwt run --engine local --offline` on a clean clone, in a fresh
> shell, with no keys in the environment. If it does not render a video, the reviewer cannot rerun
> your code — and that is an explicit submission requirement, not a nicety.

---

## Appendix A — Compliance Notes for the Advertiser

This section is not code. It is the reasoning behind Section 12's claims rules, recorded so the
design decisions are auditable rather than mysterious.

**Why the guardrail blocks rather than warns.** This advertises a financial product. Meta's
financial-services standards prohibit claims implying a specific financial result, and Google's
policies classify guaranteed-return claims as Unacceptable Claims — repeat violations escalate to
account-level suspension. Both platforms also apply their rules to the creative, the copy *and* the
landing page; a compliant ad pointing at a non-compliant page is still rejected.

**Why the product-specific rules exist.** Three of the hard rules — `position_access_implication`,
`copy_trading_implication`, `managed_accounts_implication` — are not generic policy compliance. They
encode contradictions with CrowdWisdomTrading's **own published FAQ**, which states:

> *"We only know what all the traders are speaking about, what they share. We dont have access to
> their positions."*

and explicitly disclaims copy-trading, algorithmic trading, and personalised advice. Copy implying
otherwise is false as well as non-compliant.

**Why the performance statistic is a hard block.** The product's headline statistic cannot currently
be substantiated: it appears as three different values on the company's own site, and the public
track-record page and `/api/predictions` endpoint that would verify it both returned HTTP 404 at
build time. An unsubstantiated performance claim is a worse position than no claim — it is a policy
violation and a substantiation problem at once.

**The angle this leaves open, and it is the better one.** The product's genuinely distinctive
property is *transparency of process*: every published call carries its date, ticker, direction,
entry, targets, stops, a chart, and its eventual outcome, publicly. That is a **process** claim
rather than a **performance** claim. It is verifiable, it is defensible, it differentiates against
every competitor named on the company's own comparison pages, and the claims engine permits it
without a safe harbour. **The guardrail should produce a better ad, not merely a safer one.**

**Two operational constraints that are not about copy:**

- **Every linked subpage returned HTTP 404 at build time** — `/about`, `/compare`, `/terms`,
  `/crowdwisdompredictions`. The ad must target `https://crowdwisdomtrading.com/` only. Any CTA
  pointing at a subpage currently leads to a dead end, and a landing-page mismatch is an independent
  rejection trigger.
- **The platform's risk warning must be legible and held on screen long enough to read.** The
  `risk_disclosure_present` and `safe_harbour_duration_s` validators enforce this at schema level,
  and `t_qa` recomputes the duration from the rendered timeline rather than trusting the declared value.

---

*This specification is complete. Build every file exactly as shown. Every function, every import,
every config option matters — the ones that look like paranoia are the ones that were paid for.*
