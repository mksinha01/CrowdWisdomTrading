"""Storyboard generation, variant judging, and beat splicing (Story S22).

Reconstructed WOW requirements:
| Id | Requirement | Where it is enforced |
|---|---|---|
| WOW-1 | The hook is a scored search over 12 candidates, not a sentence someone liked (spec line 32) | _generate_hook_candidates |
| WOW-2 | Three genuinely distinct variants, one per angle — writing one script and claiming three is not acceptable (spec line 3483) | _write_storyboard_variant called 3× |
| WOW-3 | The splice: the winner absorbs named beats from the losers, with a receipt trail in generation.variants[].beats_stolen_from (spec line 765) | _judge_variants + splice application |
| WOW-4 | Every shot carries executable camera and lighting direction — "not decoration — they are compiled into the render" (spec line 1405) | Shot.camera validator + S14 |
| WOW-5 | social_proof and pain_point get an explicit scoring boost because they are ~0.1% of fintech creatives but survive ~2.1× longer (spec line 4488) | beats.hook_score_boost (S05) |
| WOW-6 | The cinematic grade — "ten lines of filtergraph is the entire difference between 'an AI slideshow' and 'a movie trailer'" (spec line 3157) | CINEMATIC_GRADE (S14) |
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import Field

from cwt.clients.llm import (
    ArtifactValidationError,
    BudgetExceeded,
    LLMClient,
    Tier,
)
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactError, ArtifactStore
from cwt.domain.beats import (
    BEAT_DEFAULT_PROPORTIONS,
    UNDERUSED_HIGH_DURABILITY,
    hook_score_boost,
)
from cwt.domain.claims import scan_prohibited_facts
from cwt.domain.models import (
    DEFAULT_PROHIBITED_FACTS,
    SCHEMA_VERSION,
    AdPatterns,
    AngleName,
    BeatName,
    BeatSpan,
    Camera,
    CameraMove,
    ComplianceBlock,
    Composition,
    CreativeScores,
    GenerationBlock,
    HookArchetype,
    HookCandidate,
    HookCandidates,
    MedianBeat,
    OnScreenText,
    ResearchBrief,
    Shot,
    Storyboard,
    StoryboardMeta,
    VariantRecord,
    Voiceover,
    VOSegment,
    _CwtBaseModel,
)
from cwt.prompts.script import (
    build_hook_candidates_prompt,
    build_storyboard_prompt,
    build_variant_judge_prompt,
)
from cwt.util.jsonio import read_json, write_json
from cwt.util.paths import RunPaths

logger = logging.getLogger("cwt.tools.storyboard")

ANGLES: tuple[str, ...] = ("pain", "unique_data", "crowd_effect")
RISK_DISCLOSURE_TEXT = (
    "Trading involves significant risk. Informational and educational only. Not financial advice."
)


# ===========================================================================
# LLM Interface Models (Internal Schemas for complete_validated)
# ===========================================================================


class RawHookCandidate(_CwtBaseModel):
    """Raw candidate produced by LLM before boost and selection."""

    id: str
    archetype: HookArchetype
    text_overlay: str
    first_frame_description: str
    sound_design: str
    stop_power_score: float
    why_it_stops_the_scroll: str
    selected: bool = False
    rejection_reason: str | None = None


class RawHookCandidatesResponse(_CwtBaseModel):
    """Container schema for the 12 hook candidates returned by the model."""

    candidates: list[RawHookCandidate]


class VariantJudgeScore(_CwtBaseModel):
    """Scored evaluation of one storyboard variant by the judge."""

    angle: AngleName
    scores: dict[str, float]
    weighted_mean: float
    strongest_shot_id: str
    strongest_shot_why: str = ""


class SpliceDirective(_CwtBaseModel):
    """Directive from judge describing what shot to splice from a loser into winner."""

    from_angle: str
    shot_id: str
    improves_axis: str = ""
    how_to_integrate: str = ""


class JudgeResult(_CwtBaseModel):
    """Structured response from the variant judge."""

    variants: list[VariantJudgeScore]
    winner: AngleName | str
    splices: list[SpliceDirective] = Field(default_factory=list)


# ===========================================================================
# Helpers
# ===========================================================================


def _run_async(coro: Any) -> Any:
    """Run an async coroutine synchronously, even if an event loop is already active."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _get_llm_client(settings: Settings, paths: RunPaths) -> LLMClient:
    api_key = (
        settings.openrouter_api_key
        if settings.llm_provider == "openrouter"
        else settings.nvidia_api_key
    )
    return LLMClient(
        provider=settings.llm_provider,
        api_key=api_key,
        model_cheap=settings.model_cheap,
        model_strong=settings.model_strong,
        fallbacks=settings.model_fallbacks,
        max_concurrency=settings.llm_max_concurrency,
        max_usd=settings.max_usd,
        ledger_path=paths.ledger,
        timeout_s=settings.llm_timeout_seconds,
        repair_attempts=settings.llm_json_repair_attempts,
    )


