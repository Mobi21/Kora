"""Tests for workspace policy matrix (Task 6 — Phase 9 Tooling)."""
from __future__ import annotations

import pytest

from kora_v2.capabilities.policy import (
    ApprovalMode,
    PolicyKey,
    SessionState,
    TaskState,
)
from kora_v2.capabilities.workspace.policy import build_default_policy

# ── Helpers ───────────────────────────────────────────────────────────────────

def _key(action: str, account: str = "personal") -> PolicyKey:
    return PolicyKey(capability="workspace", action=action, account=account)


def _session() -> SessionState:
    return SessionState(session_id="test-session")


def _task() -> TaskState:
    return TaskState(task_id="test-task")


# ── 1. Default policy DENIES gmail.send for personal ─────────────────────────

def test_gmail_send_denied_for_personal() -> None:
    policy = build_default_policy(account="personal")
    decision = policy.evaluate(_key("gmail.send", "personal"), _session(), _task())
    assert not decision.allowed
    assert decision.mode == ApprovalMode.DENY


# ── 2. Default policy DENIES gmail.draft for personal ────────────────────────

def test_gmail_draft_denied_for_personal() -> None:
    policy = build_default_policy(account="personal")
    decision = policy.evaluate(_key("gmail.draft", "personal"), _session(), _task())
    assert not decision.allowed
    assert decision.mode == ApprovalMode.DENY


# ── 3. calendar.create_event = FIRST_PER_TASK ────────────────────────────────

def test_calendar_create_event_first_per_task() -> None:
    policy = build_default_policy(account="personal")
    decision = policy.evaluate(_key("calendar.create_event"), _session(), _task())
    assert decision.allowed
    assert decision.mode == ApprovalMode.FIRST_PER_TASK
    assert decision.requires_prompt  # not yet granted


def test_calendar_create_event_no_prompt_after_task_grant() -> None:
    policy = build_default_policy(account="personal")
    task = _task()
    key = _key("calendar.create_event")
    # First call — should require prompt
    d1 = policy.evaluate(key, _session(), task)
    assert d1.requires_prompt
    # Simulate grant
    task.granted_this_task.add(key.serialize())
    # Second call — should NOT require prompt
    d2 = policy.evaluate(key, _session(), task)
    assert not d2.requires_prompt


# ── 4. calendar.delete_event = ALWAYS_ASK ────────────────────────────────────

def test_calendar_delete_event_always_ask() -> None:
    policy = build_default_policy(account="personal")
    task = _task()
    key = _key("calendar.delete_event")
    d1 = policy.evaluate(key, _session(), task)
    assert decision_mode_is(d1, ApprovalMode.ALWAYS_ASK)
    assert d1.allowed
    assert d1.requires_prompt
    # Even after grant, ALWAYS_ASK still prompts
    task.granted_this_task.add(key.serialize())
    d2 = policy.evaluate(key, _session(), task)
    assert d2.requires_prompt


def decision_mode_is(decision, mode):
    return decision.mode == mode


# ── 5. Reads are NEVER_ASK ───────────────────────────────────────────────────

@pytest.mark.parametrize("action", [
    "gmail.search",
    "gmail.get_message",
    "calendar.list",
    "calendar.get_event",
    "drive.search",
    "drive.get_file",
    "docs.read",
    "tasks.list",
])
def test_reads_are_never_ask(action: str) -> None:
    policy = build_default_policy(account="personal")
    decision = policy.evaluate(_key(action), _session(), _task())
    assert decision.allowed
    assert decision.mode == ApprovalMode.NEVER_ASK
    assert not decision.requires_prompt


# ── 6. read_only=True converts all writes to DENY ────────────────────────────

@pytest.mark.parametrize("action", [
    "calendar.create_event",
    "calendar.update_event",
    "calendar.delete_event",
    "drive.upload",
    "docs.create",
    "docs.update",
    "tasks.create",
])
def test_read_only_denies_all_writes(action: str) -> None:
    policy = build_default_policy(account="personal", read_only=True)
    decision = policy.evaluate(_key(action), _session(), _task())
    assert not decision.allowed
    assert decision.mode == ApprovalMode.DENY


def test_read_only_still_allows_reads() -> None:
    """read_only should not affect reads — they remain NEVER_ASK."""
    policy = build_default_policy(account="personal", read_only=True)
    for action in ("gmail.search", "calendar.list", "drive.get_file", "docs.read"):
        decision = policy.evaluate(_key(action), _session(), _task())
        assert decision.allowed, f"{action} should be allowed in read_only mode"
        assert decision.mode == ApprovalMode.NEVER_ASK
