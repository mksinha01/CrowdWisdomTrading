"""Tests for tools.storyboard (Story S22).

Covers:
- The boost raises a social_proof candidate's score and clamps at 10.0 (WOW-5)
- A response with only one social_proof candidate triggers exactly one regeneration
- A variant whose beats violate the median tolerance raises ValidationError naming the beat (validator 6)
- A prohibited fact in a loser variant is detected before splicing (validator 10)
- _apply_splices produces a storyboard where validators 1, 2, 4 still pass
- beats_stolen_from records the splice source angle (WOW-3)
- An unintegrable splice is dropped and warned, and the result still parses
- generate_hook_candidates tool contract
- write_storyboard_variant tool contract (scratch output, camera move, safe harbour)
- judge_variants tool contract (winner written, review_verdict NOT written)
- Rule A6: BudgetExceeded propagates
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cwt.clients.llm import (
    ArtifactValidationError,
    BudgetExceeded,
    LLMClient,
    Tier,
)
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactStore
from cwt.domain.models import (
    DEFAULT_PROHIBITED_FACTS,
    AdPatterns,
    AngleName,
    BeatName,
    CreativeScores,
    GenerationBlock,
    HookArchetype,
    HookCandidate,
    HookCandidates,
    ResearchBrief,
    Shot,
    Storyboard,
    VariantRecord,
)
from cwt.tools.storyboard import (
    JudgeResult,
    RawHookCandidate,
    RawHookCandidatesResponse,
    SpliceDirective,
    VariantJudgeScore,
    _apply_splices,
    _generate_hook_candidates,
    _judge_variants,
    _write_storyboard_variant,
    generate_hook_candidates,
    judge_variants,
    write_storyboard_variant,
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


def _seed_prerequisites(paths: RunPaths) -> tuple[ResearchBrief, AdPatterns]:
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    brief_data = _load_fixture("research_brief.json")
    brief = ResearchBrief.model_validate(brief_data)
    store.write("research_brief", brief)

    patterns_data = _load_fixture("ad_patterns.json")
    # Align median beat timeline with the 42s storyboard fixture
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
    return brief, patterns


def _create_sample_raw_candidates(
    *,
    social_proof_count: int = 2,
    pain_point_count: int = 2,
    sp_score: float = 9.5,
) -> RawHookCandidatesResponse:
    cands: list[RawHookCandidate] = []
    idx = 1

    # social_proof candidates
    for _ in range(social_proof_count):
        cands.append(
            RawHookCandidate(
                id=f"h{idx:02d}",
                archetype=HookArchetype.SOCIAL_PROOF,
                text_overlay=f"THEY DISAGREE {idx}",
                first_frame_description="Trader reviewing crowd consensus chart.",
                sound_design="Subtle hum.",
                stop_power_score=sp_score,
                why_it_stops_the_scroll="Social proof mechanism.",
            )
        )
        idx += 1

    # pain_point candidates
    for _ in range(pain_point_count):
        cands.append(
            RawHookCandidate(
                id=f"h{idx:02d}",
                archetype=HookArchetype.PAIN_POINT,
                text_overlay=f"SIGNAL OVERLOAD {idx}",
                first_frame_description="Overwhelmed trader staring at red charts.",
                sound_design="Static noise.",
                stop_power_score=8.5,
                why_it_stops_the_scroll="Directly addresses emotional fatigue.",
            )
        )
        idx += 1

    other_archs = [
        HookArchetype.PATTERN_INTERRUPT,
        HookArchetype.CONTRARIAN_STAT,
        HookArchetype.QUESTION,
        HookArchetype.VISUAL_SHOCK,
    ]
    while len(cands) < 12:
        arch = other_archs[len(cands) % len(other_archs)]
        cands.append(
            RawHookCandidate(
                id=f"h{idx:02d}",
                archetype=arch,
                text_overlay=f"HOOK {idx}",
                first_frame_description="Striking visual opening.",
                sound_design="Clean synth sweep.",
                stop_power_score=8.0,
                why_it_stops_the_scroll="Pattern interruption.",
            )
        )
        idx += 1

    return RawHookCandidatesResponse(candidates=cands[:12])


class MockLLMClient:
    """Mock LLMClient for storyboard tests."""

    def __init__(
        self,
        *,
        hook_responses: list[Any] | None = None,
        storyboard_responses: dict[str, Any] | None = None,
        judge_response: Any | None = None,
        budget_exceeded: bool = False,
    ):
        self.hook_responses = list(hook_responses or [])
        self.storyboard_responses = storyboard_responses or {}
        self.judge_response = judge_response
        self.budget_exceeded = budget_exceeded
        self.call_count = 0
        self.stages_called: list[str] = []
        self._sem = asyncio.Semaphore(4)
        self.run_cost_usd = 0.0

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

        if self.budget_exceeded:
            raise BudgetExceeded(spent=3.50, cap=3.00, stage=stage)

        if "generate_hook_candidates" in stage:
            if self.hook_responses:
                resp = self.hook_responses.pop(0)
                if isinstance(resp, Exception):
                    raise resp
                if isinstance(resp, schema):
                    return resp
                return schema.model_validate(resp)
            return _create_sample_raw_candidates()

        if "storyboard_variant" in stage:
            for angle_key, resp in self.storyboard_responses.items():
                if angle_key in stage:
                    if isinstance(resp, Exception):
                        raise resp
                    if isinstance(resp, schema):
                        return resp
                    data = dict(resp)
                    if validation_context:
                        data.setdefault("validation_context", validation_context)
                    return schema.model_validate(data)

            # Default fixture storyboard adapted to angle
            sb_data = _load_fixture("storyboard.json")
            if validation_context:
                sb_data.setdefault("validation_context", validation_context)
            return schema.model_validate(sb_data)

        if "judge_variants" in stage:
            if self.judge_response:
                if isinstance(self.judge_response, Exception):
                    raise self.judge_response
                if isinstance(self.judge_response, schema):
                    return self.judge_response
                return schema.model_validate(self.judge_response)

            return JudgeResult(
                variants=[
                    VariantJudgeScore(
                        angle=AngleName.PAIN,
                        scores={"hook_strength": 8.0, "mechanism_clarity": 7.5},
                        weighted_mean=7.75,
                        strongest_shot_id="s03",
                    ),
                    VariantJudgeScore(
                        angle=AngleName.UNIQUE_DATA,
                        scores={"hook_strength": 9.0, "mechanism_clarity": 8.8},
                        weighted_mean=8.9,
                        strongest_shot_id="s05",
                    ),
                    VariantJudgeScore(
                        angle=AngleName.CROWD_EFFECT,
                        scores={"hook_strength": 7.8, "mechanism_clarity": 8.0},
                        weighted_mean=7.9,
                        strongest_shot_id="s04",
                    ),
                ],
                winner=AngleName.UNIQUE_DATA,
                splices=[
                    SpliceDirective(
                        from_angle="pain",
                        shot_id="s03",
                        improves_axis="emotional_arc",
                        how_to_integrate="Integrate into problem beat",
                    )
                ],
            )

        raise ValueError(f"Unhandled mock stage: {stage}")


# ===========================================================================
# Unit and Integration Tests
# ===========================================================================


def test_boost_raises_social_proof_and_clamps(run_paths: RunPaths, test_settings: Settings):
    """The boost raises a social_proof candidate's score and clamps at 10.0 (WOW-5)."""
    _seed_prerequisites(run_paths)

    # Candidate with raw 9.5 stop_power_score. With +0.8 boost -> 10.3 -> clamped to 10.0
    mock_resp = _create_sample_raw_candidates(sp_score=9.5)
    mock_client = MockLLMClient(hook_responses=[mock_resp])

    artifact = asyncio.run(
        _generate_hook_candidates(test_settings, run_paths, client=mock_client)
    )

    sp_cand = next(c for c in artifact.candidates if c.archetype == HookArchetype.SOCIAL_PROOF)
    assert sp_cand.stop_power_score == 10.0
    assert "social_proof" in artifact.underused_archetypes_boosted


