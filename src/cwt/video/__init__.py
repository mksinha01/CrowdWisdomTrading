"""Video rendering backends and filtergraphs."""
from __future__ import annotations

from cwt.video.backend import (
    Availability,
    RenderResult,
    VideoBackend,
    VideoBackendChain,
    build_chain,
)
from cwt.video.ffmpeg_bin import (
    MediaInfo,
    ffmpeg_path,
    ffmpeg_version,
    ffprobe_path,
    probe,
    probe_duration_from_stderr,
)
from cwt.video.filtergraph import (
    CINEMATIC_GRADE,
    build_colour_grade,
    build_shot_filter,
    build_zoompan_expr,
    clamp,
)
from cwt.video.local_ffmpeg import (
    LocalFfmpegBackend,
    build_shot_argv,
    render_shot,
)

__all__ = [
    "Availability",
    "CINEMATIC_GRADE",
    "LocalFfmpegBackend",
    "MediaInfo",
    "RenderResult",
    "VideoBackend",
    "VideoBackendChain",
    "build_chain",
    "build_colour_grade",
    "build_shot_argv",
    "build_shot_filter",
    "build_zoompan_expr",
    "clamp",
    "ffmpeg_path",
    "ffmpeg_version",
    "ffprobe_path",
    "probe",
    "probe_duration_from_stderr",
    "render_shot",
]
