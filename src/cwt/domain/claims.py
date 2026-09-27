"""Deterministic financial-advertising claims engine.

PURE. No I/O. No LLM. Exhaustively unit-tested in tests/test_claims.py.

WHY DETERMINISTIC FIRST: an LLM judge that can be argued out of a "guaranteed" is
not a compliance control. The hard rules below are FINAL — the LLM judge that runs
afterwards may escalate a severity but may never de-escalate a hard finding.

WHY THE RULESET IS PRODUCT-SPECIFIC: the three rules marked PRODUCT_DISCLAIMER
encode contradictions with CrowdWisdomTrading's OWN published FAQ. Copy implying
position access, copy-trading or managed accounts is not merely non-compliant, it
is false, because the product says so itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

RULESET_VERSION = "1.0.0"


class Severity(StrEnum):
    HARD = "hard"      # unconditional block; forces a rewrite; no override
    SOFT = "soft"      # requires a disclosure or a rewrite; LLM judge adjudicates


@dataclass(frozen=True)
class ClaimRule:
    id: str
    severity: Severity
    pattern: re.Pattern[str]
    why: str
    fix: str


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    matched_text: str
    why: str
    fix: str


CLAIM_RULES: tuple[ClaimRule, ...] = (
    ClaimRule(
        "guaranteed_returns",
        Severity.HARD,
        re.compile(
            r"\b(guarantee[ds]?|guaranty)\b.{0,40}\b(return|profit|gain|win|income|money|result)\b",
            re.I,
        ),
        (
            "Absolute-return promise. Prohibited by Meta and Google financial-services policy "
            "and unsubstantiable."
        ),
        "Replace the outcome promise with a described process.",
    ),
    ClaimRule(
        "risk_free_language",
        Severity.HARD,
        re.compile(
            r"\b(risk[-\s]?free|riskless|no\s+risk|zero\s+risk|can'?t\s+lose|cannot\s+lose|never\s+lose)\b",
            re.I,
        ),
        "Eliminates the possibility of loss. False as a matter of fact.",
        "State that trading involves risk of loss.",
    ),
    ClaimRule(
        "specific_profit_figure",
        Severity.HARD,
        re.compile(
            r"(\$\s?\d[\d,]*\s?(k|m|usd)?\s*(profit|per\s+(day|week|month)|in\s+\d+\s+(days|weeks))"
            r"|make\s+\$?\d[\d,]*\s*(a|per)\s+(day|week|month))",
            re.I,
        ),
        (
            "Specific earnings figure. Unsubstantiated earnings claim; a top disapproval "
            "trigger on both platforms."
        ),
        "Remove the figure entirely. Describe the method instead.",
    ),
    ClaimRule(
        "percentage_return_promise",
        Severity.HARD,
        re.compile(
            r"\b\d{1,4}(\.\d+)?\s?%\s*(return|gain|profit|roi|per\s+(day|week|month|year)"
            r"|monthly|weekly|annually)\b",
            re.I,
        ),
        "Promised percentage return.",
        (
            "Remove. If a historical statistic is essential it must carry its methodology "
            "and a safe harbour."
        ),
    ),
    ClaimRule(
        "win_rate_statistic",
        Severity.HARD,
        re.compile(
            r"\b(\d{1,3}(\.\d+)?\s?%\s*(of\s+)?(our\s+)?(tracked\s+)?"
            r"(directions?|calls?|signals?|trades?|picks?)\b.{0,20}\b(hit|win|correct|accurate|right|profit)"
            r"|\b(win|hit|accuracy|success)\s*rate\b.{0,20}\d{1,3}\s?%)",
            re.I,
        ),
        (
            "Performance statistic. Unverifiable for this product: its public "
            "track-record page and /api/predictions both returned HTTP 404 at build time, "
            "and the figure appears as three different values (73%, 73.8%, 74.1%) on its own site."
        ),
        (
            "Remove the statistic. Substitute the process claim: "
            "'every call is published with its outcome'."
        ),
    ),
    ClaimRule(
        "double_your_money",
        Severity.HARD,
        re.compile(
            r"\b(double|triple|10x|100x|multiply)\b.{0,20}\b(money|account|capital|portfolio|investment)\b",
            re.I,
        ),
        "Multiplier promise.",
        "Remove.",
    ),
    ClaimRule(
        "beat_the_market",
        Severity.HARD,
        re.compile(
            r"\b(beat|outperform|crush|destroy)\s+the\s+(market|s&p|index|street)\b",
            re.I,
        ),
        "Comparative performance promise.",
        "Remove, or reframe as a described method with no comparative claim.",
    ),
    ClaimRule(
        "financial_freedom",
        Severity.HARD,
        re.compile(
            r"\b(retire\s+(early|in\s+your)|financial\s+freedom|quit\s+your\s+job|"
            r"passive\s+income|change\s+your\s+life)\b",
            re.I,
        ),
        (
            "Lifestyle-outcome promise implying a guaranteed financial result. "
            "Explicitly listed as prohibited on Meta."
        ),
        "Remove.",
    ),
    ClaimRule(
        "payout_imagery",
        Severity.HARD,
        re.compile(
            r"\b(bank\s+balance|account\s+balance|payout|withdrawal\s+screenshot|profit\s+screenshot)\b",
            re.I,
        ),
        "Payout or balance imagery. Explicitly prohibited on Meta regardless of framing.",
        "Remove.",
    ),
    # ── PRODUCT_DISCLAIMER: contradicts CrowdWisdomTrading's own published FAQ ──
    ClaimRule(
        "position_access_implication",
        Severity.HARD,
        re.compile(
            r"\b(see|track|follow|copy|mirror|know)\b.{0,30}\b(their|the\s+pros'?|"
            r"smart\s+money'?s?|institutional)\b.{0,20}\b(position|trade|book|order\s+flow|entry)\b",
            re.I,
        ),
        (
            "PRODUCT_DISCLAIMER: implies access to traders' real positions. The product's "
            "own FAQ states it does not have access to positions, so this is false as well "
            "as non-compliant."
        ),
        (
            "Reframe as opinion aggregation: "
            "'the calls professional traders are publishing publicly'."
        ),
    ),
    ClaimRule(
        "copy_trading_implication",
        Severity.HARD,
        re.compile(
            r"\b(copy\s+(our|my|the)\s+trades?|auto[-\s]?trade|autotrade|trade\s+for\s+you|"
            r"we\s+trade\s+for\s+you|done\s+for\s+you)\b",
            re.I,
        ),
        (
            "PRODUCT_DISCLAIMER: implies copy-trading or automated execution. "
            "The product explicitly disclaims both."
        ),
        "State the opposite explicitly, or remove.",
    ),
    ClaimRule(
        "managed_accounts_implication",
        Severity.HARD,
        re.compile(
            r"\b(managed\s+account|we\s+manage|manage\s+your\s+(money|funds|portfolio)|"
            r"someone\s+else\s+trades)\b",
            re.I,
        ),
        (
            "PRODUCT_DISCLAIMER: implies managed accounts. "
            "The product explicitly disclaims this."
        ),
        "Remove.",
    ),
    # ── SOFT: permitted only with a disclosure or a rewrite ──
    ClaimRule(
        "implied_certainty",
        Severity.SOFT,
        re.compile(
            r"\b(never\s+miss|always\s+right|guaranteed\s+win|100\s?%\s+(accurate|correct)|"
            r"the\s+exact\s+(entry|price|top|bottom))\b",
            re.I,
        ),
        "Implies certainty about an individual signal.",
        "Soften to describe the process rather than the outcome.",
    ),
    ClaimRule(
        "unsubstantiated_superlative",
        Severity.SOFT,
        re.compile(
            r"\b(the\s+best|the\s+most\s+accurate|unlike\s+any\s+other|the\s+only\s+platform|"
            r"world'?s\s+(best|leading))\b",
            re.I,
        ),
        (
            "Unsubstantiated superlative. Meta's unsubstantiated-claims policy "
            "targets implied statistical dominance."
        ),
        "Remove the superlative, or make it specific and provable.",
    ),
    ClaimRule(
        "testimonial_earnings",
        Severity.SOFT,
        re.compile(r"\b(i|we)\s+(made|earned|turned|banked)\s+\$?\d", re.I),
        "Testimonial containing an earnings claim, requiring a typical-results disclosure.",
        "Remove the figure, or add a clear and prominent typical-results disclosure.",
    ),
)


def detect_claims(text: str) -> list[Finding]:
    """Scan text for prohibited claims. The ONE entry point — nothing else needs the rules."""
    findings: list[Finding] = []
    for rule in CLAIM_RULES:
        for match in rule.pattern.finditer(text):
            findings.append(
                Finding(
                    rule_id=rule.id,
                    severity=rule.severity,
                    matched_text=match.group(0).strip(),
                    why=rule.why,
                    fix=rule.fix,
                )
            )
    return findings


def has_hard_block(findings: list[Finding]) -> bool:
    return any(f.severity is Severity.HARD or f.severity == Severity.HARD for f in findings)


def rewrite_instructions(findings: list[Finding]) -> list[str]:
    """De-duplicated `fix` strings, ordered hard-first.

    These strings are written FOR A MODEL to consume — the rewrite prompt uses
    them verbatim. That is why `fix` is a specific instruction rather than
    'remove this'.
    """
    seen: set[str] = set()
    out: list[str] = []
    for finding in sorted(findings, key=lambda f: f.severity != Severity.HARD):
        if finding.fix not in seen:
            seen.add(finding.fix)
            out.append(finding.fix)
    return out


_WORD_STEMS: dict[str, str] = {
    "10": r"(?:10|ten)",
    "20": r"(?:20|twenty)",
    "30": r"(?:30|thirty)",
    "40": r"(?:40|forty)",
    "50": r"(?:50|fifty)",
    "60": r"(?:60|sixty)",
    "70": r"(?:70|seventy)",
    "73": r"(?:73|seventy[-\s]three)",
    "74": r"(?:74|seventy[-\s]four)",
    "80": r"(?:80|eighty)",
    "90": r"(?:90|ninety)",
    "100": r"(?:100|one[-\s]hundred|hundred)",
    "16000": r"(?:16,?000|sixteen[-\s]thousand)",
    "16564": r"(?:16,564|16564)",
}


def scan_prohibited_facts(text: str, prohibited: list[dict[str, Any]] | list[Any]) -> list[Finding]:
    """Check the script against the research brief's `prohibited_facts` list.

    Matches the fact string AND its numeric variants, so '74.1%' is caught by a
    rule written for '74.1% of tracked directions hit', and 'about 74 percent' is
    caught too. Paraphrase resistance is the whole point of this function.
    """
    findings: list[Finding] = []
    for entry in prohibited:
        if isinstance(entry, dict):
            fact = entry.get("fact", "")
            reason = entry.get("reason", "")
            rule = entry.get("rule", "Remove.")
        else:
            fact = getattr(entry, "fact", "")
            reason = getattr(entry, "reason", "")
            rule = getattr(entry, "rule", "Remove.")

        if not fact:
            continue

        # Exact phrase
        if fact.lower() in text.lower():
            findings.append(
                Finding(
                    rule_id="prohibited_fact",
                    severity=Severity.HARD,
                    matched_text=fact,
                    why=reason,
                    fix=rule,
                )
            )
            continue

        # Numeric variants: extract every number in the fact and look for it nearby
        # a relevant noun. Catches "74.1%", "74%", "about seventy-four percent".
        clean_fact = fact.replace(",", "")
        numbers = list(re.findall(r"\d+(?:\.\d+)?", clean_fact))
        # Also include percentage numbers mentioned in reason (e.g. 73% in reason)
        for r_pct in re.findall(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", reason, re.I):
            if r_pct not in numbers:
                numbers.append(r_pct)

        seen_stems: set[str] = set()
        for number in numbers:
            stem = number.split(".")[0]
            if stem in seen_stems:
                continue
            seen_stems.add(stem)

            stem_pat = _WORD_STEMS.get(stem, re.escape(stem))
            # Match percentage variants: e.g. 74.1%, 74%, about 74 percent
            variant = re.compile(rf"\b{stem_pat}(?:\.\d+)?\s?(?:%|percent\b)", re.I)
            for match in variant.finditer(text):
                findings.append(
                    Finding(
                        rule_id="prohibited_fact_numeric_variant",
                        severity=Severity.HARD,
                        matched_text=match.group(0).strip(),
                        why=f"Numerically derives from a prohibited fact ({fact!r}). {reason}",
                        fix=rule,
                    )
                )

            # Match large counts (e.g. 16,564 traders)
            if len(stem) >= 4:
                if len(stem) == 5:
                    large_num_pat = re.compile(
                        rf"\b(?:{stem[0:2]}[,\s]?{stem[2:]}|{stem_pat})\b", re.I
                    )
                else:
                    large_num_pat = re.compile(rf"\b{stem_pat}\b", re.I)
                for match in large_num_pat.finditer(text):
                    findings.append(
                        Finding(
                            rule_id="prohibited_fact_numeric_variant",
                            severity=Severity.HARD,
                            matched_text=match.group(0).strip(),
                            why=f"Numerically derives from a prohibited fact ({fact!r}). {reason}",
                            fix=rule,
                        )
                    )
    return findings
