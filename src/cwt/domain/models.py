"""Domain models for all CWT video ads pipeline JSON artifacts.

Contract specified in doc/video-ads-agent.md §3 and doc/stories/S03-artifact-schema-models.md.
"""

from __future__ import annotations

import functools
import math
from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

SCHEMA_VERSION = 1


class ArtifactVersionError(RuntimeError):
    """Raised when an artifact has a schema_version greater than supported."""

    pass


# ===========================================================================
# Closed Enums (§11.4 reference data / spec lines 4474–4496)
# ===========================================================================


class BeatName(StrEnum):
    HOOK = "hook"
    PROBLEM = "problem"
    AGITATION = "agitation"
    MECHANISM = "mechanism"
    PROOF = "proof"
    OBJECTION = "objection"
    CTA = "cta"


class HookArchetype(StrEnum):
    PATTERN_INTERRUPT = "pattern_interrupt"
    CONTRARIAN_STAT = "contrarian_stat"
    QUESTION = "question"
    VISUAL_SHOCK = "visual_shock"
    SOCIAL_PROOF = "social_proof"
    PAIN_POINT = "pain_point"
    BOLD_STATEMENT = "bold_statement"


class CameraMove(StrEnum):
    STATIC = "static"
    PUSH_IN = "push_in"
    PULL_OUT = "pull_out"
    WHIP_PAN = "whip_pan"


class TransitionName(StrEnum):
    CUT = "cut"
    DISSOLVE = "dissolve"
    FLASH_WHITE = "flash_white"
    WIPELEFT = "wipeleft"
    WIPERIGHT = "wiperight"
    ZOOMBLUR = "zoomblur"


class SubjectName(StrEnum):
    ABSTRACT_MARKET_DATA = "abstract_market_data"
    TRADER_SILHOUETTE = "trader_silhouette"
    CHART_DETAIL = "chart_detail"
    CITY_NIGHT = "city_night"
    SCREEN_GLOW = "screen_glow"
    TYPOGRAPHY_CARD = "typography_card"


class AngleName(StrEnum):
    PAIN = "pain"
    UNIQUE_DATA = "unique_data"
    CROWD_EFFECT = "crowd_effect"

from cwt.domain.claims import Severity


# Literal sets (carry no weight / structural types)
DepthOfField = Literal["deep", "shallow", "medium"]
Stabilisation = Literal["locked", "smooth", "handheld"]
Contrast = Literal["low", "medium", "high", "extreme"]
HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


# ===========================================================================
# Base Models
# ===========================================================================


class _CwtBaseModel(BaseModel):
    """Base model enforcing strict validation with extra keys forbidden."""

    model_config = ConfigDict(extra="forbid")


class ArtifactBase(_CwtBaseModel):
    """Base class for top-level JSON artifacts with version gating."""

    schema_version: int = SCHEMA_VERSION

    @model_validator(mode="after")
    def _check_version(self) -> Self:
        if self.schema_version > SCHEMA_VERSION:
            raise ArtifactVersionError(
                f"Unsupported schema_version {self.schema_version} "
                f"(current supported version is {SCHEMA_VERSION})"
            )
        return self


# ===========================================================================
# §3.1 Winning Ads
# ===========================================================================


class AdQuery(_CwtBaseModel):
    keywords: list[str]
    countries: list[str]
    window_start: date
    window_end: date
    window_days: int


class AdSource(_CwtBaseModel):
    actor_id: str
    actor_fallbacks_tried: list[str] = Field(default_factory=list)
    run_id: str
    dataset_id: str
    items_returned: int
    items_after_window_filter: int
    actual_charge_usd: float
    charge_cap_usd: float


class AdRanking(_CwtBaseModel):
    method: str
    weights: dict[str, float]
    excluded_reasons: dict[str, int]


class Ad(_CwtBaseModel):
    ad_id: str
    collation_id: str | None = None
    page_name: str
    page_id: str | None = None
    is_active: bool
    started_running: date
    ended_running: date | None = None
    active_days: int
    publisher_platforms: list[str]
    display_format: str
    cta_type: str | None = None
    cta_text: str | None = None
    link_url: str | None = None
    body_text: str
    title: str | None = None
    link_description: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    video_urls: list[str] = Field(default_factory=list)
    video_duration_s: float | None = None
    ad_library_url: str
    performance_score: float
    normalised_from: str