def test_regeneration_triggered_on_insufficient_social_proof(
    run_paths: RunPaths, test_settings: Settings
):
    """A response with only one social_proof candidate triggers exactly one regeneration."""
    _seed_prerequisites(run_paths)

    # First attempt: only 1 social proof candidate (violation)
    bad_resp = _create_sample_raw_candidates(social_proof_count=1)
    # Second attempt: valid 2 social proof candidates
    good_resp = _create_sample_raw_candidates(social_proof_count=2)

    mock_client = MockLLMClient(hook_responses=[bad_resp, good_resp])

    artifact = asyncio.run(
        _generate_hook_candidates(test_settings, run_paths, client=mock_client)
    )

    assert mock_client.call_count == 2
    sp_count = sum(1 for c in artifact.candidates if c.archetype == HookArchetype.SOCIAL_PROOF)
    assert sp_count == 2


def test_validator_6_median_tolerance_failure():
    """A variant whose beats violate the median tolerance raises ValidationError naming the beat."""
    sb_data = _load_fixture("storyboard.json")

    # In storyboard.json, problem beat starts at 3.4s.
    # Set median timeline expectation where problem beat should start at 5.5s with tolerance 0.5s.
    # Deviation is |3.4 - 5.5| = 2.1s > 0.5s tolerance, triggering validator 6.
    context = {
        "median_beat_timeline": [
            {"beat": "hook", "start_s": 0.0, "tolerance_s": 1.0},
            {"beat": "problem", "start_s": 5.5, "tolerance_s": 0.5},
        ]
    }
    sb_data["validation_context"] = context

    with pytest.raises(ValidationError) as exc_info:
        Storyboard.model_validate(sb_data)

    err_str = str(exc_info.value)
    # Assert the message names the beat (validator 6 doing its job)
    assert "problem" in err_str.lower()
    assert "deviates from mined median" in err_str


