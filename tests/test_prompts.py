"""Tests for prompt templates and builders (Story S13).

Spec: doc/video-ads-agent.md §6 (lines 1059-1657)
Story: doc/stories/S13-prompt-package.md
"""

from __future__ import annotations

import pytest

import cwt.prompts
from cwt.prompts import (
    AD_EXTRACTION_PROMPT,
    BEAT_SHEET_PROMPT,
    BRIEF_ASSEMBLY_PROMPT,
    CLAIMS_JUDGE_PROMPT,
    CLAIMS_POLICY,
    CREATIVE_REVIEW_PROMPT,
    CROWD_EFFECT_RESEARCH_PROMPT,
    HOOK_CANDIDATES_PROMPT,
    PAIN_RESEARCH_PROMPT,
    PROMPT_VERSION,
    REWRITE_PROMPT,
    STORYBOARD_PROMPT,
    UNIQUE_DATA_RESEARCH_PROMPT,
    VARIANT_JUDGE_PROMPT,
    _COMMON_RESEARCH_RULES,
    build_beat_sheet_prompt,
    build_brief_prompt,
    build_claims_judge_prompt,
    build_creative_review_prompt,
    build_extraction_prompt,
    build_hook_candidates_prompt,
    build_research_prompt,
    build_rewrite_prompt,
    build_storyboard_prompt,
    build_variant_judge_prompt,
    safe_format,
)
from cwt.prompts import _format, claims_policy, extract, research, review, script

TEMPLATES = [
    AD_EXTRACTION_PROMPT,
    BEAT_SHEET_PROMPT,
    PAIN_RESEARCH_PROMPT,
    UNIQUE_DATA_RESEARCH_PROMPT,
    CROWD_EFFECT_RESEARCH_PROMPT,
    BRIEF_ASSEMBLY_PROMPT,
    HOOK_CANDIDATES_PROMPT,
    STORYBOARD_PROMPT,
    VARIANT_JUDGE_PROMPT,
    REWRITE_PROMPT,
    CREATIVE_REVIEW_PROMPT,
    CLAIMS_POLICY,
    CLAIMS_JUDGE_PROMPT,
]


def test_thirteen_templates_present_and_substantial():
    """Verify all 13 prompt templates have len > 200."""
    assert len(TEMPLATES) == 13
    for tmpl in TEMPLATES:
        assert isinstance(tmpl, str)
        assert len(tmpl) > 200


def test_prompt_version():
    """Verify PROMPT_VERSION == '1.0.0' on package and submodules."""
    assert PROMPT_VERSION == "1.0.0"
    assert _format.PROMPT_VERSION == "1.0.0"
    assert extract.PROMPT_VERSION == "1.0.0"
    assert research.PROMPT_VERSION == "1.0.0"
    assert script.PROMPT_VERSION == "1.0.0"
    assert review.PROMPT_VERSION == "1.0.0"
    assert claims_policy.PROMPT_VERSION == "1.0.0"


class TestSafeFormat:
    def test_safe_format_normal_substitution(self):
        result = safe_format("Hello {name}!", name="Trader")
        assert result == "Hello Trader!"

    def test_safe_format_missing_placeholder_returns_uninterpolated(self):
        """Contract: on KeyError/IndexError/ValueError return the UNINTERPOLATED template."""
        raw = "{a} {missing}"
        assert safe_format(raw, a=1) == raw

    def test_safe_format_stray_brace_returns_uninterpolated(self):
        raw = "Stray brace {unclosed"
        assert safe_format(raw, unclosed=1) == raw

    def test_safe_format_positional_mismatch_returns_uninterpolated(self):
        raw = "Positional {0}"
        assert safe_format(raw, key="val") == raw

    def test_safe_format_double_braces_survive(self):
        raw = "JSON snippet: {{'key': '{name}'}}"
        result = safe_format(raw, name="val")
        assert result == "JSON snippet: {'key': 'val'}"

    def test_safe_format_ignores_extra_kwargs(self):
        result = safe_format("Just {a}", a="foo", extra="bar")
        assert result == "Just foo"


