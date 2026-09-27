---
name: cwt-extract-ad-patterns
description: Extract hooks, pains, concepts, and beat sheets from winning ads; aggregate median timeline.
version: 1.0.0
metadata:
  hermes:
    tags: [analysis, patterns, beats]
    category: marketing
    requires_toolsets: [cwt]
---

# Extract Ad Patterns

## When to Use
You are the `cwt-hook-analyst` and a card asks you to extract patterns from the sourced winning ads.

## Procedure
1. `kanban_show()` — read the parent `ads` card's `metadata.artifact_path` to get `winning_ads.json`.
2. Call `cwt_extract_ad_patterns` with the ads from the artifact. It extracts per-ad:
   - hook: text, archetype, stop_power_score, why_it_stops_the_scroll
   - pain: the core pain point
   - concept: the central concept
   - beat_sheet: ordered beats with start_s, end_s, what_happens
3. Call `cwt_aggregate_beat_timeline` to compute the aggregate:
   - archetype_distribution
   - median_hook_duration_s
   - median_beat_timeline with tolerances
   - underused_high_durability (the single most valuable field)
4. Verify the output artifact `ad_patterns.json` validates.
5. Call `kanban_complete` with `metadata.artifact_path` pointing to the artifact.

## Pitfalls
- Never invent a beat sheet for an ad with no `video_duration_s`. Skip such ads.
- The beat_sheet is the highest-value output — which beat occupies which second.
  A lazy aggregate here becomes a hard failure downstream (the storyboard validator enforces it).
- `underused_high_durability` is the single most valuable field. Published fintech benchmarks show
  Social Proof appears in ~0.1% of video creatives yet survives ~2.1× longer than average.
  The hook generator is explicitly instructed to prefer archetypes on this list.

## Verification
`cwt_verify_artifact --name ad_patterns` returns `{"ok": true}`.