"""Tests for tools/bundle.py — story S26.

Done-when criteria:
  - verify_artifact on a valid fixture → {"ok": True}
  - verify_artifact on a corrupted one → {"ok": False} with an error, no raise
  - verify_artifact on an unknown name → ok=False listing valid names
  - _cost_report sums a synthetic 10-line ledger correctly
  - Token allowlist: a README containing OPENROUTER_API_KEY or EXA_API_KEY raises
  - A stale storyboard.html is regenerated (JSON newer → mtime advances)
  - complete is False when final.mp4 is absent, and the run does not raise
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from cwt.config import Settings
from cwt.tools.bundle import (
    _FORBIDDEN_README_PATTERNS,
    _cost_report,
    _readme_submission,
    _regenerate_storyboard_html_if_stale,
    assemble_submission,
    verify_artifact,
)
from cwt.util.paths import RunPaths

# ── helpers ────────────────────────────────────────────────────────────────

_MINIMAL_ENV: dict[str, str] = {
    "LLM_PROVIDER": "openrouter",
    "OPENROUTER_API_KEY": "",
    "NVIDIA_API_KEY": "",
    "APIFY_TOKEN": "apify_api_TESTTOKEN1234",
    "TAVILY_API_KEY": "tvly-dev-TESTKEY5678",
    "EXA_API_KEY": "exa-key-999",
    "APIFY_MAX_CHARGE_USD": "1.00",
    "VIDEO_BACKEND_CHAIN": "local_ffmpeg",
    "CLAIMS_GATE_ENABLED": "1",
}


def _make_settings(**overrides: str) -> Settings:
    env = dict(_MINIMAL_ENV)
    env.update(overrides)
    return Settings.from_env(env=env)


def _make_run(tmp_path: Path) -> tuple[RunPaths, Settings]:
    run_dir = tmp_path / "runs" / "test-run"
    paths = RunPaths(run_dir).ensure()
    settings = _make_settings()
    return paths, settings


def _minimal_storyboard_dict() -> dict[str, Any]:
    """Bare-minimum Storyboard that passes all eleven validators."""
    return {
        "schema_version": 1,
        "meta": {
            "run_id": "20260926-1402-a7f3",
            "generated_at": "2026-09-26T14:31:52Z",
            "product": "CrowdWisdomTrading",
            "landing_url": "https://crowdwisdomtrading.com/",
            "total_duration_s": 35.0,
            "aspect_ratio": "9:16",
            "resolution": "1080x1920",
            "fps": 30,
            "angle": "unique_data",
            "angle_rationale": "Test rationale",
        },
        "visual_hook": {
            "archetype": "visual_shock",
            "first_3_seconds": "A single red candlestick on black.",
            "text_overlay": "TOO MANY VOICES.",
            "sound_design": "One synth note.",
            "stop_power_score": 8.9,
            "why_it_stops_the_scroll": "It externalises feeling.",
            "alternatives_considered": [],
        },
        "beats": [
            {"beat": "hook",      "start_s": 0.0,  "end_s": 3.0,  "tolerance_s": 1.2, "on_target": True},
            {"beat": "problem",   "start_s": 3.0,  "end_s": 8.0,  "tolerance_s": 2.0, "on_target": True},
            {"beat": "agitation", "start_s": 8.0,  "end_s": 14.0, "tolerance_s": 2.4, "on_target": True},
            {"beat": "mechanism", "start_s": 14.0, "end_s": 24.0, "tolerance_s": 3.0, "on_target": True},
            {"beat": "proof",     "start_s": 24.0, "end_s": 29.0, "tolerance_s": 3.0, "on_target": True},
            {"beat": "cta",       "start_s": 29.0, "end_s": 35.0, "tolerance_s": 3.0, "on_target": True},
        ],
        "shots": [
            {
                "id": "s01",
                "beat": "hook",
                "start_s": 0.0,
                "duration_s": 3.0,
                "description": "Red candlestick on black.",
                "subject": "abstract_market_data",
                "asset": {"kind": "generated_chart", "ref": "candle_01", "source": "internal"},
                "camera": {"move": "static", "intensity": 0.0, "lens_mm": 50, "depth_of_field": "deep", "stabilisation": "locked"},
                "lighting": {"key": "none", "contrast": "extreme", "colour_temp_k": 6500},
                "palette": ["#050505", "#1a1a1a", "#ef4444", "#22d3ee"],
                "composition": {"framing": "centre", "text_safe_area": {"top": 0.15, "bottom": 0.25}},
                "transition_in": {"type": "cut", "duration_s": 0.0},
                "transition_out": {"type": "cut", "duration_s": 0.0},
                "on_screen_text": [],
                "sfx": [],
            },
            {
                "id": "s02",
                "beat": "problem",
                "start_s": 3.0,
                "duration_s": 5.0,
                "description": "Text overlay on dark bg.",
                "subject": "abstract_market_data",
                "asset": {"kind": "generated_chart", "ref": "text_02", "source": "internal"},
                "camera": {"move": "static", "intensity": 0.0, "lens_mm": 50, "depth_of_field": "deep", "stabilisation": "locked"},
                "lighting": {"key": "practical_cyan", "contrast": "high", "colour_temp_k": 7000},
                "palette": ["#050505", "#0a0a0a", "#22d3ee", "#e2e8f0"],
                "composition": {"framing": "centre", "text_safe_area": {"top": 0.15, "bottom": 0.25}},
                "transition_in": {"type": "cut", "duration_s": 0.0},
                "transition_out": {"type": "cut", "duration_s": 0.0},
                "on_screen_text": [],
                "sfx": [],
            },
            {
                "id": "s03",
                "beat": "agitation",
                "start_s": 8.0,
                "duration_s": 6.0,
                "description": "Agitation shot.",
                "subject": "abstract_market_data",
                "asset": {"kind": "generated_chart", "ref": "agit_03", "source": "internal"},
                "camera": {"move": "static", "intensity": 0.0, "lens_mm": 50, "depth_of_field": "deep", "stabilisation": "locked"},
                "lighting": {"key": "none", "contrast": "extreme", "colour_temp_k": 6500},
                "palette": ["#050505", "#1a1a1a", "#ef4444", "#22d3ee"],
                "composition": {"framing": "centre", "text_safe_area": {"top": 0.15, "bottom": 0.25}},
                "transition_in": {"type": "cut", "duration_s": 0.0},
                "transition_out": {"type": "cut", "duration_s": 0.0},
                "on_screen_text": [],
                "sfx": [],
            },
            {
                "id": "s04",
                "beat": "mechanism",
                "start_s": 14.0,
                "duration_s": 10.0,
                "description": "Mechanism shot.",
                "subject": "abstract_market_data",
                "asset": {"kind": "generated_chart", "ref": "mech_04", "source": "internal"},
                "camera": {"move": "static", "intensity": 0.0, "lens_mm": 50, "depth_of_field": "deep", "stabilisation": "locked"},
                "lighting": {"key": "none", "contrast": "extreme", "colour_temp_k": 6500},
                "palette": ["#050505", "#1a1a1a", "#ef4444", "#22d3ee"],
                "composition": {"framing": "centre", "text_safe_area": {"top": 0.15, "bottom": 0.25}},
                "transition_in": {"type": "cut", "duration_s": 0.0},
                "transition_out": {"type": "cut", "duration_s": 0.0},
                "on_screen_text": [],
                "sfx": [],
            },
            {
                "id": "s05",
                "beat": "proof",
                "start_s": 24.0,
                "duration_s": 5.0,
                "description": "Proof shot.",
                "subject": "abstract_market_data",
                "asset": {"kind": "generated_chart", "ref": "proof_05", "source": "internal"},
                "camera": {"move": "static", "intensity": 0.0, "lens_mm": 50, "depth_of_field": "deep", "stabilisation": "locked"},
                "lighting": {"key": "none", "contrast": "extreme", "colour_temp_k": 6500},
                "palette": ["#050505", "#1a1a1a", "#ef4444", "#22d3ee"],
                "composition": {"framing": "centre", "text_safe_area": {"top": 0.15, "bottom": 0.25}},
                "transition_in": {"type": "cut", "duration_s": 0.0},
                "transition_out": {"type": "cut", "duration_s": 0.0},
                "on_screen_text": [],
                "sfx": [],
            },
            {
                "id": "s06",
                "beat": "cta",
                "start_s": 29.0,
                "duration_s": 6.0,
                "description": "Risk disclosure and CTA. Trading involves significant risk. Informational and educational only. Not financial advice.",
                "subject": "abstract_market_data",
                "asset": {"kind": "generated_chart", "ref": "cta_06", "source": "internal"},
                "camera": {"move": "static", "intensity": 0.0, "lens_mm": 50, "depth_of_field": "deep", "stabilisation": "locked"},
                "lighting": {"key": "none", "contrast": "extreme", "colour_temp_k": 6500},
                "palette": ["#050505", "#1a1a1a", "#ef4444", "#22d3ee"],
                "composition": {"framing": "centre", "text_safe_area": {"top": 0.15, "bottom": 0.25}},
                "transition_in": {"type": "cut", "duration_s": 0.0},
                "transition_out": {"type": "cut", "duration_s": 0.0},
                "on_screen_text": [
                    {
                        "text": "Trading involves significant risk. Informational and educational only. Not financial advice.",
                        "at_s": 29.1,
                        "until_s": 33.0,
                        "style": "disclaimer",
                        "position": "lower_third",
                    }
                ],
                "sfx": [],
            },
        ],
        "voiceover": {
            "voice_id": "en-US-AndrewNeural",
            "full_text": "Too many voices. Not financial advice.",
            "segments": [
                {"shot_id": "s01", "text": "Too many voices.", "start_s": 0.2, "end_s": 2.8},
                {"shot_id": "s06", "text": "Not financial advice.", "start_s": 29.1, "end_s": 32.0},
            ],
            "total_words": 7,
            "words_per_minute": 120,
        },
        "music": {
            "asset_ref": "tension_bed_90bpm",
            "intensity_curve": [{"at_s": 0.0, "level": 0.2}],
            "riser_at_s": [],
            "resolve_at_s": 29.0,
            "duck_under_vo": True,
        },
        "compliance": {
            "risk_disclosure_present": True,
            "risk_disclosure_shot_id": "s06",
            "risk_disclosure_text": "Trading involves significant risk. Informational and educational only. Not financial advice.",
            "safe_harbour_duration_s": 3.9,
            "methodology_adjacent": False,
            "claims_checked": True,
            "prohibited_facts_absent": True,
        },
        "generation": {
            "variants_written": 3,
            "variants": [
                {"angle": "pain",        "judge_score": 7.6, "beats_stolen_from": []},
                {"angle": "unique_data", "judge_score": 8.8, "beats_stolen_from": []},
                {"angle": "crowd_effect","judge_score": 7.9, "beats_stolen_from": []},
            ],
            "winner": "unique_data",
            "revision_rounds": 1,
            "creative_scores": {
                "hook_strength": 9.1,
                "mechanism_clarity": 8.8,
                "proof_credibility": 9.0,
                "emotional_arc": 7.9,
                "brand_fit": 9.2,
                "compliance_safety": 9.0,
                "weighted_mean": 8.83,
                "threshold": 8.0,
                "verdict": "pass",
            },
            "claims_rewrite_rounds": 0,
        },
    }


def _write_storyboard(paths: RunPaths, data: dict | None = None) -> Path:
    sb_path = paths.artifacts / "storyboard.json"
    payload = data if data is not None else _minimal_storyboard_dict()
    with open(sb_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, indent=2))
    return sb_path


# ── verify_artifact ────────────────────────────────────────────────────────

class TestVerifyArtifact:
    def test_valid_storyboard_returns_ok_true(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        _write_storyboard(paths)

        result = verify_artifact(settings=settings, paths=paths, name="storyboard")

        assert result["ok"] is True
        assert result["name"] == "storyboard"
        assert result["schema_version"] == 1
        assert result["error"] is None
        assert "storyboard.json" in result["path"]

    def test_corrupted_artifact_returns_ok_false_no_raise(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        # Write invalid JSON to the artifact
        sb_path = paths.artifacts / "storyboard.json"
        sb_path.write_text("{this is not valid json", encoding="utf-8")

        result = verify_artifact(settings=settings, paths=paths, name="storyboard")

        assert result["ok"] is False
        assert result["error"] is not None
        assert len(result["error"]) > 0
        # Must not raise — the exception must be caught inside verify_artifact

    def test_missing_artifact_returns_ok_false_no_raise(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)

        result = verify_artifact(settings=settings, paths=paths, name="storyboard")

        assert result["ok"] is False
        assert "does not exist" in (result["error"] or "")

    def test_schema_validation_failure_returns_ok_false_no_raise(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        # Write a JSON object that is not a valid Storyboard
        sb_path = paths.artifacts / "storyboard.json"
        sb_path.write_text(json.dumps({"schema_version": 1, "junk": True}), encoding="utf-8")

        result = verify_artifact(settings=settings, paths=paths, name="storyboard")

        assert result["ok"] is False
        assert result["error"] is not None

    def test_unknown_name_returns_ok_false_with_valid_names(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)

        result = verify_artifact(settings=settings, paths=paths, name="nope")

        assert result["ok"] is False
        assert "nope" in (result["error"] or "")
        # Error must list valid names
        assert "storyboard" in (result["error"] or "")
        assert "winning_ads" in (result["error"] or "")

    def test_never_raises_on_unknown_name(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        # Should not raise under any circumstances
        result = verify_artifact(settings=settings, paths=paths, name="__does_not_exist__")
        assert isinstance(result, dict)
        assert result["ok"] is False

    def test_error_is_truncated_to_400_chars(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        # Write an artifact with a deeply invalid structure so validation message is long
        sb_path = paths.artifacts / "storyboard.json"
        sb_path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

        result = verify_artifact(settings=settings, paths=paths, name="storyboard")

        assert result["ok"] is False
        assert result["error"] is not None
        assert len(result["error"]) <= 400

    def test_valid_winning_ads(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        payload = {
            "schema_version": 1,
            "generated_at": "2026-09-26T14:02:11Z",
            "query": {
                "keywords": ["trading signals"],
                "countries": ["US"],
                "window_start": "2026-08-27",
                "window_end": "2026-09-26",
                "window_days": 30,
            },
            "source": {
                "actor_id": "apify~facebook-ads-scraper",
                "actor_fallbacks_tried": [],
                "run_id": "abc",
                "dataset_id": "xyz",
                "items_returned": 1,
                "items_after_window_filter": 1,
                "actual_charge_usd": 0.25,
                "charge_cap_usd": 1.00,
            },
            "ranking": {
                "method": "weighted_longevity_signal",
                "weights": {"active_days": 0.45, "is_active": 0.25, "platform_breadth": 0.15, "recency": 0.15},
                "excluded_reasons": {},
            },
            "ads": [],
            "warnings": [],
        }
        (paths.artifacts / "winning_ads.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        result = verify_artifact(settings=settings, paths=paths, name="winning_ads")
        assert result["ok"] is True


# ── _cost_report ───────────────────────────────────────────────────────────

class TestCostReport:
    def _write_ledger(self, paths: RunPaths, records: list[dict]) -> None:
        ledger = paths.ledger
        with open(ledger, "w", encoding="utf-8", newline="\n") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")

    def test_sums_ten_line_ledger_correctly(self, tmp_path: Path) -> None:
        paths, _ = _make_run(tmp_path)
        # 5 cheap @ $0.01, 5 strong @ $0.05 → total $0.30
        records = []
        for i in range(5):
            records.append({
                "stage": f"stage_{i}",
                "model": "google/gemini-2.5-flash",
                "tier": "cheap",
                "cost_usd": 0.01,
                "run_id": "test-run",
            })
        for i in range(5):
            records.append({
                "stage": f"stage_{i}",
                "model": "anthropic/claude-sonnet-4.5",
                "tier": "strong",
                "cost_usd": 0.05,
                "run_id": "test-run",
            })
        self._write_ledger(paths, records)

        report = _cost_report(paths)

        assert report["calls"] == 10
        assert abs(report["total_usd"] - 0.30) < 1e-6
        assert abs(report["by_tier"]["cheap"]["usd"] - 0.05) < 1e-6
        assert report["by_tier"]["cheap"]["calls"] == 5
        assert abs(report["by_tier"]["strong"]["usd"] - 0.25) < 1e-6
        assert report["by_tier"]["strong"]["calls"] == 5

    def test_by_stage_aggregation(self, tmp_path: Path) -> None:
        paths, _ = _make_run(tmp_path)
        records = [
            {"stage": "extract", "model": "m", "tier": "cheap", "cost_usd": 0.10},
            {"stage": "extract", "model": "m", "tier": "cheap", "cost_usd": 0.05},
            {"stage": "write",   "model": "m", "tier": "strong", "cost_usd": 0.20},
        ]
        self._write_ledger(paths, records)

        report = _cost_report(paths)

        assert abs(report["by_stage"]["extract"] - 0.15) < 1e-6
        assert abs(report["by_stage"]["write"] - 0.20) < 1e-6

    def test_by_model_aggregation(self, tmp_path: Path) -> None:
        paths, _ = _make_run(tmp_path)
        records = [
            {"stage": "s", "model": "model-a", "tier": "cheap", "cost_usd": 0.03},
            {"stage": "s", "model": "model-a", "tier": "cheap", "cost_usd": 0.02},
            {"stage": "s", "model": "model-b", "tier": "strong", "cost_usd": 0.10},
        ]
        self._write_ledger(paths, records)

        report = _cost_report(paths)

        assert abs(report["by_model"]["model-a"] - 0.05) < 1e-6
        assert abs(report["by_model"]["model-b"] - 0.10) < 1e-6

    def test_apify_usd_from_winning_ads(self, tmp_path: Path) -> None:
        paths, _ = _make_run(tmp_path)
        payload = {
            "schema_version": 1,
            "generated_at": "2026-09-26T14:02:11Z",
            "query": {"keywords": [], "countries": [], "window_start": "2026-08-27", "window_end": "2026-09-26", "window_days": 30},
            "source": {
                "actor_id": "apify~facebook-ads-scraper",
                "actor_fallbacks_tried": [],
                "run_id": "abc",
                "dataset_id": "xyz",
                "items_returned": 10,
                "items_after_window_filter": 10,
                "actual_charge_usd": 0.37,
                "charge_cap_usd": 1.00,
            },
            "ranking": {"method": "weighted_longevity_signal", "weights": {}, "excluded_reasons": {}},
            "ads": [],
            "warnings": [],
        }
        (paths.artifacts / "winning_ads.json").write_text(json.dumps(payload), encoding="utf-8")

        report = _cost_report(paths)

        assert abs(report["external"]["apify"]["usd"] - 0.37) < 1e-6
        assert abs(report["grand_total_usd"] - 0.37) < 1e-6

    def test_tavily_and_exa_are_zero_free_tier(self, tmp_path: Path) -> None:
        paths, _ = _make_run(tmp_path)

        report = _cost_report(paths)

        assert report["external"]["tavily"]["usd"] == 0.00
        assert report["external"]["tavily"]["free_tier"] is True
        assert report["external"]["exa"]["usd"] == 0.00
        assert report["external"]["exa"]["free_tier"] is True

    def test_empty_ledger_returns_zeros(self, tmp_path: Path) -> None:
        paths, _ = _make_run(tmp_path)

        report = _cost_report(paths)

        assert report["calls"] == 0
        assert report["total_usd"] == 0.0

    def test_cost_band_warning_below_low(self, tmp_path: Path) -> None:
        paths, _ = _make_run(tmp_path)
        # Empty ledger → grand_total = 0 → below band
        report = _cost_report(paths)
        assert report["warning"] is not None
        assert "below" in report["warning"].lower()

    def test_cost_band_warning_above_high(self, tmp_path: Path) -> None:
        paths, _ = _make_run(tmp_path)
        records = [
            {"stage": "s", "model": "m", "tier": "strong", "cost_usd": 3.00}
        ]
        self._write_ledger(paths, records)

        report = _cost_report(paths)

        assert report["warning"] is not None
        assert "exceeds" in report["warning"].lower()

    def test_no_warning_within_band(self, tmp_path: Path) -> None:
        paths, _ = _make_run(tmp_path)
        records = [
            {"stage": "s", "model": "m", "tier": "cheap", "cost_usd": 0.30}
        ]
        self._write_ledger(paths, records)

        report = _cost_report(paths)

        assert report["warning"] is None


# ── _readme_submission — token allowlist ────────────────────────────────────

class TestReadmeAllowlist:
    def _make_manifest(self) -> dict:
        return {"offline": True, "command": "cwt run --engine local --offline"}

    def _make_cost(self) -> dict:
        return {
            "total_usd": 0.0,
            "grand_total_usd": 0.0,
            "warning": None,
            "external": {
                "apify": {"usd": 0.0},
                "tavily": {"credits_used": 0, "free_tier": True, "usd": 0.0},
                "exa": {"searches": 0, "free_tier": True, "usd": 0.0},
            },
        }

    def test_readme_contains_apify_and_tavily_tokens(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        cost = self._make_cost()
        manifest = self._make_manifest()

        content = _readme_submission(
            settings=settings, paths=paths, cost=cost, manifest=manifest
        )

        assert "APIFY_TOKEN" in content
        assert "TAVILY_API_KEY" in content
        # Must contain actual token values
        assert settings.apify_token in content
        assert settings.tavily_api_key in content

    def test_openrouter_key_raises(self, tmp_path: Path) -> None:
        """README containing OPENROUTER_API_KEY must raise (Rule R1)."""
        paths, settings = _make_run(tmp_path)
        cost = self._make_cost()
        manifest = self._make_manifest()
        # Give settings a real-looking OpenRouter key
        bad_settings = _make_settings(OPENROUTER_API_KEY="sk-or-v1-badkeyvalue")

        # The README generator should detect "sk-or-v1-" in the token value
        # if it leaks via settings — but the forbidden patterns list catches
        # the *label* OPENROUTER_API_KEY if any caller inserts it manually.
        # We test via the pattern list: inject via a monkey-patched apify_token
        # that starts with a forbidden prefix.
        class _SettingsBadApify(Settings):
            @property
            def apify_token(self):  # type: ignore[override]
                return "sk-or-v1-OOPS"

        # Directly test the allowlist check by calling with a token that triggers
        # a forbidden pattern in the output.
        # The simplest approach: call _readme_submission with settings that have
        # apify_token containing a forbidden prefix — sk-or-v1-.
        settings_obj = _make_settings()

        # Patch: write a README manually containing the forbidden string, then
        # verify the guard in _readme_submission catches it by having the apify
        # token itself be the offender.
        import cwt.tools.bundle as bundle_mod
        import cwt.config as config_mod

        # Monkeypatch: temporarily inject the forbidden pattern via
        # settings.apify_token.  We do this at the attribute level.
        orig = settings_obj.apify_token
        # frozen dataclass — use object.__setattr__
        object.__setattr__(settings_obj, "apify_token", "sk-or-v1-INJECTED")

        with pytest.raises(ValueError, match="Rule R1"):
            _readme_submission(
                settings=settings_obj, paths=paths, cost=cost, manifest=manifest
            )

        # Restore (not strictly necessary for frozen, but good hygiene)
        object.__setattr__(settings_obj, "apify_token", orig)

    def test_exa_key_label_in_readme_raises(self, tmp_path: Path) -> None:
        """If EXA_API_KEY label appears in README content it must raise."""
        paths, settings = _make_run(tmp_path)
        cost = self._make_cost()
        manifest = self._make_manifest()

        # Inject via apify_token containing the forbidden label
        object.__setattr__(settings, "apify_token", "apify_api_EXA_API_KEY=leaked")

        with pytest.raises(ValueError, match="Rule R1"):
            _readme_submission(
                settings=settings, paths=paths, cost=cost, manifest=manifest
            )

    def test_forbidden_patterns_list_is_complete(self) -> None:
        """All patterns from Rule R1 are present in _FORBIDDEN_README_PATTERNS."""
        patterns = _FORBIDDEN_README_PATTERNS
        assert "OPENROUTER_API_KEY" in patterns
        assert "NVIDIA_API_KEY" in patterns
        assert "EXA_API_KEY" in patterns
        assert "sk-or-v1-" in patterns
        assert "nvapi-" in patterns


# ── stale storyboard.html regeneration ─────────────────────────────────────

class TestStaleStoryboardHtml:
    def test_stale_html_is_regenerated(self, tmp_path: Path) -> None:
        """If storyboard.json is newer than storyboard.html, the HTML is regenerated."""
        paths, settings = _make_run(tmp_path)
        sb_json = _write_storyboard(paths)

        # Write a stale HTML with old content
        sb_html = paths.artifacts / "storyboard.html"
        sb_html.write_text("<html>OLD CONTENT</html>", encoding="utf-8")

        # Force storyboard.json to appear newer by sleeping a small amount
        # and re-touching it (or adjusting mtime explicitly).
        old_mtime = sb_html.stat().st_mtime
        time.sleep(0.05)  # ensure filesystem time granularity
        sb_json.touch()   # update mtime

        assert sb_json.stat().st_mtime > sb_html.stat().st_mtime

        _regenerate_storyboard_html_if_stale(
            sb_json_src=sb_json,
            sb_html_src=sb_html,
            settings=settings,
            paths=paths,
        )

        new_mtime = sb_html.stat().st_mtime
        # HTML should be re-written (mtime should be >= old_mtime, content different)
        assert new_mtime >= old_mtime
        new_content = sb_html.read_text(encoding="utf-8")
        assert "<html>OLD CONTENT</html>" != new_content

    def test_fresh_html_is_not_regenerated(self, tmp_path: Path) -> None:
        """If HTML is newer than JSON, it is left alone."""
        paths, settings = _make_run(tmp_path)
        sb_json = _write_storyboard(paths)

        time.sleep(0.05)
        # Write HTML AFTER JSON so it's newer
        sb_html = paths.artifacts / "storyboard.html"
        sb_html.write_text("<html>FRESH CONTENT</html>", encoding="utf-8")

        assert sb_html.stat().st_mtime >= sb_json.stat().st_mtime

        _regenerate_storyboard_html_if_stale(
            sb_json_src=sb_json,
            sb_html_src=sb_html,
            settings=settings,
            paths=paths,
        )

        assert sb_html.read_text(encoding="utf-8") == "<html>FRESH CONTENT</html>"


# ── assemble_submission ────────────────────────────────────────────────────

class TestAssembleSubmission:
    def test_complete_false_when_final_mp4_absent(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        # Do NOT write final.mp4 — everything else can be absent too
        result = assemble_submission(settings=settings, paths=paths)

        assert result["complete"] is False
        assert "final.mp4" in result["missing"]

    def test_does_not_raise_when_files_absent(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        # Completely empty run directory — should not raise
        result = assemble_submission(settings=settings, paths=paths)
        assert isinstance(result, dict)

    def test_complete_true_when_required_files_present(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        # Write required files
        final_mp4 = paths.render / "final.mp4"
        final_mp4.write_bytes(b"fake mp4 content")
        _write_storyboard(paths)

        result = assemble_submission(settings=settings, paths=paths)

        # complete=True means no REQUIRED files missing
        assert result["complete"] is True
        assert "final.mp4" not in result["missing"]
        assert "storyboard.json" not in result["missing"]

    def test_submission_dir_created(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        result = assemble_submission(settings=settings, paths=paths)
        assert Path(result["dir"]).is_dir()

    def test_cost_report_always_written(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        result = assemble_submission(settings=settings, paths=paths)
        submission_dir = Path(result["dir"])
        assert (submission_dir / "cost_report.json").is_file()

    def test_readme_always_written(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        result = assemble_submission(settings=settings, paths=paths)
        submission_dir = Path(result["dir"])
        readme = submission_dir / "README-SUBMISSION.md"
        assert readme.is_file()
        content = readme.read_text(encoding="utf-8")
        assert "APIFY_TOKEN" in content
        assert "TAVILY_API_KEY" in content

    def test_final_mp4_copied_to_submission(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        (paths.render / "final.mp4").write_bytes(b"mp4data")
        _write_storyboard(paths)

        result = assemble_submission(settings=settings, paths=paths)
        submission_dir = Path(result["dir"])
        assert (submission_dir / "final.mp4").is_file()

    def test_storyboard_json_copied(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        _write_storyboard(paths)

        result = assemble_submission(settings=settings, paths=paths)
        submission_dir = Path(result["dir"])
        assert (submission_dir / "storyboard.json").is_file()

    def test_claims_report_glob_pattern(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        # Write a claims_report with a non-standard name
        claims_src = paths.artifacts / "claims_report_round2.json"
        claims_src.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

        result = assemble_submission(settings=settings, paths=paths)
        submission_dir = Path(result["dir"])
        assert (submission_dir / "claims_report.json").is_file()

    def test_optional_contact_sheet_absence_does_not_set_complete_false(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        (paths.render / "final.mp4").write_bytes(b"mp4data")
        _write_storyboard(paths)
        # Do NOT write contact_sheet.png

        result = assemble_submission(settings=settings, paths=paths)

        # complete should still be True (contact_sheet.png is optional)
        assert result["complete"] is True

    def test_return_dict_shape(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        result = assemble_submission(settings=settings, paths=paths)

        assert "dir" in result
        assert "files" in result
        assert "total_bytes" in result
        assert "missing" in result
        assert "complete" in result
        assert isinstance(result["files"], list)
        assert isinstance(result["missing"], list)
        assert isinstance(result["total_bytes"], int)
        assert isinstance(result["complete"], bool)

    def test_total_bytes_nonzero_when_files_present(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        (paths.render / "final.mp4").write_bytes(b"x" * 1024)
        _write_storyboard(paths)

        result = assemble_submission(settings=settings, paths=paths)
        assert result["total_bytes"] > 0

    def test_render_manifest_copied(self, tmp_path: Path) -> None:
        paths, settings = _make_run(tmp_path)
        manifest_data = {
            "schema_version": 1,
            "run_id": "test-run",
            "generated_at": "2026-09-26T14:02:11Z",
            "offline": True,
            "command": "cwt run --engine local --offline",
            "backend_used": "local_ffmpeg",
            "backends_tried": [{"backend": "hyperframes", "available": False, "reason": "not installed"}],
            "ffmpeg_argv": ["ffmpeg", "-y", "-i", "input.mp4", "output.mp4"],
            "output_path": "render/final.mp4",
            "duration_s": 35.0,
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "asset_licences": [],
        }
        (paths.artifacts / "render_manifest.json").write_text(
            json.dumps(manifest_data), encoding="utf-8"
        )

        result = assemble_submission(settings=settings, paths=paths)
        submission_dir = Path(result["dir"])
        assert (submission_dir / "render_manifest.json").is_file()

    def test_offline_mode_recorded(self, tmp_path: Path) -> None:
        """assemble_submission records offline mode from render_manifest."""
        paths, settings = _make_run(tmp_path)
        manifest_data = {
            "schema_version": 1,
            "run_id": "test-run",
            "generated_at": "2026-09-26T14:02:11Z",
            "offline": True,
            "command": "cwt run --engine local --offline",
            "backend_used": "local_ffmpeg",
            "backends_tried": [],
            "ffmpeg_argv": [],
            "output_path": "render/final.mp4",
            "duration_s": 35.0,
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "asset_licences": [],
        }
        (paths.artifacts / "render_manifest.json").write_text(
            json.dumps(manifest_data), encoding="utf-8"
        )

        result = assemble_submission(settings=settings, paths=paths)
        assert result["offline"] is True
        assert result["command"] == "cwt run --engine local --offline"


# ── gitignore check ────────────────────────────────────────────────────────

class TestGitignore:
    def test_submission_dir_in_gitignore(self) -> None:
        """submission/ must be in .gitignore (Rule R1 — the token file cannot be committed)."""
        root = Path(__file__).parent.parent
        gitignore = root / ".gitignore"
        assert gitignore.is_file(), ".gitignore not found at project root"
        content = gitignore.read_text(encoding="utf-8")
        assert "submission/" in content, (
            "Rule R1 violation: 'submission/' must be in .gitignore — "
            "the token file in README-SUBMISSION.md must never be committed."
        )
