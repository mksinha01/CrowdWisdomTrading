"""Tests for tools.ads (Story S19).

Tests source_winning_ads and rank_winning_ads tools:
- Offline execution with recorded Apify fixtures
- Window filter excluding ads ended before window_start
- Collation ID dedupe and counter
- Drop empty body_text and dedupe duplicate copy
- Excluded reasons sum invariant: sum(excluded_reasons) == items_returned - len(ads)
- Hand-computed ranking order, p90 longevity calculation, degradation detection
- Empty result handling without raising
- Rule R1 secret token gating (assert on raw file bytes)
- LLM reader docstrings (Rule A1 / §7.2)
"""

from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import pytest

from cwt.clients.http_cache import HttpCache
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactStore
from cwt.domain.models import Ad, WinningAds
from cwt.tools.ads import (
    DEFAULT_WEIGHTS,
    rank_winning_ads,
    source_winning_ads,
)
from cwt.util.paths import RunPaths


@pytest.fixture
def test_settings() -> Settings:
    """Fixture returning settings loaded with test defaults."""
    return Settings.from_env()


@pytest.fixture
def run_paths(tmp_path: Path) -> RunPaths:
    """Fixture providing initialized RunPaths in a temporary directory."""
    return RunPaths(tmp_path / "test_run").ensure()


def test_docstrings_written_for_llm_reader():
    """Verify tool docstrings are written for an LLM reader per Rule A1 / §7.2."""
    for fn in (source_winning_ads, rank_winning_ads):
        doc = fn.__doc__
        assert doc is not None, f"{fn.__name__} missing docstring"
        assert "CALL THIS:" in doc, f"{fn.__name__} docstring missing 'CALL THIS:'"
        assert "WHEN NOT TO CALL:" in doc, f"{fn.__name__} docstring missing 'WHEN NOT TO CALL:'"


def test_source_winning_ads_offline_with_fixtures(test_settings: Settings, run_paths: RunPaths):
    """Test source_winning_ads with recorded HTTP fixtures completely offline."""
    fixtures_root = Path("fixtures/http")
    assert fixtures_root.exists()

    cache = HttpCache(root=fixtures_root, offline=True)
    ref_date = date(2026, 9, 27)

    result = source_winning_ads(
        settings=test_settings,
        paths=run_paths,
        keywords=["trading signals"],
        countries=["US"],
        window_days=30,
        max_items=60,
        cache=cache,
        today=ref_date,
    )

    # 1. Verify return dictionary structure
    assert "artifact_path" in result
    assert result["ads_found"] == 4
    assert result["after_window"] == 4
    assert result["charge_usd"] == pytest.approx(0.36)
    assert result["actor_id"] == "apify~facebook-ads-scraper"
    assert isinstance(result["warnings"], list)

    # 2. Verify artifact on disk
    art_file = Path(result["artifact_path"])
    assert art_file.exists()
    assert art_file == run_paths.artifacts / "winning_ads.json"

    # Rule R1: assert no secret token in written file bytes
    raw_bytes = art_file.read_bytes().lower()
    assert b"token" not in raw_bytes

    # 3. Read and validate via ArtifactStore
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    art = store.read("winning_ads")
    assert isinstance(art, WinningAds)
    assert art.query.window_days == 30
    assert art.source.items_returned == 4
    assert art.source.items_after_window_filter == 4
    assert art.source.actual_charge_usd == pytest.approx(0.36)

    # In fixture: 4 items, 1 has no body text -> 3 kept
    assert len(art.ads) == 3
    assert art.ranking.method == "unranked"
    assert art.ranking.excluded_reasons["no_body_text"] == 1
    assert art.ranking.excluded_reasons["outside_window"] == 0
    assert art.ranking.excluded_reasons["duplicate_collation"] == 0
    assert art.ranking.excluded_reasons["duplicate_copy"] == 0

    # Warning for fewer than 8 ads
    assert any("fewer than 8 ads inside window" in w for w in art.warnings)

    # Provenance record exists
    assert run_paths.provenance.exists()


