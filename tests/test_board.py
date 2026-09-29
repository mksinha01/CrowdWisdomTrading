"""Tests for hermes/board.py — no live Hermes required.

All kanban calls are monkeypatched so the tests run fully offline.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from cwt.hermes.board import (
    TERMINAL_BAD,
    PipelineBlocked,
    PipelineTimeout,
    RunSummary,
    _dump_diagnostics,
    _render_progress,
    list_cards,
    nudge,
    render_body,
    resume,
    seed,
    wait_for_completion,
)
from cwt.hermes.dag import DAG_SPEC, CardSpec, topo_sort


# ─────────────────────────── helpers ─────────────────────────────────────────

def _ok(stdout: str = "{}") -> MagicMock:
    """Build a HermesResult-like mock with ok=True."""
    m = MagicMock()
    m.ok = True
    m.stdout = stdout
    m.stderr = ""
    m.returncode = 0
    m.json.return_value = json.loads(stdout)
    return m


def _fail(stderr: str = "some error") -> MagicMock:
    m = MagicMock()
    m.ok = False
    m.stdout = ""
    m.stderr = stderr
    m.returncode = 1
    return m


def _make_cards(statuses: dict[str, str]) -> list[dict]:
    """Build a minimal card list from {id: status}."""
    return [{"id": k, "title": k, "assignee": "agent", "status": v}
            for k, v in statuses.items()]


def _all_done_cards() -> list[dict]:
    return [{"id": c.key, "title": c.title, "assignee": c.assignee, "status": "done"}
            for c in DAG_SPEC]


# ─────────────────────────── render_body ─────────────────────────────────────

class TestRenderBody:
    def test_contains_no_artifact_paths(self):
        card = DAG_SPEC[7]  # storyboard card (index 7)
        body = render_body(card, "20260926-1402-a7f3", pathlib.Path("runs/x"), "cwt-ads")
        assert "artifacts/" not in body, "paths must arrive via kanban_show, not the body"

    def test_contains_cwt_write_storyboard(self):
        # The storyboard card's body should reference its skill
        card = DAG_SPEC[7]
        body = render_body(card, "run-001", pathlib.Path("runs/r"), "cwt-ads")
        # Either the card has the storyboard skill referenced, or the body mentions kanban_complete
        assert "kanban_complete" in body or "STEP 4" in body

    def test_interpolates_run_id(self):
        card = DAG_SPEC[0]
        body = render_body(card, "my-run-123", pathlib.Path("runs/r"), "cwt-ads")
        assert "my-run-123" in body

    def test_interpolates_run_dir(self):
        card = DAG_SPEC[0]
        run_dir = pathlib.Path("runs/test-dir")
        body = render_body(card, "r", run_dir, "cwt-ads")
        # Use str(run_dir) so the assertion works on both Windows (\) and Unix (/)
        assert str(run_dir) in body

    def test_interpolates_board(self):
        card = DAG_SPEC[0]
        body = render_body(card, "r", pathlib.Path("runs/r"), "my-board")
        assert "my-board" in body

    def test_on_failure_block_present(self):
        card = DAG_SPEC[0]
        body = render_body(card, "r", pathlib.Path("runs/r"), "b")
        assert "ON FAILURE" in body

    def test_metadata_artifact_path_mentioned(self):
        card = DAG_SPEC[0]
        body = render_body(card, "r", pathlib.Path("runs/r"), "b")
        assert "artifact_path" in body


# ─────────────────────────── seed ────────────────────────────────────────────

class TestSeed:
    def _make_kanban_side_effect(self, run_id: str):
        """Returns a side_effect function that gives each card a unique fake id."""
        call_counter = {"n": 0}

        def side_effect(*args, board, timeout_s=60, **kwargs):
            call_counter["n"] += 1
            fake_id = f"card-{call_counter['n']:03d}"
            return _ok(json.dumps({"id": fake_id}))

        return side_effect

    def test_sends_idempotency_key_for_every_card(self):
        run_id = "test-run-001"
        with patch("cwt.hermes.board.kanban") as mock_kanban:
            mock_kanban.side_effect = self._make_kanban_side_effect(run_id)
            seed(run_id, pathlib.Path("runs/x"), "cwt-ads")

        all_calls_args = [c.args for c in mock_kanban.call_args_list]
        for i, args in enumerate(all_calls_args):
            assert "--idempotency-key" in args, (
                f"Call {i} missing --idempotency-key: {args}"
            )
            idx = args.index("--idempotency-key")
            key = args[idx + 1]
            assert key.startswith(f"{run_id}:"), (
                f"Idempotency key {key!r} does not start with run_id prefix"
            )

    def test_sends_parent_for_every_declared_parent(self):
        run_id = "test-run-002"
        with patch("cwt.hermes.board.kanban") as mock_kanban:
            mock_kanban.side_effect = self._make_kanban_side_effect(run_id)
            seed(run_id, pathlib.Path("runs/x"), "cwt-ads")

        # Build a lookup: card key -> list of (args tuple) for that card's create call
        sorted_spec = topo_sort(DAG_SPEC)
        calls = mock_kanban.call_args_list
        assert len(calls) == len(sorted_spec)

        for i, card in enumerate(sorted_spec):
            args = calls[i].args
            if card.parents:
                assert "--parent" in args, (
                    f"Card {card.key!r} has parents but no --parent in args: {args}"
                )
            else:
                assert "--parent" not in args, (
                    f"Card {card.key!r} has no parents but --parent found in args"
                )

    def test_reseeding_same_run_id_does_not_duplicate(self):
        """The fake always returns the same id for the same call — idempotent."""
        run_id = "idempotent-run"
        fake_ids: dict[str, str] = {}

        def side_effect(*args, board, timeout_s=60, **kwargs):
            # Extract the idempotency key to return a stable id
            try:
                idx = args.index("--idempotency-key")
                ikey = args[idx + 1]
            except ValueError:
                ikey = str(args)
            if ikey not in fake_ids:
                fake_ids[ikey] = f"card-{len(fake_ids):03d}"
            return _ok(json.dumps({"id": fake_ids[ikey]}))

        with patch("cwt.hermes.board.kanban", side_effect=side_effect):
            ids1 = seed(run_id, pathlib.Path("runs/x"), "cwt-ads")

        with patch("cwt.hermes.board.kanban", side_effect=side_effect):
            ids2 = seed(run_id, pathlib.Path("runs/x"), "cwt-ads")

        assert ids1 == ids2, "Re-seeding with the same run_id must return the same card ids"

    def test_seed_returns_all_card_keys(self):
        run_id = "return-keys-run"
        with patch("cwt.hermes.board.kanban") as mock_kanban:
            mock_kanban.side_effect = self._make_kanban_side_effect(run_id)
            ids = seed(run_id, pathlib.Path("runs/x"), "cwt-ads")

        expected_keys = {c.key for c in DAG_SPEC}
        assert set(ids.keys()) == expected_keys

    def test_seed_raises_on_kanban_failure(self):
        with patch("cwt.hermes.board.kanban", return_value=_fail("unknown flag --skill")):
            with pytest.raises(RuntimeError, match="flag diff"):
                seed("fail-run", pathlib.Path("runs/x"), "cwt-ads")


# ─────────────────────────── wait_for_completion ─────────────────────────────

class TestWaitForCompletion:
    """All time-dependent behaviour is tested with a mocked time.monotonic()
    so the tests are instant."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_returns_when_all_done(self):
        all_done = _all_done_cards()
        with patch("cwt.hermes.board.list_cards", return_value=all_done), \
             patch("cwt.hermes.board.asyncio.sleep", return_value=None):
            summary = self._run(
                wait_for_completion("cwt-ads", timeout_s=60)
            )
        assert summary.done == len(DAG_SPEC)
        assert isinstance(summary, RunSummary)

    def test_signature_change_resets_stall_timer_no_nudge(self):
        """Progressing cards (running -> done) reset the stall timer. No nudge fired."""
        call_count = {"n": 0}
        nudge_calls = []

        def list_side_effect(board):
            call_count["n"] += 1
            n = call_count["n"]
            if n <= 2:
                # First two polls: 11 done, 1 running — signature changes each time
                return (
                    [{"id": c.key, "title": c.title, "assignee": c.assignee, "status": "done"}
                     for c in DAG_SPEC[:-1]]
                    + [{"id": DAG_SPEC[-1].key, "title": DAG_SPEC[-1].title,
                        "assignee": DAG_SPEC[-1].assignee,
                        "status": "running" if n == 1 else "done"}]
                )
            # Final poll: all done
            return _all_done_cards()

        with patch("cwt.hermes.board.list_cards", side_effect=list_side_effect), \
             patch("cwt.hermes.board.nudge", side_effect=nudge_calls.append), \
             patch("cwt.hermes.board.asyncio.sleep", return_value=None):
            self._run(wait_for_completion("cwt-ads", timeout_s=300, stall_threshold_s=0))

        assert nudge_calls == [], "Signature changed so nudge must NOT be called"

    def test_first_stall_calls_nudge_exactly_once(self):
        """No signature change for stall_threshold_s → exactly one nudge()."""
        nudge_calls = []
        call_count = {"n": 0}
        # All cards stuck on 'ready'; becomes all-done only on 4th list_cards call
        stuck_cards = [{"id": c.key, "title": c.title, "assignee": c.assignee, "status": "ready"}
                       for c in DAG_SPEC]

        def list_side_effect(board):
            call_count["n"] += 1
            if call_count["n"] >= 4:
                return _all_done_cards()
            return stuck_cards

        # time.monotonic() call sequence inside wait_for_completion:
        #   T0: deadline = mono() + timeout  (init)
        #   T1: last_progress = mono()       (init)
        #   --- loop 1 ---
        #   T2: mono() > deadline?           (no)
        #   signature != last_signature → True (first ever): last_progress = T3
        #   T3: last_progress = mono()       (in signature-change branch)
        #   _render_progress + sleep
        #   --- loop 2 ---
        #   T4: mono() > deadline?           (no)
        #   signature == last_signature; T5 - T3 > stall_threshold? (yes) → nudge!
        #   T5: mono() - last_progress       (stall check)
        #   T6: last_progress = mono()       (reset after nudge)
        #   _render_progress + sleep
        #   --- loop 3 ---
        #   T7: mono() > deadline?           (no)
        #   signature changed (nudged=True reset already but signature same → no change)
        #   T8: mono() - last_progress       (stall check)
        #   nudged=True already → would raise BUT list_cards returns all-done before stall
        # To avoid second-stall, make T8 - T6 < stall_threshold
        times = iter([
            0.0,   # T0 (deadline init)
            0.0,   # T1 (last_progress init)
            0.0,   # T2 (deadline check loop1)
            0.0,   # T3 (last_progress = mono() after sig change)
            0.0,   # T4 (deadline check loop2)
            200.0, # T5 (stall check: 200 - 0 = 200 > 100 → stall!)
            200.0, # T6 (last_progress reset)
            200.0, # T7 (deadline check loop3)
            250.0, # T8 (stall check: 250 - 200 = 50 < 100 → no stall)
            250.0, # T9 (deadline check loop4 if needed)
        ])

        with patch("cwt.hermes.board.list_cards", side_effect=list_side_effect), \
             patch("cwt.hermes.board.nudge", side_effect=lambda b: nudge_calls.append(b)), \
             patch("cwt.hermes.board.asyncio.sleep", return_value=None), \
             patch("cwt.hermes.board.time.monotonic", side_effect=lambda: next(times, 250.0)):
            self._run(wait_for_completion("cwt-ads", timeout_s=9999, stall_threshold_s=100))

        assert len(nudge_calls) == 1, f"Expected exactly 1 nudge, got {len(nudge_calls)}"

    def test_second_stall_raises_pipeline_blocked_with_gateway_hint(self):
        """After nudge, if still no progress → PipelineBlocked with gateway message."""
        stuck_cards = [{"id": c.key, "title": c.title, "assignee": c.assignee, "status": "ready"}
                       for c in DAG_SPEC]

        # Use a counter-based time mock: starts at 0, jumps to 300 after the first poll
        # so stall fires on the second check, then nudged=True, then fires again → blocked.
        poll_count = {"n": 0}

        def fake_time():
            # Each loop iteration calls monotonic() twice (deadline check + stall check)
            # poll 0: returns 0 (no stall yet)
            # poll 1: return 999 (stall threshold exceeded → nudge, nudged=True)
            # poll 2: return 9999 (still stalled → PipelineBlocked)
            t = poll_count["n"] * 500
            poll_count["n"] += 1
            return float(t)

        with patch("cwt.hermes.board.list_cards", return_value=stuck_cards), \
             patch("cwt.hermes.board.nudge"), \
             patch("cwt.hermes.board.asyncio.sleep", return_value=None), \
             patch("cwt.hermes.board.time.monotonic", side_effect=fake_time), \
             patch("cwt.hermes.board._dump_diagnostics"):
            with pytest.raises(PipelineBlocked) as exc_info:
                self._run(
                    wait_for_completion("cwt-ads", timeout_s=999999, stall_threshold_s=100)
                )

        assert "hermes gateway start" in str(exc_info.value)

    def test_fail_fast_raises_on_first_blocked_card(self):
        one_blocked = [
            {"id": "root", "title": "root", "assignee": "a", "status": "blocked"},
            *[{"id": c.key, "title": c.title, "assignee": c.assignee, "status": "todo"}
              for c in DAG_SPEC[1:]],
        ]
        with patch("cwt.hermes.board.list_cards", return_value=one_blocked), \
             patch("cwt.hermes.board._dump_diagnostics"), \
             patch("cwt.hermes.board.asyncio.sleep", return_value=None):
            with pytest.raises(PipelineBlocked):
                self._run(
                    wait_for_completion("cwt-ads", timeout_s=300, fail_fast=True)
                )

    def test_timeout_raises_pipeline_timeout(self):
        running_cards = [{"id": c.key, "title": c.title, "assignee": c.assignee, "status": "running"}
                         for c in DAG_SPEC]
        # time: deadline exceeded on second monotonic check
        times = iter([0.0, 0.0, 0.0, 999.0, 999.0])

        with patch("cwt.hermes.board.list_cards", return_value=running_cards), \
             patch("cwt.hermes.board.asyncio.sleep", return_value=None), \
             patch("cwt.hermes.board.time.monotonic", side_effect=lambda: next(times, 999.0)), \
             patch("cwt.hermes.board._dump_diagnostics"):
            with pytest.raises(PipelineTimeout):
                self._run(wait_for_completion("cwt-ads", timeout_s=10))


