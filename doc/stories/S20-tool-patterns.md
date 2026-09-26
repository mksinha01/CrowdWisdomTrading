# S20 — Tool surface — patterns

**Phase** 5 · **Depends on** S19, S05, S09, S13 · **Blocks** S21
**Spec** `doc/video-ads-agent.md` lines **474–523** (`ad_patterns.json`), **1101–1170** (§6.2 extract prompts), **3419–3432** (the `patterns` card)
**Context budget** ~16k (spec 4k + story 1.6k + output 9k)
**Produces** `tools/patterns.py`, `tests/test_tools_patterns.py`

---

## Goal

Turn N raw winning ads into the structural evidence the scriptwriter is held to: hook, pain, concept
and — the highest-value output — the **beat sheet**, which beat occupies which second.

§3.2 line 519 calls `underused_high_durability` *"the single most valuable field in this artifact"*,
and *"a real, sourced edge rather than a stylistic preference."* The `patterns` card body (spec line
3427) warns: *"a lazy aggregate here becomes a hard failure downstream"* — because validator 6
enforces the median timeline at schema level.

## Interface contract — FROZEN

```python
# tools/patterns.py

def extract_ad_patterns(*, settings: Settings, paths: RunPaths,
                        concurrency: int | None = None) -> dict:
    """One LLM extraction per ad (CHEAP tier, parallel), then aggregate.
    WRITES artifacts/ad_patterns.json
    RETURNS {"artifact_path","ads_analysed","archetypes","median_hook_s","underused_boosted","llm_calls"}"""

def aggregate_beat_timeline(*, settings: Settings, paths: RunPaths) -> dict:
    """Recompute only the aggregate block. Cheap, no LLM.
    REWRITES artifacts/ad_patterns.json in place."""

# ── internals ──
async def _extract_one(client: LLMClient, ad: Ad, *, sem: asyncio.Semaphore) -> AdPattern: ...
def _aggregate(patterns: list[AdPattern], ads: list[Ad], *, total_duration_s: float) -> PatternAggregate: ...
```

## Rules that bind this story

- **Rule A5** — extraction is **CHEAP** tier. ~24 ads = ~24 CHEAP calls. This is the volume call site;
  routing it to STRONG would cost more than the entire rest of the run.
- **Rule A4** — bound concurrency with the semaphore from `LLMClient`. A 24-call batch against
  NVIDIA's ~40 RPM **per model** limit needs the cap.
- **Rule A3** — every extraction goes through `complete_validated` with the `AdPattern` schema, so the
  three JSON layers and the repair loop apply. Never `json.loads` a raw completion here.
- **§3.2 line 519–523** — the archetype boost is sourced, not stylistic. Do not let it become a
  config value.
- **Rule A6** — the budget is enforced inside the client. This story must **not** pre-check cost and
  skip ads to stay under budget; `BudgetExceeded` propagates and the card blocks.

## Build steps

1. Read `winning_ads.json` via `ArtifactStore`. If it has fewer than 3 ads, block with a clear reason
   rather than producing a meaningless median.
2. **Beat sheets need a duration.** `Ad.video_duration_s` is frequently `None` (spec line 2401). Two
   paths:
   - duration present → `build_beat_sheet_prompt(ad_text, duration_s)`, real timings
   - duration absent → **skip the beat sheet**, still extract hook/pain/concept, and record the ad in
     `warnings` as `beat_sheet_skipped:no_duration`
   Aggregate only over sheets that exist. This is the honest path; fabricating a duration would poison
   the median that validator 6 enforces.
3. `_extract_one`:
   - call `build_extraction_prompt(ad.body_text, ad.active_days)` on CHEAP
   - call `build_beat_sheet_prompt(...)` only when the duration is known
   - validate against `AdPattern`; on `ArtifactValidationError` **skip the ad** and warn — do not abort
     the batch for one bad ad
   - a repair-loop salvage is fine; record `repair_attempts` in the run's ledger (already done by S09)
