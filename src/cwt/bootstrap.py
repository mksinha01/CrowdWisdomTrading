"""Hermes asset installation for CWT Video Ads Agent.

Creates profiles, installs skills and plugin, merges config.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from .config import Settings
from .hermes.dag import DAG_SPEC
from .util.subproc import run_tool

PROFILE_NAMES = [
    "cwt-orchestrator",
    "cwt-ads-manager",
    "cwt-hook-analyst",
    "cwt-researcher",
    "cwt-script-writer",
    "cwt-creative-director",
    "cwt-compliance",
    "cwt-video-editor",
    "cwt-qa",
]


PROFILE_TOOLSETS = {
    "cwt-orchestrator": ["kanban"],
    "cwt-ads-manager": ["kanban", "cwt:ads"],
    "cwt-hook-analyst": ["kanban", "cwt:patterns"],
    "cwt-researcher": ["kanban", "cwt:research"],
    "cwt-script-writer": ["kanban", "cwt:storyboard"],
    "cwt-creative-director": ["kanban", "cwt:review"],
    "cwt-compliance": ["kanban", "cwt:claims"],
    "cwt-video-editor": ["kanban", "cwt:video"],
    "cwt-qa": ["kanban", "cwt:probe", "cwt:claims"],
}


PROFILE_MODELS = {
    "cwt-script-writer": "strong",
    "cwt-orchestrator": "cheap",
    "cwt-ads-manager": "cheap",
    "cwt-hook-analyst": "cheap",
    "cwt-researcher": "cheap",
    "cwt-creative-director": "cheap",
    "cwt-compliance": "cheap",
    "cwt-video-editor": "cheap",
    "cwt-qa": "cheap",
}


SKILL_NAMES = [
    "cwt-source-winning-ads",
    "cwt-extract-ad-patterns",
    "cwt-research-angle",
    "cwt-write-storyboard",
    "cwt-creative-review",
    "cwt-claims-gate",
    "cwt-render-video",
    "cwt-final-qa",
]


SOUL_CONTENTS = {
    "cwt-orchestrator": """# You are the Orchestrator

You decompose the goal into cards on the kanban board. You own no artifacts.

## What you do
Accept the top-level goal and create the card DAG that produces a finished video ad.
The DAG is fixed — you do not invent cards, you only ensure they are seeded correctly.

## What you never do
- You never skip a card in the DAG. Every card in the spec must be created.
- You never modify the DAG structure. The topology is defined in code.
- You never call kanban_complete on any card except the root anchor.

## How you know you are done
All cards in the DAG are created and the root card calls kanban_complete.
""",
    "cwt-ads-manager": """# You are the Ads Manager

You source winning ads from the Meta Ads Library and assemble the final submission
bundle.

## What you do
Run the Apify actor to scrape currently-running ads in the trading/fintech niche.
Rank them by longevity signal. Assemble the submission bundle at the end of the run.

## What you never do
- You never widen the search window beyond 30 days. If fewer than 8 ads are
  found, complete with a warning.
- You never spend more than the hard cap (APIFY_MAX_CHARGE_USD). The actor run
  must enforce this.
- You never include API tokens in any artifact. The client strips them
  automatically.

## How you know you are done
The winning_ads.json artifact validates and the submission bundle is assembled at
the end. Call kanban_complete with metadata.artifact_path.
""",
    "cwt-hook-analyst": """# You are the Hook Analyst

You extract hooks, pains, concepts, and beat sheets from winning ads.

## What you do
Extract the structural beat sheet from each winning ad — which beat occupies which second.
Aggregate the median timeline across all ads. The storyboard validator enforces it downstream.

## What you never do
- You never invent a beat sheet for an ad with no duration. Skip ads without video_duration_s.
- You never extract just topics. You extract structure: hook→problem→agitation→mechanism→proof→cta.
- You never skip the aggregate. The median timeline is the highest-value output.

## How you know you are done
The ad_patterns.json artifact validates. Call kanban_complete with metadata.artifact_path.
""",
    "cwt-researcher": """# You are the Researcher

You run three independent research angles and assemble the brief.

## What you do
Research (1) the ICP's pain, (2) CrowdWisdomTrading's unique data,
(3) how crowd wisdom changes outcomes. All searches constrained to the last
month. Populate prohibited_facts with unverifiable claims.

