"""Asset sourcer — CC0 allowlist, content-hash cache, and Pillow-generated stills.

Story S17. Spec §3.5 line 877 / §13 / §16.3.

Three AssetRef.kind values, three strategies
────────────────────────────────────────────
• internal        — ``fixtures/assets/stills/<ref>.png`` bundled, no network
• cc0             — bundled if present, else fetch from the CC0 allowlist into
                    ``paths.cache`` keyed by content-SHA-256; raises on any
                    unlisted host (licence control, Rule R1)
• generated_chart — drawn locally by Pillow from the shot description + brand
                    palette; seeded from ``hash(shot.id)`` so renders are
                    byte-identical across runs (determinism rule)

Architecture rules
──────────────────
• Rule R1 — allowlist is a licence control; never source from outside it.
• Rule W9 — long_path() applied at every mkdir site for paths.cache.
• Offline guarantee — every asset the fixture storyboard references is bundled
  so ``cwt run --engine local --offline`` works with zero network and zero spend.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from cwt.clients.http_cache import HttpCache
    from cwt.config import Settings
    from cwt.domain.models import AssetRef, Shot
    from cwt.util.paths import RunPaths

logger = logging.getLogger("cwt.assets")

# ---------------------------------------------------------------------------
# Licence constants
# ---------------------------------------------------------------------------

ASSET_LICENCES = ("CC0-1.0", "MIT", "internal")

# The ONLY hosts that may serve CC0 content (Rule R1).
_CC0_ALLOWLIST: frozenset[str] = frozenset(
    {
        "cdn.pixabay.com",
        "freesound.org",
        "upload.wikimedia.org",
        "opengameart.org",
    }
)

# ---------------------------------------------------------------------------
# Brand palette (spec §3.3 / §15)
# ---------------------------------------------------------------------------

_BG_RGB = (5, 5, 5)
_CYAN_RGB = (34, 211, 238)
_ORANGE_RGB = (251, 146, 60)
_AMBER_RGB = (251, 191, 36)
_INDIGO_RGB = (129, 140, 248)
_ROSE_RGB = (251, 113, 133)

# Canvas dimensions for all generated stills (1080x1920)
_W = 1080
_H = 1920

# Root for fixture assets
_FIXTURE_ROOT = Path(__file__).parent.parent.parent.parent / "fixtures" / "assets"


# ---------------------------------------------------------------------------
# AssetRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetRecord:
    """A resolved asset: reference name -> file on disk + licence metadata."""

    ref: str
    path: Path
    license: str
    source_url: str
    attribution_required: bool
    kind: str
    sha256: str


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _lerp_rgb(
    a: tuple[int, int, int],
    b: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Subject drawing routines — all seeded, all deterministic
# ---------------------------------------------------------------------------


def _draw_abstract_market_data(rng: random.Random, ref: str) -> "Image":  # type: ignore[name-defined]
    """Procedural candlestick chart on deep black."""
    from PIL import Image, ImageDraw  # type: ignore[import]

    img = Image.new("RGB", (_W, _H), _BG_RGB)
    d = ImageDraw.Draw(img)

    if "single_cyan_line" in ref:
        # Unique variant: just one cyan line through the middle
        y_mid = _H // 2
        d.line([(60, y_mid), (_W - 60, y_mid)], fill=_CYAN_RGB, width=6)
        return img

    num_candles = 32 if "proliferation" in ref else 18
    cw = _W // (num_candles + 2)
    padding_x = (_W - cw * num_candles) // 2

    for i in range(num_candles):
        x = padding_x + i * cw + cw // 2
        is_green = rng.random() > 0.45
        body_h = int(rng.uniform(40, 220))
        wick_top = int(rng.uniform(10, 60))
        wick_bot = int(rng.uniform(10, 60))
        cy = int(rng.uniform(_H * 0.2, _H * 0.75))
        colour = _CYAN_RGB if is_green else (239, 68, 68)
        half_bw = max(cw // 3, 4)
        d.line(
            [(x, cy - body_h // 2 - wick_top), (x, cy + body_h // 2 + wick_bot)],
            fill=colour,
            width=2,
        )
        d.rectangle(
            [(x - half_bw, cy - body_h // 2), (x + half_bw, cy + body_h // 2)],
            fill=colour,
        )

    return img


def _draw_trader_silhouette(rng: random.Random, ref: str) -> "Image":  # type: ignore[name-defined]
    """Abstract geometric silhouette (no photograph — avoids model release)."""
    from PIL import Image, ImageDraw  # type: ignore[import]

    img = Image.new("RGB", (_W, _H), _BG_RGB)
    d = ImageDraw.Draw(img)

    if "dusk" in ref:
        for y in range(_H):
            t = y / _H
            col = _lerp_rgb((15, 10, 30), _BG_RGB, t)
            d.line([(0, y), (_W, y)], fill=col)
        accent = _ORANGE_RGB
    else:
        accent = _CYAN_RGB

    cx = _W // 2
    head_r = 90
    head_y = int(_H * 0.42)
    d.ellipse(
        [(cx - head_r, head_y - head_r), (cx + head_r, head_y + head_r)],
        fill=(10, 10, 10),
        outline=accent,
        width=3,
    )
    shoulder_y = head_y + head_r + 10
    hip_y = int(_H * 0.82)
    d.polygon(
        [
            (cx - 160, shoulder_y),
            (cx + 160, shoulder_y),
            (cx + 220, hip_y),
            (cx - 220, hip_y),
        ],
        fill=(10, 10, 10),
        outline=accent,
    )
    if "desk" in ref:
        desk_y = int(_H * 0.80)
        d.rectangle([(cx - 280, desk_y), (cx + 280, desk_y + 20)], fill=accent)
        mon_x, mon_y = cx, int(_H * 0.58)
        mon_w, mon_h = 160, 110
        d.rectangle(
            [(mon_x - mon_w, mon_y - mon_h), (mon_x + mon_w, mon_y + mon_h)],
            outline=accent,
            width=3,
        )
    return img


def _draw_chart_detail(rng: random.Random, ref: str) -> "Image":  # type: ignore[name-defined]
    """Technical chart with grid, axes, and a seeded price line."""
    from PIL import Image, ImageDraw  # type: ignore[import]

    img = Image.new("RGB", (_W, _H), _BG_RGB)
    d = ImageDraw.Draw(img)

    grid_cols = 12 if "dense" in ref else 8
    grid_rows = 18 if "dense" in ref else 12

    for gx in range(grid_cols + 1):
        x = 80 + gx * ((_W - 140) // grid_cols)
        d.line([(x, 120), (x, _H - 120)], fill=(30, 30, 30), width=1)
    for gy in range(grid_rows + 1):
        y = 120 + gy * ((_H - 240) // grid_rows)
        d.line([(80, y), (_W - 60, y)], fill=(30, 30, 30), width=1)

    d.line([(80, 120), (80, _H - 120)], fill=_CYAN_RGB, width=2)
    d.line([(80, _H - 120), (_W - 60, _H - 120)], fill=_CYAN_RGB, width=2)

    pts_count = 60
    px_step = max((_W - 140) // pts_count, 1)
    y_min, y_max = 140, _H - 140
    pts = []
    v = rng.uniform(0.3, 0.7)
    for i in range(pts_count):
        v = max(0.05, min(0.95, v + rng.gauss(0, 0.04)))
        x = 80 + i * px_step
        y = int(y_min + (1 - v) * (y_max - y_min))
        pts.append((x, y))

    if len(pts) > 1:
        d.line(pts, fill=_CYAN_RGB, width=3)
        lx, ly = pts[-1]
        d.ellipse([(lx - 8, ly - 8), (lx + 8, ly + 8)], fill=_CYAN_RGB)

    return img


def _draw_city_night(rng: random.Random, ref: str) -> "Image":  # type: ignore[name-defined]
    """Procedural city skyline at night with glowing windows."""
    from PIL import Image, ImageDraw  # type: ignore[import]

    img = Image.new("RGB", (_W, _H), (2, 2, 8))
    d = ImageDraw.Draw(img)

    for y in range(_H):
        t = y / _H
        col = _lerp_rgb((4, 4, 18), (2, 2, 8), t)
        d.line([(0, y), (_W, y)], fill=col)

    horizon = int(_H * 0.62)
    buildings = 14
    bw = _W // buildings
    for i in range(buildings):
        h_b = int(rng.uniform(_H * 0.18, _H * 0.50))
        bx = i * bw
        by = horizon - h_b
        d.rectangle([(bx, by), (bx + bw - 4, _H)], fill=(10, 10, 20))
        for wy in range(by + 10, horizon, 20):
            for wx in range(bx + 6, bx + bw - 6, 14):
                if rng.random() > 0.35:
                    win_col = rng.choice([_AMBER_RGB, _CYAN_RGB, (240, 240, 200)])
                    d.rectangle([(wx, wy), (wx + 8, wy + 12)], fill=win_col)

    if "canary" in ref:
        twx = _W // 2
        twy = int(_H * 0.10)
        d.rectangle([(twx - 18, twy), (twx + 18, horizon)], fill=(18, 18, 30))
        d.ellipse([(twx - 8, twy - 8), (twx + 8, twy + 8)], fill=_ROSE_RGB)

    return img


def _draw_screen_glow(rng: random.Random, ref: str) -> "Image":  # type: ignore[name-defined]
    """Monitor / screen glow abstract with radial emanation + scan lines."""
    from PIL import Image, ImageDraw  # type: ignore[import]

    img = Image.new("RGB", (_W, _H), _BG_RGB)
    d = ImageDraw.Draw(img)

    glow_col = _AMBER_RGB if "amber" in ref else _CYAN_RGB

    cx, cy = _W // 2, _H // 2
    for radius in range(400, 0, -20):
        t = 1 - radius / 400
        col = _lerp_rgb(_BG_RGB, glow_col, t * 0.5)
        d.ellipse(
            [(cx - radius, cy - radius), (cx + radius, cy + radius)],
            outline=col,
            width=3,
        )

    for y in range(0, _H, 6):
        if rng.random() > 0.7:
            d.line([(0, y), (_W, y)], fill=(40, 40, 40), width=1)

    bar_x = 120
    for i in range(8):
        bar_w = int(rng.uniform(100, 600))
        by = int(_H * 0.20 + i * (_H * 0.07))
        d.rectangle([(bar_x, by), (bar_x + bar_w, by + 14)], fill=glow_col)
        d.rectangle([(bar_x + bar_w, by), (bar_x + 650, by + 14)], fill=(20, 20, 20))

    return img


def _draw_typography_card(
    rng: random.Random,
    ref: str,
    on_screen_text: str = "",
) -> "Image":  # type: ignore[name-defined]
    """Large display text on the brand-dark background."""
    from PIL import Image, ImageDraw  # type: ignore[import]

    bg = _BG_RGB if "black" in ref else (8, 8, 12)
    text_col = _CYAN_RGB if "cyan" in ref else (203, 213, 225)

    img = Image.new("RGB", (_W, _H), bg)
    d = ImageDraw.Draw(img)

    d.line([(80, 160), (_W - 80, 160)], fill=_CYAN_RGB, width=4)

    text = on_screen_text or "COLLECTIVE\nINTELLIGENCE\nFOR TRADERS"
    try:
        from PIL import ImageFont  # type: ignore[import]

        font = None
        for font_name in [
            "arialbd.ttf",
            "Arial Bold.ttf",
            "DejaVuSans-Bold.ttf",
            "LiberationSans-Bold.ttf",
        ]:
            try:
                font = ImageFont.truetype(font_name, size=96)
                break
            except (OSError, IOError):
                continue
        if font is None:
            font = ImageFont.load_default(size=72)

        lines = text.upper().splitlines()
        y = int(_H * 0.28)
        for line in lines:
            bbox = d.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
            d.text(((_W - tw) // 2, y), line, fill=text_col, font=font)
            y += bbox[3] - bbox[1] + 28
    except Exception:
        lines = text.upper().splitlines()
        y = int(_H * 0.30)
        for line in lines:
            block_w = min(len(line) * 42, _W - 160)
            d.rectangle([(80, y), (80 + block_w, y + 80)], fill=text_col)
            y += 110

    d.line([(80, _H - 200), (_W - 80, _H - 200)], fill=_CYAN_RGB, width=2)
    return img


# ---------------------------------------------------------------------------
# Audio synthesis helpers
# ---------------------------------------------------------------------------


def _synth_audio(out_path: Path, kind: str, duration_s: float) -> bool:
    """Synthesise a simple audio fixture using ffmpeg; returns True on success."""
    from cwt.util.subproc import ToolFailed, ToolNotFound, run_tool

    try:
        from cwt.video.ffmpeg_bin import ffmpeg_path
        ffmpeg_exe = ffmpeg_path()
    except Exception:
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)

    SPECS: dict[str, list[str]] = {
        "tension_bed": [
            "-f", "lavfi", "-i",
            f"anoisesrc=color=brown:amplitude=0.15:duration={duration_s}",
            "-af", "highpass=f=60,lowpass=f=800,volume=0.4",
        ],
        "synth_note_clean": [
            "-f", "lavfi", "-i",
            f"sine=frequency=440:duration={duration_s}",
            "-af", "afade=t=out:st=0.8:d=0.2,volume=0.5",
        ],
        "riser_cut_to_silence": [
            "-f", "lavfi", "-i",
            f"sine=frequency=220:duration={duration_s}",
            "-af", f"afade=t=out:st={max(0.0, duration_s - 0.05)}:d=0.05,volume=0.4",
        ],
        "impact_low": [
            "-f", "lavfi", "-i",
            f"sine=frequency=60:duration={duration_s}",
            "-af", "afade=t=out:st=0.3:d=0.4,volume=0.7",
        ],
    }

    spec_key = next((k for k in SPECS if k in kind), "tension_bed")
    filter_args = SPECS[spec_key]

    try:
        res = run_tool([ffmpeg_exe, "-y", *filter_args, str(out_path)], timeout_s=30)
        return res.ok and out_path.exists() and out_path.stat().st_size > 0
    except (ToolFailed, ToolNotFound, Exception) as exc:
        logger.warning("Audio synthesis failed for %s: %s", kind, exc)
        return False


def _write_silence(out_path: Path, duration_s: float) -> None:
    """Write a minimal WAV silence file as audio fallback."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 22050
    n_frames = int(sample_rate * duration_s)
    with wave.open(str(out_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack("<" + "h" * n_frames, *([0] * n_frames)))


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def _load_manifest() -> list[dict]:
    """Load MANIFEST.json from the fixtures directory."""
    manifest_path = _FIXTURE_ROOT / "MANIFEST.json"
    if not manifest_path.exists():
        return []
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# AssetSourcer
# ---------------------------------------------------------------------------


class AssetSourcer:
    """Resolve ``AssetRef`` -> ``AssetRecord`` (file on disk + licence).

    Thread-safety: ``generate()`` is deterministic — concurrent callers with the
    same ``shot.id`` write identical bytes. No locking needed.
    """

    def __init__(
        self,
        *,
        paths: "RunPaths",
        settings: "Settings",
        cache: "HttpCache",
    ) -> None:
        self._paths = paths
        self._settings = settings
        self._cache = cache
        self._resolved: dict[str, AssetRecord] = {}
        self._warnings: list[str] = []
        self._manifest_entries: list[dict] = _load_manifest()

    # ------------------------------------------------------------------
    # Public API (FROZEN interface contract)
    # ------------------------------------------------------------------

    def resolve(self, asset: "AssetRef", shot: "Shot") -> AssetRecord:
        """AssetRef -> a file on disk. Deterministic and idempotent."""
        if asset.ref in self._resolved:
            return self._resolved[asset.ref]

        manifest_entry = self._find_manifest_entry(asset.ref)

        if asset.kind == "generated_chart":
            rec = self.generate(shot, self._gen_out_path(asset.ref))
        elif asset.kind == "internal":
            rec = self._resolve_internal(asset, manifest_entry)
        elif asset.kind == "cc0":
            rec = self._resolve_cc0(asset, manifest_entry)
        else:
            raise ValueError(
                f"Unknown asset kind {asset.kind!r} for ref {asset.ref!r}"
            )

        self._resolved[asset.ref] = rec
        return rec

    def generate(self, shot: "Shot", out_path: Path) -> AssetRecord:
        """kind == 'generated_chart': draw the shot's described image with Pillow.

        Seeded from ``hash(shot.id)`` so two runs with the same storyboard
        produce byte-identical PNGs.
        """
        seed = int(hashlib.sha256(shot.id.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)

        out_path.parent.mkdir(parents=True, exist_ok=True)

        if not out_path.exists():
            img = self._draw_subject(
                rng,
                shot.subject.value,
                shot.asset.ref,
                shot,
            )
            img.save(str(out_path), format="PNG")

        sha = _sha256_file(out_path)
        rec = AssetRecord(
            ref=shot.asset.ref,
            path=out_path,
            license="internal",
            source_url="generated://pillow",
            attribution_required=False,
            kind="generated_chart",
            sha256=sha,
        )
        self._resolved[shot.asset.ref] = rec
        return rec

    def manifest(self) -> list[dict]:
        """Return the ``render_manifest.assets[]`` block.

        One entry per distinct ``ref`` resolved so far.  Every entry has
        ``ref``, ``path``, ``license``, ``source_url``, ``attribution_required``
        (spec §3.5 line 877).
        """
        if self._resolved:
            return [
                {
                    "ref": rec.ref,
                    "path": rec.path,
                    "license": rec.license,
                    "source_url": rec.source_url,
                    "attribution_required": rec.attribution_required,
                    "kind": rec.kind,
                    "sha256": rec.sha256,
                }
                for rec in self._resolved.values()
            ]
        return self._manifest_from_fixture()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_manifest_entry(self, ref: str) -> dict | None:
        for entry in self._manifest_entries:
            if entry.get("ref") == ref:
                return entry
        return None

    def _gen_out_path(self, ref: str) -> Path:
        out_dir = self._paths.assets / "generated"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{ref}.png"

    def _resolve_internal(
        self,
        asset: "AssetRef",
        manifest_entry: dict | None,
    ) -> AssetRecord:
        bundled = _FIXTURE_ROOT / "stills" / f"{asset.ref}.png"
        if not bundled.exists():
            return self._auto_generate_internal(asset)
        sha = _sha256_file(bundled)
        me = manifest_entry or {}
        return AssetRecord(
            ref=asset.ref,
            path=bundled,
            license=me.get("license", "internal"),
            source_url=me.get("source_url", "generated://pillow"),
            attribution_required=bool(me.get("attribution_required", False)),
            kind="internal",
            sha256=sha,
        )

    def _auto_generate_internal(self, asset: "AssetRef") -> AssetRecord:
        """Generate a synthetic PNG for a bundled-internal asset whose file is absent."""
        from cwt.domain.models import SubjectName

        seed = int(hashlib.sha256(asset.ref.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)

        subject_str = "abstract_market_data"
        for entry in self._manifest_entries:
            if entry.get("ref") == asset.ref:
                subject_str = entry.get("subject", subject_str)
                break
        else:
            for s in SubjectName:
                if s.value in asset.ref.lower():
                    subject_str = s.value
                    break

        img = self._draw_subject(rng, subject_str, asset.ref, shot=None)
        out_path = _FIXTURE_ROOT / "stills" / f"{asset.ref}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out_path), format="PNG")

        sha = _sha256_file(out_path)
        return AssetRecord(
            ref=asset.ref,
            path=out_path,
            license="internal",
            source_url="generated://pillow",
            attribution_required=False,
            kind="internal",
            sha256=sha,
        )

    def _resolve_cc0(
        self,
        asset: "AssetRef",
        manifest_entry: dict | None,
    ) -> AssetRecord:
        # 1. Bundled file?
        for subdir in ("sfx", "music", "stills", "clips"):
            for ext in ("mp3", "wav", "ogg", "flac", "png", "jpg", "mp4"):
                candidate = _FIXTURE_ROOT / subdir / f"{asset.ref}.{ext}"
                if candidate.exists():
                    sha = _sha256_file(candidate)
                    me = manifest_entry or {}
                    return AssetRecord(
                        ref=asset.ref,
                        path=candidate,
                        license=me.get("license", "CC0-1.0"),
                        source_url=me.get("source_url", "bundled"),
                        attribution_required=bool(me.get("attribution_required", False)),
                        kind="cc0",
                        sha256=sha,
                    )

        # 2. Synthesisable audio?
        if any(
            kw in asset.ref
            for kw in ["tension_bed", "synth_note", "riser_cut", "impact_low"]
        ):
            return self._synth_audio_asset(asset, manifest_entry)

        # 3. Fetchable from allowlist?
        if manifest_entry and manifest_entry.get("source_url"):
            return self._fetch_cc0(asset, manifest_entry)

        raise FileNotFoundError(
            f"CC0 asset {asset.ref!r} is not bundled, not synthesisable, "
            "and has no source_url in the manifest."
        )

    def _synth_audio_asset(
        self,
        asset: "AssetRef",
        manifest_entry: dict | None,
    ) -> AssetRecord:
        """Synthesise an audio asset; fall back to silence if ffmpeg fails."""
        sfx_dir = _FIXTURE_ROOT / "sfx"
        music_dir = _FIXTURE_ROOT / "music"

        if "tension_bed" in asset.ref:
            out_path = music_dir / f"{asset.ref}.wav"
            duration_s = 92.0
        else:
            out_path = sfx_dir / f"{asset.ref}.wav"
            duration_s = 1.0 if "impact" in asset.ref else 2.5

        out_path.parent.mkdir(parents=True, exist_ok=True)

        if not out_path.exists():
            ok = _synth_audio(out_path, asset.ref, duration_s)
            if not ok:
                self._warnings.append(
                    f"Audio synthesis failed for {asset.ref!r}; "
                    "writing silence fallback."
                )
                _write_silence(out_path, duration_s)

        sha = _sha256_file(out_path)
        me = manifest_entry or {}
        return AssetRecord(
            ref=asset.ref,
            path=out_path,
            license=me.get("license", "CC0-1.0"),
            source_url=me.get("source_url", "synthesised://ffmpeg"),
            attribution_required=False,
            kind="cc0",
            sha256=sha,
        )

    def _fetch_cc0(
        self,
        asset: "AssetRef",
        manifest_entry: dict,
    ) -> AssetRecord:
        """Fetch a CC0 asset from the allowlist via HttpCache."""
        import asyncio

        source_url = manifest_entry["source_url"]
        host = urlparse(source_url).hostname or ""
        if host not in _CC0_ALLOWLIST:
            raise PermissionError(
                f"Asset {asset.ref!r} source host {host!r} is not in the "
                f"CC0 allowlist (Rule R1). Allowed: {sorted(_CC0_ALLOWLIST)}"
            )

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(
                        asyncio.run,
                        self._cache.request("GET", source_url),
                    )
                    resp = fut.result(timeout=60)
            else:
                resp = loop.run_until_complete(
                    self._cache.request("GET", source_url)
                )
        except RuntimeError:
            resp = asyncio.run(self._cache.request("GET", source_url))

        if resp.status_code != 200:
            raise RuntimeError(
                f"CC0 fetch for {asset.ref!r} returned HTTP {resp.status_code}"
            )

        content = resp.body
        if isinstance(content, (bytes, bytearray)):
            raw = bytes(content)
        elif isinstance(content, str):
            raw = content.encode("latin-1")
        else:
            raw = str(content).encode("utf-8")

        sha = hashlib.sha256(raw).hexdigest()
        ext = Path(urlparse(source_url).path).suffix or ".bin"
        cached_path = self._paths.cache / sha[:2] / f"{sha}{ext}"
        cached_path.parent.mkdir(parents=True, exist_ok=True)

        if not cached_path.exists():
            cached_path.write_bytes(raw)

        sidecar = cached_path.with_suffix(".license.json")
        if not sidecar.exists():
            sidecar.write_text(
                json.dumps(
                    {
                        "license": manifest_entry.get("license", "CC0-1.0"),
                        "source_url": source_url,
                        "attribution_required": manifest_entry.get(
                            "attribution_required", False
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        return AssetRecord(
            ref=asset.ref,
            path=cached_path,
            license=manifest_entry.get("license", "CC0-1.0"),
            source_url=source_url,
            attribution_required=bool(
                manifest_entry.get("attribution_required", False)
            ),
            kind="cc0",
            sha256=sha,
        )

    def _manifest_from_fixture(self) -> list[dict]:
        """Build manifest list from MANIFEST.json (before resolve() is called)."""
        result = []
        for entry in self._manifest_entries:
            ref = entry.get("ref", "")
            path: Path | None = None
            for subdir in ("stills", "sfx", "music", "clips"):
                for ext in ("png", "jpg", "mp3", "wav", "ogg", "mp4"):
                    candidate = _FIXTURE_ROOT / subdir / f"{ref}.{ext}"
                    if candidate.exists():
                        path = candidate
                        break
                if path:
                    break
            if path is None:
                path = _FIXTURE_ROOT / "stills" / f"{ref}.png"

            result.append(
                {
                    "ref": ref,
                    "path": path,
                    "license": entry.get("license", "internal"),
                    "source_url": entry.get("source_url", "generated://pillow"),
                    "attribution_required": entry.get("attribution_required", False),
                    "kind": entry.get("kind", "internal"),
                    "sha256": "",
                }
            )
        return result

    def _draw_subject(
        self,
        rng: random.Random,
        subject: str,
        ref: str,
        shot: "Shot | None",
    ) -> "Image":  # type: ignore[name-defined]
        """Dispatch to the correct drawing routine by subject name."""
        on_screen_text = ""
        if shot is not None and shot.on_screen_text:
            on_screen_text = shot.on_screen_text[0].text

        dispatch = {
            "abstract_market_data": _draw_abstract_market_data,
            "trader_silhouette": _draw_trader_silhouette,
            "chart_detail": _draw_chart_detail,
            "city_night": _draw_city_night,
            "screen_glow": _draw_screen_glow,
        }

        if subject == "typography_card":
            img = _draw_typography_card(rng, ref, on_screen_text)
        else:
            draw_fn = dispatch.get(subject, _draw_abstract_market_data)
            if draw_fn is None:
                logger.warning(
                    "Unknown subject %r; falling back to abstract_market_data",
                    subject,
                )
                draw_fn = _draw_abstract_market_data
            img = draw_fn(rng, ref)

        if img.size != (_W, _H):
            img = img.resize((_W, _H), resample=3)  # LANCZOS

        return img
