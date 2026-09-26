# S30 — Plugin registration & handlers

**Phase** 6 · **Depends on** S29 · **Blocks** S31
**Spec** `doc/video-ads-agent.md` lines **1786–1910** (§7.2 `__init__.py` + `handlers.py`), **4588–4615** (Rule A1), **4061–4081** (§10.2)
**Context budget** ~17k (spec 4k + story 1.8k + output 10k) — 19 handlers, 1 shown
**Produces** `hermes/plugins/cwt/__init__.py`, `hermes/plugins/cwt/handlers.py`, `tests/test_handlers.py`

---

## Goal

Wire the 19 schemas to the 19 real implementations, through a wrapper whose entire purpose is that a
failure inside the pipeline cannot kill the agent turn.

> **Rule A1.** *"A tool handler that raises kills the agent turn, not just the call."* The `@_safe`
> wrapper is not defensive style — it is the difference between a card that retries and a worker that dies.

> **G4.** §7.2 shows **one** handler (`source_winning_ads`) and the comment *"… one `@_safe` function
> per registered tool, same shape. Nineteen in total."* The other 18 are derived from §10.2's table and
> the frozen signatures in S19–S26.

## Interface contract — FROZEN

```python
# hermes/plugins/cwt/__init__.py
logger = logging.getLogger("cwt.plugin")

def _after_call(tool_name, args, result, task_id, duration_ms, **kwargs) -> None: ...
def _on_task_completed(task_id, summary=None, metadata=None, **kwargs) -> None: ...
def register(ctx) -> None: ...

# hermes/plugins/cwt/handlers.py
def _ctx(args: dict) -> tuple[Settings, RunPaths]: ...
def _safe(fn): ...                       # decorator: never raise, always return a JSON string

# 19 handlers, each @_safe-wrapped, signature (args: dict, settings: Settings, paths: RunPaths) -> dict
source_winning_ads, rank_winning_ads, extract_ad_patterns, research_angle, assemble_brief,
generate_hook_candidates, write_storyboard_variant, judge_variants, score_storyboard, apply_rewrite,
check_claims, rewrite_for_compliance, synthesize_voiceover, render_video, probe_media,
render_storyboard_html, make_contact_sheet, verify_artifact, assemble_submission
```

## The 19 pairings — `schemas.X` → `handlers.y` → `tools.z`

| # | Tool | Handler | Delegates to |
|---|---|---|---|
| 1 | `cwt_source_winning_ads` | `source_winning_ads` | `tools.ads.source_winning_ads` |
| 2 | `cwt_rank_winning_ads` | `rank_winning_ads` | `tools.ads.rank_winning_ads` |
| 3 | `cwt_extract_ad_patterns` | `extract_ad_patterns` | `tools.patterns.extract_ad_patterns` |
| 4 | `cwt_research_angle` | `research_angle` | `tools.research.research_angle` |
| 5 | `cwt_assemble_brief` | `assemble_brief` | `tools.research.assemble_brief` |
| 6 | `cwt_generate_hook_candidates` | `generate_hook_candidates` | `tools.storyboard.generate_hook_candidates` |
| 7 | `cwt_write_storyboard_variant` | `write_storyboard_variant` | `tools.storyboard.write_storyboard_variant` |
| 8 | `cwt_judge_variants` | `judge_variants` | `tools.storyboard.judge_variants` |
| 9 | `cwt_score_storyboard` | `score_storyboard` | `tools.storyboard.score_storyboard` |
| 10 | `cwt_apply_rewrite` | `apply_rewrite` | `tools.storyboard.apply_rewrite` |
| 11 | `cwt_check_claims` | `check_claims` | `tools.claims.check_claims` |
| 12 | `cwt_rewrite_for_compliance` | `rewrite_for_compliance` | `tools.claims.rewrite_for_compliance` |
| 13 | `cwt_synthesize_voiceover` | `synthesize_voiceover` | `tools.video.synthesize_voiceover` |
| 14 | `cwt_render_video` | `render_video` | `tools.video.render_video` |
| 15 | `cwt_probe_media` | `probe_media` | `tools.video.probe_media` |
| 16 | `cwt_render_storyboard_html` | `render_storyboard_html` | `tools.storyboard.render_storyboard_html` |
| 17 | `cwt_make_contact_sheet` | `make_contact_sheet` | `tools.storyboard.make_contact_sheet` |
| 18 | `cwt_verify_artifact` | `verify_artifact` | `tools.bundle.verify_artifact` |
| 19 | `cwt_assemble_submission` | `assemble_submission` | `tools.bundle.assemble_submission` |

## Rules that bind this story

- **Rule A1 — handlers must NEVER raise.** Always return a JSON string, success or error. The
  `except Exception` in `_safe` is deliberate: keep the `# noqa: BLE001 — deliberate` comment.
- **Rule A1 corollary (spec 1816)** — if `register()` raises, the plugin is disabled **but Hermes
  continues running**. That is the behaviour we want. Do not wrap `register` in a try/except that
  swallows its own errors silently — log loudly and let it disable.
- **§7.2 line 1861 — ZERO business logic in handlers.** *"If you find yourself writing an `if` that is
  not argument unpacking, the logic belongs in `src/cwt/tools/`."* A handler unpacks `args`, resolves
  context, calls the tool, wraps the result. Nothing else.