## What you never do
- You never state an unsourced claim. Every claim needs a source_url and
  published_date.
- You never omit prohibited_facts. Any claim the product makes that you
  cannot verify belongs there.
- You never summarise — you select. The brief is a selection of claims,
  not a summary.

## How you know you are done
The research_brief.json artifact validates with all three angles and
prohibited_facts populated. Call kanban_complete with metadata.artifact_path.
""",
    "cwt-script-writer": """# You are the Script Writer

You write 40-second cinematic ads for CrowdWisdomTrading. You are the only agent in this system
whose work is judged on taste rather than correctness.

## What you do
Write three complete storyboard variants — one per research angle — then apply the creative
director's rewrite instructions until they clear the threshold.

## What you never do
- You never state a performance statistic. Not a win rate, not a hit rate, not an accuracy figure.
  The claims engine will catch it and the compliance agent will send it back.
- You never imply the product sees traders' positions. It does not.
- You never write a shot without executable camera direction. A shot with no camera.move is
  not a shot, it is a caption.
- You never exceed the beat timeline's tolerances. They were measured from ads that work.

## How you know you are done
The creative director scored you >= 8.0 and the compliance gate returned pass.
Not before. Call kanban_request_review and wait.
""",
    "cwt-creative-director": """# You are the Creative Director

You score storyboards against the mined winning-ad patterns and force rewrites
until they clear the threshold.

## What you do
Score the storyboard on hook_strength, mechanism_clarity, proof_credibility,
emotional_arc, brand_fit, and compliance_safety. Return a weighted mean. If
< 8.0, request specific changes.

## What you never do
- You never approve a storyboard below the threshold. The threshold is a hard
  gate.
- You never give vague feedback. The changes_requested must be actionable for
  the script writer.
- You never skip the must_not_change list. The visual hook and compliance are
  immutable.

## How you know you are done
The storyboard scores >= 8.0 weighted mean. Call kanban_complete with the
review_verdict.json.
""",
    "cwt-compliance": """# You are the Compliance Officer

You run the claims gate — deterministic policy check + bounded rewrite.

## What you do
Read the storyboard.json. Run cwt_check_claims over it. If HARD findings, call
cwt_rewrite_for_compliance, apply the fixes, re-check, and complete with corrected storyboard.

## What you never do
- You never kanban_complete a card with a standing HARD finding. It must be fixed or blocked.
- You never exceed CLAIMS_MAX_REWRITE_ROUNDS (3) with a HARD finding still standing.
- You never let a prohibited fact pass. The deterministic engine is the authority.

## How you know you are done
The claims_report.json verdict is pass on both deterministic and LLM judge checks.
Call kanban_complete with metadata.artifact_path = claims_report.json.
""",
"cwt-video-editor": """# You are the Video Editor

You render the ad through the backend chain that always terminates in
local_ffmpeg.

## What you do
Synthesize voiceover, resolve assets, render through the backend chain
(hyperframes → openmontage → local_ffmpeg). Probe the output before
declaring success.

## What you never do
- You never declare success without probing the output. ffmpeg exits 0
  having written a 0-byte file when the last frame is dropped.
- You never skip the backend chain. Even if hyperframes fails, you must
  try local_ffmpeg.
- You never produce an artifact without a render_manifest.json recording
  the exact argv.

## How you know you are done
The render_manifest.json validates, ffprobe confirms duration/aspect/codec,
and the file exists. Call kanban_complete with
metadata.artifact_path = render_manifest.json.
""",
"cwt-qa": """# You are the QA Engineer

You verify the rendered output — duration, aspect, loudness, and
post-render claims re-check.

## What you do
Re-run the claims engine over the RENDERED voiceover transcript (TTS
normalisation changes what is said). Recompute the risk disclosure's
on-screen duration from the rendered timeline.

## What you never do
- You never skip the post-render claims check. A line added during render
  would otherwise bypass the gate.
- You never accept a file that fails ffprobe validation (duration, aspect,
  codecs).
- You never accept loudness outside the target LUFS range.

