"""Ad extraction and beat sheet decomposition prompts."""

from __future__ import annotations

from ._format import safe_format

PROMPT_VERSION = "1.0.0"

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
