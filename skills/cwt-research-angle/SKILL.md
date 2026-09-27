---
name: cwt-research-angle
description: Run one research angle (pain, unique_data, or crowd_effect) with Tavily and Exa, last 30 days.
version: 1.0.0
metadata:
  hermes:
    tags: [research, search, tavily, exa]
    category: marketing
    requires_toolsets: [cwt]
---

# Research Angle

## When to Use
You are the `cwt-researcher` and a card asks you to research one of three angles:
- `pain` — the ICP's pain (signal overload, decision paralysis, etc.)
- `unique_data` — CrowdWisdomTrading's unique data (per-prediction public record)
- `crowd_effect` — how crowd wisdom changes retail trading outcomes

## Procedure
1. `kanban_show()` — read the parent `patterns` card's `metadata.artifact_path` to get `ad_patterns.json`.
2. Call `cwt_research_angle` with the angle name. It runs:
   - Tavily search with `time_range="month"` and `search_depth="advanced"`
   - Exa search with `startPublishedDate` set to 30 days ago
   - Both constrained to the last month
3. The tool returns claims with: text, source_url, source_title, published_date, provider, confidence.
4. For `unique_data` angle: you MUST populate `prohibited_facts` in the research brief.
   Any claim the product makes that you could not verify from a public source belongs there.
   The claims engine reads it and hard-blocks the script if any of it appears.
5. Call `kanban_complete` with `metadata.artifact_path` (the angle's partial brief is stored).

## Pitfalls
- Never state an unsourced claim. Every claim needs a source_url and published_date.
- The `unique_data` angle is the only one that MUST populate `prohibited_facts`.
  If you skip this, the compliance gate has nothing to enforce.
- Search queries must be specific to the angle. Do not reuse queries across angles.
- Find the strongest COUNTER-argument too. A script that only cites supporting evidence
  is propaganda, and the objection beat needs something honest to answer.

## Verification
`cwt_verify_artifact --name research_brief` returns `{"ok": true}` after the `brief` card assembles all three.