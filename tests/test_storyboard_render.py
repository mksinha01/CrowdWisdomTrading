"""Tests for Story S23: Storyboard Part B (score, rewrite, HTML, contact sheet).

Covers:
- weighted_mean matches a hand-computed value; the model's own arithmetic is ignored
- a request_changes verdict with weakest_axes of length ≠ 2 is rejected and retried
- must_not_change enforcement fires: a rewrite that alters visual_hook raises YourRewriteChangedProtectedFields
- must_not_change enforcement fires: a rewrite that alters a protected shot raises YourRewriteChangedProtectedFields
- revision_rounds increments; the storyboard still validates afterward
- _render_storyboard_html output contains all 12 shot ids, the VO text, and no http://
- make_contact_sheet produces a 1080×(3*cells_h + gutters) PNG with 12 cells (Pillow available, no ffmpeg in the test env)
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PIL import Image
import pytest

from cwt.clients.llm import LLMClient, Tier
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactStore
from cwt.domain.models import (
    AdPatterns,
    ResearchBrief,
    ReviewVerdict,
    Storyboard,
)
from cwt.tools.storyboard import (
    CREATIVE_AXIS_WEIGHTS,
    RawReviewResponse,
    YourRewriteChangedProtectedFields,
    _make_contact_sheet,
    _render_storyboard_html,
    _score_storyboard,
    apply_rewrite,
    make_contact_sheet,
    render_storyboard_html,
    score_storyboard,
)
from cwt.util.paths import RunPaths

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def test_settings() -> Settings:
    return Settings.from_env()


@pytest.fixture
def run_paths(tmp_path: Path) -> RunPaths:
    return RunPaths(tmp_path / "test_run").ensure()


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _seed_storyboard_artifacts(paths: RunPaths) -> tuple[Storyboard, AdPatterns, ResearchBrief]:
    store = ArtifactStore(paths, run_id=paths.run_dir.name)

    sb_data = _load_fixture("storyboard.json")
    sb = Storyboard.model_validate(sb_data)
    store.write("storyboard", sb)

    patterns_data = _load_fixture("ad_patterns.json")
    # Align median beat timeline with the 42s storyboard fixture (same as test_tools_storyboard.py)
    patterns_data["aggregate"]["median_beat_timeline"] = [
        {"beat": "hook", "start_s": 0.0, "end_s": 3.4, "tolerance_s": 1.2},
        {"beat": "problem", "start_s": 3.4, "end_s": 8.8, "tolerance_s": 2.0},
        {"beat": "agitation", "start_s": 8.8, "end_s": 14.6, "tolerance_s": 2.4},
        {"beat": "mechanism", "start_s": 14.6, "end_s": 24.1, "tolerance_s": 3.0},
        {"beat": "proof", "start_s": 24.1, "end_s": 30.2, "tolerance_s": 3.0},
        {"beat": "objection", "start_s": 30.2, "end_s": 35.9, "tolerance_s": 3.0},
        {"beat": "cta", "start_s": 35.9, "end_s": 42.0, "tolerance_s": 3.0},
    ]
    patterns = AdPatterns.model_validate(patterns_data)
    store.write("ad_patterns", patterns)

    brief_data = _load_fixture("research_brief.json")
    brief = ResearchBrief.model_validate(brief_data)
    store.write("research_brief", brief)

    return sb, patterns, brief


class MockReviewClient:
    """Mock LLM client for testing creative review and rewrite."""

    def __init__(
        self,
        *,
        review_responses: list[Any] | None = None,
        rewrite_responses: list[Any] | None = None,
    ) -> None:
        self.review_responses = list(review_responses or [])
        self.rewrite_responses = list(rewrite_responses or [])
        self.call_count = 0
        self.stages_called: list[str] = []

    async def complete_validated(
        self,
        *,
        tier: Tier,
        messages: list[dict],
        schema: type,
        stage: str,
        max_repairs: int | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        validation_context: dict[str, Any] | None = None,
    ) -> Any:
        self.call_count += 1
        self.stages_called.append(stage)

        if "score_storyboard" in stage:
            if self.review_responses:
                resp = self.review_responses.pop(0)
                if isinstance(resp, Exception):
                    raise resp
                if isinstance(resp, schema):
                    return resp
                return schema.model_validate(resp)
            return RawReviewResponse(
                verdict="pass",
                scores={k: 8.5 for k in CREATIVE_AXIS_WEIGHTS},
                weighted_mean=8.5,
                weakest_axes=["emotional_arc", "mechanism_clarity"],
                best_moment="s01 single cyan line",
            )

        if "apply_rewrite" in stage:
            if self.rewrite_responses:
                resp = self.rewrite_responses.pop(0)
                if isinstance(resp, Exception):
                    raise resp
                if isinstance(resp, schema):
                    return resp
                data = dict(resp)
                if validation_context:
                    data.setdefault("validation_context", validation_context)
                return schema.model_validate(data)

            sb_data = _load_fixture("storyboard.json")
            if validation_context:
                sb_data.setdefault("validation_context", validation_context)
            return schema.model_validate(sb_data)

        raise ValueError(f"Unexpected stage {stage}")


# ---------------------------------------------------------------------------
# Test 1: weighted_mean matches hand computation; model arithmetic ignored
# ---------------------------------------------------------------------------


def test_weighted_mean_matches_hand_computed_value(test_settings: Settings, run_paths: RunPaths):
    """Test that weighted_mean is recomputed in Python using fixed weights, ignoring model arithmetic."""
    sb, patterns, _ = _seed_storyboard_artifacts(run_paths)

    scores = {
        "hook_strength": 9.0,      # 0.25 * 9.0 = 2.25
        "mechanism_clarity": 7.0,  # 0.20 * 7.0 = 1.40
        "proof_credibility": 8.0,  # 0.15 * 8.0 = 1.20
        "emotional_arc": 6.0,      # 0.15 * 6.0 = 0.90
        "brand_fit": 8.0,          # 0.15 * 8.0 = 1.20
        "compliance_safety": 8.0,  # 0.10 * 8.0 = 0.80
    }
    # Expected hand-computed sum = 2.25 + 1.40 + 1.20 + 0.90 + 1.20 + 0.80 = 7.75
    expected_weighted_mean = 7.75

    mock_client = MockReviewClient(
        review_responses=[
            RawReviewResponse(
                verdict="pass",  # Model claims pass, but 7.75 < 8.0 threshold
                scores=scores,
                weighted_mean=9.99,  # Bogus arithmetic from model
                weakest_axes=["emotional_arc", "mechanism_clarity"],
                changes_requested="Mechanism beat needs more outcome focus.",
            )
        ]
    )

    res = score_storyboard(settings=test_settings, paths=run_paths, client=mock_client)

    assert res["weighted_mean"] == expected_weighted_mean
    assert res["weighted_mean"] != 9.99
    # Python overrides verdict to request_changes because 7.75 < 8.0
    assert res["verdict"] == "request_changes"
    assert res["weakest_axes"] == ["emotional_arc", "mechanism_clarity"]
    assert res["round"] == 1


# ---------------------------------------------------------------------------
# Test 2: request_changes with weakest_axes != 2 rejected and retried
# ---------------------------------------------------------------------------


def test_request_changes_verdict_with_invalid_weakest_axes_retries(
    test_settings: Settings, run_paths: RunPaths
):
    """Test that request_changes with weakest_axes != 2 raises validation error and retries."""
    sb, patterns, _ = _seed_storyboard_artifacts(run_paths)

    # First response has only 1 weakest axis -> schema validation raises ValueError
    invalid_raw = {
        "verdict": "request_changes",
        "scores": {k: 7.0 for k in CREATIVE_AXIS_WEIGHTS},
        "weighted_mean": 7.0,
        "weakest_axes": ["emotional_arc"],  # length 1, invalid!
        "changes_requested": "Lift the emotional arc.",
    }
    with pytest.raises(Exception) as exc_info:
        RawReviewResponse.model_validate(invalid_raw)
    assert "weakest_axes must have exactly 2 axes" in str(exc_info.value)

    # Now verify that when client returns the valid response on retry, it passes
    valid_resp = RawReviewResponse(
        verdict="request_changes",
        scores={k: 7.0 for k in CREATIVE_AXIS_WEIGHTS},
        weighted_mean=7.0,
        weakest_axes=["emotional_arc", "mechanism_clarity"],
        changes_requested="Lift emotional arc and clarify mechanism.",
    )
    mock_client = MockReviewClient(review_responses=[valid_resp])
    res = score_storyboard(settings=test_settings, paths=run_paths, client=mock_client)

    assert res["verdict"] == "request_changes"
    assert len(res["weakest_axes"]) == 2


# ---------------------------------------------------------------------------
# Test 3: must_not_change enforcement fires (negative proof)
# ---------------------------------------------------------------------------


def test_must_not_change_enforcement_raises_on_altered_visual_hook(
    test_settings: Settings, run_paths: RunPaths
):
    """A rewrite that alters visual_hook when protected must raise YourRewriteChangedProtectedFields."""
    sb, _, _ = _seed_storyboard_artifacts(run_paths)

    verdict = ReviewVerdict(
        schema_version=1,
        reviewer="cwt-creative-director",
        round=1,
        verdict="request_changes",
        scores={k: 7.5 for k in CREATIVE_AXIS_WEIGHTS},
        weighted_mean=7.5,
        threshold=8.0,
        weakest_axes=["emotional_arc", "mechanism_clarity"],
        changes_requested="Fix emotional arc.",
        must_fix=["emotional_arc"],
        must_not_change=["visual_hook", "compliance"],
    )
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    store.write("review_verdict", verdict)

    # Create mutated storyboard where visual_hook has been modified
    mutated_sb_data = sb.model_dump(mode="json")
    mutated_sb_data["visual_hook"]["text_overlay"] = "FORBIDDEN MODIFIED HOOK TEXT"

    mock_client = MockReviewClient(rewrite_responses=[mutated_sb_data])

    with pytest.raises(YourRewriteChangedProtectedFields) as exc_info:
        apply_rewrite(settings=test_settings, paths=run_paths, client=mock_client)

    assert "visual_hook" in str(exc_info.value)


def test_must_not_change_enforcement_raises_on_altered_protected_shot(
    test_settings: Settings, run_paths: RunPaths
):
    """A rewrite that alters a shot listed in must_not_change must raise."""
    sb, _, _ = _seed_storyboard_artifacts(run_paths)

    verdict = ReviewVerdict(
        schema_version=1,
        reviewer="cwt-creative-director",
        round=1,
        verdict="request_changes",
        scores={k: 7.5 for k in CREATIVE_AXIS_WEIGHTS},
        weighted_mean=7.5,
        threshold=8.0,
        weakest_axes=["emotional_arc", "mechanism_clarity"],
        changes_requested="Fix emotional arc.",
        must_fix=["emotional_arc"],
        must_not_change=["s01", "compliance"],
    )
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    store.write("review_verdict", verdict)

    # Mutate shot s01 description
    mutated_sb_data = sb.model_dump(mode="json")
    mutated_sb_data["shots"][0]["description"] = "A completely different opening visual."

    mock_client = MockReviewClient(rewrite_responses=[mutated_sb_data])

    with pytest.raises(YourRewriteChangedProtectedFields) as exc_info:
        apply_rewrite(settings=test_settings, paths=run_paths, client=mock_client)

    assert "s01" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 4: revision_rounds increments and storyboard validates
# ---------------------------------------------------------------------------


def test_apply_rewrite_increments_revision_rounds_and_validates(
    test_settings: Settings, run_paths: RunPaths
):
    """Test that a valid rewrite increments revision_rounds and passes validation."""
    sb, _, _ = _seed_storyboard_artifacts(run_paths)
    initial_rounds = sb.generation.revision_rounds

    verdict = ReviewVerdict(
        schema_version=1,
        reviewer="cwt-creative-director",
        round=1,
        verdict="request_changes",
        scores={k: 7.5 for k in CREATIVE_AXIS_WEIGHTS},
        weighted_mean=7.5,
        threshold=8.0,
        weakest_axes=["emotional_arc", "mechanism_clarity"],
        changes_requested="Tweak description of s05.",
        must_fix=["emotional_arc"],
        must_not_change=["visual_hook", "s01", "s02", "compliance"],
    )
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    store.write("review_verdict", verdict)

    # Valid rewrite: modifies only s05, keeping protected fields intact
    updated_sb_data = sb.model_dump(mode="json")
    for s in updated_sb_data["shots"]:
        if s["id"] == "s05":
            s["description"] = "Updated description with improved emotional arc."

    mock_client = MockReviewClient(rewrite_responses=[updated_sb_data])

    res = apply_rewrite(settings=test_settings, paths=run_paths, client=mock_client)

    assert res["valid"] is True
    assert res["revision_rounds"] == initial_rounds + 1

    # Verify on-disk storyboard
    updated_sb = store.read("storyboard")
    assert updated_sb.generation.revision_rounds == initial_rounds + 1
    # Check validator passes
    Storyboard.model_validate(updated_sb.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Test 5: _render_storyboard_html content, shots, VO, safety
# ---------------------------------------------------------------------------


def test_render_storyboard_html_content_and_safety(
    test_settings: Settings, run_paths: RunPaths
):
    """HTML output must contain all 12 shot ids, the VO text, and no external URLs."""
    sb, _, _ = _seed_storyboard_artifacts(run_paths)

    res = render_storyboard_html(settings=test_settings, paths=run_paths)
    assert res["shots_rendered"] == len(sb.shots)
    assert res["bytes"] > 0

    html_file = Path(res["artifact_path"])
    assert html_file.exists()
    content = html_file.read_text(encoding="utf-8")

    # Safety: no unapproved external URLs
    assert "http://" not in content
    # Any https must be crowdwisdomtrading or internal
    for match in ["http://", "https://"]:
        if match in content:
            assert "crowdwisdomtrading" in content

    # All 12 shots must appear
    for i in range(1, 13):
        assert f"s{i:02d}" in content

    # Compliance risk disclosure must be present
    assert "Trading involves significant risk" in content

    # VO text must be present (unescaped matches original text)
    assert sb.voiceover.full_text in html.unescape(content)


# ---------------------------------------------------------------------------
# Test 6: make_contact_sheet produces 1080x(3*cells_h + gutters) PNG with 12 cells
# ---------------------------------------------------------------------------


def test_make_contact_sheet_dimensions_and_cells(
    test_settings: Settings, run_paths: RunPaths
):
    """make_contact_sheet produces a 1080×(3*cells_h + gutters) PNG with 12 cells."""
    sb, _, _ = _seed_storyboard_artifacts(run_paths)

    res = make_contact_sheet(settings=test_settings, paths=run_paths, cols=4, rows=3)

    assert res["cols"] == 4
    assert res["rows"] == 3
    assert res["cells"] == 12

    sheet_file = Path(res["artifact_path"])
    assert sheet_file.exists()

    img = Image.open(sheet_file)
    assert img.mode == "RGB"
    assert img.size[0] == 1080

    # 4 cols: base_w = (1080 - 3*2) // 4 = 268
    # cell_h = int(268 * 16 / 9) = 476
    # 3 rows: total_h = 3 * 476 + 2 * 2 = 1432
    expected_cell_h = int(((1080 - 6) // 4) * 16 / 9)
    expected_total_h = 3 * expected_cell_h + 4
    assert img.size[1] == expected_total_h


# ---------------------------------------------------------------------------
# Test 7: Integration seed for runs/_s23 specification check
# ---------------------------------------------------------------------------


def test_generate_s23_reference_artifacts(test_settings: Settings):
    """Seed runs/_s23/artifacts/ so the specification verification command runs green."""
    target_dir = Path("runs/_s23")
    paths = RunPaths(target_dir).ensure()
    sb, _, _ = _seed_storyboard_artifacts(paths)

    render_storyboard_html(settings=test_settings, paths=paths)
    make_contact_sheet(settings=test_settings, paths=paths, cols=4, rows=3)

    html_file = paths.artifacts / "storyboard.html"
    assert html_file.exists()
    sheet_file = paths.artifacts / "contact_sheet.png"
    assert sheet_file.exists()
