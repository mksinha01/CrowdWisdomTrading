"""Tests for src/cwt/hermes/dag.py — topological sort, parent wiring, assignees, skills.

NOTE ON CARD COUNT:
The spec prose at §9.1 line 3341 and the "Seeding 11 cards" banner in §9.7 say
11 cards. The actual DAG_SPEC defines 12. The data is the source of truth because
`wait_for_completion` (S28) compares `len(done) == len(DAG_SPEC)`. 12 is the
self-consistent count; the prose mentions are a known defect in the specification.
"""
from __future__ import annotations

import re
import pytest

from cwt.hermes.dag import CardSpec, DAG_SPEC, topo_sort


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KEYS = {c.key for c in DAG_SPEC}
_BY_KEY = {c.key: c for c in DAG_SPEC}

# The nine agent profiles S31 creates (Rule K2).
_VALID_ASSIGNEE_RE = re.compile(
    r"^cwt-(orchestrator|ads-manager|hook-analyst|researcher"
    r"|script-writer|creative-director|compliance|video-editor|qa)$"
)

# The eight skill directories S31 creates (must match skills/ directory names).
_VALID_SKILLS = {
    "cwt-source-winning-ads",
    "cwt-extract-ad-patterns",
    "cwt-research-angle",
    "cwt-write-storyboard",
    "cwt-creative-review",
    "cwt-claims-gate",
    "cwt-render-video",
    "cwt-final-qa",
}


# ---------------------------------------------------------------------------
# 1. Card count — 12, not 11 (see module docstring for rationale)
# ---------------------------------------------------------------------------

def test_dag_has_twelve_cards():
    # The spec prose says "11 cards" but the data structure defines 12.
    # This test documents the known defect and asserts the correct count.
    assert len(DAG_SPEC) == 12, (
        f"Expected 12 cards (spec prose is wrong — see module docstring), "
        f"got {len(DAG_SPEC)}"
    )


# ---------------------------------------------------------------------------
# 2. All keys are unique
# ---------------------------------------------------------------------------

def test_all_keys_unique():
    keys = [c.key for c in DAG_SPEC]
    assert len(keys) == len(set(keys)), "Duplicate card keys found"


# ---------------------------------------------------------------------------
# 3. Every parent reference names a card that exists
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("card", DAG_SPEC, ids=lambda c: c.key)
def test_parents_exist(card: CardSpec):
    for parent in card.parents:
        assert parent in _KEYS, (
            f"Card {card.key!r} references unknown parent {parent!r}"
        )


# ---------------------------------------------------------------------------
# 4. topo_sort returns all 12 cards in valid topological order
# ---------------------------------------------------------------------------

def test_topo_sort_returns_all_cards():
    result = topo_sort()
    assert len(result) == 12
    assert {c.key for c in result} == _KEYS


def test_topo_sort_valid_order():
    order = topo_sort()
    position = {c.key: i for i, c in enumerate(order)}
    for card in DAG_SPEC:
        for parent in card.parents:
            assert position[parent] < position[card.key], (
                f"Parent {parent!r} must appear before child {card.key!r}"
            )


def test_topo_sort_specific_orderings():
    """Spot-check the critical orderings from the story's 'Done when' block."""
    order = [c.key for c in topo_sort()]
    assert order.index("brief") > order.index("res_crowd")
    assert order.index("brief") > order.index("res_pain")
    assert order.index("brief") > order.index("res_unique")
    assert order.index("compliance") > order.index("script")
    assert order.index("render") > order.index("compliance")
    assert order.index("qa") > order.index("render")
    assert order.index("collect") > order.index("qa")


# ---------------------------------------------------------------------------
# 5. topo_sort is stable — two calls return identical key sequences
# ---------------------------------------------------------------------------

def test_topo_sort_is_stable():
    first = [c.key for c in topo_sort()]
    second = [c.key for c in topo_sort()]
    assert first == second, "topo_sort() must be deterministic across calls"


def test_topo_sort_stable_on_custom_spec():
    """Stability must hold even for subsets (used by S28 resume logic)."""
    subset = [c for c in DAG_SPEC if c.key not in ("collect",)]
    first = [c.key for c in topo_sort(subset)]
    second = [c.key for c in topo_sort(subset)]
    assert first == second


# ---------------------------------------------------------------------------
# 6. topo_sort raises on a cycle
# ---------------------------------------------------------------------------

