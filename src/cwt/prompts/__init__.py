"""Prompt templates + builders.

Every builder follows the same contract:
  - reads a template (default or caller-supplied override)
  - interpolates placeholders
  - on KeyError (a custom template with an unknown placeholder) returns the
    UNINTERPOLATED template rather than raising — a bad override degrades, it
    does not kill the run.
"""

from ._format import PROMPT_VERSION, safe_format
from .claims_policy import (
    CLAIMS_JUDGE_PROMPT,
    CLAIMS_POLICY,
    build_claims_judge_prompt,
)
from .extract import (
    AD_EXTRACTION_PROMPT,
    BEAT_SHEET_PROMPT,
    build_beat_sheet_prompt,
    build_extraction_prompt,
)
from .research import (
    _COMMON_RESEARCH_RULES,
    BRIEF_ASSEMBLY_PROMPT,
    CROWD_EFFECT_RESEARCH_PROMPT,
    PAIN_RESEARCH_PROMPT,
    UNIQUE_DATA_RESEARCH_PROMPT,
    build_brief_prompt,
    build_research_prompt,
)
from .review import (
    CREATIVE_REVIEW_PROMPT,
    build_creative_review_prompt,
)
from .script import (
    HOOK_CANDIDATES_PROMPT,
    REWRITE_PROMPT,
    STORYBOARD_PROMPT,
    VARIANT_JUDGE_PROMPT,
    build_hook_candidates_prompt,
    build_rewrite_prompt,
    build_storyboard_prompt,
    build_variant_judge_prompt,
)

__all__ = [
    "AD_EXTRACTION_PROMPT",
    "BEAT_SHEET_PROMPT",
    "BRIEF_ASSEMBLY_PROMPT",
    "CLAIMS_JUDGE_PROMPT",
    "CLAIMS_POLICY",
    "CREATIVE_REVIEW_PROMPT",
    "CROWD_EFFECT_RESEARCH_PROMPT",
    "HOOK_CANDIDATES_PROMPT",
    "PAIN_RESEARCH_PROMPT",
    "PROMPT_VERSION",
    "REWRITE_PROMPT",
    "STORYBOARD_PROMPT",
    "UNIQUE_DATA_RESEARCH_PROMPT",
    "VARIANT_JUDGE_PROMPT",
    "_COMMON_RESEARCH_RULES",
    "build_beat_sheet_prompt",
    "build_brief_prompt",
    "build_claims_judge_prompt",
    "build_creative_review_prompt",
    "build_extraction_prompt",
    "build_hook_candidates_prompt",
    "build_research_prompt",
    "build_rewrite_prompt",
    "build_storyboard_prompt",
    "build_variant_judge_prompt",
    "safe_format",
]
