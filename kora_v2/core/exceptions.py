"""Kora V2 exception hierarchy.

All Kora-specific exceptions inherit from KoraError, allowing
for unified error handling while maintaining specific error types.
"""


class KoraError(Exception):
    """Base exception for all Kora-related errors."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} | Details: {self.details}"
        return self.message


# ── LLM Errors ────────────────────────────────────────────────────────────

class LLMError(KoraError):
    """Base exception for LLM-related errors."""


class LLMConnectionError(LLMError):
    """Cannot reach the LLM API endpoint."""


class LLMGenerationError(LLMError):
    """LLM returned an error during generation."""


class LLMTimeoutError(LLMError):
    """LLM request timed out."""


# ── Memory Errors ─────────────────────────────────────────────────────────

class MemoryError(KoraError):
    """Base exception for memory-related errors."""


# ── Worker Errors ────────────────────────────────────────────────────────


class PlanningFailedError(KoraError):
    """Raised when the planner agent fails after retries."""
