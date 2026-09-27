"""Tests for cwt.bootstrap module."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from cwt.bootstrap import (
    PROFILE_NAMES,
    SKILL_NAMES,
    create_profiles,
    hermes_home,
    install_plugin,
    install_skills,
    merge_config,
    verify_dag_assignees,
    write_profile_configs,
)
from cwt.hermes.dag import DAG_SPEC


class TestHermesHome:
    def test_default_hermes_home(self):
        with patch("pathlib.Path.home", return_value=Path("/fake/home")):
            home = hermes_home()
            assert home == Path("/fake/home/.hermes")

    def test_custom_hermes_home(self):
        with patch.dict(os.environ, {"HERMES_HOME": "/custom/path"}):
            home = hermes_home()
            assert home == Path("/custom/path")


class TestMergeConfig:
    def test_merge_adds_new_keys(self, tmp_path: Path):
        template = tmp_path / "template.yaml"
        target = tmp_path / "target.yaml"

        template.write_text("a: 1\nb: 2\n", encoding="utf-8")
        target.write_text("a: 10\n", encoding="utf-8")

        result = merge_config(template, target)

        assert "b" in result["added"]
        assert "a" not in result["changed"]
        # a has different values, so it's a conflict that gets skipped
        assert "a" in result["skipped"]

        with target.open() as f:
            data = yaml.safe_load(f)
        assert data["a"] == 10  # existing preserved
        assert data["b"] == 2   # new added

    def test_merge_changes_with_force(self, tmp_path: Path):
        template = tmp_path / "template.yaml"
        target = tmp_path / "target.yaml"

        template.write_text("a: 1\n", encoding="utf-8")
        target.write_text("a: 10\n", encoding="utf-8")

        result = merge_config(template, target, force=True)

        assert "a" in result["changed"]
        assert "a" not in result["skipped"]

        with target.open() as f:
            data = yaml.safe_load(f)
        assert data["a"] == 1

    def test_merge_skips_without_force(self, tmp_path: Path):
        template = tmp_path / "template.yaml"
        target = tmp_path / "target.yaml"

        template.write_text("a: 1\n", encoding="utf-8")
        target.write_text("a: 10\n", encoding="utf-8")

        result = merge_config(template, target, force=False)

        assert "a" in result["skipped"]
        assert "a" not in result["changed"]

        with target.open() as f:
            data = yaml.safe_load(f)
        assert data["a"] == 10  # unchanged

    def test_deep_merge(self, tmp_path: Path):
        template = tmp_path / "template.yaml"
        target = tmp_path / "target.yaml"

        template.write_text("a:\n  b: 1\n  c: 2\n", encoding="utf-8")
        target.write_text("a:\n  b: 10\n", encoding="utf-8")

        result = merge_config(template, target)

        assert "a.c" in result["added"]
        assert "a.b" not in result["changed"]

        with target.open() as f:
            data = yaml.safe_load(f)
        assert data["a"]["b"] == 10
        assert data["a"]["c"] == 2


class TestCreateProfiles:
    def test_create_profiles_idempotent(self, tmp_path: Path):
        # Mock hermes binary
        mock_hermes = tmp_path / "hermes"
        mock_hermes.write_text("#!/bin/sh\necho 'profile created'", encoding="utf-8")
        mock_hermes.chmod(0o755)

        with patch("shutil.which", return_value=str(mock_hermes)):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                # First call
                created1 = create_profiles(hermes_home=tmp_path, names=["test-profile"])
                # Simulate the profile directory being created
                (tmp_path / "profiles" / "test-profile").mkdir(parents=True, exist_ok=True)
                # Second call (idempotent)
                created2 = create_profiles(hermes_home=tmp_path, names=["test-profile"])

        assert "test-profile" in created1
        assert created2 == []  # already exists


class TestInstallPlugin:
    def test_install_plugin_correct_depth(self, tmp_path: Path):
        source = tmp_path / "source_plugin"
        source.mkdir()
        (source / "plugin.yaml").write_text("name: test\n", encoding="utf-8")
        (source / "__init__.py").write_text("", encoding="utf-8")
        (source / "schemas.py").write_text("", encoding="utf-8")
        (source / "handlers.py").write_text("", encoding="utf-8")

        hermes_home = tmp_path / "hermes_home"
        target = install_plugin(hermes_home=hermes_home, source=source)

        assert target == hermes_home / "plugins" / "cwt"
        assert target.exists()
        assert (target / "plugin.yaml").exists()

    def test_install_plugin_rejects_deep_nesting(self, tmp_path: Path):
        source = tmp_path / "source_plugin"
        source.mkdir()
        (source / "plugin.yaml").write_text("name: test\n", encoding="utf-8")
        (source / "__init__.py").write_text("", encoding="utf-8")
        (source / "schemas.py").write_text("", encoding="utf-8")
        (source / "handlers.py").write_text("", encoding="utf-8")
        # Create a subdirectory (invalid depth)
        (source / "subdir").mkdir()

        hermes_home = tmp_path / "hermes_home"

        with pytest.raises(RuntimeError, match="invalid depth"):
            install_plugin(hermes_home=hermes_home, source=source)


class TestInstallSkills:
    def test_install_all_skills(self, tmp_path: Path):
        source = tmp_path / "skills"
        source.mkdir()
        for skill in SKILL_NAMES:
            skill_dir = source / skill
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(f"# {skill}\n", encoding="utf-8")

        hermes_home = tmp_path / "hermes_home"
        installed = install_skills(hermes_home=hermes_home, source=source)

        assert len(installed) == len(SKILL_NAMES)
        for skill in SKILL_NAMES:
            assert (hermes_home / "skills" / skill / "SKILL.md").exists()


class TestVerifyDagAssignees:
    def test_all_assignees_covered(self):
        missing = verify_dag_assignees(PROFILE_NAMES)
        assert missing == [], f"Missing profiles for assignees: {missing}"

    def test_missing_assignee_detected(self):
        incomplete = [p for p in PROFILE_NAMES if p != "cwt-script-writer"]
        missing = verify_dag_assignees(incomplete)
        assert "cwt-script-writer" in missing


class TestWriteProfileConfigs:
    def test_writes_all_three_files(self, tmp_path: Path):
        from cwt.config import Settings

        # Minimal settings for testing
        settings = Settings(
            llm_provider="openrouter",
            openrouter_api_key="test",
            openrouter_base_url="https://openrouter.ai/api/v1",
            nvidia_api_key="",
            nvidia_base_url="",
            model_cheap="test",
            model_strong="test",
            model_fallbacks=[],
            llm_max_concurrency=4,
            llm_json_repair_attempts=2,
            llm_timeout_seconds=120.0,
            apify_token="",
            apify_ads_actor_id="",
            apify_ads_actor_fallbacks=[],
            apify_max_items=60,
            apify_max_charge_usd=1.0,
            apify_run_timeout_seconds=900,
            tavily_api_key="",
            exa_api_key="",
            video_backend_chain=["local_ffmpeg"],
            ffmpeg_bin="",
            ffprobe_bin="",
            video_width=1080,
            video_height=1920,
            video_fps=30,
            video_min_seconds=30,
            video_max_seconds=60,
            video_loudness_lufs=-14.0,
            video_true_peak_dbtp=-1.5,
            tts_backend_chain=["edge_tts", "piper", "silent"],
            edge_tts_voice="en-US-AndrewNeural",
            piper_voice_path="",
            hyperframes_enabled="auto",
            openmontage_home="",
            hermes_bin="",
            hermes_min_version="0.16.0",
            board="cwt-ads",
            run_timeout_seconds=5400,
            stall_threshold_seconds=180,
            max_usd=2.0,
            claims_gate_enabled=True,
            claims_max_rewrite_rounds=3,
            creative_threshold=8.0,
            creative_max_rounds=3,
            engine_defaults_to_hermes=True,
        )

        write_profile_configs(hermes_home=tmp_path, names=PROFILE_NAMES, settings=settings)

        for name in PROFILE_NAMES:
            profile_dir = tmp_path / "profiles" / name
            assert (profile_dir / "config.yaml").exists()
            assert (profile_dir / ".env").exists()
            assert (profile_dir / "SOUL.md").exists()

            # Check .env has the four required keys
            env_content = (profile_dir / ".env").read_text()
            for key in ["APIFY_TOKEN", "TAVILY_API_KEY", "EXA_API_KEY", "OPENROUTER_API_KEY"]:
                assert key in env_content

            # Check config.yaml has model and toolsets
            config_content = (profile_dir / "config.yaml").read_text()
            assert "model:" in config_content
            assert "tools:" in config_content
            assert "kanban" in config_content


class TestInstallHermesAssets:
    def test_install_hermes_assets_missing_dag_assignee_fails(self):
        # This test verifies that verify_dag_assignees catches missing profiles
        # We test the verification logic directly rather than the full install
        incomplete_profiles = [p for p in PROFILE_NAMES if p != "cwt-script-writer"]
        missing = verify_dag_assignees(incomplete_profiles)
        assert "cwt-script-writer" in missing

    def test_install_hermes_assets_all_assignees_covered(self):
        # All profiles cover all assignees
        missing = verify_dag_assignees(PROFILE_NAMES)
        assert missing == []


class TestProfileNamesMatchDag:
    def test_profile_names_cover_all_assignees(self):
        """Ensure PROFILE_NAMES covers every assignee in DAG_SPEC."""
        assignees = {card.assignee for card in DAG_SPEC}
        missing = assignees - set(PROFILE_NAMES)
        assert not missing, f"PROFILE_NAMES missing: {missing}"

    def test_creative_director_included_despite_no_card(self):
        """cwt-creative-director has no card but is required as a reviewer."""
        assert "cwt-creative-director" in PROFILE_NAMES


class TestSkillFrontmatter:
    def test_all_skills_have_requires_toolsets_cwt(self):
        """Every skill must declare requires_toolsets: [cwt] in frontmatter."""
        skills_root = Path(__file__).parent.parent / "skills"
        for skill in SKILL_NAMES:
            skill_file = skills_root / skill / "SKILL.md"
            assert skill_file.exists(), f"Missing skill file: {skill_file}"

            content = skill_file.read_text()
            assert content.startswith("---"), f"{skill}: missing YAML frontmatter"

            # Parse frontmatter
            parts = content.split("---", 2)
            frontmatter = yaml.safe_load(parts[1])

            meta = frontmatter.get("metadata", {})
            hermes_meta = meta.get("hermes", {})
            requires = hermes_meta.get("requires_toolsets", [])
            assert "cwt" in requires, f"{skill}: requires_toolsets must include 'cwt'"
