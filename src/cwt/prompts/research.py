"""Market research and brief assembly prompt templates."""

from __future__ import annotations

from ._format import safe_format

PROMPT_VERSION = "1.0.0"

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
