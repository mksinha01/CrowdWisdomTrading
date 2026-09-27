"""Exhaustive test suite for deterministic financial-advertising claims engine.

Story: S06 Deterministic Claims Engine
Spec: doc/video-ads-agent.md §8.6, §3.4, §6.6, Appendix A
Contracts verified:
- len(CLAIM_RULES) == 15 (12 HARD + 3 SOFT)
- Every rule has >= 1 positive and >= 1 negative test
- Clean script reference ad produces zero HARD findings
- Numeric variant paraphrase resistance: 74.1%, 74%, about 74 percent,
  about seventy-four percent, 73%
- Case-insensitivity across rules
- Severity isolation across sentence/line boundaries
- rewrite_instructions: hard-first ordering, stable within group, deduplication
- has_hard_block logic
"""

from __future__ import annotations

import pytest

from cwt.domain.claims import (
    CLAIM_RULES,
    RULESET_VERSION,
    ClaimRule,
    Finding,
    Severity,
    detect_claims,
    has_hard_block,
    rewrite_instructions,
    scan_prohibited_facts,
)
from cwt.domain.models import DEFAULT_PROHIBITED_FACTS

# Reference compliant voiceover from §3.4 (spec line 711)
CLEAN_VOICEOVER_TEXT = (
    "Too many voices. Every day, thousands of traders post their read on the same "
    "five tickers — and every one of them is certain. So which one is right? "
    "Following one analyst means inheriting one person's blind spots. You cannot "
    "see the disagreement, because you are only reading one side of it. We read all of them. "
    "Thousands of professional traders across YouTube, Reddit and X, analysed by AI "
    "agents, distilled into the consensus that actually holds — with the entry, the "
    "targets and the stops written down. And here is the part nobody else does. Every "
    "single call is published, with its outcome. The wins and the misses. You can read "
    "the whole record before you pay us anything. That is what intelligence looks like "
    "when it is not a secret. Collective intelligence for traders."
)

# Reference compliant on-screen text from §3.4
CLEAN_ON_SCREEN_TEXTS = [
    "TOO MANY VOICES.",
    "Collective intelligence for traders.",
    "Trading involves significant risk. Informational and educational only. Not financial advice.",
]


# ===========================================================================
# 1. Ruleset structure & contract tests
# ===========================================================================


def test_ruleset_structure_and_counts() -> None:
    """Frozen contract: 15 rules total, 12 HARD and 3 SOFT."""
    assert len(CLAIM_RULES) == 15
    hard_rules = [r for r in CLAIM_RULES if r.severity == Severity.HARD]
    soft_rules = [r for r in CLAIM_RULES if r.severity == Severity.SOFT]
    assert len(hard_rules) == 12
    assert len(soft_rules) == 3
    assert RULESET_VERSION == "1.0.0"
    for r in CLAIM_RULES:
        assert isinstance(r, ClaimRule)


def test_all_expected_rule_ids_present() -> None:
    rule_ids = {r.id for r in CLAIM_RULES}
    expected_hard = {
        "guaranteed_returns",
        "risk_free_language",
        "specific_profit_figure",
        "percentage_return_promise",
        "win_rate_statistic",
        "double_your_money",
        "beat_the_market",
        "financial_freedom",
        "payout_imagery",
        "position_access_implication",
        "copy_trading_implication",
        "managed_accounts_implication",
    }
    expected_soft = {
        "implied_certainty",
        "unsubstantiated_superlative",
        "testimonial_earnings",
    }
    assert rule_ids == expected_hard | expected_soft


def test_product_disclaimer_marker_preserved() -> None:
    """The 3 product-disclaimer rules must record PRODUCT_DISCLAIMER in why text."""
    disclaimer_rules = {
        "position_access_implication",
        "copy_trading_implication",
        "managed_accounts_implication",
    }
    rules_by_id = {r.id: r for r in CLAIM_RULES}
    for rid in disclaimer_rules:
        assert rid in rules_by_id
        assert "PRODUCT_DISCLAIMER" in rules_by_id[rid].why


# ===========================================================================
# 2. Exhaustive Per-Rule Positive Tests (all 15 rules)
# ===========================================================================

