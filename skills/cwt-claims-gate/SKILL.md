---
name: cwt-claims-gate
description: Run the deterministic claims policy check + bounded LLM rewrite on a storyboard.
version: 1.0.0
metadata:
  hermes:
    tags: [compliance, claims, legal]
    category: marketing
    requires_toolsets: [cwt]
---

# Claims Gate

## When to Use
You are the `cwt-compliance` and a card asks you to run the claims gate on the storyboard.

## Procedure
1. `kanban_show()` — read the `script` card's `metadata.artifact_path` to get `storyboard.json`.
2. Call `cwt_check_claims` with the storyboard. It runs:
   - Deterministic rule engine (pure Python, no LLM) — hard-blocks prohibited claims
   - LLM judge (cheap tier) — catches soft violations (implied guarantees, etc.)
3. The tool returns a `claims_report.json` with verdict: `pass`, `request_changes`, or `block`.
4. If verdict is `pass`:
   - Call `kanban_complete` with `metadata.artifact_path = claims_report.json`.
5. If verdict is `request_changes` with HARD findings:
   - Call `cwt_rewrite_for_compliance` with the findings. It returns specific fix instructions.
   - Apply the fixes to the storyboard (overwrite `artifacts/storyboard.json` in place).
   - Record the round in `generation.claims_rewrite_rounds`.
   - Re-run `cwt_check_claims` on the corrected storyboard.
   - Repeat up to `CLAIMS_MAX_REWRITE_ROUNDS` (default 3).
   - If a HARD finding still stands after max rounds, call `kanban_block` with the finding text.
6. If verdict is `block` (unfixable):
   - Call `kanban_block` with the finding text.

## Pitfalls
- NEVER `kanban_complete` a card with a standing HARD finding. It must be fixed or blocked.
- The deterministic engine is the authority. Do not argue with it.
- `prohibited_facts` from the research brief are injected verbatim into the check.
  Any numeric variant (73%, 73.8%, 74.1%) counts as a match.
- TTS normalisation changes what is actually said — the QA card re-checks the RENDERED transcript.

## Verification
`cwt_verify_artifact --name claims_report` returns `{"ok": true}` and
`verdict == "pass"` on both deterministic and LLM judge checks.