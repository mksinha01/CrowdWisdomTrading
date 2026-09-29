"""Submission bundle tools.

Story S26 — Tool surface — bundle.
Spec doc/video-ads-agent.md §4 (tool table lines 4080-4081), §18 step 15 (lines 5444-5451),
collect card (lines 3540-3551), root acceptance criteria (lines 3395-3401).

Two tools that close the loop:
  - ``verify_artifact``  — schema-validate one named artifact; never raises.
  - ``assemble_submission`` — build submission/ from the run's artifacts.

Internal helpers:
  - ``_cost_report``       — aggregate llm_ledger.jsonl into per-stage spend.
  - ``_readme_submission`` — generate README-SUBMISSION.md.

Rule R1 — the submission/ directory MUST be gitignored so the two tokens
           written into README-SUBMISSION.md can never be committed.
Rule A1 / §5 — verify_artifact must return a JSON-serialisable dict and
               must NEVER raise; an invalid artifact → {"ok": false, "error": ...}.
§3.0 rule 4  — no API token enters an artifact; _cost_report reads
               llm_ledger.jsonl (stage/model/cost only — safe).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from cwt.config import Settings
from cwt.domain.artifacts import ARTIFACT_MODELS, ARTIFACT_NAMES
from cwt.util.jsonio import now_iso, read_json
from cwt.util.paths import RunPaths

# ── Token allow-list (Rule R1) ─────────────────────────────────────────────
# Only these two token names may appear in README-SUBMISSION.md.
# Any other known secret pattern in the generated README raises immediately.
_README_ALLOWED_TOKEN_NAMES = frozenset({"APIFY_TOKEN", "TAVILY_API_KEY"})

# Patterns for keys that must NOT appear in the README (hard-coded).
_FORBIDDEN_README_PATTERNS = (
    "OPENROUTER_API_KEY",
    "NVIDIA_API_KEY",
    "EXA_API_KEY",
    "sk-or-v1-",
    "nvapi-",
    "xxxxxxxx-xxxx-xxxx-xxxx-",  # raw EXA key shape
)

# §16.2 worked-example cost band: ~$0.24–$0.70 per run.
_COST_BAND_LOW_USD = 0.10   # below this is suspicious (likely incomplete run)
_COST_BAND_HIGH_USD = 2.00  # above this warrants a warning

# Files that are REQUIRED in the bundle (missing → complete=False).
_REQUIRED_FILES = ("final.mp4", "storyboard.json")
# Files that are OPTIONAL (missing is noted but does not affect complete flag).
_OPTIONAL_FILES = ("contact_sheet.png", "cost_report.json")
# All expected bundle files (in display order).
_BUNDLE_FILES = (
    "final.mp4",
    "storyboard.json",
    "storyboard.html",
    "contact_sheet.png",
    "render_manifest.json",
    "claims_report.json",
    "cost_report.json",
    "README-SUBMISSION.md",
)


# ── public: verify_artifact ────────────────────────────────────────────────

def verify_artifact(*, settings: Settings, paths: RunPaths, name: str) -> dict:
    """Schema-validate one artifact. Returns {"ok": true} on success (spec line 4080).

    CALL THIS: to self-check an artifact before moving to the next pipeline stage.

    WHEN NOT TO CALL: do not call on arbitrary or temporary scratch files not in ARTIFACT_NAMES. Do not call before the target artifact file has been written to disk.

    WRITES nothing.
    RETURNS {"ok":bool,"name":str,"schema_version":int|None,"path":str,"error":str|None}

    This tool is called by agents to self-check an artifact before the next stage.
    It must never raise — a crash here kills the agent turn (Rule A1 / §5).
    An unknown ``name`` returns ok=False listing the valid artifact names.
    """
    valid_names = list(ARTIFACT_NAMES)
    if name not in ARTIFACT_MODELS:
        return {
            "ok": False,
            "name": name,
            "schema_version": None,
            "path": "",
            "error": (
                f"Unknown artifact name '{name}'. "
                f"Valid names: {valid_names}"
            ),
        }

    artifact_path = paths.artifacts / f"{name}.json"
    raw: Any = None  # initialise so the except block can reference it

    try:
        if not artifact_path.is_file():
            return {
                "ok": False,
                "name": name,
                "schema_version": None,
                "path": str(artifact_path),
                "error": f"Artifact file does not exist: {artifact_path}",
            }

        raw = read_json(artifact_path)
        if not isinstance(raw, dict):
            return {
                "ok": False,
                "name": name,
                "schema_version": None,
                "path": str(artifact_path),
                "error": "Artifact is not a JSON object.",
            }

        schema_version = raw.get("schema_version")
        model_cls = ARTIFACT_MODELS[name]
        model_cls.model_validate(raw)

        return {
            "ok": True,
            "name": name,
            "schema_version": schema_version,
            "path": str(artifact_path),
            "error": None,
        }

    except Exception as exc:  # noqa: BLE001
        # Rule A1: never propagate — return ok=False with a truncated message.
        return {
            "ok": False,
            "name": name,
            "schema_version": raw.get("schema_version") if isinstance(raw, dict) else None,
            "path": str(artifact_path),
            "error": str(exc)[:400],
        }


# ── internal: _cost_report ─────────────────────────────────────────────────

def _cost_report(paths: RunPaths) -> dict:
    """Aggregate llm_ledger.jsonl into per-stage and per-service spend.

    The ledger is written by S09's LLM client _record() helper.
    Each line is a JSON object with at least:
      {"stage": str, "model": str, "tier": "cheap"|"strong", "cost_usd": float}

    External services block:
    - apify_usd: winning_ads.source.actual_charge_usd
    - tavily_credits: 2 x number of advanced searches (spec line 252)
    - exa_searches: count of cached Exa fixtures (HTTP cache)

    Tavily and Exa free tiers are $0.00 (§16.1). Claiming otherwise misreports.

    §16.2 cost band cross-check: warns if total is outside [$0.10, $2.00].
    §3.0 rule 4: the ledger contains stage/model/cost rows only — safe.
    """
    ledger_path = paths.ledger
    records: list[dict] = []
    if ledger_path.is_file():
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    by_stage: dict[str, float] = {}
    by_tier: dict[str, dict[str, Any]] = {
        "cheap": {"usd": 0.0, "calls": 0},
        "strong": {"usd": 0.0, "calls": 0},
    }
    by_model: dict[str, float] = {}
    total_usd = 0.0
    calls = 0
    run_id: str | None = None

    for rec in records:
        if not isinstance(rec, dict):
            continue
        stage = str(rec.get("stage", "unknown"))
        model = str(rec.get("model", "unknown"))
        tier = str(rec.get("tier", "cheap"))
        cost = float(rec.get("cost_usd", 0.0))
        if run_id is None:
            run_id = rec.get("run_id")

        by_stage[stage] = by_stage.get(stage, 0.0) + cost
        if tier in by_tier:
            by_tier[tier]["usd"] += cost
            by_tier[tier]["calls"] += 1
        by_model[model] = by_model.get(model, 0.0) + cost
        total_usd += cost
        calls += 1

    # ── External services block ──────────────────────────────────────────
    apify_usd = 0.0
    winning_ads_path = paths.artifacts / "winning_ads.json"
    if winning_ads_path.is_file():
        try:
            wa = read_json(winning_ads_path)
            apify_usd = float(
                wa.get("source", {}).get("actual_charge_usd", 0.0)
            )
        except Exception:  # noqa: BLE001
            pass

    # Tavily advanced searches: each costs 2 credits (spec line 252).
    # Count from research_brief if available.
    tavily_advanced_searches = 0
    research_path = paths.artifacts / "research_brief.json"
    if research_path.is_file():
        try:
            rb = read_json(research_path)
            for angle_data in (rb.get("angles") or {}).values():
                if isinstance(angle_data, dict):
                    queries = angle_data.get("search_queries_used") or []
                    provider = angle_data.get("provider", "")
                    if provider == "tavily" or not provider:
                        tavily_advanced_searches += len(queries)
        except Exception:  # noqa: BLE001
            pass

    tavily_credits = tavily_advanced_searches * 2

    # Exa: count cached fixture files as a proxy for searches made.
    exa_searches = 0
    cache_dir = paths.cache
    if cache_dir.is_dir():
        exa_searches = sum(
            1
            for f in cache_dir.rglob("*.json")
            if "exa" in f.name.lower() or "exa" in str(f.parent).lower()
        )

    external = {
        "apify": {"usd": round(apify_usd, 4)},
        "tavily": {
            "free_tier": True,
            "usd": 0.00,
            "credits_used": tavily_credits,
            "advanced_searches": tavily_advanced_searches,
            "note": "§16.1 Tavily free tier — $0.00",
        },
        "exa": {
            "free_tier": True,
            "usd": 0.00,
            "searches": exa_searches,
            "note": "§16.1 Exa $10 free credit — $0.00",
        },
    }

    grand_total_usd = round(total_usd + apify_usd, 4)

    # §16.2 cost band warning
    warning: str | None = None
    if grand_total_usd < _COST_BAND_LOW_USD:
        warning = (
            f"Total spend ${grand_total_usd:.4f} is below the expected band "
            f"(${_COST_BAND_LOW_USD:.2f}–${_COST_BAND_HIGH_USD:.2f}). "
            "The ledger may be incomplete."
        )
    elif grand_total_usd > _COST_BAND_HIGH_USD:
        warning = (
            f"Total spend ${grand_total_usd:.4f} exceeds the expected band "
            f"(${_COST_BAND_LOW_USD:.2f}–${_COST_BAND_HIGH_USD:.2f}). "
            "A failed-and-retried run costs more — verify the ledger."
        )

    return {
        "run_id": run_id,
        "by_stage": {k: round(v, 6) for k, v in by_stage.items()},
        "by_tier": {
            k: {"usd": round(v["usd"], 6), "calls": v["calls"]}
            for k, v in by_tier.items()
        },
        "by_model": {k: round(v, 6) for k, v in by_model.items()},
        "total_usd": round(total_usd, 6),
        "calls": calls,
        "external": external,
        "grand_total_usd": grand_total_usd,
        "warning": warning,
    }


# ── internal: _readme_submission ───────────────────────────────────────────

def _readme_submission(
    *,
    settings: Settings,
    paths: RunPaths,
    cost: dict,
    manifest: dict,
) -> str:
    """Generate README-SUBMISSION.md content.

    Contains, in order (story build step 3):
    1. Repo link
    2. The two allowed tokens (Apify, Tavily) with a note on Apify free tier
    3. The exact command that produced this bundle + offline flag
    4. Recording recipe pointer
    5. Five-step rerun block
    6. Note that offline run needs no keys at all

    Rule R1: must NOT contain OpenRouter/NVIDIA/Exa keys or any other credential.
    The token allowlist is hard-coded; raise if any forbidden pattern appears.
    """
    run_id = paths.run_dir.name
    generated_at = now_iso()
    offline_flag = manifest.get("offline", False)
    command_used = manifest.get("command", "cwt run --engine local --offline")
    total_usd = cost.get("grand_total_usd", 0.0)

    apify_token = settings.apify_token
    tavily_key = settings.tavily_api_key

    lines = [
        "# CWT Video Ads Agent — Submission Package",
        "",
        f"> Generated: {generated_at}  ",
        f"> Run ID: `{run_id}`  ",
        f"> Offline mode: `{offline_flag}`  ",
        "",
        "---",
        "",
        "## 1. Repository",
        "",
        "The full source code is at the public GitHub repository:",
        "",
        "```",
        "https://github.com/mksinha01/CrowdWisdomTrading",
        "```",
        "",
        "> Replace the URL above with your actual repo link before emailing.",
        "",
        "---",
        "",
        "## 2. API Tokens",
        "",
        "These tokens let the reviewer rerun the code against live APIs.",
        "The brief (§18 step 16) explicitly requires them.",
        "",
        "### Apify Token",
        "",
        "```",
        f"APIFY_TOKEN={apify_token}",
        "```",
        "",
        "> Apify free tier: $5 / month, expires at the end of each billing cycle.",
        "> Credits reset monthly. At ~$3.40-$5.80 per 1,000 ads the pipeline uses roughly",
        "> $0.20-$0.40 per run with the default APIFY_MAX_ITEMS=60 cap.",
        "",
        "### Tavily API Key",
        "",
        "```",
        f"TAVILY_API_KEY={tavily_key}",
        "```",
        "",
        "> Tavily Researcher plan: 1,000 credits / month. search_depth=advanced costs",
        f"> 2 credits per call. This run used approximately"
        f" {cost.get('external', {}).get('tavily', {}).get('credits_used', 0)} credits.",
        "",
        "---",
        "",
        "## 3. Command That Produced This Bundle",
        "",
        "```bash",
        command_used,
        "```",
        "",
        f"Offline mode: `{offline_flag}`",
        "",
        "---",
        "",
        "## 4. Recording Recipe",
        "",
        "The kanban video shows the agents working on the Hermes dashboard.",
        "To reproduce it, see:",
        "",
        "```",
        "scripts/record_kanban_video.ps1",
        "```",
        "",
        "Run the script to print the recipe, then start your screen recorder and execute:",
        "",
        "```powershell",
        ".\\scripts\\record_kanban_video.ps1",
        "cwt run --offline --record-pacing",
        "```",
        "",
        "Capture 5-10 minutes of the board, then speed up 4-8x in post.",
        "",
        "---",
        "",
        "## 5. How to Rerun in One Command",
        "",
        "### Prerequisites",
        "",
        "| Step | Command | Notes |",
        "|---|---|---|",
        "| 1 | `.\\scripts\\bootstrap.ps1` | Installs Python 3.11, ffmpeg, Hermes, node 22 |",
        "| 2 | Fill `.env` | Copy `.env.example`, paste the tokens above |",
        "| 3 | `hermes model` | Verify the Hermes model is responsive |",
        "| 4 | `cwt bootstrap` | Preflight: checks binaries, APIs, model slugs |",
        "| 5 | `cwt run --engine local --offline` | Renders the bundled fixture ad — no live keys needed |",
        "",
        "### Offline run (no keys required)",
        "",
        "```bash",
        "cwt run --engine local --offline",
        "```",
        "",
        "The --offline flag replays recorded HTTP fixtures from fixtures/http/.",
        "It needs no API keys at all. The output is a rendered video of the",
        "bundled fixture storyboard — not a freshly researched one, but a valid",
        "video that proves the render chain works end-to-end.",
        "",
        "> §13: the offline run must work before a live one. It is the contract with",
        "> the reviewer and the fastest debugging loop.",
        "",
        "---",
        "",
        "## 6. Cost Summary",
        "",
        "| Service | Spend |",
        "| --- | --- |",
        f"| LLM (OpenRouter / NVIDIA) | ${cost.get('total_usd', 0.0):.4f} |",
        f"| Apify (Meta Ads Library) | ${cost.get('external', {}).get('apify', {}).get('usd', 0.0):.4f} |",
        "| Tavily | $0.00 (free tier) |",
        "| Exa | $0.00 (free tier) |",
        f"| **Total** | **${total_usd:.4f}** |",
        "",
    ]

    if cost.get("warning"):
        lines.extend([
            f"> Cost warning: {cost['warning']}",
            "",
        ])

    content = "\n".join(lines)

    # ── Rule R1: hard-coded token allowlist check ──────────────────────────
    for pattern in _FORBIDDEN_README_PATTERNS:
        if pattern in content:
            raise ValueError(
                f"Rule R1 violation: forbidden secret pattern '{pattern}' "
                "found in README-SUBMISSION.md content. "
                "Only APIFY_TOKEN and TAVILY_API_KEY are permitted."
            )

    return content


# ── public: assemble_submission ────────────────────────────────────────────

def assemble_submission(*, settings: Settings, paths: RunPaths) -> dict:
    """Build submission/.

    CALL THIS: last, on the ``collect`` card, after QA passed.
    It is the final step of the run.

    WHEN NOT TO CALL: never before QA. A bundle containing a video that failed
    the loudness or disclosure check is worse than no bundle — it looks finished.

    WRITES submission/** (see the file list in the story spec).
    RETURNS {"dir":str,"files":[...],"total_bytes":int,"missing":[...],"complete":bool}

    Files assembled (story S26 table):
    - final.mp4            from render/final.mp4
    - storyboard.json      from artifacts/storyboard.json
    - storyboard.html      from artifacts/storyboard.html (regenerated if stale)
    - contact_sheet.png    from artifacts/contact_sheet.png (generated if absent)
    - render_manifest.json from artifacts/render_manifest.json
    - claims_report.json   from artifacts/claims_report*.json
    - cost_report.json     derived from llm_ledger.jsonl
    - README-SUBMISSION.md generated (contains the two API tokens)

    Rule R1: submission/ is gitignored — the token file must never be committed.
    §13: records which mode produced the bundle (offline: true|false) and the exact command.
    """
    submission_dir = paths.run_dir / "submission"
    submission_dir.mkdir(parents=True, exist_ok=True)

    missing: list[str] = []
    copied_files: list[str] = []

    # ── Determine offline mode and command ─────────────────────────────────
    # Read from render manifest if available; default to False.
    offline = False
    command_used = "cwt run --engine local --offline"
    render_manifest_path = paths.artifacts / "render_manifest.json"
    if render_manifest_path.is_file():
        try:
            rm = read_json(render_manifest_path)
            if isinstance(rm, dict):
                offline = bool(rm.get("offline", False))
                if rm.get("command"):
                    command_used = str(rm["command"])
        except Exception:  # noqa: BLE001
            pass

    bundle_manifest = {"offline": offline, "command": command_used}

    # ── 1. final.mp4 ──────────────────────────────────────────────────────
    final_mp4_src = paths.render / "final.mp4"
    if final_mp4_src.is_file():
        dst = submission_dir / "final.mp4"
        shutil.copy2(final_mp4_src, dst)
        copied_files.append(str(dst))
    else:
        missing.append("final.mp4")

    # ── 2. storyboard.json ────────────────────────────────────────────────
    sb_json_src = paths.artifacts / "storyboard.json"
    if sb_json_src.is_file():
        dst = submission_dir / "storyboard.json"
        shutil.copy2(sb_json_src, dst)
        copied_files.append(str(dst))
    else:
        missing.append("storyboard.json")

    # ── 3. storyboard.html — regenerate if stale ──────────────────────────
    # The compliance card overwrites storyboard.json in place (S24), so a
    # cached HTML can describe a script that no longer exists. This is the
    # most likely quiet bug in the bundle.
    sb_html_src = paths.artifacts / "storyboard.html"
    _regenerate_storyboard_html_if_stale(
        sb_json_src=sb_json_src,
        sb_html_src=sb_html_src,
        settings=settings,
        paths=paths,
    )
    if sb_html_src.is_file():
        dst = submission_dir / "storyboard.html"
        shutil.copy2(sb_html_src, dst)
        copied_files.append(str(dst))
    else:
        missing.append("storyboard.html")

    # ── 4. contact_sheet.png — generate if absent ─────────────────────────
    # Cheap, offline (Pillow only). Its absence from the bundle is a visible gap.
    contact_sheet_src = paths.artifacts / "contact_sheet.png"
    if not contact_sheet_src.is_file() and sb_json_src.is_file():
        _try_generate_contact_sheet(
            settings=settings,
            paths=paths,
            out_path=contact_sheet_src,
        )
    if contact_sheet_src.is_file():
        dst = submission_dir / "contact_sheet.png"
        shutil.copy2(contact_sheet_src, dst)
        copied_files.append(str(dst))
    # contact_sheet.png is optional — missing is noted but does not fail 'complete'

    # ── 5. render_manifest.json ───────────────────────────────────────────
    if render_manifest_path.is_file():
        dst = submission_dir / "render_manifest.json"
        shutil.copy2(render_manifest_path, dst)
        copied_files.append(str(dst))
    else:
        missing.append("render_manifest.json")

    # ── 6. claims_report.json — accept claims_report*.json pattern ────────
    claims_src = _find_claims_report(paths)
    if claims_src is not None:
        dst = submission_dir / "claims_report.json"
        shutil.copy2(claims_src, dst)
        copied_files.append(str(dst))
    else:
        missing.append("claims_report.json")

    # ── 7. cost_report.json — derived from llm_ledger.jsonl ───────────────
    cost = _cost_report(paths)
    cost_report_dst = submission_dir / "cost_report.json"
    _write_json_utf8lf(cost_report_dst, cost)
    copied_files.append(str(cost_report_dst))

    # ── 8. README-SUBMISSION.md — generated ───────────────────────────────
    readme_content = _readme_submission(
        settings=settings,
        paths=paths,
        cost=cost,
        manifest=bundle_manifest,
    )
    readme_dst = submission_dir / "README-SUBMISSION.md"
    readme_dst.write_text(readme_content, encoding="utf-8", newline="\n")
    copied_files.append(str(readme_dst))

    # ── Compute complete flag ─────────────────────────────────────────────
    # Only required files drive the complete flag.
    required_missing = [m for m in missing if m in _REQUIRED_FILES]
    complete = len(required_missing) == 0

    total_bytes = sum(
        Path(f).stat().st_size for f in copied_files if Path(f).is_file()
    )

    return {
        "dir": str(submission_dir),
        "files": copied_files,
        "total_bytes": total_bytes,
        "missing": missing,
        "complete": complete,
        "offline": offline,
        "command": command_used,
    }


# ── private helpers ────────────────────────────────────────────────────────

def _find_claims_report(paths: RunPaths) -> Path | None:
    """Return the first claims_report*.json in artifacts/, or None."""
    # Exact match first
    exact = paths.artifacts / "claims_report.json"
    if exact.is_file():
        return exact
    # Glob fallback (claims_report_round2.json etc.)
    candidates = sorted(paths.artifacts.glob("claims_report*.json"))
    return candidates[0] if candidates else None


def _write_json_utf8lf(path: Path, data: Any) -> None:
    """Write JSON with utf-8 and LF line endings (§3.0 rule 3)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")


def _regenerate_storyboard_html_if_stale(
    *,
    sb_json_src: Path,
    sb_html_src: Path,
    settings: Settings,
    paths: RunPaths,
) -> None:
    """Regenerate storyboard.html if storyboard.json is newer than the HTML.

    The compliance card overwrites storyboard.json in place (S24), so a cached
    HTML can describe a script that no longer exists. This is the most likely
    quiet bug in the bundle.
    """
    if not sb_json_src.is_file():
        return

    should_regenerate = (
        not sb_html_src.is_file()
        or sb_json_src.stat().st_mtime > sb_html_src.stat().st_mtime
    )

    if should_regenerate:
        try:
            from cwt.tools.storyboard import render_storyboard_html

            render_storyboard_html(
                settings=settings,
                paths=paths,
                storyboard_path=str(sb_json_src),
            )
        except Exception:  # noqa: BLE001
            # Non-fatal: the file will be absent and noted in missing[].
            pass


def _try_generate_contact_sheet(
    *,
    settings: Settings,
    paths: RunPaths,
    out_path: Path,
) -> None:
    """Generate contact_sheet.png via S23's make_contact_sheet if possible.

    Cheap, offline (Pillow only). Never raises — absence is already tracked.
    """
    try:
        from cwt.tools.storyboard import make_contact_sheet

        make_contact_sheet(settings=settings, paths=paths)
    except Exception:  # noqa: BLE001
        pass
