"""Context budget monitor for MiniMax M2.7 (205K context window).

Tracks cumulative token usage per conversation and manages tiered
compression strategies. MiniMax docs warn that the model terminates
prematurely near context capacity -- proactive management is critical.

Token counting uses tiktoken cl100k_base. tiktoken is a required
dependency; the fallback path exists only as a safety net.
"""

import json
import math
from enum import StrEnum
from typing import Any

import structlog

logger = structlog.get_logger()

# Try to import tiktoken for accurate token counting.
try:
    import tiktoken

    _ENCODER = tiktoken.get_encoding("cl100k_base")
    _HAS_TIKTOKEN = True
except ImportError:
    _ENCODER = None
    _HAS_TIKTOKEN = False
    logger.warning(
        "tiktoken not installed -- token counting will use inaccurate character-based "
        "estimation (~4 chars/token). Install with: pip install tiktoken"
    )


class BudgetTier(StrEnum):
    """Context budget tiers with increasing compression."""

    NORMAL = "normal"  # 0-100K: full fidelity
    PRUNE = "prune"  # 100K-150K: observation masking (strip thinking blocks + mask old tool results)
    SUMMARIZE = "summarize"  # 150K-175K: summarize old turns
    AGGRESSIVE = "aggressive"  # 175K-195K: aggressive summarization
    HARD_STOP = "hard_stop"  # 195K+: refuse generation


# Tier thresholds in tokens
TIER_THRESHOLDS: dict[BudgetTier, int] = {
    BudgetTier.NORMAL: 0,
    BudgetTier.PRUNE: 100_000,
    BudgetTier.SUMMARIZE: 150_000,
    BudgetTier.AGGRESSIVE: 175_000,
    BudgetTier.HARD_STOP: 195_000,
}

# Budget allocation (tokens)
BUDGET_ALLOCATION: dict[str, int] = {
    "tools": 3_000,
    "system_prompt": 6_000,
    "conversation": 120_000,
    "tool_context": 15_000,
    "output_and_thinking": 50_000,
    "safety_margin": 11_000,
}


def count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken or character estimate.

    Args:
        text: Text to count tokens for.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    if _HAS_TIKTOKEN and _ENCODER:
        return len(_ENCODER.encode(text))
    # Fallback: ~4 chars per token -- intentionally overestimates to prevent
    # context overflow. Better to trigger compaction early than hit 400 errors.
    return max(1, len(text) // 4)


def count_message_tokens(message: dict[str, Any]) -> int:
    """Count tokens in a single message including overhead.

    Handles:
    - Text content (string)
    - Content blocks (list of dicts with thinking, text, tool_use)
    - Tool call arguments

    Args:
        message: Message dict with role and content.

    Returns:
        Token count including 4-token message overhead.
    """
    tokens = 4  # Per-message overhead (role, delimiters)

    content = message.get("content", "")

    if isinstance(content, str):
        tokens += count_tokens(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "text":
                    tokens += count_tokens(block.get("text", ""))
                elif block_type == "thinking":
                    tokens += count_tokens(block.get("thinking", ""))
                elif block_type == "tool_use":
                    tokens += count_tokens(block.get("name", ""))
                    input_data = block.get("input", {})
                    tokens += count_tokens(json.dumps(input_data) if input_data else "")
                elif block_type == "tool_result":
                    tokens += count_tokens(block.get("content", ""))

    return tokens


def count_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Count total tokens across all messages.

    Args:
        messages: List of message dicts.

    Returns:
        Total token count.
    """
    return sum(count_message_tokens(msg) for msg in messages)


class ContextBudgetMonitor:
    """Monitors context window usage and triggers compression.

    Tracks cumulative tokens and determines which budget tier
    the conversation is in. Used by the ReAct loop to decide
    when to strip thinking blocks or summarize history.
    """

    def __init__(self, context_window: int = 200_000):
        """Initialize the budget monitor.

        Args:
            context_window: Total context window size in tokens.
        """
        self._context_window = context_window

    def _tier_thresholds(self) -> dict[BudgetTier, int]:
        """Return thresholds scaled to the configured context window.

        The original thresholds were tuned for a ~200K token context window.
        Scaling allows smaller windows (e.g., in tests) to reach all tiers.
        """
        base_window = 200_000
        if self._context_window == base_window:
            return TIER_THRESHOLDS

        scale = max(self._context_window / base_window, 0.01)
        scaled: dict[BudgetTier, int] = {BudgetTier.NORMAL: 0}

        for tier in (
            BudgetTier.PRUNE,
            BudgetTier.SUMMARIZE,
            BudgetTier.AGGRESSIVE,
            BudgetTier.HARD_STOP,
        ):
            scaled[tier] = max(1, math.floor(TIER_THRESHOLDS[tier] * scale))

        # Keep tiers strictly increasing even for very small windows.
        ordered = [
            BudgetTier.PRUNE,
            BudgetTier.SUMMARIZE,
            BudgetTier.AGGRESSIVE,
            BudgetTier.HARD_STOP,
        ]
        last_value = 0
        for tier in ordered:
            scaled[tier] = max(scaled[tier], last_value + 1)
            last_value = scaled[tier]

        # HARD_STOP must still leave room for at least one token below the full window.
        scaled[BudgetTier.HARD_STOP] = min(
            scaled[BudgetTier.HARD_STOP],
            max(1, self._context_window - 1),
        )

        return scaled

    def estimate_current_usage(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> int:
        """Estimate current context usage from messages.

        Args:
            messages: Current conversation messages.
            system_prompt: System prompt text.
            tools: Tool definitions.

        Returns:
            Estimated total tokens.
        """
        total = count_messages_tokens(messages)
        total += count_tokens(system_prompt)

        if tools:
            tools_text = json.dumps(tools)
            total += count_tokens(tools_text)

        return total

    def get_tier(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> BudgetTier:
        """Determine current budget tier based on estimated usage.

        Args:
            messages: Current conversation messages.
            system_prompt: System prompt.
            tools: Tool definitions.

        Returns:
            Current BudgetTier.
        """
        estimated = self.estimate_current_usage(messages, system_prompt, tools)
        thresholds = self._tier_thresholds()

        if estimated >= thresholds[BudgetTier.HARD_STOP]:
            return BudgetTier.HARD_STOP
        if estimated >= thresholds[BudgetTier.AGGRESSIVE]:
            return BudgetTier.AGGRESSIVE
        if estimated >= thresholds[BudgetTier.SUMMARIZE]:
            return BudgetTier.SUMMARIZE
        if estimated >= thresholds[BudgetTier.PRUNE]:
            return BudgetTier.PRUNE
        return BudgetTier.NORMAL

    def remaining_budget(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> int:
        """Estimated remaining tokens in context window.

        Based on actual message content, not cumulative tracking
        (which is semantically wrong since each call re-submits history).
        """
        used = self.estimate_current_usage(messages, system_prompt, tools)
        return max(0, self._context_window - used)

    def should_refuse_generation(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> bool:
        """Check if context is too full for safe generation.

        Args:
            messages: Current messages.
            system_prompt: System prompt.
            tools: Tool definitions.

        Returns:
            True if generation should be refused.
        """
        return self.get_tier(messages, system_prompt, tools) == BudgetTier.HARD_STOP

    def get_status(self) -> dict[str, Any]:
        """Get budget monitor status for observability."""
        return {
            "context_window": self._context_window,
            "has_tiktoken": _HAS_TIKTOKEN,
        }

    def reset(self) -> None:
        """Reset for a new conversation (no-op -- stateless monitor)."""
