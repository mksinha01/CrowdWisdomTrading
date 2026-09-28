"""cwt — the command line.

EXIT CODES (a reviewer can tell what happened without reading a log):
  0  success
  1  configuration or preflight error
  2  pipeline timeout
  3  pipeline blocked (a card gave up)
  4  LLM budget exceeded
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

from rich.console import Console

from cwt.config import Settings
from cwt.util.paths import RunPaths, mint_run_id

console = Console()
EXIT_OK, EXIT_CONFIG, EXIT_TIMEOUT, EXIT_BLOCKED, EXIT_BUDGET = 0, 1, 2, 3, 4


def _banner(settings: Settings, paths: RunPaths, offline: bool, engine: str) -> None:
    """The config echo. This is the first debugging tool in production and it
    costs nothing to print."""
    try:
        from cwt.video.ffmpeg_bin import ffmpeg_path, ffmpeg_version
        ff = f"{ffmpeg_path()}  ({ffmpeg_version()})"
    except Exception as exc:
        ff = f"[red]NOT FOUND: {exc}[/red]"
    try:
        from cwt.hermes.cli import hermes_bin, hermes_version
        hm = f"{hermes_bin()}  (v{hermes_version()})"
    except Exception as exc:
        hm = f"[red]NOT FOUND: {exc}[/red]"

    try:
        run_dir = getattr(paths, "run_dir", paths)
        run_name = getattr(run_dir, "name", str(run_dir))

        console.print("[bold cyan]=== CWT Video Ads Agent ===[/bold cyan]")
        console.print(f"run_id       {run_name}")
        console.print(f"run_dir      {run_dir}")
        console.print(f"engine       {engine}")
        console.print(f"offline      {offline}")
        console.print(f"provider     {getattr(settings, 'llm_provider', '')}  "
                      f"(cheap={getattr(settings, 'model_cheap', '')}  strong={getattr(settings, 'model_strong', '')})")
        video_chain = getattr(settings, "video_backend_chain", [])
        console.print(f"video        {','.join(video_chain)}")
        console.print(f"ffmpeg       {ff}")
        console.print(f"hermes       {hm}")
        console.print(f"board        {getattr(settings, 'board', '')}")
        console.print(f"budget       ${getattr(settings, 'max_usd', 0.0):.2f}")
    except Exception as exc:
        console.print(f"[red]Error rendering banner: {exc}[/red]")


async def _cmd_run(args: argparse.Namespace) -> int:
    from cwt.engine import run_pipeline

    settings = Settings.from_env()
    if getattr(args, "backend", None):
        settings = settings.with_backend_chain([args.backend])
    run_id = getattr(args, "run_id", None) or mint_run_id()
    paths = RunPaths(Path(getattr(args, "run_dir", None) or "runs") / run_id).ensure()

    if getattr(args, "resume", False):
        from cwt.hermes.board import resume
        resume(run_id, settings.board)

    if getattr(args, "engine", "hermes") == "hermes" and not getattr(args, "offline", False):
        pass  # doctor has already verified the gateway is reachable
    _banner(settings, paths, getattr(args, "offline", False), getattr(args, "engine", "hermes"))

    try:
        summary = await run_pipeline(
            settings=settings,
            paths=paths,
            engine=getattr(args, "engine", "hermes"),
            offline=getattr(args, "offline", False),
            record_pacing=getattr(args, "record_pacing", False),
            force_stage=getattr(args, "force_stage", []),
        )
    except Exception as exc:
        name = type(exc).__name__
        console.print(f"[red]{name}: {exc}[/red]")
        return {"PipelineTimeout": EXIT_TIMEOUT, "PipelineBlocked": EXIT_BLOCKED,
                "BudgetExceeded": EXIT_BUDGET}.get(name, EXIT_CONFIG)

    console.print(f"[bold green]Done.[/bold green] {summary['done']} stages, "
                  f"${summary['cost_usd']:.4f}, output: {summary['output']}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cwt", description="CWT Video Ads Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the full pipeline end to end")
    p_run.add_argument("--engine", choices=["hermes", "local"], default="hermes")
    p_run.add_argument("--offline", action="store_true",
                       help="Use recorded fixtures only. Zero API spend.")
    p_run.add_argument("--backend", help="Pin one video backend (e.g. local_ffmpeg)")
    p_run.add_argument("--run-id", help="Reuse an existing run id")
    p_run.add_argument("--run-dir", default="runs")
    p_run.add_argument("--resume", action="store_true", help="Unblock and continue a prior run")
    p_run.add_argument("--record-pacing", action="store_true",
                       help="Insert deliberate pauses so the dashboard recording is watchable")
    p_run.add_argument("--force-stage", action="append", default=[],
                       help="Re-run this stage even if its artifact is valid. Repeatable.")
    p_run.add_argument("--fail-fast", action="store_true")
    p_run.add_argument("--timeout", type=int)

    p_doctor = sub.add_parser("doctor", help="Preflight: binaries, APIs, models, Hermes flags")
    p_doctor.add_argument("--json", action="store_true")

    p_seed = sub.add_parser("seed", help="Create the kanban cards without running")
    p_seed.add_argument("--run-id", required=True)

    sub.add_parser("status", help="Show the current board")
    sub.add_parser("bootstrap", help="Create the Hermes profiles, skills and plugin")
    sub.add_parser("clean", help="Remove runs/ except the most recent")

    args = parser.parse_args(argv)

    if args.command == "run":
        return asyncio.run(_cmd_run(args))
    if args.command == "doctor":
        from cwt.doctor import run_doctor
        return run_doctor(json_output=args.json)
    if args.command == "seed":
        from cwt.hermes.board import seed
        settings = Settings.from_env()
        paths = RunPaths(Path("runs") / args.run_id).ensure()
        ids = seed(args.run_id, paths.run_dir, settings.board)
        console.print_json(json.dumps(ids))
        return EXIT_OK
    if args.command == "status":
        from cwt.hermes.board import _render_progress, list_cards
        try:
            cards = list_cards(Settings.from_env().board)
        except Exception:
            cards = []
        _render_progress(cards)
        return EXIT_OK
    if args.command == "bootstrap":
        from cwt.bootstrap import install_hermes_assets
        return install_hermes_assets()
    if args.command == "clean":
        runs = sorted(Path("runs").glob("20*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in runs[1:]:
            shutil.rmtree(old, ignore_errors=True)
            console.print(f"removed {old}")
        return EXIT_OK
    return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
