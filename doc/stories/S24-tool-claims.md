# S24 — Tool surface — claims

**Phase** 5 · **Depends on** S06, S22 · **Blocks** S25 (post-render QA)
**Spec** `doc/video-ads-agent.md` lines **826–860** (`claims_report.json`), **1574–1657** (§6.6), **3491–3509** (the `compliance` card), **4717–4744** (Rules C2, C3)
**Context budget** ~15k (spec 3.5k + story 1.7k + output 8k)
**Produces** `tools/claims.py`, `tests/test_tools_claims.py`

---

## Goal

Two tools that run the claims gate **twice** — once over the storyboard, once over the rendered
transcript — with a deterministic engine that runs first and an LLM judge that covers only the residue.

> **Rule C3.** *"Check the storyboard, render, ship"* is wrong. TTS normalisation changes what is
> actually said, and a line added during render bypasses the gate entirely. The gate runs twice.

## Interface contract — FROZEN

```python
# tools/claims.py

def check_claims(*, settings: Settings, paths: RunPaths, stage: str,
                 transcript: str | None = None) -> dict:
    """stage: "pre_render" | "post_render".
    pre_render reads artifacts/storyboard.json; post_render scans the supplied transcript.
    WRITES artifacts/claims_report.json
    RETURNS {"verdict":"pass"|"request_changes"|"block","hard_count":int,"soft_count":int,
             "rewrite_instructions":[...],"rounds_used":int,"rounds_remaining":int}"""

def rewrite_for_compliance(*, settings: Settings, paths: RunPaths,
                           round_no: int | None = None) -> dict:
    """Apply the deterministic fixes. Bounded by CLAIMS_MAX_REWRITE_ROUNDS.
    REWRITES artifacts/storyboard.json in place; increments generation.claims_rewrite_rounds
    RETURNS {"artifact_path","rounds_used","fixes_applied":[...],"remaining_findings":[...]}"""

# ── internals ──
def _collect_script_text(sb: Storyboard) -> dict[str, list[tuple[str, str]]]:
    """field -> [(shot_id, text)] for every SPOKEN and ON-SCREEN string.
    Deliberately EXCLUDES shot.description and camera fields — those are not claims."""

def _verdict(hard: int, soft: int, rounds_used: int, max_rounds: int) -> str: ...
```

## Rules that bind this story

- **Rule C1 — deterministic first, and it wins.** `detect_claims` (S06) runs **before** the LLM.
  Its `HARD` verdicts are **final**.
- **Rule C2 — a hard finding is never overridable.** No config flag, no `force`, no `override`
  parameter. After `CLAIMS_MAX_REWRITE_ROUNDS` with a HARD finding standing, the card **blocks**.
  Spec line 3506: *"It must NEVER silently pass. A blocked card with a clear reason is a better outcome
  than a shipped unsubstantiated claim."*
- **Rule C3 — two passes**, and the post-render pass scans the **transcript**, not the storyboard.
- **§6.6 line 1621** — the LLM judge handles only what regex cannot: implied guarantees, juxtaposition,
  misleading framing, unsubstantiated superlatives, missing disclosures, substitution defeats.
  **It may escalate a severity. It may never de-escalate a `hard` finding.**
- **§10.2 docstring rule** — *"do not call this on the storyboard's shot descriptions. It scans the
  SPOKEN and ON-SCREEN text only."* `_collect_script_text` enforces this by construction.

## Build steps

1. `_collect_script_text` — return `{field: [(shot_id, text)]}` over:
   - `voiceover.full_text` → `[("", text)]`
   - `voiceover.segments[].text` → `[(seg.shot_id, seg.text)]`
   - `shots[].on_screen_text[].text` → `[(shot.id, t.text)]`
   - `visual_hook.text_overlay` → `[("visual_hook", text)]`
   **Not** `shots[].description`, **not** `camera.*`, **not** `why_it_stops_the_scroll`. A camera move
   is not a claim, and a finding on one is a false positive that wastes a rewrite round.
