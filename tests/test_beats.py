"""Tests for domain/beats.py: beat taxonomy, timeline aggregation, and archetype durability."""

from __future__ import annotations

import pytest

from cwt.domain.beats import (
    ASPECT_RATIOS,
    BEAT_DEFAULT_PROPORTIONS,
    BEAT_TAXONOMY,
    CAMERA_MOVES,
    COLOUR_TEMPS_K,
    DEFAULT_DURABILITY_TABLE,
    HOOK_ARCHETYPES,
    SUBJECTS,
    TRANSITIONS,
    UNDERUSED_HIGH_DURABILITY,
    aggregate_beat_timeline,
    archetype_distribution,
    compute_durability,
    default_timeline,
    hook_score_boost,
    median_hook_duration,
    underused_high_durability,
)
from cwt.domain.models import (
    BeatEntry,
    BeatName,
    HookArchetype,
    HookPattern,
    Storyboard,
)
from tests.test_models import _load_fixture

# ===========================================================================
# 1. Constants & Reference Data
# ===========================================================================


def test_beat_taxonomy_constants() -> None:
    """Validate closed beat taxonomy and default proportions."""
    assert len(BEAT_TAXONOMY) == 7
    expected = ("hook", "problem", "agitation", "mechanism", "proof", "objection", "cta")
    assert tuple(b.value for b in BEAT_TAXONOMY) == expected

    # Proportions sum to exactly 1.00 and cover all beats
    assert set(BEAT_DEFAULT_PROPORTIONS.keys()) == set(expected)
    assert abs(sum(BEAT_DEFAULT_PROPORTIONS.values()) - 1.0) < 1e-6
    assert BEAT_DEFAULT_PROPORTIONS["hook"] == 0.08
    assert BEAT_DEFAULT_PROPORTIONS["mechanism"] == 0.23


def test_archetype_constants() -> None:
    """Validate hook archetypes and durability edge list."""
    assert len(HOOK_ARCHETYPES) == 7
    assert "social_proof" in HOOK_ARCHETYPES
    assert "contrarian_stat" in HOOK_ARCHETYPES

    # Underused high durability
    assert UNDERUSED_HIGH_DURABILITY == ("social_proof", "pain_point", "question")

    # Reference data lists
    assert CAMERA_MOVES == ("static", "push_in", "pull_out", "whip_pan")
    assert TRANSITIONS == ("cut", "dissolve", "flash_white", "wipeleft", "wiperight", "zoomblur")
    assert COLOUR_TEMPS_K == (3200, 4300, 5600, 6500, 7000, 9000)
    assert ASPECT_RATIOS == ("9:16", "1:1", "4:5", "16:9")
    assert len(SUBJECTS) == 6


# ===========================================================================
# 2. default_timeline
# ===========================================================================


def test_default_timeline_properties() -> None:
    """Verify default_timeline produces contiguous, span-covering MedianBeats."""
    tl = default_timeline(42.0)
    assert len(tl) == 7
    assert tl[0].start_s == 0.0
    assert tl[-1].end_s == 42.0

    # Contiguity check
    assert all(a.end_s == b.start_s for a, b in zip(tl, tl[1:]))

    # Beat sequence matches taxonomy
    assert [b.beat for b in tl] == list(BEAT_TAXONOMY)

    # Tolerances are all 3.0 by default
    assert all(b.tolerance_s == 3.0 for b in tl)

    # Check known timings for 42.0s
    # 0.08 * 42 = 3.36
    assert tl[0].beat == BeatName.HOOK
    assert tl[0].start_s == 0.0
    assert tl[0].end_s == 3.36


def test_default_timeline_custom_duration_and_tolerance() -> None:
    tl = default_timeline(30.0, tolerance_s=2.5)
    assert tl[0].start_s == 0.0
    assert tl[-1].end_s == 30.0
    assert all(a.end_s == b.start_s for a, b in zip(tl, tl[1:]))
    assert all(b.tolerance_s == 2.5 for b in tl)


def test_default_timeline_negative_duration_raises() -> None:
    with pytest.raises(ValueError, match="total_duration_s must be positive"):
        default_timeline(-5.0)


# ===========================================================================
# 3. aggregate_beat_timeline & Contiguity Repair
# ===========================================================================


def test_aggregate_beat_timeline_fallback_on_few_samples() -> None:
    """When len(sheets) < min_samples, falls back to default_timeline and flags diagnostic."""
    small_sheets: list[list[BeatEntry]] = [
        [BeatEntry(beat=BeatName.HOOK, start_s=0.0, end_s=3.0, what_happens="hook")]
        for _ in range(3)
    ]
    tl, diag = aggregate_beat_timeline(small_sheets, total_duration_s=42.0, min_samples=5)

    assert diag["fallback"] is True
    assert "Sample count (3) < min_samples (5)" in diag["fallback_reason"]
    assert diag["sheet_count"] == 3
    assert len(tl) == 7
    assert tl[0].start_s == 0.0
    assert tl[-1].end_s == 42.0
    assert all(a.end_s == b.start_s for a, b in zip(tl, tl[1:]))


