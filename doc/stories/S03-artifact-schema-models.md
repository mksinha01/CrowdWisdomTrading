# S03 — Artifact schema models  ⚠ LARGE

**Phase** 1 · **Depends on** S02 · **Blocks** S04, S09, S14
**Spec** `doc/video-ads-agent.md` lines **367–884** (all of §3)
**Context budget** ~22k (spec 6k + story 1.6k + output 12k) — **the largest story in the set**
**Produces** `src/cwt/domain/__init__.py`, `domain/models.py` (models half)

---

## Goal

Every JSON artifact that flows between stages gets a pydantic v2 model. These models *are* this
project's database schema. After this story, an artifact that does not match the contract cannot be
constructed at all — which means every downstream stage can trust its inputs without defensive checks.

**This story writes models only. The eleven `Storyboard` validators are S04.** Split point is marked.

## Interface contract — FROZEN

```python
# domain/models.py
SCHEMA_VERSION = 1

class ArtifactVersionError(RuntimeError): ...

class ArtifactBase(BaseModel):
    schema_version: int = SCHEMA_VERSION
    @model_validator(mode="after")
    def _check_version(self): ...        # raise ArtifactVersionError if schema_version > SCHEMA_VERSION

# ── §3.1 ──
class AdQuery(BaseModel):       keywords: list[str]; countries: list[str]
                                window_start: date; window_end: date; window_days: int
class AdSource(BaseModel):      actor_id: str; actor_fallbacks_tried: list[str]; run_id: str
                                dataset_id: str; items_returned: int; items_after_window_filter: int
                                actual_charge_usd: float; charge_cap_usd: float
class AdRanking(BaseModel):     method: str; weights: dict[str, float]
                                excluded_reasons: dict[str, int]
class Ad(BaseModel):            ad_id: str; collation_id: str | None; page_name: str; page_id: str | None
                                is_active: bool; started_running: date; ended_running: date | None
                                active_days: int; publisher_platforms: list[str]; display_format: str
                                cta_type: str | None; cta_text: str | None; link_url: str | None
                                body_text: str; title: str | None; link_description: str | None
                                image_urls: list[str]; video_urls: list[str]
                                video_duration_s: float | None; ad_library_url: str
                                performance_score: float; normalised_from: str
class WinningAds(ArtifactBase): query: AdQuery; source: AdSource; ranking: AdRanking
                                ads: list[Ad]; warnings: list[str]

# ── §3.2 ──
class HookPattern(BaseModel):   text: str; archetype: HookArchetype; stop_power_score: float
                                why_it_stops_the_scroll: str
class BeatEntry(BaseModel):     beat: BeatName; start_s: float; end_s: float; what_happens: str
class MedianBeat(BaseModel):    beat: BeatName; start_s: float; end_s: float; tolerance_s: float
class PatternAggregate(BaseModel): ad_count: int; archetype_distribution: dict[str, float]
                                median_hook_duration_s: float; median_beat_timeline: list[MedianBeat]
                                underused_high_durability: list[str]
class AdPattern(BaseModel):     ad_id: str; hook: HookPattern; pain: str; concept: str
                                beat_sheet: list[BeatEntry]
class AdPatterns(ArtifactBase): source_ads_sha256: str; patterns: list[AdPattern]
                                aggregate: PatternAggregate; warnings: list[str]

# ── §3.3 ──
class ResearchClaim(BaseModel): text: str; source_url: str; source_title: str
                                published_date: date | None; provider: str; confidence: float
class ResearchAngle(BaseModel): angle: AngleName; claims: list[ResearchClaim]
                                synthesis: str; search_queries_used: list[str]
class ProhibitedFact(BaseModel): fact: str; reason: str; rule: str
class PricingTier(BaseModel):   tier: str; price: str; period: str
class BrandPalette(BaseModel):  background: str; surface: str; primary_accent: str
                                secondary_accents: list[str]; success: str; text: str; mood: str
class ProductFacts(BaseModel):  name: str; tagline: str; legal_entity: str; landing_url: str
                                landing_url_verified_200: bool; markets: list[str]
                                pricing: list[PricingTier]; explicit_disclaimers: dict[str, bool]
                                brand: BrandPalette
class ResearchBrief(ArtifactBase): window: dict[str, date]; angles: dict[AngleName, ResearchAngle]
                                product: ProductFacts; prohibited_facts: list[ProhibitedFact]

# ── §3.4 shot grammar — every field here is consumed by S14's filtergraph ──
class Transition(BaseModel):    type: TransitionName; duration_s: float
class Camera(BaseModel):        move: CameraMove; intensity: float = Field(ge=0.0, le=1.0)
                                lens_mm: int; depth_of_field: Literal["deep","shallow","medium"]
                                stabilisation: Literal["locked","smooth","handheld"]
class Lighting(BaseModel):      key: str; contrast: Literal["low","medium","high","extreme"]
                                colour_temp_k: int
class Composition(BaseModel):   framing: str; text_safe_area: dict[str, float]  # keys: top,bottom
class AssetRef(BaseModel):      kind: str; ref: str; source: Literal["internal","cc0","generated"]
class OnScreenText(BaseModel):  text: str; at_s: float; until_s: float; style: str; position: str
class SfxCue(BaseModel):        ref: str; at_s: float; gain_db: float
class Shot(BaseModel):          id: str; beat: BeatName; start_s: float; duration_s: float
                                description: str; subject: SubjectName; asset: AssetRef
                                camera: Camera; lighting: Lighting; palette: list[str]
                                composition: Composition; transition_in: Transition
                                transition_out: Transition; on_screen_text: list[OnScreenText]
                                sfx: list[SfxCue]
class VisualHook(BaseModel):    archetype: HookArchetype; first_3_seconds: str; text_overlay: str
                                sound_design: str; stop_power_score: float
                                why_it_stops_the_scroll: str; alternatives_considered: list[dict]
class BeatSpan(BaseModel):      beat: BeatName; start_s: float; end_s: float
                                tolerance_s: float; on_target: bool
class VOSegment(BaseModel):     shot_id: str; text: str; start_s: float; end_s: float
class Voiceover(BaseModel):     voice_id: str; full_text: str; segments: list[VOSegment]
                                total_words: int; words_per_minute: int
class MusicCue(BaseModel):      at_s: float; level: float
class Music(BaseModel):         asset_ref: str; intensity_curve: list[MusicCue]
                                riser_at_s: list[float]; resolve_at_s: float; duck_under_vo: bool
class ComplianceBlock(BaseModel): risk_disclosure_present: bool; risk_disclosure_shot_id: str | None
                                risk_disclosure_text: str; safe_harbour_duration_s: float
                                methodology_adjacent: bool; claims_checked: bool
                                prohibited_facts_absent: bool
class VariantRecord(BaseModel): angle: AngleName; judge_score: float; beats_stolen_from: list[str]
class CreativeScores(BaseModel): hook_strength: float; mechanism_clarity: float
                                proof_credibility: float; emotional_arc: float; brand_fit: float
                                compliance_safety: float; weighted_mean: float
                                threshold: float; verdict: Literal["pass","request_changes"]
class GenerationBlock(BaseModel): variants_written: int; variants: list[VariantRecord]; winner: AngleName
                                revision_rounds: int; creative_scores: CreativeScores
                                claims_rewrite_rounds: int
class StoryboardMeta(BaseModel): run_id: str; generated_at: str; product: str; landing_url: str
                                total_duration_s: float; aspect_ratio: str; resolution: str; fps: int
                                angle: AngleName; angle_rationale: str
class Storyboard(ArtifactBase):  meta: StoryboardMeta; visual_hook: VisualHook
                                beats: list[BeatSpan]; shots: list[Shot]; voiceover: Voiceover
                                music: Music; compliance: ComplianceBlock
                                generation: GenerationBlock

# ── §3.5 ──
class HookCandidate(BaseModel): id: str; archetype: HookArchetype; text_overlay: str
                                first_frame_description: str; sound_design: str
                                stop_power_score: float; why_it_stops_the_scroll: str
                                selected: bool; rejection_reason: str | None
class HookCandidates(ArtifactBase): candidates: list[HookCandidate]
                                archetypes_covered: list[str]; underused_archetypes_boosted: list[str]
class ReviewVerdict(ArtifactBase): reviewer: str; round: int
                                verdict: Literal["pass","request_changes"]
                                scores: dict[str, float]; weighted_mean: float; threshold: float
                                weakest_axes: list[str]; changes_requested: str | None
                                must_fix: list[str]; must_not_change: list[str]
class ClaimFinding(BaseModel):  rule_id: str; severity: Severity; matched_text: str
                                location: dict | None; why: str; fix: str
class ClaimsReport(ArtifactBase): stage: Literal["pre_render","post_render"]
                                verdict: Literal["pass","request_changes","block"]
                                deterministic: dict; llm_judge: dict
                                rewrite_instructions: list[str]
                                rounds_used: int; rounds_remaining: int
class RenderManifest(ArtifactBase): backend_chain_configured: list[str]; backend_chain_tried: list[dict]
                                backend_used: str; output: dict; loudness: dict
                                argv: list[str]; cwd: str; assets: list[dict]
                                ffmpeg_version: str; warnings: list[str]
```