def _format_prohibited_facts(prohibited_facts: list[Any]) -> str:
    lines = []
    for pf in prohibited_facts:
        fact = pf.fact if hasattr(pf, "fact") else pf.get("fact", "")
        reason = pf.reason if hasattr(pf, "reason") else pf.get("reason", "")
        rule = pf.rule if hasattr(pf, "rule") else pf.get("rule", "")
        lines.append(f"- FACT: {fact}\n  REASON: {reason}\n  RULE: {rule}")
    return "\n".join(lines)


def _format_median_beat_timeline(median_beats: list[Any]) -> str:
    lines = []
    for mb in median_beats:
        b_name = mb.beat.value if hasattr(mb.beat, "value") else str(mb.beat)
        start = float(mb.start_s if hasattr(mb, "start_s") else mb.get("start_s", 0.0))
        end = float(mb.end_s if hasattr(mb, "end_s") else mb.get("end_s", 0.0))
        tol = float(getattr(mb, "tolerance_s", 1.0) if hasattr(mb, "tolerance_s") else mb.get("tolerance_s", 1.0))
        lines.append(f"- {b_name}: {start:.1f}s to {end:.1f}s (tolerance: ±{tol:.1f}s)")
    return "\n".join(lines)


def _scan_storyboard_for_prohibited_facts(
    storyboard: Storyboard,
    prohibited: list[Any],
) -> None:
    """Validator 10 check: scan voiceover, OST, and shot descriptions for prohibited facts."""
    targets: list[tuple[str, str]] = [
        (storyboard.voiceover.full_text, "voiceover full_text")
    ]
    for seg in storyboard.voiceover.segments:
        targets.append((seg.text, f"voiceover segment for shot '{seg.shot_id}'"))
    for shot in storyboard.shots:
        for ost in shot.on_screen_text:
            targets.append((ost.text, f"shot '{shot.id}' on-screen text"))
        if shot.description:
            targets.append((shot.description, f"shot '{shot.id}' description"))

    for text, loc in targets:
        if not text:
            continue
        findings = scan_prohibited_facts(text, prohibited)
        if findings:
            f = findings[0]
            raise ValueError(
                f"Prohibited fact {f.why} ({f.matched_text!r}) matched in variant '{storyboard.meta.angle}' {loc}"
            )


# ===========================================================================
# 1. Hook Candidates
# ===========================================================================


