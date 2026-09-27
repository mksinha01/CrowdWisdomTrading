"""Artifact store and provenance tracking.

Story S07 — Artifact store & provenance.
Spec doc/video-ads-agent.md §3.0 conventions, §9.6 resume layers, §3.5 schemas.

One class through which every artifact is read and written. It owns three things:
the schema_version gate, the input-hash provenance log, and the get_if_valid() check
that makes --resume skip work instead of re-spending API credits.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from cwt.domain.models import (
    SCHEMA_VERSION,
    AdPatterns,
    ArtifactBase,
    ArtifactVersionError,
    ClaimsReport,
    HookCandidates,
    RenderManifest,
    ResearchBrief,
    ReviewVerdict,
    Storyboard,
    WinningAds,
)
from cwt.util.jsonio import now_iso, read_json, write_json
from cwt.util.paths import RunPaths

ARTIFACT_NAMES: tuple[str, ...] = (
    "winning_ads",
    "ad_patterns",
    "research_brief",
    "storyboard",
    "hook_candidates",
    "review_verdict",
    "claims_report",
    "render_manifest",
)

# name -> model class. The single registry; nothing else maps names to models.
ARTIFACT_MODELS: dict[str, type[ArtifactBase]] = {
    "winning_ads": WinningAds,
    "ad_patterns": AdPatterns,
    "research_brief": ResearchBrief,
    "storyboard": Storyboard,
    "hook_candidates": HookCandidates,
    "review_verdict": ReviewVerdict,
    "claims_report": ClaimsReport,
    "render_manifest": RenderManifest,
}

# Secret scan patterns (§3.0 Rule 4 / Rule R1)
# Tokens never enter an artifact. Raise on detection; never include the secret value in the error.
_SECRET_SCAN_RE = re.compile(r"([?&]token=|apify_api_|sk-or-v1-|nvapi-|tvly-)")

# Concurrency: Stages run in parallel (three research cards). Two writers to
# provenance.json can interleave. Guard with a module-level threading.Lock and
# accept that cross-process writes are last-wins.
_PROVENANCE_LOCK = threading.Lock()


class ArtifactError(RuntimeError):
    """Raised when an artifact cannot be read, validated, or written."""


@dataclass(frozen=True)
class Provenance:
    name: str
    sha256: str
    inputs: dict[str, str]
    written_at: str
    run_id: str


def sha256_file(path: Path | str) -> str:
    """Read a file in 64 KiB chunks and return lowercase hex SHA-256."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def _scan_for_secrets(name: str, serialized_json: str) -> None:
    """Scan serialized JSON string for forbidden secret patterns and raise if found."""
    match = _SECRET_SCAN_RE.search(serialized_json)
    if match:
        prefix = match.group(1)
        raise ArtifactError(
            f"Secret token detected in artifact '{name}': matched prefix '{prefix}'"
        )


