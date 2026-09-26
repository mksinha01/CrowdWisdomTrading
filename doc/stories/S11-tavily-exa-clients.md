# S11 — Tavily + Exa search clients

**Phase** 2 · **Depends on** S08 · **Blocks** S21 (`tools/research.py`)
**Spec** `doc/video-ads-agent.md` lines **2404–2519** (§8.4), **4536–4572** (Rules H3, H4), **5296–5329** (§17)
**Context budget** ~11k (spec 3k + story 1.5k + output 6k)
**Produces** `clients/tavily.py`, `clients/exa.py`, `tests/test_search.py`

---

## Goal

Two search clients, both bounded to the last month. The brief requires research *"limit search time to
last month"* — and on both APIs the obvious way to express that is a **legacy parameter that is
silently ignored**, producing unrestricted results and a research window that is a lie.

## Interface contract — FROZEN

```python
# clients/tavily.py  ────────────────────────────────────────────
@dataclass(frozen=True)
class SearchHit:
    title: str; url: str; content: str
    published_date: str | None; score: float; provider: str    # "tavily" | "exa"

async def tavily_search(*, api_key: str, query: str, max_results: int = 10, days: int = 30,
                        depth: str = "advanced", include_domains: list[str] | None = None,
                        cache: HttpCache, client: httpx.AsyncClient) -> list[SearchHit]: ...

# clients/exa.py  ───────────────────────────────────────────────
def _iso_start(days: int) -> str: ...      # ISO 8601 date-time, NOT a bare date

async def exa_search(*, api_key: str, query: str, num_results: int = 10, days: int = 30,
                     category: str | None = None, cache: HttpCache,
                     client: httpx.AsyncClient) -> list[SearchHit]: ...
```

`SearchHit` is defined once (in `tavily.py`) and imported by `exa.py`. Both return the same shape so
S21's three research angles are provider-agnostic — the `provider` field records which one answered.

## Rules that bind this story

- **Rule H3 — Tavily has no `days` parameter.** It is a legacy news-topic-only parameter that
  third-party docs still describe. Passing it is **SILENTLY IGNORED**: you get unrestricted results and
  the "last 30 days" window in your brief is a lie. The current parameter is `time_range`, values
  `day | week | month | year`, which works under `topic="general"`.
- **Rule H4 — Exa's enums changed.** `type`: `instant | fast | auto | deep-lite | deep | deep-reasoning`
  (`neural` and `keyword` are legacy and **400**). `category`: `company | publication | news |
  personal site | financial report | people` (`research paper` and `tweet` are legacy and **400**).
- **Rule H4 corollary** — `company` and `people` **REJECT date filters with HTTP 400**. The spec raises
  a `ValueError` client-side rather than letting it 400. Keep that.
- **§2 line 252 / §16.1** — `search_depth="advanced"` costs **2 credits**; everything else 1. The free
  Tavily plan is 1,000 credits/month ≈ 500 advanced searches. Budget accordingly; do not silently
  upgrade depth.
- **Rule R1** — Tavily auth is a **Bearer header**; Exa is `x-api-key`. Neither key goes in a URL, so
  the cache key stays clean. Do not regress to `api_key`-in-body (the legacy v1 style).

## Build steps

1. `SearchHit` + `tavily_search` — copy spec lines 2427–2474. The `time_range` derivation at line 2449
   is buggy as written: `"month" if n <= 31 else ("week" if n <= 7 else "year")` — the `week` branch
   is unreachable. **Fix it:** `"week" if n <= 7 else ("month" if n <= 31 else "year")`.
2. `tavily_search` payload — keep `include_published_date: True` and `filter_by_published_date: True`;
   without them `published_date` comes back `None` and every claim fails the research prompt's
   sourcing requirement.
3. Explicit 429 handling: raise `httpx.HTTPStatusError` so `retry_async` (S02) can honour `Retry-After`.
4. `_iso_start` + `exa_search` — copy spec lines 2437–2440 and 2492–2518.
5. **Keep the client-side `ValueError`** for `category in ("company","people")` combined with `days`.
   The message must name the working alternative: *"Use 'news' or 'publication' when you need a
   last-month window."*
6. Route both through `HttpCache.request()`. Callers pass `days=30`; the cache key includes the
   `startPublishedDate` so two different windows are two fixtures.
7. Tests (recorded fixtures; `respx` for assertions):
   - **Rule H3 regression test**: build the payload and assert `"days" not in payload` and
     `payload["time_range"] in {"day","week","month","year"}`. This is the test that stops someone
     "fixing" the window back to a no-op.
   - **Rule H4 regression tests**: `type` is one of the current six; `category` is one of the current
     six; `neural`/`research paper` are absent from the module entirely (grep the source).
   - the `company` + date-filter `ValueError` fires
   - 429 propagates as `HTTPStatusError`
   - both clients return `SearchHit` with `provider` set correctly

## Decisions the spec leaves open

- **Retry/exhausted behaviour.** Neither client retries internally — `retry_async` owns that. A
  `SearchHit` list that comes back empty is **not** an error; S21 records it in
  `search_queries_used` and lowers the angle's confidence.
- **`max_results` vs `num_results`.** Tavily uses `max_results`, Exa `numResults`. Keep the two
  signatures different — do not "unify" them, the payload keys differ.
- **Content truncation.** Exa truncates to `maxCharacters: 2500`; Tavily returns its own `content`.
  Do not add a shared truncation step — the prompts were tuned against these two shapes.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_search.py -q -v      # green, no network

.venv/Scripts/python -c "
import inspect, cwt.clients.tavily as t, cwt.clients.exa as e
src = inspect.getsource(t) + inspect.getsource(e)
for bad in ('\"days\"', 'neural', 'research paper', 'tweet'):
    assert bad not in src, f'legacy value present: {bad}'
print('no legacy params/enums ok')
print(e._iso_start(30))   # 2026-08-27T00:00:00.000Z
"
```

## Handoff

S21's `cwt_research_angle` calls both clients for each of the three angles, dedupes by URL, and writes
`ResearchClaim`s with `provider` set from `SearchHit.provider`. Because everything is cached, re-running
a research angle costs zero credits — that is what makes `--force-stage research` cheap.
