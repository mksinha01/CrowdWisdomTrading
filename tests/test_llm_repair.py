"""Tests for LLM Client, Repair Loop, Tolerant JSON Extraction, Budget & Concurrency (S09)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import httpx
import pytest
import respx
from pydantic import BaseModel, Field

from cwt.clients.llm import (
    PROFILES,
    RETRYABLE,
    ArtifactValidationError,
    BudgetExceeded,
    LLMClient,
    LLMResult,
    ProviderProfile,
    Tier,
    UnparseableJson,
    estimate_cost,
    extract_json,
    schema_instruction,
)


# ===========================================================================
# 1. extract_json tests
# ===========================================================================

def test_extract_json_clean():
    raw = '{"key": "value", "count": 42, "flag": true}'
    assert extract_json(raw) == {"key": "value", "count": 42, "flag": True}


def test_extract_json_fenced_json():
    raw = '```json\n{"message": "hello world", "nested": {"a": 1}}\n```'
    assert extract_json(raw) == {"message": "hello world", "nested": {"a": 1}}


def test_extract_json_fenced_bare():
    raw = '```\n{"bare": "fence", "ok": true}\n```'
    assert extract_json(raw) == {"bare": "fence", "ok": True}


def test_extract_json_prose_wrapped():
    raw = "Sure! Here is the JSON you requested:\n{\"status\": \"ok\", \"code\": 200}\nHope that helps!"
    assert extract_json(raw) == {"status": "ok", "code": 200}


def test_extract_json_brace_inside_string():
    # Headline or text contains curly braces inside JSON string literal
    raw = '{"headline": "Save 50% with {SPECIAL} code!", "id": 123}'
    assert extract_json(raw) == {"headline": "Save 50% with {SPECIAL} code!", "id": 123}


def test_extract_json_escaped_quote_and_nested_braces():
    raw = 'Here is output: {"text": "He said \\"hello {world}\\" to everyone", "num": 5} end'
    assert extract_json(raw) == {"text": 'He said "hello {world}" to everyone', "num": 5}


def test_extract_json_unbalanced_braces():
    raw = '{"key": "value", "incomplete": {'
    with pytest.raises(UnparseableJson) as exc_info:
        extract_json(raw)
    assert exc_info.value.raw == raw


def test_extract_json_garbage_raises():
    raw = "This response contains absolutely no json or braces whatsoever."
    with pytest.raises(UnparseableJson) as exc_info:
        extract_json(raw)
    assert exc_info.value.raw == raw
    assert "Could not extract JSON" in str(exc_info.value)


def test_extract_json_syntax_error_inside_braces():
    raw = "Some prose { not valid json syntax : 123 } trailing"
    with pytest.raises(UnparseableJson) as exc_info:
        extract_json(raw)
    assert exc_info.value.raw == raw


# ===========================================================================
# 2. Schema instruction and pricing tests
# ===========================================================================

class SampleSchema(BaseModel):
    name: str
    score: float
    tags: list[str] = Field(default_factory=list)


def test_schema_instruction():
    instruction = schema_instruction(SampleSchema)
    assert "Return ONLY a single JSON object." in instruction
    assert "JSON Schema" in instruction
    schema_dict = json.loads(instruction.split("\n\n", 1)[1])
    assert "properties" in schema_dict
    assert "name" in schema_dict["properties"]
    assert "score" in schema_dict["properties"]


def test_estimate_cost_known_models():
    # google/gemini-2.5-flash: 0.075 / 1M prompt, 0.30 / 1M completion
    cost_flash = estimate_cost("google/gemini-2.5-flash", 1_000_000, 1_000_000)
    assert pytest.approx(cost_flash, rel=1e-5) == 0.375

    # anthropic/claude-sonnet-4.5: 3.00 / 1M prompt, 15.00 / 1M completion
    cost_sonnet = estimate_cost("anthropic/claude-sonnet-4.5", 10_000, 2_000)
    expected_sonnet = (10_000 * 3.00 + 2_000 * 15.00) / 1_000_000
    assert pytest.approx(cost_sonnet, rel=1e-5) == expected_sonnet


def test_estimate_cost_unknown_model(caplog):
    cost = estimate_cost("unknown/model-xyz", 10_000, 10_000)
    assert cost == 0.0
    assert "No price for model unknown/model-xyz" in caplog.text


# ===========================================================================
# 3. Provider profiles and Layer 1/Layer 2 verification
# ===========================================================================

def test_provider_profiles():
    assert "openrouter" in PROFILES
    assert "nvidia" in PROFILES
    assert PROFILES["openrouter"].supports_json_mode is True
    assert PROFILES["openrouter"].extra_headers["HTTP-Referer"] == "https://crowdwisdomtrading.com"
    assert PROFILES["openrouter"].extra_headers["X-Title"] == "CWT Video Ads Agent"
    assert PROFILES["nvidia"].supports_json_mode is False


@pytest.mark.asyncio
async def test_json_mode_layers(tmp_path: Path):
    ledger = tmp_path / "ledger.jsonl"

    # OpenRouter client: supports_json_mode = True
    client_openrouter = LLMClient(
        provider="openrouter",
        api_key="test-key",
        model_cheap="google/gemini-2.5-flash",
        model_strong="anthropic/claude-sonnet-4.5",
        fallbacks=[],
        max_concurrency=4,
        max_usd=1.0,
        ledger_path=ledger,
    )

    with respx.mock(assert_all_called=True) as respx_mock:
        route_or = respx_mock.post("https://openrouter.ai/api/v1/chat/completions").respond(
            json={
                "choices": [{"message": {"content": '{"name": "test", "score": 9.5}'}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20},
                "model": "google/gemini-2.5-flash",
            }
        )
        await client_openrouter.complete(
            tier=Tier.CHEAP,
            messages=[{"role": "user", "content": "hi"}],
            schema=SampleSchema,
        )
        req_payload = json.loads(route_or.calls.last.request.content)
        # Layer 1: response_format present
        assert req_payload.get("response_format") == {"type": "json_object"}
        # Layer 2: schema instruction system message present
        assert any(
            m["role"] == "system" and "Return ONLY a single JSON object." in m["content"]
            for m in req_payload["messages"]
        )

    # NVIDIA client: supports_json_mode = False
    client_nvidia = LLMClient(
        provider="nvidia",
        api_key="test-key",
        model_cheap="meta/llama-3.3-70b-instruct",
        model_strong="meta/llama-3.3-70b-instruct",
        fallbacks=[],
        max_concurrency=4,
        max_usd=1.0,
        ledger_path=ledger,
    )

    with respx.mock(assert_all_called=True) as respx_mock:
        route_nv = respx_mock.post("https://integrate.api.nvidia.com/v1/chat/completions").respond(
            json={
                "choices": [{"message": {"content": '{"name": "test", "score": 8.0}'}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20},
                "model": "meta/llama-3.3-70b-instruct",
            }
        )
        await client_nvidia.complete(
            tier=Tier.CHEAP,
            messages=[{"role": "user", "content": "hi"}],
            schema=SampleSchema,
        )
        req_payload_nv = json.loads(route_nv.calls.last.request.content)
        # Layer 1: response_format MUST NOT be present
        assert "response_format" not in req_payload_nv
        # Layer 2: schema instruction system message MUST be present
        assert any(
            m["role"] == "system" and "Return ONLY a single JSON object." in m["content"]
            for m in req_payload_nv["messages"]
        )


# ===========================================================================
# 4. Repair loop test
# ===========================================================================

@pytest.mark.asyncio
async def test_repair_loop_success_on_attempt_1(tmp_path: Path):
    """First response invalid, second response valid -> returns model, repair_attempts == 1."""
    ledger = tmp_path / "ledger.jsonl"
    client = LLMClient(
        provider="openrouter",
        api_key="test-key",
        model_cheap="google/gemini-2.5-flash",
        model_strong="anthropic/claude-sonnet-4.5",
        fallbacks=[],
        max_concurrency=4,
        max_usd=1.0,
        ledger_path=ledger,
        repair_attempts=2,
    )

    with respx.mock(assert_all_called=True) as respx_mock:
        route = respx_mock.post("https://openrouter.ai/api/v1/chat/completions").side_effect = [
            # Call 0: Invalid (score missing, wrong type)
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"name": "test_bad"}'}}],
                    "usage": {"prompt_tokens": 60, "completion_tokens": 15},
                },
            ),
            # Call 1 (repair 1): Valid
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"name": "test_fixed", "score": 9.2}'}}],
                    "usage": {"prompt_tokens": 90, "completion_tokens": 25},
                },
            ),
        ]

        result: SampleSchema = await client.complete_validated(
            tier=Tier.CHEAP,
            messages=[{"role": "user", "content": "generate item"}],
            schema=SampleSchema,
            stage="test_repair",
        )

        assert result.name == "test_fixed"
        assert result.score == 9.2
        assert client.last_result is not None
        assert client.last_result.repair_attempts == 1
        assert client.last_result.salvaged is False


# ===========================================================================
# 5. Salvage tests
# ===========================================================================

class SchemaWithOptional(BaseModel):
    title: str
    notes: Optional[str] = None
    tags: Optional[list[str]] = None


@pytest.mark.asyncio
async def test_salvage_missing_only_optional(tmp_path: Path):
    """Response missing only optional field is salvaged and salvaged is True."""
    ledger = tmp_path / "ledger.jsonl"
    client = LLMClient(
        provider="openrouter",
        api_key="test-key",
        model_cheap="google/gemini-2.5-flash",
        model_strong="anthropic/claude-sonnet-4.5",
        fallbacks=[],
        max_concurrency=4,
        max_usd=1.0,
        ledger_path=ledger,
        repair_attempts=1,
    )

    # In SchemaWithOptionalWithoutDefault, notes & tags are optional in type annotation
    class StrictOptionalSchema(BaseModel):
        title: str
        notes: Optional[str]
        tags: Optional[list[str]]

    with respx.mock(assert_all_called=True) as respx_mock:
        # LLM returns title but omits notes and tags on both initial call and repair attempt
        respx_mock.post("https://openrouter.ai/api/v1/chat/completions").respond(
            json={
                "choices": [{"message": {"content": '{"title": "Salvageable Ad"}'}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 15},
            }
        )

        validated: StrictOptionalSchema = await client.complete_validated(
            tier=Tier.CHEAP,
            messages=[{"role": "user", "content": "ad info"}],
            schema=StrictOptionalSchema,
            stage="test_salvage",
        )

        assert validated.title == "Salvageable Ad"
        assert validated.notes is None
        assert validated.tags == []
        assert client.last_result is not None
        assert client.last_result.salvaged is True

        # Check ledger record has salvaged: True
        lines = ledger.read_text(encoding="utf-8").splitlines()
        last_rec = json.loads(lines[-1])
        assert last_rec["salvaged"] is True


@pytest.mark.asyncio
async def test_salvage_must_not_fire_for_missing_required(tmp_path: Path):
    """Salvage must NOT fire when a required field is missing -> ArtifactValidationError."""
    ledger = tmp_path / "ledger.jsonl"
    client = LLMClient(
        provider="openrouter",
        api_key="test-key",
        model_cheap="google/gemini-2.5-flash",
        model_strong="anthropic/claude-sonnet-4.5",
        fallbacks=[],
        max_concurrency=4,
        max_usd=1.0,
        ledger_path=ledger,
        repair_attempts=1,
    )

    with respx.mock(assert_all_called=True) as respx_mock:
        # Returns only notes; required 'title' is missing
        respx_mock.post("https://openrouter.ai/api/v1/chat/completions").respond(
            json={
                "choices": [{"message": {"content": '{"notes": "just notes"}'}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 15},
            }
        )

        with pytest.raises(ArtifactValidationError) as exc_info:
            await client.complete_validated(
                tier=Tier.CHEAP,
                messages=[{"role": "user", "content": "ad info"}],
                schema=SchemaWithOptional,
                stage="failing_stage",
            )

        err = exc_info.value
        assert err.stage == "failing_stage"
        assert err.schema == "SchemaWithOptional"
        assert err.error is not None


# ===========================================================================
# 6. Budget enforcement test
# ===========================================================================

@pytest.mark.asyncio
async def test_budget_exceeded_raised_inside_record(tmp_path: Path):
    """BudgetExceeded is raised from _record on the call that crosses the cap."""
    ledger = tmp_path / "ledger.jsonl"
    # Set cap very low: $0.0001
    client = LLMClient(
        provider="openrouter",
        api_key="test-key",
        model_cheap="google/gemini-2.5-flash",
        model_strong="anthropic/claude-sonnet-4.5",
        fallbacks=[],
        max_concurrency=4,
        max_usd=0.0001,
        ledger_path=ledger,
    )

    with respx.mock(assert_all_called=True) as respx_mock:
        # Prompt: 10,000 tokens @ $0.075/1M = $0.00075 (> $0.0001 cap)
        respx_mock.post("https://openrouter.ai/api/v1/chat/completions").respond(
            json={
                "choices": [{"message": {"content": '{"status": "ok"}'}}],
                "usage": {"prompt_tokens": 10000, "completion_tokens": 100},
                "model": "google/gemini-2.5-flash",
            }
        )

        with pytest.raises(BudgetExceeded) as exc_info:
            await client.complete(
                tier=Tier.CHEAP,
                messages=[{"role": "user", "content": "big prompt"}],
                stage="scoring_stage",
            )

        err = exc_info.value
        assert err.cap == 0.0001
        assert err.spent > 0.0001
        assert err.stage == "scoring_stage"


# ===========================================================================
# 7. Semaphore concurrency bound test
# ===========================================================================

@pytest.mark.asyncio
async def test_semaphore_bounds_concurrency(tmp_path: Path):
    """12 concurrent complete() calls with max_concurrency=4 never exceed 4 in flight."""
    ledger = tmp_path / "ledger.jsonl"
    client = LLMClient(
        provider="openrouter",
        api_key="test-key",
        model_cheap="google/gemini-2.5-flash",
        model_strong="anthropic/claude-sonnet-4.5",
        fallbacks=[],
        max_concurrency=4,
        max_usd=10.0,
        ledger_path=ledger,
    )

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def mock_handler(request: httpx.Request):
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            if in_flight > max_in_flight:
                max_in_flight = in_flight
        await asyncio.sleep(0.01)
        async with lock:
            in_flight -= 1
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"status": "ok"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                "model": "google/gemini-2.5-flash",
            },
        )

    with respx.mock() as respx_mock:
        respx_mock.post("https://openrouter.ai/api/v1/chat/completions").mock(side_effect=mock_handler)

        tasks = [
            client.complete(tier=Tier.CHEAP, messages=[{"role": "user", "content": f"msg {i}"}])
            for i in range(12)
        ]
        results = await asyncio.gather(*tasks)

        assert len(results) == 12
        assert max_in_flight <= 4


# ===========================================================================
# 8. 429 Demotion test
# ===========================================================================

@pytest.mark.asyncio
async def test_consecutive_429_demotes_to_fallback(tmp_path: Path, monkeypatch):
    """3 consecutive 429s demote to fallbacks[0] and the next call uses it."""
    # Speed up retry sleep for test
    real_sleep = asyncio.sleep
    monkeypatch.setattr("cwt.util.retry.asyncio.sleep", lambda s: real_sleep(0))

    ledger = tmp_path / "ledger.jsonl"
    client = LLMClient(
        provider="openrouter",
        api_key="test-key",
        model_cheap="google/gemini-2.5-flash",
        model_strong="anthropic/claude-sonnet-4.5",
        fallbacks=["meta/llama-3.3-70b-instruct"],
        max_concurrency=4,
        max_usd=10.0,
        ledger_path=ledger,
    )
    client._retry_base = 0.001

    requested_models: list[str] = []

    def mock_handler(request: httpx.Request):
        payload = json.loads(request.content)
        requested_models.append(payload["model"])
        if len(requested_models) <= 3:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                "model": payload["model"],
            },
        )

    with respx.mock() as respx_mock:
        respx_mock.post("https://openrouter.ai/api/v1/chat/completions").mock(side_effect=mock_handler)

        # Call 1: will fail with 429 three times, triggering demotion, and 4th attempt succeeds
        res1 = await client.complete(tier=Tier.CHEAP, messages=[{"role": "user", "content": "hi"}])
        assert client._demoted_to == "meta/llama-3.3-70b-instruct"

        # Call 2: next call must use demoted fallback model
        res2 = await client.complete(tier=Tier.CHEAP, messages=[{"role": "user", "content": "next"}])
        assert res2.model == "meta/llama-3.3-70b-instruct"

        # Verify the requested models
        # First 3 attempts used google/gemini-2.5-flash
        assert requested_models[0] == "google/gemini-2.5-flash"
        assert requested_models[1] == "google/gemini-2.5-flash"
        assert requested_models[2] == "google/gemini-2.5-flash"
        # 4th attempt and subsequent call used fallback
        assert requested_models[3] == "meta/llama-3.3-70b-instruct"
        assert requested_models[4] == "meta/llama-3.3-70b-instruct"


# ===========================================================================
# 9. Cost ledger verification
# ===========================================================================

@pytest.mark.asyncio
async def test_ledger_records_monotonically(tmp_path: Path):
    """llm_ledger.jsonl gets one line per call with run_total_usd monotonically increasing."""
    ledger = tmp_path / "llm_ledger.jsonl"
    client = LLMClient(
        provider="openrouter",
        api_key="test-key",
        model_cheap="google/gemini-2.5-flash",
        model_strong="anthropic/claude-sonnet-4.5",
        fallbacks=[],
        max_concurrency=4,
        max_usd=10.0,
        ledger_path=ledger,
    )

    with respx.mock() as respx_mock:
        respx_mock.post("https://openrouter.ai/api/v1/chat/completions").respond(
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
                "model": "google/gemini-2.5-flash",
            }
        )

        for i in range(3):
            await client.complete(
                tier=Tier.CHEAP,
                messages=[{"role": "user", "content": f"call {i}"}],
                stage=f"stage_{i}",
            )

    assert ledger.exists()
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3

    records = [json.loads(line) for line in lines]
    totals = [r["run_total_usd"] for r in records]

    assert totals[0] > 0.0
    assert totals[1] > totals[0]
    assert totals[2] > totals[1]

    for i, r in enumerate(records):
        assert r["stage"] == f"stage_{i}"
        assert r["tier"] == "cheap"
        assert r["model"] == "google/gemini-2.5-flash"
        assert r["prompt_tokens"] == 1000
        assert r["completion_tokens"] == 500
        assert "cost_usd" in r
        assert "latency_ms" in r
        assert "ts" in r
        assert r["salvaged"] is False
        assert r["repair_attempts"] == 0


@pytest.mark.asyncio
async def test_ledger_write_failure_does_not_abort_run(tmp_path: Path, monkeypatch):
    """Ledger write failures (OSError) must not kill a run."""
    ledger = tmp_path / "forbidden_dir" / "ledger.jsonl"
    # Make append_jsonl raise OSError
    def failing_append(path, record):
        raise OSError("Disk write failed")

    monkeypatch.setattr("cwt.clients.llm.append_jsonl", failing_append)

    client = LLMClient(
        provider="openrouter",
        api_key="test-key",
        model_cheap="google/gemini-2.5-flash",
        model_strong="anthropic/claude-sonnet-4.5",
        fallbacks=[],
        max_concurrency=4,
        max_usd=10.0,
        ledger_path=ledger,
    )

    with respx.mock() as respx_mock:
        respx_mock.post("https://openrouter.ai/api/v1/chat/completions").respond(
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "model": "google/gemini-2.5-flash",
            }
        )
        # Should succeed without raising OSError
        res = await client.complete(tier=Tier.CHEAP, messages=[{"role": "user", "content": "hi"}])
        assert res.text == '{"ok": true}'
