# S22 — Tool surface — storyboard A (hooks, variants, judge)

**Phase** 5 · **Depends on** S21, S13, S05 · **Blocks** S23, S24
**Spec** `doc/video-ads-agent.md` lines **623–785** (`storyboard.json`), **790–808** (`hook_candidates.json`), **1311–1513** (§6.4), **3474–3490** (the `script` card)
**Context budget** ~20k (spec 5.5k + story 1.8k + output 11k)
**Produces** `tools/storyboard.py` (part A), `tests/test_tools_storyboard.py`

---

## Goal

The creative core. Generate 12 scored hook candidates, write three competing storyboard variants — one
per research angle — judge them, splice the best beats from the losers into the winner.

> **G5 — the WOW requirements are undefined.** §12 cites `WOW-3` (the splice mechanism), `WOW-5` (the
> underused-archetype boost) and `WOW-6` (the cinematic grade), but **no WOW rules are ever stated**.
> The spec's own numbers for WOW-1, WOW-2 and WOW-4 do not exist anywhere in the document. This story
> reconstructs what is recoverable and states it explicitly so the "wow" is engineered, not hoped for.

### The reconstructed WOW requirements

| Id | Requirement | Where it is enforced |
|---|---|---|
| WOW-1 | The hook is a **scored search over 12 candidates**, not a sentence someone liked (spec line 32) | `_generate_hook_candidates` |
| WOW-2 | Three **genuinely distinct** variants, one per angle — writing one script and claiming three is not acceptable (spec line 3483) | `_write_storyboard_variant` called 3× |
| WOW-3 | **The splice**: the winner absorbs named beats from the losers, with a receipt trail in `generation.variants[].beats_stolen_from` (spec line 765) | `_judge_variants` + splice application |
| WOW-4 | Every shot carries **executable** camera and lighting direction — *"not decoration — they are compiled into the render"* (spec line 1405) | `Shot.camera` validator + S14 |
| WOW-5 | `social_proof` and `pain_point` get an **explicit scoring boost** because they are ~0.1% of fintech creatives but survive ~2.1× longer (spec line 4488) | `beats.hook_score_boost` (S05) |
| WOW-6 | The **cinematic grade** — *"ten lines of filtergraph is the entire difference between 'an AI slideshow' and 'a movie trailer'"* (spec line 3157) | `CINEMATIC_GRADE` (S14) |

Carry this table into the module docstring. It is the design record for a requirement set the spec
references but never writes down.

## Interface contract — FROZEN

```python
# tools/storyboard.py  (part A)

def generate_hook_candidates(*, settings: Settings, paths: RunPaths) -> dict:
    """12 scored candidates across 6 archetypes, with per-candidate rejection reasons.
    WRITES artifacts/hook_candidates.json
    RETURNS {"artifact_path","candidates":int,"selected_id","archetypes_covered":[...],
             "underused_boosted":[...]}"""

def write_storyboard_variant(*, settings: Settings, paths: RunPaths, angle: str,
                             hook_id: str, total_duration_s: float) -> dict:
    """One complete variant for one research angle. STRONG tier.
    WRITES artifacts/variants/<angle>.json  (scratch)
    RETURNS {"angle","shots":int,"duration_s","valid":true,"variant_path"}"""

def judge_variants(*, settings: Settings, paths: RunPaths) -> dict:
    """Score three variants, pick a winner, emit the splice list.
    WRITES artifacts/review_verdict.json? — NO. Writes nothing; returns the judgement.
    RETURNS {"variants":[...],"winner":str,"splices":[...],"winner_path":str}"""

# ── internals ──
async def _generate_hook_candidates(settings, paths) -> HookCandidates: ...
async def _write_storyboard_variant(settings, paths, *, angle, hook, brief, patterns) -> Storyboard: ...
async def _judge_variants(settings, paths, variants: dict[str, Storyboard]) -> JudgeResult: ...
def _apply_splices(winner: Storyboard, splices: list[dict], variants: dict[str, Storyboard]) -> Storyboard: ...
```

## Rules that bind this story

- **Rule A5** — **STRONG** tier for all three calls. This is the ~5 strong calls per run that justify
  the tier split. Everything else in the pipeline is CHEAP.
- **Rule A3** — `complete_validated` with the `Storyboard` schema. **Pass `max_tokens=8192`** — a
  12-shot storyboard exceeds the client's 4096 default and will truncate into a JSON parse failure.
- **Rule C3 / validator 9** — the risk disclosure must be present, in the final 8 seconds, legible for
  at least 3 seconds. The prompt states it; validator 9 enforces presence; the `safe_harbour_duration_s`
  field records the duration. **This story computes it from the beat timings; S25 recomputes it from
  the rendered timeline.**
- **Validator 6** — beats must hold the median tolerances. Pass the **same** `median_beat_timeline`
  into the prompt and into `Storyboard.validation_context`, or the storyboard is written to one grammar
  and validated against another.
