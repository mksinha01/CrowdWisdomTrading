"""Tests for HTTP fixture cache and --offline support (Story S08)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from cwt.clients.http_cache import (
    CachedResponse,
    HttpCache,
    OfflineFixtureMissing,
    request_cache_key,
)


@pytest.mark.asyncio
async def test_online_miss_fetches_once_and_writes_one_file(tmp_path: Path):
    """A miss online fetches from the network once and writes exactly one cache file."""
    cache = HttpCache(root=tmp_path, offline=False)

    with respx.mock(assert_all_called=True) as respx_mock:
        route = respx_mock.post("https://api.example.com/v1/search").respond(
            status_code=200,
            json={"results": ["hit1", "hit2"]},
            headers={"Content-Type": "application/json"},
        )

        resp = await cache.request(
            "POST",
            "https://api.example.com/v1/search",
            json_body={"query": "trading"},
        )

    assert resp.status_code == 200
    assert resp.body == {"results": ["hit1", "hit2"]}
    assert cache.misses == 1
    assert cache.hits == 0
    assert route.call_count == 1

    # Verify exactly one fixture file was written to disk
    written_files = list(tmp_path.rglob("*.json"))
    assert len(written_files) == 1
    key = request_cache_key("POST", "https://api.example.com/v1/search", {"query": "trading"})
    expected_path = tmp_path / key[:2] / f"{key}.json"
    assert written_files[0] == expected_path


@pytest.mark.asyncio
async def test_online_second_call_is_hit(tmp_path: Path):
    """A second identical call is a hit — respx asserts the route was called once total."""
    cache = HttpCache(root=tmp_path, offline=False)

    with respx.mock(assert_all_called=True) as respx_mock:
        route = respx_mock.post("https://api.example.com/v1/search").respond(
            status_code=200,
            json={"results": ["hit1", "hit2"]},
        )

        resp1 = await cache.request(
            "POST",
            "https://api.example.com/v1/search",
            json_body={"query": "trading"},
        )
        assert resp1.status_code == 200
        assert cache.misses == 1
        assert cache.hits == 0

        # Second call with same inputs
        resp2 = await cache.request(
            "POST",
            "https://api.example.com/v1/search",
            json_body={"query": "trading"},
        )
        assert resp2.status_code == 200
        assert resp2.body == resp1.body
        assert cache.misses == 1
        assert cache.hits == 1
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_offline_hit_returns_cached_body_zero_http(tmp_path: Path):
    """offline + hit returns the cached body with zero HTTP calls."""
    # First, record a response in online mode
    online_cache = HttpCache(root=tmp_path, offline=False)
    with respx.mock() as respx_mock:
        respx_mock.get("https://api.example.com/v1/data").respond(
            status_code=200,
            json={"status": "ok", "items": [1, 2, 3]},
        )
        await online_cache.request("GET", "https://api.example.com/v1/data")

    # Now in offline mode, no routes mocked, any HTTP call would fail
    offline_cache = HttpCache(root=tmp_path, offline=True)
    resp = await offline_cache.request("GET", "https://api.example.com/v1/data")
    assert resp.status_code == 200
    assert resp.body == {"status": "ok", "items": [1, 2, 3]}
    assert offline_cache.hits == 1
    assert offline_cache.misses == 0


@pytest.mark.asyncio
async def test_offline_miss_raises_offline_fixture_missing(tmp_path: Path):
    """offline + miss raises OfflineFixtureMissing and the message names the record command."""
    offline_cache = HttpCache(root=tmp_path, offline=True)

    with pytest.raises(OfflineFixtureMissing) as exc_info:
        await offline_cache.request(
            "POST",
            "https://api.example.com/v1/unrecorded",
            json_body={"a": 1},
        )

    msg = str(exc_info.value)
    assert "No fixture for POST https://api.example.com/v1/unrecorded" in msg
    assert "Expected:" in msg
    assert "python scripts/record_fixtures.py --stage <stage>" in msg


def test_b5_guard_raises_on_token_and_apify_api():
    """Rule R1 (B5): Refuse to cache a URL carrying a credential."""
    forbidden_urls = [
        "https://api.apify.com/v2/acts/x/runs?token=my_secret_token",
        "https://api.apify.com/v2/acts/x/runs?foo=bar&token=secret",
        "https://api.apify.com/v2/acts/x/runs?api_key=secret",
        "https://api.apify.com/v2/acts/x/runs?api-key=secret",
        "https://api.apify.com/v2/acts/x/runs?apikey=secret",
        "https://api.apify.com/v2/acts/apify_api_12345/runs",
        "https://api.openai.com/v1/models/sk-or-v1-abc",
        "https://integrate.api.nvidia.com/v1/models/nvapi-abc",
        "https://api.tavily.com/v1/search?token=tvly-abc",
    ]

    for url in forbidden_urls:
        with pytest.raises(ValueError, match="Refusing to cache a URL carrying a credential"):
            request_cache_key("POST", url, {})


@pytest.mark.asyncio
async def test_b5_guard_raises_via_http_cache_request(tmp_path: Path):
    """HttpCache.request() triggers the B5 guard before any network or disk I/O."""
    cache = HttpCache(root=tmp_path, offline=True)
    with pytest.raises(ValueError) as exc_info:
        await cache.request("POST", "https://api.apify.com/v2/acts/x/runs?token=apify_api_x")

    assert "Refusing to cache a URL carrying a credential (Rule R1)" in str(exc_info.value)
    assert "caller must strip ?token= before calling HttpCache.request()" in str(exc_info.value)


def test_to_disk_never_writes_a_credential(tmp_path: Path):
    """to_disk scrubs Authorization and sensitive headers so credentials never leak to disk."""
    target_path = tmp_path / "fixture.json"
    cached = CachedResponse(
        status_code=200,
        headers={
            "Authorization": "Bearer super_secret_token_12345",
            "x-api-key": "tvly-secret-abcde",
            "Proxy-Authorization": "Basic dXNlcjpwYXNz",
            "Content-Type": "application/json",
            "X-Normal-Header": "safe-value",
        },
        body={"data": "test"},
    )
    cached.to_disk(target_path)

    # Grep the raw text written to disk
    raw_content = target_path.read_text(encoding="utf-8")
    assert "super_secret_token_12345" not in raw_content
    assert "Bearer" not in raw_content
    assert "tvly-secret-abcde" not in raw_content
    assert "dXNlcjpwYXNz" not in raw_content

    # Assert redacted headers
    assert '"Authorization": "<redacted>"' in raw_content
    assert '"x-api-key": "<redacted>"' in raw_content
    assert '"Proxy-Authorization": "<redacted>"' in raw_content
    assert '"Content-Type": "application/json"' in raw_content
    assert '"X-Normal-Header": "safe-value"' in raw_content

    # Load back via from_disk
    loaded = CachedResponse.from_disk(target_path)
    assert loaded.headers["Authorization"] == "<redacted>"
    assert loaded.headers["x-api-key"] == "<redacted>"
    assert loaded.headers["Proxy-Authorization"] == "<redacted>"
    assert loaded.headers["Content-Type"] == "application/json"
    assert loaded.body == {"data": "test"}


def test_key_stability():
    """Key stability: method casing, None vs empty body, and dict order give identical keys."""
    k1 = request_cache_key("POST", "https://api.tavily.com/search", {"query": "x", "n": 1})
    k2 = request_cache_key("post", "https://api.tavily.com/search", {"n": 1, "query": "x"})
    assert k1 == k2

    k3 = request_cache_key(
        "POST",
        "https://api.exa.ai/search",
        {"query": "fintech", "meta": {"sub": "deep", "depth": 2}},
    )
    k4 = request_cache_key(
        "post",
        "https://api.exa.ai/search",
        {"meta": {"depth": 2, "sub": "deep"}, "query": "fintech"},
    )
    assert k3 == k4

    k_none = request_cache_key("GET", "https://api.example.com/items", None)
    k_empty = request_cache_key("get", "https://api.example.com/items", {})
    assert k_none == k_empty


@pytest.mark.asyncio
async def test_invalid_json_does_not_corrupt_cache(tmp_path: Path):
    """When a response body is not valid JSON, raise RuntimeError and do not write to cache."""
    cache = HttpCache(root=tmp_path, offline=False)

    with respx.mock() as respx_mock:
        respx_mock.get("https://api.example.com/v1/bad").respond(
            status_code=502,
            text="<html>Bad Gateway</html>",
        )

        with pytest.raises(RuntimeError) as exc_info:
            await cache.request("GET", "https://api.example.com/v1/bad")

        assert "was not valid JSON" in str(exc_info.value)
        assert "https://api.example.com/v1/bad" in str(exc_info.value)

    # Cache directory should remain empty
    written_files = list(tmp_path.rglob("*.json"))
    assert len(written_files) == 0


@pytest.mark.asyncio
async def test_custom_client_is_not_closed(tmp_path: Path):
    """When a caller provides an AsyncClient, HttpCache must not close it."""
    cache = HttpCache(root=tmp_path, offline=False)
    client = httpx.AsyncClient()

    try:
        with respx.mock() as respx_mock:
            respx_mock.get("https://api.example.com/status").respond(
                status_code=200,
                json={"status": "ok"},
            )
            resp = await cache.request("GET", "https://api.example.com/status", client=client)
            assert resp.status_code == 200
            assert not client.is_closed
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_head_and_empty_body_responses(tmp_path: Path):
    """HEAD requests or 204 No Content responses are cached with body=None."""
    cache = HttpCache(root=tmp_path, offline=False)

    with respx.mock() as respx_mock:
        respx_mock.head("https://example.com/page").respond(
            status_code=200,
            headers={"Content-Length": "0"},
        )
        resp = await cache.request("HEAD", "https://example.com/page")
        assert resp.status_code == 200
        assert resp.body is None

        # Repeat to verify cache hit
        resp_cached = await cache.request("HEAD", "https://example.com/page")
        assert resp_cached.status_code == 200
        assert resp_cached.body is None
        assert cache.hits == 1