POSITIVE_CASES: list[tuple[str, str, Severity]] = [
    (
        "guaranteed_returns",
        "We guarantee return on your investment.",
        Severity.HARD,
    ),
    (
        "risk_free_language",
        "This is a completely risk-free trading system.",
        Severity.HARD,
    ),
    (
        "specific_profit_figure",
        "You can make $500 a day with our strategies.",
        Severity.HARD,
    ),
    (
        "percentage_return_promise",
        "Expect a 15% monthly return on your balance.",
        Severity.HARD,
    ),
    (
        "win_rate_statistic",
        "74% of our tracked signals hit their targets.",
        Severity.HARD,
    ),
    (
        "double_your_money",
        "Double your capital in just a few trades.",
        Severity.HARD,
    ),
    (
        "beat_the_market",
        "Learn how to beat the market every quarter.",
        Severity.HARD,
    ),
    (
        "financial_freedom",
        "Achieve financial freedom with smart analysis.",
        Severity.HARD,
    ),
    (
        "payout_imagery",
        "Check out this real bank balance today.",
        Severity.HARD,
    ),
    (
        "position_access_implication",
        "See the pros position before anyone else.",
        Severity.HARD,
    ),
    (
        "copy_trading_implication",
        "Simply copy our trades directly into your broker.",
        Severity.HARD,
    ),
    (
        "managed_accounts_implication",
        "Open a managed account and let experts trade.",
        Severity.HARD,
    ),
    (
        "implied_certainty",
        "With this tool you will never miss a move.",
        Severity.SOFT,
    ),
    (
        "unsubstantiated_superlative",
        "We are the best platform for market intelligence.",
        Severity.SOFT,
    ),
    (
        "testimonial_earnings",
        "I made $1500 using this consensus alert.",
        Severity.SOFT,
    ),
]


@pytest.mark.parametrize("expected_rule_id, positive_text, expected_severity", POSITIVE_CASES)
def test_each_rule_positive_trigger(
    expected_rule_id: str, positive_text: str, expected_severity: Severity
) -> None:
    findings = detect_claims(positive_text)
    triggered_ids = [f.rule_id for f in findings]
    assert expected_rule_id in triggered_ids, (
        f"Rule '{expected_rule_id}' failed to trigger on: {positive_text!r}"
    )

    finding = next(f for f in findings if f.rule_id == expected_rule_id)
    assert finding.severity == expected_severity
    assert finding.matched_text
    assert finding.matched_text == finding.matched_text.strip()

    # Rule metadata integrity
    rule = next(r for r in CLAIM_RULES if r.id == expected_rule_id)
    assert finding.why == rule.why
    assert finding.fix == rule.fix


# ===========================================================================
# 3. Exhaustive Per-Rule Negative Tests (all 15 rules)
# ===========================================================================

NEGATIVE_CASES: list[tuple[str, str]] = [
    (
        "guaranteed_returns",
        "Trading carries risk and outcomes vary across market conditions.",
    ),
    (
        "risk_free_language",
        "Trading involves significant risk of loss.",
    ),
    (
        "specific_profit_figure",
        "Subscriptions start at $29.99 billed each month.",
    ),
    (
        "percentage_return_promise",
        "We track consensus across top market sectors and assets.",
    ),
    (
        "win_rate_statistic",
        "Every single call is published with its outcome.",
    ),
    (
        "double_your_money",
        "Manage your capital responsibly and protect your downside.",
    ),
    (
        "beat_the_market",
        "Understand how the market actually moves when signals align.",
    ),
    (
        "financial_freedom",
        "Build a disciplined, repeatable trading process.",
    ),
    (
        "payout_imagery",
        "Review the full public trade log on the dashboard.",
    ),
    (
        "position_access_implication",
        "See the calls professional traders are publishing publicly.",
    ),
    (
        "copy_trading_implication",
        "Execute your own decisions on your chosen brokerage platform.",
    ),
    (
        "managed_accounts_implication",
        "You maintain full control of your portfolio and execution.",
    ),
    (
        "implied_certainty",
        "Capture high-probability setups when broad consensus forms.",
    ),
    (
        "unsubstantiated_superlative",
        "A platform for aggregating publicly shared analyst opinions.",
    ),
    (
        "testimonial_earnings",
        "I analyzed multiple alerts before taking any trade.",
    ),
]


