# S29 — Plugin manifest & tool schemas  ⚠ LARGE

**Phase** 6 · **Depends on** S19–S26 · **Blocks** S30
**Spec** `doc/video-ads-agent.md` lines **1722–1784** (the manifest), **4059–4082** (the 19-tool table), **4083–4111** (the docstring rule)
**Context budget** ~21k (spec 3.5k + story 1.8k + output 12k) — 19 JSON schemas is bulk transcription
**Produces** `hermes/plugins/cwt/plugin.yaml`, `hermes/plugins/cwt/schemas.py`

---

## Goal

The **manifest** that makes Hermes load our plugin, and the **19 JSON schemas** the model actually sees.
Together they are the plugin's public interface.

> **G3 — `schemas.py` is referenced by name 19 times in the spec and its contents are never shown.**
> Only the pattern `schemas.SOURCE_WINNING_ADS` appears (spec line 1819). Every schema below is derived
> from §10.2's tool table and the corresponding tool signature in S19–S26. Record the derivation.

## Interface contract — FROZEN

```python
# hermes/plugins/cwt/schemas.py
# One module-level constant per tool, named EXACTLY as the spec's register() table expects.
SOURCE_WINNING_ADS, RANK_WINNING_ADS, EXTRACT_AD_PATTERNS, RESEARCH_ANGLE, ASSEMBLE_BRIEF,
GENERATE_HOOKS, WRITE_STORYBOARD, JUDGE_VARIANTS, SCORE_STORYBOARD, APPLY_REWRITE,
CHECK_CLAIMS, REWRITE_COMPLIANCE, SYNTHESIZE_VO, RENDER_VIDEO, PROBE_MEDIA,
RENDER_SB_HTML, MAKE_CONTACT_SHEET, VERIFY_ARTIFACT, ASSEMBLE_SUBMISSION

# Each is an OpenAI-style function schema:
{
  "type": "function",
  "function": {
    "name": "cwt_source_winning_ads",
    "description": "<the tool docstring from tools/ads.py, verbatim>",
    "parameters": {"type": "object", "properties": {...}, "required": [...],
                   "additionalProperties": False}
  }
}
```

## The 19 schemas

| Constant | Tool name | Required args | Optional args |
|---|---|---|---|
| `SOURCE_WINNING_ADS` | `cwt_source_winning_ads` | `keywords: array[str]` | `countries: array[str]`, `window_days: int`, `max_items: int` |
| `RANK_WINNING_ADS` | `cwt_rank_winning_ads` | — | `top_n: int` |
| `EXTRACT_AD_PATTERNS` | `cwt_extract_ad_patterns` | — | `concurrency: int` |
| `RESEARCH_ANGLE` | `cwt_research_angle` | `angle: enum[pain,unique_data,crowd_effect]` | `queries: array[str]` |
| `ASSEMBLE_BRIEF` | `cwt_assemble_brief` | — | — |
| `GENERATE_HOOKS` | `cwt_generate_hook_candidates` | — | — |
| `WRITE_STORYBOARD` | `cwt_write_storyboard_variant` | `angle: enum[...]`, `hook_id: str` | `total_duration_s: number` |
| `JUDGE_VARIANTS` | `cwt_judge_variants` | — | — |
| `SCORE_STORYBOARD` | `cwt_score_storyboard` | — | — |
| `APPLY_REWRITE` | `cwt_apply_rewrite` | — | `verdict_path: str`, `splice_list: array[object]` |
| `CHECK_CLAIMS` | `cwt_check_claims` | `stage: enum[pre_render,post_render]` | `transcript: str` |
| `REWRITE_COMPLIANCE` | `cwt_rewrite_for_compliance` | — | `round_no: int` |
| `SYNTHESIZE_VO` | `cwt_synthesize_voiceover` | — | — |
| `RENDER_VIDEO` | `cwt_render_video` | — | `backend: str` |
| `PROBE_MEDIA` | `cwt_probe_media` | — | `path: str` |
| `RENDER_SB_HTML` | `cwt_render_storyboard_html` | — | `storyboard_path: str` |
| `MAKE_CONTACT_SHEET` | `cwt_make_contact_sheet` | — | `cols: int`, `rows: int` |
| `VERIFY_ARTIFACT` | `cwt_verify_artifact` | `name: enum[the 8 ARTIFACT_NAMES]` | — |
| `ASSEMBLE_SUBMISSION` | `cwt_assemble_submission` | — | — |

## Rules that bind this story