def test_window_filter_and_collation_dedupe(test_settings: Settings, run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch):
    """Test window filter excludes ended ads and collation dedupe collapses rows."""
    ref_date = date(2026, 9, 26)
    # window_start = 2026-08-27 (30 days)

    mock_raw_items = [
        # 1. Valid active ad in window
        {
            "adArchiveID": "ad_1",
            "collationId": "col_1",
            "pageName": "Alpha Trading",
            "isActive": True,
            "startDateFormatted": "2026-09-10",
            "snapshot": {"body": {"text": "Alpha signals for pro traders."}},
        },
        # 2. Duplicate collation_id of ad_1
        {
            "adArchiveID": "ad_2",
            "collationId": "col_1",
            "pageName": "Alpha Trading",
            "isActive": True,
            "startDateFormatted": "2026-09-12",
            "snapshot": {"body": {"text": "Different copy but same collation."}},
        },
        # 3. Inactive ad that ended BEFORE window_start (2026-08-20 < 2026-08-27)
        {
            "adArchiveID": "ad_3",
            "collationId": "col_2",
            "pageName": "Old Trading",
            "isActive": False,
            "startDateFormatted": "2026-08-01",
            "endDateFormatted": "2026-08-20",
            "snapshot": {"body": {"text": "Old signals that ended long ago."}},
        },
        # 4. Inactive ad that started and ended DURING window -> KEPT
        {
            "adArchiveID": "ad_4",
            "collationId": "col_3",
            "pageName": "Recent Trading",
            "isActive": False,
            "startDateFormatted": "2026-08-28",
            "endDateFormatted": "2026-09-05",
            "snapshot": {"body": {"text": "Ran into the window."}},
        },
        # 5. Ad with empty body_text -> DROPPED as no_body_text
        {
            "adArchiveID": "ad_5",
            "collationId": "col_4",
            "pageName": "Silent Ad",
            "isActive": True,
            "startDateFormatted": "2026-09-15",
            "snapshot": {"body": {"text": "   "}},
        },
        # 6. Duplicate copy (same body_text as ad_4, but different collation) -> DROPPED as duplicate_copy
        {
            "adArchiveID": "ad_6",
            "collationId": "col_5",
            "pageName": "Copied Ad",
            "isActive": True,
            "startDateFormatted": "2026-09-18",
            "snapshot": {"body": {"text": "Ran into the window."}},
        },
    ]

    async def mock_run_actor(**kwargs):
        for it in mock_raw_items:
            it["_cwt_actor_id"] = "apify~facebook-ads-scraper"
            it["_cwt_run_id"] = "mock_run_1"
            it["_cwt_dataset_id"] = "mock_ds_1"
            it["_cwt_charge_usd"] = 0.05
            it["_cwt_total_charge_usd"] = 0.30
        return mock_raw_items

    monkeypatch.setattr("cwt.tools.ads.run_actor", mock_run_actor)

    res = source_winning_ads(
        settings=test_settings,
        paths=run_paths,
        keywords=["signals"],
        countries=["US"],
        window_days=30,
        today=ref_date,
    )

    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    art = store.read("winning_ads")
    assert isinstance(art, WinningAds)

    # Check exclusions:
    # ad_3: outside_window (1)
    # ad_2: duplicate_collation (1)
    # ad_5: no_body_text (1)
    # ad_6: duplicate_copy (1)
    # Kept: ad_1, ad_4 (2 ads)
    reasons = art.ranking.excluded_reasons
    assert reasons["outside_window"] == 1
    assert reasons["duplicate_collation"] == 1
    assert reasons["no_body_text"] == 1
    assert reasons["duplicate_copy"] == 1

    # Invariant: sum(excluded_reasons.values()) == items_returned - len(ads)
    assert sum(reasons.values()) == art.source.items_returned - len(art.ads)
    assert art.source.items_returned == 6
    assert len(art.ads) == 2
    assert [a.ad_id for a in art.ads] == ["ad_1", "ad_4"]