class WinningAds(ArtifactBase):
    generated_at: str | None = None
    query: AdQuery
    source: AdSource
    ranking: AdRanking
    ads: list[Ad]
    warnings: list[str] = Field(default_factory=list)


# ===========================================================================
# §3.2 Ad Patterns
# ===========================================================================


class HookPattern(_CwtBaseModel):
    text: str
    archetype: HookArchetype
    stop_power_score: float
    why_it_stops_the_scroll: str


class BeatEntry(_CwtBaseModel):
    beat: BeatName
    start_s: float
    end_s: float
    what_happens: str


class MedianBeat(_CwtBaseModel):
    beat: BeatName
    start_s: float
    end_s: float
    tolerance_s: float


class PatternAggregate(_CwtBaseModel):
    ad_count: int
    archetype_distribution: dict[str, float]
    median_hook_duration_s: float
    median_beat_timeline: list[MedianBeat]
    underused_high_durability: list[str] = Field(default_factory=list)


class AdPattern(_CwtBaseModel):
    ad_id: str
    hook: HookPattern
    pain: str
    concept: str
    beat_sheet: list[BeatEntry]


class AdPatterns(ArtifactBase):
    source_ads_sha256: str
    patterns: list[AdPattern]
    aggregate: PatternAggregate
    warnings: list[str] = Field(default_factory=list)


# ===========================================================================
# §3.3 Research Brief
# ===========================================================================


class ResearchClaim(_CwtBaseModel):
    text: str
    source_url: str
    source_title: str
    published_date: date | None = None
    provider: str
    confidence: float


class ResearchAngle(_CwtBaseModel):
    angle: AngleName
    claims: list[ResearchClaim]
    synthesis: str
    search_queries_used: list[str] = Field(default_factory=list)


class ProhibitedFact(_CwtBaseModel):
    fact: str
    reason: str
    rule: str


class PricingTier(_CwtBaseModel):
    tier: str
    price: str
    period: str


class BrandPalette(_CwtBaseModel):
    background: str
    surface: str
    primary_accent: str
    secondary_accents: list[str]
    success: str
    text: str
    mood: str


class ProductFacts(_CwtBaseModel):
    name: str
    tagline: str
    legal_entity: str
    landing_url: str
    landing_url_verified_200: bool
    markets: list[str]
    pricing: list[PricingTier]
    explicit_disclaimers: dict[str, bool | str]
    brand: BrandPalette


class ResearchBrief(ArtifactBase):
    window: dict[str, date]
    angles: dict[AngleName, ResearchAngle]
    product: ProductFacts
    prohibited_facts: list[ProhibitedFact]


# ===========================================================================
# §3.4 Shot Grammar & Storyboard
# ===========================================================================


class Transition(_CwtBaseModel):
    type: TransitionName
    duration_s: float


class Camera(_CwtBaseModel):
    move: CameraMove
    intensity: float = Field(default=0.0, ge=0.0, le=1.0)
    lens_mm: int
    depth_of_field: DepthOfField
    stabilisation: Stabilisation


class Lighting(_CwtBaseModel):
    key: str
    contrast: Contrast
    colour_temp_k: int


class Composition(_CwtBaseModel):
    framing: str
    text_safe_area: dict[str, float]


class AssetRef(_CwtBaseModel):
    kind: str
    ref: str
    source: Literal["internal", "cc0", "generated"]


class OnScreenText(_CwtBaseModel):
    text: str
    at_s: float
    until_s: float
    style: str
    position: str


class SfxCue(_CwtBaseModel):
    ref: str
    at_s: float
    gain_db: float


class Shot(_CwtBaseModel):
    id: str
    beat: BeatName
    start_s: float
    duration_s: float
    description: str
    subject: SubjectName
    asset: AssetRef
    camera: Camera
    lighting: Lighting
    palette: Annotated[list[HexColor], Field(min_length=4, max_length=4)]
    composition: Composition
    transition_in: Transition
    transition_out: Transition
    on_screen_text: list[OnScreenText] = Field(default_factory=list)
    sfx: list[SfxCue] = Field(default_factory=list)


class VisualHook(_CwtBaseModel):
    archetype: HookArchetype
    first_3_seconds: str
    text_overlay: str
    sound_design: str
    stop_power_score: float
    why_it_stops_the_scroll: str
    alternatives_considered: list[dict[str, Any]] = Field(default_factory=list)