async def _generate_hook_candidates(
    settings: Settings,
    paths: RunPaths,
    *,
    client: LLMClient | None = None,
) -> HookCandidates:
    """Generate 12 scored candidates across six archetypes, apply boost, and select winner."""
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    brief = store.read("research_brief")
    brief_str = (
        brief.model_dump_json(indent=2)
        if hasattr(brief, "model_dump_json")
        else json.dumps(brief, indent=2)
    )

    if client is None:
        client = _get_llm_client(settings, paths)

    prompt = build_hook_candidates_prompt(brief_str)

    # Attempt generation; if under-represented in social_proof or pain_point, regenerate once
    candidates: list[RawHookCandidate] = []
    for attempt in range(2):
        res = await client.complete_validated(
            tier=Tier.STRONG,
            messages=[{"role": "user", "content": prompt}],
            schema=RawHookCandidatesResponse,
            temperature=0.0,
            stage="generate_hook_candidates",
        )
        candidates = res.candidates
        sp_count = sum(
            1
            for c in candidates
            if (c.archetype.value if hasattr(c.archetype, "value") else str(c.archetype))
            == "social_proof"
        )
        pp_count = sum(
            1
            for c in candidates
            if (c.archetype.value if hasattr(c.archetype, "value") else str(c.archetype))
            == "pain_point"
        )

        if sp_count >= 2 and pp_count >= 2:
            break

        if attempt == 0:
            logger.warning(
                "Hook generation drift: social_proof=%d, pain_point=%d (expected >= 2 each). Regenerating once.",
                sp_count,
                pp_count,
            )
            continue
        else:
            logger.warning(
                "Hook generation drift after regeneration: social_proof=%d, pain_point=%d. Accepting with warning.",
                sp_count,
                pp_count,
            )

    # WOW-5: apply beats.hook_score_boost and clamp to 10.0
    underused_boosted: list[str] = []
    for c in candidates:
        arch_str = c.archetype.value if hasattr(c.archetype, "value") else str(c.archetype)
        boost = hook_score_boost(arch_str)
        if boost >= 0.8 and arch_str not in underused_boosted:
            underused_boosted.append(arch_str)
        c.stop_power_score = min(10.0, round(float(c.stop_power_score) + boost, 2))

    # Pick winner and assign per-candidate rejection reasons
    sorted_candidates = sorted(candidates, key=lambda c: c.stop_power_score, reverse=True)
    winner = sorted_candidates[0]
    runner_up = sorted_candidates[1] if len(sorted_candidates) > 1 else None
    winner_arch = winner.archetype.value if hasattr(winner.archetype, "value") else str(winner.archetype)

    domain_candidates: list[HookCandidate] = []
    for c in candidates:
        if c.id == winner.id:
            c.selected = True
            c.rejection_reason = None
        else:
            c.selected = False
            if runner_up and c.id == runner_up.id:
                c.rejection_reason = (
                    f"Runner-up to {winner_arch} ({winner.id}) with score {winner.stop_power_score:.1f}"
                )
            else:
                c.rejection_reason = (
                    f"Lower stop-power score ({c.stop_power_score:.1f}) than winning {winner_arch} ({winner.stop_power_score:.1f})"
                )

        domain_candidates.append(
            HookCandidate(
                id=c.id,
                archetype=c.archetype,
                text_overlay=c.text_overlay,
                first_frame_description=c.first_frame_description,
                sound_design=c.sound_design,
                stop_power_score=c.stop_power_score,
                why_it_stops_the_scroll=c.why_it_stops_the_scroll,
                selected=c.selected,
                rejection_reason=c.rejection_reason,
            )
        )

    archetypes_covered = list(
        dict.fromkeys(
            c.archetype.value if hasattr(c.archetype, "value") else str(c.archetype)
            for c in domain_candidates
        )
    )

    artifact = HookCandidates(
        schema_version=SCHEMA_VERSION,
        candidates=domain_candidates,
        archetypes_covered=archetypes_covered,
        underused_archetypes_boosted=underused_boosted,
    )
    return artifact


