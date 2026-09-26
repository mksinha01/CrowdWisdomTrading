# S04 — Storyboard validators

**Phase** 1 · **Depends on** S03 · **Blocks** S05, S06, S07, S14, S22
**Spec** `doc/video-ads-agent.md` lines **623–785** (§3.4 + the validator table)
**Context budget** ~17k (spec 5k + story 1.7k + output 9k)
**Produces** `domain/models.py` (validators appended), `tests/test_models.py`

---

## Goal

Eleven `@model_validator` methods on `Storyboard`, each enforcing a structural property that a
generative model will otherwise violate. These are what make this a *system* rather than a generator:
a storyboard that breaks the timing grammar, drifts off-brand, or smuggles in a prohibited statistic
**fails to parse**. It never reaches a reviewer as a plausible-looking but broken artifact.

> Spec line 784: *"Validators 6, 9 and 10 are the ones that make this a system rather than a
> generator. Do not soften them into warnings."*

## Interface contract — FROZEN

All eleven are `@model_validator(mode="after")` on `Storyboard`, raising `ValueError` with an
actionable message naming the offending shot/beat id. Append to `domain/models.py`; add no new module.

| # | Method | Fails when | Message must name |
|---|---|---|---|
| 1 | `beats_covers_timeline` | Beats have gaps/overlaps, or do not span `0 → total_duration_s` | the gap or overlap in seconds |
| 2 | `shots_match_beats` | A shot's `[start, start+duration)` falls outside its declared beat's range | the shot id and its beat |
| 3 | `duration_in_bounds` | `total_duration_s` outside `[VIDEO_MIN_SECONDS, VIDEO_MAX_SECONDS]` | both bounds and the actual |
| 4 | `shot_durations_sum` | Shot durations sum to ≠ `total_duration_s` within 0.05s | both totals and the delta |
| 5 | `hook_within_three_seconds` | The `hook` beat ends after `3.0 + tolerance_s` | the actual end time |
| 6 | `beats_within_median_tolerance` | Any beat start deviates from the mined median by more than `tolerance_s` | beat name, median, actual, tolerance |
| 7 | `palette_is_dark_and_accented` | A shot's palette has no near-black **and** no accent | the shot id and which is missing |
| 8 | `text_within_safe_area` | On-screen text sits outside the shot's declared safe area | the shot id and the text |
| 9 | `risk_disclosure_present` | `compliance.risk_disclosure_present` is `False` | Rule C1 |
| 10 | `no_prohibited_facts` | Any `prohibited_facts[].fact` or numeric variant appears anywhere in the script | the fact and where it matched |
| 11 | `voiceover_matches_shots` | A VO segment names a nonexistent `shot_id`, or summed segment duration exceeds video duration | the bad id / the overrun |

## Rules that bind this story

- **Rule C1 / C2** — validator 9 is a **hard gate**, not a warning. There is no override path.
- **§3.4 note** — validators 6, 9, 10 must not be degraded to warnings or `logger.warning` calls.
- **G11** — validators 2, 7, 8 read `Shot.camera`, `Shot.palette`, `Shot.composition`. If S03 named
  those differently, fix S03 rather than working around it.

## Build steps

1. `beats_covers_timeline` — sort by `start_s`; assert `beats[0].start_s == 0`, each
   `beats[i].end_s == beats[i+1].start_s` (tolerance 0.01s), and `beats[-1].end_s == total_duration_s`.
2. `shots_match_beats` — build a `{beat_name: BeatSpan}` map first; a shot whose beat is absent from
   `beats` is itself an error.
3. `duration_in_bounds` — read bounds from `cwt.config.Settings`. Cache with `functools.lru_cache`;
   do **not** re-read env per validation.
4. `hook_within_three_seconds` — `3.0 + hook.tolerance_s`. §3.4's example has hook ending at 3.4 with
   tolerance 1.2, so the ceiling is 4.2.
5. `beats_within_median_tolerance` — needs the median timeline. Take it from
   `generation`-adjacent data or pass it in; if unavailable, **skip this validator** and record why
   in `warnings`. Never silently pass a deviation you could have caught. (Coordinate with S05, which
   owns the median — see Decisions.)
6. `palette_is_dark_and_accented` — parse each hex. "Near-black" = max RGB channel < 0x30. "Accent" =
   a channel > 0x80. A palette of only greys fails.
7. `no_prohibited_facts` — **delegate to `domain/claims.py::scan_prohibited_facts` (S06)**. Do not
   reimplement the numeric-variant matching; that function exists and is tested. Wire the import
   lazily inside the validator to avoid a circular import at module load.
8. `voiceover_matches_shots` — build the shot-id set, then check every `VOSegment.shot_id`, then
   `sum(end_s - start_s) <= total_duration_s + 0.05`.
9. Tests: one passing fixture (the §3.4 example, completed to all 12 shots) and **one failing case per
   validator** — 11 negative tests minimum. A validator with no negative test is not proven.

## Decisions the spec leaves open

- **Validator 6's data source.** The median timeline lives in `ad_patterns.json`, which the
  `Storyboard` does not embed. Three options: (a) add an optional
  `Storyboard.validation_context: dict | None` field carrying the medians; (b) pass the medians into
  a `validate_against(patterns)` classmethod; (c) skip when absent. **Choose (a)** — it keeps the
  artifact self-describing and makes `--resume` re-validation deterministic. Record the choice in
  `generation` so a reader knows which medians were used.
- **`0.05s` tolerance** is stated only for validator 4. Use `0.01s` for beat contiguity (validator 1)
  and the stated `0.05s` for the duration sum. Document both.
- **Floating-point.** Compare with `math.isclose(rel_tol=0, abs_tol=...)`, never `==`.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_models.py -q          # green
.venv/Scripts/python -m pytest tests/test_models.py -q -k validator -v
# -> 11 selected, 11 passed. Fewer than 11 means a validator is unproven — go back.

# negative proof, one example:
.venv/Scripts/python -c "
from cwt.domain.models import Storyboard
import json,pathlib
sb=json.loads(pathlib.Path('tests/fixtures/storyboard.json').read_text())
sb['compliance']['risk_disclosure_present']=False
try: Storyboard.model_validate(sb); print('FAIL')
except Exception as e: print('validator 9 fired:', str(e)[:80])"
```

## Handoff

S05 supplies the median timeline (see Decisions). S06 supplies `scan_prohibited_facts`. S22 relies on
validators 1–11 to reject a bad variant **before** it is written to disk — so a `ValidationError` in
S22's path is a feature, not a bug. **Never add an `if settings.debug` escape hatch to a validator.**
