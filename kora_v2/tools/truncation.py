"""Structure-aware, budget-adaptive tool result truncation.

Replaces the naive character cutoff with intelligent strategies:
- JSON-aware: Keeps complete objects/items, adds "... and X more"
- Line-aware: Keeps header + first N rows for tables/lists
- Head+tail: 70/30 split preserves content at both ends
- Error preservation: Always keeps error messages in full
- Budget-adaptive: Tier limits shrink under context pressure
"""

import json
from dataclasses import dataclass

import structlog

from kora_v2.context.budget import BudgetTier

logger = structlog.get_logger()

# Character limits per budget tier
TIER_LIMITS: dict[BudgetTier, int] = {
    BudgetTier.NORMAL: 4000,
    BudgetTier.PRUNE: 3000,
    BudgetTier.SUMMARIZE: 2000,
    BudgetTier.AGGRESSIVE: 1000,
    BudgetTier.HARD_STOP: 500,
}

# Head/tail split ratio for plain text fallback
HEAD_RATIO = 0.70
TAIL_RATIO = 0.30


@dataclass
class TruncationResult:
    """Result of a truncation operation with metadata."""

    content: str  # The truncated string
    truncated: bool  # Whether truncation occurred
    original_length: int  # Original string length
    total_count: int | None  # Total items if JSON array


def truncate_tool_result(
    result: str,
    budget_tier: BudgetTier | None = None,
) -> TruncationResult:
    """Truncate a tool result with structure-aware strategies.

    Args:
        result: Raw tool result string.
        budget_tier: Current context budget tier (defaults to NORMAL).

    Returns:
        TruncationResult with truncated content and metadata.
    """
    if not result:
        return TruncationResult(
            content=result or "",
            truncated=False,
            original_length=len(result) if result else 0,
            total_count=None,
        )

    original_length = len(result)
    tier = budget_tier or BudgetTier.NORMAL
    limit = TIER_LIMITS.get(tier, TIER_LIMITS[BudgetTier.NORMAL])

    # Short-circuit if within limit
    if original_length <= limit:
        return TruncationResult(
            content=result,
            truncated=False,
            original_length=original_length,
            total_count=None,
        )

    # Error preservation: if the result contains an error, try to keep it
    error_portion = _extract_error_portion(result)
    if error_portion and len(error_portion) <= limit:
        return TruncationResult(
            content=error_portion,
            truncated=True,
            original_length=original_length,
            total_count=None,
        )

    # Try JSON-aware truncation
    stripped = result.strip()
    if stripped.startswith(("[", "{")):
        json_result = _truncate_json(result, limit)
        if json_result is not None:
            content, total_count = json_result
            return TruncationResult(
                content=content,
                truncated=True,
                original_length=original_length,
                total_count=total_count,
            )

    # Try line-aware truncation
    if "\n" in result:
        return TruncationResult(
            content=_truncate_lines(result, limit),
            truncated=True,
            original_length=original_length,
            total_count=None,
        )

    # Plain text fallback: head + tail
    return TruncationResult(
        content=_truncate_head_tail(result, limit),
        truncated=True,
        original_length=original_length,
        total_count=None,
    )


def _truncate_json(result: str, limit: int) -> tuple[str, int | None] | None:
    """Truncate JSON result while preserving structure.

    For arrays: keeps first N complete items.
    For objects: keeps first N complete key-value pairs.

    Returns (truncated_string, total_count) or None if JSON parsing fails.
    total_count is the total array length for arrays, None for objects/scalars.
    """
    try:
        data = json.loads(result)
    except (json.JSONDecodeError, ValueError):
        return None

    if isinstance(data, list):
        content, total = _truncate_json_array(data, limit)
        return content, total
    elif isinstance(data, dict):
        return _truncate_json_object(data, limit), None

    # Scalar -- just stringify and truncate
    text = json.dumps(data, indent=2, default=str)
    if len(text) <= limit:
        return text, None
    return None  # Fall through to line truncation