- **§10.2 line 4083 — every tool description is written for an LLM reader.** *"The docstring is the
  tool's contract with the model, not documentation for a human."* Import the descriptions from
  `tools/*.py` rather than retyping them: `SOURCE_WINNING_ADS["function"]["description"] = tools.ads.source_winning_ads.__doc__`.
  Two copies of a tool description will drift, and the model reads the schema's copy.
- **Every schema must set `additionalProperties: False`.** A model that invents an argument should get a
  clear rejection, not a silent `**kwargs` swallow.
- **`requires_env` missing ⇒ the plugin is disabled with a clear message, not a failure at call time**
  (spec line 1766). List all four keys.
- **Rule A1** — a plugin lives at `~/.hermes/plugins/<name>/`, flat or **one category level deep**.
  Anything deeper is **silently ignored**. `cwt` is flat; that matters for S31's installer.

## Build steps

1. `plugin.yaml` — copy spec lines 1733–1784 verbatim: `name`, `version`, `description`, `author`,
   `license`, `homepage`, the 19 `provides_tools`, the two `provides_hooks`
   (`post_tool_call`, `kanban_task_completed`), and the four `requires_env` entries with their
   `description`, `url` and `secret: true`.
2. `schemas.py` — a small `_fn(name, description, properties, required)` helper so the 19 literals stay
   readable, then the 19 constants. Each `description` is pulled from the tool function's `__doc__`.
3. `enum`s: `angle` (3 values), `stage` (2 values), `check_claims.stage` (2), `verify_artifact.name`
   (the 8 `ARTIFACT_NAMES`). Generate the `verify_artifact` enum from
   `cwt.domain.artifacts.ARTIFACT_NAMES` so it cannot drift.
4. Descriptions must include the **WHEN NOT TO CALL** section. §10.2's worked example (lines 4087–4111)
   is the template: `CALL THIS`, `WHEN NOT TO CALL`, `INPUTS`, `WRITES`, `RETURNS`. A tool schema
   without a "when not to call" invites the model to call it at the wrong time, which is the most
   expensive failure mode in an agent pipeline.
5. Tests:
   - all 19 constants exist and are importable **by the exact names in the spec's `register()` table**
   - every schema's `function.name` starts with `cwt_` and matches its constant's intent
   - every `description` is non-empty and contains `"WHEN NOT TO CALL"`
   - every `parameters` block sets `additionalProperties: False`
   - every enum's values match the corresponding Python enum / constant in `domain/`
   - `plugin.yaml`'s `provides_tools` is exactly the 19 tool names — assert the set equality against
     `[s["function"]["name"] for s in schemas.ALL_SCHEMAS]`

## Decisions the spec leaves open

- **`ALL_SCHEMAS`** is an addition: a tuple of the 19 in registration order, so S30's `register()` can
  iterate rather than listing names a second time, and so the test can assert set equality with
  `plugin.yaml`. Keep the names identical to the spec's table.
- **`verify_artifact`'s `name` enum is generated**, not hand-listed, from `ARTIFACT_NAMES` (S07).
- **No `settings` or `paths` arguments appear in any schema.** §7.2's `_ctx()` resolves them from
  `CWT_RUN_DIR`. Exposing them would let the model fabricate a path — and the body already tells it
  *"Do not guess paths"* (spec line 3623).
- **Descriptions are imported, not copied.** If the import is awkward at module load, resolve lazily
  inside a `build_schemas()` function called by `register()` — but keep one source of truth.
- **`plugin.yaml` is YAML and the tools list is load-bearing.** A typo'd tool name there means the tool
  is registered in code but not advertised, and the model will never call it.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_plugin_schemas.py -q -v      # green

.venv/Scripts/python -c "
import yaml, pathlib
from cwt.hermes.plugins.cwt import schemas
y = yaml.safe_load(pathlib.Path('hermes/plugins/cwt/plugin.yaml').read_text())
declared = set(y['provides_tools'])
built = {s['function']['name'] for s in schemas.ALL_SCHEMAS}
print(len(declared), len(built))
assert declared == built, declared ^ built
assert len(built) == 19
assert all('WHEN NOT TO CALL' in s['function']['description'] for s in schemas.ALL_SCHEMAS)
print('manifest and schemas agree on 19 tools')"
```

## Handoff

S30's `register(ctx)` iterates `schemas.ALL_SCHEMAS` and wires each name to a handler in
`handlers.py` — the pairing table in §7.2 (spec lines 1818–1838) is the contract. S31 installs
`plugin.yaml` + `__init__.py` + `schemas.py` + `handlers.py` into `~/.hermes/plugins/cwt/`. **A tool
present in `plugin.yaml` but absent from `schemas.py` is invisible to the model and will never be called.**
