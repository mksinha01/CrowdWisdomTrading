"""Tests for scripts and fixture recording/scrubbing (Story S34).

Verifies:
  - scrub_fixtures.py --check returns 1 on a fixture containing ?token=apify_api_x
  - scrub_fixtures.py rewrites that fixture and a second --check returns 0
  - the scrubber redacts Authorization: Bearer sk-or-v1-... inside a headers block
  - a rewritten fixture is LF-only (b"\\r\\n" not in read_bytes())
  - bootstrap.sh parses under bash -n
  - record_fixtures.py --help lists every stage; without --force on an existing fixture it skips
  - record_kanban_video.ps1 contains no 0.0.0.0 (Rule P1 / dashboard security)
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cwt.clients.http_cache import CachedResponse, request_cache_key
from scripts.fetch_piper_voice import PINNED_SHA256, verify_sha256
from scripts.record_fixtures import STAGES, RecordingHttpCache
from scripts.scrub_fixtures import (
    main as scrub_main,
    scrub_file,
    scrub_fixture_data,
    scrub_text,
)


def test_scrub_fixtures_check_fails_on_token(tmp_path: Path) -> None:
    """scrub_fixtures.py --check returns 1 on a fixture containing ?token=apify_api_x."""
    fixture_file = tmp_path / "test_fixture.json"
    dirty_data = {
        "status_code": 200,
        "headers": {"content-type": "application/json"},
        "body": {
            "request_url": "https://api.apify.com/v2/acts/x/runs?token=apify_api_secret12345&status=SUCCEEDED"
        },
    }
    fixture_file.write_text(json.dumps(dirty_data, indent=2), encoding="utf-8")

    exit_code = scrub_main(["--check", str(fixture_file)])
    assert exit_code == 1

    # Verify --check did not modify file content
    content = fixture_file.read_text(encoding="utf-8")
    assert "apify_api_secret12345" in content


def test_scrub_fixtures_rewrites_and_second_check_passes(tmp_path: Path) -> None:
    """scrub_fixtures.py rewrites that fixture and a second --check returns 0."""
    fixture_file = tmp_path / "test_fixture.json"
    dirty_data = {
        "status_code": 200,
        "headers": {"content-type": "application/json"},
        "body": {
            "request_url": "https://api.apify.com/v2/acts/x/runs?token=apify_api_x&limit=10"
        },
    }
    fixture_file.write_text(json.dumps(dirty_data, indent=2), encoding="utf-8")

    # Initial check fails
    exit_code_initial = scrub_main(["--check", str(fixture_file)])
    assert exit_code_initial == 1

    # Run scrub in rewrite mode (returns 1 to gate CI when secrets were scrubbed)
    exit_code_rewrite = scrub_main([str(fixture_file)])
    assert exit_code_rewrite == 1

    # Second check must pass with 0 (clean)
    exit_code_second = scrub_main(["--check", str(fixture_file)])
    assert exit_code_second == 0

    # Verify content was scrubbed
    data = json.loads(fixture_file.read_text(encoding="utf-8"))
    assert "token=<redacted>" in data["body"]["request_url"]
    assert "apify_api_x" not in data["body"]["request_url"]


def test_scrub_fixtures_redacts_auth_headers(tmp_path: Path) -> None:
    """The scrubber redacts Authorization: Bearer sk-or-v1-... inside a headers block."""
    fixture_file = tmp_path / "auth_fixture.json"
    data = {
        "status_code": 200,
        "headers": {
            "Authorization": "Bearer sk-or-v1-supersecretkey9999",
            "X-Api-Key": "tvly-secretkey8888",
            "Content-Type": "application/json",
        },
        "body": {"message": "ok"},
    }
    fixture_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    scrub_file(fixture_file, check_only=False)

    rewritten = json.loads(fixture_file.read_text(encoding="utf-8"))
    assert rewritten["headers"]["Authorization"] == "<redacted>"
    assert rewritten["headers"]["X-Api-Key"] == "<redacted>"
    assert rewritten["headers"]["Content-Type"] == "application/json"

    file_text = fixture_file.read_text(encoding="utf-8")
    assert "sk-or-v1-" not in file_text
    assert "tvly-" not in file_text


def test_scrubbed_fixture_is_lf_only(tmp_path: Path) -> None:
    """A rewritten fixture is LF-only (b"\\r\\n" not in read_bytes())."""
    fixture_file = tmp_path / "crlf_fixture.json"
    # Write initial content with CRLF
    content_crlf = (
        '{\r\n'
        '  "status_code": 200,\r\n'
        '  "headers": {\r\n'
        '    "Authorization": "Bearer sk-or-v1-secret"\r\n'
        '  },\r\n'
        '  "body": {}\r\n'
        '}\r\n'
    )
    fixture_file.write_bytes(content_crlf.encode("utf-8"))
    assert b"\r\n" in fixture_file.read_bytes()

    # Rewrite fixture
    scrub_file(fixture_file, check_only=False)

    # Must be LF-only
    raw_after = fixture_file.read_bytes()
    assert b"\r\n" not in raw_after
    assert b"\n" in raw_after


def test_bootstrap_sh_bash_n() -> None:
    """bootstrap.sh parses under bash -n and has LF line endings."""
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "scripts" / "bootstrap.sh"
    assert script_path.is_file()

    # Rule W8: LF line endings
    raw = script_path.read_bytes()
    assert b"\r\n" not in raw

    bash_bin = shutil.which("bash")
    if bash_bin:
        res = subprocess.run(
            [bash_bin, "-n", "scripts/bootstrap.sh"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"bash -n failed: {res.stderr}"


def test_record_fixtures_help_lists_every_stage() -> None:
    """record_fixtures.py --help lists every stage."""
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "record_fixtures.py"
    res = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    help_text = res.stdout.lower()
    for stage in ("ads", "patterns", "research", "storyboard", "claims", "render", "all"):
        assert stage in help_text
        assert stage in STAGES


def test_record_fixtures_skips_existing_without_force(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Without --force on an existing fixture it skips."""
    cache_root = tmp_path / "fixtures"
    url = "https://api.example.com/test-endpoint"
    method = "GET"
    body = {"query": "test"}

    # Seed an existing fixture in disk cache
    key = request_cache_key(method, url, body)
    fixture_dir = cache_root / key[:2]
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / f"{key}.json"

    seeded = CachedResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        body={"seeded": True},
    )
    seeded.to_disk(fixture_path)
    assert fixture_path.exists()

    # 1. Without force -> should hit cache and skip live request
    cache_no_force = RecordingHttpCache(root=cache_root, force=False)
    resp = asyncio.run(cache_no_force.request(method, url, json_body=body))
    assert resp.body == {"seeded": True}
    assert cache_no_force.hits == 1
    assert cache_no_force.misses == 0

    captured = capsys.readouterr()
    assert f"Skipping existing fixture {fixture_path.name}" in captured.out


def test_record_kanban_video_no_wildcard_binds() -> None:
    """record_kanban_video.ps1 contains no 0.0.0.0 (Rule P1 / dashboard security)."""
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    ps_file = scripts_dir / "record_kanban_video.ps1"
    assert ps_file.is_file()

    text = ps_file.read_text(encoding="utf-8")
    assert "0.0.0.0" not in text
    assert "127.0.0.1:9119" in text
    assert "--offline --record-pacing" in text

    # Verify no script contains 0.0.0.0
    for script_file in scripts_dir.iterdir():
        if script_file.is_file():
            content = script_file.read_text(encoding="utf-8", errors="ignore")
            assert "0.0.0.0" not in content, f"{script_file.name} contains 0.0.0.0"


def test_fetch_piper_voice_sha_mismatch(tmp_path: Path) -> None:
    """fetch_piper_voice refuses to install on a SHA-256 mismatch."""
    fake_voice = tmp_path / "fake_voice.onnx"
    fake_voice.write_bytes(b"corrupted or wrong audio binary data")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_sha256(fake_voice, expected_hash=PINNED_SHA256)