Enums (StrEnum) — the closed sets from spec lines 4474–4496:
`BeatName`, `HookArchetype`, `CameraMove`, `TransitionName`, `SubjectName`, `AngleName`, `Severity`.

Literal sets (not enums — they carry no weight): `DepthOfField`, `Stabilisation`, `Contrast`.

## Rules that bind this story

- **G11** — the `Shot` field set is only inferable from the §3.4 example JSON. Every field above is
  either in that example or consumed by S14's filtergraph. **If you add a field, S14 must be told.**
- **§3.0 rule 2** — evolution is append-only. Adding a field means an *optional* field plus a
  validator. Never rename or repurpose.
- **Rule R1** — no model may carry an API token. `AdSource` deliberately stores `run_id`/`dataset_id`
  but **not** a request URL. `Ad` stores `ad_library_url` (public) — never an Apify API URL.

## Build steps

1. Write the enums first — everything else references them.
2. Write the models in §3 order: ads → patterns → brief → shot grammar → storyboard → §3.5.
3. Every `*.json` example in §3 is a **test fixture**. Transcribe each one literally into
   `tests/fixtures/` and assert it round-trips: `Storyboard.model_validate(example)` then
   `.model_dump()` equals the example modulo key order.
4. `ArtifactBase._check_version` raises `ArtifactVersionError` — never silently misinterprets.
5. Add `model_config = ConfigDict(extra="forbid")` on every artifact model so a typo'd key is an error.
6. **STOP HERE.** Validators are S04.

