"""Scriptwriting, storyboard generation, variant judging, and rewriting prompts."""

from __future__ import annotations

from ._format import safe_format

PROMPT_VERSION = "1.0.0"

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
