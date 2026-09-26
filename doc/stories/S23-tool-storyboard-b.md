# S23 — Tool surface — storyboard B (score, rewrite, HTML, contact sheet)

**Phase** 5 · **Depends on** S22 · **Blocks** S24
**Spec** `doc/video-ads-agent.md` lines **810–824** (`review_verdict.json`), **1516–1572** (§6.5), **4397–4466** (§11.3), **4397** (`_make_contact_sheet`)
**Context budget** ~18k (spec 4.5k + story 1.8k + output 10k)
**Produces** `tools/storyboard.py` (part B), `tests/test_storyboard_render.py`

---

## Goal

Close the creative loop and produce the two human-readable outputs.

The creative director scores a storyboard against **the patterns mined from ads currently running in
this exact niche** — *"that is your reference standard — not your taste"* (spec line 1522). Below the
threshold, changes are requested and the writer revises, up to 3 rounds, then the card blocks.

> **The threshold is not a formality.** *"Do not pass work that is merely acceptable — the threshold
> exists because 'fine' is not the goal"* (spec line 1535).

## Interface contract — FROZEN

```python
# tools/storyboard.py  (part B)

def score_storyboard(*, settings: Settings, paths: RunPaths) -> dict:
    """Creative-director self-check BEFORE requesting review.
    WRITES artifacts/review_verdict.json
    RETURNS {"verdict","weighted_mean","threshold","weakest_axes","round","changes_requested"}"""

def apply_rewrite(*, settings: Settings, paths: RunPaths, verdict_path: str | None = None,
                  splice_list: list[dict] | None = None) -> dict:
    """Apply a review verdict's instructions OR a splice list. Preserves must_not_change.
    REWRITES artifacts/storyboard.json in place; increments generation.revision_rounds
    RETURNS {"artifact_path","revision_rounds","applied":[...],"preserved":[...],"valid":true}"""

def render_storyboard_html(*, settings: Settings, paths: RunPaths,
                           storyboard_path: str | None = None) -> dict:
    """Human-readable HTML. The brief requires the storyboard be 'saved and shared in
    json human readable format' — the JSON is the machine artifact, this is what a
    human reads.
    WRITES artifacts/storyboard.html
    RETURNS {"artifact_path","shots_rendered","bytes"}"""

def make_contact_sheet(*, settings: Settings, paths: RunPaths, cols: int = 4,
                       rows: int = 3) -> dict:
    """4x3 grid of each shot's first frame. Pillow. No ffmpeg required.
    WRITES artifacts/contact_sheet.png
    RETURNS {"artifact_path","cols","rows","cells"}"""

# ── internals ──
async def _score_storyboard(settings, paths, *, storyboard, patterns) -> ReviewVerdict: ...
async def _apply_rewrite(settings, paths, *, storyboard, verdict) -> Storyboard: ...
def _render_storyboard_html(sb: Storyboard) -> str: ...   # G9: named in the spec, never implemented
def _make_contact_sheet(sb: Storyboard, paths: RunPaths, *, cols: int, rows: int) -> Path: ...
def _beat_ribbon(sb: Storyboard) -> str: ...
def _score_cards(sb: Storyboard) -> str: ...
```

## Rules that bind this story

- **§6.5 line 1525** — the weighted axes and their weights are fixed:
  `hook_strength 0.25, mechanism_clarity 0.20, proof_credibility 0.15, emotional_arc 0.15,
  brand_fit 0.15, compliance_safety 0.10`. Sum to 1.0. Do not reweight.
- **`REWRITE_PROMPT` line 1459** — *"Fix ONLY what was asked. Preserve everything else EXACTLY."* The
  verdict carries `must_not_change`, and **changing a listed item must fail**. Enforce in code: diff
  the before/after for every path in `must_not_change` and raise if any changed.
- **Rule A5** — both `_score_storyboard` and `_apply_rewrite` are STRONG tier (creative judgement and
  creative rewriting), and both count toward the ~5 strong calls per run.
- **§11.3 line 4399** — no build step, no dependencies. **One templated self-contained file.** The
  `storyboard.html` must open from `file://` with no network.
- **§3.4 line 626** — the storyboard *"must be readable by a human who has never seen the system."*
  `storyboard.html` is how that is delivered.

## Build steps

