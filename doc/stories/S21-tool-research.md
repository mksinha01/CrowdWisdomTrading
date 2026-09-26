# S21 — Tool surface — research

**Phase** 5 · **Depends on** S20, S11, S09, S13 · **Blocks** S22
**Spec** `doc/video-ads-agent.md` lines **525–621** (`research_brief.json`), **1172–1309** (§6.3), **3433–3473** (the three research cards + `brief`)
**Context budget** ~17k (spec 4.5k + story 1.7k + output 9k)
**Produces** `tools/research.py`, `tests/test_tools_research.py`

---

## Goal

Run three independent research angles against the last 30 days, then **select** — not summarise — the
best claims into a brief the scriptwriter can use on camera.

The brief's three mandatory inputs map one-to-one onto three cards that run **concurrently**: the ICP's
pain, CrowdWisdomTrading's unique data, and crowd wisdom versus a single expert. That parallelism is
called *"the money shot"* in the recording recipe (spec line 3387).

## Interface contract — FROZEN

```python
# tools/research.py

ANGLES = ("pain", "unique_data", "crowd_effect")

def research_angle(*, settings: Settings, paths: RunPaths, angle: str,
                   queries: list[str] | None = None) -> dict:
    """One angle: Tavily + Exa, last-month window, sourced claims only.
    WRITES artifacts/angles/<angle>.json   (a scratch artifact, not in ARTIFACT_NAMES)
    RETURNS {"angle","claims":int,"sourced":int,"queries_used":[...],"window":{...},
             "prohibited_facts_found":int,"warnings":[...]}"""

def assemble_brief(*, settings: Settings, paths: RunPaths) -> dict:
    """SELECT the 8-12 best claims across the three angles; pick the strongest angle.
    WRITES artifacts/research_brief.json
    RETURNS {"artifact_path","selected_claims","strongest_angle","angle_rationale",
             "prohibited_facts":int}"""

# ── internals ──
async def _search_angle(client: HttpClient, angle: str, queries: list[str]) -> list[SearchHit]: ...
def _to_claims(hits: list[SearchHit], *, window_start: date) -> list[ResearchClaim]: ...
```

## Rules that bind this story

- **Rule H3** — Tavily window comes from `time_range`, not `days`. S11 already fixed the parameter;
  this story must not reintroduce a `days=` kwarg in a way that reads as a window control.
- **Rule H4** — Exa's `company` and `people` categories reject date filters. Do not combine them.
- **§6.3 line 1181** — *"Every claim MUST carry a source_url and a published_date. A claim you cannot
  source is not a claim; drop it."* Enforce this **in code**, not only in the prompt: a claim with an
  empty `source_url` is dropped before it reaches the artifact.
- **§3.3 line 618** — `prohibited_facts` is a **required, non-empty** field. *"the agent may append to
  it but must never remove an entry."* The verified starting set (spec lines 598–614) is a **floor**.
- **Rule A5** — CHEAP tier. The synthesis step is 3 calls; assembly is 1.
- **Rule C1** — an unverifiable claim the product makes must land in `prohibited_facts`, where the
  deterministic engine (S06) can hard-block it. This is the mechanism that makes the compliance gate
  work on real content rather than only on generic policy.

## Build steps

1. **`prohibited_facts` — start from the verified set.** Transcribe spec lines 598–614 verbatim into a
   module constant `BASELINE_PROHIBITED_FACTS`:
   - `74.1% of tracked directions hit` — appears as 73%, 73.8% and 74.1% on the product's own site;
     the track-record page and `/api/predictions` both 404 as of 2026-09-26
   - `16,564 professional traders tracked` — self-reported, no published methodology; may only be
     attributed to the company
   - `Institutional sentiment feature` — no methodology or data source disclosed
   `assemble_brief` merges the baseline with anything the `unique_data` angle discovered, **union
   only** — never overwrite, never remove.
2. `research_angle`:
   - default queries per angle (not in the spec — author them):
     - `pain`: `["retail trader signal overload", "trading signal fatigue 2026",
       "following too many analysts", "trader decision paralysis"]`
     - `unique_data`: `["crowdwisdomtrading predictions track record",
       "crowdwisdomtrading transparency", "published trading calls with outcomes"]`
     - `crowd_effect`: `["wisdom of crowds forecast accuracy", "superforecaster aggregation",
       "Tetlock superforecasters", "information cascades herding traders"]`
   - run **both** Tavily and Exa for each query, dedupe by URL, keep the higher `score`
   - `_to_claims` — drop any hit with no `url`, or whose `published_date` is before `window_start`
     (an undated source is kept but its `confidence` is multiplied by **0.6** and the claim text notes
     it is undated, per spec line 1182)
   - call `build_research_prompt(angle, window_start, window_end)` on CHEAP → a `ResearchAngle`
   - write to `artifacts/angles/<angle>.json`
