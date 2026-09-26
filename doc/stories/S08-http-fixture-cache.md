# S08 — HTTP fixture cache / `--offline`

**Phase** 2 · **Depends on** S02 · **Blocks** S10, S11, S17
**Spec** `doc/video-ads-agent.md` lines **2521–2619** (§8.5), **4020–4029** (§9.6), **5254–5257** (§16.3)
**Context budget** ~9k (spec 2.5k + story 1.4k + output 5k)
**Produces** `clients/__init__.py`, `clients/http_cache.py`, `tests/test_http_cache.py`

---

## Goal

A content-hash write-through cache that makes `--offline` real. The design property that matters:
**every online run automatically records the fixtures an offline run needs.** Recording is not a
separate chore — it is a side effect of the first successful execution.

This is what satisfies the brief's submission requirement: *"APIFY + TAVILY tokens used — must! so we
can rerun your code! without burning our paid accounts."*

## Interface contract — FROZEN

```python
# clients/http_cache.py

class OfflineFixtureMissing(RuntimeError):
    """Raised in --offline mode when no recorded response exists.
    Deliberately LOUD and ACTIONABLE. A silent fallback to a live call would spend
    the reviewer's credits — the exact thing --offline promises not to do."""

def request_cache_key(method: str, url: str, body: dict | None) -> str:
    """sha256 of json.dumps({"m":METHOD,"u":url,"b":body or {}}, sort_keys=True,
    separators=(",",":")), utf-8. Returns lowercase hex."""

@dataclass
class CachedResponse:
    status_code: int; headers: dict[str, str]; body: object
    @classmethod
    def from_disk(cls, path: Path) -> "CachedResponse": ...
    def to_disk(self, path: Path) -> None: ...

class HttpCache:
    def __init__(self, root: Path, offline: bool): ...
    hits: int; misses: int
    async def request(self, method: str, url: str, *, json_body: dict | None = None,
                      headers: dict[str, str] | None = None, timeout: float = 60.0,
                      client: httpx.AsyncClient | None = None) -> CachedResponse: ...
```

Layout on disk: `fixtures/http/<key[:2]>/<key>.json`.

## Rules that bind this story

- **§8.5** — the cache is consulted in **both** modes. A hit is a hit whether online or offline. Only a
  **miss** differs: offline raises, online fetches and writes through.
- **Rule R1 (B5 — real bug in the spec).** `request_cache_key` hashes the **URL as passed**. Apify
  passes the token as a query parameter (spec line 2275), so a naive caller produces a
  token-in-the-key cache file **and** persists the token into `fixtures/http/**`. Mitigation is
  mandated in the contract below and asserted in the tests.
- **`OfflineFixtureMissing` must never be swallowed.** `clients/apify.py` (S10) and the search clients
  (S11) must let it propagate. Add a module-level comment saying so.
- **No silent live fallback.** If you are tempted to add `offline_fallback=True`, do not.

## Build steps

1. Copy `request_cache_key`, `CachedResponse`, `HttpCache` from spec lines 2557–2618.
2. **Add the B5 guard.** Before hashing, assert the URL carries no secret:
   ```python
   _SECRET_RE = re.compile(r"([?&](token|api[_-]?key)=)|(apify_api_|sk-or-v1-|nvapi-|tvly-)", re.I)
   if _SECRET_RE.search(url):
       raise ValueError(
           "Refusing to cache a URL carrying a credential (Rule R1). Pass a scrubbed URL: "
           "the caller must strip ?token= before calling HttpCache.request().")
   ```
   Then also scrub `headers` (`Authorization`, `x-api-key`) out of the `to_disk` payload — store
   `{"Authorization": "<redacted>"}`. The headers dict is written to disk verbatim by the spec's
   `to_disk`; that is a second leak path.
3. `request()` — copy the spec's flow, then ensure `resp.json()` failures do not corrupt the cache:
   wrap in `try/except ValueError` and raise `ArtifactError`-style with the URL (scrubbed).
4. Tests, with `respx` (already in `requirements-dev.txt`):
   - a miss online fetches once and writes exactly one file
   - a second identical call is a **hit** — `respx` asserts the route was called once total
   - offline + hit → returns the cached body, zero HTTP
   - offline + miss → `OfflineFixtureMissing`, and the message names the record command
   - **the B5 guard raises** on `?token=` and on an `apify_api_` URL
   - **`to_disk` never writes a credential**: cache a response with `Authorization: Bearer secret`,
     then `grep` the written file and assert it is absent
   - key stability: same inputs in a different dict key order → same key

## Decisions the spec leaves open

- **The spec's `_scrub` lives in `clients/apify.py`** (spec line 2274). `http_cache` needs the same
  behaviour but must not import Apify. **Put a local `_scrub_url()` here** and let S10 keep its own
  `_scrub` for log lines. Two small functions beat a circular import; note the duplication in a comment.
- **Fixture retention.** `fixtures/http/**/*.raw.json` is gitignored (spec line 341) pending
  `scrub_fixtures.py` (S34). The scrubbed `<key>.json` files **are** committed — that is the offline
  fixture set.
- **Cache eviction.** None. Fixtures are small and the key is content-addressed; a stale fixture is a
  bug, not clutter.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_http_cache.py -q -v      # green

.venv/Scripts/python -c "
from cwt.clients.http_cache import request_cache_key
a = request_cache_key('POST','https://api.tavily.com/search',{'query':'x','n':1})
b = request_cache_key('post','https://api.tavily.com/search',{'n':1,'query':'x'})
print(a == b)   # True — method case-insensitive, body key order irrelevant

from cwt.clients.http_cache import HttpCache
import asyncio
try:
    asyncio.run(HttpCache(__import__('pathlib').Path('_c'), True).request('POST','https://api.apify.com/v2/acts/x/runs?token=apify_api_x'))
except ValueError as e: print('B5 guard ok:', str(e)[:60])"
```

## Handoff

S10, S11 and S17 all take an `HttpCache` instance and call `.request()` instead of `httpx` directly.
**Callers must pass a token-scrubbed URL** — the guard enforces it. S32 threads the same instance
through a run so both the fixture recording and the offline replay use one code path.
