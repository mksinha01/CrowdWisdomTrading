"""Video backend protocol, results, and chain construction.

Rule V4: The backend chain MUST terminate in local_ffmpeg (or fixture).
build_chain() raises at startup if the last element is not a backend
available in every environment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from cwt.clients.tts import VoiceoverResult
from cwt.config import Settings
from cwt.domain.models import Storyboard
from cwt.util.paths import RunPaths


@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str = ""


class VideoBackend(Protocol):
    name: str

    def available(self) -> Availability:
        ...

    def render(
        self,
        storyboard: Storyboard,
        *,
        paths: RunPaths,
        voiceover: VoiceoverResult,
    ) -> RenderResult:
        ...


@dataclass
class RenderResult:
    ok: bool
    output: Path | None
    backend: str
    elapsed_s: float
    error: str | None = None
    argv: list[str] = field(default_factory=list)
    assets: list[dict[str, Any]] = field(default_factory=list)


class VideoBackendChain:
    def __init__(self, backends: list[VideoBackend]) -> None:
        self.backends = list(backends)

    def __getitem__(self, i: int) -> VideoBackend:
        return self.backends[i]

    def __len__(self) -> int:
        return len(self.backends)

    def __iter__(self):
        return iter(self.backends)


class _StubBackend:
    """Fallback stub for backends pending implementation in later stories (S15/S18)."""

    def __init__(self, name: str) -> None:
        self.name = name

    def available(self) -> Availability:
        return Availability(available=False, reason=f"{self.name} backend not implemented yet")

    def render(
        self,
        storyboard: Storyboard,
        *,
        paths: RunPaths,
        voiceover: VoiceoverResult,
    ) -> RenderResult:
        return RenderResult(
            ok=False,
            output=None,
            backend=self.name,
            elapsed_s=0.0,
            error=f"{self.name} backend not implemented yet",
            argv=[],
            assets=[],
        )


def _load_backend(name: str, settings: Settings) -> VideoBackend:
    if name == "local_ffmpeg":
        try:
            from cwt.video.local_ffmpeg import LocalFfmpegBackend
            return LocalFfmpegBackend()
        except (ImportError, AttributeError):
            return _StubBackend("local_ffmpeg")
    elif name == "hyperframes":
        try:
            from cwt.video.hyperframes import HyperFramesBackend
            return HyperFramesBackend()
        except (ImportError, AttributeError):
            return _StubBackend("hyperframes")
    elif name == "openmontage":
        try:
            from cwt.video.openmontage import OpenMontageBackend
            return OpenMontageBackend()
        except (ImportError, AttributeError):
            return _StubBackend("openmontage")
    elif name == "fixture":
        try:
            from cwt.video.fixture import FixtureBackend
            return FixtureBackend()
        except (ImportError, AttributeError):
            return _StubBackend("fixture")
    else:
        raise ValueError(f"Unknown video backend: {name!r}")


def build_chain(settings: Settings) -> VideoBackendChain:
    """RAISES if settings.video_backend_chain[-1] is not a guaranteed-available backend (Rule V4)."""
    if not settings.video_backend_chain:
        raise ValueError("Rule V4 violation: video_backend_chain cannot be empty.")

    if settings.video_backend_chain[-1] not in ("local_ffmpeg", "fixture"):
        raise ValueError(
            f"Rule V4 violation: video_backend_chain must terminate in 'local_ffmpeg' or 'fixture'. "
            f"Got: {settings.video_backend_chain}"
        )

    backends = [_load_backend(name, settings) for name in settings.video_backend_chain]
    chain = VideoBackendChain(backends)

    if chain[-1].name not in ("local_ffmpeg", "fixture"):
        raise ValueError(
            f"Rule V4 violation: chain tail '{chain[-1].name}' is not 'local_ffmpeg' or 'fixture'."
        )

    return chain
