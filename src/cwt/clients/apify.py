"""Apify — Meta Ads Library client.

TWO FACTS THAT WILL COST YOU AN HOUR IF YOU FORGET THEM:

1. `apify/facebook-ads-library-scraper` DOES NOT EXIST. Every actor with that slug
   is third-party. The official one is `apify~facebook-ads-scraper`.

2. In REST URLs the actor id uses a TILDE, not a slash:
       https://api.apify.com/v2/acts/apify~facebook-ads-scraper/runs
   A slash in that path returns 404.

Also: the run-sync endpoint hard-times-out at 300s. Meta Ads Library runs routinely
exceed that. ALWAYS use the async path (Rule H5).

TOKEN TRANSPORT DEVIATION NOTE (Rule R1):
The original §8.3 spec snippet passed `params={"token": token}` in the URL.
Under Rule R1 (and as pre-authorised in doc/stories/S10-apify-client.md), we deliberately
switch to passing the token via the `Authorization: Bearer <token>` header.
This keeps the secret out of:
  - Cache key hashes (HttpCache hashes method, url, body)
  - Recorded fixture paths and disk files
  - Log lines, httpx representations, and exception messages
Apify REST API v2 fully supports both Bearer header and query param authentication.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime
from typing import Any, Callable

import httpx

from cwt.clients.http_cache import HttpCache, request_cache_key

logger = logging.getLogger("cwt.apify")

BASE = "https://api.apify.com/v2"
_TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class ApifyError(RuntimeError):
    pass


def _scrub(text: str) -> str:
    """Remove the API token from anything that could reach a log or an artifact.

    This is not paranoia. The submission is a PUBLIC repo, and Apify passes the
    token as a QUERY PARAMETER by default, so it lands in URLs, error bodies
    and httpx request reprs.
    """
    return re.sub(r"([?&]token=)[^&\s\"']+", r"\1<redacted>", text)


def normalise_actor_id(actor_id: str) -> str:
    """Accept either spelling; always emit the REST-safe tilde form (Rule H1)."""
    return actor_id.replace("/", "~")


def build_input(
    *,
    keywords: list[str],
    countries: list[str],
    window_days: int,
    max_items: int,
) -> dict[str, Any]:
    """Build the actor input.

    There is NO `searchTerms` field on the official actor — keyword search is
    expressed through `startUrls` (Rule H2). Writing `searchTerms` fails SILENTLY:
    the run succeeds and returns the wrong ads.

    'Last 30 days' is `onlyAdsNewerThan` — it accepts a relative string like
    "30 days" as well as an absolute YYYY-MM-DD.
    """
    urls: list[dict[str, str]] = []
    for kw in keywords:
        for cc in countries:
            urls.append(
                {
                    "url": (
                        "https://www.facebook.com/ads/library/"
                        f"?active_status=active&ad_type=all&country={cc}"
                        f"&q={httpx.QueryParams({'q': kw})['q']}&search_type=keyword_unordered"
                    )
                }
            )
    return {
        "startUrls": urls,
        "resultsLimit": max_items,
        "activeStatus": "Active",
        "onlyAdsNewerThan": f"{window_days} days",
        "isDetailsPerAd": False,
        "enrichWithEcommerceData": False,
    }


def _normalise_date(val: Any) -> str | None:
    """Helper to parse varied date representations into YYYY-MM-DD."""
    if not val:
        return None
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, (int, float)):
        ts = float(val)
        if ts > 1e11:
            ts /= 1000.0
        try:
            return date.fromtimestamp(ts).isoformat()
        except (ValueError, OSError):
            return None
    if not isinstance(val, str):
        val = str(val)
    val = val.strip()
    if not val:
        return None

    # Check YYYY-MM-DD at the start
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", val)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # Try ISO timestamp
    try:
        clean = val.replace("Z", "+00:00")
        return datetime.fromisoformat(clean).date().isoformat()
    except ValueError:
        pass

    # Try common textual date formats
    for fmt in (
        "%b %d, %Y",
        "%B %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d %Y",
        "%B %d %Y",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%Y.%m.%d",
    ):
        try:
            return datetime.strptime(val, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalise_ad(
    raw: dict,
    *,
    actor_id: str,
    charge_per_item: float = 0.0,
    today: date | None = None,
) -> dict:
    """Raw actor item -> the Ad model's field names.

    THE ONLY place raw Apify names appear. Enforces spec §8.3 field-mapping table.
    """
    # 1. ad_id: prefer capital-ID spelling; actor emits both adArchiveID and adArchiveId
    raw_id = raw.get("adArchiveID")
    if raw_id is None or raw_id == "":
        raw_id = raw.get("adArchiveId")
    ad_id = str(raw_id) if raw_id is not None else ""

    # 2. collation_id: collationId or collationCount
    col_id = raw.get("collationId")
    if col_id is None:
        col_id = raw.get("collationCount")
    collation_id = str(col_id) if col_id is not None else None

    # 3. page_name & page_id
    page_name = str(raw.get("pageName") or "")
    p_id = raw.get("pageID")
    if p_id is None:
        p_id = raw.get("pageId")
    page_id = str(p_id) if p_id is not None else None

    # 4. is_active
    raw_active = raw.get("isActive")
    if isinstance(raw_active, bool):
        is_active = raw_active
    elif isinstance(raw_active, str):
        is_active = raw_active.lower() in ("true", "active", "1")
    elif raw_active is not None:
        is_active = bool(raw_active)
    else:
        is_active = True

    # 5. started_running & ended_running
    raw_start = raw.get("startDateFormatted") or raw.get("startDate")
    started_running = _normalise_date(raw_start)
    ref_d = today or date.today()
    if not started_running:
        started_running = ref_d.isoformat()

    if is_active:
        # null while running
        ended_running = None
    else:
        raw_end = raw.get("endDateFormatted") or raw.get("endDate")
        ended_running = _normalise_date(raw_end)

    # 6. active_days: started_running -> today (or ended_running)
    start_d = date.fromisoformat(started_running)
    if ended_running:
        end_d = date.fromisoformat(ended_running)
    else:
        end_d = ref_d
    active_days = max(0, (end_d - start_d).days)

    # 7. publisher_platforms: may be a str OR a list — coerce
    raw_plat = raw.get("publisherPlatform") or raw.get("publisherPlatforms") or []
    if isinstance(raw_plat, str):
        raw_plat_str = raw_plat.strip()
        if raw_plat_str.startswith("[") and raw_plat_str.endswith("]"):
            try:
                parsed = json.loads(raw_plat_str)
                if isinstance(parsed, list):
                    publisher_platforms = [str(p).strip().upper() for p in parsed if str(p).strip()]
                else:
                    publisher_platforms = [str(parsed).strip().upper()]
            except Exception:
                publisher_platforms = [raw_plat_str.upper()]
        elif "," in raw_plat_str:
            publisher_platforms = [p.strip().upper() for p in raw_plat_str.split(",") if p.strip()]
        elif raw_plat_str:
            publisher_platforms = [raw_plat_str.upper()]
        else:
            publisher_platforms = []
    elif isinstance(raw_plat, (list, tuple)):
        publisher_platforms = [str(p).strip().upper() for p in raw_plat if str(p).strip()]
    else:
        publisher_platforms = []

    # 8. snapshot: chained .get() access — snapshot is absent on some ad types
    snapshot = raw.get("snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}

    # snapshot.body.text
    body_obj = snapshot.get("body")
    if isinstance(body_obj, dict):
        body_text = str(body_obj.get("text") or "")
    elif isinstance(body_obj, str):
        body_text = body_obj
    else:
        body_text = str(raw.get("body_text") or raw.get("body") or "")

    # snapshot.title & linkDescription
    title_val = snapshot.get("title") or raw.get("title")
    title = str(title_val) if title_val is not None else None

    link_desc_val = snapshot.get("linkDescription") or raw.get("linkDescription")
    link_description = str(link_desc_val) if link_desc_val is not None else None

    # snapshot.ctaType / ctaText
    cta_t = snapshot.get("ctaType") or raw.get("ctaType")
    cta_type = str(cta_t) if cta_t is not None else None

    cta_txt = snapshot.get("ctaText") or raw.get("ctaText")
    cta_text = str(cta_txt) if cta_txt is not None else None

    # snapshot.displayFormat
    disp = snapshot.get("displayFormat") or raw.get("displayFormat") or "UNKNOWN"
    display_format = str(disp).upper()

    # snapshot.linkUrl
    l_url = snapshot.get("linkUrl") or raw.get("linkUrl")
    link_url = str(l_url) if l_url is not None else None

    # snapshot.images[].originalImageUrl
    image_urls: list[str] = []
    imgs = snapshot.get("images") or raw.get("images") or []
    if isinstance(imgs, list):
        for img in imgs:
            if isinstance(img, dict):
                u = img.get("originalImageUrl") or img.get("url")
                if u:
                    image_urls.append(str(u))
            elif isinstance(img, str) and img:
                image_urls.append(img)

    # snapshot.videos[].videoHdUrl (fall back to videoSdUrl)
    video_urls: list[str] = []
    vids = snapshot.get("videos") or raw.get("videos") or []
    if isinstance(vids, list):
        for vid in vids:
            if isinstance(vid, dict):
                u = vid.get("videoHdUrl") or vid.get("videoSdUrl") or vid.get("url")
                if u:
                    video_urls.append(str(u))
            elif isinstance(vid, str) and vid:
                video_urls.append(vid)

    # video_duration_s is frequently absent — leave None; do not fabricate
    video_duration_s: float | None = None
    if raw.get("video_duration_s") is not None:
        try:
            video_duration_s = float(raw["video_duration_s"])
        except (ValueError, TypeError):
            video_duration_s = None
    elif isinstance(vids, list):
        for vid in vids:
            if isinstance(vid, dict) and vid.get("videoDuration"):
                try:
                    video_duration_s = float(vid["videoDuration"])
                    break
                except (ValueError, TypeError):
                    pass

    # ad_library_url
    ad_lib_url = raw.get("ad_library_url") or raw.get("adLibraryUrl")
    if not ad_lib_url and ad_id:
        ad_library_url = f"https://www.facebook.com/ads/library/?id={ad_id}"
    elif ad_lib_url:
        ad_library_url = str(ad_lib_url)
    else:
        ad_library_url = ""

    # performance_score
    perf = raw.get("performance_score")
    try:
        performance_score = float(perf) if perf is not None else 0.0
    except (ValueError, TypeError):
        performance_score = 0.0

    normalised_from = normalise_actor_id(actor_id)
    cwt_charge = float(raw.get("_cwt_charge_usd", charge_per_item))

    # Note: impressions and spend are deliberately ignored (spec line 2401)
    return {
        "ad_id": ad_id,
        "collation_id": collation_id,
        "page_name": page_name,
        "page_id": page_id,
        "is_active": is_active,
        "started_running": started_running,
        "ended_running": ended_running,
        "active_days": active_days,
        "publisher_platforms": publisher_platforms,
        "display_format": display_format,
        "cta_type": cta_type,
        "cta_text": cta_text,
        "link_url": link_url,
        "body_text": body_text,
        "title": title,
        "link_description": link_description,
        "image_urls": image_urls,
        "video_urls": video_urls,
        "video_duration_s": video_duration_s,
        "ad_library_url": ad_library_url,
        "performance_score": performance_score,
        "normalised_from": normalised_from,
        "_cwt_charge_usd": cwt_charge,
    }


async def _run_actor_single(
    *,
    actor: str,
    actor_input: dict,
    token: str,
    max_charge_usd: float,
    timeout_s: int,
    cache: HttpCache,
    on_progress: Callable[[str], None] | None,
    poll_interval_s: float,
) -> list[dict]:
    """Execute a single Apify actor run via async POST -> poll -> fetch items."""
    headers = {"Authorization": f"Bearer {token}"}
    start_url = f"{BASE}/acts/{actor}/runs?maxTotalChargeUsd={max_charge_usd}"

    # maxTotalChargeUsd is Apify's own HARD CAP on a single run (Rule A2)
    resp = await cache.request(
        "POST",
        start_url,
        json_body=actor_input,
        headers=headers,
        timeout=60.0,
    )
    if resp.status_code == 404:
        raise ApifyError(
            f"Actor {actor!r} not found (404). Check APIFY_ADS_ACTOR_ID. Note the id "
            f"must use a TILDE in REST URLs: apify~facebook-ads-scraper (Rule H1)"
        )
    if resp.status_code >= 400:
        scrubbed_body = _scrub(str(resp.body))
        raise ApifyError(
            f"Apify start actor {actor!r} failed with status {resp.status_code}: {scrubbed_body}"
        )

    if not isinstance(resp.body, dict) or "data" not in resp.body:
        raise ApifyError(f"Unexpected response starting actor {actor!r}: {_scrub(str(resp.body))}")

    run = resp.body["data"]
    run_id, dataset_id = run["id"], run["defaultDatasetId"]
    logger.info("apify run %s started (charge cap $%.2f)", run_id, max_charge_usd)

    deadline = asyncio.get_event_loop().time() + timeout_s
    status_url = f"{BASE}/actor-runs/{run_id}"

    while True:
        status_resp = await cache.request("GET", status_url, headers=headers)
        if status_resp.status_code >= 400:
            raise ApifyError(
                f"Apify run {run_id} status check failed with status {status_resp.status_code}"
            )

        status_data = status_resp.body.get("data", {}) if isinstance(status_resp.body, dict) else {}
        status = status_data.get("status")
        if on_progress:
            on_progress(status)
        if status in _TERMINAL:
            break
        if asyncio.get_event_loop().time() > deadline:
            raise ApifyError(f"Apify run {run_id} exceeded {timeout_s}s (last status {status})")

        # In live mode (not offline), if status is not terminal, remove the cache file
        # so subsequent polling makes a fresh live request rather than looping on RUNNING
        if not cache.offline:
            key = request_cache_key("GET", status_url, None)
            cached_file = cache.root / key[:2] / f"{key}.json"
            if cached_file.exists():
                cached_file.unlink(missing_ok=True)

        if poll_interval_s > 0:
            await asyncio.sleep(poll_interval_s)

    if status != "SUCCEEDED":
        raise ApifyError(f"Apify run {run_id} ended {status}")

    # Fetch dataset items
    items_url = f"{BASE}/datasets/{dataset_id}/items?format=json&clean=true"
    items_resp = await cache.request("GET", items_url, headers=headers)
    if items_resp.status_code >= 400:
        raise ApifyError(
            f"Apify fetch dataset {dataset_id} failed with status {items_resp.status_code}"
        )

    items = items_resp.body if isinstance(items_resp.body, list) else []

    # Re-fetch run detail for usageTotalUsd
    detail_resp = await cache.request("GET", status_url, headers=headers)
    run_detail = detail_resp.body.get("data", {}) if isinstance(detail_resp.body, dict) else {}
    charge = float(run_detail.get("usageTotalUsd") or 0.0)
    logger.info("apify run %s returned %d items, charged $%.4f", run_id, len(items), charge)

    for item in items:
        if isinstance(item, dict):
            item["_cwt_charge_usd"] = charge / max(len(items), 1)

    return items


async def run_actor(
    *,
    actor_id: str,
    actor_input: dict,
    token: str,
    max_charge_usd: float,
    timeout_s: int = 900,
    cache: HttpCache,
    on_progress: Callable[[str], None] | None = None,
    fallbacks: list[str] | None = None,
    fallbacks_tried: list[str] | None = None,
    poll_interval_s: float = 10.0,
) -> list[dict]:
    """Run an Apify actor with HttpCache, spend caps, and fallback support.

    Flow:
      1. POST /runs with maxTotalChargeUsd
      2. Poll /actor-runs/{id} until terminal status
      3. GET /datasets/{id}/items?format=json&clean=true
      4. Distribute usageTotalUsd across returned items
      5. On 404 or 0-item result, retry with fallbacks if configured
    """
    primary = normalise_actor_id(actor_id)
    candidate_fallbacks = [
        normalise_actor_id(fb) for fb in (fallbacks or []) if normalise_actor_id(fb) != primary
    ]
    all_candidates = [primary] + candidate_fallbacks

    tried = fallbacks_tried if fallbacks_tried is not None else []

    last_error: Exception | None = None

    for idx, cand in enumerate(all_candidates):
        is_fallback = idx > 0
        if is_fallback:
            if cand not in tried:
                tried.append(cand)
            logger.info("Attempting fallback actor: %s", cand)

        try:
            items = await _run_actor_single(
                actor=cand,
                actor_input=actor_input,
                token=token,
                max_charge_usd=max_charge_usd,
                timeout_s=timeout_s,
                cache=cache,
                on_progress=on_progress,
                poll_interval_s=poll_interval_s,
            )
            # If 0 items returned and more fallbacks exist, retry with next fallback
            if len(items) == 0 and idx < len(all_candidates) - 1:
                logger.warning("Actor %s returned 0 items; attempting next fallback", cand)
                continue

            for it in items:
                if isinstance(it, dict):
                    it["_cwt_actor_id"] = cand
                    it["_cwt_fallbacks_tried"] = list(tried)
            return items

        except ApifyError as e:
            last_error = e
            # Retry on 404 if fallbacks remain
            if "404" in str(e) and idx < len(all_candidates) - 1:
                logger.warning("Actor %s returned 404; attempting next fallback", cand)
                continue
            raise

    if last_error:
        raise last_error
    return []
