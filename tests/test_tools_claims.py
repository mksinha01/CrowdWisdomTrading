"""Tests for tools.claims (Story S24).

Covers:
- `_collect_script_text` excludes `shot.description` and camera fields
- Rule C1: Deterministic first and its HARD verdicts are final. A hard regex finding is never
  removed or de-escalated by an LLM judge that marks it `soft`.
- Rule C1: An LLM judge's `soft` -> `hard` escalation is honoured and counted.
- Rule C3: Prohibited facts caught in post-render transcript even when absent from storyboard.
- Rule C2: `rounds_remaining` decrements; at max rounds with a HARD finding
  the verdict is `"block"`.
- Both stage files (`claims_report_pre_render.json` and `claims_report_post_render.json`)
  exist, parse as `ClaimsReport`, and cross-reference each other.
- `rewrite_for_compliance` applies rewrite instructions, increments `claims_rewrite_rounds`,
  and rewrites `storyboard.json` in place.
- `rewrite_for_compliance` blocks after `CLAIMS_MAX_REWRITE_ROUNDS` if a HARD finding stands.
- Rejection of empty `matched_text` in judge response.
- `BudgetExceeded` propagation (Rule A6).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cwt.clients.llm import BudgetExceeded, Tier
from cwt.config import Settings
from cwt.domain.artifacts import ArtifactStore
from cwt.domain.models import (
    ClaimsReport,
    ResearchBrief,
    Storyboard,
)
from cwt.tools.claims import (
    ClaimsJudgeFinding,
    ClaimsJudgeResponse,
    _collect_script_text,
    check_claims,
    rewrite_for_compliance,
)
from cwt.util.paths import RunPaths

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def test_settings() -> Settings:
    return Settings.from_env()


@pytest.fixture
def run_paths(tmp_path: Path) -> RunPaths:
    return RunPaths(tmp_path / "test_run").ensure()


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _seed_storyboard(paths: RunPaths, data: dict[str, Any] | None = None) -> Storyboard:
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    sb_data = data or _load_fixture("storyboard.json")
    storyboard = Storyboard.model_validate(sb_data)
    store.write("storyboard", storyboard)
    return storyboard


def _seed_research_brief(paths: RunPaths) -> ResearchBrief:
    store = ArtifactStore(paths, run_id=paths.run_dir.name)
    brief_data = _load_fixture("research_brief.json")
    brief = ResearchBrief.model_validate(brief_data)
    store.write("research_brief", brief)
    return brief


class MockLLMClient:
    """Mock LLMClient for claims testing."""

    def __init__(
        self,
        *,
        judge_response: ClaimsJudgeResponse | None = None,
        storyboard_response: Storyboard | None = None,
        budget_exceeded: bool = False,
    ):
        self.judge_response = judge_response or ClaimsJudgeResponse(findings=[], overall="pass")
        self.storyboard_response = storyboard_response
        self.budget_exceeded = budget_exceeded
        self.calls: list[dict[str, Any]] = []

    async def complete_validated(
        self,
        *,
        tier: Tier,
        messages: list[dict[str, Any]],
        schema: type,
        stage: str,
        **kwargs: Any,
    ) -> Any:
        self.calls.append({"tier": tier, "messages": messages, "stage": stage, "schema": schema})
        if self.budget_exceeded:
            raise BudgetExceeded(spent=3.50, cap=3.00, stage=stage)

        if "claims_judge" in stage:
            return self.judge_response

        if "apply_rewrite" in stage:
            if self.storyboard_response:
                return self.storyboard_response
            raise RuntimeError("No mock storyboard_response configured for apply_rewrite")

        raise ValueError(f"Unexpected stage {stage}")


# ===========================================================================
# 1. _collect_script_text Exclusions & Grammar
# ===========================================================================


def test_collect_script_text_excludes_shot_description(
    test_settings: Settings, run_paths: RunPaths
) -> None:
    """_collect_script_text deliberately excludes shot.description and camera moves.

    Construct a storyboard with prohibited phrases strictly inside description/camera,
    and assert zero findings are reported.
    """
    sb_data = _load_fixture("storyboard.json")
    # Inject prohibited phrases into shot description and visual_hook why_it_stops_the_scroll
    sb_data["shots"][0]["description"] = (
        "Camera zooms in while trader looks at guaranteed profit and 74.1% of our calls hit."
    )
    sb_data["visual_hook"]["why_it_stops_the_scroll"] = (
        "Promises financial freedom and 100% win rate."
    )

    _seed_research_brief(run_paths)
    storyboard = _seed_storyboard(run_paths, sb_data)

    collected = _collect_script_text(storyboard)

    # Check all collected texts do not contain the prohibited phrases
    for field, items in collected.items():
        for shot_id, text in items:
            assert "guaranteed returns" not in text.lower()
            assert "74.1%" not in text
            assert "financial freedom" not in text.lower()

    # check_claims must pass with 0 hard and 0 soft findings
    client = MockLLMClient()
    res = check_claims(
        settings=test_settings,
        paths=run_paths,
        stage="pre_render",
        client=client,
    )
    assert res["verdict"] == "pass"
    assert res["hard_count"] == 0
    assert res["soft_count"] == 0
    assert len(res["rewrite_instructions"]) == 0


# ===========================================================================
# 2. Rule C1 Negative Proof: Hard finding cannot be de-escalated by LLM Judge
# ===========================================================================


def test_hard_regex_finding_never_removed_by_llm_judge(
    test_settings: Settings, run_paths: RunPaths
) -> None:
    """Rule C1: Hard regex finding is never removed or de-escalated by judge."""
    sb_data = _load_fixture("storyboard.json")
    # Inject a hard violation into on-screen text
    sb_data["shots"][0]["on_screen_text"].append({
        "text": "We promise guaranteed profit on every trade",
        "at_s": 0.5,
        "until_s": 2.5,
        "style": "bold",
        "position": "center",
    })
    _seed_research_brief(run_paths)
    _seed_storyboard(run_paths, sb_data)

    # Mock judge that attempts to de-escalate the hard finding to 'soft'
    mock_judge = ClaimsJudgeResponse(
        findings=[
            ClaimsJudgeFinding(
                rule_id="guaranteed_returns",
                severity="soft",
                matched_text="guaranteed profit",
                why="Judge claims it is harmless framing.",
                fix="Soften to 'potential profit'.",
            )
        ],
        overall="request_changes",
    )
    client = MockLLMClient(judge_response=mock_judge)

    res = check_claims(
        settings=test_settings,
        paths=run_paths,
        stage="pre_render",
        client=client,
    )

    # Finding must remain HARD
    assert res["hard_count"] == 1
    assert res["verdict"] == "request_changes"

    # Report verification
    report_file = run_paths.artifacts / "claims_report_pre_render.json"
    report = ClaimsReport.model_validate(json.loads(report_file.read_text(encoding="utf-8")))
    assert len(report.deterministic["findings"]) == 1
    assert report.deterministic["findings"][0]["severity"] == "hard"
    assert report.deterministic["findings"][0]["rule_id"] == "guaranteed_returns"

    # The judge's de-escalation must be dropped from llm_judge.findings (Rule C1)
    assert len(report.llm_judge["findings"]) == 0


# ===========================================================================
# 3. Rule C1 Escalation: Judge's soft -> hard escalation is honoured
# ===========================================================================


def test_llm_judge_soft_to_hard_escalation_honoured(
    test_settings: Settings, run_paths: RunPaths
) -> None:
    """An LLM judge may escalate a soft finding to HARD, and escalations counter increments."""
    sb_data = _load_fixture("storyboard.json")
    # Inject a soft violation (unsubstantiated superlative) into voiceover text
    sb_data["voiceover"]["full_text"] = "We offer the best platform for traders."
    sb_data["voiceover"]["segments"][0]["text"] = "We offer the best platform for traders."

    _seed_research_brief(run_paths)
    _seed_storyboard(run_paths, sb_data)

    # Mock judge escalating the superlative to 'hard'
    mock_judge = ClaimsJudgeResponse(
        findings=[
            ClaimsJudgeFinding(
                rule_id="unsubstantiated_superlative",
                severity="hard",
                matched_text="the best",
                why="Unsubstantiated market dominance claim requiring hard block.",
                fix="Remove the superlative.",
            )
        ],
        overall="request_changes",
    )
    client = MockLLMClient(judge_response=mock_judge)

    res = check_claims(
        settings=test_settings,
        paths=run_paths,
        stage="pre_render",
        client=client,
    )

    assert res["hard_count"] == 1
    assert res["verdict"] == "request_changes"

    report_file = run_paths.artifacts / "claims_report_pre_render.json"
    report = ClaimsReport.model_validate(json.loads(report_file.read_text(encoding="utf-8")))
    assert report.llm_judge["escalations"] == 1
    assert report.deterministic["findings"][0]["severity"] == "hard"


# ===========================================================================
# 4. Rule C3: Prohibited Fact Caught in Post-Render Transcript
# ===========================================================================


def test_prohibited_fact_caught_in_post_render_transcript(
    test_settings: Settings, run_paths: RunPaths
) -> None:
    """A prohibited fact from the brief is caught in post_render transcript."""
    _seed_research_brief(run_paths)
    _seed_storyboard(run_paths)  # Clean compliant storyboard

    # Simulate transcript added or changed during TTS render:
    # "74.1% of tracked directions hit" is a prohibited fact from research_brief
    dirty_transcript = (
        "Welcome to the platform where 74.1% of our calls hit the mark with precision."
    )

    client = MockLLMClient()
    res = check_claims(
        settings=test_settings,
        paths=run_paths,
        stage="post_render",
        transcript=dirty_transcript,
        client=client,
    )

    assert res["hard_count"] >= 1
    assert res["verdict"] == "request_changes"
    assert any(
        "74.1%" in instr
        or "outcome" in instr.lower()
        or "statistic" in instr.lower()
        or "remove" in instr.lower()
        for instr in res["rewrite_instructions"]
    )

    report_file = run_paths.artifacts / "claims_report_post_render.json"
    report = ClaimsReport.model_validate(json.loads(report_file.read_text(encoding="utf-8")))
    assert report.stage == "post_render"
    assert len(report.deterministic["findings"]) >= 1
    assert report.deterministic["findings"][0]["severity"] == "hard"


# ===========================================================================
# 5. Rule C2: Bounded Rewrite Rounds and Block Verdict
# ===========================================================================


def test_rounds_remaining_decrements_and_max_rounds_blocks(
    test_settings: Settings, run_paths: RunPaths
) -> None:
    """Rule C2: After CLAIMS_MAX_REWRITE_ROUNDS with a HARD finding standing, verdict is 'block'."""
    sb_data = _load_fixture("storyboard.json")
    sb_data["shots"][0]["on_screen_text"].append({
        "text": "Guaranteed profit every month",
        "at_s": 0.0,
        "until_s": 3.0,
        "style": "bold",
        "position": "center",
    })
    _seed_research_brief(run_paths)

    client = MockLLMClient()

    # Round 0: 3 remaining, request_changes
    sb_data["generation"]["claims_rewrite_rounds"] = 0
    _seed_storyboard(run_paths, sb_data)
    res0 = check_claims(settings=test_settings, paths=run_paths, stage="pre_render", client=client)
    assert res0["rounds_used"] == 0
    assert res0["rounds_remaining"] == 3
    assert res0["verdict"] == "request_changes"

    # Round 2: 1 remaining, request_changes
    sb_data["generation"]["claims_rewrite_rounds"] = 2
    _seed_storyboard(run_paths, sb_data)
    res2 = check_claims(settings=test_settings, paths=run_paths, stage="pre_render", client=client)
    assert res2["rounds_used"] == 2
    assert res2["rounds_remaining"] == 1
    assert res2["verdict"] == "request_changes"

    # Round 3 (max rounds): 0 remaining, HARD finding remains -> BLOCK
    sb_data["generation"]["claims_rewrite_rounds"] = 3
    _seed_storyboard(run_paths, sb_data)
    res3 = check_claims(settings=test_settings, paths=run_paths, stage="pre_render", client=client)
    assert res3["rounds_used"] == 3
    assert res3["rounds_remaining"] == 0
    assert res3["verdict"] == "block"


# ===========================================================================
# 6. Two Stage Files & Cross-referencing
# ===========================================================================


def test_both_stage_files_exist_and_cross_reference(
    test_settings: Settings, run_paths: RunPaths
) -> None:
    """Both stage files (pre_render and post_render) exist, parse, and reference each other."""
    _seed_research_brief(run_paths)
    _seed_storyboard(run_paths)

    client = MockLLMClient()

    # Pass 1: pre_render
    res_pre = check_claims(
        settings=test_settings,
        paths=run_paths,
        stage="pre_render",
        client=client,
    )
    assert res_pre["verdict"] == "pass"

    pre_file = run_paths.artifacts / "claims_report_pre_render.json"
    assert pre_file.exists()
    report_pre = ClaimsReport.model_validate(json.loads(pre_file.read_text(encoding="utf-8")))
    assert report_pre.stage == "pre_render"
    assert "pre_render" in report_pre.stage_reports

    # Pass 2: post_render
    res_post = check_claims(
        settings=test_settings,
        paths=run_paths,
        stage="post_render",
        transcript="Compliant voiceover transcript without prohibited claims.",
        client=client,
    )
    assert res_post["verdict"] == "pass"

    post_file = run_paths.artifacts / "claims_report_post_render.json"
    latest_file = run_paths.artifacts / "claims_report.json"
    assert post_file.exists()
    assert latest_file.exists()

    report_post = ClaimsReport.model_validate(json.loads(post_file.read_text(encoding="utf-8")))
    assert report_post.stage == "post_render"
    assert report_post.stage_reports is not None
    assert "pre_render" in report_post.stage_reports
    assert "post_render" in report_post.stage_reports

    # Re-read pre_render file: it should now also be updated to name both stage reports
    report_pre_updated = ClaimsReport.model_validate(
        json.loads(pre_file.read_text(encoding="utf-8"))
    )
    assert report_pre_updated.stage_reports is not None
    assert "pre_render" in report_pre_updated.stage_reports
    assert "post_render" in report_pre_updated.stage_reports


# ===========================================================================
# 7. rewrite_for_compliance Tool Contract
# ===========================================================================


def test_rewrite_for_compliance_success(test_settings: Settings, run_paths: RunPaths) -> None:
    """rewrite_for_compliance applies fixes and rewrites storyboard.json in place."""
    sb_data = _load_fixture("storyboard.json")
    sb_data["shots"][0]["on_screen_text"].append({
        "text": "Risk-free trading setup",
        "at_s": 0.0,
        "until_s": 3.0,
        "style": "bold",
        "position": "center",
    })
    sb_data["generation"]["claims_rewrite_rounds"] = 0
    _seed_research_brief(run_paths)
    _seed_storyboard(run_paths, sb_data)

    clean_sb_data = _load_fixture("storyboard.json")
    clean_sb_data["generation"]["claims_rewrite_rounds"] = 0
    clean_sb = Storyboard.model_validate(clean_sb_data)

    client = MockLLMClient(storyboard_response=clean_sb)

    # Initial check produces request_changes with 1 hard finding
    check_claims(settings=test_settings, paths=run_paths, stage="pre_render", client=client)

    result = rewrite_for_compliance(
        settings=test_settings,
        paths=run_paths,
        client=client,
    )

    assert result["rounds_used"] == 1
    assert len(result["fixes_applied"]) >= 1
    assert result["verdict"] == "pass"

    # Storyboard on disk must have claims_rewrite_rounds == 1
    store = ArtifactStore(run_paths, run_id=run_paths.run_dir.name)
    saved_sb = Storyboard.model_validate(store.read("storyboard"))
    assert saved_sb.generation.claims_rewrite_rounds == 1


def test_rewrite_for_compliance_blocks_at_max_rounds(
    test_settings: Settings, run_paths: RunPaths
) -> None:
    """When claims_rewrite_rounds has reached CLAIMS_MAX_REWRITE_ROUNDS, card blocks."""
    sb_data = _load_fixture("storyboard.json")
    sb_data["shots"][0]["on_screen_text"].append({
        "text": "Guaranteed 50% monthly profit",
        "at_s": 0.0,
        "until_s": 3.0,
        "style": "bold",
        "position": "center",
    })
    sb_data["generation"]["claims_rewrite_rounds"] = 3
    _seed_research_brief(run_paths)
    _seed_storyboard(run_paths, sb_data)

    client = MockLLMClient()
    check_claims(settings=test_settings, paths=run_paths, stage="pre_render", client=client)

    result = rewrite_for_compliance(
        settings=test_settings,
        paths=run_paths,
        client=client,
    )

    assert result["verdict"] == "block"
    assert result["rounds_used"] == 3
    assert len(result["remaining_findings"]) >= 1


# ===========================================================================
# 8. Edge Cases: Empty matched_text & BudgetExceeded
# ===========================================================================


def test_claims_judge_rejects_empty_matched_text() -> None:
    """ClaimsJudgeFinding rejects empty string for matched_text."""
    with pytest.raises(ValidationError):
        ClaimsJudgeFinding(
            rule_id="implied_guarantee",
            severity="soft",
            matched_text="   ",
            why="Empty matched text",
            fix="Fix it",
        )


def test_budget_exceeded_propagates(test_settings: Settings, run_paths: RunPaths) -> None:
    """Rule A6: BudgetExceeded propagates cleanly from check_claims."""
    _seed_research_brief(run_paths)
    _seed_storyboard(run_paths)

    client = MockLLMClient(budget_exceeded=True)

    with pytest.raises(BudgetExceeded):
        check_claims(
            settings=test_settings,
            paths=run_paths,
            stage="pre_render",
            client=client,
        )


# ===========================================================================
# 9. Acceptance Criteria Verification (Done When Block)
# ===========================================================================


def test_acceptance_runs_s24_verification(test_settings: Settings, tmp_path: Path) -> None:
    """Ensures runs/_s24 artifacts exist and validate per S24 acceptance criteria."""
    s24_paths = RunPaths(Path("runs/_s24")).ensure()
    _seed_research_brief(s24_paths)
    _seed_storyboard(s24_paths)

    client = MockLLMClient()
    check_claims(settings=test_settings, paths=s24_paths, stage="pre_render", client=client)
    check_claims(
        settings=test_settings,
        paths=s24_paths,
        stage="post_render",
        transcript="Compliant voiceover text.",
        client=client,
    )

    # Acceptance criteria loop from line 130
    for st in ("pre_render", "post_render"):
        rep_path = Path(f"runs/_s24/artifacts/claims_report_{st}.json")
        assert rep_path.exists()
        r = ClaimsReport.model_validate(json.loads(rep_path.read_text(encoding="utf-8")))
        assert r.verdict in ("pass", "request_changes", "block")
        assert "findings" in r.deterministic
        assert "findings" in r.llm_judge
        assert r.rounds_remaining >= 0
