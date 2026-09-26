# S05 — Beat taxonomy & median-timeline aggregation

**Phase** 1 · **Depends on** S04 · **Blocks** S14 (validator 6), S20, S22
**Spec** `doc/video-ads-agent.md` lines **4468–4497** (§11.4 reference data), **474–523** (§3.2 aggregate), **5136–5209** (§15)
**Context budget** ~9k (spec 2.5k + story 1.3k + output 5k)
**Produces** `domain/beats.py`, `tests/test_beats.py`

---

## Goal

The closed beat vocabulary and the arithmetic that turns N extracted beat sheets into the single
**median timeline** every storyboard is held to. §3.2 calls
`aggregate.underused_high_durability` *"the single most valuable field in this artifact"* — this
story is where it is computed and where the archetype scoring boost lives.

The median timeline is the highest-leverage number in the project: validator 6 enforces it, and the
storyboard prompt (S13) is instructed to obey it. A lazy aggregate here becomes a hard failure three
stories downstream.

## Interface contract — FROZEN

```python
# domain/beats.py

BEAT_TAXONOMY: tuple[BeatName, ...] = ("hook","problem","agitation","mechanism","proof","objection","cta")

BEAT_DEFAULT_PROPORTIONS: dict[str, float] = {
    "hook": 0.08, "problem": 0.13, "agitation": 0.13, "mechanism": 0.23,
    "proof": 0.15, "objection": 0.13, "cta": 0.15,
}

HOOK_ARCHETYPES: tuple[str, ...] = ("pattern_interrupt","contrarian_stat","question",
                                    "visual_shock","social_proof","pain_point","bold_statement")

UNDERUSED_HIGH_DURABILITY: tuple[str, ...] = ("social_proof","pain_point","question")

CAMERA_MOVES: tuple[str, ...] = ("static","push_in","pull_out","whip_pan")
TRANSITIONS: tuple[str, ...] = ("cut","dissolve","flash_white","wipeleft","wiperight","zoomblur")
COLOUR_TEMPS_K: tuple[int, ...] = (3200,4300,5600,6500,7000,9000)
ASPECT_RATIOS: tuple[str, ...] = ("9:16","1:1","4:5","16:9")
SUBJECTS: tuple[str, ...] = ("abstract_market_data","trader_silhouette","chart_detail",
                             "city_night","screen_glow","typography_card")

# ── the two functions that matter ──

def default_timeline(total_duration_s: float, tolerance_s: float = 3.0) -> list[MedianBeat]:
    """Fallback when too few ads carry a usable video duration. Uses
    BEAT_DEFAULT_PROPORTIONS, renormalised to sum to 1.0, then scaled to total_duration_s."""

def aggregate_beat_timeline(sheets: list[list[BeatEntry]], *, total_duration_s: float,
                            min_samples: int = 5) -> tuple[list[MedianBeat], dict]:
    """Median timeline across ads. Returns (timeline, diagnostics).

    Falls back to default_timeline() when len(sheets) < min_samples, and records
    that fallback in the diagnostics dict so a reader can tell mined from defaulted."""

def archetype_distribution(hooks: list[HookPattern]) -> dict[str, float]: ...

def underused_high_durability(in_dist: dict[str, float], durability: dict[str, float]) -> list[str]:
    """Archetypes below a frequency floor whose durability exceeds the mean —
    returned in descending durability order. Drives the hook-generator boost."""

def hook_score_boost(archetype: str) -> float:
    """+0.8 for UNDERUSED_HIGH_DURABILITY members, +0.3 for others in HOOK_ARCHETYPES, else 0.0.
    Applied by S22's cwt_generate_hook_candidates."""
```

## Rules that bind this story

- **§3.2 line 519** — the boost is a *sourced edge*, not a stylistic preference. Do not let a later
  story make it configurable or removable.
- **No LLM, no I/O.** This module is pure. It is imported by a pydantic validator (S04's validator 6),
  so it must not import `clients/` or perform any network access, transitively or otherwise.
- **§3.2 line 505** — the median timeline carries a `tolerance_s` **per beat**. Returning a scalar
  tolerance breaks validator 6.

## Build steps

1. Transcribe the constants from §11.4 (lines 4474–4496) and §15 (5140–5167) verbatim. Keep the
   comment about the 2.1× durability finding — it explains why the list exists.
2. `default_timeline` — renormalise `BEAT_DEFAULT_PROPORTIONS` so it sums to exactly 1.0 (the table
   sums to 1.00 but assert it), then accumulate to `total_duration_s`. Round to 2dp; force
   `beats[-1].end_s == total_duration_s` so validator 1 passes.
3. `aggregate_beat_timeline` — for each beat in `BEAT_TAXONOMY`, collect that beat's
   `(start_s, end_s)` across all sheets that contain it. Median by `statistics.median`. Beats present
   in fewer than half the sheets are dropped from the timeline entirely (they are not the grammar).
   `tolerance_s` = the interquartile spread, clamped to `[1.0, 4.0]`.
4. Contiguity repair — after medians, force each `beats[i].end_s = beats[i+1].start_s` and
   `beats[0].start_s = 0`, `beats[-1].end_s = total_duration_s`. Medians of independent beats do not
   line up on their own; this is not cosmetic, validator 1 depends on it.
5. `underused_high_durability` — a frequency floor of `0.10` and a durability input dict. The
   durability table is not in the spec: seed it with the one sourced fact available
   (`social_proof` ≈ 2.1×, `pain_point` and `question` above `bold_statement`) and note the rest as
   `1.0`. Return only archetypes present in `in_dist`.
6. Tests: a synthetic 12-ad fixture whose medians are known by hand; a `min_samples` fallback test;
   an archetype-distribution test summing to 1.0.

## Decisions the spec leaves open

- **A full durability table is not specified.** Only `social_proof` (~2.1×), `pain_point` and
  `question` (above `bold_statement`) are named. Two options: (a) hardcode a small table with the
  named values and `1.0` elsewhere; (b) rank archetypes by measured `stop_power_score` from the mined
  ads instead. **Choose (a) plus a measured override** — use the table when `< 8` ads carry a
  `stop_power_score`, else rank by measured median score. Record which path was used in the
  diagnostics dict, and in `ad_patterns.aggregate.warnings`.
- **`min_samples=5`** is not in the spec. It is the threshold below which a median is noise.
- **`BeatName` import.** S03 defines the enum in `models.py`. Importing `models` here is fine, but
  S04's validator 6 imports *this* module — so import lazily inside functions if you hit a cycle.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_beats.py -q      # green

.venv/Scripts/python -c "
from cwt.domain.beats import *
tl = default_timeline(42.0)
assert tl[0].start_s == 0.0 and tl[-1].end_s == 42.0
assert all(a.end_s == b.start_s for a,b in zip(tl, tl[1:]))
print([f'{b.beat}:{b.start_s}-{b.end_s}' for b in tl])
print('boost', hook_score_boost('social_proof'), hook_score_boost('bold_statement'))"
# -> boost 0.8 0.3
```

## Handoff

S04's validator 6 imports the median timeline shape. S20 (`tools/patterns.py`) calls
`aggregate_beat_timeline` and `underused_high_durability` to fill `ad_patterns.json`. S22
(`cwt_generate_hook_candidates`) calls `hook_score_boost`. **Do not change `MedianBeat`'s fields or
the boost values without updating S04 and S22.**
