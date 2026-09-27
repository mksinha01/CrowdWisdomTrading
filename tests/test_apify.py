"""Tests for Apify Meta Ads Library client (Story S10)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from cwt.clients.apify import (
    BASE,
    ApifyError,
    _scrub,
    build_input,
    normalise_actor_id,
    normalise_ad,
    run_actor,
)
from cwt.clients.http_cache import HttpCache
from cwt.domain.models import Ad

# ---------------------------------------------------------------------------
# Unit tests: _scrub, normalise_actor_id, build_input
# ---------------------------------------------------------------------------


def test_scrub():
    """Verify _scrub redacts query-param tokens in URLs per Rule R1."""
    url = "https://api.apify.com/v2/acts/x/runs?token=apify_api_SECRET&x=1"
    assert _scrub(url) == "https://api.apify.com/v2/acts/x/runs?token=<redacted>&x=1"

    # Token at the end of the query string
    url2 = "https://api.apify.com/v2/datasets/d1/items?clean=true&token=secret123"
    assert _scrub(url2) == "https://api.apify.com/v2/datasets/d1/items?clean=true&token=<redacted>"

    # URL without token remains unmodified
    url3 = "https://api.apify.com/v2/acts/apify~facebook-ads-scraper/runs"
    assert _scrub(url3) == url3


def test_normalise_actor_id():
    """Verify slash replacement to tilde per Rule H1."""
    assert normalise_actor_id("apify/facebook-ads-scraper") == "apify~facebook-ads-scraper"
    assert normalise_actor_id("apify~facebook-ads-scraper") == "apify~facebook-ads-scraper"
    assert (
        normalise_actor_id("curious_coder/facebook-ads-library-scraper")
        == "curious_coder~facebook-ads-library-scraper"
    )


def test_build_input_rule_h2():
    """Rule H2: keyword search goes through startUrls, searchTerms must NOT be present."""
    inp = build_input(
        keywords=["forex signals", "options flow"],
        countries=["US", "GB"],
        window_days=30,
        max_items=60,
    )
    assert "searchTerms" not in inp, "Rule H2 violated: searchTerms must not exist"
    assert inp["resultsLimit"] == 60
    assert inp["activeStatus"] == "Active"
    assert inp["onlyAdsNewerThan"] == "30 days"
    assert inp["isDetailsPerAd"] is False
    assert inp["enrichWithEcommerceData"] is False

    # 2 keywords * 2 countries = 4 startUrls
    urls = inp["startUrls"]
    assert len(urls) == 4
    for u in urls:
        raw_url = u["url"]
        assert (
            "https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country="
            in raw_url
        )
        assert "&search_type=keyword_unordered" in raw_url

    # Check keyword percent-encoding
    us_forex = urls[0]["url"]
    assert "country=US" in us_forex
    assert "q=forex signals" in us_forex or "q=forex%20signals" in us_forex


# ---------------------------------------------------------------------------
# Unit tests: normalise_ad
# ---------------------------------------------------------------------------


def test_normalise_ad_full_and_ad_model_validation():
    """Verify full field mapping and ensure stripped dict validates as domain.models.Ad."""
    raw = {
        "adArchiveID": "1234567890123",
        "collationId": "9876543210987",
        "pageName": "Example Signals Co",
        "pageID": "15087023444",
        "isActive": True,
        "startDateFormatted": "2026-09-04",
        "publisherPlatform": ["FACEBOOK", "INSTAGRAM"],
        "snapshot": {
            "body": {"text": "Most traders follow one voice. Here is what 16,000 sound like."},
            "title": "Collective Intelligence",
            "linkDescription": "See the consensus.",
            "ctaType": "LEARN_MORE",
            "ctaText": "Learn more",
            "displayFormat": "VIDEO",
            "linkUrl": "https://example.com/",
            "images": [],
            "videos": [
                {
                    "videoHdUrl": "https://video.example.com/hd.mp4",
                    "videoDuration": 34.2,
                }
            ],
        },
        "impressions": "100K-500K",  # should be ignored
        "spend": "$1000",  # should be ignored
    }

    ref_date = date(2026, 9, 26)
    norm = normalise_ad(
        raw, actor_id="apify/facebook-ads-scraper", charge_per_item=0.05, today=ref_date
    )

    assert norm["ad_id"] == "1234567890123"
    assert norm["collation_id"] == "9876543210987"
    assert norm["page_name"] == "Example Signals Co"
    assert norm["page_id"] == "15087023444"
    assert norm["is_active"] is True
    assert norm["started_running"] == "2026-09-04"
    assert norm["ended_running"] is None  # null while running
    assert norm["active_days"] == 22  # Sep 26 - Sep 4 = 22
    assert norm["publisher_platforms"] == ["FACEBOOK", "INSTAGRAM"]
    assert norm["body_text"] == "Most traders follow one voice. Here is what 16,000 sound like."
    assert norm["title"] == "Collective Intelligence"
    assert norm["link_description"] == "See the consensus."
    assert norm["cta_type"] == "LEARN_MORE"
    assert norm["cta_text"] == "Learn more"
    assert norm["display_format"] == "VIDEO"
    assert norm["link_url"] == "https://example.com/"
    assert norm["video_urls"] == ["https://video.example.com/hd.mp4"]
    assert norm["video_duration_s"] == 34.2
    assert norm["ad_library_url"] == "https://www.facebook.com/ads/library/?id=1234567890123"
    assert norm["normalised_from"] == "apify~facebook-ads-scraper"
    assert norm["_cwt_charge_usd"] == 0.05

    # impressions and spend must not be read
    assert "impressions" not in norm
    assert "spend" not in norm

    # Validate against domain.models.Ad by stripping internal _cwt_charge_usd annotation
    ad_dict = {k: v for k, v in norm.items() if k != "_cwt_charge_usd"}
    ad_model = Ad.model_validate(ad_dict)
    assert ad_model.ad_id == "1234567890123"
    assert ad_model.active_days == 22


def test_normalise_ad_awkward_cases():
    """Test awkward cases from S10: adArchiveId (lowercase d) & publisherPlatform as bare str."""
    # Case 1: adArchiveId with lowercase d, video fallback to videoSdUrl, text date format
    raw1 = {
        "adArchiveId": "999888777",
        "pageName": "Forex Consensus Pro",
        "isActive": True,
        "startDateFormatted": "Sep 4, 2026",
        "publisherPlatform": ["FACEBOOK"],
        "snapshot": {
            "body": {"text": "Stop guessing alone."},
            "displayFormat": "VIDEO",
            "videos": [{"videoSdUrl": "https://video.example.com/sd.mp4"}],
        },
    }
    norm1 = normalise_ad(
        raw1, actor_id="apify~facebook-ads-scraper", charge_per_item=0.10, today=date(2026, 9, 26)
    )
    assert norm1["ad_id"] == "999888777"
    assert norm1["started_running"] == "2026-09-04"
    assert norm1["video_urls"] == ["https://video.example.com/sd.mp4"]
    assert norm1["publisher_platforms"] == ["FACEBOOK"]

    # Case 2: publisherPlatform as bare string, isActive=False with endDateFormatted
    raw2 = {
        "adArchiveID": "888777666",
        "pageName": "Trading Alerts",
        "isActive": False,
        "startDateFormatted": "2026-08-20",
        "endDateFormatted": "2026-09-10",
        "publisherPlatform": "FACEBOOK",
        "snapshot": {
            "body": {"text": "Trading alerts delivered daily."},
            "displayFormat": "IMAGE",
            "images": [{"originalImageUrl": "https://img.example.com/ad.jpg"}],
        },
    }
    norm2 = normalise_ad(raw2, actor_id="apify~facebook-ads-scraper", charge_per_item=0.10)
    assert norm2["ad_id"] == "888777666"
    assert norm2["is_active"] is False
    assert norm2["started_running"] == "2026-08-20"
    assert norm2["ended_running"] == "2026-09-10"
    assert norm2["active_days"] == 21  # Aug 20 to Sep 10 = 21 days
    assert norm2["publisher_platforms"] == ["FACEBOOK"]
    assert norm2["image_urls"] == ["https://img.example.com/ad.jpg"]

    # Case 3: Missing snapshot completely
    raw3 = {
        "adArchiveID": "777666555",
        "pageName": "Empty Ad",
        "isActive": True,
        "startDateFormatted": "2026-09-15",
    }
    norm3 = normalise_ad(
        raw3, actor_id="apify~facebook-ads-scraper", charge_per_item=0.10, today=date(2026, 9, 26)
    )
    assert norm3["ad_id"] == "777666555"
    assert norm3["body_text"] == ""
    assert norm3["image_urls"] == []
    assert norm3["video_urls"] == []
    assert norm3["display_format"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# Integration tests: run_actor with HttpCache fixtures (offline)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_actor_offline_with_fixtures():
    """Verify run_actor works in offline mode using recorded fixtures under fixtures/http/."""
    fixtures_root = Path("fixtures/http")
    assert fixtures_root.exists(), "fixtures/http directory must exist"

    cache = HttpCache(root=fixtures_root, offline=True)
    input_data = build_input(
        keywords=["trading signals"], countries=["US"], window_days=30, max_items=60
    )

    progress_events: list[str] = []

    items = await run_actor(
        actor_id="apify/facebook-ads-scraper",
        actor_input=input_data,
        token="test_token_123",
        max_charge_usd=1.00,
        cache=cache,
        on_progress=progress_events.append,
        poll_interval_s=0.001,
    )

    # 4 items recorded in fixture
    assert len(items) == 4
    assert "SUCCEEDED" in progress_events

    # Verify charge distribution: 0.36 total usage / 4 items = 0.09 per item
    for it in items:
        assert it["_cwt_charge_usd"] == pytest.approx(0.09)
        assert it["_cwt_actor_id"] == "apify~facebook-ads-scraper"
        assert it["_cwt_fallbacks_tried"] == []

    # Normalise each item and verify
    normalised = [
        normalise_ad(
            it, actor_id="apify~facebook-ads-scraper", charge_per_item=it["_cwt_charge_usd"]
        )
        for it in items
    ]
    assert normalised[0]["ad_id"] == "1234567890123"
    assert normalised[1]["ad_id"] == "2345678901234"
    assert normalised[2]["ad_id"] == "3456789012345"
    assert normalised[3]["ad_id"] == "4567890123456"

    # Verify each normalised ad validates against Ad model
    for ad_dict in normalised:
        clean_ad = {k: v for k, v in ad_dict.items() if k != "_cwt_charge_usd"}
        Ad.model_validate(clean_ad)


# ---------------------------------------------------------------------------
# Integration tests: run_actor with respx (network mock & header verification)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_actor_auth_header_and_polling(tmp_path: Path):
    """Verify Authorization Bearer header is sent, token is not in URL, and polling proceeds."""
    cache = HttpCache(root=tmp_path, offline=False)
    input_data = build_input(
        keywords=["trading signals"], countries=["US"], window_days=30, max_items=60
    )

    token = "secret_apify_token_xyz"
    run_id = "test_run_999"
    ds_id = "test_ds_999"

    with respx.mock(assert_all_called=True) as respx_mock:
        # POST run creation
        respx_mock.post(
            f"{BASE}/acts/apify~facebook-ads-scraper/runs?maxTotalChargeUsd=1.0"
        ).respond(
            status_code=201,
            json={"data": {"id": run_id, "defaultDatasetId": ds_id, "status": "READY"}},
        )

        # GET poll 1: RUNNING, poll 2: SUCCEEDED, poll 3 (run detail): SUCCEEDED
        respx_mock.get(f"{BASE}/actor-runs/{run_id}").side_effect = [
            httpx.Response(200, json={"data": {"id": run_id, "status": "RUNNING"}}),
            httpx.Response(
                200, json={"data": {"id": run_id, "status": "SUCCEEDED", "usageTotalUsd": 0.50}}
            ),
            httpx.Response(
                200, json={"data": {"id": run_id, "status": "SUCCEEDED", "usageTotalUsd": 0.50}}
            ),
        ]

        # GET dataset items
        respx_mock.get(f"{BASE}/datasets/{ds_id}/items?format=json&clean=true").respond(
            status_code=200,
            json=[
                {"adArchiveID": "ad1", "pageName": "Page 1", "startDateFormatted": "2026-09-01"},
                {"adArchiveID": "ad2", "pageName": "Page 2", "startDateFormatted": "2026-09-02"},
            ],
        )

        progress: list[str] = []
        items = await run_actor(
            actor_id="apify/facebook-ads-scraper",
            actor_input=input_data,
            token=token,
            max_charge_usd=1.00,
            cache=cache,
            on_progress=progress.append,
            poll_interval_s=0.001,
        )

        assert len(items) == 2
        assert items[0]["_cwt_charge_usd"] == pytest.approx(0.25)
        assert progress == ["RUNNING", "SUCCEEDED"]

        # Check all outbound requests carried Authorization header and NO token query param
        for call in respx_mock.calls:
            req: httpx.Request = call.request
            assert req.headers["authorization"] == f"Bearer {token}"
            assert "token=" not in str(req.url)


@pytest.mark.asyncio
async def test_run_actor_404_rule_h1(tmp_path: Path):
    """Rule H1: Actor 404 raises ApifyError with clear message naming Rule H1 and tilde."""
    cache = HttpCache(root=tmp_path, offline=False)
    input_data = build_input(
        keywords=["trading signals"], countries=["US"], window_days=30, max_items=60
    )

    with respx.mock() as respx_mock:
        respx_mock.post(
            f"{BASE}/acts/apify~facebook-ads-scraper/runs?maxTotalChargeUsd=1.0"
        ).respond(
            status_code=404,
            json={"error": {"type": "record-not-found", "message": "Actor not found"}},
        )

        with pytest.raises(ApifyError) as exc_info:
            await run_actor(
                actor_id="apify/facebook-ads-scraper",
                actor_input=input_data,
                token="test_tok",
                max_charge_usd=1.00,
                cache=cache,
            )

        err_msg = str(exc_info.value)
        assert "404" in err_msg
        assert "TILDE" in err_msg
        assert "apify~facebook-ads-scraper" in err_msg


@pytest.mark.asyncio
async def test_run_actor_terminal_failure(tmp_path: Path):
    """Verify non-SUCCEEDED terminal status raises ApifyError."""
    cache = HttpCache(root=tmp_path, offline=False)
    input_data = build_input(
        keywords=["trading signals"], countries=["US"], window_days=30, max_items=60
    )

    with respx.mock() as respx_mock:
        respx_mock.post(
            f"{BASE}/acts/apify~facebook-ads-scraper/runs?maxTotalChargeUsd=1.0"
        ).respond(
            status_code=201,
            json={"data": {"id": "run_fail", "defaultDatasetId": "ds_fail", "status": "READY"}},
        )
        respx_mock.get(f"{BASE}/actor-runs/run_fail").respond(
            status_code=200,
            json={"data": {"id": "run_fail", "status": "FAILED"}},
        )

        with pytest.raises(ApifyError) as exc_info:
            await run_actor(
                actor_id="apify/facebook-ads-scraper",
                actor_input=input_data,
                token="test_tok",
                max_charge_usd=1.00,
                cache=cache,
                poll_interval_s=0.001,
            )

        assert "ended FAILED" in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_actor_fallback_on_404(tmp_path: Path):
    """Verify fallback retry on 404 and recording of actor_fallbacks_tried."""
    cache = HttpCache(root=tmp_path, offline=False)
    input_data = build_input(
        keywords=["trading signals"], countries=["US"], window_days=30, max_items=60
    )

    tried_list: list[str] = []

    with respx.mock() as respx_mock:
        # Primary actor 404s
        respx_mock.post(
            f"{BASE}/acts/apify~facebook-ads-scraper/runs?maxTotalChargeUsd=1.0"
        ).respond(
            status_code=404,
            json={"error": {"type": "record-not-found", "message": "Actor not found"}},
        )

        # Fallback actor succeeds
        fb_slug = "curious_coder~facebook-ads-library-scraper"
        respx_mock.post(f"{BASE}/acts/{fb_slug}/runs?maxTotalChargeUsd=1.0").respond(
            status_code=201,
            json={"data": {"id": "run_fb", "defaultDatasetId": "ds_fb", "status": "READY"}},
        )
        respx_mock.get(f"{BASE}/actor-runs/run_fb").respond(
            status_code=200,
            json={"data": {"id": "run_fb", "status": "SUCCEEDED", "usageTotalUsd": 0.20}},
        )
        respx_mock.get(f"{BASE}/datasets/ds_fb/items?format=json&clean=true").respond(
            status_code=200,
            json=[
                {
                    "adArchiveID": "fb_ad_1",
                    "pageName": "Page FB",
                    "startDateFormatted": "2026-09-01",
                }
            ],
        )

        items = await run_actor(
            actor_id="apify/facebook-ads-scraper",
            actor_input=input_data,
            token="test_tok",
            max_charge_usd=1.00,
            cache=cache,
            fallbacks=["curious_coder/facebook-ads-library-scraper"],
            fallbacks_tried=tried_list,
            poll_interval_s=0.001,
        )

        assert len(items) == 1
        assert items[0]["adArchiveID"] == "fb_ad_1"
        assert items[0]["_cwt_actor_id"] == fb_slug
        assert tried_list == [fb_slug]
        assert items[0]["_cwt_fallbacks_tried"] == [fb_slug]


@pytest.mark.asyncio
async def test_run_actor_fallback_on_zero_items(tmp_path: Path):
    """Verify fallback retry on 0 items returned from primary actor."""
    cache = HttpCache(root=tmp_path, offline=False)
    input_data = build_input(
        keywords=["trading signals"], countries=["US"], window_days=30, max_items=60
    )

    tried_list: list[str] = []

    with respx.mock() as respx_mock:
        # Primary actor succeeds with 0 items
        respx_mock.post(
            f"{BASE}/acts/apify~facebook-ads-scraper/runs?maxTotalChargeUsd=1.0"
        ).respond(
            status_code=201,
            json={"data": {"id": "run_zero", "defaultDatasetId": "ds_zero", "status": "READY"}},
        )
        respx_mock.get(f"{BASE}/actor-runs/run_zero").respond(
            status_code=200,
            json={"data": {"id": "run_zero", "status": "SUCCEEDED", "usageTotalUsd": 0.05}},
        )
        respx_mock.get(f"{BASE}/datasets/ds_zero/items?format=json&clean=true").respond(
            status_code=200,
            json=[],  # 0 items!
        )

        # Fallback actor succeeds with 1 item
        fb_slug = "curious_coder~facebook-ads-library-scraper"
        respx_mock.post(f"{BASE}/acts/{fb_slug}/runs?maxTotalChargeUsd=1.0").respond(
            status_code=201,
            json={"data": {"id": "run_fb2", "defaultDatasetId": "ds_fb2", "status": "READY"}},
        )
        respx_mock.get(f"{BASE}/actor-runs/run_fb2").respond(
            status_code=200,
            json={"data": {"id": "run_fb2", "status": "SUCCEEDED", "usageTotalUsd": 0.15}},
        )
        respx_mock.get(f"{BASE}/datasets/ds_fb2/items?format=json&clean=true").respond(
            status_code=200,
            json=[
                {
                    "adArchiveID": "fb_ad_2",
                    "pageName": "Page FB 2",
                    "startDateFormatted": "2026-09-02",
                }
            ],
        )

        items = await run_actor(
            actor_id="apify/facebook-ads-scraper",
            actor_input=input_data,
            token="test_tok",
            max_charge_usd=1.00,
            cache=cache,
            fallbacks=["curious_coder/facebook-ads-library-scraper"],
            fallbacks_tried=tried_list,
            poll_interval_s=0.001,
        )

        assert len(items) == 1
        assert items[0]["adArchiveID"] == "fb_ad_2"
        assert items[0]["_cwt_actor_id"] == fb_slug
        assert tried_list == [fb_slug]
