"""Agent execution models -- action recording and idempotency rules.

Provides data models used by the runtime inspector (``kora_v2.runtime.inspector``)
and future replay/recovery infrastructure to track side-effecting actions
and determine whether they can be safely replayed.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class SideEffectLevel(StrEnum):
    """Classifies how an action affects the outside world."""

    NONE = "none"
    LOCAL = "local"
    EXTERNAL = "external"
    DESTRUCTIVE = "destructive"


class ActionRecord(BaseModel):
    """Immutable record of a single tool invocation within a session turn.

    Used by the runtime to track what happened, detect duplicates,
    and decide whether a replay is safe after a crash/restart.
    """

    action_id: str
    tool_name: str
    input_hash: str
    output_hash: str | None = None
    side_effect_level: SideEffectLevel
    idempotent: bool
    timestamp: datetime
    session_id: str
    turn_number: int


class IdempotencyRule(BaseModel):
    """Policy for replaying actions at a given side-effect level."""

    side_effect_level: SideEffectLevel
    safe_to_replay: bool
    requires_reauth: bool = False
    description: str = ""


IDEMPOTENCY_RULES: dict[SideEffectLevel, IdempotencyRule] = {
    SideEffectLevel.NONE: IdempotencyRule(
        side_effect_level=SideEffectLevel.NONE,
        safe_to_replay=True,
        description="Pure read -- safe to replay",
    ),
    SideEffectLevel.LOCAL: IdempotencyRule(
        side_effect_level=SideEffectLevel.LOCAL,
        safe_to_replay=False,
        description="Local writes -- logged but not auto-replayed",
    ),
    SideEffectLevel.EXTERNAL: IdempotencyRule(
        side_effect_level=SideEffectLevel.EXTERNAL,
        safe_to_replay=False,
        description="External calls -- not replayed",
    ),
    SideEffectLevel.DESTRUCTIVE: IdempotencyRule(
        side_effect_level=SideEffectLevel.DESTRUCTIVE,
        safe_to_replay=False,
        requires_reauth=True,
        description="Destructive -- requires re-authorization on replay",
    ),
}
