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