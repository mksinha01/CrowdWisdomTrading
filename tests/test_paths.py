"""Tests for path utilities and RunPaths (Story S02, Rule W9)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from cwt.util.paths import RunPaths, long_path, mint_run_id


def test_mint_run_id():
    """mint_run_id produces short YYYYMMDD-HHMM-<4hex> identifier."""
    run_id1 = mint_run_id()
    run_id2 = mint_run_id()

    pattern = r"^\d{8}-\d{4}-[0-9a-f]{4}$"
    assert re.match(pattern, run_id1)
    assert re.match(pattern, run_id2)
    # Consecutive calls should have different hex suffixes
    assert run_id1 != run_id2


def test_long_path_handling():
    """long_path handles path length escaping on Windows."""
    short = Path("short/path/file.txt")
    assert long_path(short) == str(short)

    long_str = "C:\\" + ("a" * 260)
    res = long_path(Path(long_str))
    if sys.platform == "win32":
        assert res.startswith("\\\\?\\")
        # Idempotence: already prefixed path
        assert long_path(Path(res)) == res
    else:
        assert res == long_str


def test_run_paths_layout_and_ensure(tmp_path):
    """RunPaths ensures all required directories are created without deep nesting."""
    run_dir = tmp_path / "runs" / "20260927-1030-a1b2"
    paths = RunPaths(run_dir)

    assert paths.run_dir == run_dir
    assert paths.artifacts == run_dir / "artifacts"
    assert paths.assets == run_dir / "assets"
    assert paths.cache == run_dir / "assets" / "cache"
    assert paths.render == run_dir / "render"
    assert paths.failures == run_dir / "failures"
    assert paths.ledger == run_dir / "llm_ledger.jsonl"
    assert paths.provenance == run_dir / "artifacts" / "provenance.json"

    # Before ensure, dirs don't exist
    assert not paths.artifacts.exists()

    ensured = paths.ensure()
    assert ensured is paths

    # After ensure, all directories exist
    for d in (paths.artifacts, paths.assets, paths.cache, paths.render, paths.failures):
        assert d.exists()
        assert d.is_dir()
