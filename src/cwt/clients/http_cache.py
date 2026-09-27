"""Content-hash write-through cache. This is the whole of --offline.

The design property that matters: every ONLINE run automatically records the
fixtures needed for an OFFLINE run. Recording is not a separate chore, it is a
side effect of the first successful execution.

That is what makes the submission's "so we can rerun your code without burning
our paid accounts" requirement satisfiable rather than aspirational.

CRITICAL ARCHITECTURAL INVARIANT:
OfflineFixtureMissing must NEVER be caught or swallowed by clients or callers
(e.g. clients/apify.py, clients/tavily.py, clients/exa.py).
In --offline mode, a missing fixture indicates a missing recording, and silent
fallbacks or swallows would risk making live calls that burn paid API credits.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from cwt.util.jsonio import read_json, write_json

logger = logging.getLogger("cwt.cache")

# Secret regex for detecting credentials in URLs (Rule R1 / B5 bug guard)
_SECRET_RE = re.compile(
    r"([?&](token|api[_-]?key)=)|(apify_api_|sk-or-v1-|nvapi-|tvly-)",
    re.I,
)

_SENSITIVE_HEADER_KEYS = frozenset({
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
})


def _scrub_url(url: str) -> str:
    """Scrub sensitive query parameters and known token formats from a URL.

    Duplicated from clients/apify._scrub to avoid circular imports.
    """
    scrubbed = re.sub(r"([?&](?:token|api[_-]?key)=)[^&\s\"']+", r"\1<redacted>", url, flags=re.I)
    scrubbed = re.sub(r"(apify_api_|sk-or-v1-|nvapi-|tvly-)[a-zA-Z0-9_-]+", "<redacted>", scrubbed)
    return scrubbed


def _scrub_headers(headers: dict[str, str]) -> dict[str, str]:
    """Scrub sensitive authentication headers from headers dictionary before persisting to disk."""
    scrubbed: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _SENSITIVE_HEADER_KEYS or _SECRET_RE.search(str(v)):
            scrubbed[k] = "<redacted>"
        else:
            scrubbed[k] = v
    return scrubbed


class OfflineFixtureMissing(RuntimeError):
    """Raised in --offline mode when no recorded response exists.

    Deliberately LOUD and ACTIONABLE. A silent fallback to a live call would
    spend the reviewer's credits, which is the exact thing --offline promises
    not to do.
    """


def request_cache_key(method: str, url: str, body: dict | None) -> str:
    """sha256 of json.dumps({"m":METHOD,"u":url,"b":body or {}}, sort_keys=True,
    separators=(",",":")), utf-8. Returns lowercase hex.
    """
    if _SECRET_RE.search(url):
        raise ValueError(
            "Refusing to cache a URL carrying a credential (Rule R1). Pass a scrubbed URL: "
            "the caller must strip ?token= before calling HttpCache.request()."
        )
    canonical = json.dumps(
        {"m": method.upper(), "u": url, "b": body or {}},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest().lower()


@dataclass
class CachedResponse:
    status_code: int
    headers: dict[str, str]
    body: object

    @classmethod
    def from_disk(cls, path: Path) -> "CachedResponse":
        raw = read_json(path)
        return cls(status_code=raw["status_code"], headers=raw["headers"], body=raw["body"])

    def to_disk(self, path: Path) -> None:
        self.headers = _scrub_headers(self.headers)
        write_json(
            path,
            {
                "status_code": self.status_code,
                "headers": self.headers,
                "body": self.body,
            },
        )


class HttpCache:
    def __init__(self, root: Path, offline: bool):
        self.root = Path(root)
        self.offline = offline
        self.hits = 0
        self.misses = 0

    async def request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> CachedResponse:
        key = request_cache_key(method, url, json_body)
        path = self.root / key[:2] / f"{key}.json"

        if path.exists():
            self.hits += 1
            return CachedResponse.from_disk(path)  # used in BOTH modes

        if self.offline:
            raise OfflineFixtureMissing(
                f"No fixture for {method.upper()} {url}\n"
                f"Expected: {path}\n"
                f"To record it (makes REAL API calls and may spend credits):\n"
                f"    python scripts/record_fixtures.py --stage <stage>"
            )

        self.misses += 1
        own_client = client is None
        client_to_use = client or httpx.AsyncClient(timeout=timeout)
        try:
            resp = await client_to_use.request(
                method,
                url,
                json=json_body,
                headers=headers,
                timeout=timeout,
            )
        finally:
            if own_client:
                await client_to_use.aclose()

        if resp.content == b"" and (method.upper() == "HEAD" or resp.status_code == 204):
            body = None
        else:
            try:
                body = resp.json()
            except ValueError as e:
                raise RuntimeError(
                    f"HTTP response from {_scrub_url(url)} (status {resp.status_code}) "
                    f"was not valid JSON: {e}"
                ) from e

        cached = CachedResponse(
            status_code=resp.status_code,
            headers=dict(resp.headers),
            body=body,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        cached.to_disk(path)  # write-through
        return cached