class ArtifactStore:
    def __init__(self, paths: RunPaths, *, run_id: str) -> None:
        self.paths = paths
        self.run_id = run_id

    def path_for(self, name: str) -> Path:
        """Return the filesystem path for artifacts/<name>.json."""
        if name not in ARTIFACT_MODELS:
            raise ArtifactError(f"Unknown artifact name: '{name}'")
        return self.paths.artifacts / f"{name}.json"

    def sha256_of(self, path: Path | str) -> str:
        """Compute sha256 of file in 64 KiB chunks."""
        return sha256_file(path)

    def input_hashes(self, inputs: Mapping[str, Path | str]) -> dict[str, str]:
        """Compute sha256 hashes for all input paths."""
        return {label: self.sha256_of(p) for label, p in inputs.items()}

    # ── write ──
    def write(
        self,
        name: str,
        model: ArtifactBase,
        *,
        inputs: Mapping[str, Path | str] | None = None,
    ) -> Path:
        """Validate, write utf-8/LF via jsonio, then append a Provenance record."""
        if name not in ARTIFACT_MODELS:
            raise ArtifactError(f"Unknown artifact name: '{name}'")

        expected_cls = ARTIFACT_MODELS[name]
        if not isinstance(model, expected_cls):
            raise ArtifactError(
                f"Artifact '{name}' expects model {expected_cls.__name__}, "
                f"got {type(model).__name__}"
            )

        # 3a. Cheap self-check that the object is coherent
        try:
            model.model_validate(model.model_dump())
        except ValidationError as e:
            raise ArtifactError(f"Validation failed for artifact '{name}': {e}") from e

        # 3b. Serialise and run the secret scan (raise naming offending artifact and matched prefix)
        payload = model.model_dump(mode="json")
        serialized_json = json.dumps(payload, ensure_ascii=False)
        _scan_for_secrets(name, serialized_json)

        # Compute input hashes before write to ensure input files exist
        in_hashes: dict[str, str] = {}
        if inputs is not None:
            for label, p in inputs.items():
                inp_path = Path(p)
                if not inp_path.is_file():
                    raise ArtifactError(f"Input file for '{label}' not found: {inp_path}")
                in_hashes[label] = self.sha256_of(inp_path)

        # 3c. Write via jsonio.write_json (enforces utf-8 and LF)
        out_path = self.path_for(name)
        write_json(out_path, payload)

        # 3d. Append Provenance record to artifacts/provenance.json
        art_sha = self.sha256_of(out_path)
        prov = Provenance(
            name=name,
            sha256=art_sha,
            inputs=in_hashes,
            written_at=now_iso(),
            run_id=self.run_id,
        )

        with _PROVENANCE_LOCK:
            records: list[dict[str, Any]] = []
            if self.paths.provenance.is_file():
                try:
                    loaded = read_json(self.paths.provenance)
                    if isinstance(loaded, list):
                        records = loaded
                except Exception:
                    records = []
            records.append(asdict(prov))
            write_json(self.paths.provenance, records)

        return out_path

    # ── read ──
    def read(self, name: str) -> ArtifactBase:
        """Read and validate artifact. Strict: raises on missing or invalid.

        Used by stages that have already passed get_if_valid.
        """
        if name not in ARTIFACT_MODELS:
            raise ArtifactError(f"Unknown artifact name: '{name}'")

        path = self.path_for(name)
        if not path.is_file():
            raise ArtifactError(f"Artifact '{name}' does not exist at {path}")

        try:
            data = read_json(path)
        except Exception as e:
            raise ArtifactError(
                f"Artifact '{name}' at {path} could not be parsed as JSON: {e}"
            ) from e

        if not isinstance(data, dict):
            raise ArtifactError(f"Artifact '{name}' at {path} must be a JSON object")

        # §3.0 Rule 1: A reader seeing a higher version raises ArtifactVersionError
        # §3.0 Rule 2: Outdated schemas also raise ArtifactVersionError on read
        version = data.get("schema_version")
        if not isinstance(version, int):
            raise ArtifactError(f"Artifact '{name}' missing mandatory integer schema_version")
        if version < SCHEMA_VERSION:
            raise ArtifactVersionError(
                f"Artifact '{name}' has outdated schema_version {version} "
                f"(current supported version is {SCHEMA_VERSION})"
            )
        if version > SCHEMA_VERSION:
            raise ArtifactVersionError(
                f"Unsupported schema_version {version} "
                f"(current supported version is {SCHEMA_VERSION})"
            )

        model_cls = ARTIFACT_MODELS[name]
        try:
            return model_cls.model_validate(data)
        except (ArtifactVersionError, ValidationError):
            raise
        except Exception as e:
            raise ArtifactError(f"Failed to validate artifact '{name}': {e}") from e

    # ── the resume mechanism ──
    def get_if_valid(
        self,
        name: str,
        *,
        inputs: Mapping[str, Path | str] | None = None,
    ) -> ArtifactBase | None:
        """Return the artifact iff it exists, parses, and every input hash matches
        provenance. Otherwise None. NEVER raises for a missing/corrupt artifact.
        """
        if name not in ARTIFACT_MODELS:
            return None

        path = self.path_for(name)
        if not path.is_file():
            return None

        try:
            data = read_json(path)
            if not isinstance(data, dict):
                return None
            version = data.get("schema_version")
            if not isinstance(version, int) or version != SCHEMA_VERSION:
                return None
            model_cls = ARTIFACT_MODELS[name]
            model = model_cls.model_validate(data)
        except Exception:
            return None

        if inputs is not None:
            with _PROVENANCE_LOCK:
                if not self.paths.provenance.is_file():
                    return None
                try:
                    records = read_json(self.paths.provenance)
                    if not isinstance(records, list):
                        return None
                except Exception:
                    return None

            # Find latest provenance record for name
            latest_rec: dict[str, Any] | None = None
            for r in reversed(records):
                if isinstance(r, dict) and r.get("name") == name:
                    latest_rec = r
                    break

            if latest_rec is None:
                return None

            stored_inputs = latest_rec.get("inputs")
            if not isinstance(stored_inputs, dict):
                return None

            # Verify every input hash matches
            current_hashes: dict[str, str] = {}
            for label, p in inputs.items():
                inp_p = Path(p)
                if not inp_p.is_file():
                    return None
                try:
                    current_hashes[label] = self.sha256_of(inp_p)
                except Exception:
                    return None

            if current_hashes != stored_inputs:
                return None

        return model