- **Validator 10** — `prohibited_facts` must be absent from **every** variant, including the losers
  (spec line 2008: *"The judge does not check compliance; the compliance card does, and it will reject
  the winner."*). A loser variant can still be spliced into the winner, so a prohibited fact in a loser
  is a live hazard.
- **§7.4 line 1994** — the hook is a **search, not a guess**. *Do NOT invent a hook yourself.* Always
  go through `generate_hook_candidates` first.

## Build steps

1. `_generate_hook_candidates` — `build_hook_candidates_prompt(brief)` on STRONG → 12 candidates across
   six archetypes (two each). Then:
   - apply `beats.hook_score_boost(archetype)` (S05) to each `stop_power_score` and **clamp to 10.0**
   - assert two candidates in each of `social_proof` and `pain_point` — the prompt demands it, but a
     model will drift; **regenerate once** on violation, then accept with a warning
   - fill `archetypes_covered` and `underused_archetypes_boosted` from the actual candidates
   - select the highest-scoring candidate; set `selected=True` on it and a `rejection_reason` on
     every other (the runner-up's reason should name the winning archetype)
2. `_write_storyboard_variant` — `build_storyboard_prompt(...)` with all eleven placeholders wired:
   `duration_s`, `angle`, `angle_rationale`, `beat_timeline` (rendered from the median), `prohibited`
   (rendered from `prohibited_facts`), `shot_schema`, `storyboard_schema`, `brief`, `patterns`, `hook`.
   - `shot_schema` and `storyboard_schema` come from `Shot.model_json_schema()` and
     `Storyboard.model_json_schema()` — generate them, never hand-write
   - validate; on `ArtifactValidationError` **retry once** at `temperature=0.2` (a creative call can
     legitimately need it), then block the card
   - **`camera.move` must never be missing a move on any shot** — the `cwt-script-writer` SOUL says
     *"A shot with no `camera.move` is not a shot, it is a caption"* (spec line 1955)
3. `_judge_variants` — `build_variant_judge_prompt(variants)` on STRONG. Each variant is passed
   **reduced to its beats and voiceover** (spec line 2007) — the judge does not need camera data, and
   passing full 12-shot objects three times wastes the context budget.
   - the judge returns per-variant scores, a `strongest_shot_id`, the `winner`, and a splice list
   - **`splices[]` names a shot id from a loser and the axis it improves.** Keep it.
4. `_apply_splices` — integrate each splice into the winner:
   - copy the loser's shot, **re-id it** (`s13`, `s14`…) and **re-time it** into the target beat
   - re-run the beat/duration repair so validators 1, 2, 4 still pass — splicing shifts everything
   - record `generation.variants[].beats_stolen_from` — **this receipt trail is the point** (WOW-3)
   - if a splice cannot be integrated without breaking a validator, **drop the splice** and record why
     in `warnings`. Never ship a storyboard that fails to parse.
5. `judge_variants` (the tool) orchestrates `_judge_variants` + `_apply_splices` and returns the
   winner's path. It does **not** write `review_verdict.json` — that artifact belongs to the creative
   director card's self-review (S23's `score_storyboard`).
6. Docstrings — `write_storyboard_variant`:
   > *CALL THIS: exactly three times per run, once per angle (`pain`, `unique_data`, `crowd_effect`).
   > Pass the SAME `hook_id` to all three — the hook is chosen once, by score, before the variants are
   > written.*
   >
   > *WHEN NOT TO CALL: do not call it a fourth time to "try another angle" — there are only three
   > angles. Do not pass a hook id you invented; call `cwt_generate_hook_candidates` first.*
7. Tests (mocked LLM):
   - the boost raises a `social_proof` candidate's score and clamps at 10.0
   - a response with only one `social_proof` candidate triggers exactly one regeneration
   - a variant whose beats violate the median tolerance raises `ValidationError` — assert the message
     names the beat (this is validator 6 doing its job)
   - a prohibited fact in a **loser** variant is detected before splicing
   - `_apply_splices` produces a storyboard where validators 1, 2, 4 still pass
   - `beats_stolen_from` records the splice source angle
   - an unintegrable splice is dropped and warned, and the result still parses

## Decisions the spec leaves open

- **`max_tokens=8192`** for STRONG storyboard calls (above). The client default is 4096 and a 12-shot
  storyboard with camera/lighting/composition exceeds it.
- **Variant scratch artifacts.** `ARTIFACT_NAMES` has `storyboard` and `hook_candidates` but no
  per-variant name. Write variants to `artifacts/variants/<angle>.json` outside the registry, and the
  winner to `artifacts/storyboard.json` through the store. Mirrors S21's angle scratch files.
- **The judge is a separate STRONG call, not a self-review.** S23's `score_storyboard` is the
  *self-check*; the creative director card is the *gate*. Three distinct scorings exist; do not merge them.
- **`temperature`.** 0.0 for hook generation and judging (determinism matters for the splice list),
  0.2 for variant writing (creative variance is the point), 0.2 on retry.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_tools_storyboard.py -q -v      # green, mocked LLM

.venv/Scripts/python -c "
import json, pathlib
from cwt.domain.models import Storyboard
sb = Storyboard.model_validate(json.loads(pathlib.Path('runs/_s22/artifacts/storyboard.json').read_text()))
print(sb.meta.angle, sb.meta.total_duration_s, len(sb.shots), 'shots')
spliced = [v for v in sb.generation.variants if v.beats_stolen_from]
print('splices recorded:', [(v.angle, v.beats_stolen_from) for v in spliced])
assert sb.compliance.risk_disclosure_present
assert sb.compliance.safe_harbour_duration_s >= 3.0
print('best moment:', sb.generation.creative_scores.verdict)"

.venv/Scripts/python -c "
# WOW-4: no shot may lack executable camera direction
import json, pathlib
sb = json.loads(pathlib.Path('runs/_s22/artifacts/storyboard.json').read_text())
bad = [s['id'] for s in sb['shots'] if not s['camera'].get('move')]
assert not bad, f'caption-only shots: {bad}'"
```

## Handoff

S23 adds `score_storyboard`, `apply_rewrite` and the HTML renderer to this same file, and takes over
`artifacts/storyboard.json`. S24's compliance card reads it. **`_apply_splices` is called by S23's
`apply_rewrite` too** — a review verdict is structurally a splice list, so keep the function general
rather than tying it to the judge's output shape.
