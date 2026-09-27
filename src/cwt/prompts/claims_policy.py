"""Financial advertising claims policy and judge prompt templates."""

from __future__ import annotations

from ._format import safe_format

PROMPT_VERSION = "1.0.0"

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