def test_aggregate_beat_timeline_synthetic_12_ads() -> None:
    """Synthetic 12-ad fixture with known hand-calculated medians and dropped beats.

    - 12 ads have hook, problem, agitation, mechanism, proof, cta.
    - 'objection' is present in only 4 ads (< 6) -> must be dropped!
    - Hand-calculated start_s values:
        hook: all 0.0 -> median 0.0, spread 0.0 -> tol 1.0 (clamped)
        problem: [3.0, 3.0, 3.2, 3.2, 3.4, 3.4, 3.6, 3.6, 3.8, 3.8, 4.0, 4.0]
                 median = (3.4 + 3.6) / 2 = 3.50
        agitation: starts around 9.0 -> median 9.0
        mechanism: starts around 15.0 -> median 15.0
        proof: starts around 24.0 -> median 24.0
        cta: starts around 30.0 -> median 30.0
    """
    problem_starts = [3.0, 3.0, 3.2, 3.2, 3.4, 3.4, 3.6, 3.6, 3.8, 3.8, 4.0, 4.0]

    sheets: list[list[BeatEntry]] = []
    for i in range(12):
        p_start = problem_starts[i]
        sheet = [
            BeatEntry(beat=BeatName.HOOK, start_s=0.0, end_s=p_start, what_happens="hook"),
            BeatEntry(beat=BeatName.PROBLEM, start_s=p_start, end_s=9.0, what_happens="problem"),
            BeatEntry(beat=BeatName.AGITATION, start_s=9.0, end_s=15.0, what_happens="agitation"),
            BeatEntry(beat=BeatName.MECHANISM, start_s=15.0, end_s=24.0, what_happens="mechanism"),
            BeatEntry(beat=BeatName.PROOF, start_s=24.0, end_s=30.0, what_happens="proof"),
        ]
        # Only 4 ads have objection (ads 0, 1, 2, 3)
        if i < 4:
            sheet.append(
                BeatEntry(
                    beat=BeatName.OBJECTION, start_s=30.0, end_s=33.0, what_happens="objection"
                )
            )
            sheet.append(
                BeatEntry(beat=BeatName.CTA, start_s=33.0, end_s=36.0, what_happens="cta")
            )
        else:
            sheet.append(
                BeatEntry(beat=BeatName.CTA, start_s=30.0, end_s=36.0, what_happens="cta")
            )
        sheets.append(sheet)

    total_duration_s = 36.0
    tl, diag = aggregate_beat_timeline(sheets, total_duration_s=total_duration_s, min_samples=5)

    assert diag["fallback"] is False
    assert diag["sheet_count"] == 12
    assert "objection" in diag["dropped_beats"]
    assert "hook" in diag["retained_beats"]
    assert "problem" in diag["retained_beats"]
    assert "agitation" in diag["retained_beats"]
    assert "mechanism" in diag["retained_beats"]
    assert "proof" in diag["retained_beats"]
    assert "cta" in diag["retained_beats"]

    # Timeline has 6 retained beats (objection dropped)
    assert len(tl) == 6
    retained_names = [b.beat.value for b in tl]
    assert retained_names == ["hook", "problem", "agitation", "mechanism", "proof", "cta"]

    # Verify problem start matches hand median
    problem_beat = next(b for b in tl if b.beat == BeatName.PROBLEM)
    assert problem_beat.start_s == 3.50

    # Verify hook end matches problem start (contiguity repair)
    hook_beat = next(b for b in tl if b.beat == BeatName.HOOK)
    assert hook_beat.start_s == 0.0
    assert hook_beat.end_s == 3.50

    # Verify total contiguity
    assert tl[0].start_s == 0.0
    assert tl[-1].end_s == total_duration_s
    assert all(a.end_s == b.start_s for a, b in zip(tl, tl[1:]))

    # Verify tolerance clamping to [1.0, 4.0]
    for b in tl:
        assert 1.0 <= b.tolerance_s <= 4.0


