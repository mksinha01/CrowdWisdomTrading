"""Exa search client bounded to the last month.

Auth is the x-api-key header.

Current valid types:
    instant | fast | auto | deep-lite | deep | deep-reasoning

Current valid categories:
    company | publication | news | personal site | financial report | people

'company' and 'people' reject date filters with HTTP 400 - never combine them
with startPublishedDate.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from cwt.clients.http_cache import HttpCache, request_cache_key
from cwt.clients.tavily import SearchHit

logger = logging.getLogger("cwt.search")

VALID_TYPES = frozenset({
    "instant",
    "fast",
    "auto",
    "deep-lite",
    "deep",
    "deep-reasoning",
})

VALID_CATEGORIES = frozenset({
    "company",
    "publication",
    "news",
    "personal site",
    "financial report",
    "people",
})


def _iso_start(days: int, *, now: datetime | None = None) -> str:
    """Exa wants ISO 8601 date-time, not a bare date."""
    base = now if now is not None else datetime.now(timezone.utc)
    d = base - timedelta(days=days)
    return d.strftime("%Y-%m-%dT00:00:00.000Z")


async def exa_search(
    *,
    api_key: str,
    query: str,
    num_results: int = 10,
    days: int = 30,
    category: str | None = None,
    type: str = "auto",
    cache: HttpCache,
    client: httpx.AsyncClient | None = None,
) -> list[SearchHit]:
    """Exa /search. Auth is the x-api-key header."""
    if type not in VALID_TYPES:
        raise ValueError(
            f"Invalid Exa type: {type!r}. Must be one of: {sorted(VALID_TYPES)}"
        )

    payload: dict[str, Any] = {
        "query": query,
        "type": type,
        "numResults": num_results,
        "contents": {"text": {"maxCharacters": 2500}, "highlights": True},
    }

    if category is not None:
        if category not in VALID_CATEGORIES:
            raise ValueError(
                f"Invalid Exa category: {category!r}. Must be one of: {sorted(VALID_CATEGORIES)}"
            )
        payload["category"] = category
        if category in ("company", "people") and days:
            raise ValueError(
                f"Exa category {category!r} rejects date filters. Use 'news' or "
                f"'publication' when you need a last-month window."
            )

    if days:
        payload["startPublishedDate"] = _iso_start(days)

    url = "https://api.exa.ai/search"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }

    resp = await cache.request(
        "POST",
        url,
        json_body=payload,
        headers=headers,
        timeout=60.0,
        client=client,
    )

    if resp.status_code == 429:
        if not cache.offline:
            key = request_cache_key("POST", url, payload)
            cached_file = cache.root / key[:2] / f"{key}.json"
            if cached_file.exists():
                cached_file.unlink(missing_ok=True)
        dummy_req = httpx.Request("POST", url)
        dummy_resp = httpx.Response(
            resp.status_code,
            headers=resp.headers,
            json=resp.body if isinstance(resp.body, (dict, list)) else None,
            request=dummy_req,
        )
        raise httpx.HTTPStatusError("Exa rate limit", request=dummy_req, response=dummy_resp)

    if resp.status_code >= 400:
        if not cache.offline:
            key = request_cache_key("POST", url, payload)
            cached_file = cache.root / key[:2] / f"{key}.json"
            if cached_file.exists():
                cached_file.unlink(missing_ok=True)
        dummy_req = httpx.Request("POST", url)
        dummy_resp = httpx.Response(
            resp.status_code,
            headers=resp.headers,
            json=resp.body if isinstance(resp.body, (dict, list)) else None,
            request=dummy_req,
        )
        dummy_resp.raise_for_status()

    data = resp.body if isinstance(resp.body, dict) else {}
    results = data.get("results", [])
    return [
        SearchHit(
            title=str(r.get("title") or ""),
            url=str(r.get("url") or ""),
            content=str(r.get("text") or ""),
            published_date=r.get("publishedDate"),
            score=float(r.get("score") or 0.0),
            provider="exa",
        )
        for r in results
        if isinstance(r, dict)
    ]