1. `_score_storyboard` — `build_creative_review_prompt(storyboard_json, patterns_json, threshold)`
   on STRONG. The storyboard is passed **as JSON**, and the patterns as the reference standard.
   - compute `weighted_mean` **in Python** from the returned per-axis scores; do not trust the model's
     arithmetic (spec's own examples disagree with themselves: `8.83` vs a `8.14` example)
   - `verdict = "pass" if weighted_mean >= threshold else "request_changes"`
   - on `request_changes`, require non-empty `weakest_axes` (exactly 2) and `changes_requested`
   - carry `must_not_change` forward; default it to `["visual_hook","compliance"]` when the model
     omits it — a rewrite must never silently drop the compliance block
   - increment `round` from the previous verdict on disk
2. `score_storyboard` (tool) — refuses to score an invalid storyboard, writes
   `review_verdict.json`, and **does not block on a failing score**. Returning
   `verdict: "request_changes"` is the correct outcome; the writer then calls `apply_rewrite`. Only the
   *third* rejection blocks the card (spec line 3487, `CREATIVE_MAX_ROUNDS=3`).
3. `_apply_rewrite` — `build_rewrite_prompt(verdict, scores, weakest_axes, changes_requested,
   must_fix, must_not_change, prohibited_block)` on STRONG. The prompt demands *"the COMPLETE corrected
   storyboard JSON — the same schema, every field, not a diff."*
   - **enforce `must_not_change` after the call**: compare the returned storyboard against the input
     for each protected path; raise `YourRewriteChangedProtectedFields` (a `RuntimeError`) naming them
   - re-validate; a rewrite that breaks validator 6 is a failed rewrite, not a new storyboard
   - increment `generation.revision_rounds`
4. `apply_rewrite` (tool) — accepts **either** `verdict_path` (a review verdict) **or** `splice_list`
   (the judge's output from S22). Both are structurally the same operation, so both route to
   `_apply_splices` (S22) or `_apply_rewrite`. Record which was applied.
5. `_render_storyboard_html` + `STORYBOARD_HTML` — copy the template from spec lines 4405–4461
   **verbatim**, including the full `<style>` block (CSS is doubled-braced for `.format()` — copy the
   doubling). Then implement the four helpers the template needs:
   - `_beat_ribbon` — one flex cell per beat, width proportional to `(end_s - start_s)`, coloured from
     the brand palette. Colours must be **distinct per beat** and readable on near-black.
   - `_score_cards` — one card per creative axis with the score and the weighted mean against threshold.
   - `_shot_card(shot)` — the per-shot card: id, beat, timing, description, VO line (matched by
     `shot_id` from `voiceover.segments`), and a `.swatches` row of the shot's four palette colours.
   - `voiceover.full_text` into `.vo`.
   Fill `{title}`, `{angle_label}`, `{duration}`, `{aspect}`, `{resolution}`, `{run_id}`,
   `{hook_overlay}`, `{hook_description}`, `{hook_why}`, `{vo_text}`.
6. `make_contact_sheet` — Pillow, 4×3 grid. Each cell is the shot's **first frame**, produced by
   `AssetSourcer.generate(shot, ...)` (S17) — not a rendered video frame, so **no ffmpeg is needed**.
   Cell aspect 9:16, 2px brand-coloured separator, shot id and timing as a caption strip. Label the
   grid from the storyboard's actual shot count; a 12-shot storyboard fills exactly 4×3.
7. Docstrings — `score_storyboard`:
   > *CALL THIS: yourself, on the `script` card, BEFORE calling `kanban_request_review`. Do not send
   > work you know is weak — the director will return it and cost a round.*
   >
   > *WHEN NOT TO CALL: do not call it to "argue" with a director verdict, and do not call it on a
   > loser variant. It scores `artifacts/storyboard.json` only.*
8. Tests (mocked LLM, no network):
   - `weighted_mean` matches a hand-computed value; the model's own arithmetic is ignored
   - a `request_changes` verdict with `weakest_axes` of length ≠ 2 is rejected and retried
   - **`must_not_change` enforcement fires**: a rewrite that alters `visual_hook` raises
   - `revision_rounds` increments; the storyboard still validates afterward
   - `_render_storyboard_html` output contains all 12 shot ids, the VO text, and **no `http://`**
   - `make_contact_sheet` produces a `1080×(3*cells_h + gutters)` PNG with 12 cells (Pillow available,
     no ffmpeg in the test env)

## Decisions the spec leaves open

- **G9 — `_render_storyboard_html` and `_make_contact_sheet` are named in §11.3 but never implemented.**
  Only the HTML *template* is given (lines 4405–4461). Everything above is the derivation. Record it in
  the module docstring alongside the WOW table from S22.
- **`weighted_mean` computed in Python**, not read from the model. Cheaper than a repair round and
  removes a whole class of "the model's arithmetic disagreed with its own verdict" bugs.
- **Contact sheet cell source.** Using generated stills rather than extracted video frames means the
  contact sheet works offline and needs no ffmpeg — and it is an honest preview, because those stills
  *are* what each shot renders from.
- **`must_not_change` default.** The spec's example lists `["visual_hook","s01","s02","compliance"]`.
  Always inject `compliance` even if the model omits it — dropping a compliance constraint during a
  rewrite is the worst possible silent failure.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_storyboard_render.py -q -v      # green, offline

.venv/Scripts/python -c "
import pathlib, re
h = pathlib.Path('runs/_s23/artifacts/storyboard.html').read_text(encoding='utf-8')
assert 'http://' not in h and 'https://' not in h or 'crowdwisdomtrading' in h
for i in range(1, 13): assert f's{i:02d}' in h, i
assert 'Trading involves significant risk' in h
print('html', len(h), 'bytes, 12 shots, no external deps')

from PIL import Image
img = Image.open('runs/_s23/artifacts/contact_sheet.png'); print(img.size, img.mode)"

# must_not_change enforcement (negative proof)
.venv/Scripts/python -c "
# monkeypatch the LLM to return a storyboard with a changed visual_hook -> apply_rewrite must raise"
```

## Handoff

S24's compliance card reads `artifacts/storyboard.json` and may overwrite it in place after a
compliance rewrite — which means **`storyboard.html` must be regenerated after compliance**, not
before. The `collect` card (S26) renders both into `submission/`. `review_verdict.json` is the record
that the creative gate ran and is a submission deliverable.
