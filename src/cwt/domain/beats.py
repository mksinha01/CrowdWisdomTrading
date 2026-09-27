"""Beat taxonomy, reference data, and median-timeline aggregation.

Implements the closed beat vocabulary and the arithmetic that aggregates
extracted ad beat sheets into a median timeline. Also provides archetype
distribution analysis and the underused-high-durability scoring boost.
"""

from __future__ import annotations

import collections
import statistics
from typing import Any

from cwt.domain.models import (
    BeatEntry,
    BeatName,
    HookPattern,
    MedianBeat,
)

# ===========================================================================
# Reference Data (§11.4 lines 4474–4496 & §15 lines 5140–5167)
# ===========================================================================

BEAT_TAXONOMY: tuple[BeatName, ...] = (
    BeatName.HOOK,
    BeatName.PROBLEM,
    BeatName.AGITATION,
    BeatName.MECHANISM,
    BeatName.PROOF,
    BeatName.OBJECTION,
    BeatName.CTA,
)

# Default proportions. The ACTUAL timings come from the mined median timeline;
# these are only the fallback when too few ads carry a usable video duration.
BEAT_DEFAULT_PROPORTIONS: dict[str, float] = {
    "hook": 0.08,
    "problem": 0.13,
    "agitation": 0.13,
    "mechanism": 0.23,
    "proof": 0.15,
    "objection": 0.13,
    "cta": 0.15,
}

HOOK_ARCHETYPES: tuple[str, ...] = (
    "pattern_interrupt",
    "contrarian_stat",
    "question",
    "visual_shock",
    "social_proof",
    "pain_point",
    "bold_statement",
)

# Sourced: Social Proof is ~0.1% of fintech video creatives yet survives ~2.1x
# longer than average. Pain Point and Question also outlast Bold Statement.
# These get an explicit scoring boost. See WOW-5.
UNDERUSED_HIGH_DURABILITY: tuple[str, ...] = ("social_proof", "pain_point", "question")

CAMERA_MOVES: tuple[str, ...] = ("static", "push_in", "pull_out", "whip_pan")
TRANSITIONS: tuple[str, ...] = (
    "cut",
    "dissolve",
    "flash_white",
    "wipeleft",
    "wiperight",
    "zoomblur",
)
COLOUR_TEMPS_K: tuple[int, ...] = (3200, 4300, 5600, 6500, 7000, 9000)
ASPECT_RATIOS: tuple[str, ...] = ("9:16", "1:1", "4:5", "16:9")
SUBJECTS: tuple[str, ...] = (
    "abstract_market_data",
    "trader_silhouette",
    "chart_detail",
    "city_night",
    "screen_glow",
    "typography_card",
)

# Seeded durability values (S05 §15):
# social_proof ≈ 2.1x, pain_point and question above bold_statement, rest 1.0.
DEFAULT_DURABILITY_TABLE: dict[str, float] = {
    "social_proof": 2.1,
    "pain_point": 1.5,
    "question": 1.3,
    "bold_statement": 1.0,
    "pattern_interrupt": 1.0,
    "contrarian_stat": 1.0,
    "visual_shock": 1.0,
}


def _extract_beat_entry(entry: Any) -> tuple[str, float, float]:
    """Extract (beat_name, start_s, end_s) from BeatEntry model or dict."""
    if isinstance(entry, dict):
        raw_b = entry.get("beat", "")
        b = raw_b.value if hasattr(raw_b, "value") else str(raw_b)
        s = float(entry.get("start_s", 0.0))
        e = float(entry.get("end_s", 0.0))
    else:
        raw_b = getattr(entry, "beat", "")
        b = raw_b.value if hasattr(raw_b, "value") else str(raw_b)
        s = float(getattr(entry, "start_s", 0.0))
        e = float(getattr(entry, "end_s", 0.0))
    return b, s, e


def _extract_hook_archetype(hook: Any) -> str:
    """Extract hook archetype string from HookPattern model or dict."""
    if isinstance(hook, dict):
        raw = hook.get("archetype", "")
    else:
        raw = getattr(hook, "archetype", "")
    return raw.value if hasattr(raw, "value") else str(raw)


