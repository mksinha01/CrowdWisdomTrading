"""Async retry with exponential backoff, full jitter, and Retry-After support."""
from __future__ import annotations

import asyncio
import email.utils
import random
from datetime import datetime, timezone
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


def _extract_retry_after(exc: Exception) -> float | None:
    """Extract Retry-After header delay in seconds from an exception, if present."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers is None:
        headers = getattr(exc, "headers", None)

    if headers is not None and hasattr(headers, "get"):
        val = headers.get("retry-after") or headers.get("Retry-After")
        if val is not None:
            try:
                return max(0.0, float(val))
            except (ValueError, TypeError):
                try:
                    dt = email.utils.parsedate_to_datetime(str(val))
                    now = datetime.now(timezone.utc)
                    delay = (dt - now).total_seconds()
                    return max(0.0, delay)
                except Exception:
                    pass
    return None


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 5,
    base: float = 1.0,
    cap: float = 60.0,
) -> T:
    """Retry an async function with exponential backoff and full jitter.

    sleep = random.uniform(0, min(cap, base * 2**attempt))
    Honours Retry-After header if present on the raised exception.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    for attempt in range(max_attempts):
        try:
            return await fn()
        except Exception as exc:
            if attempt == max_attempts - 1:
                raise
            retry_after = _extract_retry_after(exc)
            if retry_after is not None:
                sleep_s = min(cap, retry_after)
            else:
                sleep_s = random.uniform(0, min(cap, base * (2**attempt)))
            await asyncio.sleep(sleep_s)

    raise RuntimeError("Unreachable")
