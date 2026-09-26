# S10 — Apify / Meta Ads Library client

**Phase** 2 · **Depends on** S08, S09 · **Blocks** S19 (`tools/ads.py`)
**Spec** `doc/video-ads-agent.md` lines **2237–2402** (§8.3), **4506–4587** (Rules H1–H5), **4617–4631** (Rule A2), **5265–5294** (§17 Apify)
**Context budget** ~14k (spec 4k + story 1.6k + output 8k)
**Produces** `clients/apify.py`, `tests/test_apify.py`

---

## Goal

Source currently-running Meta ads. Three failure modes here are silent — the run succeeds and returns
the wrong thing — which is exactly why this module is small, heavily commented, and has a normalisation
boundary nothing downstream crosses.

The signature of this module: **normalise once, here.** After this file, no other module ever reads a
raw Apify field name.

## Interface contract — FROZEN

```python
# clients/apify.py
BASE = "https://api.apify.com/v2"
_TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}

class ApifyError(RuntimeError): ...

def _scrub(text: str) -> str:
    """re.sub(r'([?&]token=)[^&\\s\"\\']+', r'\\1<redacted>', text).
    Not paranoia: the submission is a PUBLIC repo and Apify passes the token as a
    QUERY PARAMETER, so it lands in URLs, error bodies and httpx reprs by default."""

def normalise_actor_id(actor_id: str) -> str:
    """Accept either spelling; always emit the REST-safe tilde form."""

def build_input(*, keywords: list[str], countries: list[str], window_days: int,
                max_items: int) -> dict[str, Any]: ...

def normalise_ad(raw: dict, *, actor_id: str, charge_per_item: float) -> dict:
    """Raw actor item -> the Ad model's field names. THE ONLY place raw names appear."""

async def run_actor(*, actor_id: str, actor_input: dict, token: str, max_charge_usd: float,
                    timeout_s: int = 900, cache: HttpCache,
                    on_progress: Callable[[str], None] | None = None) -> list[dict]: ...
```

## Rules that bind this story

- **Rule H1** — actor id uses a **TILDE** in REST URLs. `apify/facebook-ads-library-scraper` **does not
  exist**; every actor with that slug is third-party. A slash in the path segment 404s even for a
  valid actor. `normalise_actor_id` exists because of this and nothing else.
- **Rule H2** — there is **no `searchTerms` field**. Keyword search is expressed through `startUrls` as
  a Facebook Ad Library search URL. Writing `searchTerms` **fails silently**: the run SUCCEEDS and
  returns the wrong ads. No error. You will not notice for an hour.
- **Rule H5** — the sync endpoint (`run-sync-get-dataset-items`) hard-times-out at **300s**. Meta Ads
  Library runs routinely exceed that → HTTP 408 with the run still going server-side. **Always async:**
  `POST /runs` → poll `/actor-runs/{id}` → fetch `/datasets/{id}/items`.
- **Rule A2** — every run carries `maxTotalChargeUsd`. The free tier is **$5/month** and credits
  **expire** at cycle end; a misconfigured run can consume the month in one call. Belt
  (`maxTotalChargeUsd`), braces (`resultsLimit`), plus the content cache.
- **Rule R1** — `_scrub` every URL before it reaches a log line, an exception message, or an artifact.

## Build steps

1. Copy `_scrub`, `normalise_actor_id`, `build_input`, `run_actor` from spec lines 2274–2375.
2. **`build_input` — copy the `startUrls` construction exactly** (lines 2301–2318). Note the nested
   `httpx.QueryParams({'q': kw})['q']` — it percent-encodes the keyword. Do not replace it with a bare
   f-string; a keyword containing `&` or a space silently breaks the search URL.
3. **Route every HTTP call through `HttpCache`**, not raw `httpx`. Do **not** put `token` in the URL —
   pass it as a param to the cache's `request()`? No: the cache hashes the URL. **Pass the token via
   the `Authorization: Bearer` header** instead of `?token=`. Apify accepts both; the header form keeps
   the credential out of the cache key entirely. Add a comment recording that choice.