def default_timeline(total_duration_s: float, tolerance_s: float = 3.0) -> list[MedianBeat]:
    """Fallback when too few ads carry a usable video duration.

    Uses BEAT_DEFAULT_PROPORTIONS, renormalised to sum to 1.0, then scaled
    to total_duration_s. Round to 2dp; force beats[-1].end_s == total_duration_s.
    """
    if total_duration_s <= 0:
        raise ValueError("total_duration_s must be positive")

    total_prop = sum(BEAT_DEFAULT_PROPORTIONS.values())
    assert abs(total_prop - 1.0) < 1e-6, (
        f"BEAT_DEFAULT_PROPORTIONS must sum to 1.0, got {total_prop}"
    )

    beats: list[MedianBeat] = []
    current_start = 0.0
    tax_list = list(BEAT_TAXONOMY)
    for i, beat_name in enumerate(tax_list):
        b_str = beat_name.value if hasattr(beat_name, "value") else str(beat_name)
        prop = BEAT_DEFAULT_PROPORTIONS[b_str] / total_prop
        if i == len(tax_list) - 1:
            end_s = round(total_duration_s, 2)
        else:
            end_s = round(current_start + prop * total_duration_s, 2)
        beats.append(
            MedianBeat(
                beat=BeatName(b_str),
                start_s=current_start,
                end_s=end_s,
                tolerance_s=round(tolerance_s, 2),
            )
        )
        current_start = end_s

    return beats