class BeatSpan(_CwtBaseModel):
    beat: BeatName
    start_s: float
    end_s: float
    tolerance_s: float
    on_target: bool = True


class VOSegment(_CwtBaseModel):
    shot_id: str
    text: str
    start_s: float
    end_s: float


class Voiceover(_CwtBaseModel):
    voice_id: str
    full_text: str
    segments: list[VOSegment]
    total_words: int
    words_per_minute: int


class MusicCue(_CwtBaseModel):
    at_s: float
    level: float


class Music(_CwtBaseModel):
    asset_ref: str
    intensity_curve: list[MusicCue]
    riser_at_s: list[float] = Field(default_factory=list)
    resolve_at_s: float
    duck_under_vo: bool = True


class ComplianceBlock(_CwtBaseModel):
    risk_disclosure_present: bool
    risk_disclosure_shot_id: str | None = None
    risk_disclosure_text: str
    safe_harbour_duration_s: float
    methodology_adjacent: bool
    claims_checked: bool
    prohibited_facts_absent: bool


class VariantRecord(_CwtBaseModel):
    angle: AngleName
    judge_score: float
    beats_stolen_from: list[str] = Field(default_factory=list)


class CreativeScores(_CwtBaseModel):
    hook_strength: float
    mechanism_clarity: float
    proof_credibility: float
    emotional_arc: float
    brand_fit: float
    compliance_safety: float
    weighted_mean: float
    threshold: float
    verdict: Literal["pass", "request_changes"]


class GenerationBlock(_CwtBaseModel):
    variants_written: int
    variants: list[VariantRecord]
    winner: AngleName
    revision_rounds: int
    creative_scores: CreativeScores
    claims_rewrite_rounds: int


class StoryboardMeta(_CwtBaseModel):
    run_id: str
    generated_at: str
    product: str
    landing_url: str
    total_duration_s: float
    aspect_ratio: str
    resolution: str
    fps: int
    angle: AngleName
    angle_rationale: str


_POSITION_Y_MAP: dict[str, float] = {
    "top": 0.08,
    "header": 0.08,
    "top_banner": 0.08,
    "upper_third": 0.33,
    "center": 0.50,
    "middle": 0.50,
    "lower_third": 0.70,
    "bottom": 0.90,
    "footer": 0.90,
}


def _hex_to_rgb(hex_code: str) -> tuple[int, int, int]:
    clean = hex_code.lstrip("#")
    return int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16)


@functools.lru_cache(maxsize=1)
def _get_video_duration_bounds() -> tuple[float, float]:
    try:
        from cwt.config import Settings

        settings = Settings.from_env()
        return float(settings.video_min_seconds), float(settings.video_max_seconds)
    except Exception:
        return 30.0, 60.0


DEFAULT_PROHIBITED_FACTS: list[dict[str, Any]] = [
    {
        "fact": "74.1% of tracked directions hit",
        "reason": "Unverifiable. Appears as 73%, 73.8% and 74.1% on the product's own site.",
        "rule": "Must never appear in any generated script, in any form, including paraphrases and rounded variants.",
    },
    {
        "fact": "16,564 professional traders tracked",
        "reason": "Self-reported with no published counting or de-duplication methodology.",
        "rule": "May not be stated as a verified fact. If used, it must be attributed to the company ('the company says it tracks...') or avoided.",
    },
    {
        "fact": "Institutional sentiment feature",
        "reason": "No methodology or data source disclosed anywhere.",
        "rule": "May not be described or implied in creative.",
    },
]


