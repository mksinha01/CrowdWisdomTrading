"""The ONLY place the hermes binary is invoked.

Every kanban interaction in this codebase goes through run_hermes(). That makes
Hermes version drift a one-file fix rather than a rewrite (Rule R3).
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from functools import lru_cache

from cwt.util.subproc import ToolFailed, run_tool

DEFAULT_TIMEOUT = 120.0


@dataclass(frozen=True)
class HermesResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def json(self):
        try:
            return json.loads(self.stdout)
        except Exception:
            import re
            m = re.search(r"Created\s+(t_[0-9a-fA-F]+)", self.stdout)
            if m:
                return {"id": m.group(1)}
            raise


@lru_cache(maxsize=1)
def hermes_bin() -> str:
    override = os.getenv("HERMES_BIN", "").strip()
    if override:
        return override
    found = shutil.which("hermes")
    if not found:
        raise RuntimeError(
            "The `hermes` binary was not found on PATH.\n"
            "Install it:  iex (irm https://hermes-agent.nousresearch.com/install.ps1)   [Windows]\n"
            "             curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash   [Linux/macOS]\n"
            "Or set HERMES_BIN to its absolute path."
        )
    return found


def run_hermes(args: list[str], *, timeout_s: float = DEFAULT_TIMEOUT,
               check: bool = False, input: str | None = None) -> HermesResult:
    if isinstance(args, str):
        raise TypeError("run_hermes() takes a list, not a shell string (Rule W1)")
    result = run_tool([hermes_bin(), *args], timeout_s=timeout_s, input=input)
    out = HermesResult(result.returncode, result.stdout, result.stderr)
    if check and not out.ok:
        raise ToolFailed(result)
    return out


def hermes_version() -> str:
    return run_hermes(["--version"], timeout_s=30).stdout.strip()


def kanban(*args: str, board: str, timeout_s: float = DEFAULT_TIMEOUT,
           input: str | None = None) -> HermesResult:
    return run_hermes(["kanban", "--board", board, *args], timeout_s=timeout_s, input=input)


def supported_flags() -> set[str]:
    """Parse `hermes kanban create --help` for the flags we depend on.

    Doctor calls this and FAILS if any flag we rely on is missing. That converts
    'mysterious silent misbehaviour after a Hermes upgrade' into 'clear preflight
    error naming the missing flag'.
    """
    out = run_hermes(["kanban", "create", "--help"], timeout_s=30)
    text = out.stdout + out.stderr
    flags: set[str] = set()
    for token in text.replace(",", " ").split():
        if token.startswith("--"):
            flags.add(token.rstrip(".,;:").split("=")[0])
    return flags