def aggregate_beat_timeline(
    sheets: list[list[BeatEntry]],
    *,
    total_duration_s: float,
    min_samples: int = 5,
) -> tuple[list[MedianBeat], dict[str, Any]]:
    """Median timeline across ads. Returns (timeline, diagnostics).

    Falls back to default_timeline() when len(sheets) < min_samples, and records
    that fallback in the diagnostics dict so a reader can tell mined from defaulted.

    Beats present in fewer than half the sheets are dropped from the timeline.
    tolerance_s is the interquartile spread of start_s clamped to [1.0, 4.0].
    Contiguity repair forces beats[i].end_s = beats[i+1].start_s,
    beats[0].start_s = 0.0, and beats[-1].end_s = total_duration_s.
    """
    if total_duration_s <= 0:
        raise ValueError("total_duration_s must be positive")

    if len(sheets) < min_samples:
        diagnostics = {
            "fallback": True,
            "fallback_reason": f"Sample count ({len(sheets)}) < min_samples ({min_samples})",
            "sheet_count": len(sheets),
            "min_samples": min_samples,
            "total_duration_s": total_duration_s,
            "warnings": [
                f"Timeline defaulted: only {len(sheets)} sheets available (min {min_samples})"
            ],
        }
        return default_timeline(total_duration_s), diagnostics

    # Collect (start_s, end_s) per beat in BEAT_TAXONOMY across sheets
    collected: dict[str, list[tuple[float, float]]] = {
        (b.value if hasattr(b, "value") else str(b)): [] for b in BEAT_TAXONOMY
    }
    for sheet in sheets:
        sheet_entries: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)
        for entry in sheet:
            b, s, e = _extract_beat_entry(entry)
            if b in collected:
                sheet_entries[b].append((s, e))
        for b, entries in sheet_entries.items():
            min_s = min(s for s, _ in entries)
            max_e = max(e for _, e in entries)
            collected[b].append((min_s, max_e))

    threshold = len(sheets) / 2.0
    retained_beats: list[str] = []
    dropped_beats: list[str] = []

    for b in BEAT_TAXONOMY:
        b_str = b.value if hasattr(b, "value") else str(b)
        count = len(collected[b_str])
        if count < threshold:
            dropped_beats.append(b_str)
        else:
            retained_beats.append(b_str)

    # If too few beats retained to form a viable timeline, fall back
    if len(retained_beats) < 2:
        reason = (
            f"Fewer than 2 beats retained after frequency filtering (retained: {retained_beats})"
        )
        diagnostics = {
            "fallback": True,
            "fallback_reason": reason,
            "sheet_count": len(sheets),
            "min_samples": min_samples,
            "total_duration_s": total_duration_s,
            "retained_beats": retained_beats,
            "dropped_beats": dropped_beats,
            "warnings": [
                "Timeline defaulted: fewer than 2 beats present in >= half of sheets"
            ],
        }
        return default_timeline(total_duration_s), diagnostics

    # Compute medians and tolerances
    candidate_list: list[dict[str, Any]] = []
    for b_str in retained_beats:
        starts = [s for s, _ in collected[b_str]]
        med_start = statistics.median(starts)
        if len(starts) >= 2:
            try:
                q = statistics.quantiles(starts, n=4)
                spread = q[2] - q[0]
            except Exception:
                spread = 1.0
        else:
            spread = 1.0
        tol = round(max(1.0, min(4.0, spread)), 2)
        candidate_list.append({
            "beat": BeatName(b_str),
            "start_s": round(med_start, 2),
            "end_s": 0.0,
            "tolerance_s": tol,
        })

    # Contiguity repair:
    # 1. beats[0].start_s = 0.0
    # 2. beats[i].end_s = beats[i+1].start_s
    # 3. beats[-1].end_s = total_duration_s
    candidate_list[0]["start_s"] = 0.0
    for i in range(len(candidate_list) - 1):
        curr_start = candidate_list[i]["start_s"]
        next_start = candidate_list[i + 1]["start_s"]
        if next_start <= curr_start:
            next_start = round(curr_start + 0.1, 2)
            candidate_list[i + 1]["start_s"] = next_start
        candidate_list[i]["end_s"] = next_start

    last_beat = candidate_list[-1]
    last_beat["end_s"] = round(total_duration_s, 2)
    if last_beat["end_s"] <= last_beat["start_s"]:
        last_beat["start_s"] = max(0.0, round(total_duration_s - 0.5, 2))
        if len(candidate_list) > 1:
            candidate_list[-2]["end_s"] = last_beat["start_s"]

    timeline = [
        MedianBeat(
            beat=item["beat"],
            start_s=item["start_s"],
            end_s=item["end_s"],
            tolerance_s=item["tolerance_s"],
        )
        for item in candidate_list
    ]

    diagnostics = {
        "fallback": False,
        "sheet_count": len(sheets),
        "min_samples": min_samples,
        "total_duration_s": total_duration_s,
        "retained_beats": [b.beat.value for b in timeline],
        "dropped_beats": dropped_beats,
        "sample_counts_by_beat": {b: len(collected[b]) for b in collected},
        "warnings": [],
    }
    return timeline, diagnostics


def archetype_distribution(hooks: list[HookPattern]) -> dict[str, float]:
    """Calculate frequency distribution of hook archetypes.

    Returns dict mapping archetype to proportion, rounded to 2dp and
    adjusted so sum is exactly 1.0. Returns empty dict if hooks is empty.
    """
    if not hooks:
        return {}

    counts: dict[str, int] = collections.defaultdict(int)
    for h in hooks:
        arch = _extract_hook_archetype(h)
        if arch:
            counts[arch] += 1

    total = sum(counts.values())
    if total == 0:
        return {}

    dist = {arch: round(c / total, 2) for arch, c in counts.items()}
    # Adjust discrepancy on highest frequency item so sum is exactly 1.0
    diff = round(1.0 - sum(dist.values()), 2)
    if diff != 0 and dist:
        max_k = max(dist, key=lambda k: dist[k])
        dist[max_k] = round(dist[max_k] + diff, 2)

    return dist