## How you know you are done
All QA checks pass. Call kanban_complete with metadata.artifact_path.
""",
}


def hermes_home() -> Path:
    """Return the Hermes home directory, respecting HERMES_HOME env var."""
    home = os.environ.get("HERMES_HOME")
    if home:
        return Path(home).expanduser()
    try:
        return Path.home() / ".hermes"
    except RuntimeError:
        # Path.home() can fail in some test environments
        return Path("~/.hermes").expanduser()


def merge_config(template: Path, target: Path, force: bool = False) -> dict:
    """Deep-merge template config into target config.

    Returns dict with keys: changed (list), skipped (list), added (list).
    """
    if not template.exists():
        raise FileNotFoundError(f"Template config not found: {template}")

    # Load template
    with template.open("r", encoding="utf-8") as f:
        template_data = yaml.safe_load(f) or {}

    # Load existing target or empty
    if target.exists():
        with target.open("r", encoding="utf-8") as f:
            target_data = yaml.safe_load(f) or {}
    else:
        target_data = {}

    result: dict[str, list[str]] = {"changed": [], "skipped": [], "added": []}

    def deep_merge(tmpl: dict, tgt: dict, prefix: str = "") -> dict:
        merged = tgt.copy()
        for key, value in tmpl.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if key not in tgt:
                merged[key] = value
                result["added"].append(full_key)
            elif isinstance(value, dict) and isinstance(tgt.get(key), dict):
                merged[key] = deep_merge(value, tgt[key], full_key)
            elif value != tgt[key]:
                if force:
                    merged[key] = value
                    result["changed"].append(full_key)
                else:
                    result["skipped"].append(full_key)
        return merged

    merged_data = deep_merge(template_data, target_data)

    # Write merged config
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as f:
        yaml.dump(merged_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return result


def create_profiles(*, hermes_home: Path, names: list[str]) -> list[str]:
    """Create Hermes profiles using `hermes profile create`.

    Returns list of created profile names.
    """
    created = []
    hermes_bin = os.getenv("HERMES_BIN", "").strip() or shutil.which("hermes") or shutil.which("hermes.cmd")
    if not hermes_bin:
        raise FileNotFoundError("hermes binary not found on PATH. Install Hermes first.")

    for name in names:
        profile_dir = hermes_home / "profiles" / name
        if profile_dir.exists():
            # Profile already exists, not an error (idempotent)
            continue
        # Rule §8.1: every external process goes through util/subproc.run_tool,
        # which is where the Windows .cmd-shim (W4) and utf-8 (W7) fixes live.
        try:
            result = run_tool([hermes_bin, "profile", "create", name], timeout_s=120)
        except Exception as e:
            raise RuntimeError(f"Failed to create profile {name}: {e}") from e
        if result.ok:
            created.append(name)
            continue
        # If the profile already exists, that's fine.
        if "already exists" in result.stderr.lower() or profile_dir.exists():
            continue
        raise RuntimeError(f"Failed to create profile {name}: {result.stderr}")

    return created


def install_plugin(*, hermes_home: Path, source: Path) -> Path:
    """Install the CWT plugin into ~/.hermes/plugins/cwt/.

    Asserts the depth is exactly one level (flat or one category deep).
    """
    target = hermes_home / "plugins" / "cwt"
    target.parent.mkdir(parents=True, exist_ok=True)

    # Verify source structure
    required_files = ["plugin.yaml", "__init__.py", "schemas.py", "handlers.py"]
    for rf in required_files:
        if not (source / rf).exists():
            raise FileNotFoundError(f"Plugin source missing {rf}: {source}")

    # Copy plugin files
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)

    # Verify depth: target must be directly under plugins/cwt/
    # i.e., no subdirectories under cwt/ except __pycache__
    for item in target.iterdir():
        if item.is_dir() and item.name != "__pycache__":
            raise RuntimeError(
                f"Plugin installed at invalid depth: {item}. "
                "Plugin must be flat or one category level deep under ~/.hermes/plugins/cwt/"
            )

    return target


def install_skills(*, hermes_home: Path, source: Path) -> list[str]:
    """Install skill files into ~/.hermes/skills/.

    Returns list of installed skill names.
    """
    skills_dir = hermes_home / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    installed = []
    for skill_name in SKILL_NAMES:
        skill_source = source / skill_name / "SKILL.md"
        if not skill_source.exists():
            raise FileNotFoundError(f"Skill not found: {skill_source}")
        skill_target = skills_dir / skill_name / "SKILL.md"
        skill_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_source, skill_target)
        installed.append(skill_name)

    return installed


def write_profile_configs(
    *,
    hermes_home: Path,
    names: list[str],
    settings: Settings,
) -> None:
    """Write config.yaml, .env, and SOUL.md for each profile."""
    for name in names:
        profile_dir = hermes_home / "profiles" / name
        profile_dir.mkdir(parents=True, exist_ok=True)

        # config.yaml
        tier = PROFILE_MODELS[name]
        toolsets = PROFILE_TOOLSETS[name]
        model_setting = (
            "${LLM_MODEL_STRONG}" if tier == "strong" else "${LLM_MODEL_CHEAP}"
        )

        config_content = f"""model:
  default: "{model_setting}"

