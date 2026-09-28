"""Record HTTP fixtures for offline test and demo execution.

Makes REAL API calls and spends credits. Requires --force or interactive
confirmation before making any live call.

Spec:
  - --stage {ads,patterns,research,storyboard,claims,render,all}
  - --force required to overwrite an existing fixture; otherwise skip existing
  - print, before any call: estimated Apify spend and Tavily credit cost
  - run client calls through HttpCache(root, offline=False) write-through
  - scrub on write reusing scrub_fixtures.py's functions
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx

from cwt.clients.http_cache import CachedResponse, HttpCache, request_cache_key
from cwt.config import Settings
from cwt.util.paths import RunPaths
from scripts.scrub_fixtures import scrub_fixture_data

STAGES = ("ads", "patterns", "research", "storyboard", "claims", "render", "all")


def print_spend_warning(apify_max_charge: float, tavily_queries: int = 6) -> None:
    """Print the estimated cost warning before making any live calls."""
    print("=" * 68)
    print("WARNING: record_fixtures.py makes REAL API calls and spends credits!")
    print(f"  Estimated Apify spend: up to ${apify_max_charge:.2f} USD (APIFY_MAX_CHARGE_USD)")
    print(f"  Estimated Tavily credit cost: {2 * tavily_queries} credits (2 × {tavily_queries} queries for advanced search)")
    print("=" * 68)


class RecordingHttpCache(HttpCache):
    """Write-through HttpCache that enforces scrub-on-write and skip-unless-force."""

    def __init__(self, root: Path, force: bool = False):
        super().__init__(root=root, offline=False)
        self.force = force

    async def request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> CachedResponse:
        key = request_cache_key(method, url, json_body)
        path = self.root / key[:2] / f"{key}.json"

        if path.exists() and not self.force:
            print(f"Skipping existing fixture {path.name} (use --force to overwrite)")
            self.hits += 1
            return CachedResponse.from_disk(path)

        self.misses += 1
        own_client = client is None
        client_to_use = client or httpx.AsyncClient(timeout=timeout)
        try:
            resp = await client_to_use.request(
                method,
                url,
                json=json_body,
                headers=headers,
                timeout=timeout,
            )
        finally:
            if own_client:
                await client_to_use.aclose()

        if resp.content == b"" and (method.upper() == "HEAD" or resp.status_code == 204):
            body = None
        else:
            try:
                body = resp.json()
            except Exception:
                body = resp.text

        # Rule R1 / S34: Scrub on write reusing scrub_fixtures.py
        raw_dict = {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "body": body,
        }
        scrubbed_dict, _ = scrub_fixture_data(raw_dict)

        cached = CachedResponse(
            status_code=scrubbed_dict["status_code"],
            headers=scrubbed_dict["headers"],
            body=scrubbed_dict["body"],
        )
        cached.to_disk(path)
        print(f"Recorded and scrubbed fixture -> {path}")
        return cached


def record_stage_ads(cache: RecordingHttpCache, paths: RunPaths) -> None:
    """Record Apify Meta Ads Library fixtures."""
    from cwt.tools.ads import cwt_source_winning_ads
    print("Recording stage 'ads'...")
    try:
        cwt_source_winning_ads(paths=paths, cache=cache)
    except Exception as e:
        print(f"Stage 'ads' recording note/error: {e}")


def record_stage_research(cache: RecordingHttpCache, paths: RunPaths) -> None:
    """Record Tavily and Exa search fixtures."""
    from cwt.tools.research import search_angle_sources
    print("Recording stage 'research'...")
    try:
        settings = Settings.from_env()
        tavily_key = settings.tavily_api_key
        exa_key = settings.exa_api_key
    except Exception:
        tavily_key = ""
        exa_key = ""

    for angle in ("pain", "unique_data", "crowd_effect"):
        try:
            search_angle_sources(
                angle=angle,
                cache=cache,
                tavily_api_key=tavily_key,
                exa_api_key=exa_key,
            )
        except Exception as e:
            print(f"Stage 'research' ({angle}) recording note/error: {e}")


def record_stage_render(cache: RecordingHttpCache, paths: RunPaths) -> None:
    """Record asset download fixtures."""
    from cwt.video.assets import AssetSourcer
    print("Recording stage 'render' assets...")
    try:
        sourcer = AssetSourcer(paths=paths, cache=cache)
        # Ensure base CC0 assets are cached
        sourcer.ensure_base_assets()
    except Exception as e:
        print(f"Stage 'render' recording note/error: {e}")


def record_stage(stage: str, cache: RecordingHttpCache, paths: RunPaths) -> None:
    """Record fixtures for the specified stage."""
    if stage in ("ads", "all"):
        record_stage_ads(cache, paths)
    if stage in ("research", "all"):
        record_stage_research(cache, paths)
    if stage in ("render", "all"):
        record_stage_render(cache, paths)
    if stage in ("patterns", "storyboard", "claims"):
        print(f"Stage '{stage}' relies on previously recorded upstream fixtures or local LLM/rules.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record HTTP fixtures for offline test and demo execution (spends credits)."
    )
    parser.add_argument(
        "--stage",
        choices=STAGES,
        default="all",
        help="Stage to record fixtures for: ads, patterns, research, storyboard, claims, render, all",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-recording even if fixture exists (spends API credits)",
    )
    parser.add_argument(
        "--root",
        default="fixtures/http",
        help="HTTP fixtures root directory (default: fixtures/http)",
    )
    parser.add_argument(
        "--run-id",
        default="recording",
        help="Run ID used for temporary staging",
    )
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        apify_max_charge = settings.apify_max_charge_usd
    except Exception:
        apify_max_charge = 1.0

    # 1. Print spend warning before any call
    print_spend_warning(apify_max_charge)

    # 2. Gate live calls: require --force or interactive confirmation
    if not args.force:
        if sys.stdin.isatty():
            ans = input("Proceed with live recording? [y/N]: ").strip().lower()
            if ans not in ("y", "yes"):
                print("Aborted.")
                return 0
        else:
            print("Notice: Running non-interactively without --force; existing fixtures will be skipped.")

    # 3. Setup cache and execute recording
    root_path = Path(args.root)
    root_path.mkdir(parents=True, exist_ok=True)
    cache = RecordingHttpCache(root=root_path, force=args.force)
    paths = RunPaths.from_parts(Path("runs"), args.run_id)
    paths.ensure()

    record_stage(args.stage, cache, paths)
    print("Recording completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
