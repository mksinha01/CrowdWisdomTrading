"""Tests for Tavily and Exa search clients (Story S11)."""

from __future__ import annotations

import inspect
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

import cwt.clients.exa as exa_mod
import cwt.clients.tavily as tavily_mod
from cwt.clients.exa import VALID_CATEGORIES, VALID_TYPES, _iso_start, exa_search
from cwt.clients.http_cache import HttpCache, OfflineFixtureMissing, request_cache_key
from cwt.clients.tavily import SearchHit, tavily_search
from cwt.util.retry import _extract_retry_after, retry_async

# ---------------------------------------------------------------------------
# Done-When / Regression: No legacy parameters or enums in source code
# ---------------------------------------------------------------------------


def test_no_legacy_params_in_source():
    """Verify legacy values ('days' string, neural, research paper, tweet) are absent."""
    src = inspect.getsource(tavily_mod) + inspect.getsource(exa_mod)
    for bad in ('"days"', "neural", "research paper", "tweet"):
        assert bad not in src, f"legacy value present in source: {bad}"


# ---------------------------------------------------------------------------
# Exa _iso_start tests
# ---------------------------------------------------------------------------


def test_iso_start_format():
    """Exa requires ISO 8601 date-time ending with T00:00:00.000Z."""
    result = _iso_start(30)
    assert re.match(r"^\d{4}-\d{2}-\d{2}T00:00:00\.000Z$", result)


def test_iso_start_fixed_date():
    """Verify exact calculation with a fixed reference date."""
    fixed_now = datetime(2026, 9, 26, 15, 30, tzinfo=timezone.utc)
    # 2026-09-26 - 30 days = 2026-08-27
    assert _iso_start(30, now=fixed_now) == "2026-08-27T00:00:00.000Z"
    # 2026-09-26 - 7 days = 2026-09-19
    assert _iso_start(7, now=fixed_now) == "2026-09-19T00:00:00.000Z"
    # 2026-09-26 - 0 days = 2026-09-26
    assert _iso_start(0, now=fixed_now) == "2026-09-26T00:00:00.000Z"


# ---------------------------------------------------------------------------
# Tavily: Rule H3, Payload, and Auth tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tavily_rule_h3_payload_time_range(tmp_path: Path):
    """Rule H3 regression: 'days' must not be in payload; time_range must be valid."""
    cache = HttpCache(root=tmp_path, offline=False)

    import json

    captured_payloads: list[dict] = []

    def capture_tavily(request):
        captured_payloads.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"results": []})

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.post("https://api.tavily.com/search").mock(side_effect=capture_tavily)

        # Test days=30 (default) -> "month"
        await tavily_search(
            api_key="tvly-key1",
            query="retail trader signal overload",
            days=30,
            cache=cache,
        )

    payload1 = captured_payloads[0]
    assert "days" not in payload1, "Rule H3 violated: 'days' must not be in payload"
    assert payload1["time_range"] in {"day", "week", "month", "year"}
    assert payload1["time_range"] == "month"
    assert payload1["topic"] == "general"
    assert payload1["include_published_date"] is True
    assert payload1["filter_by_published_date"] is True
    assert payload1["search_depth"] == "advanced"
    assert payload1["max_results"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("days_arg", "expected_time_range"),
    [
        (1, "day"),
        (5, "week"),
        (7, "week"),
        (14, "month"),
        (30, "month"),
        (31, "month"),
        (60, "year"),
        (365, "year"),
    ],
)
async def test_tavily_time_range_mapping(
    tmp_path: Path, days_arg: int, expected_time_range: str
):
    """Verify time_range mapping branch logic: day (<=1), week (<=7), month (<=31), year (>31)."""
    import json

    cache = HttpCache(root=tmp_path, offline=False)
    captured_payload: dict = {}

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.post("https://api.tavily.com/search").mock(
            side_effect=lambda request: captured_payload.update(
                json.loads(request.content.decode())
            )
            or httpx.Response(200, json={"results": []})
        )

        await tavily_search(
            api_key="tvly-key",
            query="test query",
            days=days_arg,
            cache=cache,
        )

    assert "days" not in captured_payload
    assert captured_payload["time_range"] == expected_time_range