class Storyboard(ArtifactBase):
    meta: StoryboardMeta
    visual_hook: VisualHook
    beats: list[BeatSpan]
    shots: list[Shot]
    voiceover: Voiceover
    music: Music
    compliance: ComplianceBlock
    generation: GenerationBlock
    warnings: list[str] = Field(default_factory=list)
    validation_context: dict[str, Any] | None = None

    @model_serializer(mode="wrap")
    def _serialize_storyboard(self, handler: Any) -> dict[str, Any]:
        result = handler(self)
        if "validation_context" not in self.model_fields_set:
            result.pop("validation_context", None)
        if "warnings" not in self.model_fields_set:
            result.pop("warnings", None)
        return result

    # -----------------------------------------------------------------------
    # Eleven Storyboard Validators (doc/stories/S04-storyboard-validators.md)
    # -----------------------------------------------------------------------

    @model_validator(mode="after")
    def beats_covers_timeline(self) -> Self:
        """1. Assert beats span 0 -> total_duration_s with no gaps or overlaps.

        Tolerance for beat contiguity is 0.01s.
        """
        if not self.beats:
            raise ValueError("Storyboard beats list cannot be empty")

        sorted_beats = sorted(self.beats, key=lambda b: b.start_s)
        # Check start
        if not math.isclose(sorted_beats[0].start_s, 0.0, abs_tol=0.01):
            gap = sorted_beats[0].start_s
            raise ValueError(
                f"Beats do not start at 0.0s (first beat '{sorted_beats[0].beat}' "
                f"starts at {sorted_beats[0].start_s:.2f}s, gap: {gap:.2f}s)"
            )

        # Check contiguous
        for i in range(len(sorted_beats) - 1):
            curr_b = sorted_beats[i]
            next_b = sorted_beats[i + 1]
            if not math.isclose(curr_b.end_s, next_b.start_s, abs_tol=0.01):
                diff = next_b.start_s - curr_b.end_s
                if diff > 0:
                    raise ValueError(
                        f"Gap of {diff:.2f}s between beat '{curr_b.beat}' (ends {curr_b.end_s:.2f}s) "
                        f"and '{next_b.beat}' (starts {next_b.start_s:.2f}s)"
                    )
                else:
                    overlap = -diff
                    raise ValueError(
                        f"Overlap of {overlap:.2f}s between beat '{curr_b.beat}' (ends {curr_b.end_s:.2f}s) "
                        f"and '{next_b.beat}' (starts {next_b.start_s:.2f}s)"
                    )

        # Check end
        if not math.isclose(sorted_beats[-1].end_s, self.meta.total_duration_s, abs_tol=0.01):
            diff = self.meta.total_duration_s - sorted_beats[-1].end_s
            if diff > 0:
                raise ValueError(
                    f"Gap of {diff:.2f}s between last beat '{sorted_beats[-1].beat}' "
                    f"end ({sorted_beats[-1].end_s:.2f}s) and total_duration_s ({self.meta.total_duration_s:.2f}s)"
                )
            else:
                overlap = -diff
                raise ValueError(
                    f"Overlap of {overlap:.2f}s between last beat '{sorted_beats[-1].beat}' "
                    f"end ({sorted_beats[-1].end_s:.2f}s) and total_duration_s ({self.meta.total_duration_s:.2f}s)"
                )
        return self

    @model_validator(mode="after")
    def shots_match_beats(self) -> Self:
        """2. Assert every shot's [start, start+duration) falls within its declared beat's range."""
        beat_map: dict[BeatName, BeatSpan] = {b.beat: b for b in self.beats}
        for shot in self.shots:
            if shot.beat not in beat_map:
                raise ValueError(
                    f"Shot '{shot.id}' references unknown beat '{shot.beat}'"
                )
            beat = beat_map[shot.beat]
            shot_start = shot.start_s
            shot_end = shot.start_s + shot.duration_s
            if shot_start < beat.start_s - 0.01 or shot_end > beat.end_s + 0.01:
                raise ValueError(
                    f"Shot '{shot.id}' range [{shot_start:.2f}s, {shot_end:.2f}s) falls outside "
                    f"declared beat '{shot.beat}' range [{beat.start_s:.2f}s, {beat.end_s:.2f}s)"
                )
        return self

    @model_validator(mode="after")
    def duration_in_bounds(self) -> Self:
        """3. Assert total_duration_s is within [VIDEO_MIN_SECONDS, VIDEO_MAX_SECONDS]."""
        min_s, max_s = _get_video_duration_bounds()
        dur = self.meta.total_duration_s
        if dur < min_s or dur > max_s:
            raise ValueError(
                f"total_duration_s {dur:.2f}s outside bounds [{min_s:.2f}s, {max_s:.2f}s]"
            )
        return self

    @model_validator(mode="after")
    def shot_durations_sum(self) -> Self:
        """4. Assert shot durations sum to total_duration_s within 0.05s tolerance."""
        shot_total = sum(s.duration_s for s in self.shots)
        expected = self.meta.total_duration_s
        delta = abs(shot_total - expected)
        if not math.isclose(shot_total, expected, abs_tol=0.05):
            raise ValueError(
                f"Shot durations sum {shot_total:.2f}s does not match total_duration_s "
                f"{expected:.2f}s (delta: {delta:.2f}s, tolerance: 0.05s)"
            )
        return self

    @model_validator(mode="after")
    def hook_within_three_seconds(self) -> Self:
        """5. Assert hook beat ends at or before 3.0s + tolerance_s."""
        hook_beat = next((b for b in self.beats if b.beat == BeatName.HOOK), None)
        if hook_beat is None:
            raise ValueError("Hook beat is missing from storyboard beats")
        max_allowed = 3.0 + hook_beat.tolerance_s
        if hook_beat.end_s > max_allowed + 0.01:
            raise ValueError(
                f"Hook beat ends at {hook_beat.end_s:.2f}s, exceeding maximum allowed "
                f"3.0s + tolerance {hook_beat.tolerance_s:.2f}s ({max_allowed:.2f}s)"
            )
        return self

    @model_validator(mode="after")
    def beats_within_median_tolerance(self) -> Self:
        """6. Assert beat start times do not deviate from mined medians by more than tolerance_s."""
        medians: list[Any] | None = None
        if self.validation_context:
            medians = self.validation_context.get("median_beat_timeline")
            if medians is None and "aggregate" in self.validation_context:
                medians = self.validation_context["aggregate"].get("median_beat_timeline")

        if not medians:
            self.warnings.append(
                "Validator beats_within_median_tolerance skipped: median timeline unavailable in validation_context"
            )
            return self

        median_map: dict[str, tuple[float, float]] = {}
        for m in medians:
            if isinstance(m, dict):
                b_name = m.get("beat")
                m_start = float(m.get("start_s", 0.0))
                m_tol = float(m.get("tolerance_s", 0.0))
            else:
                b_name = getattr(m, "beat", None)
                m_start = float(getattr(m, "start_s", 0.0))
                m_tol = float(getattr(m, "tolerance_s", 0.0))
            if b_name:
                b_str = b_name.value if hasattr(b_name, "value") else str(b_name)
                median_map[b_str] = (m_start, m_tol)

        for beat in self.beats:
            b_str = beat.beat.value if hasattr(beat.beat, "value") else str(beat.beat)
            if b_str in median_map:
                m_start, m_tol = median_map[b_str]
                tol = m_tol if m_tol > 0 else beat.tolerance_s
                deviation = abs(beat.start_s - m_start)
                if deviation > tol + 0.01:
                    raise ValueError(
                        f"Beat '{beat.beat}' start {beat.start_s:.2f}s deviates from mined median "
                        f"{m_start:.2f}s by {deviation:.2f}s, exceeding tolerance {tol:.2f}s"
                    )
        return self

    @model_validator(mode="after")
    def palette_is_dark_and_accented(self) -> Self:
        """7. Assert each shot's palette has near-black (max RGB < 0x30) and accent (any RGB > 0x80)."""
        for shot in self.shots:
            rgbs = [_hex_to_rgb(c) for c in shot.palette]
            has_near_black = any(max(r, g, b) < 0x30 for r, g, b in rgbs)
            has_accent = any((r > 0x80 or g > 0x80 or b > 0x80) for r, g, b in rgbs)
            if not has_near_black or not has_accent:
                if not has_near_black and not has_accent:
                    missing = "near-black and accent"
                elif not has_near_black:
                    missing = "near-black"
                else:
                    missing = "accent"
                raise ValueError(
                    f"Shot '{shot.id}' palette lacks {missing} color "
                    f"(brand drift: dark mode requires near-black and vibrant accent)"
                )
        return self

    @model_validator(mode="after")
    def text_within_safe_area(self) -> Self:
        """8. Assert on-screen text sits within the shot's declared vertical safe area."""
        for shot in self.shots:
            top = shot.composition.text_safe_area.get("top", 0.0)
            bottom = shot.composition.text_safe_area.get("bottom", 0.0)
            safe_top = top
            safe_bottom = 1.0 - bottom
            for ost in shot.on_screen_text:
                pos = ost.position.lower().strip()
                if pos in _POSITION_Y_MAP:
                    y = _POSITION_Y_MAP[pos]
                else:
                    try:
                        y = float(pos)
                    except ValueError:
                        y = 0.50

                if y < safe_top - 0.01 or y > safe_bottom + 0.01:
                    raise ValueError(
                        f"Shot '{shot.id}' on-screen text '{ost.text}' at position '{ost.position}' "
                        f"(y={y:.2f}) sits outside declared safe area [top={top:.2f}, bottom={bottom:.2f}]"
                    )
        return self

    @model_validator(mode="after")
    def risk_disclosure_present(self) -> Self:
        """9. Assert compliance.risk_disclosure_present is True (Rule C1 hard gate)."""
        if not self.compliance.risk_disclosure_present:
            raise ValueError(
                "Rule C1 violation: compliance.risk_disclosure_present must be True"
            )
        return self

    @model_validator(mode="after")
    def no_prohibited_facts(self) -> Self:
        """10. Assert no prohibited facts or numeric variants appear anywhere in the script."""
        from cwt.domain.claims import scan_prohibited_facts

        prohibited: list[Any]
        if self.validation_context and "prohibited_facts" in self.validation_context:
            prohibited = self.validation_context["prohibited_facts"]
        else:
            prohibited = DEFAULT_PROHIBITED_FACTS

        check_targets: list[tuple[str, str]] = [
            (self.voiceover.full_text, "voiceover full_text")
        ]
        for seg in self.voiceover.segments:
            check_targets.append(
                (seg.text, f"voiceover segment for shot '{seg.shot_id}'")
            )
        for shot in self.shots:
            for ost in shot.on_screen_text:
                check_targets.append(
                    (ost.text, f"shot '{shot.id}' on-screen text")
                )

        for text, location in check_targets:
            if not text:
                continue
            findings = scan_prohibited_facts(text, prohibited)
            if findings:
                f = findings[0]
                raise ValueError(
                    f"Prohibited fact {f.why} ({f.matched_text!r}) matched in {location}"
                )
        return self

    @model_validator(mode="after")
    def voiceover_matches_shots(self) -> Self:
        """11. Assert VO segments reference existing shots and do not exceed video duration."""
        shot_ids = {s.id for s in self.shots}
        for seg in self.voiceover.segments:
            if seg.shot_id not in shot_ids:
                raise ValueError(
                    f"Voiceover segment references nonexistent shot_id '{seg.shot_id}'"
                )

        vo_duration = sum(seg.end_s - seg.start_s for seg in self.voiceover.segments)
        if vo_duration > self.meta.total_duration_s + 0.05:
            overrun = vo_duration - self.meta.total_duration_s
            raise ValueError(
                f"Summed voiceover segment duration {vo_duration:.2f}s exceeds video duration "
                f"{self.meta.total_duration_s:.2f}s by {overrun:.2f}s overrun"
            )
        return self


