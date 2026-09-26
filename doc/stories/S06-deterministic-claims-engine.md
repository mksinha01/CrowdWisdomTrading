# S06 — Deterministic claims engine

**Phase** 1 · **Depends on** S04 · **Blocks** S24 (claims tool), S04 validator 10
**Spec** `doc/video-ads-agent.md` lines **2621–2810** (§8.6), **1574–1657** (§6.6 policy prompt), **5462–5505** (Appendix A)
**Context budget** ~15k (spec 4.5k + story 1.5k + output 8k)
**Produces** `domain/claims.py`, `tests/test_claims.py`

---

## Goal

A pure, deterministic regex engine that hard-blocks prohibited financial-advertising claims. It runs
**before** any LLM is consulted, and its `HARD` verdicts are **final** — the LLM judge (S24) may
escalate a severity but may never de-escalate one of these.

This is the compliance control. Appendix A is explicit that a HARD finding has no override path: a
blocked card in the demo recording is a better outcome than a shipped unsubstantiated performance
claim on a financial product.

## Interface contract — FROZEN

```python
# domain/claims.py
RULESET_VERSION = "1.0.0"

class Severity(StrEnum):
    HARD = "hard"      # unconditional block; forces rewrite; no override
    SOFT = "soft"      # requires a disclosure or a rewrite; LLM judge adjudicates

@dataclass(frozen=True)
class ClaimRule:
    id: str; severity: Severity; pattern: re.Pattern; why: str; fix: str

@dataclass(frozen=True)
class Finding:
    rule_id: str; severity: Severity; matched_text: str; why: str; fix: str

CLAIM_RULES: tuple[ClaimRule, ...] = (...)          # 12 HARD + 3 SOFT = 15 rules

def detect_claims(text: str) -> list[Finding]: ...
def has_hard_block(findings: list[Finding]) -> bool: ...
def rewrite_instructions(findings: list[Finding]) -> list[str]: ...   # dedup'd, hard-first
def scan_prohibited_facts(text: str, prohibited: list[dict]) -> list[Finding]: ...
```

**The 15 rules, verbatim from spec lines 2669–2749:**

HARD (12): `guaranteed_returns`, `risk_free_language`, `specific_profit_figure`,
`percentage_return_promise`, `win_rate_statistic`, `double_your_money`, `beat_the_market`,
`financial_freedom`, `payout_imagery`, `position_access_implication`*, `copy_trading_implication`*,
`managed_accounts_implication`*
SOFT (3): `implied_certainty`, `unsubstantiated_superlative`, `testimonial_earnings`

`*` = **PRODUCT_DISCLAIMER** — these encode contradictions with CrowdWisdomTrading's *own published
FAQ* ("We dont have access to their positions"). Copy implying otherwise is false as well as
non-compliant. Keep the `PRODUCT_DISCLAIMER` comment marker so the distinction survives.

## Rules that bind this story

- **Rule C1** — the engine runs first and its HARD verdicts are final. `detect_claims` has no
  `severity_filter` parameter. Do not add one.
- **Rule C2** — no override path exists. Do not add a `skip_rules` argument, a config bypass, or a
  "force" flag. Someone will set it at 2am to make a demo work.
- **§8.6 line 2626** — PURE. No I/O, no network, no `Settings`, no logging side effects.
- **`fix` strings are written for a model to consume.** The rewrite prompt (S13) takes them verbatim.
  `"remove this"` is not a fix; `"Remove the figure. Describe the process instead."` is.

## Build steps

1. Transcribe `CLAIM_RULES` from spec lines 2669–2749 **exactly**. Copy every regex, every `why`,
   every `fix` character-for-character. These were tuned against real policy text and re-typing them
   from memory will silently weaken the gate.
2. `detect_claims` — iterate rules, `finditer`, build `Finding`s. Strip `matched_text`.
3. `rewrite_instructions` — sort HARD before SOFT (stable within group), dedupe on `fix`, preserve
   first-seen order inside each group.
4. `scan_prohibited_facts` — two passes per entry, exactly as spec lines 2790–2809:
   - exact case-insensitive substring → `prohibited_fact`
   - every number in the fact → a `\b<stem>(\.\d+)?\s?(%|percent)` variant scan → `prohibited_fact_numeric_variant`
   The second pass is the paraphrase resistance. It is why `74.1%`, `74%` and `about 74 percent` are
   all caught by a rule written for `74.1% of tracked directions hit`.
5. Tests — **exhaustive, one positive and one negative per rule** (spec line 181: *"The guardrail is
   a control, so it is proven"*).
   - 15 positive cases, each triggering exactly its intended `rule_id`
   - A **clean-script** case: the §3.4 voiceover text must produce **zero HARD findings**. That
     script is the reference compliant ad; if it trips a rule, the rule is over-broad.
   - Numeric-variant cases: `74.1%`, `74%`, `about seventy-four percent`, `73%`
   - Case-insensitivity: `GUARANTEED RETURNS`, `Risk-Free`
   - Severity-isolation: regex crossing sentence boundaries must not produce a false HARD
6. Assert `len(CLAIM_RULES) == 15` and the HARD/SOFT split is 12/3.

## Decisions the spec leaves open

- **`Finding.location`.** §3.5's `claims_report` example has a `location: {shot_id, field}`, but
  `detect_claims` returns `Finding` without it (spec line 2660–2666). **Keep `Finding` as specified** —
  the *caller* (S24) attaches `location` when it knows which shot the text came from. Do not add an
  optional field here.
- **Overlapping matches.** A sentence can trip two rules. Report both; `rewrite_instructions` dedupes
  the fixes. Do not attempt to rank or suppress.
- **The clean-script test is the most valuable test in the repo.** If the §3.4 voiceover trips a rule,
  fix the *rule*, not the fixture — the fixture is the ad the system is designed to produce.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_claims.py -q -v
# -> every rule has >=1 positive and >=1 negative case; all green

.venv/Scripts/python -c "
from cwt.domain.claims import *
print(len(CLAIM_RULES), sum(r.severity=='hard' for r in CLAIM_RULES))     # 15 12
print(has_hard_block(detect_claims('We guarantee returns of 15% monthly.')))  # True
print(detect_claims('Every call is published with its outcome.'))             # []
f = scan_prohibited_facts('about 74 percent of directions hit', [{'fact':'74.1% of tracked directions hit','reason':'r','rule':'Remove.'}])
print([x.rule_id for x in f])   # ['prohibited_fact_numeric_variant']"
```

## Handoff

S04's validator 10 imports `scan_prohibited_facts`. S24 (`cwt_check_claims`) calls `detect_claims`,
`has_hard_block` and `rewrite_instructions`, and attaches `location` per finding. S13's
`CLAIMS_JUDGE_PROMPT` is the *second* layer and must be told the engine already ran — it adjudicates
the residue only, and may escalate but never de-escalate.