def _truncate_json_array(data: list, limit: int) -> tuple[str, int]:
    """Keep first N complete items from a JSON array.

    Returns:
        Tuple of (truncated string, total array length).
    """
    total = len(data)
    if total == 0:
        return "[]", 0

    # Binary search for how many items fit
    kept: list = []
    for item in data:
        candidate = json.dumps(kept + [item], indent=2, default=str)
        suffix = f"\n... and {total - len(kept) - 1} more items" if len(kept) + 1 < total else ""
        if len(candidate) + len(suffix) > limit and kept:
            break
        kept.append(item)

    remaining = total - len(kept)
    output = json.dumps(kept, indent=2, default=str)
    if remaining > 0:
        output += f"\n... and {remaining} more items"
    return output, total


def _truncate_json_object(data: dict, limit: int) -> str:
    """Keep first N key-value pairs from a JSON object."""
    total_keys = len(data)
    if total_keys == 0:
        return "{}"

    preferred_list_keys = ("runs", "events", "templates", "items", "results")
    list_key = next(
        (key for key in preferred_list_keys if isinstance(data.get(key), list) and data.get(key)),
        None,
    )
    if list_key is None:
        list_key = next(
            (key for key, value in data.items() if isinstance(value, list) and value),
            None,
        )
    if list_key:
        items = list(data[list_key])
        remaining = 0
        while items:
            candidate = dict(data)
            candidate[list_key] = items
            if remaining > 0:
                candidate[f"{list_key}_truncated_count"] = remaining
            text = json.dumps(candidate, separators=(",", ":"), default=str)
            if len(text) <= limit:
                return text
            items = items[:-1]
            remaining += 1

    kept: dict = {}
    for key, value in data.items():
        kept[key] = value
        candidate = json.dumps(kept, indent=2, default=str)
        suffix = f"\n... and {total_keys - len(kept)} more keys" if len(kept) < total_keys else ""
        if len(candidate) + len(suffix) > limit and len(kept) > 1:
            del kept[key]
            break

    remaining_keys = total_keys - len(kept)
    output = json.dumps(kept, indent=2, default=str)
    if remaining_keys > 0:
        output += f"\n... and {remaining_keys} more keys"
    return output


def _truncate_lines(result: str, limit: int) -> str:
    """Truncate line-based output, keeping header + first N lines."""
    lines = result.split("\n")
    total_lines = len(lines)

    kept: list[str] = []
    current_len = 0
    for line in lines:
        new_len = current_len + len(line) + 1  # +1 for newline
        suffix = f"\n... ({total_lines - len(kept) - 1} more lines)"
        if new_len + len(suffix) > limit and kept:
            break
        kept.append(line)
        current_len = new_len

    remaining = total_lines - len(kept)
    output = "\n".join(kept)
    if remaining > 0:
        output += f"\n... ({remaining} more lines)"
    return output


def _truncate_head_tail(result: str, limit: int) -> str:
    """Truncate with head (70%) + tail (30%) split."""
    separator = "\n\n... [truncated] ...\n\n"
    available = limit - len(separator)
    if available <= 0:
        return result[:limit]

    head_size = int(available * HEAD_RATIO)
    tail_size = available - head_size

    head = result[:head_size]
    tail = result[-tail_size:] if tail_size > 0 else ""

    return head + separator + tail


def _extract_error_portion(result: str) -> str | None:
    """Extract error-related content from a result string.

    Finds the earliest error marker and returns everything from that point.
    Returns None if no error marker is found.
    """
    lower = result.lower()
    earliest_idx = len(result)

    for marker in [
        "traceback (most recent call last):",
        "traceback",
        "error:",
        "exception:",
        "failed:",
    ]:
        idx = lower.find(marker)
        if idx != -1 and idx < earliest_idx:
            earliest_idx = idx

    if earliest_idx < len(result):
        return result[earliest_idx:]

    return None