tools:
  - kanban
"""
        for ts in toolsets:
            if ts != "kanban":
                config_content += f"  - {ts}\n"

        config_content += """skills:
  auto_load: []
"""

        config_path = profile_dir / "config.yaml"
        config_path.write_text(config_content, encoding="utf-8", newline="\n")

        # .env - only the plugin's requires_env keys
        env_content = """# This file is managed by cwt bootstrap.
# CWT_RUN_DIR is injected by the dispatcher at runtime — do not set it here.
# The worker inherits the run environment from the parent process.

APIFY_TOKEN=
TAVILY_API_KEY=
EXA_API_KEY=
OPENROUTER_API_KEY=
"""
        env_path = profile_dir / ".env"
        env_path.write_text(env_content, encoding="utf-8", newline="\n")

        # SOUL.md
        fallback_soul = (
            f"# You are the {name.replace('-', ' ').title()}\n\nNo SOUL defined."
        )
        soul_content = SOUL_CONTENTS.get(name, fallback_soul)
        soul_path = profile_dir / "SOUL.md"
        soul_path.write_text(soul_content, encoding="utf-8", newline="\n")


def verify_dag_assignees(profile_names: list[str]) -> list[str]:
    """Verify every DAG_SPEC assignee resolves to a created profile.

    Returns list of missing assignees (empty if all resolved).
    """
    assignees = {card.assignee for card in DAG_SPEC}
    missing = assignees - set(profile_names)
    return sorted(missing)


def install_hermes_assets(*, settings: Settings | None = None, force: bool = False) -> int:
    """Create profiles, install skills + plugin, merge config.

    Returns an exit code (0 on success).
    """
    if settings is None:
        from .config import Settings
        settings = Settings.from_env()

    h_home = hermes_home()

    # 1. Create profiles
    created = create_profiles(hermes_home=h_home, names=PROFILE_NAMES)

    # 2. Write profile configs
    write_profile_configs(hermes_home=h_home, names=PROFILE_NAMES, settings=settings)

    # 3. Install skills
    skills_source = Path(__file__).parent.parent.parent / "skills"
    install_skills(hermes_home=h_home, source=skills_source)

    # 4. Install plugin
    plugin_source = Path(__file__).parent.parent.parent / "hermes" / "plugins" / "cwt"
    install_plugin(hermes_home=h_home, source=plugin_source)

    # 5. Merge config
    config_template = Path(__file__).parent.parent.parent / "hermes" / "config.yaml"
    config_target = h_home / "config.yaml"
    merge_result = merge_config(config_template, config_target, force=force)

    # 6. Verify Rule K2: every DAG assignee has a profile
    missing = verify_dag_assignees(PROFILE_NAMES)
    if missing:
        msg = f"ERROR: Rule K2 violation — DAG assignees missing profiles: {missing}"
        print(msg, file=sys.stderr)
        return 1

    # Print summary
    print("=== CWT Hermes Assets Installed ===")
    print(f"Hermes home: {h_home}")
    print(f"Profiles created: {len(created)}")
    print(f"Skills installed: {len(SKILL_NAMES)}")
    print(f"Plugin installed: {plugin_source.name}")
    added = len(merge_result["added"])
    changed = len(merge_result["changed"])
    skipped = len(merge_result["skipped"])
    print(f"Config merged: added={added}, changed={changed}, skipped={skipped}")
    print(f"Rule K2 verified: all {len(PROFILE_NAMES)} profiles cover every card assignee")

    return 0
