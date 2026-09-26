# S13 — Prompt package

**Phase** 3 · **Depends on** S01 · **Blocks** S20, S21, S22, S24
**Spec** `doc/video-ads-agent.md` lines **1059–1657** (all of §6)
**Context budget** ~16k (spec 7k + story 1.5k + output 7k) — mostly transcription, little thinking
**Produces** `src/cwt/prompts/__init__.py`, `_format.py`, `extract.py`, `research.py`, `script.py`, `review.py`, `claims_policy.py`

---

## Goal

Every prompt string in the system, isolated from orchestration. No stage module contains a prompt
literal. Six files, all transcribed verbatim from §6, plus one small helper that fixes a real bug in
the spec.

**This story is mostly copy-paste. Do not paraphrase, summarise, or "improve" a prompt.** These
prompts encode the product's compliance posture and its creative strategy; a reworded compliance
constraint is a weakened gate.

## Interface contract — FROZEN

```python
# prompts/_format.py          ← NEW (fixes B1)
def safe_format(template: str, **kwargs) -> str:
    """Interpolate, tolerating unknown placeholders and stray braces.
    On KeyError/IndexError/ValueError return the UNINTERPOLATED template —
    a malformed custom override degrades, it does not kill the run."""

# prompts/extract.py
AD_EXTRACTION_PROMPT, BEAT_SHEET_PROMPT
build_extraction_prompt(ad_text, active_days, custom=None) -> str
build_beat_sheet_prompt(ad_text, duration_s, custom=None) -> str

# prompts/research.py
_COMMON_RESEARCH_RULES
PAIN_RESEARCH_PROMPT, UNIQUE_DATA_RESEARCH_PROMPT, CROWD_EFFECT_RESEARCH_PROMPT, BRIEF_ASSEMBLY_PROMPT
build_research_prompt(angle, window_start, window_end, custom=None) -> str
build_brief_prompt(angle_outputs, custom=None) -> str

# prompts/script.py
HOOK_CANDIDATES_PROMPT, STORYBOARD_PROMPT, VARIANT_JUDGE_PROMPT, REWRITE_PROMPT
build_hook_candidates_prompt(brief, custom=None) -> str
build_storyboard_prompt(*, duration_s, angle, angle_rationale, beat_timeline, prohibited,
                        shot_schema, storyboard_schema, brief, patterns, hook, custom=None) -> str
build_variant_judge_prompt(variants, custom=None) -> str
build_rewrite_prompt(*, verdict, scores, weakest_axes, changes_requested, must_fix,
                     must_not_change, prohibited_block, custom=None) -> str

# prompts/review.py
CREATIVE_REVIEW_PROMPT
build_creative_review_prompt(storyboard, patterns, threshold, custom=None) -> str

# prompts/claims_policy.py
CLAIMS_POLICY, CLAIMS_JUDGE_PROMPT
build_claims_judge_prompt(script, custom=None) -> str

# prompts/__init__.py — re-exports everything above (spec lines 1077–1090)
```

## Rules that bind this story

- **B1 — the spec has a real bug here.** `prompts/__init__.py` (lines 1093–1098) *defines*
  `safe_format` **and** imports from `.extract` (line 1077), while `extract.py` (line 1165) *calls*
  `safe_format` without importing it. As written it is a `NameError` at import time or a circular
  import. **Fix: move `safe_format` to `prompts/_format.py` and import it in every module that needs
  it.** Re-export from `__init__.py` for backwards compatibility.
- **§6 line 1063** — every template is a `build_x()` function with a `custom` override that **falls back
  gracefully**. A malformed custom override must degrade to the default, never crash a run.
- **The `{{ }}` doubling is load-bearing.** Every JSON example inside a template is doubled-braced
  because the template goes through `.format()`. Un-doubling one brace turns a JSON example into a
  `KeyError`. Copy them exactly.