def test_aggregate_beat_timeline_tolerance_clamping() -> None:
    """Tolerances should clamp to 1.0 min and 4.0 max."""
    # Narrow spread: all identical -> clamped to 1.0
    sheets_narrow = [
        [
            BeatEntry(beat=BeatName.HOOK, start_s=0.0, end_s=3.0, what_happens="h"),
            BeatEntry(beat=BeatName.CTA, start_s=3.0, end_s=30.0, what_happens="c"),
        ]
        for _ in range(6)
    ]
    tl_narrow, _ = aggregate_beat_timeline(sheets_narrow, total_duration_s=30.0)
    for b in tl_narrow:
        assert b.tolerance_s >= 1.0

    # Wide spread: start times ranging from 1.0 to 20.0
    wide_starts = [1.0, 2.0, 10.0, 18.0, 19.0, 20.0]
    sheets_wide = [
        [
            BeatEntry(beat=BeatName.HOOK, start_s=0.0, end_s=wide_starts[i], what_happens="h"),
            BeatEntry(beat=BeatName.CTA, start_s=wide_starts[i], end_s=30.0, what_happens="c"),
        ]
        for i in range(6)
    ]
    tl_wide, _ = aggregate_beat_timeline(sheets_wide, total_duration_s=30.0)
    cta_beat = next(b for b in tl_wide if b.beat == BeatName.CTA)
    assert cta_beat.tolerance_s == 4.0  # Clamped to upper bound


def test_aggregate_beat_timeline_dict_entries() -> None:
    """Function supports raw dicts as well as Pydantic models."""
    sheets_dict: list[list[dict]] = [
        [
            {"beat": "hook", "start_s": 0.0, "end_s": 3.0, "what_happens": "h"},
            {"beat": "cta", "start_s": 3.0, "end_s": 10.0, "what_happens": "c"},
        ]
        for _ in range(5)
    ]
    tl, diag = aggregate_beat_timeline(
        sheets_dict, total_duration_s=10.0, min_samples=5  # type: ignore[arg-type]
    )
    assert diag["fallback"] is False
    assert len(tl) == 2
    assert tl[0].start_s == 0.0
    assert tl[-1].end_s == 10.0


# ===========================================================================
# 4. archetype_distribution
# ===========================================================================


def test_archetype_distribution_sums_to_one() -> None:
    """Verify archetype distribution sums to 1.0 and matches §3.2 proportions."""
    # 12 hooks matching §3.2: 4 contrarian_stat, 3 bold_statement, 2 pattern_interrupt,
    # 2 question, 1 social_proof
    archetypes = (
        ["contrarian_stat"] * 4
        + ["bold_statement"] * 3
        + ["pattern_interrupt"] * 2
        + ["question"] * 2
        + ["social_proof"] * 1
    )
    hooks = [
        HookPattern(
            text=f"Hook {i}",
            archetype=HookArchetype(arch),
            stop_power_score=7.5,
            why_it_stops_the_scroll="test",
        )
        for i, arch in enumerate(archetypes)
    ]
    dist = archetype_distribution(hooks)

    assert dist == {
        "contrarian_stat": 0.33,
        "bold_statement": 0.25,
        "pattern_interrupt": 0.17,
        "question": 0.17,
        "social_proof": 0.08,
    }
    assert sum(dist.values()) == 1.0


def test_archetype_distribution_odd_numbers() -> None:
    """Verify rounding adjustment ensures exact 1.0 sum even with 1/3 splits."""
    hooks = [
        HookPattern(
            text="h1",
            archetype=HookArchetype.PATTERN_INTERRUPT,
            stop_power_score=8.0,
            why_it_stops_the_scroll="...",
        ),
        HookPattern(
            text="h2",
            archetype=HookArchetype.CONTRARIAN_STAT,
            stop_power_score=8.0,
            why_it_stops_the_scroll="...",
        ),
        HookPattern(
            text="h3",
            archetype=HookArchetype.QUESTION,
            stop_power_score=8.0,
            why_it_stops_the_scroll="...",
        ),
    ]
    dist = archetype_distribution(hooks)
    assert sum(dist.values()) == 1.0
    assert len(dist) == 3


def test_archetype_distribution_empty() -> None:
    assert archetype_distribution([]) == {}


# ===========================================================================
# 5. underused_high_durability
# ===========================================================================


def test_underused_high_durability_basic() -> None:
    """Archetypes below frequency floor (0.10) whose durability exceeds the mean.

    In §3.2 distribution:
      contrarian_stat: 0.33 (>= 0.10) -> no
      bold_statement: 0.25 (>= 0.10) -> no
      pattern_interrupt: 0.17 (>= 0.10) -> no
      question: 0.17 (>= 0.10) -> no
      social_proof: 0.08 (< 0.10) and durability 2.1 > mean (~1.27) -> YES!
    """
    in_dist = {
        "contrarian_stat": 0.33,
        "bold_statement": 0.25,
        "pattern_interrupt": 0.17,
        "question": 0.17,
        "social_proof": 0.08,
    }
    result = underused_high_durability(in_dist)
    assert result == ["social_proof"]


