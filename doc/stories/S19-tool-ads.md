# S19 — Tool surface — ads

**Phase** 5 · **Depends on** S07, S10, S13 · **Blocks** S20
**Spec** `doc/video-ads-agent.md` lines **407–472** (`winning_ads.json`), **4059–4082** (§10.2), **3404–3418** (the `ads` card)
**Context budget** ~15k (spec 3.5k + story 1.6k + output 8k)
**Produces** `tools/__init__.py`, `tools/ads.py`, `tests/test_tools_ads.py`

---

## Goal

Source and rank currently-winning ads. Two tools, one artifact (`winning_ads.json`), and the ranking
methodology that the whole downstream pipeline treats as its evidence base.

**Every tool in this and every following Phase 5 story follows the same shape** — read it once here:

```python
# tools/ads.py — the shape every tool module uses
def source_winning_ads(*, settings: Settings, paths: RunPaths,
                       keywords: list[str], countries: list[str] | None = None,
                       window_days: int = 30, max_items: int | None = None) -> dict:
    """Tool docstring written FOR AN LLM READER. See §10.2."""
```

- Tools are **plain functions**, not classes. Signature is keyword-only after `*`.
- Tools receive `settings` and `paths` — they **never** read env or construct paths.
- Tools return a **plain `dict`**, never a pydantic model. The plugin handler (S30) does the
  `json.dumps`.
- **Tools may raise.** The `@_safe` wrapper in S30 is what converts a raise into a JSON error string.
  Raising inside a tool is correct and expected.

## Interface contract — FROZEN

```python
# tools/__init__.py
from . import ads, patterns, research, storyboard, claims, video, bundle
__all__ = ["ads","patterns","research","storyboard","claims","video","bundle"]

# tools/ads.py
def source_winning_ads(*, settings: Settings, paths: RunPaths,
                       keywords: list[str], countries: list[str] | None = None,
                       window_days: int = 30, max_items: int | None = None) -> dict: ...
    # WRITES artifacts/winning_ads.json
    # RETURNS {"artifact_path": str, "ads_found": int, "after_window": int,
    #          "charge_usd": float, "actor_id": str, "warnings": list[str]}

def rank_winning_ads(*, settings: Settings, paths: RunPaths,
                     top_n: int | None = None) -> dict: ...
    # REWRITES artifacts/winning_ads.json in place (ranking block + per-ad performance_score)
    # RETURNS {"artifact_path": str, "ranked": int, "method": str, "degraded": bool}
```

## Ranking — the algorithm, since it is the pipeline's evidence base

§3.1 line 430–433 gives the method and weights verbatim:

```
method: "weighted_longevity_signal"
weights: { "active_days": 0.45, "is_active": 0.25, "platform_breadth": 0.15, "recency": 0.15 }
```

- `active_days` — normalised against the **90th percentile** of the batch, clamped to 1.0
- `is_active` — `1.0` if currently running
- `platform_breadth` — `len(publisher_platforms) / 4`, clamped
- `recency` — linear decay from `window_start` (0.0) to today (1.0)

**The degradation requirement (spec lines 469–472).** *"Impressions and spend are frequently absent or
bucketed… the ranker must degrade gracefully when they are missing and record that degradation in
`ranking.weights` plus `warnings`."* Since this implementation never reads impressions or spend at all,
`degraded` is `True` **whenever fewer than 5 ads carry a usable `video_duration_s`** — that is the real
degradation path here. Record it honestly.

## Rules that bind this story

- **Rule A2** — `maxTotalChargeUsd` on every run, plus `resultsLimit`. Never remove the cap.
- **Rule R1** — no token in `winning_ads.json`. S10's `_scrub` handles logs; `ArtifactStore.write`
  (S07) hard-gates the artifact. Do not disable either.
- **Rule A1 / §7.2** — a tool's docstring is its contract with the model. Write it as if an LLM is the
  only reader, because it is. State **when to call it** and **when not to**.
