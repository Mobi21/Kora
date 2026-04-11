"""Vault action implementations — thin wrappers over MirrorTarget."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from kora_v2.capabilities.base import StructuredFailure
from kora_v2.capabilities.policy import (
    PolicyKey,
    PolicyMatrix,
    SessionState,
    TaskState,
)
from kora_v2.capabilities.vault.config import VaultCapabilityConfig
from kora_v2.capabilities.vault.mirror import MirrorTarget, WriteResult

log = structlog.get_logger(__name__)

_CAP = "vault"


@dataclass
class VaultActionContext:
    config: VaultCapabilityConfig
    policy: PolicyMatrix
    target: MirrorTarget
    session: SessionState
    task: TaskState | None = None


def _evaluate(ctx: VaultActionContext, action: str) -> StructuredFailure | None:
    """Evaluate the policy matrix for the given action.

    Vault actions are NEVER_ASK, so this should always pass.
    Returns a StructuredFailure only if the policy returns DENY.
    """
    key = PolicyKey(capability=_CAP, action=action)
    decision = ctx.policy.evaluate(key, session=ctx.session, task=ctx.task)
    if not decision.allowed:
        log.debug("vault.action.policy_denied", action=action, reason=decision.reason)
        return StructuredFailure(
            capability=_CAP,
            action=action,
            path=f"vault.policy.{action}",
            reason="policy_denied",
            user_message=f"Vault action '{action}' is denied by policy: {decision.reason}",
            recoverable=False,
            machine_details={"policy_mode": decision.mode, "reason": decision.reason},
        )
    return None


async def vault_write_note(
    ctx: VaultActionContext,
    relative_path: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    *,
    approved: bool = False,
) -> WriteResult:
    """Write a note to the vault mirror under the notes subdirectory."""
    denial = _evaluate(ctx, "vault.write_note")
    if denial is not None:
        return WriteResult(success=False, path=None, failure=denial)

    log.debug("vault.write_note", path=relative_path)
    return await ctx.target.write_note(relative_path, content, metadata)


async def vault_write_clip(
    ctx: VaultActionContext,
    *,
    source_url: str,
    title: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    approved: bool = False,
) -> WriteResult:
    """Write a clip to the vault mirror under the clips subdirectory."""
    denial = _evaluate(ctx, "vault.write_clip")
    if denial is not None:
        return WriteResult(success=False, path=None, failure=denial)

    log.debug("vault.write_clip", url=source_url, title=title)
    return await ctx.target.write_clip(
        source_url=source_url,
        title=title,
        content=content,
        metadata=metadata,
    )


async def vault_read_note(
    ctx: VaultActionContext,
    relative_path: str,
    *,
    approved: bool = False,
) -> WriteResult:
    """Read a note from the vault mirror notes subdirectory."""
    denial = _evaluate(ctx, "vault.read_note")
    if denial is not None:
        return WriteResult(success=False, path=None, failure=denial)

    log.debug("vault.read_note", path=relative_path)
    return await ctx.target.read_note(relative_path)
