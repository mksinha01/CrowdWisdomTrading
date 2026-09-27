"""Run directory layout + the Windows long-path escape hatch.

The run directory is deliberately SHALLOW. `runs/<run_id>/` with a short run_id
keeps us clear of the 260-character Windows path limit, which a deep
runs/<uuid>/artifacts/cache/<sha256>/... tree would blow through (Rule W9).
"""
from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def mint_run_id() -> str:
    """Short by design: YYYYMMDD-HHMM-<4 hex>. The 4 hex chars make collisions
    vanishingly unlikely without needing a UUID's 36 characters in every path."""
    return f"{datetime.now():%Y%m%d-%H%M}-{secrets.token_hex(2)}"


def long_path(p: Path) -> str:
    """Prefix with \\\\?\\ on Windows when the path approaches MAX_PATH.

    Only applied at the few call sites that genuinely need it — ffmpeg and mkdir
    on deeply nested asset caches. Applying it everywhere produces paths that
    some tools display unreadably.
    """
    s = str(p)
    if sys.platform == "win32" and len(s) > 250 and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + os.path.abspath(s)
    return s


@dataclass(frozen=True)
class RunPaths:
    run_dir: Path

    def __post_init__(self) -> None:
        if not isinstance(self.run_dir, Path):
            object.__setattr__(self, "run_dir", Path(self.run_dir))

    @property
    def artifacts(self) -> Path:
        return self.run_dir / "artifacts"

    @property
    def assets(self) -> Path:
        return self.run_dir / "assets"

    @property
    def cache(self) -> Path:
        return self.run_dir / "assets" / "cache"

    @property
    def render(self) -> Path:
        return self.run_dir / "render"

    @property
    def failures(self) -> Path:
        return self.run_dir / "failures"

    @property
    def ledger(self) -> Path:
        return self.run_dir / "llm_ledger.jsonl"

    @property
    def provenance(self) -> Path:
        return self.artifacts / "provenance.json"

    def ensure(self) -> RunPaths:
        for d in (self.artifacts, self.assets, self.cache, self.render, self.failures):
            d.mkdir(parents=True, exist_ok=True)
        return self
