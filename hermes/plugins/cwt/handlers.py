"""CWT plugin handlers — thin adapters from Hermes tool schema to src/cwt/tools.

Rule A1: handlers must NEVER raise. Always return a JSON string, success or error.
A raising handler takes down the agent turn, not just the tool call.

Each handler:
1. Unpacks `args` (required args via `args["x"]`, optional via `args.get("x", default)`)
2. Resolves `settings` and `paths` via `_ctx(args)`
3. Calls the tool function with named arguments matching the tool's signature
4. Returns the tool's dict result as a JSON string

All 19 handlers follow the same pattern.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from cwt.config import Settings
from cwt.util.paths import RunPaths

logger = logging.getLogger("cwt.plugin.handlers")


def _scrub(msg: str) -> str:
    """Scrub Apify token from error messages (Rule R1)."""
    # Apify token format: apify_api_<long_string>
    import re
    return re.sub(r"apify_api_[A-Za-z0-9_-]+", "<redacted>", msg)


def _ctx(args: dict[str, Any]) -> tuple[Settings, RunPaths]:
    """Resolve Settings and RunPaths from environment.

    CWT_RUN_DIR is the run context. If it is unset, raise an actionable message.
    """
    run_dir_env = os.environ.get("CWT_RUN_DIR")
    if not run_dir_env:
        raise RuntimeError(
            "CWT_RUN_DIR is not set. This tool must run inside a CWT-dispatched "
            "kanban worker. Run `cwt run` rather than invoking the worker by hand."
        )

    paths = RunPaths.from_run_dir(Path(run_dir_env))
    settings = Settings.from_env()
    return settings, paths


def _safe(fn):
    """Decorator: never raise, always return a JSON string.

    On success: returns json.dumps(result)
    On exception: returns json.dumps({"ok": false, "error": "<Type>: <message>"})
    Error message is scrubbed of Apify tokens.
    """
    def wrapper(args: dict[str, Any], settings: Settings, paths: RunPaths) -> str:
        try:
            result = fn(args, settings, paths)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 — deliberate: keep the agent turn alive
            scrubbed = _scrub(f"{type(exc).__name__}: {exc}")
            return json.dumps({"ok": False, "error": scrubbed}, ensure_ascii=False)
    return wrapper


# ===========================================================================
# 1. Ads group
# ===========================================================================

from cwt.tools import ads as ads_tools


@_safe
def source_winning_ads(args, settings, paths):
    return ads_tools.source_winning_ads(
        settings=settings,
        paths=paths,
        keywords=args["keywords"],
        countries=args.get("countries", ["US", "GB", "IN"]),
        window_days=args.get("window_days", 30),
        max_items=args.get("max_items"),
    )


@_safe
def rank_winning_ads(args, settings, paths):
    return ads_tools.rank_winning_ads(
        settings=settings,
        paths=paths,
        top_n=args.get("top_n"),
    )


# ===========================================================================
# 2. Patterns group
# ===========================================================================

from cwt.tools import patterns as patterns_tools


@_safe
def extract_ad_patterns(args, settings, paths):
    return patterns_tools.extract_ad_patterns(
        settings=settings,
        paths=paths,
        concurrency=args.get("concurrency"),
    )


# ===========================================================================
# 3. Research group
# ===========================================================================

from cwt.tools import research as research_tools


@_safe
def research_angle(args, settings, paths):
    return research_tools.research_angle(
        settings=settings,
        paths=paths,
        angle=args["angle"],
        queries=args.get("queries"),
    )


@_safe
def assemble_brief(args, settings, paths):
    return research_tools.assemble_brief(
        settings=settings,
        paths=paths,
    )


# ===========================================================================
# 4. Storyboard group
# ===========================================================================

from cwt.tools import storyboard as storyboard_tools


@_safe
def generate_hook_candidates(args, settings, paths):
    return storyboard_tools.generate_hook_candidates(
        settings=settings,
        paths=paths,
    )


@_safe
def write_storyboard_variant(args, settings, paths):
    return storyboard_tools.write_storyboard_variant(
        settings=settings,
        paths=paths,
        angle=args["angle"],
        hook_id=args["hook_id"],
        total_duration_s=args.get("total_duration_s", 42.0),
    )


@_safe
def judge_variants(args, settings, paths):
    return storyboard_tools.judge_variants(
        settings=settings,
        paths=paths,
    )


@_safe
def score_storyboard(args, settings, paths):
    return storyboard_tools.score_storyboard(
        settings=settings,
        paths=paths,
    )


@_safe
def apply_rewrite(args, settings, paths):
    return storyboard_tools.apply_rewrite(
        settings=settings,
        paths=paths,
        verdict_path=args.get("verdict_path"),
        splice_list=args.get("splice_list"),
    )


# ===========================================================================
# 5. Claims group
# ===========================================================================

from cwt.tools import claims as claims_tools


@_safe
def check_claims(args, settings, paths):
    return claims_tools.check_claims(
        settings=settings,
        paths=paths,
        stage=args["stage"],
        transcript=args.get("transcript"),
    )


@_safe
def rewrite_for_compliance(args, settings, paths):
    return claims_tools.rewrite_for_compliance(
        settings=settings,
        paths=paths,
        round_no=args.get("round_no"),
    )


# ===========================================================================
# 6. Video group
# ===========================================================================

from cwt.tools import video as video_tools


@_safe
def synthesize_voiceover(args, settings, paths):
    return video_tools.synthesize_voiceover(
        settings=settings,
        paths=paths,
    )


@_safe
def render_video(args, settings, paths):
    return video_tools.render_video(
        settings=settings,
        paths=paths,
        backend=args.get("backend"),
    )


@_safe
def probe_media(args, settings, paths):
    return video_tools.probe_media(
        settings=settings,
        paths=paths,
        path=args.get("path"),
    )


@_safe
def render_storyboard_html(args, settings, paths):
    return storyboard_tools.render_storyboard_html(
        settings=settings,
        paths=paths,
        storyboard_path=args.get("storyboard_path"),
    )


@_safe
def make_contact_sheet(args, settings, paths):
    return storyboard_tools.make_contact_sheet(
        settings=settings,
        paths=paths,
        cols=args.get("cols", 4),
        rows=args.get("rows", 3),
    )


# ===========================================================================
# 7. Bundle group
# ===========================================================================

from cwt.tools import bundle as bundle_tools


@_safe
def verify_artifact(args, settings, paths):
    return bundle_tools.verify_artifact(
        settings=settings,
        paths=paths,
        name=args["name"],
    )


@_safe
def assemble_submission(args, settings, paths):
    return bundle_tools.assemble_submission(
        settings=settings,
        paths=paths,
    )


# ===========================================================================
# Handler list for programmatic access (tests, registration order)
# ===========================================================================

__all__ = [
    "source_winning_ads",
    "rank_winning_ads",
    "extract_ad_patterns",
    "research_angle",
    "assemble_brief",
    "generate_hook_candidates",
    "write_storyboard_variant",
    "judge_variants",
    "score_storyboard",
    "apply_rewrite",
    "check_claims",
    "rewrite_for_compliance",
    "synthesize_voiceover",
    "render_video",
    "probe_media",
    "render_storyboard_html",
    "make_contact_sheet",
    "verify_artifact",
    "assemble_submission",
]