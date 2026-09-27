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


class Severity(StrEnum):
    HARD = "hard"
    SOFT = "soft"


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


class Storyboard(ArtifactBase):
    meta: StoryboardMeta
    visual_hook: VisualHook
    beats: list[BeatSpan]
    shots: list[Shot]
    voiceover: Voiceover
    music: Music
    compliance: ComplianceBlock
    generation: GenerationBlock


# ---------------------------------------------------------------------------
# S04 Split Point: Eleven Storyboard validators are appended here in S04.
# ---------------------------------------------------------------------------


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
