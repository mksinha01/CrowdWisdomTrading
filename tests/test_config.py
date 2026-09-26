"""Tests for Settings and configuration loading (Story S01)."""
import logging

import pytest

from cwt.config import ConfigError, Settings, require_env


def test_settings_from_env_defaults():
    """Verify default Settings values when loaded from env."""
    settings = Settings.from_env()
    assert settings.board == "cwt-ads"
    assert settings.video_backend_chain == ["hyperframes", "openmontage", "local_ffmpeg"]
    assert settings.llm_provider == "openrouter"
    assert settings.apify_max_charge_usd == 1.00
    assert settings.claims_gate_enabled is True
    assert settings.video_width == 1080
    assert settings.video_height == 1920
    assert settings.engine_defaults_to_hermes is True


def test_rule_v4_termination_invariant():
    """Rule V4: video_backend_chain MUST terminate in local_ffmpeg."""
    with pytest.raises(ConfigError, match="Rule V4 violation"):
        Settings.from_env({
            "APIFY_MAX_CHARGE_USD": "1.00",
            "VIDEO_BACKEND_CHAIN": "hyperframes,openmontage",
        })

    with pytest.raises(ConfigError, match="Rule V4 violation"):
        Settings.from_env({
            "APIFY_MAX_CHARGE_USD": "1.00",
            "VIDEO_BACKEND_CHAIN": "",
        })

    # with_backend_chain must also enforce Rule V4
    settings = Settings.from_env()
    with pytest.raises(ConfigError, match="Rule V4 violation"):
        settings.with_backend_chain(["hyperframes", "openmontage"])

    new_settings = settings.with_backend_chain(["local_ffmpeg"])
    assert new_settings.video_backend_chain == ["local_ffmpeg"]
    assert new_settings is not settings


def test_rule_a2_apify_charge_invariant():
    """Rule A2: apify_max_charge_usd must be non-zero positive; raise if absent or 0."""
    # Absent
    with pytest.raises(ConfigError, match="Rule A2 violation"):
        Settings.from_env({
            "VIDEO_BACKEND_CHAIN": "local_ffmpeg",
        })

    # Empty string
    with pytest.raises(ConfigError, match="Rule A2 violation"):
        Settings.from_env({
            "APIFY_MAX_CHARGE_USD": "",
            "VIDEO_BACKEND_CHAIN": "local_ffmpeg",
        })

    # Zero
    with pytest.raises(ConfigError, match="Rule A2 violation"):
        Settings.from_env({
            "APIFY_MAX_CHARGE_USD": "0",
            "VIDEO_BACKEND_CHAIN": "local_ffmpeg",
        })

    # Negative
    with pytest.raises(ConfigError, match="Rule A2 violation"):
        Settings.from_env({
            "APIFY_MAX_CHARGE_USD": "-0.50",
            "VIDEO_BACKEND_CHAIN": "local_ffmpeg",
        })

    # Invalid string
    with pytest.raises(ConfigError, match="Rule A2 violation"):
        Settings.from_env({
            "APIFY_MAX_CHARGE_USD": "not-a-number",
            "VIDEO_BACKEND_CHAIN": "local_ffmpeg",
        })


def test_rule_c2_claims_gate_warning(caplog):
    """Rule C2: claims_gate_enabled must default True; log a WARNING if set to False."""
    with caplog.at_level(logging.WARNING):
        s = Settings.from_env({
            "APIFY_MAX_CHARGE_USD": "1.00",
            "CLAIMS_GATE_ENABLED": "0",
            "VIDEO_BACKEND_CHAIN": "local_ffmpeg",
        })
        assert s.claims_gate_enabled is False
        assert any("Rule C2" in rec.message for rec in caplog.records)


def test_require_env():
    """require_env returns value or raises ConfigError with obtain URL."""
    env = {
        "OPENROUTER_API_KEY": "sk-test-123",
    }
    assert require_env("OPENROUTER_API_KEY", env) == "sk-test-123"

    with pytest.raises(ConfigError, match="https://openrouter.ai/keys"):
        require_env("OPENROUTER_API_KEY", {})

    with pytest.raises(ConfigError, match="https://console.apify.com/account/integrations"):
        require_env("APIFY_TOKEN", {})


def test_settings_hashable():
    """Settings instance must be frozen and hashable."""
    s1 = Settings.from_env()
    s2 = Settings.from_env()
    assert hash(s1) == hash(s2)
    s_set = {s1, s2}
    assert len(s_set) == 1


def test_engine_defaults_to_hermes():
    """Decision B3: engine_defaults_to_hermes is False only when CWT_ENGINE=local."""
    s_default = Settings.from_env()
    assert s_default.engine_defaults_to_hermes is True

    s_local = Settings.from_env({
        "APIFY_MAX_CHARGE_USD": "1.00",
        "CWT_ENGINE": "local",
        "VIDEO_BACKEND_CHAIN": "local_ffmpeg",
    })
    assert s_local.engine_defaults_to_hermes is False