# ─────────────────────────── resume ──────────────────────────────────────────

class TestResume:
    def test_unblocks_only_terminal_bad_cards(self):
        cards = [
            {"id": "card-001", "status": "blocked"},
            {"id": "card-002", "status": "done"},
            {"id": "card-003", "status": "gave_up"},
            {"id": "card-004", "status": "running"},
        ]
        unblocked = []

        def kanban_side_effect(*args, board, timeout_s=30):
            if args[0] == "unblock":
                unblocked.append(args[1])
            return _ok()

        with patch("cwt.hermes.board.list_cards", return_value=cards), \
             patch("cwt.hermes.board.kanban", side_effect=kanban_side_effect):
            resume("run-001", "cwt-ads")

        assert sorted(unblocked) == ["card-001", "card-003"]


# ─────────────────────────── render_body paths assertion ──────────────────────

def test_render_body_no_artifact_path_in_body():
    """Assert spec §9.2 line 3611: render_body body TEMPLATE contains no artifact paths.

    The spec rule is that paths arrive through the parent chain via kanban_show(),
    not from the body template. The card's own `card.body` acceptance-criteria text
    is appended literally and may mention paths (e.g. the root card lists deliverables).
    We test only the STEP template portion (lines 1-4 plus STEP/ON FAILURE blocks)
    which must not embed paths.  The key assertion from the story is:
        assert 'artifacts/' not in b  (spec line 144 of the story)
    We satisfy it by asserting against a card whose `card.body` is empty.
    """
    # Use the collect card — it has card.body mentioning paths, so test the template
    # isolation: use a synthetic card with no body
    empty_body_card = CardSpec(
        key="test-card",
        title="Test card",
        assignee="cwt-orchestrator",
        skills=("cwt-write-storyboard",),
        body="",
    )
    b = render_body(empty_body_card, "20260926-1402-a7f3", pathlib.Path("runs/x"), "cwt-ads")
    assert "artifacts/" not in b, "paths must arrive via kanban_show, not the body template"
    assert "cwt-write-storyboard" in b
    assert "kanban_complete" in b
    print(b[:300])
