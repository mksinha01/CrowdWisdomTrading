"""OpenAI-compatible LLM client.

OpenRouter and NVIDIA NIM are both OpenAI-compatible at /v1/chat/completions, so
this is ONE implementation with two configuration records — not two clients.
There is no `if provider == "nvidia"` anywhere except inside ProviderProfile.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import time
from typing import Any, TypeVar, Union, get_args, get_origin

import httpx
from pydantic import BaseModel, ValidationError

from cwt.util.jsonio import append_jsonl, now_iso
from cwt.util.retry import retry_async

logger = logging.getLogger("cwt.llm")

T = TypeVar("T", bound=BaseModel)

RETRYABLE = {429, 500, 502, 503, 504, 522, 524}


class Tier(StrEnum):
    CHEAP = "cheap"      # extraction, scoring, judging — ~35 calls/run
    STRONG = "strong"    # creative writing — ~5 calls/run


class UnparseableJson(ValueError):
    def __init__(self, raw: str):
        super().__init__(f"Could not extract JSON from model output ({len(raw)} chars)")
        self.raw = raw


class BudgetExceeded(RuntimeError):
    def __init__(self, spent: float, cap: float, stage: str):
        super().__init__(f"LLM budget ${cap:.2f} exceeded at stage {stage!r} (spent ${spent:.4f})")
        self.spent, self.cap, self.stage = spent, cap, stage


class ArtifactValidationError(RuntimeError):
    def __init__(self, *, stage: str, schema: str, error: Exception | None):
        super().__init__(f"Stage {stage!r} could not produce valid {schema} after repairs: {error}")
        self.stage, self.schema, self.error = stage, schema, error


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    base_url: str
    api_key_env: str
    supports_json_mode: bool
    extra_headers: dict[str, str] = field(default_factory=dict)


PROFILES: dict[str, ProviderProfile] = {
    "openrouter": ProviderProfile(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        supports_json_mode=True,
        extra_headers={
            "HTTP-Referer": "https://crowdwisdomtrading.com",
            "X-Title": "CWT Video Ads Agent",
        },
    ),
    "nvidia": ProviderProfile(
        name="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key_env="NVIDIA_API_KEY",
        # NIM accepts response_format inconsistently across models. We rely on
        # prompt-level enforcement plus the repair loop instead. This is a
        # capability declaration, not a workaround — see Rule A3.
        supports_json_mode=False,
    ),
}


def extract_json(raw: str) -> Any:
    """Tolerant JSON extraction. Deliberately dumb and deterministic.

    Models wrap JSON in markdown fences and prose even when explicitly told not
    to. This is the single most common real failure in structured-output
    pipelines, so it gets three escalating attempts before giving up.
    """
    raw = raw.strip()
    try:
        return json.loads(raw)                                   # 1. already clean
    except json.JSONDecodeError:
        pass

    fence = raw
    if "```" in raw:                                             # 2. strip fences
        parts = raw.split("```")
        for part in parts:
            candidate = part
            if candidate.lower().startswith("json"):
                candidate = candidate[4:]
            try:
                return json.loads(candidate.strip())
            except json.JSONDecodeError:
                continue

    start = raw.find("{")                                        # 3. brace-depth scan
    if start == -1:
        raise UnparseableJson(raw)
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise UnparseableJson(raw) from exc
    raise UnparseableJson(raw)


def schema_instruction(schema: type[BaseModel]) -> str:
    """Layer 2 of JSON enforcement. ALWAYS applied, even when the provider has
    native JSON mode — it measurably improves field-level accuracy."""
    return (
        "Return ONLY a single JSON object. No prose, no markdown fences, no commentary.\n"
        "It must validate against this JSON Schema. Every `required` field must be present.\n"
        "Use null for unknown optional values — never omit a key, never invent one.\n\n"
        + json.dumps(schema.model_json_schema(), indent=2)
    )


@dataclass
class LLMResult:
    text: str
    model: str
    tier: str
    stage: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    salvaged: bool = False
    repair_attempts: int = 0


class LLMClient:
    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        model_cheap: str,
        model_strong: str,
        fallbacks: list[str],
        max_concurrency: int,
        max_usd: float,
        ledger_path: Path,
        timeout_s: float = 120.0,
        repair_attempts: int = 2,
    ):
        self.profile = PROFILES[provider]
        self.api_key = api_key
        self.models = {Tier.CHEAP: model_cheap, Tier.STRONG: model_strong}
        self.fallbacks = list(fallbacks)
        self.max_usd = max_usd
        self.ledger_path = Path(ledger_path)
        self.repair_attempts = repair_attempts
        self.timeout_s = timeout_s
        self._sem = asyncio.Semaphore(max_concurrency)
        self._consecutive_429 = 0
        self._demoted_to: str | None = None
        self.run_cost_usd = 0.0
        self.last_result: LLMResult | None = None
        self._retry_base = 1.0

    async def complete(
        self,
        *,
        tier: Tier,
        messages: list[dict],
        schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stage: str = "",
        repair_attempts: int = 0,
    ) -> LLMResult:
        tier = Tier(tier)
        model = self._demoted_to or self.models[tier]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # LAYER 1 — provider-native JSON mode, when the profile supports it.
        if schema and self.profile.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}
        # LAYER 2 — prompt-level schema injection. ALWAYS.
        if schema:
            payload["messages"] = [
                *messages,
                {"role": "system", "content": schema_instruction(schema)},
            ]

        started = time.perf_counter()
        async with self._sem:
            async def _call():
                # Ensure model in payload uses current demoted model if demoted during retries
                current_model = self._demoted_to or self.models[tier]
                payload["model"] = current_model

                async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                    resp = await client.post(
                        f"{self.profile.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                            **self.profile.extra_headers,
                        },
                        json=payload,
                    )
                    if resp.status_code == 429:
                        self._consecutive_429 += 1
                        if self._consecutive_429 >= 3 and self.fallbacks:
                            self._demoted_to = self.fallbacks[0]
                            logger.warning(
                                "3 consecutive 429s — demoting to %s for this run",
                                self._demoted_to,
                            )
                        resp.raise_for_status()
                    resp.raise_for_status()
                    self._consecutive_429 = 0
                    return resp.json()

            data = await retry_async(_call, max_attempts=5, base=self._retry_base, cap=60.0)

        usage = data.get("usage") or {}
        result = LLMResult(
            text=data["choices"][0]["message"]["content"],
            model=data.get("model", model),
            tier=str(tier),
            stage=stage,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
            repair_attempts=repair_attempts,
        )
        self.last_result = result
        self._record(result)
        return result

    async def complete_validated(
        self,
        *,
        tier: Tier,
        messages: list[dict],
        schema: type[T],
        stage: str,
        max_repairs: int | None = None,
    ) -> T:
        """Layer 3 plus the repair loop. This is the function stages actually call."""
        max_repairs = self.repair_attempts if max_repairs is None else max_repairs
        convo = list(messages)
        last_error: Exception | None = None

        for attempt in range(max_repairs + 1):
            result = await self.complete(
                tier=tier,
                messages=convo,
                schema=schema,
                stage=stage,
                repair_attempts=attempt,
            )
            try:
                return schema.model_validate(extract_json(result.text))
            except (UnparseableJson, ValidationError) as exc:
                last_error = exc
                if attempt == max_repairs:
                    break
                convo = [
                    *convo,
                    {"role": "assistant", "content": result.text},
                    {"role": "user", "content": self._repair_instruction(exc, schema)},
                ]
                logger.warning(
                    "stage=%s repair %d/%d: %s",
                    stage,
                    attempt + 1,
                    max_repairs,
                    str(exc)[:200],
                )

        salvaged = self._try_salvage(last_error, schema)
        if salvaged is not None:
            logger.warning("stage=%s salvaged by filling defaults for optional fields", stage)
            if self.last_result is not None:
                self.last_result.salvaged = True
            self._mark_last_ledger_salvaged()
            return salvaged

        raise ArtifactValidationError(stage=stage, schema=schema.__name__, error=last_error)

    @staticmethod
    def _repair_instruction(exc: Exception, schema: type[BaseModel]) -> str:
        if isinstance(exc, UnparseableJson):
            return (
                "Your previous response was not parseable as JSON. "
                "Return ONLY the JSON object — no markdown fences, no explanation."
            )
        lines = []
        if hasattr(exc, "errors") and callable(exc.errors):
            for err in exc.errors()[:10]:                    # cap: never flood the context
                loc = ".".join(str(p) for p in err.get("loc", []))
                lines.append(f"  - `{loc}`: {err.get('msg', '')}")
        else:
            lines.append(f"  - {exc}")
        return (
            "Your previous response failed schema validation:\n"
            + "\n".join(lines)
            + "\n\nFix ONLY these problems. Preserve everything already valid. "
            "Return the complete corrected JSON object."
        )

    @staticmethod
    def _is_optional_field(field_info: Any) -> bool:
        if not field_info.is_required():
            return True
        origin = get_origin(field_info.annotation)
        args = get_args(field_info.annotation)
        if origin is Union and type(None) in args:
            return True
        return False

    @staticmethod
    def _default_for_field(field_info: Any) -> Any:
        origin = get_origin(field_info.annotation)
        args = get_args(field_info.annotation)
        if field_info.annotation in (list, list[str]) or origin is list:
            return []
        if origin is Union:
            for arg in args:
                if arg is not type(None) and (arg in (list, list[str]) or get_origin(arg) is list):
                    return []
        return None

    @classmethod
    def _try_salvage(cls, exc: Exception | None, schema: type[T]) -> T | None:
        """Narrow, high-frequency case: structurally valid but missing only OPTIONAL
        fields. Fills []/None defaults. NEVER fills a required field and never
        coerces a wrong-typed value — those are real errors and must surface."""
        if not isinstance(exc, ValidationError):
            return None
        try:
            errors = exc.errors()
            if not errors:
                return None
            data = errors[0].get("input")
            if not isinstance(data, dict):
                return None
            data = dict(data)
            for name, field_info in schema.model_fields.items():
                if name not in data and cls._is_optional_field(field_info):
                    data[name] = cls._default_for_field(field_info)
            return schema.model_validate(data)
        except Exception:
            return None

    def _mark_last_ledger_salvaged(self) -> None:
        try:
            if not self.ledger_path.exists():
                return
            lines = self.ledger_path.read_text(encoding="utf-8").splitlines()
            if not lines:
                return
            last = json.loads(lines[-1])
            last["salvaged"] = True
            lines[-1] = json.dumps(last, ensure_ascii=False, default=str)
            with open(self.ledger_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(lines) + "\n")
        except OSError as exc:
            logger.warning("Failed to update salvaged flag in ledger: %s", exc)

    def _record(self, result: LLMResult) -> None:
        cost = estimate_cost(result.model, result.prompt_tokens, result.completion_tokens)
        self.run_cost_usd += cost
        try:
            append_jsonl(
                self.ledger_path,
                {
                    "ts": now_iso(),
                    "stage": result.stage,
                    "tier": result.tier,
                    "model": result.model,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                    "cost_usd": cost,
                    "latency_ms": result.latency_ms,
                    "salvaged": result.salvaged,
                    "repair_attempts": result.repair_attempts,
                    "run_total_usd": self.run_cost_usd,
                },
            )
        except OSError as exc:
            logger.warning("Failed to write LLM ledger at %s: %s", self.ledger_path, exc)

        # Budget is enforced HERE, inside the client, on the call that crosses the
        # cap — not checked by callers afterwards. A prompt-level request to
        # "stay under budget" is not a control. This is.
        if self.run_cost_usd > self.max_usd:
            raise BudgetExceeded(self.run_cost_usd, self.max_usd, result.stage)


# Static price table. An unknown model returns 0.0 and logs a warning — we never
# guess a price, because a wrong number in the cost ledger is worse than a zero.
PRICES: dict[str, tuple[float, float]] = {   # (usd per 1M prompt, usd per 1M completion)
    "google/gemini-2.5-flash": (0.075, 0.30),
    "anthropic/claude-sonnet-4.5": (3.00, 15.00),
    "meta/llama-3.3-70b-instruct": (0.12, 0.30),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = PRICES.get(model)
    if price is None:
        logger.warning("No price for model %s — recording cost 0.0", model)
        return 0.0
    return (prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000
