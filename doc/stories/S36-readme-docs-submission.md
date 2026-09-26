# S36 — README, docs & submission

**Phase** 8 · **Depends on** S35 · **Blocks** —
**Spec** `doc/video-ads-agent.md` lines **5003–5100** (§13 deployment), **5399–5458** (§18 setup), **64–65** (README), **66** (NOTICE), **5462–5505** (Appendix A), **3967–3984** (§9.4)
**Context budget** ~15k (spec 4k + story 1.9k + output 8k) — mostly writing, little code
**Produces** `README.md`, `NOTICE`, `LICENSE`, `doc/stories/PROGRESS.md` (final), `submission/README-SUBMISSION.md`

---

## Goal

Make the project legible to the person receiving it. The brief's deliverables are a repo link, two API
tokens, and a kanban recording — and the README is what makes the first of those worth opening.

## The deliverables the brief demands (spec lines 50–52)

> - A link to your GitHub/GitLab repository.
> - APIFY + TAVILY tokens used — **must!** so we can rerun your code! without burning our paid accounts
> - A video output of the hermes kanban

## Rules that bind this story

- **§9.4 — the single most likely reviewer confusion.** `LLM_MODEL_CHEAP` / `LLM_MODEL_STRONG` configure
  **our own** `LLMClient`, used by the `cwt_*` tools. They do **not** configure the Hermes *worker* —
  the model that reads a card and decides which tool to call. That is set with `hermes model`, lives in
  `~/.hermes/config.yaml`, and must have **≥ 64k context**. **This must be stated explicitly in the
  README**, with the call-out: *"Before recording the demo, run `hermes model` and pick a free-tier model."*
- **§11.1 line 4349 — dashboard security, non-negotiable.** *"The dashboard's plugin routes are
  unauthenticated by design. Never run `hermes dashboard --host 0.0.0.0`. Bind to localhost."*
- **Rule R1** — the README ships publicly. **No tokens in it.** The two brief-mandated tokens live only
  in `submission/README-SUBMISSION.md` (S26), and `submission/` is gitignored.
- **§13 line 5099** — *"The `--offline` run must work before you attempt a live one."* The quickstart
  order is: offline local → offline hermes → live. Do not reorder it to lead with `cwt run`.
- **Rule C2 / Appendix A** — the compliance posture is a *feature* to explain, not hide. Appendix A
  says it plainly: *"The guardrail should produce a better ad, not merely a safer one."*

## Build steps

