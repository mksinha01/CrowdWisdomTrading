"""JSON schemas the LLM sees — one module-level constant per tool.

S29 — Plugin manifest & tool schemas.
Spec §10.2 lines 4059–4111, §7.2 lines 1818–1838.

DESIGN
------
* Every constant is an OpenAI-style function schema:
    {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
* Descriptions are imported directly from the tool function __doc__ (spec line 4083–4069):
  one source of truth, no drift.
* Every parameters block sets additionalProperties: False so the model gets a clear
  rejection rather than a silent **kwargs swallow (spec story rule).
* ALL_SCHEMAS is a tuple of the 19 in registration order so S30's register() can
  iterate and so tests can assert set equality with plugin.yaml.
* The verify_artifact name enum is generated from domain.artifacts.ARTIFACT_NAMES
  so it cannot drift (spec story § "Decisions the spec leaves open").
"""

from __future__ import annotations

from cwt.domain.artifacts import ARTIFACT_NAMES
from cwt.tools import ads, bundle, claims, patterns, research, storyboard, video


def _fn(
    name: str,
    description: str,
    properties: dict,
    required: list[str],
) -> dict:
    """Build one OpenAI-style function schema.

    Every schema produced here sets additionalProperties: False.
    """
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


# ---------------------------------------------------------------------------
# 1. Ads group
# ---------------------------------------------------------------------------

SOURCE_WINNING_ADS = _fn(
    name="cwt_source_winning_ads",
    description=ads.source_winning_ads.__doc__,
    properties={
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Search keywords for the Meta Ads Library scraper "
                "(e.g. ['trading signals', 'stock market alerts']). "
                "Defaults to the standard CWT keyword set when omitted."
            ),
        },
        "countries": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "ISO-3166-1 alpha-2 country codes to target "
                "(e.g. ['US', 'GB', 'IN']). Defaults to ['US', 'GB', 'IN']."
            ),
        },
        "window_days": {
            "type": "integer",
            "description": (
                "Lookback window in days. Must be 30. "
                "Do not widen this — the brief specifies last month."
            ),
            "default": 30,
        },
        "max_items": {
            "type": "integer",
            "description": (
                "Maximum number of raw items to fetch from Apify. "
                "Defaults to APIFY_MAX_ITEMS from env."
            ),
        },
    },
    required=["keywords"],
)

RANK_WINNING_ADS = _fn(
    name="cwt_rank_winning_ads",
    description=ads.rank_winning_ads.__doc__,
    properties={
        "top_n": {
            "type": "integer",
            "description": (
                "Keep only the top N ranked ads in the artifact. "
                "Omit to keep all scored ads."
            ),
        },
    },
    required=[],
)

# ---------------------------------------------------------------------------
# 2. Patterns group
# ---------------------------------------------------------------------------

EXTRACT_AD_PATTERNS = _fn(
    name="cwt_extract_ad_patterns",
    description=patterns.extract_ad_patterns.__doc__,
    properties={
        "concurrency": {
            "type": "integer",
            "description": (
                "Number of parallel LLM calls. Defaults to LLM_MAX_CONCURRENCY from env. "
                "Reduce if you hit rate-limit errors."
            ),
        },
    },
    required=[],
)

# ---------------------------------------------------------------------------
# 3. Research group
# ---------------------------------------------------------------------------

RESEARCH_ANGLE = _fn(
    name="cwt_research_angle",
    description=research.research_angle.__doc__,
    properties={
        "angle": {
            "type": "string",
            "enum": ["pain", "unique_data", "crowd_effect"],
            "description": (
                "Which of the three research angles to run. "
                "Each card runs exactly one angle; run all three concurrently."
            ),
        },
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Override the default search queries for this angle. "
                "Omit to use the built-in query set for the angle."
            ),
        },
    },
    required=["angle"],
)

ASSEMBLE_BRIEF = _fn(
    name="cwt_assemble_brief",
    description=research.assemble_brief.__doc__,
    properties={},
    required=[],
)

# ---------------------------------------------------------------------------
# 4. Storyboard group
# ---------------------------------------------------------------------------

GENERATE_HOOKS = _fn(
    name="cwt_generate_hook_candidates",
    description=storyboard.generate_hook_candidates.__doc__,
    properties={},
    required=[],
)

WRITE_STORYBOARD = _fn(
    name="cwt_write_storyboard_variant",
    description=storyboard.write_storyboard_variant.__doc__,
    properties={
        "angle": {
            "type": "string",
            "enum": ["pain", "unique_data", "crowd_effect"],
            "description": (
                "Research angle this variant is written for. "
                "Call exactly once per angle with the SAME hook_id."
            ),
        },
        "hook_id": {
            "type": "string",
            "description": (
                "ID of the selected hook candidate from hook_candidates.json "
                "(e.g. 'h01'). Must exist in the artifact — "
                "do not invent a hook id."
            ),
        },
        "total_duration_s": {
            "type": "number",
            "description": (
                "Target total video duration in seconds. "
                "Must be within [VIDEO_MIN_SECONDS, VIDEO_MAX_SECONDS]. "
                "Defaults to 40.0 s."
            ),
            "default": 40.0,
        },
    },
    required=["angle", "hook_id"],
)

JUDGE_VARIANTS = _fn(
    name="cwt_judge_variants",
    description=storyboard.judge_variants.__doc__,
    properties={},
    required=[],
)

SCORE_STORYBOARD = _fn(
    name="cwt_score_storyboard",
    description=storyboard.score_storyboard.__doc__,
    properties={},
    required=[],
)