def test_topo_sort_raises_on_cycle():
    cycle_spec = [
        CardSpec(key="a", title="A", assignee="cwt-orchestrator", parents=("b",)),
        CardSpec(key="b", title="B", assignee="cwt-orchestrator", parents=("a",)),
    ]
    with pytest.raises(ValueError, match="cycle"):
        topo_sort(cycle_spec)


# ---------------------------------------------------------------------------
# 7. topo_sort raises on an unknown parent
# ---------------------------------------------------------------------------

def test_topo_sort_raises_on_unknown_parent():
    bad_spec = [
        CardSpec(key="x", title="X", assignee="cwt-orchestrator", parents=("nonexistent",)),
    ]
    with pytest.raises(ValueError, match="unknown parent"):
        topo_sort(bad_spec)


# ---------------------------------------------------------------------------
# 8. Every assignee matches the nine valid profile names (Rule K2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("card", DAG_SPEC, ids=lambda c: c.key)
def test_assignee_matches_valid_profile(card: CardSpec):
    assert _VALID_ASSIGNEE_RE.match(card.assignee), (
        f"Card {card.key!r} has assignee {card.assignee!r} which does not match "
        f"any profile S31 creates. Unresolvable assignees hang silently (Rule K2)."
    )


def test_creative_director_not_an_assignee():
    """cwt-creative-director is a *reviewer*, not an assignee (spec line 3360).
    It appears in card bodies but must never be an `assignee` field value.
    This is expected per spec: 8 of the 9 profiles are assignees."""
    assignees = {c.assignee for c in DAG_SPEC}
    assert "cwt-creative-director" not in assignees, (
        "cwt-creative-director should be a reviewer only, not an assignee"
    )


# ---------------------------------------------------------------------------
# 9. Every skills entry is one of the eight valid skill names (S31)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("card", DAG_SPEC, ids=lambda c: c.key)
def test_skills_are_valid(card: CardSpec):
    for skill in card.skills:
        assert skill in _VALID_SKILLS, (
            f"Card {card.key!r} references unknown skill {skill!r}. "
            f"Valid skills: {sorted(_VALID_SKILLS)}"
        )


# ---------------------------------------------------------------------------
# 10. Structural spot checks on the exact parent wiring from the story table
# ---------------------------------------------------------------------------

def test_root_has_no_parents():
    assert _BY_KEY["root"].parents == ()


def test_root_is_goal():
    assert _BY_KEY["root"].goal is True


def test_ads_parent_is_root():
    assert _BY_KEY["ads"].parents == ("root",)


def test_patterns_parent_is_ads():
    assert _BY_KEY["patterns"].parents == ("ads",)


def test_res_pain_parent_is_patterns():
    assert _BY_KEY["res_pain"].parents == ("patterns",)


def test_res_unique_parent_is_patterns():
    assert _BY_KEY["res_unique"].parents == ("patterns",)


def test_res_crowd_parent_is_patterns():
    assert _BY_KEY["res_crowd"].parents == ("patterns",)


def test_brief_parents_are_three_research_cards():
    assert set(_BY_KEY["brief"].parents) == {"res_pain", "res_unique", "res_crowd"}


def test_script_parent_is_brief():
    assert _BY_KEY["script"].parents == ("brief",)


def test_compliance_parent_is_script():
    assert _BY_KEY["compliance"].parents == ("script",)


def test_render_parent_is_compliance():
    assert _BY_KEY["render"].parents == ("compliance",)


def test_qa_parent_is_render():
    assert _BY_KEY["qa"].parents == ("render",)


def test_collect_parent_is_qa():
    assert _BY_KEY["collect"].parents == ("qa",)


# ---------------------------------------------------------------------------
# 11. CardSpec is frozen (immutable)
# ---------------------------------------------------------------------------

def test_cardspec_is_frozen():
    card = CardSpec(key="test", title="Test", assignee="cwt-orchestrator")
    with pytest.raises((AttributeError, TypeError)):
        card.key = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 12. All 12 expected keys are present
# ---------------------------------------------------------------------------

def test_expected_keys_present():
    expected = {
        "root", "ads", "patterns",
        "res_pain", "res_unique", "res_crowd",
        "brief", "script", "compliance",
        "render", "qa", "collect",
    }
    assert _KEYS == expected
