"""Preflight. Run before anything spends money.

Every check either passes, warns, or fails with an ACTIONABLE message. A doctor
that says "something is wrong" is useless; one that names the missing flag, the
bad model slug and the exact install command is the difference between a
five-minute fix and an hour of guessing.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from cwt.config import Settings

console = Console()

REQUIRED_KANBAN_FLAGS = {
    "--assignee",
    "--body",
    "--parent",
    "--idempotency-key",
    "--workspace",
    "--priority",
    "--max-runtime",
    "--max-retries",
    "--skill",
    "--json",
}


@dataclass
class Check:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str = ""
    hint: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == "fail"]


def _check_python() -> Check:
    if sys.version_info < (3, 11):
        return Check(
            "python",
            "fail",
            f"{sys.version_info[:2]}",
            "Python 3.11+ required",
        )
    try:
        ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    except AttributeError:
        ver = ".".join(str(x) for x in sys.version_info[:3])
    return Check(
        "python",
        "ok",
        ver,
    )


def _check_ffmpeg() -> Check:
    from cwt.video.ffmpeg_bin import ffmpeg_path, ffmpeg_version, ffprobe_path

    try:
        path = ffmpeg_path()
        version = ffmpeg_version()
    except Exception as exc:
        return Check(
            "ffmpeg",
            "fail",
            str(exc),
            "pip install imageio-ffmpeg, or set CWT_FFMPEG_BIN to an absolute path",
        )
    detail = f"{path} ({version})"
    try:
        ffprobe_path()
    except Exception:
        return Check(
            "ffmpeg",
            "warn",
            f"{detail}; ffprobe missing",
            "Duration verification will fall back to parsing `ffmpeg -i` stderr. "
            "Verification is NOT skipped — see Rule W3.",
        )
    return Check("ffmpeg", "ok", detail)


def _check_hermes() -> Check:
    from cwt.hermes.cli import hermes_bin, hermes_version, supported_flags

    try:
        binary = hermes_bin()
        version = hermes_version()
    except Exception as exc:
        return Check(
            "hermes",
            "fail",
            str(exc),
            "Windows:  iex (irm https://hermes-agent.nousresearch.com/install.ps1)\n"
            "Linux:    curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash",
        )

    try:
        flags = supported_flags()
    except Exception as exc:
        return Check(
            "hermes",
            "fail",
            f"v{version} at {binary} — could not check flags: {exc}",
            "Ensure hermes is working properly.",
        )

    missing = REQUIRED_KANBAN_FLAGS - flags
    if missing:
        return Check(
            "hermes",
            "fail",
            f"v{version} at {binary} — missing kanban flags: {sorted(missing)}",
            "Your Hermes version differs from the one this spec targets. "
            "Either upgrade Hermes, or update src/cwt/hermes/dag.py and board.py to "
            "match your version's flag spelling. All Hermes coupling is in "
            "src/cwt/hermes/ — this is a contained fix.",
        )
    return Check("hermes", "ok", f"v{version} at {binary}")


def _check_worker_model() -> Check:
    """Warn — loudly — when the Hermes WORKER model may be too small.

    This is the check that prevents the most likely reviewer confusion: our
    LLM_MODEL_* vars do not configure the workers.
    """
    from cwt.hermes.cli import run_hermes

    hint = (
        "Run `hermes model` and pick a model with >=64k context. "
        "In --offline mode the workers still run on this model."
    )
    try:
        result = run_hermes(["config", "get", "model"], timeout_s=30)
        model = result.stdout.strip()
        if not result.ok or not model:
            return Check("worker model", "warn", "not set", hint)
    except Exception as exc:
        return Check("worker model", "warn", f"could not check model: {exc}", hint)
    return Check("worker model", "ok", f"{model}  (verify >=64k context)")


def _check_profiles() -> Check:
    """Verify every DAG_SPEC assignee resolves to a created profile (Rule K2)."""
    from cwt.hermes.cli import run_hermes
    from cwt.hermes.dag import DAG_SPEC

    hint = "Run `cwt bootstrap` to create the nine CWT profiles."
    try:
        result = run_hermes(["profile", "list"], timeout_s=30)
        if not result.ok:
            return Check(
                "profiles",
                "fail",
                f"hermes profile list failed: {result.stderr or result.stdout}",
                hint,
            )
        output = result.stdout
    except Exception as exc:
        return Check("profiles", "fail", str(exc), hint)

    tokens = set(output.replace(",", " ").replace("*", " ").split())
    lines = {line.strip() for line in output.splitlines() if line.strip()}
    found = tokens | lines

    required = {c.assignee for c in DAG_SPEC}
    missing = required - found
    if missing:
        return Check("profiles", "fail", f"missing profiles: {sorted(missing)}", hint)

    cwt_profiles = {p for p in found if p.startswith("cwt-")}
    count = len(cwt_profiles) if cwt_profiles else len(required)
    return Check("profiles", "ok", f"{count} CWT profiles found")


def _check_backend_chain(settings: Settings) -> Check:
    from cwt.video.backend import build_chain

    hint = (
        "VIDEO_BACKEND_CHAIN must end with local_ffmpeg. That invariant is "
        "what makes the pipeline unable to hard-fail on video."
    )
    try:
        chain = build_chain(settings)
    except Exception as exc:
        return Check(
            "backend chain",
            "fail",
            str(exc),
            hint,
        )

    tail = chain[-1].name
    if tail not in ("local_ffmpeg", "fixture"):
        return Check(
            "backend chain",
            "fail",
            f"last backend is {tail!r}, which is not guaranteed available",
            hint,
        )
    lines = []
    for backend in chain:
        avail = backend.available()
        mark = "[green]OK[/green]" if avail.available else "[dim]--[/dim]"
        lines.append(f"{mark} {backend.name:<14} {avail.reason}")
    return Check("backend chain", "ok", "\n".join(lines))


def _check_llm_models(settings: Settings) -> Check:
    """Validate the configured slugs against /v1/models.

    Model slugs drift. A spec that hardcodes a slug it cannot verify is a spec
    that breaks in three weeks. This converts 'mysterious 404 mid-run' into
    'clear preflight error before any spend'.
    """
    import httpx
    from cwt.clients.llm import PROFILES

    profile = PROFILES.get(settings.llm_provider)
    if not profile:
        return Check(
            "llm models",
            "fail",
            f"unknown provider {settings.llm_provider!r}",
            "Set LLM_PROVIDER to 'openrouter' or 'nvidia' in .env",
        )

    key = os.getenv(profile.api_key_env, "") or getattr(
        settings, f"{profile.name}_api_key", ""
    )
    if not key:
        return Check(
            "llm models",
            "fail",
            f"{profile.api_key_env} not set",
            "Set it in .env",
        )

    try:
        resp = httpx.get(
            f"{profile.base_url}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        )
        resp.raise_for_status()
        available = {m["id"] for m in resp.json().get("data", [])}
    except Exception as exc:
        return Check(
            "llm models",
            "warn",
            f"could not list models: {exc}",
            "Model validation skipped. A bad slug will surface as a 404 mid-run.",
        )

    wanted = {settings.model_cheap, settings.model_strong, *settings.model_fallbacks}
    missing = wanted - available
    if missing:
        return Check(
            "llm models",
            "fail",
            f"not available: {sorted(missing)}",
            "Fix LLM_MODEL_* in .env. Note that OpenRouter slugs are "
            "`vendor/model` and NVIDIA slugs are often `vendor/model` too.",
        )
    return Check("llm models", "ok", f"{len(wanted)} slugs verified")


def _check_gateway(settings: Settings) -> Check:
    """The dispatcher runs in the gateway by default. If it is not up, every card
    sits on `ready` forever and the run appears to hang."""
    from cwt.hermes.cli import run_hermes

    hint = (
        "Start it with `hermes gateway start`, or the board will never dispatch. "
        "cwt run will detect the stall and tell you."
    )
    try:
        result = run_hermes(["kanban", "dispatch", "--dry-run"], timeout_s=30)
        if not result.ok and "gateway" in (result.stderr + result.stdout).lower():
            return Check("gateway", "warn", "not running", hint)
    except Exception as exc:
        return Check("gateway", "warn", f"could not check gateway: {exc}", hint)
    return Check("gateway", "ok", "dispatcher reachable")


def run_doctor(json_output: bool = False, settings: Settings | None = None) -> int:
    if settings is None:
        from cwt.config import Settings

        settings = Settings.from_env()

    checks = [
        _check_python(),
        _check_ffmpeg(),
        _check_hermes(),
        _check_worker_model(),
        _check_profiles(),
        _check_backend_chain(settings),
        _check_llm_models(settings),
    ]
    if settings.engine_defaults_to_hermes:
        checks.append(_check_gateway(settings))

    report = Report(checks=checks)

    if json_output:
        import json as _json

        console.print_json(_json.dumps([c.__dict__ for c in report.checks]))
    else:
        for check in report.checks:
            icon = {
                "ok": "[green]OK  [/green]",
                "warn": "[yellow]WARN[/yellow]",
                "fail": "[red]FAIL[/red]",
            }.get(check.status, f"[{check.status}]")
            console.print(f"{icon} {check.name:<16} {check.detail}")
            if check.hint and check.status != "ok":
                for line in check.hint.splitlines():
                    console.print(f"     [dim]{line}[/dim]")

        if report.failed:
            console.print(
                f"\n[red]{len(report.failed)} check(s) failed. Fix these before running.[/red]"
            )
            return 1
        console.print("\n[green]All checks passed.[/green]")
        return 0

    return 1 if report.failed else 0