def test_underused_high_durability_descending_order_and_filtering() -> None:
    """Returns multiple qualified archetypes in descending durability order."""
    in_dist = {
        "bold_statement": 0.85,
        "social_proof": 0.05,
        "pain_point": 0.05,
        "question": 0.05,
    }
    # social_proof (2.1) > pain_point (1.5) > question (1.3) > mean (~1.27)
    result = underused_high_durability(in_dist)
    assert result == ["social_proof", "pain_point", "question"]


def test_underused_high_durability_only_present_in_in_dist() -> None:
    """Rule 5: Return only archetypes present in in_dist."""
    in_dist = {
        "bold_statement": 0.95,
        "social_proof": 0.05,
    }
    # Even though pain_point has durability 1.5 > mean, it is NOT in in_dist
    result = underused_high_durability(in_dist)
    assert result == ["social_proof"]
    assert "pain_point" not in result


def test_underused_high_durability_custom_durability() -> None:
    in_dist = {"arch_a": 0.05, "arch_b": 0.95}
    durability = {"arch_a": 3.0, "arch_b": 1.0}  # mean = 2.0
    result = underused_high_durability(in_dist, durability)
    assert result == ["arch_a"]


def test_underused_high_durability_empty_dist() -> None:
    assert underused_high_durability({}) == []


# ===========================================================================
# 6. hook_score_boost
# ===========================================================================


def test_hook_score_boost() -> None:
    """Verify boost values: +0.8 for underused high durability, +0.3 for others, 0.0 otherwise."""
    # Underused high durability
    assert hook_score_boost("social_proof") == 0.8
    assert hook_score_boost("pain_point") == 0.8
    assert hook_score_boost("question") == 0.8

    # Other hook archetypes
    assert hook_score_boost("bold_statement") == 0.3
    assert hook_score_boost("contrarian_stat") == 0.3
    assert hook_score_boost("pattern_interrupt") == 0.3
    assert hook_score_boost("visual_shock") == 0.3

    # Unknown
    assert hook_score_boost("unknown_archetype") == 0.0

    # With HookArchetype Enum input
    assert hook_score_boost(HookArchetype.SOCIAL_PROOF) == 0.8
    assert hook_score_boost(HookArchetype.BOLD_STATEMENT) == 0.3


# ===========================================================================
# 7. compute_durability
# ===========================================================================


def test_compute_durability_fallback_below_min_samples() -> None:
    hooks = [
        HookPattern(
            text=f"h{i}",
            archetype=HookArchetype.SOCIAL_PROOF,
            stop_power_score=8.0,
            why_it_stops_the_scroll="...",
        )
        for i in range(5)
    ]
    table, diag = compute_durability(hooks, min_samples=8)
    assert diag["durability_source"] == "default_table"
    assert table == DEFAULT_DURABILITY_TABLE


def test_compute_durability_measured_scores() -> None:
    # 8 hooks with stop_power_scores
    hooks = [
        HookPattern(
            text=f"h{i}",
            archetype=HookArchetype.CONTRARIAN_STAT if i < 4 else HookArchetype.SOCIAL_PROOF,
            stop_power_score=9.0 if i < 4 else 8.5,
            why_it_stops_the_scroll="...",
        )
        for i in range(8)
    ]
    table, diag = compute_durability(hooks, min_samples=8)
    assert diag["durability_source"] == "measured_stop_power"
    assert table["contrarian_stat"] == 9.0
    assert table["social_proof"] == 8.5
    # Missing archetypes fall back to default table values
    assert table["bold_statement"] == 1.0


# ===========================================================================
# 8. median_hook_duration
# ===========================================================================


def test_median_hook_duration() -> None:
    sheets = [
        [BeatEntry(beat=BeatName.HOOK, start_s=0.0, end_s=dur, what_happens="h")]
        for dur in [3.0, 3.2, 3.4, 3.6, 4.0]
    ]
    med = median_hook_duration(sheets)
    assert med == 3.4

    # Fallback when empty
    assert median_hook_duration([]) == 3.0


# ===========================================================================
# 9. Storyboard Validator 6 Integration
# ===========================================================================


def test_storyboard_validator_with_aggregated_timeline() -> None:
    """Ensure aggregate_beat_timeline output passes Storyboard validator 6."""
    data = _load_fixture("storyboard.json")

    # Generate a default timeline for 42.0s
    tl = default_timeline(42.0, tolerance_s=3.0)

    # Attach to validation_context
    data_with_ctx = dict(data)
    data_with_ctx["validation_context"] = {
        "median_beat_timeline": [
            {
                "beat": b.beat.value,
                "start_s": b.start_s,
                "end_s": b.end_s,
                "tolerance_s": b.tolerance_s,
            }
            for b in tl
        ]
    }

    # Should validate cleanly without raising
    sb = Storyboard.model_validate(data_with_ctx)
    assert sb.meta.total_duration_s == 42.0