3. **`unique_data` is the compliant angle and the one that matters.** Its prompt (spec lines 1214–1247)
   forbids position-access, copy-trading and managed-account claims, and it returns
   `prohibited_facts_found`. Parse those into `ProhibitedFact` entries and carry them forward.
   Also handle `crowd_effect`'s `counterargument` and `pain`'s plain shape — the three angles return
   **different** extra fields, so parse per-angle rather than with one schema.
4. `assemble_brief`:
   - read all three angle files
   - call `build_brief_prompt(angle_outputs)` on CHEAP → selection + `strongest_angle` +
     `angle_rationale` + `counterargument_to_address`
   - **`strongest_angle` must be one of the three literal values.** Validate it; a model returning
     `"unique data"` with a space is a schema failure, and the repair loop should fix it.
   - assemble `ProductFacts` from §3.3 lines 569–596 — name, tagline, legal entity
     (`Tsuroni LTD`), `landing_url`, markets, pricing tiers, `explicit_disclaimers`, brand palette.
     **Verify `landing_url` returns 200** with a single `httpx.head` through the cache; record the
     result in `landing_url_verified_200`. Appendix A notes every linked subpage 404s, so the ad must
     target the root URL only.
   - write `research_brief.json`
5. Docstrings — `research_angle`:
   > *CALL THIS: on one of the three research cards (`res_pain`, `res_unique`, `res_crowd`), once
   > each, concurrently. Pass `angle` explicitly — the three cards differ only by that argument.*
   >
   > *WHEN NOT TO CALL: do not call it twice for the same angle in one run; the content cache makes
   > the second call free but the board should have one card per angle. Do not call it with a
   > `window_days` other than 30 — the brief specifies last month.*
6. Tests (recorded search fixtures; mocked LLM):
   - a hit dated before `window_start` is dropped
   - an undated hit is kept with `confidence` scaled and the text annotated
   - a claim with no `source_url` never reaches the artifact
   - `prohibited_facts` contains the three baseline entries **plus** one discovered one
   - **the union never shrinks**: run `assemble_brief` twice with a discovered fact, assert the
     baseline is still present
   - `strongest_angle` is one of the three literals
   - the written artifact validates against `ResearchBrief`

## Decisions the spec leaves open

- **Angle scratch artifacts.** `ARTIFACT_NAMES` (spec line 379) has `research_brief` but no per-angle
  name. Write the per-angle outputs under `artifacts/angles/<angle>.json` **outside** the
  `ArtifactStore` registry — they are intermediate, not contracted. `assemble_brief` is what produces
  the contracted artifact. Document this.
- **Default queries** are authored here — the spec gives `search_queries_used` as a field but no seed
  queries. Four per angle, biased toward first-hand accounts for `pain` and toward the forecasting
  literature for `crowd_effect` (the prompt names Tetlock, Galton's ox, prediction markets, herding,
  and the Madness of Crowds critique).
- **`landing_url_verified_200`** — a HEAD through `HttpCache`. If the head fails, set `False` and warn;
  never fail the run. Appendix A implies it may genuinely be down.
- **The `pain` angle has no extra field**, `crowd_effect` adds `counterargument`, `unique_data` adds
  `prohibited_facts_found`. Parse with three small per-angle models, not one permissive model.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_tools_research.py -q -v     # green, offline fixtures

.venv/Scripts/python -c "
import json, pathlib
from cwt.domain.models import ResearchBrief
b = ResearchBrief.model_validate(json.loads(pathlib.Path('runs/_s21/artifacts/research_brief.json').read_text()))
print(b.product.name, b.product.legal_entity, b.product.landing_url_verified_200)
print(len(b.angles), 'angles;', b.angles['unique_data'].angle)
facts = [f['fact'] for f in b.prohibited_facts]
assert len(facts) >= 3, 'baseline prohibited_facts must be present'
for f in facts: print(' -', f)"
```

## Handoff

S22 reads `research_brief.json` for the `{brief}` placeholder, the three angles (one variant each), the
`strongest_angle`, and — critically — `prohibited_facts`, which it injects into the `{prohibited}`
placeholder of `STORYBOARD_PROMPT` **and** which validator 10 enforces. S24's claims tool reads the
same `prohibited_facts` via `scan_prohibited_facts` for the post-render pass.