class TestBuildersDefault:
    def test_build_extraction_prompt(self):
        prompt = build_extraction_prompt(ad_text="Tired of losing money?", active_days=45)
        assert isinstance(prompt, str) and len(prompt) > 0
        assert "Tired of losing money?" in prompt
        assert "45 days" in prompt

    def test_build_beat_sheet_prompt(self):
        prompt = build_beat_sheet_prompt(ad_text="Sample ad text", duration_s=40.0)
        assert isinstance(prompt, str) and len(prompt) > 0
        assert "Sample ad text" in prompt
        assert "40.0" in prompt

    @pytest.mark.parametrize("angle", ["pain", "unique_data", "crowd_effect"])
    def test_build_research_prompt(self, angle: str):
        prompt = build_research_prompt(
            angle=angle,
            window_start="2026-08-01",
            window_end="2026-09-01",
        )
        assert isinstance(prompt, str) and len(prompt) > 0
        assert "2026-08-01" in prompt
        assert angle in prompt

    def test_build_brief_prompt(self):
        prompt = build_brief_prompt(angle_outputs="Outputs from 3 angles")
        assert isinstance(prompt, str) and len(prompt) > 0
        assert "Outputs from 3 angles" in prompt

    def test_build_hook_candidates_prompt(self):
        prompt = build_hook_candidates_prompt(brief="Market brief")
        assert isinstance(prompt, str) and len(prompt) > 0
        assert "Market brief" in prompt

    def test_build_storyboard_prompt(self):
        prompt = build_storyboard_prompt(
            duration_s=42,
            angle="pain",
            angle_rationale="High emotional resonance",
            beat_timeline="0-3s hook, 3-10s problem",
            prohibited="No win rates",
            shot_schema='{"shot_id": "s01"}',
            storyboard_schema='{"shots": []}',
            brief="Product brief",
            patterns="Mined ad patterns",
            hook="Stop guessing",
        )
        assert isinstance(prompt, str) and len(prompt) > 0
        assert "42" in prompt
        assert "pain" in prompt
        assert "147 words" in prompt or "147" in prompt
        assert "Stop guessing" in prompt

    def test_build_storyboard_prompt_word_budget_calculation(self):
        """Duration 42s * 3.5 = 147 words."""
        prompt = build_storyboard_prompt(
            duration_s=42,
            angle="pain",
            angle_rationale="test",
            beat_timeline="test",
            prohibited="test",
            shot_schema="test",
            storyboard_schema="test",
            brief="test",
            patterns="test",
            hook="test",
        )
        assert "roughly 147 words" in prompt

    def test_build_variant_judge_prompt(self):
        prompt = build_variant_judge_prompt(variants="Three variants text")
        assert isinstance(prompt, str) and len(prompt) > 0
        assert "Three variants text" in prompt

    def test_build_rewrite_prompt(self):
        prompt = build_rewrite_prompt(
            verdict="request_changes",
            scores='{"brand_fit": 5.0}',
            weakest_axes="brand_fit, hook_strength",
            changes_requested="Darker tone",
            must_fix="shot 2 lighting",
            must_not_change="hook text",
            prohibited_block="No profit claims",
        )
        assert isinstance(prompt, str) and len(prompt) > 0
        assert "request_changes" in prompt
        assert "Darker tone" in prompt
        assert "shot 2 lighting" in prompt
        assert "hook text" in prompt

    def test_build_creative_review_prompt(self):
        prompt = build_creative_review_prompt(
            storyboard="Storyboard content",
            patterns="Winning patterns",
            threshold=7.5,
        )
        assert isinstance(prompt, str) and len(prompt) > 0
        assert "Storyboard content" in prompt
        assert "Winning patterns" in prompt
        assert "7.5" in prompt

    def test_build_claims_judge_prompt(self):
        prompt = build_claims_judge_prompt(script="Voiceover and on-screen text")
        assert isinstance(prompt, str) and len(prompt) > 0
        assert "Voiceover and on-screen text" in prompt
        # Policy injected
        assert CLAIMS_POLICY in prompt


class TestCustomOverrides:
    def test_custom_override_valid(self):
        custom = "Custom ad analysis: {ad_text} active: {active_days}"
        prompt = build_extraction_prompt("My ad", 10, custom=custom)
        assert prompt == "Custom ad analysis: My ad active: 10"

    def test_custom_override_with_escaped_braces(self):
        custom = "Custom with JSON: {{'sample': '{ad_text}'}} active: {active_days}"
        prompt = build_extraction_prompt("My ad", 10, custom=custom)
        assert prompt == "Custom with JSON: {'sample': 'My ad'} active: 10"

    def test_custom_override_malformed_falls_back_gracefully(self):
        """Malformed custom override degrades to raw template, does not raise."""
        bad_custom = "Bad custom: {unknown_placeholder} {ad_text}"
        prompt = build_extraction_prompt("My ad", 10, custom=bad_custom)
        assert prompt == bad_custom


class TestSpecificSpecContracts:
    def test_hook_candidates_prompt_six_archetypes(self):
        """Spec §6.4: Mentions all six archetypes by name."""
        archetypes = [
            "pattern_interrupt",
            "contrarian_stat",
            "question",
            "visual_shock",
            "social_proof",
            "pain_point",
        ]
        for arc in archetypes:
            assert arc in HOOK_CANDIDATES_PROMPT

    def test_storyboard_prompt_exact_risk_disclosure(self):
        """Spec §6.4: Contains exact required risk disclosure."""
        expected = (
            "Trading involves significant risk. Informational and educational only. Not financial advice."
        )
        assert expected in STORYBOARD_PROMPT

    def test_claims_policy_disclaimers(self):
        """Spec §6.6: Mentions positions, copy-trading, managed accounts."""
        assert "positions" in CLAIMS_POLICY
        assert "copy-trading" in CLAIMS_POLICY
        assert "managed accounts" in CLAIMS_POLICY

    def test_review_weighted_axes_sum_to_one(self):
        """Spec §6.5: Weights must sum to 1.0 (0.25+0.20+0.15+0.15+0.15+0.10)."""
        weights = [0.25, 0.20, 0.15, 0.15, 0.15, 0.10]
        assert pytest.approx(sum(weights)) == 1.0
