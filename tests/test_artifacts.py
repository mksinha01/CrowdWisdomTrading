"""Tests for ArtifactStore and provenance tracking (Story S07).

Spec: doc/video-ads-agent.md §3.0 conventions, §9.6 resume layers, §3.5 schemas.
Story: doc/stories/S07-artifact-store-and-provenance.md.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cwt.domain.artifacts import (
    ARTIFACT_MODELS,
    ARTIFACT_NAMES,
    ArtifactError,
    ArtifactStore,
    Provenance,
    sha256_file,
)
from cwt.domain.models import (
    SCHEMA_VERSION,
    ArtifactVersionError,
    Storyboard,
    WinningAds,
)
from cwt.util.jsonio import read_json, write_json
from cwt.util.paths import RunPaths

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(filename: str) -> dict[str, Any]:
    path = FIXTURES_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


# ===========================================================================
# 1. Registry tests
# ===========================================================================


def test_artifact_names_and_models_registry() -> None:
    """ARTIFACT_NAMES and ARTIFACT_MODELS are the single authoritative registry."""
    assert ARTIFACT_NAMES == (
        "winning_ads",
        "ad_patterns",
        "research_brief",
        "storyboard",
        "hook_candidates",
        "review_verdict",
        "claims_report",
        "render_manifest",
    )
    assert set(ARTIFACT_MODELS.keys()) == set(ARTIFACT_NAMES)
    for name in ARTIFACT_NAMES:
        model_cls = ARTIFACT_MODELS[name]
        assert model_cls is not None
        assert issubclass(model_cls, object)


# ===========================================================================
# 2. sha256_file utility
# ===========================================================================


def test_sha256_file_chunks(tmp_path: Path) -> None:
    """sha256_file reads in 64 KiB chunks and produces lowercase hex."""
    data = b"CWT_TEST_CHUNK_DATA_" * 4000  # ~80 KiB (> 64 KiB chunk)
    test_file = tmp_path / "chunk_test.bin"
    test_file.write_bytes(data)

    expected = hashlib.sha256(data).hexdigest().lower()
    computed = sha256_file(test_file)
    assert computed == expected
    assert computed.islower()


# ===========================================================================
# 3. Round-trip all eight artifacts
# ===========================================================================


@pytest.mark.parametrize("name", ARTIFACT_NAMES)
def test_roundtrip_all_eight_artifacts(tmp_path: Path, name: str) -> None:
    """Round-trip all 8 artifact names using §3 fixtures: write -> read -> get_if_valid."""
    run_dir = tmp_path / "runs" / f"run-{name}"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id=f"run-{name}")

    fixture_data = _load_fixture(f"{name}.json")
    model_cls = ARTIFACT_MODELS[name]
    model = model_cls.model_validate(fixture_data)

    # Initial get_if_valid should return None (not written yet)
    assert store.get_if_valid(name) is None
    assert model.schema_version == SCHEMA_VERSION

    # Write
    written_path = store.write(name, model)
    assert written_path == store.path_for(name)
    assert written_path.is_file()

    # Read (strict)
    read_model = store.read(name)
    assert read_model == model

    # get_if_valid (resume check without inputs)
    valid_model = store.get_if_valid(name)
    assert valid_model == model


def test_provenance_dataclass() -> None:
    """Provenance dataclass is frozen and has exact fields."""
    prov = Provenance(
        name="storyboard",
        sha256="abc123",
        inputs={"brief": "def456"},
        written_at="2026-09-26T14:02:11Z",
        run_id="run-1",
    )
    assert prov.name == "storyboard"
    assert prov.sha256 == "abc123"
    assert prov.inputs == {"brief": "def456"}
    assert prov.written_at == "2026-09-26T14:02:11Z"
    assert prov.run_id == "run-1"
    with pytest.raises(Exception):
        prov.name = "mutated"  # type: ignore[misc]


# ===========================================================================
# 4. Provenance tracking & append-only history
# ===========================================================================


def test_provenance_created_and_appended(tmp_path: Path) -> None:
    """write() creates and appends Provenance records to artifacts/provenance.json."""
    run_dir = tmp_path / "runs" / "run-prov"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-prov-001")

    # Create dummy input files
    inp1 = tmp_path / "input1.txt"
    inp1.write_text("input-data-1", encoding="utf-8")
    inp2 = tmp_path / "input2.json"
    inp2.write_text('{"foo": "bar"}', encoding="utf-8")

    inputs_map = {"file1": inp1, "file2": inp2}

    # Write hook_candidates with inputs
    hook_model = ARTIFACT_MODELS["hook_candidates"].model_validate(
        _load_fixture("hook_candidates.json")
    )
    store.write("hook_candidates", hook_model, inputs=inputs_map)

    assert paths.provenance.is_file()
    prov_data = read_json(paths.provenance)
    assert isinstance(prov_data, list)
    assert len(prov_data) == 1

    rec1 = prov_data[0]
    assert rec1["name"] == "hook_candidates"
    assert rec1["run_id"] == "run-prov-001"
    assert rec1["sha256"] == sha256_file(store.path_for("hook_candidates"))
    assert rec1["inputs"] == {
        "file1": sha256_file(inp1),
        "file2": sha256_file(inp2),
    }
    assert "written_at" in rec1

    # Write another artifact review_verdict with input
    verdict_model = ARTIFACT_MODELS["review_verdict"].model_validate(
        _load_fixture("review_verdict.json")
    )
    store.write("review_verdict", verdict_model, inputs={"hook": store.path_for("hook_candidates")})

    prov_data2 = read_json(paths.provenance)
    assert isinstance(prov_data2, list)
    assert len(prov_data2) == 2
    assert prov_data2[0]["name"] == "hook_candidates"
    assert prov_data2[1]["name"] == "review_verdict"


# ===========================================================================
# 5. Resume mechanism (get_if_valid) & input invalidation
# ===========================================================================


def test_get_if_valid_input_invalidation(tmp_path: Path) -> None:
    """get_if_valid returns model when inputs match, returns None when input modified."""
    run_dir = tmp_path / "runs" / "run-resume"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-resume-001")

    # Input file
    brief_file = tmp_path / "brief.json"
    write_json(brief_file, {"topic": "trading signals"})

    storyboard_model = Storyboard.model_validate(_load_fixture("storyboard.json"))
    store.write("storyboard", storyboard_model, inputs={"brief": brief_file})

    # Initially valid
    assert store.get_if_valid("storyboard", inputs={"brief": brief_file}) == storyboard_model

    # Rewrite input file with different content
    write_json(brief_file, {"topic": "stock alerts altered"})

    # Must return None instantly because input hash mismatch
    assert store.get_if_valid("storyboard", inputs={"brief": brief_file}) is None


def test_get_if_valid_input_deleted_or_missing(tmp_path: Path) -> None:
    """get_if_valid returns None if an input file is deleted/missing."""
    run_dir = tmp_path / "runs" / "run-missing-inp"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-missing-001")

    inp_file = tmp_path / "dep.txt"
    inp_file.write_text("initial dependency", encoding="utf-8")

    model = Storyboard.model_validate(_load_fixture("storyboard.json"))
    store.write("storyboard", model, inputs={"dep": inp_file})

    assert store.get_if_valid("storyboard", inputs={"dep": inp_file}) == model

    # Delete input file
    inp_file.unlink()

    # Never raises; returns None
    assert store.get_if_valid("storyboard", inputs={"dep": inp_file}) is None


def test_get_if_valid_input_set_changed(tmp_path: Path) -> None:
    """get_if_valid returns None if the set of input labels changes."""
    run_dir = tmp_path / "runs" / "run-inp-set"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-inp-set-001")

    f1 = tmp_path / "f1.txt"
    f1.write_text("1", encoding="utf-8")
    f2 = tmp_path / "f2.txt"
    f2.write_text("2", encoding="utf-8")

    model = Storyboard.model_validate(_load_fixture("storyboard.json"))
    store.write("storyboard", model, inputs={"f1": f1})

    # Asking with different or extra inputs returns None
    assert store.get_if_valid("storyboard", inputs={"f1": f1, "f2": f2}) is None
    assert store.get_if_valid("storyboard", inputs={"f2": f2}) is None
    assert store.get_if_valid("storyboard", inputs={}) is None


def test_get_if_valid_never_raises_on_corrupt_or_missing(tmp_path: Path) -> None:
    """get_if_valid NEVER raises for missing or corrupt files."""
    run_dir = tmp_path / "runs" / "run-corrupt"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-corrupt-001")

    # 1. Missing artifact file
    assert store.get_if_valid("storyboard") is None

    # 2. Corrupt JSON
    art_path = store.path_for("storyboard")
    art_path.write_bytes(b"{ incomplete: json ...")
    assert store.get_if_valid("storyboard") is None

    # 3. Invalid schema fields (missing required fields)
    write_json(art_path, {"schema_version": 1, "garbage": 123})
    assert store.get_if_valid("storyboard") is None

    # 4. Unknown artifact name
    assert store.get_if_valid("not_an_artifact") is None


# ===========================================================================
# 6. Secret scan gate (§3.0 Rule 4 & Rule R1)
# ===========================================================================


def test_secret_scan_fires_apify_token(tmp_path: Path) -> None:
    """Secret scan raises ArtifactError if Apify token is present, and does not leak it."""
    run_dir = tmp_path / "runs" / "run-secret"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-secret-001")

    data = _load_fixture("winning_ads.json")
    secret_url = "https://api.apify.com/v2/acts/x/runs?token=apify_api_secret_999"
    data["ads"][0]["ad_library_url"] = secret_url
    model = WinningAds.model_validate(data)

    with pytest.raises(ArtifactError) as exc_info:
        store.write("winning_ads", model)

    err_msg = str(exc_info.value)
    # Offending artifact named
    assert "winning_ads" in err_msg
    # Matched prefix named
    assert "?token=" in err_msg or "apify_api_" in err_msg
    # Sensitive value MUST NOT be included
    assert "apify_api_secret_999" not in err_msg
    assert "secret_999" not in err_msg

    # Verify file was NOT written to disk
    assert not store.path_for("winning_ads").exists()


@pytest.mark.parametrize(
    ("token_value", "expected_prefix"),
    [
        ("https://api.example.com/feed?token=sensitive123", "?token="),
        ("https://api.example.com/feed?page=1&token=sensitive456", "&token="),
        ("apify_api_key_test_secret_12345", "apify_api_"),
        ("sk-or-v1-abcdef1234567890", "sk-or-v1-"),
        ("nvapi-1234567890abcdef", "nvapi-"),
        ("tvly-9876543210fedcba", "tvly-"),
    ],
)
def test_secret_scan_all_prefixes(tmp_path: Path, token_value: str, expected_prefix: str) -> None:
    """Secret scan intercepts all forbidden prefixes without value leakage."""
    run_dir = tmp_path / "runs" / f"run-sec-{expected_prefix.strip('?&=')}"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-sec")

    data = _load_fixture("winning_ads.json")
    data["ads"][0]["body_text"] = f"Check this: {token_value}"
    model = WinningAds.model_validate(data)

    with pytest.raises(ArtifactError) as exc_info:
        store.write("winning_ads", model)

    err_msg = str(exc_info.value)
    assert "winning_ads" in err_msg
    assert expected_prefix in err_msg
    assert token_value not in err_msg
    assert not store.path_for("winning_ads").exists()


# ===========================================================================
# 7. Schema version gate (§3.0 Rule 1 & Rule 2)
# ===========================================================================


def test_schema_version_future_rejected(tmp_path: Path) -> None:
    """Artifact with schema_version: 99 returns None on get_if_valid, raises on read()."""
    run_dir = tmp_path / "runs" / "run-future"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-future-001")

    data = _load_fixture("storyboard.json")
    data["schema_version"] = 99
    write_json(store.path_for("storyboard"), data)

    # get_if_valid returns None (triggers regeneration)
    assert store.get_if_valid("storyboard") is None

    # read() strictly raises ArtifactVersionError
    with pytest.raises(ArtifactVersionError, match="Unsupported schema_version 99"):
        store.read("storyboard")


def test_schema_version_outdated_rejected(tmp_path: Path) -> None:
    """Artifact with schema_version: 0 returns None on get_if_valid, raises on read()."""
    run_dir = tmp_path / "runs" / "run-outdated"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-outdated-001")

    data = _load_fixture("storyboard.json")
    data["schema_version"] = 0
    write_json(store.path_for("storyboard"), data)

    # get_if_valid returns None
    assert store.get_if_valid("storyboard") is None

    # read() raises ArtifactVersionError
    with pytest.raises(ArtifactVersionError, match="outdated schema_version"):
        store.read("storyboard")


# ===========================================================================
# 8. Strict read() tests
# ===========================================================================


def test_read_missing_raises_artifact_error(tmp_path: Path) -> None:
    """read() raises ArtifactError if the artifact file does not exist."""
    run_dir = tmp_path / "runs" / "run-read-missing"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-read-001")

    with pytest.raises(ArtifactError, match="does not exist"):
        store.read("storyboard")


def test_read_corrupt_json_raises_artifact_error(tmp_path: Path) -> None:
    """read() raises ArtifactError if file is not valid JSON."""
    run_dir = tmp_path / "runs" / "run-read-corrupt"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-read-002")

    store.path_for("storyboard").write_text("{ corrupt", encoding="utf-8")
    with pytest.raises(ArtifactError, match="could not be parsed as JSON"):
        store.read("storyboard")


def test_read_invalid_schema_raises_validation_error(tmp_path: Path) -> None:
    """read() raises ValidationError if content does not match schema."""
    run_dir = tmp_path / "runs" / "run-read-inv"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-read-003")

    write_json(store.path_for("storyboard"), {"schema_version": 1, "extra": "invalid"})
    with pytest.raises(ValidationError):
        store.read("storyboard")


def test_read_and_write_unknown_artifact_raises(tmp_path: Path) -> None:
    """Unknown artifact name raises ArtifactError on read, write, and path_for."""
    run_dir = tmp_path / "runs" / "run-unknown"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-unk-001")

    with pytest.raises(ArtifactError, match="Unknown artifact name"):
        store.path_for("not_valid")

    with pytest.raises(ArtifactError, match="Unknown artifact name"):
        store.read("not_valid")

    model = Storyboard.model_validate(_load_fixture("storyboard.json"))
    with pytest.raises(ArtifactError, match="Unknown artifact name"):
        store.write("not_valid", model)


def test_write_mismatched_model_class_raises(tmp_path: Path) -> None:
    """write() raises ArtifactError if model class does not match registry."""
    run_dir = tmp_path / "runs" / "run-mismatch"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-mis-001")

    storyboard_model = Storyboard.model_validate(_load_fixture("storyboard.json"))
    with pytest.raises(ArtifactError, match="expects model WinningAds"):
        store.write("winning_ads", storyboard_model)


def test_write_missing_input_file_raises(tmp_path: Path) -> None:
    """write() raises ArtifactError if an input file path does not exist."""
    run_dir = tmp_path / "runs" / "run-missing-inp"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-minp-001")

    model = Storyboard.model_validate(_load_fixture("storyboard.json"))
    nonexistent = tmp_path / "does_not_exist.json"

    with pytest.raises(ArtifactError, match="Input file for 'dep' not found"):
        store.write("storyboard", model, inputs={"dep": nonexistent})


# ===========================================================================
# 9. Concurrency: multiple writers to provenance.json
# ===========================================================================


def test_concurrent_provenance_writes(tmp_path: Path) -> None:
    """Concurrent writes to provenance.json interleave safely under threading lock."""
    run_dir = tmp_path / "runs" / "run-threads"
    paths = RunPaths(run_dir).ensure()
    store = ArtifactStore(paths, run_id="run-threads-001")

    models = {
        name: ARTIFACT_MODELS[name].model_validate(_load_fixture(f"{name}.json"))
        for name in ARTIFACT_NAMES
    }

    def write_worker(name: str) -> None:
        store.write(name, models[name])

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(write_worker, name) for name in ARTIFACT_NAMES]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    prov_list = read_json(paths.provenance)
    assert isinstance(prov_list, list)
    assert len(prov_list) == len(ARTIFACT_NAMES)
    written_names = {r["name"] for r in prov_list}
    assert written_names == set(ARTIFACT_NAMES)


# ===========================================================================
# 10. Spec verification snippet (Done when)
# ===========================================================================


def test_done_when_verification(tmp_path: Path) -> None:
    """Validate exact behavior specified in the 'Done when' contract."""
    d = tmp_path / "r"
    paths = RunPaths(d).ensure()
    s = ArtifactStore(paths, run_id="20260926-1402-a7f3")

    # 1. get_if_valid -> None (not written yet)
    assert s.get_if_valid("storyboard") is None

    # Write §3 fixture with an input
    input_file = tmp_path / "research_brief.json"
    write_json(input_file, _load_fixture("research_brief.json"))

    model = Storyboard.model_validate(_load_fixture("storyboard.json"))
    s.write("storyboard", model, inputs={"brief": input_file})

    # 2. get_if_valid -> model (inputs unchanged)
    assert s.get_if_valid("storyboard", inputs={"brief": input_file}) == model

    # 3. touch/rewrite an input -> None
    write_json(input_file, {"modified": True})
    assert s.get_if_valid("storyboard", inputs={"brief": input_file}) is None