- **§7.2 `_ctx`** — `CWT_RUN_DIR` is the run context. If it is unset, raise
  `"CWT_RUN_DIR is not set. This tool must run inside a CWT-dispatched kanban worker. Run \`cwt run\`
  rather than invoking the worker by hand."` — an actionable message, not a `KeyError`.
- **Rule R1** — `_safe` returns `str(exc)` in the error payload. An Apify exception message can carry
  a URL. Scrub it: run the message through `clients.apify._scrub` before serialising.
- **§7.2 hooks are observational.** `_after_call`'s **return value is ignored**. It logs. Do not make
  it enforce anything.

## Build steps

1. `__init__.py` — copy spec lines 1789–1852 verbatim. The `_TOOLS` tuple pairs `(name, schema,
   handler)` in the spec's order; keep it. `register()` iterates and calls
   `ctx.register_tool(name=..., toolset="cwt", schema=..., handler=...)`, then registers the two hooks,
   then registers the eight skills by name.
   - **Build `_TOOLS` from `schemas.ALL_SCHEMAS` (S29) rather than re-listing names**, so the schema
     list is the single source of truth. Keep the order stable.
2. `handlers.py` — copy `_ctx` and `_safe` from spec lines 1874–1895 verbatim, **adding the `_scrub`
   call** to the error path.
3. Write the 19 handlers. Each is 4–8 lines:
   ```python
   @_safe
   def write_storyboard_variant(args, settings, paths):
       return tools.storyboard.write_storyboard_variant(
           settings=settings, paths=paths,
           angle=args["angle"], hook_id=args["hook_id"],
           total_duration_s=args.get("total_duration_s", 42.0),
       )
   ```
   - **required args use `args["x"]`** — a `KeyError` becomes a clean JSON error naming the field
   - **optional args use `args.get("x", <the default from S19–S26>)`** — the default must match the
     tool's own default, or the schema and the implementation disagree
   - **pass only the arguments the schema declares.** Do not forward `**args` — that lets an invented
     key reach the tool.
4. Defaults must match S19–S26 exactly: `countries=["US"]`, `window_days=30`,
   `total_duration_s=42.0`, `cols=4`, `rows=3`, `stage` is **required** for `check_claims`.
5. Tests (`tests/test_handlers.py`, no live Hermes):
   - **every one of the 19 handlers returns a `str` that parses as JSON** — parametrise over the list
   - `_safe` on a tool that raises returns `{"ok": false, "error": "ValueError: ..."}`, **never raises**
   - `_ctx` raises with the `CWT_RUN_DIR` message when the env var is unset
   - a missing required arg returns `{"ok": false, "error": "... 'keywords' ..."}` rather than crashing
   - **the error payload is scrubbed**: raise an exception whose message contains
     `?token=apify_api_secret` and assert `<redacted>` in the output
   - `_TOOLS` covers all 19 names and each handler is callable
   - `register()` against a fake `ctx` registers 19 tools, 2 hooks and 8 skills

## Decisions the spec leaves open

- **`_scrub` on the error path** is an addition, required by Rule R1. The spec's `_safe` interpolates
  `f"{type(exc).__name__}: {exc}"` raw. Apify's own 404 message includes the actor path (safe), but an
  `httpx` error repr includes the request URL with the token (not safe). Scrub unconditionally.
- **`args.get("total_duration_s", 42.0)`** — 42s is the spec's own worked example duration (lines 635,
  869, 5240). It is a default, not a constraint; the script card passes the real value.
- **`register()` is called exactly once at startup.** Do not add a module-level side effect; the
  spec's comment (line 1814) says the disable-on-raise behaviour is intentional.
- **Handlers import `cwt.tools` lazily if needed.** The plugin is loaded by Hermes from
  `~/.hermes/plugins/cwt/`, where `cwt` may resolve to the installed package. S31 installs the plugin
  as a copy, not a symlink, so imports resolve against the venv's installed `cwt`.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_handlers.py -q -v      # green, no live Hermes

.venv/Scripts/python -c "
import json, os
from cwt.hermes.plugins.cwt import handlers as h
os.environ['CWT_RUN_DIR'] = 'runs/_s30'
fns = [getattr(h, n) for n in ('source_winning_ads','rank_winning_ads','extract_ad_patterns',
    'research_angle','assemble_brief','generate_hook_candidates','write_storyboard_variant',
    'judge_variants','score_storyboard','apply_rewrite','check_claims','rewrite_for_compliance',
    'synthesize_voiceover','render_video','probe_media','render_storyboard_html',
    'make_contact_sheet','verify_artifact','assemble_submission')]
print(len(fns), 'handlers')
for f in fns:
    out = f({})                      # every required arg missing -> JSON error, never a raise
    assert isinstance(out, str); json.loads(out)
print('all 19 return parseable JSON on empty args')"
```

## Handoff

S31's `cwt bootstrap` copies `plugin.yaml`, `__init__.py`, `schemas.py`, `handlers.py` into
`~/.hermes/plugins/cwt/` and registers the eight skills. **Nothing in `src/cwt/tools/` knows the plugin
exists** — the dependency runs one way only, which is what lets `cwt run --engine local` bypass Hermes
entirely (S32) and still call the same tools.