2. `check_claims`:
   - **pre_render**: read the storyboard, collect the script text, run `detect_claims` on each string,
     attach `location: {shot_id, field}` to every finding (S06 deliberately leaves `location` to the
     caller — B4), then run `scan_prohibited_facts` against `research_brief.prohibited_facts`.
     **B4: wiring `scan_prohibited_facts` is mandatory** — §3.3 calls `prohibited_facts` a hard gate and
     validator 10 enforces it, but no tool in the spec ever calls the function.
   - **post_render**: take `transcript` (from `VoiceoverResult.transcript`, S12) and run the same
     deterministic pass over it. Also re-run `scan_prohibited_facts`.
   - then run the LLM judge: `build_claims_judge_prompt(script)` on CHEAP, passing the concatenated
     script text. Parse findings; **drop any `hard` claim the judge marks as de-escalated** (Rule C1)
     and count escalations of `soft` → `hard`.
   - verdict: `"block"` if any HARD finding and `rounds_used >= CLAIMS_MAX_REWRITE_ROUNDS`;
     `"request_changes"` if any finding remains; else `"pass"`
   - merge with any **existing** report for the other stage — the final artifact must show that
     **both** passes ran. `claims_report.stage` is singular, so keep one file per stage:
     `artifacts/claims_report.json` (the latest) plus `artifacts/claims_report_<stage>.json`.
     The `collect` card requires a `pass` verdict on **both** (spec line 3398).
3. `rewrite_for_compliance`:
   - build the rewrite instruction list from `rewrite_instructions(findings)` (S06) — these are
     **specific instructions, not creative work** (spec line 3501)
   - apply them via the STRONG rewrite path from S23, passing the fixes as `changes_requested` and
     `must_not_change=["visual_hook","compliance"]`
   - re-run `check_claims(stage="pre_render")` and record `rounds_used`
   - if a HARD finding survives the max rounds, **return `verdict: "block"` and let the card block.**
     Do not attempt a fourth round, and do not soften the finding.
4. Docstrings — `check_claims` is the spec's own worked example (lines 4087–4111). **Copy it
   verbatim**, then extend the INPUTS block with the `stage` semantics above.
   `rewrite_for_compliance`:
   > *CALL THIS: only when `cwt_check_claims` returned `request_changes` with hard findings. The fixes
   > are mechanical — apply them and re-check.*
   >
   > *WHEN NOT TO CALL: never to "make a blocker go away". After `CLAIMS_MAX_REWRITE_ROUNDS`, block
   > the card with the finding text. A blocked card is the correct outcome.*
5. Tests (mocked LLM):
   - `_collect_script_text` **excludes** `shot.description` — construct a storyboard with a prohibited
     phrase only in a description and assert zero findings
   - a `hard` regex finding is never removed by an LLM judge that marks it `soft` (Rule C1)
   - the judge's `soft` → `hard` escalation is honoured
   - a prohibited fact from the brief is caught in the post-render transcript even when absent from
     the storyboard (the TTS-normalisation scenario Rule C3 exists for)
   - `rounds_remaining` decrements; at max rounds with a HARD finding the verdict is `"block"`
   - both stage files exist and both parse after two calls

## Decisions the spec leaves open

- **Two report files, one contract.** `ClaimsReport.stage` is singular but the pipeline needs two
  passes recorded (spec line 3498: *"verdict == 'pass' on BOTH the pre-render and post-render passes"*).
  **Decision: one file per stage** (`claims_report_pre_render.json`,
  `claims_report_post_render.json`) plus a `claims_report.json` that mirrors the latest. Add a
  `stage_reports: dict[str, str]` field to `ClaimsReport` as an optional addition (append-only per
  §3.0 rule 2) so the final artifact names both.
- **Non-empty `matched_text` for the judge's findings.** The judge may return an empty span. Reject
  it in the repair loop — an empty `matched_text` cannot be actioned.
- **`rounds_used` is per-stage.** A pre-render round must not consume the post-render budget, or a
  late fix could exhaust the allowance before the gate that actually matters.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_tools_claims.py -q -v     # green, mocked LLM

.venv/Scripts/python -c "
import json, pathlib
from cwt.domain.models import ClaimsReport
for st in ('pre_render','post_render'):
    r = ClaimsReport.model_validate(json.loads(pathlib.Path(f'runs/_s24/artifacts/claims_report_{st}.json').read_text()))
    print(st, r.verdict, 'hard:', len(r.deterministic['findings']), 'llm:', len(r.llm_judge['findings']))
    assert r.rounds_remaining >= 0"

# Rule C1 negative proof
.venv/Scripts/python -c "
# monkeypatch judge to return the hard finding as severity='soft' -> it must still be hard"
"
```

## Handoff

S25 calls `check_claims(stage="post_render", transcript=...)` during QA. The `compliance` card (S27)
owns the pre-render pass and **overwrites `artifacts/storyboard.json` in place** when it rewrites — so
S23's `storyboard.html` and S26's submission bundle must be regenerated afterwards, never cached.