def test_rank_winning_ads_hand_computed_order_and_p90(test_settings: Settings, run_paths: RunPaths):
    """Verify ranking order and scores against hand-computed values from the spec."""
    ref_date = date(2026, 9, 26)
    window_days = 30
    window_start = ref_date - timedelta(days=window_days)  # 2026-08-27

    # Handcraft 3 ads
    # Ad A: active_days=22, active=True, platforms=2, started=2026-09-04 (spec example)
    # Ad B: active_days=30, active=True, platforms=4, started=2026-08-27
    # Ad C: active_days=5, active=False, platforms=1, started=2026-09-20, ended=2026-09-25
    ads = [
        Ad(
            ad_id="ad_A",
            collation_id="col_A",
            page_name="Page A",
            is_active=True,
            started_running=date(2026, 9, 4),
            ended_running=None,
            active_days=22,
            publisher_platforms=["FACEBOOK", "INSTAGRAM"],
            display_format="VIDEO",
            body_text="Ad A copy",
            video_duration_s=34.2,
            ad_library_url="https://facebook.com/ads/library/?id=ad_A",
            performance_score=0.0,
            normalised_from="apify~facebook-ads-scraper",
        ),
        Ad(
            ad_id="ad_B",
            collation_id="col_B",
            page_name="Page B",
            is_active=True,
            started_running=date(2026, 8, 27),
            ended_running=None,
            active_days=30,
            publisher_platforms=["FACEBOOK", "INSTAGRAM", "AUDIENCE_NETWORK", "MESSENGER"],
            display_format="VIDEO",
            body_text="Ad B copy",
            video_duration_s=45.0,
            ad_library_url="https://facebook.com/ads/library/?id=ad_B",
            performance_score=0.0,
            normalised_from="apify~facebook-ads-scraper",
        ),
        Ad(
            ad_id="ad_C",
            collation_id="col_C",
            page_name="Page C",
            is_active=False,
            started_running=date(2026, 9, 20),
            ended_running=date(2026, 9, 25),
            active_days=5,
            publisher_platforms=["FACEBOOK"],
            display_format="IMAGE",
            body_text="Ad C copy",
            video_duration_s=None,
            ad_library_url="https://facebook.com/ads/library/?id=ad_C",
            performance_score=0.0,
            normalised_from="apify~facebook-ads-scraper",
        ),
    ]

    art = WinningAds(
        query={
            "keywords": ["signals"],
            "countries": ["US"],
            "window_start": window_start,
            "window_end": ref_date,
            "window_days": window_days,
        },
        source={
            "actor_id": "apify~facebook-ads-scraper",
            "actor_fallbacks_tried": [],
            "run_id": "r1",
            "dataset_id": "d1",
            "items_returned": 3,
            "items_after_window_filter": 3,
            "actual_charge_usd": 0.20,
            "charge_cap_usd": 1.00,
        },
        ranking={
            "method": "unranked",
            "weights": {},
            "excluded_reasons": {"outside_window": 0, "no_body_text": 0, "duplicate_collation": 0, "duplicate_copy": 0},
        },
        ads=ads,
        warnings=[],
    )

    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    store.write("winning_ads", art)

    # active_days values = [5, 22, 30]
    # k = (3 - 1) * 0.9 = 1.8 -> f=1, c=2
    # p90 = vals[1] * 0.2 + vals[2] * 0.8 = 22 * 0.2 + 30 * 0.8 = 4.4 + 24.0 = 28.4
    #
    # Hand-calculate expected score for Ad B:
    # norm_active: 30 / 28.4 > 1.0 -> clamped to 1.0 -> 0.45 * 1.0 = 0.45
    # norm_is_active: 1.0 * 0.25 = 0.25
    # platform_breadth: 4 / 4 = 1.0 -> 0.15 * 1.0 = 0.15
    # recency: (2026-08-27 - 2026-08-27) / 30 = 0.0 -> 0.15 * 0.0 = 0.0
    # Expected Ad B total = 0.45 + 0.25 + 0.15 + 0.0 = 0.85
    #
    # Hand-calculate expected score for Ad A (the spec example):
    # norm_active: 22 / 28.4 = 0.774648 -> 0.45 * 0.774648 = 0.34859
    # norm_is_active: 1.0 * 0.25 = 0.25
    # platform_breadth: 2 / 4 = 0.5 -> 0.15 * 0.5 = 0.075
    # recency: (2026-09-04 - 2026-08-27) = 8 days -> (8/30) * 0.15 = 0.04
    # Expected Ad A total = 0.34859 + 0.25 + 0.075 + 0.04 = 0.7136
    #
    # Hand-calculate expected score for Ad C:
    # norm_active: 5 / 28.4 = 0.176056 -> 0.45 * 0.176056 = 0.0792
    # norm_is_active: 0.0 * 0.25 = 0.0
    # platform_breadth: 1 / 4 = 0.25 -> 0.15 * 0.25 = 0.0375
    # recency: (2026-09-20 - 2026-08-27) = 24 days -> (24/30) * 0.15 = 0.12
    # Expected Ad C total = 0.0792 + 0.0 + 0.0375 + 0.12 = 0.2367

    res = rank_winning_ads(settings=test_settings, paths=run_paths, top_n=2)

    assert res["ranked"] == 2
    assert res["method"] == "weighted_longevity_signal"
    assert res["degraded"] is True  # Only 2 ads have video_duration_s (< 5)

    ranked_art = store.read("winning_ads")
    assert isinstance(ranked_art, WinningAds)
    assert ranked_art.ranking.method == "weighted_longevity_signal"
    assert ranked_art.ranking.weights == DEFAULT_WEIGHTS

    # Full list remains in artifact (3 ads), but top_n sliced the return count
    assert len(ranked_art.ads) == 3

    # Ranking order: Ad B > Ad A > Ad C
    assert ranked_art.ads[0].ad_id == "ad_B"
    assert ranked_art.ads[1].ad_id == "ad_A"
    assert ranked_art.ads[2].ad_id == "ad_C"

    assert ranked_art.ads[0].performance_score == pytest.approx(0.85, abs=0.001)
    assert ranked_art.ads[1].performance_score == pytest.approx(0.7136, abs=0.001)
    assert ranked_art.ads[2].performance_score == pytest.approx(0.2367, abs=0.001)


