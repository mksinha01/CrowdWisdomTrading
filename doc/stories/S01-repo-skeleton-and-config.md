# S01 — Repo skeleton, packaging & config

**Phase** 0 · **Depends on** — · **Blocks** S02, and every later story
**Spec** `doc/video-ads-agent.md` lines **56–188** (file tree), **191–364** (env), **886–927** (deps), **931–973** (Dockerfile)
**Context budget** ~11k (spec 3.5k + story 1.5k + output 6k)
**Produces** `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `.env.example`, `.env`, `.gitignore`, `.gitattributes`, `Makefile`, `Dockerfile`, `src/cwt/__init__.py`, `src/cwt/config.py`, `runs/.gitkeep`

---

## Goal

After this story the repo is a real, installable Python 3.11 package with every directory from the
spec's file tree present, and **one** module that reads environment variables. No other module may
ever call `os.getenv` — that is the whole point of `config.py`.

## Interface contract — FROZEN

```python
# src/cwt/config.py
@dataclass(frozen=True)
class Settings:
    # ── LLM ──
    llm_provider: str                  # "openrouter" | "nvidia"
    openrouter_api_key: str
    openrouter_base_url: str
    nvidia_api_key: str
    nvidia_base_url: str
    model_cheap: str
    model_strong: str
    model_fallbacks: list[str]
    llm_max_concurrency: int           # default 4
    llm_json_repair_attempts: int      # default 2
    llm_timeout_seconds: float         # default 120.0
    # ── Apify ──
    apify_token: str
    apify_ads_actor_id: str            # default "apify~facebook-ads-scraper"
    apify_ads_actor_fallbacks: list[str]
    apify_max_items: int               # default 60
    apify_max_charge_usd: float        # default 1.00
    apify_run_timeout_seconds: int     # default 900
    # ── Search ──
    tavily_api_key: str
    exa_api_key: str
    # ── Video ──
    video_backend_chain: list[str]     # MUST end with "local_ffmpeg"
    ffmpeg_bin: str
    ffprobe_bin: str
    video_width: int                   # 1080
    video_height: int                  # 1920
    video_fps: int                     # 30
    video_min_seconds: int             # 30
    video_max_seconds: int             # 60
    video_loudness_lufs: float         # -14.0
    video_true_peak_dbtp: float        # -1.5
    # ── TTS ──
    tts_backend_chain: list[str]       # default ["edge_tts","piper","silent"]
    edge_tts_voice: str                # "en-US-AndrewNeural"
    piper_voice_path: str
    # ── Optional backends ──
    hyperframes_enabled: str           # "auto"
    openmontage_home: str
    # ── Hermes ──
    hermes_bin: str
    hermes_min_version: str            # "0.16.0"
    # ── Run control ──
    board: str                         # "cwt-ads"
    run_timeout_seconds: int           # 5400
    stall_threshold_seconds: int       # 180
    max_usd: float                     # 2.00
    # ── Compliance ──
    claims_gate_enabled: bool          # True
    claims_max_rewrite_rounds: int     # 3
    creative_threshold: float          # 8.0
    creative_max_rounds: int           # 3
    # ── Derived (S33 doctor + S32 engine rely on these) ──
    engine_defaults_to_hermes: bool    # True when llm_provider is set — see B3

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings": ...

    def with_backend_chain(self, chain: Sequence[str]) -> "Settings": ...
```

Also frozen: `require_env(name)` helper raising a `ConfigError` with the obtain-URL from `.env.example`.

## Rules that bind this story

- **Rule V4** — `from_env()` **must raise** if `video_backend_chain[-1] != "local_ffmpeg"`. This is the
  invariant that makes "the pipeline cannot hard-fail on video" true by construction. Not a warning.
- **Rule A2** — `apify_max_charge_usd` must be non-zero; raise if it is `0` or absent.
- **Rule C2** — `claims_gate_enabled` must default `True`; log a WARNING if it is set to `False`. Do not
  raise — but the warning must name Rule C2.

## Build steps

1. Create the directory tree from spec lines 61–187 verbatim, including empty package `__init__.py`
   files and `runs/.gitkeep`. Directories with no files yet still get created.
2. Write `pyproject.toml` — `[project]` name `cwt`, requires-python `>=3.11`, console script
   `cwt = "cwt.cli:main"`, and `[tool.ruff]` / `[tool.mypy]` / `[tool.pytest.ini_options]` config.
3. Copy `requirements.txt` and `requirements-dev.txt` from spec lines 888–922 verbatim, including the
   comments explaining why `moviepy` and `apify-client` are absent.
4. Write `.env.example` from spec lines 195–312 verbatim — keep every comment, it is the documentation.
5. Write `.gitignore` (lines 316–346) and `.gitattributes` (lines 350–363) verbatim.
6. Write `src/cwt/config.py` per the contract above.
7. Write the `Dockerfile` from spec lines 935–973 verbatim, including the port-map comment.
8. Write a `Makefile` with targets `bootstrap`, `doctor`, `run`, `offline`, `test`, `lint`, `demo`.
9. `python -m venv .venv && .venv/Scripts/pip install -e . -r requirements.txt -r requirements-dev.txt`.

## Decisions the spec leaves open

- **`engine_defaults_to_hermes` (B3).** `doctor.py` line 4291 reads this field but `.env.example`
  never defines it. Derive it: `True` unless `CWT_ENGINE=local`. Add `CWT_ENGINE` to `.env.example`.
- **`ConfigError` location.** Put it in `config.py`, not a shared errors module — it is only raised here.
- **`.env` loading.** Use `python-dotenv` with `override=False` so real env vars win, matching the
  precedence chain stated at spec lines 199–202. Do **not** reverse it.

## Done when

```bash
.venv/Scripts/python -c "from cwt.config import Settings; s=Settings.from_env(); print(s.board, s.video_backend_chain)"
# -> cwt-ads ['hyperframes', 'openmontage', 'local_ffmpeg']

.venv/Scripts/python -c "import os; os.environ['VIDEO_BACKEND_CHAIN']='hyperframes,openmontage'; from cwt.config import Settings; Settings.from_env()"
# -> MUST raise (Rule V4)

.venv/Scripts/python -m pytest tests/ -q      # 0 tests collected, exit 5 is acceptable
.venv/Scripts/python -m cwt --help            # argparse error is fine; module must import
```

## Handoff

Later stories may assume: `Settings.from_env()` works, every field above exists, `Settings` is frozen
and hashable, and `settings.with_backend_chain([...])` returns a new instance. **No module other than
`config.py` reads environment variables** — if you need a value, add a field here.
