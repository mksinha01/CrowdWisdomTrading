"""Tavily search client bounded to the last month.

The API does not accept a day-count parameter under general search.
The current parameter is 'time_range', with values: day | week | month | year.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from cwt.clients.http_cache import HttpCache, request_cache_key

logger = logging.getLogger("cwt.search")


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    content: str
    published_date: str | None
    score: float
    provider: str


async def tavily_search(
    *,
    api_key: str,
    query: str,
    max_results: int = 10,
    days: int = 30,
    depth: str = "advanced",
    include_domains: list[str] | None = None,
    cache: HttpCache,
    client: httpx.AsyncClient | None = None,
) -> list[SearchHit]:
    """Tavily /search. Auth is a BEARER HEADER - api_key-in-body is the legacy v1 style."""
    n = max(1, min(days, 365))
    time_range = "day" if n <= 1 else ("week" if n <= 7 else ("month" if n <= 31 else "year"))
    payload: dict[str, Any] = {
        "query": query,
        "search_depth": depth,  # 'advanced' costs 2 credits, everything else 1
        "topic": "general",  # 'news' auto-enables published-date fields
        "time_range": time_range,  # Use time_range instead of day-count parameter
        "max_results": max_results,
        "include_published_date": True,
        "filter_by_published_date": True,
    }
    if include_domains:
        payload["include_domains"] = include_domains

    url = "https://api.tavily.com/search"
    headers = {"Authorization": f"Bearer {api_key}"}

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
        raise httpx.HTTPStatusError("Tavily rate limit", request=dummy_req, response=dummy_resp)

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
            content=str(r.get("content") or ""),
            published_date=r.get("published_date"),
            score=float(r.get("score") or 0.0),
            provider="tavily",
        )
        for r in results
        if isinstance(r, dict)
    ]