1. **`README.md`** — the order that matters:

   1. **One paragraph** — what it is: a multi-agent system that autonomously produces a 30–60s
      cinematic video ad for crowdwisdomtrading.com. *"The system does not make text ads. It makes a
      film."*
   2. **Quickstart — the offline path first**, three commands:
      ```
      ./scripts/bootstrap.ps1          # or bootstrap.sh
      hermes model                     # pick a >=64k-context model (see the call-out below)
      cwt run --engine local --offline # renders a real 42s ad. No keys. No network. $0.00
      ```
      State that this needs **no API keys at all**, and that it is the proof the system works.
   3. **⚠ The two-models call-out** (§9.4) — a boxed block, not a footnote. This is the thing a
      reviewer will otherwise get wrong.
   4. **The live quickstart** — `.env`, `cwt bootstrap`, `hermes gateway start`, `cwt doctor`,
      `cwt run`. Note the expected cost: **≈ $0.24–0.70** (§16.2).
   5. **What it does** — the pipeline in eleven stages, one line each, with the DAG diagram from §9.1.
      Name the three research cards running **concurrently** — that is the visual payoff.
   6. **The agent team** — the nine profiles table from §7.3, with the tier column, and the note that
      only `cwt-script-writer` runs strong.
   7. **Why the compliance gate is deterministic first** — a short section from Appendix A. The
      product's own FAQ says it does not see positions; three hard rules encode that. And the
      *"better ad, not merely a safer one"* argument: transparency of process is a **process** claim,
      which is verifiable and defensible, where a performance claim is neither.
   8. **Recording the kanban video** — point at `scripts/record_kanban_video.ps1` rather than
      duplicating the checklist (one source of truth).
   9. **Deployment** — the Windows and Linux sequences from §13.1/§13.2, verbatim.
   10. **What success looks like** — the expected `cwt doctor` and `cwt run` output from §13.3, with
       the actual output from S35's verification.
   11. **Troubleshooting** — the §14 gotchas table, trimmed to the ones a reviewer will hit:
       Apify 404 (tilde), Exa 400 (legacy enum), a card stuck on `ready` (assignee ≠ profile name),
       the board not advancing (gateway not running), `WinError 193` (`.cmd` shim), a 0-byte render
       (trusted ffmpeg's exit code), and a blank dashboard (bound to `0.0.0.0`).
   12. **Security note** — localhost-only dashboard; the repo is public; `submission/` carries the two
       tokens and is gitignored.
   13. **Licences** — MIT for this repo; third-party notes point at `NOTICE`.
2. **`NOTICE`** — the third-party licences and, specifically, the **AGPL-3.0 reasoning for the
   OpenMontage adapter**:
   - we invoke OpenMontage as an **external process** and consume only its output file
   - we **never vendor its source**
   - the backend is **best-effort and documented as such**; the chain never depends on it
   - the adapter boundary is `video/openmontage.py` and nothing else
   Also list: HyperFrames (Apache-2.0), `imageio-ffmpeg` (BSD), edge-tts (LGPL), Piper (MIT),
   Hermes Agent (MIT).
3. **`LICENSE`** — MIT.
4. **`doc/stories/PROGRESS.md`** — the final state: every story with its status, the commit that
   completed it, and the verification command that was run. This is the build's audit trail and it
   makes the story breakdown reusable. Include S35's clean-clone output verbatim.
5. **`submission/README-SUBMISSION.md`** — verify S26's generator produced it, and that it contains
   exactly: the repo link, the **Apify and Tavily tokens**, the command that produced the bundle, and
   the five-step rerun block. **Confirm `submission/` is in `.gitignore`** before this file exists
   anywhere near a commit.

## Decisions the spec leaves open

- **The README leads with the offline path.** §18's order is offline (step 10) → hermes-offline (11) →
  live (12). A README that leads with `cwt run` invites the reviewer to spend money before seeing the
  system work. Leading with `--engine local --offline` also front-loads the strongest fact: **it needs
  no keys at all.**
- **`PROGRESS.md` is the reusable artifact.** The story breakdown in `doc/stories/` is worth more if it
  records what actually happened. Keep it short — status, commit, verification.
- **Do not restate the gotchas table in full.** Fourteen rows of internal detail dilutes the four that
  a reviewer will actually hit. Point at §14 of the spec for the rest.
- **The two-token file is deliberately outside the repo.** The brief asks for the tokens; the repo is
  public. `submission/` is the seam. State this explicitly in the README's security section so a
  reviewer understands the tokens are coming by email, not missing.

## Done when

```bash
# the repo is publishable
grep -rniE 'apify_api_|sk-or-v1-|nvapi-|tvly-|exa_api' README.md NOTICE LICENSE && echo "LEAK" || echo "no secrets in the public docs"
git check-ignore submission/README-SUBMISSION.md && echo "submission/ is ignored — tokens cannot be committed"

# the docs are accurate
grep -c 'hermes model' README.md          # the >=64k worker-model call-out is present
grep -c '0.0.0.0' README.md               # only in the "never do this" warning
.venv/Scripts/python -c "
import re, pathlib
r = pathlib.Path('README.md').read_text(encoding='utf-8')
for token in ('--engine local --offline', 'hermes model', '64k', 'cwt doctor', 'localhost'):
    assert token in r, token
print('README covers the reviewer-critical facts')"

# the deliverable set
ls submission/    # final.mp4 storyboard.json storyboard.html contact_sheet.png
                  # render_manifest.json claims_report.json cost_report.json README-SUBMISSION.md
```

## Handoff

None — this is the last story. The deliverable is the repository plus the three items the brief asks
for, emailed to `gilad@crowdwisdomtrading.com`:

1. the repo link
2. the Apify and Tavily tokens (from `submission/README-SUBMISSION.md`, **not** from the repo)
3. the kanban recording, produced with `scripts/record_kanban_video.ps1` while running
   `cwt run --offline --record-pacing`
