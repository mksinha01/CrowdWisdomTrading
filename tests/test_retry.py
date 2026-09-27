"""Tests for retry_async utility (Story S02)."""
from __future__ import annotations

import pytest

from cwt.util.retry import retry_async


@pytest.mark.asyncio
async def test_retry_async_immediate_success():
    """retry_async returns result immediately on first attempt."""
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        return "success"

    result = await retry_async(fn, max_attempts=3, base=0.01)
    assert result == "success"
    assert calls == 1


@pytest.mark.asyncio
async def test_retry_async_succeeds_on_attempt_3():
    """retry_async recovers when function raises twice then succeeds on attempt 3."""
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError(f"fail {calls}")
        return "recovered"

    result = await retry_async(fn, max_attempts=5, base=0.01)
    assert result == "recovered"
    assert calls == 3


@pytest.mark.asyncio
async def test_retry_async_exhausts_attempts():
    """retry_async re-raises exception when max_attempts is exceeded."""
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise ValueError(f"failure {calls}")

    with pytest.raises(ValueError, match="failure 3"):
        await retry_async(fn, max_attempts=3, base=0.01)
    assert calls == 3


@pytest.mark.asyncio
async def test_retry_async_invalid_max_attempts():
    """retry_async raises ValueError if max_attempts < 1."""
    async def fn():
        return 1

    with pytest.raises(ValueError, match="max_attempts must be at least 1"):
        await retry_async(fn, max_attempts=0)


class DummyResponse:
    def __init__(self, headers: dict[str, str]):
        self.headers = headers


class DummyHTTPError(Exception):
    def __init__(self, message: str, headers: dict[str, str]):
        super().__init__(message)
        self.response = DummyResponse(headers)


@pytest.mark.asyncio
async def test_retry_async_honours_retry_after():
    """retry_async extracts Retry-After from exception response."""
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DummyHTTPError("rate limit", {"retry-after": "0.02"})
        return "after-retry"

    result = await retry_async(fn, max_attempts=3, base=0.01)
    assert result == "after-retry"
    assert calls == 2
