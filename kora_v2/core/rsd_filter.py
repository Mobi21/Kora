"""Rejection-sensitive dysphoria (RSD) output filter.

Standalone utility any component can call. Not tied to the ADHD module —
it consumes ``OutputRule`` lists from any neurodivergent module that
provides them.

Phase 5 runs as a detection-only pass (callers decide what to do with
violations). Phase 8 will introduce automatic rewrite via a cheap LLM
pass before delivery.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from kora_v2.adhd.protocol import OutputRule


class RSDFilterResult(BaseModel):
    """Result of running ``check_output`` against a text blob."""

    passed: bool
    violations: list[dict[str, Any]] = Field(default_factory=list)
    rewritten: str | None = None


async def check_output(
    text: str, rules: list[OutputRule]
) -> RSDFilterResult:
    """Check ``text`` against each rule's compiled regex.

    Returns a ``RSDFilterResult`` with ``passed=False`` if any rule
    matches. ``violations`` carries the rule name, matched substring,
    position, and replacement guidance so callers can log or surface
    them. ``rewritten`` is always ``None`` in Phase 5 — automatic
    rewrites land in Phase 8 with the Memory Steward.
    """
    if not text or not rules:
        return RSDFilterResult(passed=True)

    violations: list[dict[str, Any]] = []
    for rule in rules:
        try:
            pattern = re.compile(rule.pattern, re.IGNORECASE)
        except re.error:
            continue
        for match in pattern.finditer(text):
            violations.append(
                {
                    "rule": rule.name,
                    "match": match.group(),
                    "position": match.start(),
                    "suggestion": rule.replacement_guidance,
                }
            )

    return RSDFilterResult(
        passed=len(violations) == 0,
        violations=violations,
        rewritten=None,
    )


__all__ = ["RSDFilterResult", "check_output"]
