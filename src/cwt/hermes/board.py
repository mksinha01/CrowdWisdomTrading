"""Kanban board operations. The only module that talks to the board.

# ── IMPORTANT: nudge() is a nudge, NEVER a force-complete ────────────────────
# The dispatcher runs inside the gateway. When the gateway is down, cards sit on
# `ready` forever. The stall detector nudges ONCE (one dispatch tick), then fails
# loudly with the fix message.
#
# NEVER call `kanban complete` to clear a stall. The pipeline does not fabricate
# progress. If a future change here reaches for `kanban complete` as a recovery
# strategy — stop. Re-read Rules K3 and K4 in the spec first.
# ─────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table

from cwt.hermes.cli import kanban
from cwt.hermes.dag import DAG_SPEC, CardSpec, topo_sort
from cwt.util.jsonio import write_json

logger = logging.getLogger("cwt.board")
console = Console()

TERMINAL_BAD = {"blocked", "gave_up"}


def render_body(card: CardSpec, run_id: str, run_dir: Path, board: str) -> str:
    """The contract every worker reads. Produced verbatim into the card body.

    Note what this does NOT contain: artifact paths. Paths arrive through the
    parent chain via kanban_show(), so a card never has to be re-seeded when a
    path convention changes. That is why ``--parent`` is a context channel, not
    merely a scheduling gate.
    """
    skills = ", ".join(card.skills) if card.skills else "(none)"
    return f"""RUN_ID: {run_id}
RUN_DIR: {run_dir}
BOARD: {board}

STEP 1 — Call kanban_show() with no arguments. Read every parent's
         metadata.artifact_path. Those are your inputs. Do not guess paths.

STEP 2 — Follow the pinned skill exactly: {skills}
         If no skill is pinned, call kanban_show() and follow the body above.

STEP 3 — Do your work by calling the cwt_* tools. They write the artifacts for you.
         NEVER hand-write an artifact JSON file — the tools validate before writing
         and a hand-written file will fail the next stage's schema check.

STEP 4 — Call kanban_complete with:
             summary  = 2-3 sentences a human will read on the dashboard
             metadata = {{"stage": "{card.key}",
                          "artifact_path": "<path relative to RUN_DIR>",
                          "sha256": "<sha256 of the artifact file>",
                          "status": "ok"}}
         A completion WITHOUT metadata.artifact_path is a bug: the next stage has
         nothing to read and will block.

