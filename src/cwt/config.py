"""Configuration management for CWT Video Ads Agent.

Per specification, this is the ONLY module in the entire codebase permitted
to read environment variables or load the .env file.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Sequence

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

OBTAIN_URLS: dict[str, str] = {
    "OPENROUTER_API_KEY": "https://openrouter.ai/keys",
    "NVIDIA_API_KEY": "https://build.nvidia.com/",
    "APIFY_TOKEN": "https://console.apify.com/account/integrations",
    "TAVILY_API_KEY": "https://app.tavily.com/home",
    "EXA_API_KEY": "https://dashboard.exa.ai/api-keys",
}


class ConfigError(ValueError):
    """Raised when configuration is invalid, invariants fail, or required variables are missing."""
    pass


def require_env(name: str, env: Mapping[str, str] | None = None) -> str:
    """Return environment variable value or raise ConfigError with obtain-URL from .env.example."""
    if env is None:
        load_dotenv(override=False)
        source: Mapping[str, str] = os.environ
    else:
        source = env

    val = source.get(name)
    if not val or not val.strip():
        url = OBTAIN_URLS.get(name)
        if url:
            raise ConfigError(
                f"Missing required environment variable '{name}'. Obtain it at: {url}"
            )
        raise ConfigError(f"Missing required environment variable '{name}'.")
    return val


def _get_str(source: Mapping[str, str], key: str, default: str) -> str:
    val = source.get(key)
    if val is None:
        return default
    return val


def _get_int(source: Mapping[str, str], key: str, default: int) -> int:
    val = source.get(key)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except ValueError as exc:
        raise ConfigError(f"Invalid integer for '{key}': {val!r}") from exc


def _get_float(source: Mapping[str, str], key: str, default: float) -> float:
    val = source.get(key)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val)
    except ValueError as exc:
        raise ConfigError(f"Invalid float for '{key}': {val!r}") from exc


def _get_list(source: Mapping[str, str], key: str, default: list[str]) -> list[str]:
    val = source.get(key)
    if val is None:
        return list(default)
    parts = [p.strip() for p in val.split(",") if p.strip()]
    return parts


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

    def __hash__(self) -> int:
        items = []
        for k, v in self.__dict__.items():
            if isinstance(v, list):
                items.append((k, tuple(v)))
            else:
                items.append((k, v))
        return hash(tuple(items))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Settings):
            return False
        return self.__dict__ == other.__dict__

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Load settings from environment with precedence:

        1. Runtime environment variables
        2. .env file in project root (override=False)
        3. Defaults
        """
        if env is None:
            load_dotenv(dotenv_path=Path(".env"), override=False)
            source: Mapping[str, str] = os.environ
        else:
            source = env

        # ── Rule A2: Apify spend cap validation ──
        raw_apify_charge = source.get("APIFY_MAX_CHARGE_USD")
        if raw_apify_charge is None or raw_apify_charge.strip() == "":
            raise ConfigError(
                "Rule A2 violation: APIFY_MAX_CHARGE_USD is absent. A hard spend cap is required."
            )
        try:
            apify_max_charge_usd = float(raw_apify_charge)
        except ValueError as exc:
            raise ConfigError(
                f"Rule A2 violation: invalid APIFY_MAX_CHARGE_USD: {raw_apify_charge!r}"
            ) from exc

        if apify_max_charge_usd <= 0:
            raise ConfigError(
                "Rule A2 violation: apify_max_charge_usd must be non-zero and positive, "
                f"got: {apify_max_charge_usd}"
            )

        # ── Rule V4: Video backend chain termination validation ──
        video_backend_chain = _get_list(
            source,
            "VIDEO_BACKEND_CHAIN",
            ["hyperframes", "openmontage", "local_ffmpeg"],
        )
        if not video_backend_chain or video_backend_chain[-1] != "local_ffmpeg":
            raise ConfigError(
                "Rule V4 violation: video_backend_chain must terminate in 'local_ffmpeg'. "
                f"Got: {video_backend_chain}"
            )

        # ── Rule C2: Claims gate warning ──
        raw_claims = source.get("CLAIMS_GATE_ENABLED", "1")
        if isinstance(raw_claims, str):
            claims_gate_enabled = raw_claims.strip().lower() not in ("0", "false", "no", "off")
        else:
            claims_gate_enabled = bool(raw_claims)

        if not claims_gate_enabled:
            logger.warning(
                "Rule C2 warning: claims_gate_enabled is set to False. "
                "Claims gate disabled, output unshippable."
            )

        # ── Derived engine_defaults_to_hermes (Decision B3) ──
        cwt_engine = _get_str(source, "CWT_ENGINE", "hermes").strip().lower()
        engine_defaults_to_hermes = (cwt_engine != "local")

        return cls(
            # LLM
            llm_provider=_get_str(source, "LLM_PROVIDER", "openrouter"),
            openrouter_api_key=_get_str(source, "OPENROUTER_API_KEY", ""),
            openrouter_base_url=_get_str(source, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            nvidia_api_key=_get_str(source, "NVIDIA_API_KEY", ""),
            nvidia_base_url=_get_str(source, "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            model_cheap=_get_str(source, "LLM_MODEL_CHEAP", "google/gemini-2.5-flash"),
            model_strong=_get_str(source, "LLM_MODEL_STRONG", "anthropic/claude-sonnet-4.5"),
            model_fallbacks=_get_list(
                source,
                "LLM_MODEL_FALLBACKS",
                ["meta/llama-3.3-70b-instruct", "qwen/qwen2.5-72b-instruct"],
            ),
            llm_max_concurrency=_get_int(source, "LLM_MAX_CONCURRENCY", 4),
            llm_json_repair_attempts=_get_int(source, "LLM_JSON_REPAIR_ATTEMPTS", 2),
            llm_timeout_seconds=_get_float(source, "LLM_TIMEOUT_SECONDS", 120.0),
            # Apify
            apify_token=_get_str(source, "APIFY_TOKEN", ""),
            apify_ads_actor_id=_get_str(source, "APIFY_ADS_ACTOR_ID", "apify~facebook-ads-scraper"),
            apify_ads_actor_fallbacks=_get_list(
                source,
                "APIFY_ADS_ACTOR_FALLBACKS",
                ["curious_coder~facebook-ads-library-scraper"],
            ),
            apify_max_items=_get_int(source, "APIFY_MAX_ITEMS", 60),
            apify_max_charge_usd=apify_max_charge_usd,
            apify_run_timeout_seconds=_get_int(source, "APIFY_RUN_TIMEOUT_SECONDS", 900),
            # Search
            tavily_api_key=_get_str(source, "TAVILY_API_KEY", ""),
            exa_api_key=_get_str(source, "EXA_API_KEY", ""),
            # Video
            video_backend_chain=video_backend_chain,
            ffmpeg_bin=_get_str(source, "CWT_FFMPEG_BIN", ""),
            ffprobe_bin=_get_str(source, "CWT_FFPROBE_BIN", ""),
            video_width=_get_int(source, "VIDEO_WIDTH", 1080),
            video_height=_get_int(source, "VIDEO_HEIGHT", 1920),
            video_fps=_get_int(source, "VIDEO_FPS", 30),
            video_min_seconds=_get_int(source, "VIDEO_MIN_SECONDS", 30),
            video_max_seconds=_get_int(source, "VIDEO_MAX_SECONDS", 60),
            video_loudness_lufs=_get_float(source, "VIDEO_LOUDNESS_LUFS", -14.0),
            video_true_peak_dbtp=_get_float(source, "VIDEO_TRUE_PEAK_DBTP", -1.5),
            # TTS
            tts_backend_chain=_get_list(
                source,
                "TTS_BACKEND_CHAIN",
                ["edge_tts", "piper", "silent"],
            ),
            edge_tts_voice=_get_str(source, "EDGE_TTS_VOICE", "en-US-AndrewNeural"),
            piper_voice_path=_get_str(
                source, "PIPER_VOICE_PATH", "fixtures/assets/voices/en_US-ryan-high.onnx"
            ),
            # Optional backends
            hyperframes_enabled=_get_str(source, "HYPERFRAMES_ENABLED", "auto"),
            openmontage_home=_get_str(source, "OPENMONTAGE_HOME", ""),
            # Hermes
            hermes_bin=_get_str(source, "HERMES_BIN", ""),
            hermes_min_version=_get_str(source, "HERMES_MIN_VERSION", "0.16.0"),
            # Run control
            board=_get_str(source, "CWT_BOARD", "cwt-ads"),
            run_timeout_seconds=_get_int(source, "CWT_RUN_TIMEOUT_SECONDS", 5400),
            stall_threshold_seconds=_get_int(source, "CWT_STALL_THRESHOLD_SECONDS", 180),
            max_usd=_get_float(source, "CWT_MAX_USD", 2.00),
            # Compliance
            claims_gate_enabled=claims_gate_enabled,
            claims_max_rewrite_rounds=_get_int(source, "CLAIMS_MAX_REWRITE_ROUNDS", 3),
            creative_threshold=_get_float(source, "CREATIVE_THRESHOLD", 8.0),
            creative_max_rounds=_get_int(source, "CREATIVE_MAX_ROUNDS", 3),
            # Derived
            engine_defaults_to_hermes=engine_defaults_to_hermes,
        )

    def with_backend_chain(self, chain: Sequence[str]) -> "Settings":
        """Return a new Settings instance with updated video_backend_chain, enforcing Rule V4."""
        new_chain = list(chain)
        if not new_chain or new_chain[-1] != "local_ffmpeg":
            raise ConfigError(
                "Rule V4 violation: video_backend_chain must terminate in 'local_ffmpeg'. "
                f"Got: {new_chain}"
            )
        return replace(self, video_backend_chain=new_chain)
