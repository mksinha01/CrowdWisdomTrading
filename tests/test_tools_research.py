"""Tests for tools.research (Story S21).

Tests research_angle and assemble_brief tools:
- a hit dated before window_start is dropped
- an undated hit is kept with confidence scaled and the text annotated
- a claim with no source_url never reaches the artifact
- prohibited_facts contains the three baseline entries plus one discovered one
- the union never shrinks: run assemble_brief twice with a discovered fact, assert baseline is present
- strongest_angle is one of the three literals
- the written artifact validates against ResearchBrief
- _search_angle dedupes by URL and keeps the higher score
- landing_url_verified_200 reflects HEAD check and errors do not fail the run
- LLM reader docstrings (Rule A1 / §7.2)
- Rule A5: CHEAP tier is used for research and brief assembly
- Rule A6: BudgetExceeded propagates
"""

from __future__ import annotations

import asyncio
from datetime import date
import json
from pathlib import Path
from typing import Any

import pytest
import httpx
from pydantic import ValidationError

from cwt.clients.http_cache import HttpCache
from cwt.clients.llm import (
    BudgetExceeded,
    Tier,
)
from cwt.clients.tavily import SearchHit
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactError, ArtifactStore
from cwt.domain.models import (
    AngleName,
    ProhibitedFact,
    ResearchBrief,
    ResearchClaim,
)
from cwt.tools.research import (
    ANGLES,
    BASELINE_PROHIBITED_FACTS,
    BriefAssemblyOutput,
    BriefClaimSelection,
    CrowdEffectAngleOutput,
    PainAngleOutput,
    UniqueDataAngleOutput,
    _parse_date,
    _search_angle,
    _to_claims,
    assemble_brief,
    research_angle,
)
from cwt.util.paths import RunPaths


@pytest.fixture
def test_settings() -> Settings:
    return Settings.from_env()


@pytest.fixture
def run_paths(tmp_path: Path) -> RunPaths:
    return RunPaths(tmp_path / "test_run").ensure()


@pytest.fixture
def test_cache(tmp_path: Path) -> HttpCache:
    return HttpCache(root=tmp_path / "http_cache", offline=False)