4. `run_actor` flow: POST → 404 check naming Rule H1 → poll every 10s until `_TERMINAL` or deadline →
   non-`SUCCEEDED` raises → fetch dataset with `format=json&clean=true` → re-fetch run detail for
   `usageTotalUsd` → distribute `charge / len(items)` as `_cwt_charge_usd`.
5. **`normalise_ad`** — implement the field-mapping table from spec lines 2381–2399 **exactly**:

   | Raw | Normalised | Note |
   |---|---|---|
   | `adArchiveID` / `adArchiveId` | `ad_id` | prefer the capital-ID spelling; the actor emits both |
   | `collationId` / `collationCount` | `collation_id` | same ad, different audiences |
   | `pageName` / `pageID`/`pageId` | `page_name` / `page_id` | |
   | `isActive` | `is_active` | |
   | `startDateFormatted` | `started_running` | normalise to `YYYY-MM-DD`; actor format varies |
   | `endDateFormatted` | `ended_running` | `null` while running |
   | `publisherPlatform` | `publisher_platforms` | **may be a str OR a list — coerce** |
   | `snapshot.body.text` | `body_text` | **nested** — this is the ad copy |
   | `snapshot.title` / `snapshot.linkDescription` | `title` / `link_description` | |
   | `snapshot.ctaType` / `ctaText` | `cta_type` / `cta_text` | |
   | `snapshot.displayFormat` | `display_format` | IMAGE / VIDEO / CAROUSEL |
   | `snapshot.linkUrl` | `link_url` | |
   | `snapshot.images[].originalImageUrl` | `image_urls` | |
   | `snapshot.videos[].videoHdUrl` | `video_urls` | fall back to `videoSdUrl` |

   Every `snapshot.*` access must be `.get()`-chained — `snapshot` is absent on some ad types.
6. Compute `active_days` from `started_running` → today (or `ended_running`). `video_duration_s` is
   **frequently absent** — leave `None`; do not fabricate.
7. `impressions` and `spend` are absent or bucketed on commercial ads (spec line 2401). **Do not read
   them.** The ranker (S19) must degrade gracefully without them.
8. Tests: recorded fixtures under `fixtures/http/`, including the two known awkward cases — a dict
   variant with `adArchiveId` (lowercase `d`) and one with `publisherPlatform` as a bare string.

## Decisions the spec leaves open

- **Token transport (above).** The spec uses `params={"token": token}`. Switching to an
  `Authorization` header removes the credential from the cache key and from every logged URL. Record
  the deviation in a module comment — this is a deliberate, pre-authorised deviation under Rule R1,
  which outranks stylistic fidelity to §8.3.
- **`actor_fallbacks_tried`.** `APIFY_ADS_ACTOR_FALLBACKS` exists in config but §8.3 never tries them.
  Implement: on a 404 or a zero-item `SUCCEEDED` run, retry once with each fallback, and record which
  were tried. S19 writes that list into `winning_ads.source.actor_fallbacks_tried`.
- **`_cwt_charge_usd`** is an internal annotation, not a model field. Strip it before constructing the
  `Ad` model in S19.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_apify.py -q -v      # green, no network

.venv/Scripts/python -c "
from cwt.clients.apify import build_input, normalise_actor_id, _scrub
print(normalise_actor_id('apify/facebook-ads-scraper'))       # apify~facebook-ads-scraper
i = build_input(keywords=['forex signals'], countries=['US'], window_days=30, max_items=60)
assert 'searchTerms' not in i, 'Rule H2 violated'
print(i['startUrls'][0]['url'])
assert i['onlyAdsNewerThan'] == '30 days'
print(_scrub('https://api.apify.com/v2/acts/x/runs?token=apify_api_SECRET&x=1'))"
# -> ...?token=<redacted>&x=1
```

## Handoff

S19 (`cwt_source_winning_ads`) calls `build_input` + `run_actor` + `normalise_ad`, filters to the
window, and constructs `WinningAds`. **No module outside this file knows a raw Apify field name.**
S08's cache guard will raise if you regress to `?token=`.