def test_prohibited_fact_in_loser_variant_detected_before_splicing(
    run_paths: RunPaths, test_settings: Settings
):
    """A prohibited fact in a loser variant is detected before splicing (validator 10)."""
    _seed_prerequisites(run_paths)

    winner_sb = Storyboard.model_validate(_load_fixture("storyboard.json"))
    loser_sb_clean = winner_sb.model_copy(deep=True)
    loser_sb_clean.meta.angle = AngleName.PAIN

    loser_sb_bad = winner_sb.model_copy(deep=True)
    loser_sb_bad.meta.angle = AngleName.CROWD_EFFECT
    # Inject prohibited fact into loser's voiceover
    loser_sb_bad.voiceover.full_text = (
        "We achieved 74.1% of tracked directions hit in our public test."
    )

    variants = {
        "unique_data": winner_sb,
        "pain": loser_sb_clean,
        "crowd_effect": loser_sb_bad,
    }

    mock_client = MockLLMClient()

    with pytest.raises(ValueError) as exc_info:
        asyncio.run(_judge_variants(test_settings, run_paths, variants, client=mock_client))

    assert "Prohibited fact" in str(exc_info.value)
    assert "crowd_effect" in str(exc_info.value)


def test_apply_splices_validates_and_retimes():
    """_apply_splices produces a storyboard where validators 1, 2, 4 still pass."""
    base_sb = Storyboard.model_validate(_load_fixture("storyboard.json"))

    loser_sb = base_sb.model_copy(deep=True)
    loser_sb.meta.angle = AngleName.PAIN
    # A distinct shot in loser
    source_shot = loser_sb.shots[2]  # s03 in problem beat
    source_shot.description = "Sourced emotional pain shot."

    variants = {
        "unique_data": base_sb,
        "pain": loser_sb,
    }

    splice_directive = {
        "from_angle": "pain",
        "shot_id": source_shot.id,
        "improves_axis": "emotional_arc",
        "how_to_integrate": "Add into problem beat",
    }

    spliced = _apply_splices(base_sb, [splice_directive], variants)

    # Check receipt trail (WOW-3)
    winner_variant = next(
        v for v in spliced.generation.variants if v.angle == spliced.meta.angle
    )
    assert "pain" in winner_variant.beats_stolen_from

    # Check validators 1, 2, 4 by running validation on output
    validated = Storyboard.model_validate(spliced.model_dump(mode="json"))
    assert validated is not None
    assert len(validated.shots) == len(base_sb.shots) + 1


def test_unintegrable_splice_dropped_and_warned():
    """An unintegrable splice is dropped and warned, and the result still parses."""
    base_sb = Storyboard.model_validate(_load_fixture("storyboard.json"))

    variants = {"unique_data": base_sb}

    # Non-existent shot in non-existent angle
    bad_splice = {
        "from_angle": "unknown_angle",
        "shot_id": "nonexistent_shot",
    }

    res = _apply_splices(base_sb, [bad_splice], variants)
    assert any("dropped splice" in w for w in res.warnings)
    # Output must still validate
    assert Storyboard.model_validate(res.model_dump(mode="json")) is not None