def underused_high_durability(
    in_dist: dict[str, float],
    durability: dict[str, float] | None = None,
    *,
    frequency_floor: float = 0.10,
) -> list[str]:
    """Archetypes below frequency_floor whose durability exceeds the mean.

    Returned in descending durability order. Returns only archetypes present in in_dist.
    Drives the hook-generator boost.
    """
    if not in_dist:
        return []

    # Prepare durability lookup and compute mean durability
    if durability:
        if any(k in DEFAULT_DURABILITY_TABLE for k in durability):
            dur_lookup = {**DEFAULT_DURABILITY_TABLE, **durability}
            mean_dur = statistics.mean(dur_lookup.values())
        else:
            dur_lookup = dict(durability)
            mean_dur = statistics.mean(durability.values())
    else:
        dur_lookup = dict(DEFAULT_DURABILITY_TABLE)
        mean_dur = statistics.mean(DEFAULT_DURABILITY_TABLE.values())

    qualifying: list[tuple[str, float]] = []
    for arch, freq in in_dist.items():
        if freq < frequency_floor:
            d = dur_lookup.get(arch, DEFAULT_DURABILITY_TABLE.get(arch, 1.0))
            if d > mean_dur:
                qualifying.append((arch, d))

    qualifying.sort(key=lambda item: item[1], reverse=True)
    return [arch for arch, _ in qualifying]


def hook_score_boost(archetype: str) -> float:
    """+0.8 for UNDERUSED_HIGH_DURABILITY members, +0.3 for others in HOOK_ARCHETYPES, else 0.0.

    Applied by S22's cwt_generate_hook_candidates.
    """
    arch_str = archetype.value if hasattr(archetype, "value") else str(archetype)
    if arch_str in UNDERUSED_HIGH_DURABILITY:
        return 0.8
    if arch_str in HOOK_ARCHETYPES:
        return 0.3
    return 0.0


def compute_durability(
    hooks: list[HookPattern],
    *,
    min_samples: int = 8,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Compute archetype durability table from hook stop_power_scores, or fallback to default.

    Uses DEFAULT_DURABILITY_TABLE when fewer than min_samples hooks carry
    a positive stop_power_score.
    Otherwise ranks archetypes by measured median stop_power_score.
    Returns (durability_dict, diagnostics_dict).
    """
    valid_hooks: list[Any] = []
    for h in hooks:
        if isinstance(h, dict):
            s = h.get("stop_power_score")
        else:
            s = getattr(h, "stop_power_score", None)
        if s is not None and float(s) > 0:
            valid_hooks.append(h)

    if len(valid_hooks) < min_samples:
        warn = (
            f"Default durability used: only {len(valid_hooks)} hooks with score (< {min_samples})"
        )
        return (
            DEFAULT_DURABILITY_TABLE.copy(),
            {
                "durability_source": "default_table",
                "sample_count": len(valid_hooks),
                "min_samples": min_samples,
                "warnings": [warn],
            },
        )

    scores_by_arch: dict[str, list[float]] = collections.defaultdict(list)
    for h in valid_hooks:
        arch = _extract_hook_archetype(h)
        if isinstance(h, dict):
            score = float(h.get("stop_power_score", 0.0))
        else:
            score = float(getattr(h, "stop_power_score", 0.0))
        scores_by_arch[arch].append(score)

    measured: dict[str, float] = {}
    for arch in HOOK_ARCHETYPES:
        if arch in scores_by_arch:
            measured[arch] = round(statistics.median(scores_by_arch[arch]), 2)
        else:
            measured[arch] = DEFAULT_DURABILITY_TABLE.get(arch, 1.0)

    return (
        measured,
        {
            "durability_source": "measured_stop_power",
            "sample_count": len(valid_hooks),
            "min_samples": min_samples,
            "measured_archetypes": list(scores_by_arch.keys()),
            "warnings": [],
        },
    )


def median_hook_duration(sheets: list[list[BeatEntry]], fallback_s: float = 3.0) -> float:
    """Median of hook beat's duration (end_s - start_s) across sheets."""
    durations: list[float] = []
    for sheet in sheets:
        for entry in sheet:
            b, s, e = _extract_beat_entry(entry)
            if b == BeatName.HOOK.value:
                durations.append(e - s)
                break
    if not durations:
        return fallback_s
    return round(statistics.median(durations), 2)
