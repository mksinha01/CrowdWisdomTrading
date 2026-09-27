"""Safe format helper for prompt templates.

Fixes bug B1 in spec: moving safe_format out of __init__.py avoids circular
imports with prompt modules that call safe_format.
"""

from __future__ import annotations

from typing import Any

PROMPT_VERSION = "1.0.0"


def safe_format(template: str, **kwargs: Any) -> str:
    """Interpolate, tolerating unknown placeholders and stray braces.

    On KeyError/IndexError/ValueError return the UNINTERPOLATED template —
    a malformed custom override degrades, it does not kill the run.
    """
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template
