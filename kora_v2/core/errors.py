"""Kora V2 — Retry utility with exponential backoff.

Wraps async callables with retry logic for transient LLM failures
(connection errors, timeouts). Generation errors are NOT retried by
default since they indicate a bad request or rate-limit response.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import structlog

from kora_v2.core.exceptions import LLMConnectionError, LLMTimeoutError

log = structlog.get_logger(__name__)

T = TypeVar("T")


async def retry_with_backoff(
    fn: Callable[..., Awaitable[T]],
    *args: object,
    max_retries: int = 3,
    base_delay: float = 1.0,
    retryable_exceptions: tuple[type[Exception], ...] = (
        LLMConnectionError,
        LLMTimeoutError,
    ),
    **kwargs: object,
) -> T:
    """Retry *fn* with exponential backoff: ``base_delay * 2^attempt``.

    Parameters
    ----------
    fn:
        An async callable to invoke.
    *args:
        Positional arguments forwarded to *fn*.
    max_retries:
        Maximum number of retry attempts (default 3).
    base_delay:
        Initial delay in seconds (default 1.0). Doubles each attempt.
    retryable_exceptions:
        Exception types that trigger a retry. By default only
        :class:`LLMConnectionError` and :class:`LLMTimeoutError`.
    **kwargs:
        Keyword arguments forwarded to *fn*.

    Returns
    -------
    T
        The return value of *fn* on first success.

    Raises
    ------
    Exception
        The last exception if all retries are exhausted, or immediately
        if the exception is not in *retryable_exceptions*.
    """
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                log.warning(
                    "retry_with_backoff",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    delay=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
            else:
                log.error(
                    "retry_exhausted",
                    max_retries=max_retries,
                    error=str(exc),
                )

    # All retries exhausted — re-raise the last exception
    assert last_exc is not None  # noqa: S101
    raise last_exc
