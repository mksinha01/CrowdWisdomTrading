"""Pipeline tool surface."""

from . import ads

# Optional / progressive imports as later stories (S20-S26) implement them
try:
    from . import patterns
except ImportError:
    patterns = None  # type: ignore[assignment]

try:
    from . import research
except ImportError:
    research = None  # type: ignore[assignment]

try:
    from . import storyboard
except ImportError:
    storyboard = None  # type: ignore[assignment]

try:
    from . import claims
except ImportError:
    claims = None  # type: ignore[assignment]

try:
    from . import video
except ImportError:
    video = None  # type: ignore[assignment]

try:
    from . import bundle
except ImportError:
    bundle = None  # type: ignore[assignment]

__all__ = [
    "ads",
    "patterns",
    "research",
    "storyboard",
    "claims",
    "video",
    "bundle",
]