@pytest.mark.asyncio
async def test_tavily_auth_header_and_include_domains(tmp_path: Path):
    """Rule R1: Bearer token auth in header, include_domains passed through."""
    import json

    cache = HttpCache(root=tmp_path, offline=False)
    captured_headers: dict = {}
    captured_payload: dict = {}

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.post("https://api.tavily.com/search").mock(
            side_effect=lambda request: (
                captured_headers.update(dict(request.headers)),
                captured_payload.update(json.loads(request.content.decode())),
            )[1]
            or httpx.Response(200, json={"results": []})
        )

        await tavily_search(
            api_key="tvly-secret-12345",
            query="forex signals",
            depth="basic",
            include_domains=["bloomberg.com", "reuters.com"],
            cache=cache,
        )

    assert captured_headers["authorization"] == "Bearer tvly-secret-12345"
    assert "tvly-secret-12345" not in captured_payload.values()
    assert captured_payload["search_depth"] == "basic"
    assert captured_payload["include_domains"] == ["bloomberg.com", "reuters.com"]


@pytest.mark.asyncio
async def test_tavily_search_success_mapping(tmp_path: Path):
    """Verify returned SearchHit objects have provider='tavily' and correct field mapping."""
    cache = HttpCache(root=tmp_path, offline=False)

    mock_results = {
        "results": [
            {
                "title": "Retail Trader Signal Overload",
                "url": "https://example.com/article1",
                "content": "Analysis of retail trader signals and cognitive overload.",
                "published_date": "2026-09-12",
                "score": 0.95,
            },
            {
                "title": "Trading Signals Review",
                "url": "https://example.com/article2",
                "content": "Review of trading signal groups.",
                "published_date": None,
                "score": 0.62,
            },
        ]
    }

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.post("https://api.tavily.com/search").respond(
            status_code=200, json=mock_results
        )

        hits = await tavily_search(
            api_key="tvly-test",
            query="signal overload",
            cache=cache,
        )

    assert len(hits) == 2
    assert all(isinstance(h, SearchHit) for h in hits)
    assert all(h.provider == "tavily" for h in hits)

    h0 = hits[0]
    assert h0.title == "Retail Trader Signal Overload"
    assert h0.url == "https://example.com/article1"
    assert h0.content == "Analysis of retail trader signals and cognitive overload."
    assert h0.published_date == "2026-09-12"
    assert h0.score == 0.95

    h1 = hits[1]
    assert h1.title == "Trading Signals Review"
    assert h1.url == "https://example.com/article2"
    assert h1.content == "Review of trading signal groups."
    assert h1.published_date is None
    assert h1.score == 0.62


@pytest.mark.asyncio
async def test_tavily_429_propagates_http_status_error(tmp_path: Path):
    """Explicit 429 handling: raise HTTPStatusError with Retry-After header intact."""
    cache = HttpCache(root=tmp_path, offline=False)

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.post("https://api.tavily.com/search").respond(
            status_code=429,
            headers={"Retry-After": "15"},
            json={"error": "Rate limit exceeded"},
        )

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await tavily_search(
                api_key="tvly-test",
                query="rate limited query",
                cache=cache,
            )

    err = exc_info.value
    assert err.response.status_code == 429
    assert _extract_retry_after(err) == 15.0


@pytest.mark.asyncio
async def test_tavily_500_propagates_http_status_error(tmp_path: Path):
    """Server error (500) raises HTTPStatusError."""
    cache = HttpCache(root=tmp_path, offline=False)

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.post("https://api.tavily.com/search").respond(
            status_code=500,
            json={"error": "Internal Server Error"},
        )

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await tavily_search(
                api_key="tvly-test",
                query="server error query",
                cache=cache,
            )

    assert exc_info.value.response.status_code == 500


@pytest.mark.asyncio
async def test_tavily_empty_results(tmp_path: Path):
    """Empty results list is returned cleanly without raising an error."""
    cache = HttpCache(root=tmp_path, offline=False)

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.post("https://api.tavily.com/search").respond(
            status_code=200, json={"results": []}
        )

        hits = await tavily_search(
            api_key="tvly-test",
            query="obscure query",
            cache=cache,
        )

    assert hits == []


# ---------------------------------------------------------------------------
# Exa: Rule H4, Enums, ValueError, and Auth tests
# ---------------------------------------------------------------------------


def test_exa_valid_enums():
    """Rule H4: current types and categories must match the spec."""
    assert VALID_TYPES == {
        "instant",
        "fast",
        "auto",
        "deep-lite",
        "deep",
        "deep-reasoning",
    }
    assert VALID_CATEGORIES == {
        "company",
        "publication",
        "news",
        "personal site",
        "financial report",
        "people",
    }


@pytest.mark.asyncio
async def test_exa_invalid_type_raises_value_error(tmp_path: Path):
    """Passing an invalid type raises ValueError client-side."""
    cache = HttpCache(root=tmp_path, offline=False)

    with pytest.raises(ValueError, match="Invalid Exa type"):
        await exa_search(
            api_key="exa-key",
            query="test",
            type="keyword",  # invalid / legacy
            cache=cache,
        )