- **§3.1 line 467** — *"Never read a raw actor field outside `clients/apify.py`."* This module reads
  only normalised names.

## Build steps

1. `source_winning_ads`:
   - keywords default from §3.1 line 414: `["trading signals","stock market alerts","forex signals","options flow"]`
   - countries default `["US","GB","IN"]`
   - `build_input` → `run_actor` (with fallback actors per S10's decision) → `normalise_ad` each item
   - filter to the window: `started_running >= window_start` **or** `is_active`
   - dedupe on `collation_id` (spec line 433 counts `duplicate_collation` exclusions — record the count)
   - drop ads with empty `body_text` (count as `no_body_text`)
   - dedupe by `body_text` hash too — the same copy across 4 audience collations is one creative
   - construct `WinningAds`, write via `ArtifactStore`
   - **fewer than 8 ads in the window is not an error.** Complete anyway and add a warning — the
     `ads` card body says *"do NOT widen the window"* (spec line 3416). Widening it would silently
     make the "last 30 days" claim false.
2. `rank_winning_ads`:
   - compute `performance_score` per ad from the four weighted components, normalised to `[0,1)`
   - sort descending, write `performance_score` back into each `Ad`, set `ranking.method` and
     `ranking.weights`
   - fill `ranking.excluded_reasons` from step 1's counters
   - `top_n` slices the kept set; the full ranked list stays in the artifact
3. Docstrings — write them for the model. `source_winning_ads`:
   > *CALL THIS: first, on the `ads` card, once per run. Spends Apify credits — capped per run by
   > `maxTotalChargeUsd`. Never call it twice in one run; the content cache makes a duplicate free but
   > the board should have one ads card.*
   >
   > *WHEN NOT TO CALL: do not call this to "refresh" data mid-run. Do not widen `window_days` to get
   > more ads — a smaller honest sample beats a larger dishonest one.*
4. Tests with recorded Apify fixtures:
   - the window filter excludes an ad that ended before `window_start`
   - `collation_id` dedupe collapses two rows into one and increments the counter
   - ranking order matches hand-computed `performance_score`
   - `excluded_reasons` sums to `items_returned - len(ads)`
   - **no artifact contains `token`** — assert on the written file's bytes
   - the empty-result path writes a valid artifact with a warning and does **not** raise

## Decisions the spec leaves open

- **`APIFY_ADS_ACTOR_FALLBACKS` is never exercised by the spec.** S10 decided to try them on 404 or a
  zero-item run; this story records `source.actor_fallbacks_tried`.
- **Two-stage dedupe.** The spec counts `duplicate_collation` only. `body_text` dedupe is an addition —
  it is what stops four identical creatives from dominating the pattern aggregates in S20. Record it
  as a separate counter, `duplicate_copy`, so the spec's own number stays meaningful.
- **`performance_score` scale.** The §3.1 example shows `0.81`. Keep `[0,1)` and note that a score above
  ~0.75 means an ad that has been running most of the window on multiple platforms.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_tools_ads.py -q -v     # green, offline fixtures

.venv/Scripts/python -c "
import json, pathlib
from cwt.config import Settings
from cwt.util.paths import RunPaths
from cwt import tools
p = RunPaths(pathlib.Path('runs/_s19')).ensure()
r = tools.ads.source_winning_ads(settings=Settings.from_env(), paths=p, keywords=['forex signals'])
print(r)
a = json.loads((p.artifacts/'winning_ads.json').read_text())
assert 'token' not in json.dumps(a).lower()
print(len(a['ads']), a['ranking']['method'])"
```

## Handoff

S20 reads `winning_ads.json` from its parent card's `metadata.artifact_path`. The `ads` card (S27) is
`max_retries=2` — a retry after a transient Apify failure is expected and the content cache makes it
free. **`rank_winning_ads` runs before `cwt_extract_ad_patterns`** so the analyst sees a ranked list.
