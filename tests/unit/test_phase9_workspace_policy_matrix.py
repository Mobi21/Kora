"""Phase 9 workspace policy matrix regression tests.

Independent regression guard on top of Task 6's tests.
Iterates every default workspace action name and asserts the exact
(allowed, requires_prompt, mode) tuple per the spec.

Covers all 17 actions in _ACTION_METADATA.
"""
from __future__ import annotations

import pytest

from kora_v2.capabilities.policy import (
    ApprovalMode,
    PolicyKey,
    SessionState,
    TaskState,
)
from kora_v2.capabilities.workspace.policy import build_default_policy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _session() -> SessionState:
    return SessionState(session_id="reg-session")


def _task() -> TaskState:
    return TaskState(task_id="reg-task")


def _key(action: str, account: str = "personal") -> PolicyKey:
    return PolicyKey(capability="workspace", action=action, account=account)


def _policy(account: str = "personal", read_only: bool = False):
    return build_default_policy(account=account, read_only=read_only)


# ---------------------------------------------------------------------------
# Spec matrix: (action, allowed, requires_prompt, mode)
# ---------------------------------------------------------------------------

_POLICY_MATRIX = [
    # ── Reads (NEVER_ASK) ───────────────────────────────────────────────────
    ("gmail.search",          True,  False, ApprovalMode.NEVER_ASK),
    ("gmail.get_message",     True,  False, ApprovalMode.NEVER_ASK),
    ("calendar.list",         True,  False, ApprovalMode.NEVER_ASK),
    ("calendar.get_event",    True,  False, ApprovalMode.NEVER_ASK),
    ("drive.search",          True,  False, ApprovalMode.NEVER_ASK),
    ("drive.get_file",        True,  False, ApprovalMode.NEVER_ASK),
    ("docs.read",             True,  False, ApprovalMode.NEVER_ASK),
    ("tasks.list",            True,  False, ApprovalMode.NEVER_ASK),
    # ── Writes (FIRST_PER_TASK) ─────────────────────────────────────────────
    ("calendar.create_event", True,  True,  ApprovalMode.FIRST_PER_TASK),
    ("calendar.update_event", True,  True,  ApprovalMode.FIRST_PER_TASK),
    ("drive.upload",          True,  True,  ApprovalMode.FIRST_PER_TASK),
    ("docs.create",           True,  True,  ApprovalMode.FIRST_PER_TASK),
    ("docs.update",           True,  True,  ApprovalMode.FIRST_PER_TASK),
    ("tasks.create",          True,  True,  ApprovalMode.FIRST_PER_TASK),
    # ── Deletes (ALWAYS_ASK) ────────────────────────────────────────────────
    ("calendar.delete_event", True,  True,  ApprovalMode.ALWAYS_ASK),
    # ── Personal Gmail overrides (DENY) ─────────────────────────────────────
    ("gmail.send",            False, False, ApprovalMode.DENY),
    ("gmail.draft",           False, False, ApprovalMode.DENY),
]


@pytest.mark.parametrize(
    "action,expected_allowed,expected_requires_prompt,expected_mode",
    _POLICY_MATRIX,
    ids=[row[0] for row in _POLICY_MATRIX],
)
def test_default_policy_matrix(
    action: str,
    expected_allowed: bool,
    expected_requires_prompt: bool,
    expected_mode: ApprovalMode,
) -> None:
    """Each action must evaluate to the specified (allowed, requires_prompt, mode)."""
    policy = _policy()
    key = _key(action, account="personal")
    decision = policy.evaluate(key, session=_session(), task=_task())

    assert decision.allowed == expected_allowed, (
        f"{action}: expected allowed={expected_allowed}, got {decision.allowed}"
    )
    assert decision.mode == expected_mode, (
        f"{action}: expected mode={expected_mode!r}, got {decision.mode!r}"
    )
    # requires_prompt check: only matters when allowed
    if expected_allowed:
        assert decision.requires_prompt == expected_requires_prompt, (
            f"{action}: expected requires_prompt={expected_requires_prompt}, "
            f"got {decision.requires_prompt}"
        )


# ---------------------------------------------------------------------------
# Additional: read_only=True makes all writes DENY
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", [
    "calendar.create_event",
    "calendar.update_event",
    "calendar.delete_event",
    "drive.upload",
    "docs.create",
    "docs.update",
    "tasks.create",
    "gmail.send",
    "gmail.draft",
])
def test_read_only_policy_denies_all_writes(action: str) -> None:
    policy = _policy(read_only=True)
    key = _key(action)
    decision = policy.evaluate(key, session=_session(), task=_task())
    assert not decision.allowed, (
        f"read_only policy should deny {action}"
    )
    assert decision.mode == ApprovalMode.DENY


# ---------------------------------------------------------------------------
# FIRST_PER_TASK: no prompt after task grant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", [
    "calendar.create_event",
    "calendar.update_event",
    "drive.upload",
    "docs.create",
    "docs.update",
    "tasks.create",
])
def test_first_per_task_no_prompt_after_grant(action: str) -> None:
    policy = _policy()
    key = _key(action)
    task = _task()
    # First evaluation: requires_prompt
    d1 = policy.evaluate(key, session=_session(), task=task)
    assert d1.requires_prompt, f"{action}: should require prompt on first call"
    # Grant it
    task.granted_this_task.add(key.serialize())
    # Second evaluation: no longer requires_prompt
    d2 = policy.evaluate(key, session=_session(), task=task)
    assert not d2.requires_prompt, f"{action}: should NOT require prompt after grant"


# ---------------------------------------------------------------------------
# ALWAYS_ASK: still prompts even after task grant
# ---------------------------------------------------------------------------


def test_calendar_delete_always_asks_even_after_grant() -> None:
    policy = _policy()
    key = _key("calendar.delete_event")
    task = _task()
    d1 = policy.evaluate(key, session=_session(), task=task)
    assert d1.requires_prompt
    task.granted_this_task.add(key.serialize())
    d2 = policy.evaluate(key, session=_session(), task=task)
    assert d2.requires_prompt, "ALWAYS_ASK should still prompt after task grant"