@pytest.mark.asyncio
async def test_exa_invalid_category_raises_value_error(tmp_path: Path):
    """Passing an invalid category raises ValueError client-side."""
    cache = HttpCache(root=tmp_path, offline=False)

    with pytest.raises(ValueError, match="Invalid Exa category"):
        await exa_search(
            api_key="exa-key",
            query="test",
            category="blog",  # invalid
            cache=cache,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["company", "people"])
async def test_exa_company_people_date_filter_raises_value_error(
    tmp_path: Path, category: str
):
    """Rule H4 corollary: company and people reject date filters with ValueError."""
    cache = HttpCache(root=tmp_path, offline=False)

    with pytest.raises(ValueError) as exc_info:
        await exa_search(
            api_key="exa-key",
            query="forecasting",
            days=30,
            category=category,
            cache=cache,
        )

    msg = str(exc_info.value)
    assert f"Exa category '{category}' rejects date filters" in msg
    assert "Use 'news' or 'publication' when you need a last-month window." in msg


@pytest.mark.asyncio
async def test_exa_company_without_date_filter_allowed(tmp_path: Path):
    """When days=0, company category does not trigger ValueError."""
    import json

    cache = HttpCache(root=tmp_path, offline=False)
    captured_payload: dict = {}

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.post("https://api.exa.ai/search").mock(
            side_effect=lambda request: captured_payload.update(
                json.loads(request.content.decode())
            )
            or httpx.Response(200, json={"results": []})
        )

        await exa_search(
            api_key="exa-key",
            query="apple",
            days=0,
            category="company",
            cache=cache,
        )

    assert captured_payload["category"] == "company"
    assert "startPublishedDate" not in captured_payload


@pytest.mark.asyncio
async def test_exa_auth_header_and_payload_structure(tmp_path: Path):
    """Rule R1: x-api-key auth header; numResults and startPublishedDate in payload."""
    import json

    cache = HttpCache(root=tmp_path, offline=False)
    captured_headers: dict = {}
    captured_payload: dict = {}

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.post("https://api.exa.ai/search").mock(
            side_effect=lambda request: (
                captured_headers.update(dict(request.headers)),
                captured_payload.update(json.loads(request.content.decode())),
            )[1]
            or httpx.Response(200, json={"results": []})
        )

        await exa_search(
            api_key="exa-secret-key-456",
            query="wisdom of crowds forecasting",
            num_results=15,
            days=30,
            category="news",
            cache=cache,
        )

    assert captured_headers["x-api-key"] == "exa-secret-key-456"
    assert captured_headers["content-type"] == "application/json"
    assert captured_payload["type"] == "auto"
    assert captured_payload["numResults"] == 15
    assert captured_payload["category"] == "news"
    assert captured_payload["contents"] == {
        "text": {"maxCharacters": 2500},
        "highlights": True,
    }
    assert "startPublishedDate" in captured_payload
    assert re.match(r"^\d{4}-\d{2}-\d{2}T00:00:00\.000Z$", captured_payload["startPublishedDate"])


@pytest.mark.asyncio
async def test_exa_search_success_mapping(tmp_path: Path):
    """Verify returned SearchHit objects have provider='exa' and correct field mapping."""
    cache = HttpCache(root=tmp_path, offline=False)

    mock_results = {
        "results": [
            {
                "title": "Wisdom of Crowds in Financial Forecasting",
                "url": "https://example.com/crowd-wisdom",
                "text": "Empirical study showing collective prediction accuracy.",
                "publishedDate": "2026-09-05T08:30:00.000Z",
                "score": 0.91,
                "author": "Forecasting Lab",
            }
        ]
    }

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.post("https://api.exa.ai/search").respond(
            status_code=200, json=mock_results
        )

        hits = await exa_search(
            api_key="exa-test",
            query="wisdom of crowds",
            category="publication",
            cache=cache,
        )

    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, SearchHit)
    assert hit.provider == "exa"
    assert hit.title == "Wisdom of Crowds in Financial Forecasting"
    assert hit.url == "https://example.com/crowd-wisdom"
    assert hit.content == "Empirical study showing collective prediction accuracy."
    assert hit.published_date == "2026-09-05T08:30:00.000Z"
    assert hit.score == 0.91


@pytest.mark.asyncio
async def test_exa_429_propagates_http_status_error(tmp_path: Path):
    """Explicit 429 handling: raise HTTPStatusError with Retry-After header intact."""
    cache = HttpCache(root=tmp_path, offline=False)

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.post("https://api.exa.ai/search").respond(
            status_code=429,
            headers={"Retry-After": "8"},
            json={"error": "Rate limit"},
        )

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await exa_search(
                api_key="exa-test",
                query="rate limit exa",
                cache=cache,
            )

    err = exc_info.value
    assert err.response.status_code == 429
    assert _extract_retry_after(err) == 8.0


