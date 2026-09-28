"""Financial-advertising claims gate tools (Story S24).

Implements the two-pass claims gate:
1. pre_render: scans the storyboard (SPOKEN and ON-SCREEN text only)
2. post_render: scans the synthesized voiceover transcript

Rules enforced:
- Rule C1: Deterministic first, and its HARD verdicts are final. An LLM judge may escalate
  a severity (soft -> hard), but may NEVER de-escalate a hard finding.
- Rule C2: A hard finding is never overridable. After CLAIMS_MAX_REWRITE_ROUNDS with a hard
  finding standing, the verdict is 'block'.
- Rule C3: The gate runs twice — once before render, once over what was actually rendered.
- §10.2: Excludes shot descriptions and camera fields — those are not claims.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from cwt.clients.llm import BudgetExceeded, LLMClient, Tier
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactStore
from cwt.domain.claims import (
    RULESET_VERSION,
    Finding,
    Severity,
    detect_claims,
    rewrite_instructions,
    scan_prohibited_facts,
)
from cwt.domain.models import (
    DEFAULT_PROHIBITED_FACTS,
    ClaimsReport,
    ReviewVerdict,
    Storyboard,
)
from cwt.prompts.claims_policy import build_claims_judge_prompt
from cwt.util.paths import RunPaths

logger = logging.getLogger(__name__)


# ===========================================================================
# LLM Judge Schemas
# ===========================================================================


class ClaimsJudgeFinding(BaseModel):
    rule_id: str
    severity: Literal["hard", "soft"]
    matched_text: str
    why: str
    fix: str

    @field_validator("matched_text")
    @classmethod
    def _must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("matched_text must be non-empty")
        return v.strip()


class ClaimsJudgeResponse(BaseModel):
    findings: list[ClaimsJudgeFinding] = Field(default_factory=list)
    overall: Literal["pass", "request_changes"] = "pass"


# ===========================================================================
# Helpers & Internals
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


def _collect_script_text(sb: Storyboard) -> dict[str, list[tuple[str, str]]]:
    """field -> [(shot_id, text)] for every SPOKEN and ON-SCREEN string.

    Deliberately EXCLUDES shot.description and camera fields — those are not claims.
    """
    out: dict[str, list[tuple[str, str]]] = {
        "voiceover.full_text": [],
        "voiceover.segments": [],
        "voiceover.segments[].text": [],
        "on_screen_text": [],
        "shots[].on_screen_text[].text": [],
        "visual_hook.text_overlay": [],
    }

    if sb.voiceover:
        if sb.voiceover.full_text:
            out["voiceover.full_text"].append(("", sb.voiceover.full_text))
        if sb.voiceover.segments:
            for seg in sb.voiceover.segments:
                if seg.text:
                    item = (seg.shot_id, seg.text)
                    out["voiceover.segments"].append(item)
                    out["voiceover.segments[].text"].append(item)

    if sb.shots:
        for shot in sb.shots:
            if shot.on_screen_text:
                for ost in shot.on_screen_text:
                    if ost.text:
                        item = (shot.id, ost.text)
                        out["on_screen_text"].append(item)
                        out["shots[].on_screen_text[].text"].append(item)

    if sb.visual_hook and sb.visual_hook.text_overlay:
        out["visual_hook.text_overlay"].append(("visual_hook", sb.visual_hook.text_overlay))

    return out


def _verdict(hard: int, soft: int, rounds_used: int, max_rounds: int) -> str:
    """Compute the claims gate verdict.

    "block": if any HARD finding and rounds_used >= max_rounds
    "request_changes": if any finding (hard or soft) remains
    "pass": no findings remaining
    """
    if hard > 0 and rounds_used >= max_rounds:
        return "block"
    if hard > 0 or soft > 0:
        return "request_changes"
    return "pass"


def _spans_overlap(text1: str, text2: str) -> bool:
    """Check if two text spans overlap or match case-insensitively."""
    t1 = text1.strip().lower()
    t2 = text2.strip().lower()
    return bool(t1 and t2 and (t1 in t2 or t2 in t1))


async def _run_llm_judge(
    settings: Settings,
    paths: RunPaths,
    script: str,
    stage: str,
    client: LLMClient | None = None,
) -> ClaimsJudgeResponse:
    if client is None:
        client = _get_llm_client(settings, paths)
    prompt = build_claims_judge_prompt(script)
    try:
        return await client.complete_validated(
            tier=Tier.CHEAP,
            messages=[{"role": "user", "content": prompt}],
            schema=ClaimsJudgeResponse,
            temperature=0.0,
            max_tokens=2048,
            stage=f"claims_judge_{stage}",
        )
    except BudgetExceeded:
        raise
    except Exception as exc:
        logger.warning("LLM judge failed or timed out: %s", exc)
        return ClaimsJudgeResponse(findings=[], overall="pass")


# ===========================================================================
# Tool Interface Contract — FROZEN
# ===========================================================================


def check_claims(
    *,
    settings: Settings,
    paths: RunPaths,
    stage: str,
    transcript: str | None = None,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    """Run the financial-advertising claims gate over a script.

    CALL THIS: after a storyboard is written, before any render. Also call it again
    after the voiceover is synthesized — TTS normalisation changes what is actually
    said, and a line added during render would otherwise bypass the gate.

    WHEN NOT TO CALL: do not call this on the storyboard's shot descriptions. It
    scans the SPOKEN and ON-SCREEN text only. Descriptions of camera moves are not
    claims.

    INPUTS:
      stage (str, required)  — "pre_render" or "post_render". Recorded in the report
                               so a reader can tell which pass found what.
                               pre_render reads artifacts/storyboard.json;
                               post_render scans the supplied transcript.
      transcript (str)       — for post_render, the word-timed VO transcript.
                               Omit for pre_render; the tool reads the storyboard.

    WRITES: artifacts/claims_report.json
    RETURNS: {"verdict": "pass"|"request_changes"|"block", "hard_count": int,
              "soft_count": int, "rewrite_instructions": [...], "rounds_used": int,
              "rounds_remaining": int}

    A "block" verdict after CLAIMS_MAX_REWRITE_ROUNDS means a HARD finding could not
    be resolved. Call kanban_block — never complete the card.
    """
    if stage not in ("pre_render", "post_render"):
        raise ValueError(f"Invalid stage '{stage}'. Must be 'pre_render' or 'post_render'.")

    store = ArtifactStore(paths, run_id=paths.run_dir.name)

    # 1. Load prohibited facts from research brief if available
    try:
        brief_data = store.read("research_brief")
        prohibited = (
            brief_data.prohibited_facts
            if hasattr(brief_data, "prohibited_facts") and brief_data.prohibited_facts
            else DEFAULT_PROHIBITED_FACTS
        )
    except Exception:
        prohibited = DEFAULT_PROHIBITED_FACTS

    deterministic_findings: list[dict[str, Any]] = []
    script_text_for_judge: str = ""
    rounds_used: int = 0
    max_rounds = settings.claims_max_rewrite_rounds

    if stage == "pre_render":
        sb_data = store.read("storyboard")
        storyboard = (
            sb_data if isinstance(sb_data, Storyboard) else Storyboard.model_validate(sb_data)
        )
        rounds_used = getattr(storyboard.generation, "claims_rewrite_rounds", 0)

        collected = _collect_script_text(storyboard)

        # Build concatenated script text for LLM judge (spoken VO + on-screen text)
        script_parts: list[str] = []
        if storyboard.voiceover and storyboard.voiceover.full_text:
            script_parts.append(f"VOICEOVER:\n{storyboard.voiceover.full_text}")
        elif storyboard.voiceover and storyboard.voiceover.segments:
            seg_text = " ".join(seg.text for seg in storyboard.voiceover.segments if seg.text)
            if seg_text:
                script_parts.append(f"VOICEOVER:\n{seg_text}")

        ost_lines: list[str] = []
        if storyboard.visual_hook and storyboard.visual_hook.text_overlay:
            ost_lines.append(f"[visual_hook] {storyboard.visual_hook.text_overlay}")
        if storyboard.shots:
            for shot in storyboard.shots:
                for ost in shot.on_screen_text:
                    if ost.text:
                        ost_lines.append(f"[{shot.id}] {ost.text}")
        if ost_lines:
            script_parts.append("ON-SCREEN TEXT:\n" + "\n".join(ost_lines))

        script_text_for_judge = "\n\n".join(script_parts)

        # Scan text: segments (with specific shot_id), on_screen_text, visual_hook, full_text
        scan_passes = [
            ("voiceover.segments", "voiceover"),
            ("on_screen_text", "on_screen_text"),
            ("visual_hook.text_overlay", "visual_hook"),
            ("voiceover.full_text", "voiceover"),
        ]

        seen_finding_keys: set[tuple[str, str, str, str]] = set()
        seen_concrete_matches: set[tuple[str, str]] = set()

        for field_key, loc_field in scan_passes:
            for shot_id, text in collected.get(field_key, []):
                findings = detect_claims(text) + scan_prohibited_facts(text, prohibited)
                for f in findings:
                    sev = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
                    f_key = (f.rule_id, f.matched_text.strip().lower(), shot_id, loc_field)
                    if f_key in seen_finding_keys:
                        continue
                    seen_finding_keys.add(f_key)

                    # Deduplicate full_text matches if segment already reported it with shot_id
                    match_key = (f.rule_id, f.matched_text.strip().lower())
                    if not shot_id and match_key in seen_concrete_matches:
                        continue
                    if shot_id:
                        seen_concrete_matches.add(match_key)

                    deterministic_findings.append({
                        "rule_id": f.rule_id,
                        "severity": sev,
                        "matched_text": f.matched_text,
                        "location": {"shot_id": shot_id, "field": loc_field},
                        "why": f.why,
                        "fix": f.fix,
                    })

    else:  # post_render
        # Check existing post_render report for rounds_used
        post_report_file = paths.artifacts / "claims_report_post_render.json"
        if post_report_file.exists():
            try:
                prev_data = json.loads(post_report_file.read_text(encoding="utf-8"))
                rounds_used = prev_data.get("rounds_used", 0)
            except Exception:
                rounds_used = 0
        else:
            rounds_used = 0

        if transcript is None:
            vo_file = paths.artifacts / "voiceover.json"
            if vo_file.exists():
                try:
                    vo_data = json.loads(vo_file.read_text(encoding="utf-8"))
                    transcript = vo_data.get("transcript", "")
                except Exception:
                    transcript = ""
            else:
                transcript = ""

        script_text_for_judge = transcript or ""

        if transcript:
            findings = detect_claims(transcript) + scan_prohibited_facts(transcript, prohibited)
            for f in findings:
                sev = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
                deterministic_findings.append({
                    "rule_id": f.rule_id,
                    "severity": sev,
                    "matched_text": f.matched_text,
                    "location": {"shot_id": "", "field": "transcript"},
                    "why": f.why,
                    "fix": f.fix,
                })

    # 2. Run LLM Judge (skipped when client is None — offline replay is
    # deterministic-only; the LLM is a soft second opinion, never the hard gate).
    if client is None:
        llm_judge_response = ClaimsJudgeResponse(findings=[], overall="pass")
    else:
        llm_judge_response = _run_async(
            _run_llm_judge(
                settings=settings,
                paths=paths,
                script=script_text_for_judge,
                stage=stage,
                client=client,
            )
        )

    # 3. Adjudicate findings according to Rule C1:
    # - A hard finding from deterministic is final and cannot be de-escalated.
    # - If judge marks a hard finding as soft, drop that de-escalation from judge's findings.
    # - If judge escalates a soft finding to hard, honour the escalation and count it.
    det_hard_findings = [f for f in deterministic_findings if f["severity"] == "hard"]
    det_soft_findings = [f for f in deterministic_findings if f["severity"] == "soft"]

    llm_findings: list[dict[str, Any]] = []
    escalations = 0

    for jf in llm_judge_response.findings:
        if not jf.matched_text or not jf.matched_text.strip():
            # Reject empty span
            continue

        # Check if overlaps with deterministic hard findings
        overlaps_det_hard = any(
            _spans_overlap(jf.matched_text, df["matched_text"]) for df in det_hard_findings
        )
        if overlaps_det_hard:
            if jf.severity == "soft":
                # Rule C1: Judge may NOT de-escalate a hard finding.
                # Drop this de-escalation from judge's findings; deterministic stays HARD.
                continue
            # If judge confirmed hard, don't duplicate finding if identical
            continue

        # Check if overlaps with deterministic soft findings
        matching_soft = [
            df for df in det_soft_findings if _spans_overlap(jf.matched_text, df["matched_text"])
        ]
        if matching_soft:
            if jf.severity == "hard":
                # Escalation of soft -> hard
                escalations += 1
                for df in matching_soft:
                    df["severity"] = "hard"
            continue

        # New finding discovered by the judge
        llm_findings.append({
            "rule_id": jf.rule_id,
            "severity": jf.severity,
            "matched_text": jf.matched_text,
            "why": jf.why,
            "fix": jf.fix,
        })

    deterministic_block = {
        "ruleset_version": RULESET_VERSION,
        "findings": deterministic_findings,
    }

    llm_judge_block = {
        "model_tier": Tier.CHEAP.value,
        "findings": llm_findings,
        "escalations": escalations,
    }

    # 4. Synthesize rewrite instructions & counts
    all_findings_for_rewrite: list[Finding] = []
    for f in deterministic_findings:
        all_findings_for_rewrite.append(
            Finding(
                rule_id=f["rule_id"],
                severity=Severity(f["severity"]),
                matched_text=f["matched_text"],
                why=f["why"],
                fix=f["fix"],
            )
        )
    for f in llm_findings:
        all_findings_for_rewrite.append(
            Finding(
                rule_id=f["rule_id"],
                severity=Severity(f["severity"]),
                matched_text=f["matched_text"],
                why=f["why"],
                fix=f["fix"],
            )
        )

    instructions = rewrite_instructions(all_findings_for_rewrite)

    hard_count = sum(1 for f in all_findings_for_rewrite if f.severity == Severity.HARD)
    soft_count = sum(1 for f in all_findings_for_rewrite if f.severity == Severity.SOFT)
    rounds_remaining = max(0, max_rounds - rounds_used)

    verdict = _verdict(
        hard=hard_count,
        soft=soft_count,
        rounds_used=rounds_used,
        max_rounds=max_rounds,
    )

    # 5. Manage stage files & stage_reports map
    other_stage = "post_render" if stage == "pre_render" else "pre_render"
    this_stage_file = paths.artifacts / f"claims_report_{stage}.json"
    other_stage_file = paths.artifacts / f"claims_report_{other_stage}.json"

    stage_reports: dict[str, str] = {stage: str(this_stage_file)}
    if other_stage_file.exists():
        stage_reports[other_stage] = str(other_stage_file)

    report = ClaimsReport(
        stage=stage,
        verdict=verdict,
        deterministic=deterministic_block,
        llm_judge=llm_judge_block,
        rewrite_instructions=instructions,
        rounds_used=rounds_used,
        rounds_remaining=rounds_remaining,
        stage_reports=stage_reports,
    )

    # Write stage-specific file and primary claims_report.json
    this_stage_file.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    store.write("claims_report", report)

    # Update other stage file if it exists so both reference each other
    if other_stage_file.exists():
        try:
            other_data = json.loads(other_stage_file.read_text(encoding="utf-8"))
            other_data["stage_reports"] = stage_reports
            other_stage_file.write_text(json.dumps(other_data, indent=2), encoding="utf-8")
        except Exception:
            pass

    return {
        "verdict": verdict,
        "hard_count": hard_count,
        "soft_count": soft_count,
        "rewrite_instructions": instructions,
        "rounds_used": rounds_used,
        "rounds_remaining": rounds_remaining,
    }


def rewrite_for_compliance(
    *,
    settings: Settings,
    paths: RunPaths,
    round_no: int | None = None,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    """Apply the deterministic fixes. Bounded by CLAIMS_MAX_REWRITE_ROUNDS.

    CALL THIS: only when cwt_check_claims returned request_changes with hard findings.
    The fixes are mechanical — apply them and re-check.

    WHEN NOT TO CALL: never to "make a blocker go away". After CLAIMS_MAX_REWRITE_ROUNDS,
    block the card with the finding text. A blocked card is the correct outcome.

    REWRITES artifacts/storyboard.json in place; increments generation.claims_rewrite_rounds
    RETURNS {"artifact_path","rounds_used","fixes_applied":[...],"remaining_findings":[...]}
    """
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    sb_raw = store.read("storyboard")
    storyboard = (
        sb_raw if isinstance(sb_raw, Storyboard) else Storyboard.model_validate(sb_raw)
    )

    current_rounds = getattr(storyboard.generation, "claims_rewrite_rounds", 0)
    next_round = round_no if round_no is not None else (current_rounds + 1)
    max_rounds = settings.claims_max_rewrite_rounds

    # Check existing pre_render report
    pre_report_file = paths.artifacts / "claims_report_pre_render.json"
    if pre_report_file.exists():
        pre_report_data = json.loads(pre_report_file.read_text(encoding="utf-8"))
    else:
        check_claims(settings=settings, paths=paths, stage="pre_render", client=client)
        pre_report_data = json.loads(pre_report_file.read_text(encoding="utf-8"))

    fixes = pre_report_data.get("rewrite_instructions", [])
    deterministic_findings = pre_report_data.get("deterministic", {}).get("findings", [])
    llm_findings = pre_report_data.get("llm_judge", {}).get("findings", [])
    remaining = deterministic_findings + llm_findings

    # Check if already at max rounds with a hard finding
    hard_count = sum(1 for f in remaining if f.get("severity") == "hard")
    if current_rounds >= max_rounds and hard_count > 0:
        return {
            "artifact_path": str(paths.artifacts / "storyboard.json"),
            "rounds_used": current_rounds,
            "fixes_applied": [],
            "remaining_findings": remaining,
            "verdict": "block",
        }

    if not fixes:
        return {
            "artifact_path": str(paths.artifacts / "storyboard.json"),
            "rounds_used": current_rounds,
            "fixes_applied": [],
            "remaining_findings": remaining,
            "verdict": pre_report_data.get("verdict", "pass"),
        }

    # Apply fixes via STRONG rewrite path
    verdict = ReviewVerdict(
        reviewer="cwt-compliance",
        round=next_round,
        verdict="request_changes",
        scores={"compliance_safety": 0.0},
        weighted_mean=0.0,
        threshold=7.0,
        weakest_axes=["compliance_safety"],
        changes_requested="\n".join(f"- {f}" for f in fixes),
        must_fix=fixes,
        must_not_change=["visual_hook", "compliance"],
    )

    from cwt.tools.storyboard import _apply_rewrite

    rewritten = _run_async(
        _apply_rewrite(
            settings,
            paths,
            storyboard=storyboard,
            verdict=verdict,
            client=client,
        )
    )

    # Preserve revision_rounds and record claims_rewrite_rounds
    rewritten.generation.revision_rounds = storyboard.generation.revision_rounds
    rewritten.generation.claims_rewrite_rounds = next_round
    store.write("storyboard", rewritten)

    # Re-check claims
    new_report_res = check_claims(
        settings=settings,
        paths=paths,
        stage="pre_render",
        client=client,
    )

    new_report_data = json.loads(pre_report_file.read_text(encoding="utf-8"))
    new_findings = (
        new_report_data.get("deterministic", {}).get("findings", [])
        + new_report_data.get("llm_judge", {}).get("findings", [])
    )

    return {
        "artifact_path": str(paths.artifacts / "storyboard.json"),
        "rounds_used": next_round,
        "fixes_applied": fixes,
        "remaining_findings": new_findings,
        "verdict": new_report_res["verdict"],
    }
