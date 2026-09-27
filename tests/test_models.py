"""Tests for artifact schema models and version gating.

Story S03 — Artifact schema models.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cwt.domain.models import (
    SCHEMA_VERSION,
    AdPatterns,
    AngleName,
    ArtifactBase,
    ArtifactVersionError,
    AssetRef,
    BeatEntry,
    BeatName,
    Camera,
    CameraMove,
    ClaimFinding,
    ClaimsReport,
    Composition,
    HookArchetype,
    HookCandidates,
    Lighting,
    RenderManifest,
    ResearchBrief,
    ReviewVerdict,
    Severity,
    Shot,
    Storyboard,
    SubjectName,
    Transition,
    TransitionName,
    WinningAds,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(filename: str) -> dict[str, Any]:
    path = FIXTURES_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


# ===========================================================================
# Round-trip tests for all eight artifacts (§3.1 - §3.5)
# ===========================================================================


def test_winning_ads_roundtrip() -> None:
    data = _load_fixture("winning_ads.json")
    model = WinningAds.model_validate(data)
    assert model.schema_version == SCHEMA_VERSION
    assert len(model.ads) == 1
    assert model.ads[0].ad_id == "1234567890123"
    assert model.source.actor_id == "apify~facebook-ads-scraper"
    assert model.model_dump(mode="json") == data


def test_ad_patterns_roundtrip() -> None:
    data = _load_fixture("ad_patterns.json")
    model = AdPatterns.model_validate(data)
    assert model.schema_version == SCHEMA_VERSION
    assert len(model.patterns) == 1
    assert model.patterns[0].hook.archetype == HookArchetype.CONTRARIAN_STAT
    assert len(model.aggregate.median_beat_timeline) == 6
    assert model.model_dump(mode="json") == data


def test_research_brief_roundtrip() -> None:
    data = _load_fixture("research_brief.json")
    model = ResearchBrief.model_validate(data)
    assert model.schema_version == SCHEMA_VERSION
    assert AngleName.PAIN in model.angles
    assert AngleName.UNIQUE_DATA in model.angles
    assert AngleName.CROWD_EFFECT in model.angles
    assert model.product.name == "CrowdWisdomTrading"
    assert len(model.prohibited_facts) == 3
    assert model.model_dump(mode="json") == data


def test_storyboard_roundtrip() -> None:
    data = _load_fixture("storyboard.json")
    model = Storyboard.model_validate(data)
    assert model.schema_version == SCHEMA_VERSION
    assert model.meta.total_duration_s == 42.0
    assert len(model.beats) == 7
    assert len(model.shots) == 12
    assert len(model.voiceover.segments) == 12
    assert model.visual_hook.archetype == HookArchetype.VISUAL_SHOCK
    assert model.compliance.risk_disclosure_present is True
    assert model.generation.winner == AngleName.UNIQUE_DATA
    assert model.model_dump(mode="json") == data


def test_hook_candidates_roundtrip() -> None:
    data = _load_fixture("hook_candidates.json")
    model = HookCandidates.model_validate(data)
    assert model.schema_version == SCHEMA_VERSION
    assert len(model.candidates) == 1
    assert model.candidates[0].archetype == HookArchetype.PATTERN_INTERRUPT
    assert model.model_dump(mode="json") == data


def test_review_verdict_roundtrip() -> None:
    data = _load_fixture("review_verdict.json")
    model = ReviewVerdict.model_validate(data)
    assert model.schema_version == SCHEMA_VERSION
    assert model.verdict == "request_changes"
    assert model.scores["hook_strength"] == 9.1
    assert model.model_dump(mode="json") == data


def test_claims_report_roundtrip() -> None:
    data = _load_fixture("claims_report.json")
    model = ClaimsReport.model_validate(data)
    assert model.schema_version == SCHEMA_VERSION
    assert model.stage == "pre_render"
    assert model.verdict == "request_changes"
    finding = ClaimFinding.model_validate(model.deterministic["findings"][0])
    assert finding.severity == Severity.HARD
    assert finding.rule_id == "unverifiable_statistic"
    assert model.model_dump(mode="json") == data


def test_render_manifest_roundtrip() -> None:
    data = _load_fixture("render_manifest.json")
    model = RenderManifest.model_validate(data)
    assert model.schema_version == SCHEMA_VERSION
    assert model.backend_used == "local_ffmpeg"
    assert len(model.backend_chain_tried) == 3
    assert model.output["width"] == 1080
    assert model.model_dump(mode="json") == data


# ===========================================================================
# Schema Version Gate Tests
# ===========================================================================


def test_version_gate_validates_current_version() -> None:
    base = ArtifactBase(schema_version=SCHEMA_VERSION)
    assert base.schema_version == SCHEMA_VERSION


def test_version_gate_rejects_future_version_on_storyboard() -> None:
    data = _load_fixture("storyboard.json")
    data["schema_version"] = 99
    with pytest.raises(ArtifactVersionError, match="Unsupported schema_version 99"):
        Storyboard.model_validate(data)


def test_version_gate_rejects_future_version_on_all_artifacts() -> None:
    artifacts_fixtures: list[tuple[str, type[ArtifactBase]]] = [
        ("winning_ads.json", WinningAds),
        ("ad_patterns.json", AdPatterns),
        ("research_brief.json", ResearchBrief),
        ("hook_candidates.json", HookCandidates),
        ("review_verdict.json", ReviewVerdict),
        ("claims_report.json", ClaimsReport),
        ("render_manifest.json", RenderManifest),
    ]
    for filename, model_cls in artifacts_fixtures:
        data = _load_fixture(filename)
        data["schema_version"] = 2
        with pytest.raises(ArtifactVersionError, match="Unsupported schema_version 2"):
            model_cls.model_validate(data)


# ===========================================================================
# Extra fields forbidden tests (model_config = ConfigDict(extra="forbid"))
# ===========================================================================


def test_extra_fields_forbidden_on_artifact_models() -> None:
    data = _load_fixture("winning_ads.json")
    data["unknown_extra_key"] = "forbidden_value"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        WinningAds.model_validate(data)


def test_extra_fields_forbidden_on_nested_models() -> None:
    data = _load_fixture("storyboard.json")
    data["shots"][0]["bogus_property"] = 123
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Storyboard.model_validate(data)


def test_extra_fields_forbidden_on_shot() -> None:
    sb_data = _load_fixture("storyboard.json")
    shot_data = dict(sb_data["shots"][0])
    shot_data["extra_shot_field"] = "not_allowed"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Shot.model_validate(shot_data)


# ===========================================================================
# Enums and constraint tests
# ===========================================================================


def test_enums_str_membership() -> None:
    assert BeatName.HOOK == "hook"
    assert BeatName.CTA == "cta"
    assert HookArchetype.CONTRARIAN_STAT == "contrarian_stat"
    assert CameraMove.PUSH_IN == "push_in"
    assert TransitionName.FLASH_WHITE == "flash_white"
    assert SubjectName.ABSTRACT_MARKET_DATA == "abstract_market_data"
    assert AngleName.UNIQUE_DATA == "unique_data"
    assert Severity.HARD == "hard"
    assert Severity.SOFT == "soft"


def test_invalid_enum_rejected() -> None:
    with pytest.raises(ValidationError):
        BeatEntry(beat="invalid_beat", start_s=0.0, end_s=1.0, what_happens="text")  # type: ignore[arg-type]


def test_shot_palette_constraints() -> None:
    valid_palette = ["#050505", "#1a1a1a", "#ef4444", "#22d3ee"]
    # Less than 4 items
    with pytest.raises(ValidationError):
        Shot(
            id="s99",
            beat=BeatName.HOOK,
            start_s=0.0,
            duration_s=1.0,
            description="test",
            subject=SubjectName.ABSTRACT_MARKET_DATA,
            asset=AssetRef(kind="gen", ref="ref", source="internal"),
            camera=Camera(
                move=CameraMove.STATIC,
                intensity=0.0,
                lens_mm=50,
                depth_of_field="deep",
                stabilisation="locked",
            ),
            lighting=Lighting(key="none", contrast="high", colour_temp_k=6500),
            palette=valid_palette[:3],  # type: ignore[arg-type]
            composition=Composition(
                framing="centre", text_safe_area={"top": 0.1, "bottom": 0.2}
            ),
            transition_in=Transition(type=TransitionName.CUT, duration_s=0.0),
            transition_out=Transition(type=TransitionName.CUT, duration_s=0.0),
        )

    # Invalid hex string
    with pytest.raises(ValidationError):
        Shot(
            id="s99",
            beat=BeatName.HOOK,
            start_s=0.0,
            duration_s=1.0,
            description="test",
            subject=SubjectName.ABSTRACT_MARKET_DATA,
            asset=AssetRef(kind="gen", ref="ref", source="internal"),
            camera=Camera(
                move=CameraMove.STATIC,
                intensity=0.0,
                lens_mm=50,
                depth_of_field="deep",
                stabilisation="locked",
            ),
            lighting=Lighting(key="none", contrast="high", colour_temp_k=6500),
            palette=["#050505", "#1a1a1a", "not_a_hex", "#22d3ee"],
            composition=Composition(
                framing="centre", text_safe_area={"top": 0.1, "bottom": 0.2}
            ),
            transition_in=Transition(type=TransitionName.CUT, duration_s=0.0),
            transition_out=Transition(type=TransitionName.CUT, duration_s=0.0),
        )


def test_camera_intensity_bounds() -> None:
    with pytest.raises(ValidationError):
        Camera(
            move=CameraMove.STATIC,
            intensity=1.5,
            lens_mm=50,
            depth_of_field="deep",
            stabilisation="locked",
        )
    with pytest.raises(ValidationError):
        Camera(
            move=CameraMove.STATIC,
            intensity=-0.1,
            lens_mm=50,
            depth_of_field="deep",
            stabilisation="locked",
        )