@pytest.mark.asyncio
async def test_exa_empty_results(tmp_path: Path):
    """Empty results list is returned cleanly without raising an error."""
    cache = HttpCache(root=tmp_path, offline=False)

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.post("https://api.exa.ai/search").respond(
            status_code=200, json={"results": []}
        )

        hits = await exa_search(
            api_key="exa-test",
            query="empty query",
            cache=cache,
        )

    assert hits == []


# ---------------------------------------------------------------------------
# Caching, Offline Playback, and Key Differentiation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_cache_write_through_and_offline_playback(tmp_path: Path):
    """Write through in online mode, then verify offline playback reads from disk
    without network.
    """
    cache_online = HttpCache(root=tmp_path, offline=False)

    mock_data = {
        "results": [
            {
                "title": "Cached Result",
                "url": "https://example.com/cached",
                "content": "Cached body content",
                "published_date": "2026-09-01",
                "score": 0.88,
            }
        ]
    }

    with respx.mock(assert_all_called=True) as respx_mock:
        route = respx_mock.post("https://api.tavily.com/search").respond(
            status_code=200, json=mock_data
        )

        hits_online = await tavily_search(
            api_key="tvly-test",
            query="cached query",
            days=30,
            cache=cache_online,
        )

    assert route.call_count == 1
    assert len(hits_online) == 1
    assert hits_online[0].title == "Cached Result"

    # Now run in offline mode using the same cache root: no network call should be made
    cache_offline = HttpCache(root=tmp_path, offline=True)
    hits_offline = await tavily_search(
        api_key="tvly-test",
        query="cached query",
        days=30,
        cache=cache_offline,
    )

    assert len(hits_offline) == 1
    assert hits_offline[0].title == "Cached Result"
    assert cache_offline.hits == 1


@pytest.mark.asyncio
async def test_search_offline_missing_fixture_raises(tmp_path: Path):
    """In offline mode, a missing fixture raises OfflineFixtureMissing."""
    cache_offline = HttpCache(root=tmp_path, offline=True)

    with pytest.raises(OfflineFixtureMissing):
        await tavily_search(
            api_key="tvly-test",
            query="uncached query",
            cache=cache_offline,
        )


def test_different_windows_produce_different_cache_keys():
    """Callers pass days=30 vs days=7: cache keys differ so they do not collide."""
    # Exa: startPublishedDate is in payload
    p_exa_30 = {
        "query": "forecasting",
        "type": "auto",
        "numResults": 10,
        "contents": {"text": {"maxCharacters": 2500}, "highlights": True},
        "startPublishedDate": "2026-08-27T00:00:00.000Z",
    }
    p_exa_7 = dict(p_exa_30, startPublishedDate="2026-09-19T00:00:00.000Z")

    key_exa_30 = request_cache_key("POST", "https://api.exa.ai/search", p_exa_30)
    key_exa_7 = request_cache_key("POST", "https://api.exa.ai/search", p_exa_7)
    assert key_exa_30 != key_exa_7, "Exa cache keys must differ across different date windows"

    # Tavily: time_range is in payload
    p_tavily_week = {
        "query": "signals",
        "search_depth": "advanced",
        "topic": "general",
        "time_range": "week",
        "max_results": 10,
        "include_published_date": True,
        "filter_by_published_date": True,
    }
    p_tavily_month = dict(p_tavily_week, time_range="month")

    key_tavily_week = request_cache_key("POST", "https://api.tavily.com/search", p_tavily_week)
    key_tavily_month = request_cache_key("POST", "https://api.tavily.com/search", p_tavily_month)
    assert key_tavily_week != key_tavily_month, "Tavily cache keys must differ across time_ranges"


# ---------------------------------------------------------------------------
# Integration with retry_async
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_async_integration_with_tavily_429(tmp_path: Path):
    """retry_async retries on 429 and succeeds when backoff completes."""
    cache = HttpCache(root=tmp_path, offline=False)

    call_count = 0

    def response_callback(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0.01"},
                json={"error": "Rate limit"},
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Success after retry",
                        "url": "https://example.com/ok",
                        "content": "Content",
                        "published_date": "2026-09-20",
                        "score": 0.99,
                    }
                ]
            },
        )

    with respx.mock(assert_all_called=True) as respx_mock:
        respx_mock.post("https://api.tavily.com/search").mock(side_effect=response_callback)

        hits = await retry_async(
            lambda: tavily_search(
                api_key="tvly-test",
                query="retry query",
                cache=cache,
            ),
            max_attempts=3,
            base=0.01,
        )

    assert len(hits) == 1
    assert hits[0].title == "Success after retry"
    assert call_count == 2
