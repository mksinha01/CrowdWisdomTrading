"""CWT plugin — registers our tool surface with Hermes.

CRITICAL: handlers must NEVER raise. Always return a JSON string, success or error.
A raising handler takes down the agent turn, not just the tool call.
"""
import json
import logging

from . import schemas
from . import handlers as h

logger = logging.getLogger("cwt.plugin")


def _after_call(tool_name, args, result, task_id, duration_ms, **kwargs):
    """post_tool_call hook. Observational only — the return value is ignored."""
    logger.info("cwt tool=%s task=%s ms=%s ok=%s", tool_name, task_id, duration_ms,
                not str(result).startswith('{"error"'))


def _on_task_completed(task_id, summary=None, metadata=None, **kwargs):
    logger.info("kanban task %s completed: %s", task_id, (summary or "")[:120])


def register(ctx):
    """Called exactly once at startup. If this raises, the plugin is disabled
    but Hermes continues running — which is the behaviour we want."""
    logger.info("registering CWT plugin (profile=%s)", ctx.profile_name)

    _TOOLS = (
        ("cwt_source_winning_ads",     schemas.SOURCE_WINNING_ADS,     h.source_winning_ads),
        ("cwt_rank_winning_ads",       schemas.RANK_WINNING_ADS,       h.rank_winning_ads),
        ("cwt_extract_ad_patterns",    schemas.EXTRACT_AD_PATTERNS,    h.extract_ad_patterns),
        ("cwt_research_angle",         schemas.RESEARCH_ANGLE,         h.research_angle),
        ("cwt_assemble_brief",         schemas.ASSEMBLE_BRIEF,         h.assemble_brief),
        ("cwt_generate_hook_candidates", schemas.GENERATE_HOOKS,       h.generate_hook_candidates),
        ("cwt_write_storyboard_variant", schemas.WRITE_STORYBOARD,     h.write_storyboard_variant),
        ("cwt_judge_variants",         schemas.JUDGE_VARIANTS,         h.judge_variants),
        ("cwt_score_storyboard",       schemas.SCORE_STORYBOARD,       h.score_storyboard),
        ("cwt_apply_rewrite",          schemas.APPLY_REWRITE,          h.apply_rewrite),
        ("cwt_check_claims",           schemas.CHECK_CLAIMS,           h.check_claims),
        ("cwt_rewrite_for_compliance", schemas.REWRITE_COMPLIANCE,     h.rewrite_for_compliance),
        ("cwt_synthesize_voiceover",   schemas.SYNTHESIZE_VO,          h.synthesize_voiceover),
        ("cwt_render_video",           schemas.RENDER_VIDEO,           h.render_video),
        ("cwt_probe_media",            schemas.PROBE_MEDIA,            h.probe_media),
        ("cwt_render_storyboard_html", schemas.RENDER_SB_HTML,         h.render_storyboard_html),
        ("cwt_make_contact_sheet",     schemas.MAKE_CONTACT_SHEET,     h.make_contact_sheet),
        ("cwt_verify_artifact",        schemas.VERIFY_ARTIFACT,        h.verify_artifact),
        ("cwt_assemble_submission",    schemas.ASSEMBLE_SUBMISSION,    h.assemble_submission),
    )

    for name, schema, handler in _TOOLS:
        ctx.register_tool(name=name, toolset="cwt", schema=schema, handler=handler)

    ctx.register_hook("post_tool_call", _after_call)
    ctx.register_hook("kanban_task_completed", _on_task_completed)

    # Register our skills so the profiles can force-load them by name.
    for skill in ("cwt-source-winning-ads", "cwt-extract-ad-patterns", "cwt-research-angle",
                  "cwt-write-storyboard", "cwt-creative-review", "cwt-claims-gate",
                  "cwt-render-video", "cwt-final-qa"):
        ctx.register_skill(skill, f"skills/{skill}/SKILL.md")

    logger.info("CWT plugin registered: %d tools, 2 hooks, 8 skills", len(_TOOLS))
