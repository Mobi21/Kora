"""Workspace policy matrix — which actions are allowed on which accounts."""
from __future__ import annotations

from kora_v2.capabilities.policy import (
    ApprovalMode,
    PolicyKey,
    PolicyMatrix,
    PolicyRule,
)

# Capability name constant
_CAP = "workspace"


def build_default_policy(account: str = "personal", read_only: bool = False) -> PolicyMatrix:
    """Build the Phase 9 default policy matrix for a given account.

    Personal Gmail: send/draft DENIED.
    Personal Calendar: write = FIRST_PER_TASK.
    Calendar delete = ALWAYS_ASK.
    Drive/Docs write = FIRST_PER_TASK.
    Drive/Docs delete = ALWAYS_ASK.
    All reads: NEVER_ASK.

    If read_only=True, all write rules become DENY.
    """
    _write_mode = ApprovalMode.DENY if read_only else ApprovalMode.FIRST_PER_TASK
    _delete_mode = ApprovalMode.DENY if read_only else ApprovalMode.ALWAYS_ASK

    rules: list[PolicyRule] = [
        # ── Reads (NEVER_ASK) ──────────────────────────────────────────────
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="gmail.search"),
            mode=ApprovalMode.NEVER_ASK,
            reason="Reading email search results requires no approval.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="gmail.get_message"),
            mode=ApprovalMode.NEVER_ASK,
            reason="Reading an individual email requires no approval.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="calendar.list"),
            mode=ApprovalMode.NEVER_ASK,
            reason="Listing calendar events requires no approval.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="calendar.get_event"),
            mode=ApprovalMode.NEVER_ASK,
            reason="Getting a calendar event requires no approval.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="drive.search"),
            mode=ApprovalMode.NEVER_ASK,
            reason="Searching Drive files requires no approval.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="drive.get_file"),
            mode=ApprovalMode.NEVER_ASK,
            reason="Reading a Drive file requires no approval.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="docs.read"),
            mode=ApprovalMode.NEVER_ASK,
            reason="Reading a Google Doc requires no approval.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="tasks.list"),
            mode=ApprovalMode.NEVER_ASK,
            reason="Listing tasks requires no approval.",
        ),

        # ── Writes (FIRST_PER_TASK by default, DENY if read_only) ─────────
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="calendar.create_event"),
            mode=_write_mode,
            reason="Creating calendar events needs one approval per task.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="calendar.update_event"),
            mode=_write_mode,
            reason="Updating calendar events needs one approval per task.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="drive.upload"),
            mode=_write_mode,
            reason="Uploading to Drive needs one approval per task.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="docs.create"),
            mode=_write_mode,
            reason="Creating a Doc needs one approval per task.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="docs.update"),
            mode=_write_mode,
            reason="Updating a Doc needs one approval per task.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="tasks.create"),
            mode=_write_mode,
            reason="Creating a task needs one approval per task.",
        ),

        # ── Deletes (ALWAYS_ASK, or DENY if read_only) ────────────────────
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="calendar.delete_event"),
            mode=_delete_mode,
            reason="Deleting calendar events always requires explicit approval.",
        ),

        # ── Personal account Gmail overrides (most-specific: account=personal) ──
        # These override the general read/write rules for this capability+action
        # combination when the account matches "personal".
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="gmail.send", account=account),
            mode=ApprovalMode.DENY,
            reason="Sending email from the personal account is disabled by default policy.",
        ),
        PolicyRule(
            key=PolicyKey(capability=_CAP, action="gmail.draft", account=account),
            mode=ApprovalMode.DENY,
            reason="Creating Gmail drafts from the personal account is disabled by default policy.",
        ),
    ]

    return PolicyMatrix(rules=rules, default=ApprovalMode.ALWAYS_ASK)
