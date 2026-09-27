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


# ===========================================================================
# S04: Storyboard Validators (doc/stories/S04-storyboard-validators.md)
# ===========================================================================


def test_validator_01_beats_covers_timeline() -> None:
    data = _load_fixture("storyboard.json")

    # Gap between beats: beat 1 starts at 4.0s (beat 0 ends at 3.4s -> 0.60s gap)
    d = json.loads(json.dumps(data))
    d["beats"][1]["start_s"] = 4.0
    with pytest.raises(ValidationError, match=r"Gap of 0\.60s between beat 'hook'.*and 'problem'"):
        Storyboard.model_validate(d)

    # Overlap between beats: beat 1 starts at 3.0s (beat 0 ends at 3.4s -> 0.40s overlap)
    d = json.loads(json.dumps(data))
    d["beats"][1]["start_s"] = 3.0
    with pytest.raises(ValidationError, match=r"Overlap of 0\.40s between beat 'hook'.*and 'problem'"):
        Storyboard.model_validate(d)

    # Beat 0 does not start at 0.0s
    d = json.loads(json.dumps(data))
    d["beats"][0]["start_s"] = 0.5
    with pytest.raises(ValidationError, match="gap: 0.50s"):
        Storyboard.model_validate(d)

    # Last beat does not reach total_duration_s
    d = json.loads(json.dumps(data))
    d["beats"][-1]["end_s"] = 40.0
    with pytest.raises(ValidationError, match="Gap of 2.00s"):
        Storyboard.model_validate(d)


def test_validator_02_shots_match_beats() -> None:
    data = _load_fixture("storyboard.json")

    # Shot s01 exceeds hook beat end (ends at 4.0s, hook ends at 3.4s)
    d = json.loads(json.dumps(data))
    d["shots"][0]["duration_s"] = 4.0
    with pytest.raises(ValidationError, match=r"Shot 's01' range \[0\.00s, 4\.00s\) falls outside declared beat 'hook'"):
        Storyboard.model_validate(d)

    # Shot s01 starts before hook beat start
    d = json.loads(json.dumps(data))
    d["shots"][0]["start_s"] = -0.5
    with pytest.raises(ValidationError, match=r"Shot 's01'.*falls outside declared beat 'hook'"):
        Storyboard.model_validate(d)


def test_validator_03_duration_in_bounds() -> None:
    data = _load_fixture("storyboard.json")

    # Below minimum (e.g. 20s < 30s)
    d = json.loads(json.dumps(data))
    d["meta"]["total_duration_s"] = 20.0
    # Scale beats and shots to 20.0 so validator 1 & 4 pass
    d["beats"] = [
        {"beat": "hook", "start_s": 0.0, "end_s": 2.5, "tolerance_s": 1.2, "on_target": True},
        {"beat": "cta", "start_s": 2.5, "end_s": 20.0, "tolerance_s": 3.0, "on_target": True},
    ]
    d["shots"] = [
        {**d["shots"][0], "id": "s01", "beat": "hook", "start_s": 0.0, "duration_s": 2.5},
        {**d["shots"][-1], "id": "s12", "beat": "cta", "start_s": 2.5, "duration_s": 17.5},
    ]
    d["voiceover"]["segments"] = [
        {"shot_id": "s01", "text": "Too many voices.", "start_s": 0.0, "end_s": 2.0},
        {"shot_id": "s12", "text": "Collective intelligence.", "start_s": 3.0, "end_s": 15.0},
    ]
    with pytest.raises(ValidationError, match=r"total_duration_s 20\.00s outside bounds \[30\.00s, 60\.00s\]"):
        Storyboard.model_validate(d)

    # Above maximum (e.g. 70s > 60s)
    d = json.loads(json.dumps(data))
    d["meta"]["total_duration_s"] = 70.0
    d["beats"][-1]["end_s"] = 70.0
    d["shots"][-1]["duration_s"] = 6.1 + (70.0 - 42.0)
    with pytest.raises(ValidationError, match=r"total_duration_s 70\.00s outside bounds \[30\.00s, 60\.00s\]"):
        Storyboard.model_validate(d)


def test_validator_04_shot_durations_sum() -> None:
    data = _load_fixture("storyboard.json")

    # Shorten s03 by 0.2s: s03 is [3.4, 8.6) which still fits in problem beat [3.4, 8.8),
    # but total shot durations sum to 41.80s != 42.00s (delta 0.20s > 0.05s)
    d = json.loads(json.dumps(data))
    d["shots"][2]["duration_s"] = 5.2
    with pytest.raises(
        ValidationError,
        match=r"Shot durations sum 41\.80s does not match total_duration_s 42\.00s \(delta: 0\.20s, tolerance: 0\.05s\)",
    ):
        Storyboard.model_validate(d)


def test_validator_05_hook_within_three_seconds() -> None:
    data = _load_fixture("storyboard.json")

    # Hook ends at 4.5s with tolerance 1.2 (3.0 + 1.2 = 4.2 < 4.5s)
    d = json.loads(json.dumps(data))
    d["beats"][0]["end_s"] = 4.5
    d["beats"][1]["start_s"] = 4.5
    d["shots"][0]["duration_s"] = 4.5
    d["shots"][1]["start_s"] = 4.5
    d["shots"][2]["start_s"] = 4.5
    d["shots"][2]["duration_s"] = 4.3  # ends at 8.8
    with pytest.raises(
        ValidationError,
        match=r"Hook beat ends at 4\.50s, exceeding maximum allowed 3\.0s \+ tolerance 1\.20s \(4\.20s\)",
    ):
        Storyboard.model_validate(d)