def test_generate_hook_candidates_tool_contract(run_paths: RunPaths, test_settings: Settings):
    """generate_hook_candidates writes hook_candidates.json and returns frozen contract."""
    _seed_prerequisites(run_paths)

    mock_client = MockLLMClient()
    result = generate_hook_candidates(
        settings=test_settings,
        paths=run_paths,
        client=mock_client,
    )

    assert Path(result["artifact_path"]).exists()
    assert result["candidates"] == 12
    assert isinstance(result["selected_id"], str)
    assert len(result["archetypes_covered"]) >= 4
    assert "social_proof" in result["underused_boosted"]

    # Verify written artifact validates
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    hook_art = store.read("hook_candidates")
    assert isinstance(hook_art, HookCandidates)
    winner = next(c for c in hook_art.candidates if c.selected)
    assert winner.id == result["selected_id"]
    assert winner.rejection_reason is None

    # Other candidates carry rejection reasons
    losers = [c for c in hook_art.candidates if not c.selected]
    assert all(c.rejection_reason is not None for c in losers)


def test_write_storyboard_variant_tool_contract(run_paths: RunPaths, test_settings: Settings):
    """write_storyboard_variant writes scratch file and satisfies contract + WOW-4."""
    _seed_prerequisites(run_paths)

    # Seed hook candidates first
    generate_hook_candidates(
        settings=test_settings,
        paths=run_paths,
        client=MockLLMClient(),
    )

    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    hook_art = store.read("hook_candidates")
    selected_id = next(c for c in hook_art.candidates if c.selected).id

    mock_client = MockLLMClient()
    result = write_storyboard_variant(
        settings=test_settings,
        paths=run_paths,
        angle="pain",
        hook_id=selected_id,
        total_duration_s=42.0,
        client=mock_client,
    )

    assert result["angle"] == "pain"
    assert result["shots"] >= 10
    assert result["valid"] is True
    assert Path(result["variant_path"]).exists()

    # WOW-4: Verify every shot has executable camera direction
    data = json.loads(Path(result["variant_path"]).read_text(encoding="utf-8"))
    for shot in data["shots"]:
        assert shot["camera"].get("move") is not None
        assert shot["camera"]["move"] in ("static", "push_in", "pull_out", "whip_pan")

    # Safe harbour duration
    assert data["compliance"]["risk_disclosure_present"] is True
    assert data["compliance"]["safe_harbour_duration_s"] >= 3.0


def test_judge_variants_tool_contract(run_paths: RunPaths, test_settings: Settings):
    """judge_variants scores 3 variants, splices, writes storyboard.json, does NOT write review_verdict."""
    _seed_prerequisites(run_paths)

    # Seed 3 variants in artifacts/variants/
    variants_dir = run_paths.artifacts / "variants"
    variants_dir.mkdir(parents=True, exist_ok=True)
    fixture_sb = _load_fixture("storyboard.json")

    for angle in ("pain", "unique_data", "crowd_effect"):
        var_data = dict(fixture_sb)
        var_data["meta"] = dict(fixture_sb["meta"])
        var_data["meta"]["angle"] = angle
        (variants_dir / f"{angle}.json").write_text(json.dumps(var_data), encoding="utf-8")

    mock_client = MockLLMClient()
    result = judge_variants(
        settings=test_settings,
        paths=run_paths,
        client=mock_client,
    )

    assert result["winner"] == "unique_data"
    assert len(result["variants"]) == 3
    assert len(result["splices"]) >= 1
    assert Path(result["winner_path"]).exists()

    # Rule: Does NOT write review_verdict.json
    assert not (run_paths.artifacts / "review_verdict.json").exists()

    # Verify winner artifact in store
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    winner_sb = store.read("storyboard")
    assert isinstance(winner_sb, Storyboard)
    assert winner_sb.compliance.risk_disclosure_present is True
    assert winner_sb.compliance.safe_harbour_duration_s >= 3.0

    spliced_records = [v for v in winner_sb.generation.variants if v.beats_stolen_from]
    assert len(spliced_records) >= 1
    assert "pain" in spliced_records[0].beats_stolen_from


def test_budget_exceeded_propagates(run_paths: RunPaths, test_settings: Settings):
    """Rule A6: BudgetExceeded propagates without being swallowed."""
    _seed_prerequisites(run_paths)
    mock_client = MockLLMClient(budget_exceeded=True)

    with pytest.raises(BudgetExceeded):
        generate_hook_candidates(
            settings=test_settings,
            paths=run_paths,
            client=mock_client,
        )


def test_docstring_reader_rules():
    """Verify write_storyboard_variant docstring carries explicit usage guidance."""
    doc = write_storyboard_variant.__doc__ or ""
    assert "CALL THIS:" in doc
    assert "WHEN NOT TO CALL:" in doc