4. `_aggregate` — delegate to `domain/beats.py` (S05):
   - `aggregate_beat_timeline(sheets, total_duration_s=...)` → `median_beat_timeline`
   - `archetype_distribution(hooks)` → `archetype_distribution`
   - `underused_high_durability(dist, durability)` → `underused_high_durability`
   - `median_hook_duration_s` — median of the hook beat's `end_s - start_s` across sheets
   - `ad_count` — the ads that produced a pattern, not the ads fetched
5. `total_duration_s` for the aggregate is **not** in the source ads. Use the **median of the ads'
   own durations**, falling back to `settings.video_max_seconds` (60) when unknown. Record which was used.
6. `aggregate_beat_timeline` (the second tool) exists so the aggregate can be recomputed without
   re-spending 24 LLM calls — useful when a tolerance looks wrong during S35's verification. Delegate to
   `_aggregate`; no LLM.
7. Docstrings for the model — `extract_ad_patterns`:
   > *CALL THIS: on the `patterns` card, after `cwt_rank_winning_ads`. Makes ~24 cheap-tier LLM calls.*
   >
   > *WHEN NOT TO CALL: do not call it per-ad or to "top up" a partial result — it is a batch
   > operation over the whole ranked set. Call `cwt_aggregate_beat_timeline` instead if you only need
   > the aggregate recomputed.*
8. Tests (mock `LLMClient`, no network):
   - an ad with `video_duration_s=None` produces a pattern **without** a `beat_sheet` and a warning
   - the median timeline is contiguous after aggregation (validator 1 holds)
   - `sum(archetype_distribution.values()) == 1.0`
   - one malformed extraction is skipped, the batch completes, `warnings` names the ad id
   - `underused_high_durability` contains `social_proof` and `pain_point` when present in the fixture
   - **the written artifact validates against `AdPatterns`** (round-trip)

## Decisions the spec leaves open

- **`total_duration_s` for the aggregate.** Not specified. Using the ads' own median duration is the
  right call — the median timeline must describe *this niche's ads*, not our 60s ceiling. Falls back to
  `video_max_seconds` and warns.
- **Which total the storyboard is written to.** The cards imply ~40s (spec lines 1366, 1429, 1944).
  S22 picks the actual value in `[VIDEO_MIN_SECONDS, VIDEO_MAX_SECONDS]`; this story only supplies the
  grammar's *shape*, and the proportions scale to any total.
- **Concurrency default.** Use `settings.llm_max_concurrency`. Do not add a separate knob.
- **`prohibited_facts` is NOT this story's job.** It is populated by the `res_unique` research card
  (S21). Do not invent entries here.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_tools_patterns.py -q -v     # green, mocked LLM

.venv/Scripts/python -c "
import json, pathlib
from cwt.domain.models import AdPatterns
p = pathlib.Path('runs/_s20/artifacts/ad_patterns.json')
a = AdPatterns.model_validate(json.loads(p.read_text()))
tl = a.aggregate.median_beat_timeline
assert all(x.end_s == y.start_s for x,y in zip(tl, tl[1:])), 'median timeline not contiguous'
assert abs(sum(a.aggregate.archetype_distribution.values()) - 1.0) < 1e-6
print(a.aggregate.ad_count, a.aggregate.underused_high_durability)
print([f'{b.beat}:{b.start_s}-{b.end_s}±{b.tolerance_s}' for b in tl])"
```

## Handoff

S21 and S22 both read `ad_patterns.json` from their parent card. S22 passes
`median_beat_timeline` into `build_storyboard_prompt` as the `{beat_timeline}` placeholder **and** uses
it to populate the `Storyboard`'s per-beat `tolerance_s` so validator 6 can pass. **The two must come
from the same artifact** — a storyboard validated against a different timeline than it was written to
will fail for the wrong reason.