# ===========================================================================
# §3.5 Downstream Artifacts
# ===========================================================================


class HookCandidate(_CwtBaseModel):
    id: str
    archetype: HookArchetype
    text_overlay: str
    first_frame_description: str
    sound_design: str
    stop_power_score: float
    why_it_stops_the_scroll: str
    selected: bool
    rejection_reason: str | None = None


class HookCandidates(ArtifactBase):
    candidates: list[HookCandidate]
    archetypes_covered: list[str]
    underused_archetypes_boosted: list[str]


class ReviewVerdict(ArtifactBase):
    reviewer: str
    round: int
    verdict: Literal["pass", "request_changes"]
    scores: dict[str, float]
    weighted_mean: float
    threshold: float
    weakest_axes: list[str]
    changes_requested: str | None = None
    must_fix: list[str] = Field(default_factory=list)
    must_not_change: list[str] = Field(default_factory=list)


class ClaimFinding(_CwtBaseModel):
    rule_id: str
    severity: Severity
    matched_text: str
    location: dict[str, Any] | None = None
    why: str
    fix: str


class ClaimsReport(ArtifactBase):
    stage: Literal["pre_render", "post_render"]
    verdict: Literal["pass", "request_changes", "block"]
    deterministic: dict[str, Any]
    llm_judge: dict[str, Any]
    rewrite_instructions: list[str] = Field(default_factory=list)
    rounds_used: int
    rounds_remaining: int


class RenderManifest(ArtifactBase):
    backend_chain_configured: list[str]
    backend_chain_tried: list[dict[str, Any]]
    backend_used: str
    output: dict[str, Any]
    loudness: dict[str, Any]
    argv: list[str]
    cwd: str
    assets: list[dict[str, Any]]
    ffmpeg_version: str
    warnings: list[str] = Field(default_factory=list)
    # S16 addition (Rule C3): per-shot rendered timeline for QA risk-disclosure recompute.
    # Optional so the §3.5 fixture still validates without this field.
    shots: list[dict[str, Any]] | None = None
