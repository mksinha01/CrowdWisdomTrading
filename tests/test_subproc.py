"""Tests for subprocess chokepoint and executable resolution (Story S02, Rules W1, W4, W7)."""
from __future__ import annotations

import sys

import pytest

from cwt.util.subproc import (
    ToolFailed,
    ToolNotFound,
    ToolResult,
    ToolTimeout,
    resolve_exe,
    run_tool,
)


def test_run_tool_rejects_str_args_rule_w1():
    """Rule W1: run_tool MUST raise TypeError if args is a string (banning shell strings)."""
    with pytest.raises(
        TypeError, match="run_tool\\(\\) takes a list of strings, not a shell string"
    ):
        run_tool("python -c 'print(1)'")  # type: ignore[arg-type]


def test_run_tool_rejects_empty_args():
    """run_tool requires at least one argument."""
    with pytest.raises(ValueError, match="run_tool\\(\\) requires at least one argument"):
        run_tool([])


def test_resolve_exe_sys_executable():
    """resolve_exe resolves existing absolute executables successfully."""
    resolved = resolve_exe(sys.executable)
    assert resolved == sys.executable


def test_resolve_exe_not_found():
    """resolve_exe raises ToolNotFound when executable cannot be located."""
    with pytest.raises(ToolNotFound, match="not found"):
        resolve_exe("non_existent_binary_xyz_12345")


def test_run_tool_timeout():
    """run_tool raises ToolTimeout when process exceeds timeout_s."""
    with pytest.raises(ToolTimeout) as exc_info:
        run_tool(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout_s=0.5,
        )
    assert "timed out after 0.5s" in str(exc_info.value)
    assert hasattr(exc_info.value, "stdout")
    assert hasattr(exc_info.value, "stderr")


def test_run_tool_success_and_utf8_rule_w7():
    """Rule W7: run_tool passes utf-8 environment and decodes utf-8 characters properly."""
    res = run_tool([
        sys.executable,
        "-c",
        "import os, sys; print(f'dash:—;PYTHONUTF8:{os.environ.get(\"PYTHONUTF8\")}')",
    ])
    assert isinstance(res, ToolResult)
    assert res.ok is True
    assert res.returncode == 0
    assert "dash:—" in res.stdout
    assert "PYTHONUTF8:1" in res.stdout


def test_run_tool_check_failed():
    """run_tool raises ToolFailed when check=True and process returns non-zero."""
    with pytest.raises(ToolFailed) as exc_info:
        run_tool(
            [sys.executable, "-c", "import sys; sys.stderr.write('fatal boom'); sys.exit(7)"],
            check=True,
        )
    err = exc_info.value
    assert err.result.returncode == 7
    assert err.result.ok is False
    assert "fatal boom" in err.result.stderr


def test_run_tool_stdin_input():
    """run_tool passes stdin string properly to subprocess."""
    res = run_tool(
        [sys.executable, "-c", "import sys; print('GOT:' + sys.stdin.read().strip())"],
        input="hello from stdin",
    )
    assert res.ok is True
    assert res.stdout.strip() == "GOT:hello from stdin"