- **The compliance blocks are not editable.** `HOOK_CANDIDATES_PROMPT`'s "HARD CONSTRAINTS",
  `STORYBOARD_PROMPT`'s "COMPLIANCE" list, and `CLAIMS_POLICY` mirror §12's C-rules. Weakening one in
  a prompt while leaving the regex engine intact produces a script that passes the gate by accident.

## Build steps

1. `_format.py` — `safe_format` from spec lines 1093–1098, plus a module docstring explaining B1.
2. `extract.py` — spec lines 1104–1169. Import `safe_format` from `._format`.
3. `research.py` — spec lines 1178–1308. `_COMMON_RESEARCH_RULES` is concatenated with `+` into each
   angle prompt; keep that structure so the shared rules stay in one place.
4. `script.py` — spec lines 1316–1513. `build_storyboard_prompt` computes `word_budget=int(duration_s * 3.5)`
   (line 1499) — ~210 wpm. Keep it.
5. `review.py` — spec lines 1519–1571. The weighted axes must sum to 1.0 (0.25+0.20+0.15+0.15+0.15+0.10).
6. `claims_policy.py` — spec lines 1580–1656. `CLAIMS_POLICY` is injected into `CLAIMS_JUDGE_PROMPT`
   by `build_claims_judge_prompt` (line 1656) — the policy is not duplicated into the judge prompt.
7. `__init__.py` — re-export per spec lines 1077–1090, then `from ._format import safe_format`.
8. Tests — `tests/test_prompts.py`:
   - every `build_*` returns a non-empty `str`
   - **`safe_format('{a} {missing}', a=1)` returns the literal `'{a} {missing}'`** — degraded, not raised
   - a `custom` override containing `{{` survives
   - `HOOK_CANDIDATES_PROMPT` mentions all six archetypes by name
   - `STORYBOARD_PROMPT` contains the exact risk-disclosure string
     `"Trading involves significant risk. Informational and educational only. Not financial advice."`
   - `CLAIMS_POLICY` mentions all three PRODUCT_DISCLAIMER prohibitions (positions, copy-trading,
     managed accounts)
   - `build_storyboard_prompt(duration_s=42, ...)` interpolates `word_budget == 147`

## Decisions the spec leaves open

- **`custom` overrides are never used by this project.** They exist for a future operator. Keep the
  parameter, and test the fallback path — that is the whole reason `safe_format` is tolerant.
- **Prompt versioning.** The spec versions `claims.py`'s ruleset (`RULESET_VERSION`) but not prompts.
  Add a module-level `PROMPT_VERSION = "1.0.0"` to each prompt file and record it in
  `claims_report.deterministic` alongside `ruleset_version`. Cheap, and it makes a prompt change
  auditable in an artifact.
- **No f-strings.** Templates are plain triple-quoted strings with `{}` placeholders. An f-string
  would evaluate at import time and defeat `safe_format` entirely.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_prompts.py -q -v     # green

.venv/Scripts/python -c "
from cwt.prompts import *
from cwt.prompts import safe_format
assert safe_format('{a} {missing}', a=1) == '{a} {missing}'
for n in ('AD_EXTRACTION_PROMPT','BEAT_SHEET_PROMPT','PAIN_RESEARCH_PROMPT',
          'UNIQUE_DATA_RESEARCH_PROMPT','CROWD_EFFECT_RESEARCH_PROMPT','BRIEF_ASSEMBLY_PROMPT',
          'HOOK_CANDIDATES_PROMPT','STORYBOARD_PROMPT','VARIANT_JUDGE_PROMPT','REWRITE_PROMPT',
          'CREATIVE_REVIEW_PROMPT','CLAIMS_POLICY','CLAIMS_JUDGE_PROMPT'):
    assert len(globals()[n]) > 200, n
print('13 templates ok; no circular import')"
```

## Handoff

S20/S21/S22/S24 call the `build_*` functions and never touch the raw template constants. **No stage
module may contain a prompt literal** — if you need a new prompt, add it here and add a builder.