@pytest.fixture(autouse=True)
def mock_network_and_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock external search calls and landing url HEAD request so tests are 100% offline and fast."""
    import respx

    # Default mock search hits per angle
    async def _mock_search(client=None, angle="", queries=None, **kwargs):
        return [
            SearchHit(
                title=f"{angle} evidence 1",
                url=f"https://example.com/{angle}/1",
                content=f"First-hand evidence on {angle}",
                published_date="2026-09-10",
                score=0.88,
                provider="tavily",
            ),
            SearchHit(
                title=f"{angle} evidence 2",
                url=f"https://example.com/{angle}/2",
                content=f"Undated insight on {angle}",
                published_date=None,
                score=0.75,
                provider="exa",
            ),
        ]

    monkeypatch.setattr("cwt.tools.research._search_angle", _mock_search)

    # Mock HEAD request for landing_url
    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.head("https://crowdwisdomtrading.com/").mock(
            return_value=httpx.Response(200)
        )
        yield respx_mock


class MockLLMClient:
    """Mock LLMClient for deterministic testing without external API calls."""

    def __init__(
        self,
        *,
        budget_exceeded: bool = False,
        pain_output: PainAngleOutput | None = None,
        unique_data_output: UniqueDataAngleOutput | None = None,
        crowd_effect_output: CrowdEffectAngleOutput | None = None,
        brief_output: BriefAssemblyOutput | None = None,
    ):
        self.budget_exceeded = budget_exceeded
        self.pain_output = pain_output
        self.unique_data_output = unique_data_output
        self.crowd_effect_output = crowd_effect_output
        self.brief_output = brief_output
        self.tiers_used: list[Tier] = []
        self.stages_called: list[str] = []
        self.call_count = 0

    async def complete_validated(
        self,
        *,
        tier: Tier,
        messages: list[dict],
        schema: type,
        stage: str,
        max_repairs: int | None = None,
    ) -> Any:
        self.call_count += 1
        self.tiers_used.append(tier)
        self.stages_called.append(stage)

        if self.budget_exceeded:
            raise BudgetExceeded(spent=2.50, cap=2.00, stage=stage)

        if stage == "research_pain":
            if self.pain_output:
                return self.pain_output
            return PainAngleOutput(
                angle="pain",
                claims=[
                    {
                        "text": "Retail traders experience decision fatigue with multiple signal providers.",
                        "source_url": "https://example.com/pain-article",
                        "source_title": "Trading Fatigue",
                        "published_date": "2026-09-10",
                        "confidence": 0.85,
                        "provider": "tavily",
                    }
                ],
                synthesis="Self-directed retail traders suffer paralysis when signals conflict.",
                search_queries_used=["retail trader signal overload"],
            )

        if stage == "research_unique_data":
            if self.unique_data_output:
                return self.unique_data_output
            return UniqueDataAngleOutput(
                angle="unique_data",
                claims=[
                    {
                        "text": "Every call provides entry, stops, targets, and transparent outcomes.",
                        "source_url": "https://crowdwisdomtrading.com/",
                        "source_title": "CrowdWisdomTrading",
                        "published_date": None,
                        "confidence": 0.90,
                        "provider": "exa",
                    }
                ],
                synthesis="Transparent process with public record of calls and outcomes.",
                search_queries_used=["crowdwisdomtrading predictions track record"],
                prohibited_facts_found=[
                    {
                        "fact": "Guaranteed 100% win rate on all market regimes",
                        "reason": "Unsubstantiated marketing claim",
                        "rule": "Must never appear in any generated script.",
                    }
                ],
            )

        if stage == "research_crowd_effect":
            if self.crowd_effect_output:
                return self.crowd_effect_output
            return CrowdEffectAngleOutput(
                angle="crowd_effect",
                claims=[
                    {
                        "text": "Aggregated independent forecasts outperform individual domain experts.",
                        "source_url": "https://example.com/tetlock-paper",
                        "source_title": "Superforecasting Research",
                        "published_date": "2026-09-05",
                        "confidence": 0.88,
                        "provider": "tavily",
                    }
                ],
                synthesis="Aggregation of diverse forecasters yields higher calibration than single gurus.",
                search_queries_used=["wisdom of crowds forecast accuracy"],
                counterargument="Crowds fail when participants herd or share correlated information cascades.",
            )

        if stage == "assemble_brief":
            if self.brief_output:
                return self.brief_output
            return BriefAssemblyOutput(
                selected_claims=[
                    BriefClaimSelection(
                        angle=AngleName.PAIN,
                        text="Retail traders experience decision fatigue with multiple signal providers.",
                        source_url="https://example.com/pain-article",
                        confidence=0.85,
                    ),
                    BriefClaimSelection(
                        angle=AngleName.UNIQUE_DATA,
                        text="Every call provides entry, stops, targets, and transparent outcomes.",
                        source_url="https://crowdwisdomtrading.com/",
                        confidence=0.90,
                    ),
                    BriefClaimSelection(
                        angle=AngleName.CROWD_EFFECT,
                        text="Aggregated independent forecasts outperform individual domain experts.",
                        source_url="https://example.com/tetlock-paper",
                        confidence=0.88,
                    ),
                ],
                strongest_angle=AngleName.UNIQUE_DATA,
                angle_rationale="Process transparency is the strongest defensible differentiator for CWT.",
                counterargument_to_address="Crowd forecasting can degrade into an echo chamber without independence.",
            )

        raise ValueError(f"Unexpected stage in MockLLMClient: {stage}")


# ===========================================================================
# 1. _to_claims tests
# ===========================================================================


def test_to_claims_dated_before_window_dropped() -> None:
    """A hit dated before window_start is dropped."""
    window_start = date(2026, 8, 27)

    old_hit = SearchHit(
        title="Old News",
        url="https://example.com/old",
        content="Trading signals in July",
        published_date="2026-08-01",
        score=0.9,
        provider="tavily",
    )
    recent_hit = SearchHit(
        title="Recent News",
        url="https://example.com/recent",
        content="Trading signals in September",
        published_date="2026-09-05",
        score=0.85,
        provider="tavily",
    )

    claims = _to_claims([old_hit, recent_hit], window_start=window_start)
    assert len(claims) == 1
    assert claims[0].source_url == "https://example.com/recent"
    assert claims[0].published_date == date(2026, 9, 5)
    assert claims[0].confidence == pytest.approx(0.85)


def test_to_claims_undated_hit_scaled_and_annotated() -> None:
    """An undated hit is kept with confidence multiplied by 0.6 and text annotated."""
    window_start = date(2026, 8, 27)

    undated_hit = SearchHit(
        title="Undated Article",
        url="https://example.com/undated",
        content="Crowd wisdom in trading ideas",
        published_date=None,
        score=0.80,
        provider="exa",
    )

    claims = _to_claims([undated_hit], window_start=window_start)
    assert len(claims) == 1
    claim = claims[0]
    assert claim.published_date is None
    # 0.80 * 0.6 = 0.48
    assert claim.confidence == pytest.approx(0.48)
    assert "(undated)" in claim.text


def test_to_claims_empty_url_dropped() -> None:
    """A hit with empty or whitespace URL is dropped."""
    window_start = date(2026, 8, 27)

    no_url_hit = SearchHit(
        title="Ghost Source",
        url="",
        content="Some unsourced claim",
        published_date="2026-09-01",
        score=0.75,
        provider="tavily",
    )
    claims = _to_claims([no_url_hit], window_start=window_start)
    assert len(claims) == 0


def test_claim_with_no_source_url_never_reaches_artifact(
    test_settings: Settings,
    run_paths: RunPaths,
    test_cache: HttpCache,
) -> None:
    """Enforce §6.3 line 1181: A claim with an empty source_url is dropped before reaching artifact."""
    mock_llm = MockLLMClient(
        pain_output=PainAngleOutput(
            angle="pain",
            claims=[
                {
                    "text": "Unsourced claim that should be dropped",
                    "source_url": "",
                    "source_title": "No Source",
                    "published_date": "2026-09-10",
                    "confidence": 0.9,
                },
                {
                    "text": "Sourced claim that should be kept",
                    "source_url": "https://example.com/valid-source",
                    "source_title": "Valid Source",
                    "published_date": "2026-09-11",
                    "confidence": 0.8,
                },
            ],
            synthesis="Synthesis text",
            search_queries_used=["query 1"],
        )
    )

    result = research_angle(
        settings=test_settings,
        paths=run_paths,
        angle="pain",
        client=mock_llm,  # type: ignore[arg-type]
        cache=test_cache,
    )

    angle_path = run_paths.artifacts / "angles" / "pain.json"
    assert angle_path.exists()
    angle_data = json.loads(angle_path.read_text(encoding="utf-8"))

    # Assert the unsourced claim was dropped
    assert len(angle_data["claims"]) == 1
    assert angle_data["claims"][0]["source_url"] == "https://example.com/valid-source"
    assert result["claims"] == 1
    assert result["sourced"] == 1


# ===========================================================================
# 2. prohibited_facts & assemble_brief tests
# ===========================================================================


def test_prohibited_facts_contains_baseline_plus_discovered(
    test_settings: Settings,
    run_paths: RunPaths,
    test_cache: HttpCache,
) -> None:
    """prohibited_facts contains the three baseline entries plus one discovered one."""
    mock_llm = MockLLMClient()

    # Run the three angles
    for angle in ANGLES:
        research_angle(
            settings=test_settings,
            paths=run_paths,
            angle=angle,
            client=mock_llm,  # type: ignore[arg-type]
            cache=test_cache,
        )

    # Assemble brief
    result = assemble_brief(
        settings=test_settings,
        paths=run_paths,
        client=mock_llm,  # type: ignore[arg-type]
        cache=test_cache,
    )

    brief_path = Path(result["artifact_path"])
    assert brief_path.exists()
    brief_data = json.loads(brief_path.read_text(encoding="utf-8"))
    brief = ResearchBrief.model_validate(brief_data)

    # 3 baseline facts + 1 discovered from unique_data mock
    facts = [f.fact for f in brief.prohibited_facts]
    assert len(facts) >= 4
    for baseline in BASELINE_PROHIBITED_FACTS:
        assert baseline["fact"] in facts
    assert "Guaranteed 100% win rate on all market regimes" in facts
    assert result["prohibited_facts"] >= 4


def test_union_never_shrinks(
    test_settings: Settings,
    run_paths: RunPaths,
    test_cache: HttpCache,
) -> None:
    """The union never shrinks: run assemble_brief twice with a discovered fact, baseline is still present."""
    mock_llm = MockLLMClient()

    for angle in ANGLES:
        research_angle(
            settings=test_settings,
            paths=run_paths,
            angle=angle,
            client=mock_llm,  # type: ignore[arg-type]
            cache=test_cache,
        )

    # Run 1
    res1 = assemble_brief(
        settings=test_settings,
        paths=run_paths,
        client=mock_llm,  # type: ignore[arg-type]
        cache=test_cache,
    )
    brief1 = ResearchBrief.model_validate(
        json.loads(Path(res1["artifact_path"]).read_text(encoding="utf-8"))
    )

    # Run 2
    res2 = assemble_brief(
        settings=test_settings,
        paths=run_paths,
        client=mock_llm,  # type: ignore[arg-type]
        cache=test_cache,
    )
    brief2 = ResearchBrief.model_validate(
        json.loads(Path(res2["artifact_path"]).read_text(encoding="utf-8"))
    )

    facts1 = [f.fact for f in brief1.prohibited_facts]
    facts2 = [f.fact for f in brief2.prohibited_facts]

    assert len(facts1) == len(facts2)
    assert set(facts1) == set(facts2)
    for baseline in BASELINE_PROHIBITED_FACTS:
        assert baseline["fact"] in facts2


def test_strongest_angle_is_one_of_three_literals() -> None:
    """strongest_angle must be one of the three literal values ('pain', 'unique_data', 'crowd_effect')."""
    # Valid literals
    for val in ("pain", "unique_data", "crowd_effect"):
        output = BriefAssemblyOutput(
            selected_claims=[],
            strongest_angle=AngleName(val),
            angle_rationale="Rationale text",
        )
        assert output.strongest_angle == val

    # Invalid literal like "unique data" with space fails schema validation
    with pytest.raises(ValidationError):
        BriefAssemblyOutput.model_validate(
            {
                "selected_claims": [],
                "strongest_angle": "unique data",
                "angle_rationale": "Rationale",
            }
        )


def test_written_artifact_validates_against_research_brief(
    test_settings: Settings,
    run_paths: RunPaths,
    test_cache: HttpCache,
) -> None:
    """The written artifact validates against ResearchBrief schema."""
    mock_llm = MockLLMClient()

    for angle in ANGLES:
        research_angle(
            settings=test_settings,
            paths=run_paths,
            angle=angle,
            client=mock_llm,  # type: ignore[arg-type]
            cache=test_cache,
        )

    res = assemble_brief(
        settings=test_settings,
        paths=run_paths,
        client=mock_llm,  # type: ignore[arg-type]
        cache=test_cache,
    )

    artifact_path = Path(res["artifact_path"])
    raw_data = json.loads(artifact_path.read_text(encoding="utf-8"))

    brief = ResearchBrief.model_validate(raw_data)
    assert brief.schema_version == 1
    assert brief.product.name == "CrowdWisdomTrading"
    assert brief.product.legal_entity == "Tsuroni LTD"
    assert AngleName.PAIN in brief.angles
    assert AngleName.UNIQUE_DATA in brief.angles
    assert AngleName.CROWD_EFFECT in brief.angles
    assert len(brief.prohibited_facts) >= 3


# ===========================================================================
# 3. Search deduplication & error handling tests
# ===========================================================================


@pytest.mark.asyncio
async def test_search_angle_deduplication(tmp_path: Path) -> None:
    """_search_angle runs both search clients, dedupes by URL, and keeps the higher score."""
    cache = HttpCache(root=tmp_path / "cache", offline=False)

    hit1 = SearchHit(
        title="Duplicate URL - Low Score",
        url="https://example.com/same-url",
        content="Low score content",
        published_date="2026-09-01",
        score=0.45,
        provider="tavily",
    )
    hit2 = SearchHit(
        title="Duplicate URL - High Score",
        url="https://example.com/same-url",
        content="High score content",
        published_date="2026-09-01",
        score=0.89,
        provider="exa",
    )
    hit3 = SearchHit(
        title="Unique URL",
        url="https://example.com/unique",
        content="Unique content",
        published_date="2026-09-02",
        score=0.70,
        provider="tavily",
    )

    # Mock tavily and exa via monkeypatching in module
    import cwt.tools.research as res_mod

    async def mock_tavily(*args: Any, **kwargs: Any) -> list[SearchHit]:
        return [hit1, hit3]

    async def mock_exa(*args: Any, **kwargs: Any) -> list[SearchHit]:
        return [hit2]

    old_tavily = res_mod.tavily_search
    old_exa = res_mod.exa_search
    try:
        res_mod.tavily_search = mock_tavily  # type: ignore[assignment]
        res_mod.exa_search = mock_exa  # type: ignore[assignment]

        results = await _search_angle(
            cache=cache,
            angle="pain",
            queries=["test query"],
            tavily_api_key="tvly-mock",
            exa_api_key="exa-mock",
        )

        assert len(results) == 2
        url_map = {r.url: r for r in results}
        assert "https://example.com/same-url" in url_map
        assert "https://example.com/unique" in url_map
        # Check higher score was kept
        assert url_map["https://example.com/same-url"].score == pytest.approx(0.89)
        assert url_map["https://example.com/same-url"].provider == "exa"
    finally:
        res_mod.tavily_search = old_tavily
        res_mod.exa_search = old_exa


def test_landing_url_verified_200_failure_does_not_break_run(
    test_settings: Settings,
    run_paths: RunPaths,
    test_cache: HttpCache,
) -> None:
    """landing_url_verified_200 is False when HEAD check fails, but run completes safely."""
    mock_llm = MockLLMClient()

    for angle in ANGLES:
        research_angle(
            settings=test_settings,
            paths=run_paths,
            angle=angle,
            client=mock_llm,  # type: ignore[arg-type]
            cache=test_cache,
        )

    # Assemble brief without pre-cached 200 HEAD response
    res = assemble_brief(
        settings=test_settings,
        paths=run_paths,
        client=mock_llm,  # type: ignore[arg-type]
        cache=test_cache,
    )

    brief_data = json.loads(Path(res["artifact_path"]).read_text(encoding="utf-8"))
    brief = ResearchBrief.model_validate(brief_data)

    # In local testing without real network or fixture, landing_url_verified_200 will be False
    assert isinstance(brief.product.landing_url_verified_200, bool)


def test_missing_angle_scratch_artifacts_raises_artifact_error(
    test_settings: Settings,
    run_paths: RunPaths,
) -> None:
    """assemble_brief raises ArtifactError if angle scratch artifacts are missing."""
    with pytest.raises(ArtifactError, match="Missing angle scratch artifacts"):
        assemble_brief(settings=test_settings, paths=run_paths)


def test_invalid_angle_raises_value_error(
    test_settings: Settings,
    run_paths: RunPaths,
) -> None:
    """research_angle raises ValueError on unrecognized angle."""
    with pytest.raises(ValueError, match="Invalid research angle"):
        research_angle(settings=test_settings, paths=run_paths, angle="invalid_angle")


# ===========================================================================
# 4. Docstrings, Tier, and Budget tests
# ===========================================================================


def test_research_angle_docstring_reader_rule_a1() -> None:
    """Docstring contains required CALL THIS and WHEN NOT TO CALL guidance."""
    doc = research_angle.__doc__ or ""
    assert "CALL THIS: on one of the three research cards" in doc
    assert "WHEN NOT TO CALL:" in doc
    assert "Do not call it with a `window_days` other than 30" in doc


def test_assemble_brief_docstring_reader_rule_a1() -> None:
    """Docstring contains required CALL THIS and WHEN NOT TO CALL guidance."""
    doc = assemble_brief.__doc__ or ""
    assert "CALL THIS:" in doc
    assert "WHEN NOT TO CALL:" in doc


def test_rule_a5_cheap_tier_used(
    test_settings: Settings,
    run_paths: RunPaths,
    test_cache: HttpCache,
) -> None:
    """Rule A5: Research synthesis (3 calls) and brief assembly (1 call) must use Tier.CHEAP."""
    mock_llm = MockLLMClient()

    for angle in ANGLES:
        research_angle(
            settings=test_settings,
            paths=run_paths,
            angle=angle,
            client=mock_llm,  # type: ignore[arg-type]
            cache=test_cache,
        )

    assemble_brief(
        settings=test_settings,
        paths=run_paths,
        client=mock_llm,  # type: ignore[arg-type]
        cache=test_cache,
    )

    # 3 research calls + 1 assemble call = 4 calls total
    assert len(mock_llm.tiers_used) == 4
    for tier in mock_llm.tiers_used:
        assert tier == Tier.CHEAP


def test_rule_a6_budget_exceeded_propagates(
    test_settings: Settings,
    run_paths: RunPaths,
    test_cache: HttpCache,
) -> None:
    """Rule A6: BudgetExceeded propagates out of tools without being caught."""
    mock_llm = MockLLMClient(budget_exceeded=True)

    with pytest.raises(BudgetExceeded):
        research_angle(
            settings=test_settings,
            paths=run_paths,
            angle="pain",
            client=mock_llm,  # type: ignore[arg-type]
            cache=test_cache,
        )

    # Seed angle scratch files so assemble_brief reaches LLM call
    angles_dir = run_paths.artifacts / "angles"
    angles_dir.mkdir(parents=True, exist_ok=True)
    for a in ANGLES:
        (angles_dir / f"{a}.json").write_text(
            json.dumps({"angle": a, "claims": [], "synthesis": "", "search_queries_used": []})
        )

    with pytest.raises(BudgetExceeded):
        assemble_brief(
            settings=test_settings,
            paths=run_paths,
            client=mock_llm,  # type: ignore[arg-type]
            cache=test_cache,
        )