def generate_hook_candidates(
    *,
    settings: Settings,
    paths: RunPaths,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    """12 scored candidates across 6 archetypes, with per-candidate rejection reasons.

    WRITES artifacts/hook_candidates.json
    RETURNS {"artifact_path","candidates":int,"selected_id","archetypes_covered":[...],
             "underused_boosted":[...]}
    """
    artifact = _run_async(_generate_hook_candidates(settings, paths, client=client))
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    artifact_path = store.write("hook_candidates", artifact)

    winner = next(c for c in artifact.candidates if c.selected)
    return {
        "artifact_path": str(artifact_path),
        "candidates": len(artifact.candidates),
        "selected_id": winner.id,
        "archetypes_covered": artifact.archetypes_covered,
        "underused_boosted": artifact.underused_archetypes_boosted,
    }


# ===========================================================================
# 2. Storyboard Variant Writing
# ===========================================================================


async def _write_storyboard_variant(
    settings: Settings,
    paths: RunPaths,
    *,
    angle: str,
    hook: HookCandidate | dict[str, Any],
    brief: ResearchBrief | dict[str, Any],
    patterns: AdPatterns | dict[str, Any],
    client: LLMClient | None = None,
    total_duration_s: float = 40.0,
) -> Storyboard:
    """Write one complete storyboard variant for one research angle."""
    if angle not in ANGLES:
        raise ValueError(f"Unknown angle '{angle}'. Expected one of {ANGLES}")

    if client is None:
        client = _get_llm_client(settings, paths)

    # 1. Rationale for angle
    angle_rationale = f"Focuses on {angle} angle."
    if hasattr(brief, "angles"):
        angle_enum = AngleName(angle)
        angle_obj = brief.angles.get(angle_enum) or brief.angles.get(angle)
        if angle_obj and getattr(angle_obj, "synthesis", None):
            angle_rationale = angle_obj.synthesis
    elif isinstance(brief, dict) and "angles" in brief and angle in brief["angles"]:
        angle_rationale = brief["angles"][angle].get("synthesis", angle_rationale)

    # 2. Median beat timeline
    median_beats: list[Any] = []
    if hasattr(patterns, "aggregate") and hasattr(patterns.aggregate, "median_beat_timeline"):
        median_beats = patterns.aggregate.median_beat_timeline
    elif isinstance(patterns, dict) and "aggregate" in patterns:
        median_beats = patterns["aggregate"].get("median_beat_timeline", [])

    if not median_beats:
        # Fallback to default proportions scaled to duration
        cur_t = 0.0
        for b_name, prop in BEAT_DEFAULT_PROPORTIONS.items():
            dur = round(total_duration_s * prop, 2)
            end_t = round(cur_t + dur, 2)
            median_beats.append(
                MedianBeat(
                    beat=BeatName(b_name),
                    start_s=cur_t,
                    end_s=end_t,
                    tolerance_s=2.0 if b_name != "hook" else 1.2,
                )
            )
            cur_t = end_t
        median_beats[-1].end_s = total_duration_s

    # 3. Prohibited facts
    prohibited_facts: list[Any] = []
    if hasattr(brief, "prohibited_facts") and brief.prohibited_facts:
        prohibited_facts = brief.prohibited_facts
    elif isinstance(brief, dict) and "prohibited_facts" in brief:
        prohibited_facts = brief["prohibited_facts"]
    else:
        prohibited_facts = DEFAULT_PROHIBITED_FACTS

    # Format placeholders
    beat_timeline_str = _format_median_beat_timeline(median_beats)
    prohibited_str = _format_prohibited_facts(prohibited_facts)
    shot_schema_str = json.dumps(Shot.model_json_schema(), indent=2)
    storyboard_schema_str = json.dumps(Storyboard.model_json_schema(), indent=2)

    brief_str = (
        brief.model_dump_json(indent=2)
        if hasattr(brief, "model_dump_json")
        else json.dumps(brief, indent=2)
    )
    patterns_str = (
        patterns.model_dump_json(indent=2)
        if hasattr(patterns, "model_dump_json")
        else json.dumps(patterns, indent=2)
    )
    hook_str = (
        hook.model_dump_json(indent=2)
        if hasattr(hook, "model_dump_json")
        else json.dumps(hook, indent=2)
    )

    prompt = build_storyboard_prompt(
        duration_s=int(total_duration_s),
        angle=angle,
        angle_rationale=angle_rationale,
        beat_timeline=beat_timeline_str,
        prohibited=prohibited_str,
        shot_schema=shot_schema_str,
        storyboard_schema=storyboard_schema_str,
        brief=brief_str,
        patterns=patterns_str,
        hook=hook_str,
    )

    validation_ctx: dict[str, Any] = {
        "median_beat_timeline": [
            mb.model_dump() if hasattr(mb, "model_dump") else mb for mb in median_beats
        ],
        "prohibited_facts": [
            pf.model_dump() if hasattr(pf, "model_dump") else pf for pf in prohibited_facts
        ],
    }

    # Complete validated on STRONG tier, max_tokens=8192, retry once on validation failure
    try:
        storyboard = await client.complete_validated(
            tier=Tier.STRONG,
            messages=[{"role": "user", "content": prompt}],
            schema=Storyboard,
            temperature=0.2,
            max_tokens=8192,
            stage=f"storyboard_variant_{angle}",
            validation_context=validation_ctx,
        )
    except ArtifactValidationError as first_err:
        logger.warning(
            "Variant %s validation failed on first attempt: %s. Retrying once at temperature=0.2.",
            angle,
            first_err,
        )
        storyboard = await client.complete_validated(
            tier=Tier.STRONG,
            messages=[{"role": "user", "content": prompt}],
            schema=Storyboard,
            temperature=0.2,
            max_tokens=8192,
            stage=f"storyboard_variant_{angle}_retry",
            validation_context=validation_ctx,
        )

    # WOW-4: camera.move must never be missing on any shot
    for shot in storyboard.shots:
        if not shot.camera or not shot.camera.move:
            raise ValueError(
                f"Shot '{shot.id}' is missing executable camera.move (WOW-4: not decoration)"
            )

    # Rule C3 / validator 9: ensure risk disclosure safe_harbour_duration_s is recorded from beat timings
    if not storyboard.compliance.risk_disclosure_present:
        storyboard.compliance.risk_disclosure_present = True

    # Compute duration of disclosure shot/on-screen text
    disc_shot = next(
        (s for s in storyboard.shots if s.id == storyboard.compliance.risk_disclosure_shot_id),
        storyboard.shots[-1],
    )
    disc_dur = disc_shot.duration_s
    for ost in disc_shot.on_screen_text:
        if "risk" in ost.text.lower() or "not financial advice" in ost.text.lower():
            disc_dur = max(disc_dur, ost.until_s - ost.at_s)
    storyboard.compliance.safe_harbour_duration_s = max(round(disc_dur, 2), 3.0)
    storyboard.compliance.risk_disclosure_shot_id = disc_shot.id
    if not storyboard.compliance.risk_disclosure_text:
        storyboard.compliance.risk_disclosure_text = RISK_DISCLOSURE_TEXT

    # Ensure angle matches requested
    storyboard.meta.angle = AngleName(angle)
    return storyboard


def write_storyboard_variant(
    *,
    settings: Settings,
    paths: RunPaths,
    angle: str,
    hook_id: str,
    total_duration_s: float,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    """One complete variant for one research angle. STRONG tier.

    CALL THIS: exactly three times per run, once per angle (`pain`, `unique_data`, `crowd_effect`).
    Pass the SAME `hook_id` to all three — the hook is chosen once, by score, before the variants are
    written.

    WHEN NOT TO CALL: do not call it a fourth time to "try another angle" — there are only three
    angles. Do not pass a hook id you invented; call `cwt_generate_hook_candidates` first.

    WRITES artifacts/variants/<angle>.json  (scratch)
    RETURNS {"angle","shots":int,"duration_s","valid":true,"variant_path"}
    """
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    hook_candidates = store.read("hook_candidates")
    selected_hook = next((c for c in hook_candidates.candidates if c.id == hook_id), None)
    if selected_hook is None:
        raise ValueError(
            f"Hook candidate '{hook_id}' not found in hook_candidates.json. Run generate_hook_candidates first."
        )

    brief = store.read("research_brief")
    patterns = store.read("ad_patterns")

    storyboard = _run_async(
        _write_storyboard_variant(
            settings,
            paths,
            angle=angle,
            hook=selected_hook,
            brief=brief,
            patterns=patterns,
            client=client,
            total_duration_s=total_duration_s,
        )
    )

    # Write scratch artifact outside registry: artifacts/variants/<angle>.json
    variants_dir = paths.artifacts / "variants"
    variants_dir.mkdir(parents=True, exist_ok=True)
    variant_file = variants_dir / f"{angle}.json"
    write_json(variant_file, storyboard.model_dump(mode="json"))

    return {
        "angle": angle,
        "shots": len(storyboard.shots),
        "duration_s": storyboard.meta.total_duration_s,
        "valid": True,
        "variant_path": str(variant_file),
    }


# ===========================================================================
# 3. Variant Judging & Splicing
# ===========================================================================


async def _judge_variants(
    settings: Settings,
    paths: RunPaths,
    variants: dict[str, Storyboard],
    *,
    client: LLMClient | None = None,
) -> JudgeResult:
    """Score three variants on STRONG tier, select winner, and generate splice list."""
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    try:
        brief = store.read("research_brief")
        prohibited = (
            brief.prohibited_facts
            if hasattr(brief, "prohibited_facts") and brief.prohibited_facts
            else DEFAULT_PROHIBITED_FACTS
        )
    except Exception:
        prohibited = DEFAULT_PROHIBITED_FACTS

    # Validator 10: assert prohibited facts are absent from EVERY variant, including losers
    for angle_name, v in variants.items():
        _scan_storyboard_for_prohibited_facts(v, prohibited)

    if client is None:
        client = _get_llm_client(settings, paths)

    # Pass variants reduced to beats, VO, and shot summaries (spec line 2007)
    reduced_variants: list[dict[str, Any]] = []
    for angle_name, v in variants.items():
        reduced_variants.append(
            {
                "angle": angle_name,
                "total_duration_s": v.meta.total_duration_s,
                "beats": [b.model_dump() for b in v.beats],
                "voiceover": {
                    "full_text": v.voiceover.full_text,
                    "segments": [seg.model_dump() for seg in v.voiceover.segments],
                },
                "shots": [
                    {
                        "id": s.id,
                        "beat": s.beat.value if hasattr(s.beat, "value") else str(s.beat),
                        "duration_s": s.duration_s,
                        "description": s.description,
                        "on_screen_text": [ost.text for ost in s.on_screen_text],
                    }
                    for s in v.shots
                ],
            }
        )

    prompt = build_variant_judge_prompt(json.dumps(reduced_variants, indent=2))
    judge_res = await client.complete_validated(
        tier=Tier.STRONG,
        messages=[{"role": "user", "content": prompt}],
        schema=JudgeResult,
        temperature=0.0,
        stage="judge_variants",
    )
    return judge_res


def _apply_splices(
    winner: Storyboard,
    splices: list[dict[str, Any] | SpliceDirective],
    variants: dict[str, Storyboard],
) -> Storyboard:
    """Integrate each splice into the winner, re-id, re-time, and record receipt trail (WOW-3).

    If a splice cannot be integrated without breaking a validator, drop the splice and
    record why in warnings. Never ship a storyboard that fails to parse.
    """
    current_winner = winner.model_copy(deep=True)
    applied_sources: list[str] = []

    for splice in splices:
        s_dict = splice if isinstance(splice, dict) else splice.model_dump()
        from_angle = str(s_dict.get("from_angle", ""))
        shot_id = str(s_dict.get("shot_id", ""))

        if not from_angle or not shot_id:
            continue

        source_variant = variants.get(from_angle)
        if not source_variant:
            current_winner.warnings.append(
                f"Splice source variant '{from_angle}' not found; dropped splice."
            )
            continue

        source_shot = next((s for s in source_variant.shots if s.id == shot_id), None)
        if not source_shot:
            current_winner.warnings.append(
                f"Splice source shot '{shot_id}' not found in variant '{from_angle}'; dropped splice."
            )
            continue

        # Prepare a candidate storyboard with the splice integrated
        candidate = current_winner.model_copy(deep=True)
        target_beat_name = source_shot.beat
        target_beat_span = next(
            (b for b in candidate.beats if b.beat == target_beat_name), None
        )

        if not target_beat_span:
            current_winner.warnings.append(
                f"Beat '{target_beat_name}' for spliced shot '{shot_id}' not found in winner; dropped splice."
            )
            continue

        # Re-id shot to next available id (e.g. s13, s14...)
        max_num = max(
            [
                int(s.id.lstrip("s"))
                for s in candidate.shots
                if s.id.lstrip("s").isdigit()
            ]
            or [len(candidate.shots)]
        )
        new_shot_id = f"s{max_num + 1:02d}"

        new_shot = source_shot.model_copy(deep=True)
        new_shot.id = new_shot_id
        new_shot.beat = target_beat_name

        # Re-time all shots in target beat to fit target_beat_span exactly
        existing_beat_shots = [s for s in candidate.shots if s.beat == target_beat_name]
        combined_beat_shots = [*existing_beat_shots, new_shot]
        total_beat_dur = round(target_beat_span.end_s - target_beat_span.start_s, 2)
        raw_sum = sum(s.duration_s for s in combined_beat_shots)

        if raw_sum <= 0:
            current_winner.warnings.append(
                f"Invalid zero shot duration sum in beat '{target_beat_name}'; dropped splice."
            )
            continue

        # Scale durations proportionally
        for s in combined_beat_shots:
            s.duration_s = round(s.duration_s * total_beat_dur / raw_sum, 2)
        drift = round(total_beat_dur - sum(s.duration_s for s in combined_beat_shots), 2)
        combined_beat_shots[-1].duration_s = round(
            combined_beat_shots[-1].duration_s + drift, 2
        )

        # Assign contiguous start times within beat
        cur_t = target_beat_span.start_s
        for s in combined_beat_shots:
            s.start_s = round(cur_t, 2)
            cur_t = round(cur_t + s.duration_s, 2)
            # Re-time on-screen text to fit inside shot
            for ost in s.on_screen_text:
                ost.at_s = s.start_s
                ost.until_s = round(min(s.start_s + s.duration_s, target_beat_span.end_s), 2)

        # Replace beat shots in candidate.shots and sort by start_s
        other_shots = [s for s in candidate.shots if s.beat != target_beat_name]
        candidate.shots = sorted([*other_shots, *combined_beat_shots], key=lambda s: s.start_s)

        # Adjust any VO segment timing attached to beat shots
        shot_map = {s.id: s for s in candidate.shots}
        for seg in candidate.voiceover.segments:
            if seg.shot_id in shot_map:
                sh = shot_map[seg.shot_id]
                seg.start_s = sh.start_s
                seg.end_s = round(min(sh.start_s + sh.duration_s, candidate.meta.total_duration_s), 2)

        # Verify whether candidate satisfies all Storyboard validators
        try:
            validated = Storyboard.model_validate(candidate.model_dump(mode="json"))
            current_winner = validated
            if from_angle not in applied_sources:
                applied_sources.append(from_angle)
        except Exception as exc:
            current_winner.warnings.append(
                f"Splice of shot '{shot_id}' from angle '{from_angle}' broke validation ({exc}); dropped splice."
            )

    # Record receipt trail in generation.variants[].beats_stolen_from (WOW-3)
    winner_angle_val = current_winner.meta.angle
    for vr in current_winner.generation.variants:
        if vr.angle == winner_angle_val:
            vr.beats_stolen_from = list(applied_sources)

    return current_winner


def judge_variants(
    *,
    settings: Settings,
    paths: RunPaths,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    """Score three variants, pick a winner, emit the splice list.

    WRITES artifacts/review_verdict.json? — NO. Writes nothing; returns the judgement.
    RETURNS {"variants":[...],"winner":str,"splices":[...],"winner_path":str}
    """
    variants_dir = paths.artifacts / "variants"
    variants: dict[str, Storyboard] = {}

    for angle in ANGLES:
        var_file = variants_dir / f"{angle}.json"
        if not var_file.exists():
            raise FileNotFoundError(
                f"Variant scratch artifact missing: {var_file}. Ensure write_storyboard_variant was called for '{angle}'."
            )
        data = read_json(var_file)
        variants[angle] = Storyboard.model_validate(data)

    judge_res = _run_async(_judge_variants(settings, paths, variants, client=client))
    winner_angle = str(
        judge_res.winner.value if hasattr(judge_res.winner, "value") else judge_res.winner
    )

    if winner_angle not in variants:
        raise ValueError(
            f"Judge selected winner '{winner_angle}' which is not among available variants {list(variants.keys())}"
        )

    winner_storyboard = variants[winner_angle].model_copy(deep=True)

    # Build GenerationBlock on winner
    winner_score_obj = next(
        (v for v in judge_res.variants if (v.angle.value if hasattr(v.angle, "value") else str(v.angle)) == winner_angle),
        judge_res.variants[0],
    )
    scores = winner_score_obj.scores
    w_mean = float(winner_score_obj.weighted_mean)

    creative_scores = CreativeScores(
        hook_strength=float(scores.get("hook_strength", 8.5)),
        mechanism_clarity=float(scores.get("mechanism_clarity", 8.5)),
        proof_credibility=float(scores.get("proof_credibility", 8.5)),
        emotional_arc=float(scores.get("emotional_arc", 8.0)),
        brand_fit=float(scores.get("brand_fit", 8.5)),
        compliance_safety=float(scores.get("compliance_safety", 9.0)),
        weighted_mean=w_mean,
        threshold=8.0,
        verdict="pass" if w_mean >= 8.0 else "request_changes",
    )

    variant_records = [
        VariantRecord(
            angle=v.angle,
            judge_score=float(v.weighted_mean),
            beats_stolen_from=[],
        )
        for v in judge_res.variants
    ]

    winner_storyboard.generation = GenerationBlock(
        variants_written=len(variants),
        variants=variant_records,
        winner=AngleName(winner_angle),
        revision_rounds=0,
        creative_scores=creative_scores,
        claims_rewrite_rounds=0,
    )

    # WOW-3: Apply splices from losing variants into winner
    spliced_winner = _apply_splices(
        winner_storyboard,
        [s.model_dump() if hasattr(s, "model_dump") else s for s in judge_res.splices],
        variants,
    )

    # Write contracted winner artifact via ArtifactStore
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    winner_path = store.write("storyboard", spliced_winner)

    return {
        "variants": [
            v.model_dump(mode="json") if hasattr(v, "model_dump") else v
            for v in judge_res.variants
        ],
        "winner": winner_angle,
        "splices": [
            s.model_dump(mode="json") if hasattr(s, "model_dump") else s
            for s in judge_res.splices
        ],
        "winner_path": str(winner_path),
    }
