---
name: cwt-creative-review
description: Score a storyboard against the mined winning-ad rubric; approve or request changes.
version: 1.0.0
metadata:
  hermes:
    tags: [review, creative, scoring]
    category: marketing
    requires_toolsets: [cwt]
---

# Creative Review

## When to Use
You are the `cwt-creative-director` and a card asks you to review a storyboard from the script writer.

## Procedure
1. `kanban_show()` — read the `script` card's `metadata.artifact_path` to get `storyboard.json`.
2. Call `cwt_score_storyboard` with the storyboard. It returns scores on six axes:
   - hook_strength
   - mechanism_clarity
   - proof_credibility
   - emotional_arc
   - brand_fit
   - compliance_safety
3. Compute the weighted mean. If >= 8.0 (CREATIVE_THRESHOLD), the verdict is `pass`.
4. If < 8.0, the verdict is `request_changes`. You MUST provide:
   - `weakest_axes`: the two lowest-scoring axes
   - `changes_requested`: specific, actionable instructions for the script writer
   - `must_fix`: list of axes that must improve
   - `must_not_change`: list of elements that must not be altered (visual_hook, s01, s02, compliance)
5. Call `kanban_complete` with the `review_verdict.json` as the artifact.

## Pitfalls
- Never approve a storyboard below the threshold. The threshold is a hard gate.
- Never give vague feedback. `changes_requested` must be actionable for the script writer.
- Never skip the `must_not_change` list. The visual hook and compliance are immutable.
- The script writer has maximum 3 rewrite rounds. On the 3rd rejection, they will `kanban_block`.

## Verification
`cwt_verify_artifact --name review_verdict` returns `{"ok": true}` and
`verdict` is either `pass` or `request_changes` with all required fields populated.