def test_validator_06_beats_within_median_tolerance() -> None:
    data = _load_fixture("storyboard.json")

    # Validating without validation_context skips the check and records a warning
    sb = Storyboard.model_validate(data)
    assert any("Validator beats_within_median_tolerance skipped" in w for w in sb.warnings)

    # Validating with validation_context where problem beat deviates by 4.60s (> 1.00s tolerance)
    d = json.loads(json.dumps(data))
    d["validation_context"] = {
        "median_beat_timeline": [
            {"beat": "hook", "start_s": 0.0, "tolerance_s": 1.2},
            {"beat": "problem", "start_s": 8.0, "tolerance_s": 1.0},
        ]
    }
    with pytest.raises(
        ValidationError,
        match=r"Beat 'problem' start 3\.40s deviates from mined median 8\.00s by 4\.60s, exceeding tolerance 1\.00s",
    ):
        Storyboard.model_validate(d)


def test_validator_07_palette_is_dark_and_accented() -> None:
    data = _load_fixture("storyboard.json")

    # All greys: lacks both near-black (< 0x30) and accent (> 0x80)
    d = json.loads(json.dumps(data))
    d["shots"][0]["palette"] = ["#404040", "#505050", "#606060", "#707070"]
    with pytest.raises(ValidationError, match="Shot 's01' palette lacks near-black and accent color"):
        Storyboard.model_validate(d)

    # Dark only: lacks accent
    d = json.loads(json.dumps(data))
    d["shots"][0]["palette"] = ["#050505", "#101010", "#181818", "#202020"]
    with pytest.raises(ValidationError, match="Shot 's01' palette lacks accent color"):
        Storyboard.model_validate(d)

    # Bright only: lacks near-black
    d = json.loads(json.dumps(data))
    d["shots"][0]["palette"] = ["#ffffff", "#ef4444", "#22d3ee", "#38bdf8"]
    with pytest.raises(ValidationError, match="Shot 's01' palette lacks near-black color"):
        Storyboard.model_validate(d)


def test_validator_08_text_within_safe_area() -> None:
    data = _load_fixture("storyboard.json")

    # On-screen text at position 'top' (y=0.08) sits outside top margin (0.15)
    d = json.loads(json.dumps(data))
    d["shots"][1]["on_screen_text"][0]["position"] = "top"
    with pytest.raises(
        ValidationError,
        match=r"Shot 's02' on-screen text 'TOO MANY VOICES\.' at position 'top'.*sits outside declared safe area",
    ):
        Storyboard.model_validate(d)

    # On-screen text at position 'bottom' (y=0.90) sits outside bottom margin (1 - 0.25 = 0.75)
    d = json.loads(json.dumps(data))
    d["shots"][1]["on_screen_text"][0]["position"] = "bottom"
    with pytest.raises(
        ValidationError,
        match=r"Shot 's02' on-screen text 'TOO MANY VOICES\.' at position 'bottom'.*sits outside declared safe area",
    ):
        Storyboard.model_validate(d)


def test_validator_09_risk_disclosure_present() -> None:
    data = _load_fixture("storyboard.json")
    d = json.loads(json.dumps(data))
    d["compliance"]["risk_disclosure_present"] = False
    with pytest.raises(
        ValidationError,
        match="Rule C1 violation: compliance.risk_disclosure_present must be True",
    ):
        Storyboard.model_validate(d)


def test_validator_10_no_prohibited_facts() -> None:
    data = _load_fixture("storyboard.json")

    # Full text contains prohibited numeric variant '74.1%'
    d = json.loads(json.dumps(data))
    d["voiceover"]["full_text"] = "We achieve 74.1% on all calls. " + d["voiceover"]["full_text"]
    with pytest.raises(
        ValidationError,
        match=r"Prohibited fact.*74\.1%.*matched in voiceover full_text",
    ):
        Storyboard.model_validate(d)

    # VO segment contains '74%'
    d = json.loads(json.dumps(data))
    d["voiceover"]["segments"][0]["text"] = "74% win rate guaranteed."
    with pytest.raises(
        ValidationError,
        match=r"Prohibited fact.*matched in voiceover segment for shot 's01'",
    ):
        Storyboard.model_validate(d)

    # Shot on-screen text contains '16,564'
    d = json.loads(json.dumps(data))
    d["shots"][1]["on_screen_text"][0]["text"] = "16,564 TRADERS."
    with pytest.raises(
        ValidationError,
        match=r"Prohibited fact.*16,564.*matched in shot 's02' on-screen text",
    ):
        Storyboard.model_validate(d)


def test_validator_11_voiceover_matches_shots() -> None:
    data = _load_fixture("storyboard.json")

    # VO segment references nonexistent shot id
    d = json.loads(json.dumps(data))
    d["voiceover"]["segments"][0]["shot_id"] = "s99"
    with pytest.raises(
        ValidationError,
        match="Voiceover segment references nonexistent shot_id 's99'",
    ):
        Storyboard.model_validate(d)

    # VO duration exceeds video duration (overrun)
    d = json.loads(json.dumps(data))
    d["voiceover"]["segments"][-1]["end_s"] = 50.0  # causes overrun > 42.05s
    with pytest.raises(
        ValidationError,
        match=r"Summed voiceover segment duration.*exceeds video duration 42\.00s by.*overrun",
    ):
        Storyboard.model_validate(d)