APPLY_REWRITE = _fn(
    name="cwt_apply_rewrite",
    description=storyboard.apply_rewrite.__doc__,
    properties={
        "verdict_path": {
            "type": "string",
            "description": (
                "Path to a review_verdict.json to apply. "
                "Omit to read artifacts/review_verdict.json. "
                "Do not guess paths — §7.2 _ctx() resolves them from CWT_RUN_DIR."
            ),
        },
        "splice_list": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_angle": {"type": "string"},
                    "shot_id": {"type": "string"},
                    "position": {"type": "integer"},
                },
                "required": ["from_angle", "shot_id"],
                "additionalProperties": False,
            },
            "description": (
                "Explicit splice list from judge_variants. "
                "Pass either verdict_path or splice_list, not both."
            ),
        },
    },
    required=[],
)

# ---------------------------------------------------------------------------
# 5. Claims group
# ---------------------------------------------------------------------------

CHECK_CLAIMS = _fn(
    name="cwt_check_claims",
    description=claims.check_claims.__doc__,
    properties={
        "stage": {
            "type": "string",
            "enum": ["pre_render", "post_render"],
            "description": (
                "'pre_render' — reads storyboard.json for spoken and on-screen text. "
                "'post_render' — scans the supplied transcript (or voiceover.json)."
            ),
        },
        "transcript": {
            "type": "string",
            "description": (
                "Word-timed VO transcript for post_render stage. "
                "Omit for pre_render — the tool reads the storyboard automatically."
            ),
        },
    },
    required=["stage"],
)

REWRITE_COMPLIANCE = _fn(
    name="cwt_rewrite_for_compliance",
    description=claims.rewrite_for_compliance.__doc__,
    properties={
        "round_no": {
            "type": "integer",
            "description": (
                "Explicit round number (1-indexed). "
                "Omit to auto-increment from the current storyboard state."
            ),
        },
    },
    required=[],
)

# ---------------------------------------------------------------------------
# 6. Video group
# ---------------------------------------------------------------------------

SYNTHESIZE_VO = _fn(
    name="cwt_synthesize_voiceover",
    description=video.synthesize_voiceover.__doc__,
    properties={},
    required=[],
)

RENDER_VIDEO = _fn(
    name="cwt_render_video",
    description=video.render_video.__doc__,
    properties={
        "backend": {
            "type": "string",
            "description": (
                "Force a specific backend (e.g. 'local_ffmpeg'). "
                "Omit to let the chain try each backend in VIDEO_BACKEND_CHAIN order."
            ),
        },
    },
    required=[],
)

PROBE_MEDIA = _fn(
    name="cwt_probe_media",
    description=video.probe_media.__doc__,
    properties={
        "path": {
            "type": "string",
            "description": (
                "Path to the media file to probe. "
                "Omit to probe the most recent render (from render_manifest.json "
                "or render/final.mp4). Do not guess paths."
            ),
        },
    },
    required=[],
)

RENDER_SB_HTML = _fn(
    name="cwt_render_storyboard_html",
    description=storyboard.render_storyboard_html.__doc__,
    properties={
        "storyboard_path": {
            "type": "string",
            "description": (
                "Path to an alternative storyboard.json. "
                "Omit to render artifacts/storyboard.json."
            ),
        },
    },
    required=[],
)

MAKE_CONTACT_SHEET = _fn(
    name="cwt_make_contact_sheet",
    description=storyboard.make_contact_sheet.__doc__,
    properties={
        "cols": {
            "type": "integer",
            "description": "Number of columns in the grid. Defaults to 4.",
            "default": 4,
        },
        "rows": {
            "type": "integer",
            "description": "Number of rows in the grid. Defaults to 3.",
            "default": 3,
        },
    },
    required=[],
)

# ---------------------------------------------------------------------------
# 7. Bundle group
# ---------------------------------------------------------------------------

VERIFY_ARTIFACT = _fn(
    name="cwt_verify_artifact",
    description=bundle.verify_artifact.__doc__,
    properties={
        "name": {
            "type": "string",
            # Generated from ARTIFACT_NAMES so it cannot drift (spec story §Decisions)
            "enum": list(ARTIFACT_NAMES),
            "description": (
                "Name of the artifact to validate. "
                "Must be one of the 8 ARTIFACT_NAMES defined in domain.artifacts. "
                "Returns {\"ok\": false} for unknown names rather than raising."
            ),
        },
    },
    required=["name"],
)

ASSEMBLE_SUBMISSION = _fn(
    name="cwt_assemble_submission",
    description=bundle.assemble_submission.__doc__,
    properties={},
    required=[],
)

# ---------------------------------------------------------------------------
# ALL_SCHEMAS — registration order matches §7.2 lines 1818–1838
# ---------------------------------------------------------------------------

ALL_SCHEMAS: tuple[dict, ...] = (
    SOURCE_WINNING_ADS,
    RANK_WINNING_ADS,
    EXTRACT_AD_PATTERNS,
    RESEARCH_ANGLE,
    ASSEMBLE_BRIEF,
    GENERATE_HOOKS,
    WRITE_STORYBOARD,
    JUDGE_VARIANTS,
    SCORE_STORYBOARD,
    APPLY_REWRITE,
    CHECK_CLAIMS,
    REWRITE_COMPLIANCE,
    SYNTHESIZE_VO,
    RENDER_VIDEO,
    PROBE_MEDIA,
    RENDER_SB_HTML,
    MAKE_CONTACT_SHEET,
    VERIFY_ARTIFACT,
    ASSEMBLE_SUBMISSION,
)