def test_degradation_non_degraded_when_at_least_5_videos(test_settings: Settings, run_paths: RunPaths):
    """Verify degraded=False when at least 5 ads carry usable video_duration_s."""
    ref_date = date(2026, 9, 26)
    window_days = 30
    window_start = ref_date - timedelta(days=window_days)

    ads = [
        Ad(
            ad_id=f"ad_{i}",
            page_name=f"Page {i}",
            is_active=True,
            started_running=ref_date - timedelta(days=i + 1),
            active_days=i + 1,
            publisher_platforms=["FACEBOOK"],
            display_format="VIDEO",
            body_text=f"Copy {i}",
            video_duration_s=30.0 + i,  # Usable video duration
            ad_library_url=f"https://fb.com/{i}",
            performance_score=0.0,
            normalised_from="apify~facebook-ads-scraper",
        )
        for i in range(5)
    ]

    art = WinningAds(
        query={
            "keywords": ["signals"],
            "countries": ["US"],
            "window_start": window_start,
            "window_end": ref_date,
            "window_days": window_days,
        },
        source={
            "actor_id": "apify~facebook-ads-scraper",
            "actor_fallbacks_tried": [],
            "run_id": "r1",
            "dataset_id": "d1",
            "items_returned": 5,
            "items_after_window_filter": 5,
            "actual_charge_usd": 0.25,
            "charge_cap_usd": 1.00,
        },
        ranking={
            "method": "unranked",
            "weights": {},
            "excluded_reasons": {"outside_window": 0, "no_body_text": 0, "duplicate_collation": 0, "duplicate_copy": 0},
        },
        ads=ads,
        warnings=[],
    )

    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    store.write("winning_ads", art)

    res = rank_winning_ads(settings=test_settings, paths=run_paths)
    assert res["degraded"] is False
    assert res["ranked"] == 5

    ranked_art = store.read("winning_ads")
    assert not any("degraded:" in w for w in ranked_art.warnings)


def test_empty_result_path_does_not_raise(test_settings: Settings, run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch):
    """Verify empty result from Apify writes valid artifact with warning and does not raise."""
    async def mock_empty_run_actor(**kwargs):
        return []

    monkeypatch.setattr("cwt.tools.ads.run_actor", mock_empty_run_actor)

    res = source_winning_ads(
        settings=test_settings,
        paths=run_paths,
        keywords=["signals"],
    )

    assert res["ads_found"] == 0
    assert res["after_window"] == 0
    assert res["charge_usd"] == 0.0
    assert any("no ads found" in w for w in res["warnings"])

    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    art = store.read("winning_ads")
    assert isinstance(art, WinningAds)
    assert len(art.ads) == 0

    # rank_winning_ads on empty artifact also succeeds gracefully
    rank_res = rank_winning_ads(settings=test_settings, paths=run_paths)
    assert rank_res["ranked"] == 0
    assert rank_res["degraded"] is True

    ranked_art = store.read("winning_ads")
    assert isinstance(ranked_art, WinningAds)
    assert ranked_art.ranking.method == "weighted_longevity_signal"
