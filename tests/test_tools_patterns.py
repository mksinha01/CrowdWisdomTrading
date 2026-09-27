"""Tests for tools.patterns (Story S20).

Tests extract_ad_patterns and aggregate_beat_timeline tools:
- Ad with video_duration_s=None produces a pattern without a beat_sheet and a warning
- The median timeline is contiguous after aggregation (validator 1 holds)
- sum(archetype_distribution.values()) == 1.0
- One malformed extraction is skipped, the batch completes, warnings names the ad id
- underused_high_durability contains social_proof and pain_point when present in fixture
- Written artifact validates against AdPatterns (round-trip)
- Fewer than 3 ads blocks with clear reason
- aggregate_beat_timeline rewrites in place without LLM calls
- Rule A6: BudgetExceeded propagates
- LLM reader docstrings (Rule A1 / §7.2)
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from cwt.clients.llm import (
    ArtifactValidationError,
    BudgetExceeded,
    Tier,
)
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactStore
from cwt.domain.models import (
    SCHEMA_VERSION,
    Ad,
    AdPattern,
    AdPatterns,
    AdQuery,
    AdRanking,
    AdSource,
    BeatEntry,
    BeatName,
    HookArchetype,
    HookPattern,
    WinningAds,
)
from cwt.tools.patterns import (
    AdExtractionOutput,
    BeatSheetOutput,
    _extract_one,
    aggregate_beat_timeline,
    extract_ad_patterns,
)
from cwt.util.paths import RunPaths


@pytest.fixture
def test_settings() -> Settings:
    """Fixture returning settings loaded from environment or defaults."""
    return Settings.from_env()


@pytest.fixture
def run_paths(tmp_path: Path) -> RunPaths:
    """Fixture providing initialized RunPaths in a temporary directory."""
    return RunPaths(tmp_path / "test_run").ensure()


def _make_sample_ad(
    ad_id: str,
    *,
    body_text: str = "Test trading ad copy",
    active_days: int = 25,
    video_duration_s: float | None = 30.0,
) -> Ad:
    return Ad(
        ad_id=ad_id,
        page_name="Signal Pro",
        is_active=True,
        started_running=date(2026, 9, 1),
        active_days=active_days,
        publisher_platforms=["FACEBOOK", "INSTAGRAM"],
        display_format="VIDEO" if video_duration_s is not None else "IMAGE",
        body_text=body_text,
        video_duration_s=video_duration_s,
        ad_library_url=f"https://www.facebook.com/ads/library/?id={ad_id}",
        performance_score=0.85,
        normalised_from="apify~facebook-ads-scraper",
    )


def _seed_winning_ads(store: ArtifactStore, ads: list[Ad]) -> WinningAds:
    query = AdQuery(
        keywords=["trading signals"],
        countries=["US"],
        window_start=date(2026, 8, 28),
        window_end=date(2026, 9, 27),
        window_days=30,
    )
    source = AdSource(
        actor_id="apify~facebook-ads-scraper",
        actor_fallbacks_tried=[],
        run_id="test_run",
        dataset_id="test_ds",
        items_returned=len(ads),
        items_after_window_filter=len(ads),
        actual_charge_usd=0.25,
        charge_cap_usd=1.00,
    )
    ranking = AdRanking(
        method="weighted_longevity_signal",
        weights={"active_days": 0.45},
        excluded_reasons={},
    )
    winning_ads = WinningAds(
        query=query,
        source=source,
        ranking=ranking,
        ads=ads,
        warnings=[],
    )
    store.write("winning_ads", winning_ads)
    return winning_ads


class MockLLMClient:
    """Mock LLMClient providing deterministic responses without network calls."""

    def __init__(
        self,
        *,
        extraction_map: dict[str, Any] | None = None,
        beat_sheet_map: dict[str, Any] | None = None,
        fail_ad_ids: set[str] | None = None,
        budget_exceeded: bool = False,
    ):
        self.extraction_map = extraction_map or {}
        self.beat_sheet_map = beat_sheet_map or {}
        self.fail_ad_ids = fail_ad_ids or set()
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
    ) -> Any:
        self.call_count += 1
        self.stages_called.append(stage)

        if self.budget_exceeded:
            raise BudgetExceeded(spent=2.50, cap=2.00, stage=stage)

        content = messages[-1]["content"] if messages else ""
        for fail_id in self.fail_ad_ids:
            if fail_id in content:
                raise ArtifactValidationError(
                    stage=stage,
                    schema=schema.__name__,
                    error=ValueError(f"Malformed completion for ad {fail_id}"),
                )

        if stage == "patterns_extract":
            for ad_key, output in self.extraction_map.items():
                if ad_key in content:
                    if isinstance(output, Exception):
                        raise output
                    if isinstance(output, AdExtractionOutput):
                        return output
                    return schema.model_validate(output)

            # Default extraction response
            return AdExtractionOutput(
                hook=HookPattern(
                    text="Most traders follow one voice. Here is what 16,000 sound like.",
                    archetype=HookArchetype.CONTRARIAN_STAT,
                    stop_power_score=8.4,
                    why_it_stops_the_scroll="Asserts an implausible stat forcing re-reading.",
                ),
                pain="Following single analyst inherits blind spots.",
                concept="Consensus as risk control.",
                proof_type="statistic",
            )

        elif stage == "patterns_beat_sheet":
            for ad_key, output in self.beat_sheet_map.items():
                if ad_key in content:
                    if isinstance(output, Exception):
                        raise output
                    if isinstance(output, BeatSheetOutput):
                        return output
                    return schema.model_validate(output)

            # Default beat sheet
            return BeatSheetOutput(
                beats=[
                    BeatEntry(
                        beat=BeatName.HOOK,
                        start_s=0.0,
                        end_s=3.0,
                        what_happens="Bold statement",
                    ),
                    BeatEntry(
                        beat=BeatName.PROBLEM,
                        start_s=3.0,
                        end_s=8.0,
                        what_happens="Problem text",
                    ),
                    BeatEntry(
                        beat=BeatName.MECHANISM,
                        start_s=8.0,
                        end_s=20.0,
                        what_happens="Consensus demo",
                    ),
                    BeatEntry(
                        beat=BeatName.CTA,
                        start_s=20.0,
                        end_s=30.0,
                        what_happens="Logo + CTA",
                    ),
                ]
            )

        raise ValueError(f"Unexpected stage {stage}")


# ===========================================================================
# 1. Docstrings written for LLM reader (Rule A1 / §7.2)
# ===========================================================================


def test_docstrings_written_for_llm_reader():
    """Verify tool docstrings contain CALL THIS and WHEN NOT TO CALL guidance."""
    for fn in (extract_ad_patterns, aggregate_beat_timeline):
        doc = fn.__doc__
        assert doc is not None, f"{fn.__name__} missing docstring"
        assert "CALL THIS:" in doc, f"{fn.__name__} docstring missing 'CALL THIS:'"
        assert "WHEN NOT TO CALL:" in doc, f"{fn.__name__} docstring missing 'WHEN NOT TO CALL:'"


# ===========================================================================
# 2. Fewer than 3 ads blocks (Build step 1)
# ===========================================================================


def test_fewer_than_3_ads_blocks(test_settings: Settings, run_paths: RunPaths):
    """Verify extract_ad_patterns blocks when winning_ads has fewer than 3 ads."""
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    ads = [
        _make_sample_ad("ad_1"),
        _make_sample_ad("ad_2"),
    ]
    _seed_winning_ads(store, ads)

    mock_client = MockLLMClient()
    with pytest.raises(ValueError, match="at least 3 are required"):
        extract_ad_patterns(
            settings=test_settings,
            paths=run_paths,
            client=mock_client,  # type: ignore[arg-type]
        )


# ===========================================================================
# 3. Ad with video_duration_s=None skips beat_sheet and records warning
# ===========================================================================


def test_ad_with_no_duration_skips_beat_sheet_and_warns(
    test_settings: Settings,
    run_paths: RunPaths,
):
    """Verify ad with video_duration_s=None produces pattern with empty beat_sheet and warning."""
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    ads = [
        _make_sample_ad("ad_1", video_duration_s=30.0),
        _make_sample_ad("ad_2", video_duration_s=None),
        _make_sample_ad("ad_3", video_duration_s=35.0),
    ]
    _seed_winning_ads(store, ads)

    mock_client = MockLLMClient()
    result = extract_ad_patterns(
        settings=test_settings,
        paths=run_paths,
        client=mock_client,  # type: ignore[arg-type]
    )

    assert result["ads_analysed"] == 3
    artifact = store.read("ad_patterns")
    assert isinstance(artifact, AdPatterns)

    # Find ad_2 pattern
    p2 = next(p for p in artifact.patterns if p.ad_id == "ad_2")
    assert p2.beat_sheet == []
    assert p2.hook.text == "Most traders follow one voice. Here is what 16,000 sound like."

    # Check warnings recorded
    assert any("beat_sheet_skipped:no_duration" in w and "ad_2" in w for w in artifact.warnings)


# ===========================================================================
# 4. Median timeline contiguous & archetype distribution sums to 1.0
# ===========================================================================


def test_median_timeline_contiguous_and_distribution_sums_to_one(
    test_settings: Settings,
    run_paths: RunPaths,
):
    """Verify median beat timeline is contiguous and archetype distribution sums to exactly 1.0."""
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    ads = [
        _make_sample_ad("ad_1", video_duration_s=30.0),
        _make_sample_ad("ad_2", video_duration_s=32.0),
        _make_sample_ad("ad_3", video_duration_s=28.0),
    ]
    _seed_winning_ads(store, ads)

    mock_client = MockLLMClient()
    extract_ad_patterns(
        settings=test_settings,
        paths=run_paths,
        client=mock_client,  # type: ignore[arg-type]
    )

    artifact = store.read("ad_patterns")
    assert isinstance(artifact, AdPatterns)

    tl = artifact.aggregate.median_beat_timeline
    assert len(tl) > 0
    assert tl[0].start_s == 0.0
    # Contiguity invariant
    assert all(a.end_s == b.start_s for a, b in zip(tl, tl[1:]))

    dist = artifact.aggregate.archetype_distribution
    assert abs(sum(dist.values()) - 1.0) < 1e-6


# ===========================================================================
# 5. One malformed extraction is skipped, batch completes, warnings names ad id
# ===========================================================================


def test_malformed_extraction_skipped_batch_completes_warns_with_ad_id(
    test_settings: Settings,
    run_paths: RunPaths,
):
    """Verify one bad extraction does not crash the batch and warning names the failed ad id."""
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    ads = [
        _make_sample_ad("ad_ok_1", body_text="Copy 1", video_duration_s=30.0),
        _make_sample_ad("ad_bad_2", body_text="Copy 2 with ad_bad_2 inside", video_duration_s=30.0),
        _make_sample_ad("ad_ok_3", body_text="Copy 3", video_duration_s=30.0),
        _make_sample_ad("ad_ok_4", body_text="Copy 4", video_duration_s=30.0),
    ]
    _seed_winning_ads(store, ads)

    mock_client = MockLLMClient(fail_ad_ids={"ad_bad_2"})
    result = extract_ad_patterns(
        settings=test_settings,
        paths=run_paths,
        client=mock_client,  # type: ignore[arg-type]
    )

    # 3 of 4 succeeded
    assert result["ads_analysed"] == 3

    artifact = store.read("ad_patterns")
    assert isinstance(artifact, AdPatterns)
    assert len(artifact.patterns) == 3
    assert not any(p.ad_id == "ad_bad_2" for p in artifact.patterns)

    # Warning records ad_bad_2
    assert any("ad_bad_2" in w for w in artifact.warnings)


# ===========================================================================
# 6. underused_high_durability contains social_proof and pain_point
# ===========================================================================


def test_underused_high_durability_detected(test_settings: Settings, run_paths: RunPaths):
    """Verify underused_high_durability contains social_proof and pain_point when present."""
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)

    # 12 ads: 1 social_proof (0.08), 1 pain_point (0.08), 10 contrarian_stat/bold_statement
    ads = []
    extraction_map: dict[str, Any] = {}

    for i in range(12):
        ad_id = f"ad_{i:02d}"
        body = f"Copy for ad {ad_id}"
        ads.append(_make_sample_ad(ad_id, body_text=body, video_duration_s=30.0))

        if i == 0:
            arch = HookArchetype.SOCIAL_PROOF
            stop_score = 9.0
        elif i == 1:
            arch = HookArchetype.PAIN_POINT
            stop_score = 8.5
        elif i < 7:
            arch = HookArchetype.CONTRARIAN_STAT
            stop_score = 6.0
        else:
            arch = HookArchetype.BOLD_STATEMENT
            stop_score = 6.0

        extraction_map[body] = AdExtractionOutput(
            hook=HookPattern(
                text=f"Opening line {i}",
                archetype=arch,
                stop_power_score=stop_score,
                why_it_stops_the_scroll="Mechanism explanation.",
            ),
            pain="Trader frustration.",
            concept="Consensus idea.",
            proof_type="testimonial",
        )

    _seed_winning_ads(store, ads)

    mock_client = MockLLMClient(extraction_map=extraction_map)
    result = extract_ad_patterns(
        settings=test_settings,
        paths=run_paths,
        client=mock_client,  # type: ignore[arg-type]
    )

    artifact = store.read("ad_patterns")
    assert isinstance(artifact, AdPatterns)

    underused = artifact.aggregate.underused_high_durability
    assert "social_proof" in underused
    assert "pain_point" in underused
    assert result["underused_boosted"] == underused


# ===========================================================================
# 7. Written artifact validates against AdPatterns (round-trip)
# ===========================================================================


def test_artifact_roundtrip(test_settings: Settings, run_paths: RunPaths):
    """Verify ad_patterns.json validates against schema and round-trips via ArtifactStore."""
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    ads = [
        _make_sample_ad("ad_1", video_duration_s=34.2),
        _make_sample_ad("ad_2", video_duration_s=30.0),
        _make_sample_ad("ad_3", video_duration_s=40.0),
    ]
    _seed_winning_ads(store, ads)

    mock_client = MockLLMClient()
    result = extract_ad_patterns(
        settings=test_settings,
        paths=run_paths,
        client=mock_client,  # type: ignore[arg-type]
    )

    out_path = Path(result["artifact_path"])
    assert out_path.is_file()

    raw_data = json.loads(out_path.read_text(encoding="utf-8"))
    model = AdPatterns.model_validate(raw_data)
    assert model.schema_version == SCHEMA_VERSION
    assert len(model.patterns) == 3
    assert model.aggregate.ad_count == 3
    assert model.aggregate.median_hook_duration_s > 0

    # Ensure store.read works identically
    loaded = store.read("ad_patterns")
    assert isinstance(loaded, AdPatterns)
    assert loaded.model_dump(mode="json") == raw_data


# ===========================================================================
# 8. aggregate_beat_timeline rewrites in place without LLM calls
# ===========================================================================


def test_aggregate_beat_timeline_rewrites_in_place_without_llm(
    test_settings: Settings,
    run_paths: RunPaths,
):
    """Verify aggregate_beat_timeline recomputes aggregate block with 0 LLM calls."""
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    ads = [
        _make_sample_ad("ad_1", video_duration_s=30.0),
        _make_sample_ad("ad_2", video_duration_s=30.0),
        _make_sample_ad("ad_3", video_duration_s=30.0),
    ]
    _seed_winning_ads(store, ads)

    mock_client = MockLLMClient()
    extract_ad_patterns(
        settings=test_settings,
        paths=run_paths,
        client=mock_client,  # type: ignore[arg-type]
    )

    # Modify the aggregate in the artifact manually to simulate a stale/divergent timeline
    art = store.read("ad_patterns")
    assert isinstance(art, AdPatterns)
    art.aggregate.median_hook_duration_s = 99.0
    store.write("ad_patterns", art)

    # Call aggregate_beat_timeline
    res = aggregate_beat_timeline(settings=test_settings, paths=run_paths)
    assert res["llm_calls"] == 0
    assert res["ads_analysed"] == 3

    recomputed = store.read("ad_patterns")
    assert isinstance(recomputed, AdPatterns)
    # Stale 99.0 was replaced by the correct median
    assert recomputed.aggregate.median_hook_duration_s != 99.0
    assert recomputed.aggregate.median_hook_duration_s == 3.0


# ===========================================================================
# 9. BudgetExceeded propagates (Rule A6)
# ===========================================================================


def test_budget_exceeded_propagates(test_settings: Settings, run_paths: RunPaths):
    """Verify BudgetExceeded is not caught as a per-ad failure and propagates out."""
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    ads = [
        _make_sample_ad("ad_1"),
        _make_sample_ad("ad_2"),
        _make_sample_ad("ad_3"),
    ]
    _seed_winning_ads(store, ads)

    mock_client = MockLLMClient(budget_exceeded=True)
    with pytest.raises(BudgetExceeded):
        extract_ad_patterns(
            settings=test_settings,
            paths=run_paths,
            client=mock_client,  # type: ignore[arg-type]
        )


# ===========================================================================
# 10. Fallback when no ads have video_duration_s
# ===========================================================================


def test_fallback_to_video_max_seconds_when_no_durations(
    test_settings: Settings,
    run_paths: RunPaths,
):
    """Verify total_duration_s falls back to video_max_seconds when ads lack duration."""
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    ads = [
        _make_sample_ad("ad_1", video_duration_s=None),
        _make_sample_ad("ad_2", video_duration_s=None),
        _make_sample_ad("ad_3", video_duration_s=None),
    ]
    _seed_winning_ads(store, ads)

    mock_client = MockLLMClient()
    extract_ad_patterns(
        settings=test_settings,
        paths=run_paths,
        client=mock_client,  # type: ignore[arg-type]
    )

    artifact = store.read("ad_patterns")
    assert isinstance(artifact, AdPatterns)
    assert any("settings.video_max_seconds" in w for w in artifact.warnings)
    # The default timeline ends at settings.video_max_seconds (60)
    expected_end = float(test_settings.video_max_seconds)
    assert artifact.aggregate.median_beat_timeline[-1].end_s == expected_end


# ===========================================================================
# 11. Direct call to _extract_one
# ===========================================================================


@pytest.mark.asyncio
async def test_extract_one_direct():
    """Verify _extract_one returns an AdPattern model directly."""
    ad = _make_sample_ad("ad_direct", video_duration_s=30.0)
    mock_client = MockLLMClient()
    sem = asyncio.Semaphore(1)

    pattern = await _extract_one(mock_client, ad, sem=sem)  # type: ignore[arg-type]
    assert isinstance(pattern, AdPattern)
    assert pattern.ad_id == "ad_direct"
    assert pattern.hook.archetype == HookArchetype.CONTRARIAN_STAT
    assert len(pattern.beat_sheet) == 4