## Decisions the spec leaves open

- **`palette` length.** §3.4's validator 7 says "no near-black and no accent". Model it as
  `list[str]` with a `conlist(str, min_length=4, max_length=4)` and a hex pattern — the spec's own
  example always has exactly 4.
- **`BeatSpan.tolerance_s`** is required in §3.4 but the §3.2 `MedianBeat` supplies it. Default it
  from `beats.py` (S05) rather than hardcoding — but S05 comes later, so make it required here.
- **`location` on `ClaimFinding`** is `dict | None` because §3.5's `claims_report` example has it but
  `detect_claims` (S06) does not produce it. Keep optional.
- **`alternatives_considered`** is `list[dict]` with three known keys — do not model it tightly. The
  shape varies and the field is presentational.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_models.py -q -k "not validator"    # round-trips green
.venv/Scripts/python -c "
from cwt.domain.models import Storyboard, ArtifactVersionError
import json,pathlib
sb=json.loads(pathlib.Path('tests/fixtures/storyboard.json').read_text())
Storyboard.model_validate(sb); print('ok')
sb['schema_version']=99
try: Storyboard.model_validate(sb); print('FAIL — should have raised')
except ArtifactVersionError: print('version gate ok')"
```

## Handoff

S04 adds the eleven validators to this same file. S14 (`filtergraph.py`) imports `Shot`, `Camera`,
`Lighting`, `Composition` — **changing those four models breaks the renderer**. S22 builds
`Storyboard` objects and relies on `extra="forbid"` to catch model drift.
