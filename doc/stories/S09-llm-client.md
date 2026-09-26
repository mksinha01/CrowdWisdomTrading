# S09 — LLM client

**Phase** 2 · **Depends on** S02, S03 · **Blocks** S10–S25 (every LLM call in the system)
**Spec** `doc/video-ads-agent.md` lines **2812–3136** (§8.7), **4633–4700** (Rules A3–A6)
**Context budget** ~20k (spec 6k + story 1.7k + output 11k)
**Produces** `clients/llm.py`, `tests/test_llm_repair.py`

---

## Goal

One OpenAI-compatible client, two provider profiles, three JSON-enforcement layers, tier routing, a
concurrency semaphore, and a budget enforced *inside* the client on the call that crosses the cap.

~40 LLM calls happen per run. ~35 are classification and scoring — cheap tier. ~5 are the actual
creative writing — strong tier. Getting that split right is the difference between a $0.30 run and a
$6 run, and this is the only module that knows the difference.

## Interface contract — FROZEN

```python
# clients/llm.py

RETRYABLE = {429, 500, 502, 503, 504, 522, 524}

class Tier(StrEnum):        CHEAP = "cheap"; STRONG = "strong"
class UnparseableJson(ValueError):   # .raw
class BudgetExceeded(RuntimeError):  # .spent .cap .stage
class ArtifactValidationError(RuntimeError):  # .stage .schema .error

@dataclass(frozen=True)
class ProviderProfile:
    name: str; base_url: str; api_key_env: str; supports_json_mode: bool
    extra_headers: dict[str, str] = field(default_factory=dict)

PROFILES: dict[str, ProviderProfile]      # "openrouter" (json_mode=True), "nvidia" (json_mode=False)

def extract_json(raw: str) -> Any: ...
def schema_instruction(schema: type[BaseModel]) -> str: ...

@dataclass
class LLMResult:
    text: str; model: str; tier: str; stage: str
    prompt_tokens: int; completion_tokens: int; latency_ms: int
    salvaged: bool = False; repair_attempts: int = 0

class LLMClient:
    def __init__(self, *, provider: str, api_key: str, model_cheap: str, model_strong: str,
                 fallbacks: list[str], max_concurrency: int, max_usd: float,
                 ledger_path: Path, timeout_s: float = 120.0, repair_attempts: int = 2): ...
    run_cost_usd: float
    async def complete(self, *, tier: Tier, messages: list[dict],
                       schema: type[BaseModel] | None = None, temperature: float = 0.0,
                       max_tokens: int = 4096, stage: str = "") -> LLMResult: ...
    async def complete_validated(self, *, tier: Tier, messages: list[dict], schema: type[T],
                                 stage: str, max_repairs: int | None = None) -> T: ...

PRICES: dict[str, tuple[float, float]]     # (usd/1M prompt, usd/1M completion)
def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float: ...
```

## Rules that bind this story

- **Rule A3 — three layers, all required.**
  1. `response_format={"type":"json_object"}` **only** when `profile.supports_json_mode`
  2. `schema_instruction(schema)` appended as a system message, **always**, even where layer 1 works
     (it measurably improves field-level accuracy)
  3. tolerant `extract_json()`: direct parse → strip fences → brace-depth scan **outside string
     literals** → `UnparseableJson` carrying the raw text
  Plus the repair loop. Dropping any layer is the single most common real failure in structured-output
  pipelines.
- **Rule A4** — one `asyncio.Semaphore(max_concurrency)` per client, sized from config (NVIDIA free
  tier ~40 RPM **per model**, so 4). Exponential backoff with **full jitter**, honour `Retry-After`,
  and demote to `fallbacks[0]` after **3 consecutive 429s** (`self._consecutive_429`).
- **Rule A5** — only the scriptwriter uses STRONG. `Tier` is a required keyword arg; there is **no
  default**. A caller must state its tier explicitly.
