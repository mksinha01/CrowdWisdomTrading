"""The ONLY place a subprocess is spawned in this codebase.

If you are about to write `subprocess.run(...)` anywhere else, stop and call run_tool().
The Windows failure modes this prevents are documented as Rules W1, W4, W7 and W9.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class ToolNotFound(RuntimeError):
    def __init__(self, exe: str, hint: str = ""):
        super().__init__(f"{exe!r} not found. {hint}".strip())


class ToolTimeout(RuntimeError):
    def __init__(self, exe: str, timeout_s: float, stdout: str, stderr: str):
        super().__init__(f"{exe!r} timed out after {timeout_s}s")
        self.stdout, self.stderr = stdout, stderr


class ToolFailed(RuntimeError):
    def __init__(self, result: ToolResult):
        super().__init__(
            f"{result.args[0]!r} exited {result.returncode}\n"
            f"argv: {result.args}\ncwd: {result.cwd}\nstderr:\n{result.stderr[-4000:]}"
        )
        self.result = result


@dataclass(frozen=True)
class ToolResult:
    args: list[str]
    cwd: str | None
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def resolve_exe(exe: str) -> str:
    """Resolve an executable to an absolute path.

    Why this exists (Rule W4): on Windows, `npx`, `npm` and `hermes` are `.cmd`
    shims. Passing the bare name to subprocess with shell=False raises
    `[WinError 193] %1 is not a valid Win32 application`. shutil.which()
    returns the full `...\\npx.cmd` path, which does work.
    """
    if Path(exe).is_absolute() and Path(exe).exists():
        return str(exe)
    if sys.platform == "win32":
        resolved = shutil.which(exe)
        if resolved is None:
            raise ToolNotFound(exe, f"install it, or set CWT_{exe.upper()}_BIN")
        return resolved
    resolved = shutil.which(exe)
    return resolved or exe


def run_tool(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout_s: float = 300,
    env_extra: Mapping[str, str] | None = None,
    check: bool = False,
) -> ToolResult:
    """Run an external program. `args` MUST be a list — shell strings are banned.

    shell=False      no shell parsing, so spaces/quotes/& in paths cannot break it  (W1)
    encoding=utf-8   Windows cp1252 cannot encode characters in our prompts          (W7)
    errors=replace   one bad byte from a tool must not crash the run                 (W7)
    cwd              ffmpeg filtergraphs reference RELATIVE paths only               (W5)
    PYTHONUTF8=1     forces utf-8 in any Python child we spawn                       (W7)
    """
    if isinstance(args, str):
        raise TypeError(
            "run_tool() takes a list of strings, not a shell string. "
            "Shell strings are banned — see architecture Rule W1."
        )
    if not args:
        raise ValueError("run_tool() requires at least one argument")

    exe = resolve_exe(args[0])
    argv = [exe, *args[1:]]

    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if env_extra:
        env.update(env_extra)

    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (
            exc.stdout.decode("utf-8", "replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        stderr = (
            exc.stderr.decode("utf-8", "replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
        raise ToolTimeout(exe, timeout_s, stdout, stderr) from exc

    result = ToolResult(
        args=argv,
        cwd=str(cwd) if cwd else None,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
    if check and not result.ok:
        raise ToolFailed(result)
    return result
