# CWT Video Ads Agent

A multi-agent autonomous system that produces a 30–60s cinematic vertical video ad (1080x1920) for [crowdwisdomtrading.com](https://crowdwisdomtrading.com/). The system does not make text ads. It makes a film.

From scraping winning competitor ads in Meta Ads Library, extracting hooks and beat structures, and conducting tri-angle market research, to scriptwriting, deterministic financial compliance auditing, and multi-tier video rendering with synthetic voiceover — the entire pipeline is coordinated autonomously by an ensemble of specialized Hermes agents on an interactive Kanban board.

---

## 1. Quickstart — The Offline Path First

Per §13 of the specification: **the `--offline` run must work before you attempt a live one.** It requires **no API keys at all**, consumes zero network bandwidth, costs **$0.00**, and serves as the verifiable proof that the entire rendering and pipeline engine works end-to-end on your machine.

Run these three commands:

```bash
./scripts/bootstrap.ps1          # on Windows (or ./scripts/bootstrap.sh on Linux)
hermes model                     # pick a >=64k-context worker model (see call-out below)
cwt run --engine local --offline # renders a real 48s ad. No keys. No network. $0.00
```

This replays pre-recorded, scrubbed HTTP fixtures and renders a complete 48-second, 1080x1920 MP4 ad with voiceover, motion graphics, and audio mixing into `runs/<run_id>/render/final.mp4`.

---

> [!IMPORTANT]
> ### ⚠ The Hermes worker's model is NOT our model (§9.4)
>
> `LLM_MODEL_CHEAP` and `LLM_MODEL_STRONG` configure **our own** `LLMClient`, used by the `cwt_*` tools for data extraction, storyboard scoring, creative judging, and scriptwriting.
>
> They do **not** configure the Hermes *worker* — the model that reads a kanban card and decides which tool to call. That worker model is set with `hermes model`, lives in `~/.hermes/config.yaml`, and must have **≥ 64k context** (Hermes enforces this requirement).
>
> In `--offline` mode our own LLM calls are bypassed entirely via fixture replay — but Hermes workers still execute on whatever model you configure.
>
> **Before recording the demo, run `hermes model` and pick a free-tier model.**

---

## 2. Live Quickstart

When you are ready to run against live APIs:

1. **Configure Environment:**
   ```bash
   cp .env.example .env
   # Open .env and add your API keys (OpenRouter/NVIDIA, Apify, Tavily, Exa)
   ```
2. **Install Hermes Assets:**
   ```bash
   cwt bootstrap
   ```
   Creates the nine agent profiles, registers skills, and installs the native CWT Hermes plugin.
3. **Start the Kanban Dispatcher:**
   ```bash
   hermes gateway start
   ```
4. **Run Preflight Diagnostics:**
   ```bash
   cwt doctor
   ```
   Ensures all binaries (Python 3.11, FFmpeg, Node, Hermes), API keys, and model slugs are valid.
5. **Run the Live Pipeline:**
   ```bash
   cwt run
   ```
   Expected cost: **≈ $0.24–0.70** per full run (§16.2), with Apify scraping accounting for ~$0.24 and LLM creative calls accounting for the rest.

---

## 3. What It Does — The 11 Pipeline Stages

The pipeline executes as a Directed Acyclic Graph (DAG) across eleven distinct stages:

1. **`root`**: Pipeline anchor card establishing objective and acceptance criteria.
2. **`ads`**: Sources active competitor ads from Meta Ads Library via Apify (30-day window).
3. **`patterns`**: Extracts hook archetypes, emotional pain points, and aggregates median beat sheets.
4. **`res_pain`**: Market research investigating ICP pains and trading frustrations (**runs concurrently**).
5. **`res_unique`**: Research on CrowdWisdomTrading data & populates prohibited facts (**runs concurrently**).
6. **`res_crowd`**: Research on crowd wisdom vs single-expert forecasting + objections (**runs concurrently**).
7. **`brief`**: Assembles the unified creative research brief (selection over summarization).
8. **`script`**: Writes 3 storyboard variants, judges them, splices top beats, and iterates with the Creative Director.
9. **`compliance`**: Automated deterministic claims engine verification + bounded compliance rewrites.
10. **`render`**: Multi-backend video rendering (HyperFrames → OpenMontage → local FFmpeg anchor).
11. **`qa`**: Final audio/video QA (loudness, aspect ratio, duration, and post-render voiceover claims re-check).
12. **`collect`**: Assembles the complete submission bundle into `submission/`.

### Pipeline DAG Topology (§9.1)

```
                                        ┌─▶ res_pain ──┐
root ──▶ ads ──▶ patterns ──────────────┼─▶ res_unique ┼──▶ brief ──▶ script ──▶ compliance ──▶ render ──▶ qa ──▶ collect
                                        └─▶ res_crowd ─┘
                                     (concurrent research)
```

The review and compliance stages use real gates:
```
t_script ──request_review──▶ cwt-creative-director
                                 │ complete = approve
                                 │ request_changes = rewrite (max 3 rounds)
                                 ▼
t_compliance (parent: script) ── deterministic gate + bounded rewrite
                                 ▼
t_render ──▶ t_qa ──▶ collect
```

Notice that the three research cards (`res_pain`, `res_unique`, `res_crowd`) execute **concurrently** — providing an immediate visual payoff on the Kanban board.

---

## 4. Video Generation Behavior: Does It Make a New Video Every Time?

A common question is whether running the agent generates a brand-new video or reuses previous outputs.

### Short Answer
**Yes, a new video file is created on every run**, stored in a newly minted, timestamped directory (`runs/<run_id>/render/final.mp4`) and copied to `submission/final.mp4`. However, **whether the narrative content changes or stays fixed depends on the execution mode**:

| Execution Mode | Example Command | New Video File? | Different Script & Visuals? | Cost |
| :--- | :--- | :--- | :--- | :--- |
| **Offline Replay Mode** | `cwt run --engine local --offline` | **Yes** (renders fresh MP4 to new `runs/<run_id>`) | **No** (seeds deterministic storyboard & script from baseline fixtures) | **$0.00** (Zero API keys, zero network) |
| **Live Autonomous Mode** | `cwt run` (or `cwt run --engine hermes`) | **Yes** (renders fresh MP4 to new `runs/<run_id>`) | **Yes** (generates new 12-hook search, 3 script variants, LLM-judged splice, fresh TTS) | **≈ $0.24–0.70** |
| **Targeted Stage Re-run** | `cwt run --run-id <id> --force-stage render` | **Yes** (re-renders video in targeted run) | **Preserves existing run's script** (skips prior stages via artifact cache) | **$0.00** (LLM cost) |

---

### How the Modes Differ in Detail

1. **Offline Mode (`--offline`)**
   - **Purpose**: Rapid local testing, automated CI/CD validation, and recording kanban demo videos without spending API credits or network bandwidth.
   - **Behavior**: The engine seeds `runs/<run_id>/artifacts/` from `fixtures/artifacts/`. Stages 1–9 (`ads` through `compliance`) are instantly verified as valid replays. Stages 10–12 (`render`, `qa`, `collect`) execute live on your machine, compiling the HTML5/CSS3/GSAP motion graphics composition, synchronizing audio, and rendering a full 48-second 1080×1920 video at 30 fps via Hyperframes.
   - **Consistency**: Every offline run produces the **same verified narrative and beat structure**, rendering it into a unique timestamped run directory every time.

2. **Live Autonomous Mode (Default / Online)**
   - **Purpose**: Full production creative generation.
   - **Behavior**: The agent team executes dynamically across all 11 stages:
     - **Hook Generation**: Generates and scores **12 distinct hook candidates** against historical engagement archetypes.
     - **Variant Scriptwriting**: Produces **3 distinct script angles** (*pain point*, *unique data*, *crowd consensus*).
     - **Creative Splice & Judging**: The Creative Director agent evaluates the variants, splices the highest-converting beats, and rewrites for maximum narrative tension.
     - **Deterministic Compliance**: Scans every claim against prohibited financial advice rules, requiring verifiable process claims and mandatory risk disclosures.
     - **Audio & Render**: Generates fresh synthetic TTS voiceover narration, matches millisecond word timestamps, and renders dynamic motion graphics tailored to that script.
   - **Variety**: Every live run produces a **completely original ad concept, script, and visual layout**.

3. **Resuming or Re-rendering an Existing Run (`--run-id` and `--force-stage`)**
   - If you wish to re-render or adjust the video styling of an existing run without re-paying for LLM steps:
     ```powershell
     cwt run --run-id 20260929-1331-8eff --force-stage render
     ```
   - Thanks to the `ArtifactStore.get_if_valid` stage cache, all earlier stages are replayed at **$0.00**, and only the video render stage is re-executed.

4. **Zero-Overlap Voiceover Synchronization Architecture**
   - **Millisecond Audio Alignment**: Visual scene cuts (`s01` through `s12`) are synchronized beat-by-beat to spoken word timestamps extracted directly from `voiceover.json`.
   - **Sequential Clip Lifecycle**: Clips mount sequentially with strictly non-overlapping `data-start` and `data-duration` attributes, eliminating visual ghosting and bleed-through.
   - **Inner Content Animations**: GSAP transitions operate on isolated inner `#content_{sid}` containers, preserving native browser compositor performance and complying with Hyperframes static guard rules.

---

## 5. The Agent Team (§7.3)

Nine specialized agent personas collaborate on the board:

| Profile | Role | Model Tier | Tools & Capabilities |
|---|---|---|---|
| `cwt-orchestrator` | Decomposes goal into cards; owns no artifacts | cheap | Full kanban toolset |
| `cwt-ads-manager` | Sources winning ads; assembles final submission bundle | cheap | Apify ads tools, bundle assembler |
| `cwt-hook-analyst` | Extracts hooks, pains, concepts, beat sheets | cheap | Ad pattern & beat extraction |
| `cwt-researcher` | Runs the 3 research angles; builds the brief | cheap | Tavily & Exa research tools |
| `cwt-script-writer` | Writes variants, hooks, cinematic shot scripts | **strong** | Storyboard creation & splicing |
| `cwt-creative-director` | Scores storyboards against rubric; approves/rejects | cheap | Creative review tools |
| `cwt-compliance` | Evaluates claims and enforces advertising policies | cheap | Claims checker & rewrite tools |
| `cwt-video-editor` | Renders video through the backend chain | cheap | TTS & video composition tools |
| `cwt-qa` | Verifies duration, loudness, aspect ratio, post-claims | cheap | Probe & post-render claims tools |

> **Why only `cwt-script-writer` runs on the strong tier:** Approximately 35 of 40 LLM calls per run are classification, scoring, or extraction tasks where mid-tier models excel at 1/10th the cost. High-capacity frontier models are reserved strictly for creative narrative scriptwriting (Rule A5).

---

## 6. Why the Compliance Gate is Deterministic First (Appendix A)

Advertising financial services requires strict compliance. Meta and Google prohibit deceptive financial promises, guaranteed-return claims, and unsubstantiated performance statistics. Violations trigger account suspensions.

The compliance engine operates deterministically before any creative judgment:
1. **Direct FAQ Encoding**: Three hard rules (`position_access_implication`, `copy_trading_implication`, `managed_accounts_implication`) directly encode CrowdWisdomTrading's published FAQ:
   > *"We only know what all the traders are speaking about, what they share. We dont have access to their positions."*
   Any script implying copy-trading, account access, or automated management is hard-blocked.
2. **Performance Claim Blocking**: Unsubstantiated win rates or accuracy figures are hard-blocked. The public track-record endpoint was offline at build time; an unsubstantiated performance claim is a fatal regulatory vulnerability.
3. **The Better Angle**: The product's genuine differentiator is **transparency of process** — every trade call publishes entry, target, stop loss, rationale, and chart publicly. A process claim is verifiable, defensible, and distinctive:
   > *"The guardrail should produce a better ad, not merely a safer one."*
4. **Mandatory Risk Disclosure**: The on-screen risk disclaimer is verified for minimum display duration (≥ 3.0s) and legibility in both pre-render storyboard analysis and post-render QA.

---

## 7. Recording the Kanban Video

Do not manually configure screen recording. We provide a helper script:

```powershell
.\scripts\record_kanban_video.ps1
```

1. Run the script to print the verified recording recipe and settings.
2. Start your screen recorder focused on the Hermes dashboard at `http://localhost:8080`.
3. Launch the run with pacing enabled:
   ```powershell
   cwt run --offline --record-pacing
   ```
   `--record-pacing` inserts deliberate pauses between stage transitions so card movements, agent assignments, and progress indicators are clearly visible on video.
4. Record for 5–10 minutes, then speed up 4–8× in post-production.

---

## 8. Deployment

### 7.1 Windows (Primary Development Host)

```powershell
# 1. Clone repository
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

### 7.2 Linux / VPS / Docker

```bash
# 1. Clone repository
git clone https://github.com/<you>/cwt-video-ads-agent.git
cd cwt-video-ads-agent

# 2. ffmpeg + node (node only for optional HyperFrames backend)
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

---

## 9. What Success Looks Like (§13.3)

### Preflight Diagnostics (`cwt doctor`)

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

### Offline Execution (`cwt run --engine local --offline`)

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

Verified output: `48.00s`, `1080x1920`, `H.264 / AAC`, valid stream format.

---

## 10. Troubleshooting (§14 Gotchas)

| Symptom | Root Cause | Resolution |
|---|---|---|
| Apify returns HTTP 404 | Actor ID used a slash (`apify/facebook-ads-scraper`) | Use a tilde: `apify~facebook-ads-scraper`. |
| Exa returns HTTP 400 | Legacy API params (`type: neural` or bad category) | Current enums: `instant\|fast\|auto\|deep-lite\|deep\|deep-reasoning`. Date filters require category `news`. |
| Worker never starts; card stuck on `ready` | Assignee name does not match Hermes profile name | Run `cwt bootstrap` to generate exact profiles matching `dag.py`. |
| Board never advances | Hermes gateway is not running | Run `hermes gateway start`. The kanban dispatcher lives inside the gateway. |
| `[WinError 193] %1 is not a valid Win32 application` | Calling bare `hermes` or `npx` command on Windows | Resolved via `shutil.which()` to `.cmd` shim in `util/subproc.py`. |
| Render finishes but output file is 0 bytes | Trusted exit code 0 when FFmpeg dropped final frame | Pipeline validates with `ffprobe` and requires non-zero size before marking stage done. |
| Blank dashboard | Bound to `0.0.0.0` or gateway down | Never bind to `0.0.0.0`. Bind to `localhost` only. |

---

## 11. Security & Secret Isolation

- **Dashboard Security (Non-negotiable §11.1)**: The dashboard plugin routes are unauthenticated by design. **Never run `hermes dashboard --host 0.0.0.0`. Bind strictly to localhost.**
- **Public Repository (Rule R1)**: This repository is public and contains no credentials or secret tokens.
- **Submission Credentials**: The brief-mandated Apify and Tavily tokens reside exclusively in `submission/README-SUBMISSION.md`. The entire `submission/` directory is strictly gitignored so tokens are never committed. Reviewers receive the tokens and submission package via email.

---

## 12. Licenses

- **Repository**: [MIT License](LICENSE)
- **Third-Party Notices**: See [NOTICE](NOTICE) for license details of Hermes Agent, HyperFrames, FFmpeg, edge-tts, Piper, and the AGPL-3.0 process isolation architecture for OpenMontage.
