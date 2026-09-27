---
name: cwt-source-winning-ads
description: Source winning ads from Meta Ads Library via Apify, last 30 days.
version: 1.0.0
metadata:
  hermes:
    tags: [ads, scraping, meta]
    category: marketing
    requires_toolsets: [cwt]
---

# Source Winning Ads

## When to Use
You are the `cwt-ads-manager` and a card asks you to source winning ads from the Meta Ads Library.

## Procedure
1. Call `cwt_source_winning_ads` with the standard query:
   - keywords: ["trading signals", "stock market alerts", "forex signals", "options flow"]
   - countries: ["US", "GB", "IN"]
   - window_days: 30
   - max_items: from APIFY_MAX_ITEMS (default 60)
2. Call `cwt_rank_winning_ads` on the returned ads to get the weighted longevity ranking.
3. Verify the output artifact `winning_ads.json` validates.
4. Call `kanban_complete` with `metadata.artifact_path` pointing to the artifact.

## Pitfalls
- The Apify actor returns `adArchiveID` (camelCase). The normalisation in `clients/apify.py` maps it to `ad_id`.
  Never read raw actor fields outside that module.
- `active_days` is the performance proxy. Impressions and spend are frequently absent — the ranker
  degrades gracefully and records the degradation in `ranking.weights` plus `warnings`.
- The hard spend cap `APIFY_MAX_CHARGE_USD` is enforced by the client. Do not attempt to bypass it.
- If fewer than 8 ads fall inside the 30-day window, complete anyway and record a warning.
  Do NOT widen the window.

## Verification
`cwt_verify_artifact --name winning_ads` returns `{"ok": true}`.