@pytest.mark.parametrize("rule_id, negative_text", NEGATIVE_CASES)
def test_each_rule_negative_clean(rule_id: str, negative_text: str) -> None:
    findings = detect_claims(negative_text)
    triggered_ids = [f.rule_id for f in findings]
    assert rule_id not in triggered_ids, (
        f"Rule '{rule_id}' falsely triggered on compliant copy: {negative_text!r}"
    )


# ===========================================================================
# 4. Clean Script Reference Ad Test (§3.4 voiceover)
# ===========================================================================


def test_clean_script_zero_hard_findings() -> None:
    """The §3.4 voiceover is the reference compliant ad and must produce zero HARD findings."""
    findings = detect_claims(CLEAN_VOICEOVER_TEXT)
    hard_findings = [f for f in findings if f.severity == Severity.HARD]
    assert len(hard_findings) == 0, f"Clean script produced hard findings: {hard_findings}"
    assert has_hard_block(findings) is False
    assert len(findings) == 0


def test_clean_script_on_screen_texts_clean() -> None:
    for ost in CLEAN_ON_SCREEN_TEXTS:
        findings = detect_claims(ost)
        assert len(findings) == 0, f"Clean OST {ost!r} produced findings: {findings}"


def test_clean_script_no_prohibited_facts() -> None:
    findings = scan_prohibited_facts(CLEAN_VOICEOVER_TEXT, DEFAULT_PROHIBITED_FACTS)
    assert len(findings) == 0, f"Clean script triggered prohibited facts: {findings}"


# ===========================================================================
# 5. Case-insensitivity tests
# ===========================================================================


@pytest.mark.parametrize(
    "upper_text, expected_rule",
    [
        ("WE GUARANTEE RETURN ON CAPITAL", "guaranteed_returns"),
        ("100% RISK-FREE TRADING", "risk_free_language"),
        ("NO RISK INVOLVED", "risk_free_language"),
        ("DOUBLE YOUR MONEY FAST", "double_your_money"),
        ("BEAT THE MARKET EASILY", "beat_the_market"),
        ("FINANCIAL FREEDOM AWAITS", "financial_freedom"),
        ("COPY OUR TRADES TODAY", "copy_trading_implication"),
        ("MANAGED ACCOUNT SERVICE", "managed_accounts_implication"),
        ("THE BEST PLATFORM EVER", "unsubstantiated_superlative"),
    ],
)
def test_case_insensitivity(upper_text: str, expected_rule: str) -> None:
    findings = detect_claims(upper_text)
    rule_ids = [f.rule_id for f in findings]
    assert expected_rule in rule_ids


# ===========================================================================
# 6. Severity isolation across sentence / newline boundaries
# ===========================================================================


def test_severity_isolation_multiline_does_not_cross_newlines() -> None:
    """Regexes must not bridge across line breaks to create false HARD blocks."""
    multiline_text = (
        "There is no guarantee.\n"
        "Return on investment is subject to real market risk."
    )
    findings = detect_claims(multiline_text)
    assert "guaranteed_returns" not in [f.rule_id for f in findings]


def test_severity_isolation_unrelated_sentences() -> None:
    text = "We monitor risk carefully. Free members can view basic market data."
    findings = detect_claims(text)
    assert "risk_free_language" not in [f.rule_id for f in findings]


# ===========================================================================
# 7. Prohibited facts scanning & numeric variants
# ===========================================================================


def test_prohibited_facts_exact_match() -> None:
    findings = scan_prohibited_facts(
        "We note that 74.1% of tracked directions hit in our review.",
        DEFAULT_PROHIBITED_FACTS,
    )
    assert len(findings) >= 1
    assert any(f.rule_id == "prohibited_fact" for f in findings)
    assert all(f.severity == Severity.HARD for f in findings)