- **Rule A6** — budget is raised **inside `_record()`**, on the call that crosses the cap. Not checked
  by the caller afterwards. A prompt-level "stay under budget" is not a control.
- **Rule A3 note (spec 2884)** — NIM's `supports_json_mode=False` is a *capability declaration*, not a
  workaround. Do not special-case NVIDIA anywhere outside `ProviderProfile`.

## Build steps

1. `extract_json` — copy spec lines 2892–2943 verbatim. The brace-depth scanner **must** track
   `in_string` and `escape`: an ad headline containing `{` inside a JSON string will otherwise
   terminate the scan early. This is the highest-value 50 lines in the module.
2. `schema_instruction` — copy lines 2946–2954.
3. `PROFILES` — copy lines 2871–2889. OpenRouter carries the `HTTP-Referer`/`X-Title` headers.
4. `LLMClient.complete` — copy lines 2987–3038, preserving layer ordering exactly.
5. `complete_validated` — copy lines 3040–3066 plus `_repair_instruction` (3068–3079) and
   `_try_salvage` (3081–3097). `_try_salvage` fills **only optional** fields; it must never fill a
   required field or coerce a wrong type.
6. `_record` — copy lines 3099–3112. Add the missing imports (**B2**): `append_jsonl`, `now_iso` from
   `cwt.util.jsonio`. `ArtifactValidationError` is referenced at line 3066 and defined at 3115 —
   that is legal Python; leave the order alone.
7. `PRICES` + `estimate_cost` — copy lines 3123–3135. An unknown model returns `0.0` and logs a
   warning; never guess a price.
8. Tests (`respx`, no live calls):
   - `extract_json`: clean JSON / fenced ```json / fenced bare / prose-wrapped / `{` inside a string /
     unbalanced braces / garbage → `UnparseableJson`
   - repair loop: first response invalid, second valid → returns, `repair_attempts == 1`
   - salvage: response missing only an optional field → `salvaged is True`
   - **salvage must NOT fire** for a missing required field → `ArtifactValidationError`
   - budget: `max_usd=0.0001` → `BudgetExceeded` raised from `_record`, `.stage` populated
   - semaphore: 12 concurrent `complete()` calls with `max_concurrency=4` never exceed 4 in flight
   - 429×3 demotes to `fallbacks[0]` and the next call uses it
   - ledger: `llm_ledger.jsonl` gets one line per call with `run_total_usd` monotonically increasing

## Decisions the spec leaves open

- **`Retry-After` honouring** is stated (line 4664) but not implemented in the spec's `complete()`.
  `retry_async` (S02) owns it: raise an exception carrying `response`, and `retry_async` reads the
  header. Pass `httpx.HTTPStatusError` through unchanged so the header survives.
- **`max_tokens=4096`** is the spec's default. The storyboard prompt can exceed it — S22 must pass
  `max_tokens=8192` explicitly for STRONG storyboard calls. Note it in S22.
- **Ledger write failures** must not kill a run. Wrap `append_jsonl` in `try/except OSError` and log.
  A cost ledger is observability, not a control — the budget check is the control.

## Done when

```bash
.venv/Scripts/python -m pytest tests/test_llm_repair.py -q -v     # green, zero network

.venv/Scripts/python -c "
from cwt.clients.llm import extract_json, UnparseableJson
print(extract_json('Sure! Here you go:\n\`\`\`json\n{\"a\": {\"b\": 1}}\n\`\`\`\nHope that helps.'))
print(extract_json('{\"t\": \"{not json}\"}'))    # must survive the brace inside the string
try: extract_json('no json here')
except UnparseableJson as e: print('ok, raw len', len(e.raw))"
```

## Handoff

Every stage calls `complete_validated(tier=..., messages=..., schema=..., stage=...)` and gets a
validated pydantic object or a raise. **No stage constructs its own httpx client for an LLM call.**
S32 catches `BudgetExceeded` and exits 4. S33's doctor validates the slugs against `/v1/models`
before any spend.
