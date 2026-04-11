"""Vault policy matrix — local mirror, no user prompts needed."""
from __future__ import annotations

from kora_v2.capabilities.policy import (
    ApprovalMode,
    PolicyKey,
    PolicyMatrix,
    PolicyRule,
)

_CAP = "vault"


def build_vault_policy() -> PolicyMatrix:
    """Vault reads and writes are NEVER_ASK by default.

    The vault is a local mirror; there's no user-facing prompt
    for each write. Destructive operations aren't exposed here —
    the write functions only create/overwrite files in the
    configured mirror root.
    """
    rules: list[PolicyRule] = [
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="vault.write_note"),
            mode=ApprovalMode.NEVER_ASK,
            reason="Writing to the local vault mirror requires no approval.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="vault.write_clip"),
            mode=ApprovalMode.NEVER_ASK,
            reason="Writing clips to the local vault mirror requires no approval.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="vault.read_note"),
            mode=ApprovalMode.NEVER_ASK,
            reason="Reading from the local vault mirror requires no approval.",
        ),
    ]

    return PolicyMatrix(rules=rules, default=ApprovalMode.NEVER_ASK)