ON FAILURE — Call kanban_block(reason="<what failed, what you tried, what a human
             should do>"). Never complete a stage you did not produce an artifact for.

{card.body}
"""


def seed(
    run_id: str,
    run_dir: Path,
    board: str,
    spec: list[CardSpec] = DAG_SPEC,
) -> dict[str, str]:
    """Create every card in topological order, wiring parents as we go.

    ``--idempotency-key {run_id}:{key}`` is what makes ``cwt run --resume`` safe:
    re-seeding after a crash REUSES the existing card rather than duplicating the
    whole board.
    """
    ids: dict[str, str] = {}
    for card in topo_sort(spec):
        parent_ids = [ids[p] for p in card.parents]
        args = [
            "create",
            card.title,
            "--assignee",
            card.assignee,
            "--body",
            render_body(card, run_id, run_dir, board),
            "--idempotency-key",
            f"{run_id}:{card.key}",
            "--json",
        ]
        for pid in parent_ids:
            args += ["--parent", pid]
        for skill in card.skills:
            args += ["--skill", skill]
        if card.max_runtime:
            args += ["--max-runtime", card.max_runtime]
        if card.max_retries is not None:
            args += ["--max-retries", str(card.max_retries)]
        if card.goal:
            args += ["--goal", "--goal-max-turns", "20"]

        result = kanban(*args, board=board, timeout_s=60)
        if not result.ok:
            raise RuntimeError(
                f"Failed to create card {card.key!r}:\n{result.stderr[-2000:]}\n"
                f"If the error names an unknown flag, your Hermes version differs from the "
                f"one this spec targets. Run `cwt doctor` for a flag diff."
            )
        ids[card.key] = result.json()["id"]
        logger.info("seeded %s -> %s", card.key, ids[card.key])
    return ids


def list_cards(board: str) -> list[dict]:
    result = kanban("list", "--json", board=board, timeout_s=60)
    if not result.ok:
        return []
    data = result.json()
    return data if isinstance(data, list) else data.get("tasks", [])


def nudge(board: str) -> None:
    """Trigger one dispatcher tick immediately.

    Used by the stall detector. This is a NUDGE, never a force-complete — the
    pipeline never fabricates progress.
    """
    kanban("dispatch", board=board, timeout_s=60)


@dataclass
class RunSummary:
    cards: list[dict]

    @property
    def done(self) -> int:
        return sum(1 for c in self.cards if c["status"] == "done")


class PipelineTimeout(RuntimeError): ...
class PipelineBlocked(RuntimeError): ...


async def wait_for_completion(
    board: str,
    *,
    timeout_s: int,
    poll_s: int = 15,
    fail_fast: bool = False,
    stall_threshold_s: int = 180,
) -> RunSummary:
    """Poll the board until every card is done, or fail loudly.

    The stall detector is not optional. The single most likely 'it hangs forever'
    failure is the dispatcher not running because the gateway is down — cards sit
    on ``ready`` and nothing happens. We detect it, nudge once, and then raise with
    full diagnostics rather than waiting out the full timeout.

    Progress is tracked by a **card signature** ``tuple(sorted((id, status)))``.
    A change in status (e.g. ``running -> review``) resets the stall timer, even if
    no card is yet ``done``. That is the intended semantics — see spec §9.2.
    """
    deadline = time.monotonic() + timeout_s
    last_progress = time.monotonic()
    last_signature: tuple | None = None
    nudged = False

    while True:
        cards = list_cards(board)
        signature = tuple(sorted((c["id"], c["status"]) for c in cards))
        done = [c for c in cards if c["status"] == "done"]
        bad = [c for c in cards if c["status"] in TERMINAL_BAD]

        if bad and fail_fast:
            _dump_diagnostics(board, bad)
            raise PipelineBlocked(
                f"{len(bad)} card(s) blocked or gave up: {[c['id'] for c in bad]}"
            )

        if len(done) == len(DAG_SPEC) and not bad:
            return RunSummary(cards)

        if time.monotonic() > deadline:
            _dump_diagnostics(board, cards)
            raise PipelineTimeout(
                f"Pipeline exceeded {timeout_s}s. "
                f"{len(done)}/{len(DAG_SPEC)} cards done."
            )

        if signature != last_signature:
            last_signature = signature
            last_progress = time.monotonic()
            nudged = False
        elif time.monotonic() - last_progress > stall_threshold_s:
            if not nudged:
                console.print(
                    f"[yellow]No board progress in {stall_threshold_s}s — "
                    f"nudging the dispatcher[/yellow]"
                )
                nudge(board)
                nudged = True
                last_progress = time.monotonic()
            else:
                _dump_diagnostics(board, cards)
                raise PipelineBlocked(
                    "Board stalled after a dispatcher nudge. The gateway is probably not "
                    "running. Start it with `hermes gateway start`, then resume with "
                    "`cwt run --resume`."
                )

        _render_progress(cards)
        await asyncio.sleep(poll_s)


def _render_progress(cards: list[dict]) -> None:
    """Live progress table. Safe to have on screen during a recording — this is
    deliberately the thing a viewer watches."""
    table = Table(title="CWT Pipeline", show_header=True, header_style="bold cyan")
    table.add_column("Card", style="dim", width=10)
    table.add_column("Stage", width=22)
    table.add_column("Assignee", width=22)
    table.add_column("Status", width=10)
    colours = {
        "done": "green",
        "running": "cyan",
        "ready": "yellow",
        "blocked": "red",
        "todo": "dim",
        "review": "magenta",
    }
    for card in cards:
        status = card.get("status", "?")
        table.add_row(
            card.get("id", "?"),
            card.get("title", "")[:22],
            card.get("assignee", ""),
            f"[{colours.get(status, 'white')}]{status}[/]",
        )
    console.print(table)


def _dump_diagnostics(board: str, cards: list[dict]) -> None:
    """Write everything a human needs to diagnose, and print the retry command.

    Diagnostics land in ``runs/_diagnostics/`` (not inside ``runs/<run_id>/``).
    That directory intentionally outlives any individual run — diagnostics span
    runs, and surviving the run they describe is the point.
    """
    for card in cards:
        if card.get("status") not in TERMINAL_BAD and card.get("status") != "running":
            continue
        cid = card["id"]
        show = kanban("show", cid, "--json", board=board, timeout_s=30)
        runs = kanban("runs", cid, "--json", board=board, timeout_s=30)
        log = kanban("log", cid, board=board, timeout_s=30)
        write_json(
            Path("runs") / "_diagnostics" / f"{cid}.json",
            {
                "show": show.stdout,
                "runs": runs.stdout,
                "log_tail": log.stdout[-20000:],
            },
        )
        console.print(
            f"[red]Diagnostics for {cid} written to runs/_diagnostics/{cid}.json[/red]"
        )
    console.print("[yellow]To retry:  cwt run --resume[/yellow]")


def resume(run_id: str, board: str) -> None:
    """Unblock every blocked card so the dispatcher picks it up again.

    Safe because seeding is idempotent — the artifacts already on disk are read
    by the stage cache, so a resumed run does not re-spend API credits.
    """
    for card in list_cards(board):
        if card.get("status") in TERMINAL_BAD:
            kanban("unblock", card["id"], board=board, timeout_s=30)
            console.print(f"[green]unblocked {card['id']}[/green]")