def test_prohibited_facts_exact_case_insensitive() -> None:
    findings = scan_prohibited_facts(
        "74.1% OF TRACKED DIRECTIONS HIT",
        DEFAULT_PROHIBITED_FACTS,
    )
    assert any(f.rule_id == "prohibited_fact" for f in findings)


@pytest.mark.parametrize(
    "variant_snippet",
    [
        "Our success rate is 74.1% across all trades.",
        "We achieved a 74% accuracy rate.",
        "About 74 percent of directions hit.",
        "About seventy-four percent of directions hit.",
        "Our hit rate is 73% according to public stats.",
    ],
)
def test_prohibited_facts_numeric_variants(variant_snippet: str) -> None:
    findings = scan_prohibited_facts(variant_snippet, DEFAULT_PROHIBITED_FACTS)
    assert len(findings) >= 1
    assert any(f.rule_id == "prohibited_fact_numeric_variant" for f in findings)
    assert all(f.severity == Severity.HARD for f in findings)


def test_prohibited_facts_large_count_variant() -> None:
    findings = scan_prohibited_facts(
        "Tracking 16,564 traders on the platform.",
        DEFAULT_PROHIBITED_FACTS,
    )
    assert len(findings) >= 1
    assert any(f.rule_id == "prohibited_fact_numeric_variant" for f in findings)


# ===========================================================================
# 8. rewrite_instructions ordering and deduplication
# ===========================================================================


def test_rewrite_instructions_hard_first_and_deduped() -> None:
    findings = [
        Finding("soft_rule_1", Severity.SOFT, "txt", "why", "Soft fix A"),
        Finding("hard_rule_1", Severity.HARD, "txt", "why", "Hard fix 1"),
        Finding("soft_rule_2", Severity.SOFT, "txt", "why", "Soft fix B"),
        Finding("hard_rule_2", Severity.HARD, "txt", "why", "Hard fix 1"),  # Duplicate fix
        Finding("hard_rule_3", Severity.HARD, "txt", "why", "Hard fix 2"),
    ]
    instructions = rewrite_instructions(findings)
    assert instructions == ["Hard fix 1", "Hard fix 2", "Soft fix A", "Soft fix B"]


def test_rewrite_instructions_empty() -> None:
    assert rewrite_instructions([]) == []


# ===========================================================================
# 9. has_hard_block helper
# ===========================================================================


def test_has_hard_block_truth_table() -> None:
    assert has_hard_block([]) is False

    soft_only = [Finding("r1", Severity.SOFT, "txt", "why", "fix")]
    assert has_hard_block(soft_only) is False

    hard_only = [Finding("r2", Severity.HARD, "txt", "why", "fix")]
    assert has_hard_block(hard_only) is True

    mixed = [
        Finding("r1", Severity.SOFT, "txt", "why", "fix"),
        Finding("r2", Severity.HARD, "txt", "why", "fix"),
    ]
    assert has_hard_block(mixed) is True


# ===========================================================================
# 10. Multiple simultaneous rules on single text
# ===========================================================================


def test_multiple_rules_on_single_text() -> None:
    # Contains risk-free AND double your capital
    dirty_text = "Join our risk-free program and double your capital."
    findings = detect_claims(dirty_text)
    rule_ids = {f.rule_id for f in findings}
    assert "risk_free_language" in rule_ids
    assert "double_your_money" in rule_ids
    assert has_hard_block(findings) is True
    instructions = rewrite_instructions(findings)
    assert len(instructions) == 2


# ===========================================================================
# 11. Done When specification snippet verbatim
# ===========================================================================


def test_done_when_specification_snippet() -> None:
    assert len(CLAIM_RULES) == 15
    assert sum(r.severity == "hard" for r in CLAIM_RULES) == 12
    assert has_hard_block(detect_claims("We guarantee returns of 15% monthly.")) is True
    assert detect_claims("Every call is published with its outcome.") == []
    f = scan_prohibited_facts(
        "about 74 percent of directions hit",
        [{"fact": "74.1% of tracked directions hit", "reason": "r", "rule": "Remove."}],
    )
    assert [x.rule_id for x in f] == ["prohibited_fact_numeric_variant"]
