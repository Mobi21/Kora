"""Tests for kora_v2.core.errors — retry_with_backoff."""

from __future__ import annotations

import asyncio

import pytest

from kora_v2.core.errors import retry_with_backoff
from kora_v2.core.exceptions import (
    LLMConnectionError,
    LLMGenerationError,
    LLMTimeoutError,
)


@pytest.mark.asyncio
async def test_retry_succeeds_first_attempt():
    """Function succeeds on first call — no retries needed."""
    call_count = 0

    async def succeeds():
        nonlocal call_count
        call_count += 1
        return "ok"

    result = await retry_with_backoff(succeeds, max_retries=3, base_delay=0.01)
    assert result == "ok"
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_on_connection_error():
    """Retries on LLMConnectionError, then succeeds."""
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise LLMConnectionError("connection refused")
        return "recovered"

    result = await retry_with_backoff(flaky, max_retries=3, base_delay=0.01)
    assert result == "recovered"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_on_timeout_error():
    """Retries on LLMTimeoutError, then succeeds."""
    call_count = 0

    async def slow():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise LLMTimeoutError("timed out")
        return "done"

    result = await retry_with_backoff(slow, max_retries=3, base_delay=0.01)
    assert result == "done"
    assert call_count == 2


@pytest.mark.asyncio
async def test_retry_exhausted_raises():
    """All retries exhausted — re-raises the last exception."""

    async def always_fails():
        raise LLMConnectionError("still down")

    with pytest.raises(LLMConnectionError, match="still down"):
        await retry_with_backoff(always_fails, max_retries=2, base_delay=0.01)


@pytest.mark.asyncio
async def test_no_retry_on_generation_error():
    """LLMGenerationError is NOT retried by default — raises immediately."""
    call_count = 0

    async def bad_request():
        nonlocal call_count
        call_count += 1
        raise LLMGenerationError("bad request")

    with pytest.raises(LLMGenerationError, match="bad request"):
        await retry_with_backoff(bad_request, max_retries=3, base_delay=0.01)

    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_passes_args_and_kwargs():
    """Positional and keyword arguments are forwarded to the function."""

    async def adder(a: int, b: int, offset: int = 0):
        return a + b + offset

    result = await retry_with_backoff(adder, 3, 4, offset=10, max_retries=1, base_delay=0.01)
    assert result == 17


@pytest.mark.asyncio
async def test_retry_custom_retryable_exceptions():
    """Custom retryable_exceptions tuple is respected."""
    call_count = 0

    async def custom_fail():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise LLMGenerationError("retryable generation error")
        return "ok"

    result = await retry_with_backoff(
        custom_fail,
        max_retries=3,
        base_delay=0.01,
        retryable_exceptions=(LLMGenerationError,),
    )
    assert result == "ok"
    assert call_count == 2
