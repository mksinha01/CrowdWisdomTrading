"""The card graph.

Every topology claim in this document is one edit to this list.

WHY THE REVIEW IS A CARD AND NOT A LOOP INSIDE A CARD:
Hermes' review lifecycle is that a reviewer claims a card out of `review` and
terminates it with EITHER `kanban_complete` (approve) or `kanban_request_changes`
(back to the implementer). `kanban_complete` ENDS THE CARD — which would start
the render and skip the compliance gate entirely.

So compliance is its own card, gated on the script card. Only verified
primitives are used, and both gates are real gates.

    t_script ──request_review──▶ cwt-creative-director
                                     │ complete  = approve
                                     │ request_changes = rewrite (max 3)
                                     ▼
    t_compliance (parent: script)  ── deterministic gate + bounded rewrite
                                     ▼
    t_render ──▶ t_qa ──▶ t_collect

NOTE ON CARD COUNT:
The spec's prose (§9.1 line 3341 and §9.7 "Seeding 11 cards") says 11 cards.
The actual data structure below defines 12 (root, ads, patterns, res_pain,
res_unique, res_crowd, brief, script, compliance, render, qa, collect).
The data wins: `wait_for_completion` compares against `len(DAG_SPEC)`, so 12
is the self-consistent count. The prose is a known defect.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CardSpec:
    key: str
    title: str
    assignee: str
    parents: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    max_runtime: str | None = None
    max_retries: int | None = None
    goal: bool = False
    body: str = ""


DAG_SPEC: list[CardSpec] = [
    CardSpec(
        key="root",
        title="Produce a 30-60s cinematic ad for crowdwisdomtrading.com",
        assignee="cwt-orchestrator",
        skills=("cwt-source-winning-ads",),
        goal=True,
        max_runtime="10m",
        body=(
            "Acceptance criteria — ALL must hold:\n"
            "  1. render/final.mp4 exists, is 30-60s, 1080x1920, and ffprobe-valid\n"
            "  2. artifacts/storyboard.json validates and its creative_scores.verdict == 'pass'\n"
            "  3. artifacts/claims_report.json verdict == 'pass' on BOTH the pre-render and\n"
            "     post-render passes\n"
            "  4. submission/ contains the bundle described in the README\n"
            "This card is an anchor for the DAG. It completes immediately."
        ),
    ),
    CardSpec(
        key="ads",
        title="Source winning ads — Meta Ads Library via Apify, last 30 days",
        assignee="cwt-ads-manager",
        parents=("root",),
        skills=("cwt-source-winning-ads",),
        max_runtime="20m",
        max_retries=2,
        body=(
            "Find ads CURRENTLY RUNNING in the trading/fintech niche. Ad longevity is the\n"
            "performance signal: an ad still running after 21 days has survived the fatigue\n"
            "window. Hard-capped spend — see Rule A2. If the run returns fewer than 8 ads\n"
            "inside the window, complete anyway and record a warning; do NOT widen the window."
        ),
    ),
    CardSpec(
        key="patterns",
        title="Extract hooks, pains, concepts and beat sheets from the winning ads",
        assignee="cwt-hook-analyst",
        parents=("ads",),
        skills=("cwt-extract-ad-patterns",),
        max_runtime="20m",
        body=(
            "Extract structure, not just topic. The beat_sheet is the highest-value output:\n"
            "which beat occupies which second. Aggregate the median timeline across all ads —\n"
            "the storyboard validator enforces it later, so a lazy aggregate here becomes a\n"
            "hard failure downstream."
        ),
    ),
    CardSpec(
        key="res_pain",
        title="Research angle: the ICP's pain (last 30 days)",
        assignee="cwt-researcher",
        parents=("patterns",),
        skills=("cwt-research-angle",),
        max_runtime="12m",
    ),
    CardSpec(
        key="res_unique",
        title="Research angle: CrowdWisdomTrading's unique data",
        assignee="cwt-researcher",
        parents=("patterns",),
        skills=("cwt-research-angle",),
        max_runtime="12m",
        body=(
            "This card MUST populate research_brief.prohibited_facts. Any claim the product\n"
            "makes that you could not verify from a public source belongs in that list. The\n"
            "claims engine reads it and hard-blocks the script if any of it appears."
        ),
    ),
    CardSpec(
        key="res_crowd",
        title="Research angle: crowd wisdom vs single-expert forecasting",
        assignee="cwt-researcher",
        parents=("patterns",),
        skills=("cwt-research-angle",),
        max_runtime="12m",
        body=(
            "Find the strongest COUNTER-argument too. A script that only cites supporting\n"
            "evidence is propaganda, and the objection beat needs something honest to answer."
        ),
    ),
    CardSpec(
        key="brief",
        title="Assemble the research brief — select, do not summarise",
        assignee="cwt-researcher",
        parents=("res_pain", "res_unique", "res_crowd"),
        skills=("cwt-research-angle",),
        max_runtime="8m",
    ),
    CardSpec(
        key="script",
        title="Write storyboard: 3 variants, judge, splice, revise to threshold",
        assignee="cwt-script-writer",
        parents=("brief",),
        skills=("cwt-write-storyboard",),
        max_runtime="45m",
        max_retries=2,
        body=(
            "Write all THREE variants — one per research angle. The brief requires three\n"
            "distinct inputs; writing one script and claiming three is not acceptable.\n\n"
            "Your terminal call is kanban_request_review with reviewer cwt-creative-director.\n"
            "On request_changes: apply the rewrite and re-request. MAXIMUM 3 ROUNDS, then\n"
            "kanban_block with the reason.\n\n"
            "NEVER call kanban_complete. The creative director owns that decision."
        ),
    ),
    CardSpec(
        key="compliance",
        title="Claims gate — deterministic policy check + bounded rewrite",
        assignee="cwt-compliance",
        parents=("script",),
        skills=("cwt-claims-gate",),
        max_runtime="15m",
        body=(
            "Read the parent's storyboard.json. Run cwt_check_claims over it.\n\n"
            "If it passes: kanban_complete with metadata.artifact_path = claims_report.json.\n\n"
            "If HARD findings: call cwt_rewrite_for_compliance. The deterministic fixes are\n"
            "specific instructions, not creative work — apply them, re-check, and complete\n"
            "with the corrected storyboard as artifacts/storyboard.json (overwrite in place\n"
            "and record the round in generation.claims_rewrite_rounds).\n\n"
            "After CLAIMS_MAX_REWRITE_ROUNDS with a HARD finding still standing, call\n"
            "kanban_block with the finding text. It must NEVER silently pass. A blocked card\n"
            "with a clear reason is a better outcome than a shipped unsubstantiated claim."
        ),
    ),
    CardSpec(
        key="render",
        title="Render the 30-60s video from storyboard.json",
        assignee="cwt-video-editor",
        parents=("compliance",),
        skills=("cwt-render-video",),
        max_runtime="60m",
        max_retries=1,
        body=(
            "Synthesize the voiceover, resolve assets, then render through the backend chain.\n"
            "The chain ALWAYS terminates in local_ffmpeg — a failure there is a real failure,\n"
            "but the earlier backends failing is expected and is recorded, not reported as an\n"
            "error. Probe the output before declaring success: ffmpeg exits 0 having written a\n"
            "0-byte file when the last frame is dropped (Rule V3)."
        ),
    ),
    CardSpec(
        key="qa",
        title="Final QA — duration, aspect, loudness, post-render claims re-check",
        assignee="cwt-qa",
        parents=("render",),
        skills=("cwt-final-qa",),
        max_runtime="10m",
        body=(
            "Re-run the claims engine over the RENDERED voiceover transcript, not the\n"
            "storyboard. TTS normalisation changes what is actually said, and a line added\n"
            "during render would otherwise bypass the gate entirely. Recompute the risk\n"
            "disclosure's on-screen duration from the rendered timeline, not the declared value."
        ),
    ),
    CardSpec(
        key="collect",
        title="Assemble the submission bundle",
        assignee="cwt-ads-manager",
        parents=("qa",),
        max_runtime="5m",
        body=(
            "Produce submission/: final.mp4, storyboard.json, storyboard.html,\n"
            "contact_sheet.png, render_manifest.json, claims_report.json, cost_report.json,\n"
            "and README-SUBMISSION.md containing the two API tokens and the recording recipe."
        ),
    ),
]


def topo_sort(spec: list[CardSpec] = DAG_SPEC) -> list[CardSpec]:
    """Kahn's algorithm. Deterministic order — a stable sort keeps card ids
    reproducible across resumes, which matters for idempotency keys."""
    by_key = {c.key: c for c in spec}
    indegree = {c.key: len(c.parents) for c in spec}
    children: dict[str, list[str]] = {c.key: [] for c in spec}
    for card in spec:
        for parent in card.parents:
            if parent not in by_key:
                raise ValueError(f"Card {card.key!r} names unknown parent {parent!r}")
            children[parent].append(card.key)

    ready = sorted([k for k, d in indegree.items() if d == 0])
    out: list[CardSpec] = []
    while ready:
        key = ready.pop(0)
        out.append(by_key[key])
        for child in children[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(out) != len(spec):
        raise ValueError("DAG_SPEC contains a cycle")
    return out
