"""Tests for kora_v2.autonomous.state — Phase 6 state models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kora_v2.autonomous.state import (
    AutonomousCheckpoint,
    AutonomousState,
    AutonomousStepState,
)

# ── Helpers ───────────────────────────────────────────────────────────────


def _make_state(**kwargs) -> AutonomousState:
    defaults = dict(
        session_id="sess-1",
        plan_id="plan-1",
        status="idle",
        started_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    return AutonomousState(**defaults)


def _make_checkpoint(state: AutonomousState | None = None) -> AutonomousCheckpoint:
    st = state or _make_state()
    return AutonomousCheckpoint(
        checkpoint_id="ckpt-1",
        session_id=st.session_id,
        plan_id=st.plan_id,
        mode="task",
        state=st,
        resume_token="tok-abc",
        elapsed_seconds=120,
        request_count=5,
        token_estimate=1000,
        cost_estimate=0.01,
    )


# ── AutonomousState ───────────────────────────────────────────────────────


class TestAutonomousState:
    def test_defaults(self):
        state = _make_state()
        assert state.mode == "task"
        assert state.status == "idle"
        assert state.current_step_index == 0
        assert state.completed_step_ids == []
        assert state.pending_step_ids == []
        assert state.produced_artifact_ids == []
        assert state.granted_tools == []
        assert state.decision_queue == []
        assert state.overlap_score == 0.0
        assert state.interruption_pending is False
        assert state.iteration_count == 0
        assert state.request_count == 0
        assert state.token_estimate == 0
        assert state.cost_estimate == 0.0

    def test_all_statuses_valid(self):
        valid_statuses = [
            "idle", "planned", "executing", "waiting_on_user",
            "checkpointing", "reflecting", "replanning",
            "paused_for_overlap", "reviewing", "completed",
            "cancelled", "failed",
        ]
        for s in valid_statuses:
            state = _make_state(status=s)
            assert state.status == s

    def test_invalid_status_raises(self):
        with pytest.raises(Exception):
            _make_state(status="unknown_status")

    def test_mode_task_and_routine(self):
        t = _make_state(mode="task")
        r = _make_state(mode="routine")
        assert t.mode == "task"
        assert r.mode == "routine"

    def test_invalid_mode_raises(self):
        with pytest.raises(Exception):
            _make_state(mode="batch")

    def test_counters_are_mutable(self):
        state = _make_state()
        state.request_count = 10
        state.token_estimate = 5000
        state.cost_estimate = 0.25
        assert state.request_count == 10
        assert state.token_estimate == 5000

    def test_optional_fields_default_none(self):
        state = _make_state()
        assert state.root_item_id is None
        assert state.current_step_id is None
        assert state.checkpoint_due_at is None
        assert state.last_checkpoint_at is None
        assert state.latest_reflection is None
        assert state.safe_resume_token is None

    def test_metadata_and_quality_summary_default_empty(self):
        state = _make_state()
        assert state.metadata == {}
        assert state.quality_summary == {}

    def test_started_at_is_set_automatically(self):
        before = datetime.now(UTC)
        state = _make_state()
        after = datetime.now(UTC)
        assert before <= state.started_at <= after


# ── AutonomousStepState ───────────────────────────────────────────────────


class TestAutonomousStepState:
    def test_defaults(self):
        step = AutonomousStepState(
            id="step-1",
            title="Research phase",
            description="Gather sources",
        )
        assert step.status == "planned"
        assert step.worker == ""
        assert step.started_at is None
        assert step.completed_at is None
        assert step.artifacts == []
        assert step.error is None

    def test_all_step_statuses_valid(self):
        valid = ["planned", "dispatched", "waiting_on_user", "blocked", "accepted", "dropped"]
        for s in valid:
            step = AutonomousStepState(id="s", title="t", description="d", status=s)
            assert step.status == s

    def test_invalid_step_status_raises(self):
        with pytest.raises(Exception):
            AutonomousStepState(id="s", title="t", description="d", status="running")


# ── AutonomousCheckpoint ──────────────────────────────────────────────────


class TestAutonomousCheckpoint:
    def test_basic_construction(self):
        cp = _make_checkpoint()
        assert cp.checkpoint_id == "ckpt-1"
        assert cp.session_id == "sess-1"
        assert cp.plan_id == "plan-1"
        assert cp.mode == "task"
        assert cp.resume_token == "tok-abc"
        assert cp.elapsed_seconds == 120
        assert cp.reason == "periodic"

    def test_reason_field_values(self):
        for reason in ["periodic", "overlap", "budget", "replan", "termination"]:
            cp = _make_checkpoint()
            cp_with_reason = cp.model_copy(update={"reason": reason})
            assert cp_with_reason.reason == reason

    def test_created_at_auto_set(self):
        before = datetime.now(UTC)
        cp = _make_checkpoint()
        after = datetime.now(UTC)
        assert before <= cp.created_at <= after

    def test_state_is_embedded(self):
        state = _make_state(session_id="sess-xyz", plan_id="plan-xyz")
        cp = _make_checkpoint(state=state)
        assert cp.state.session_id == "sess-xyz"
        assert cp.state.plan_id == "plan-xyz"

    def test_json_roundtrip(self):
        cp = _make_checkpoint()
        serialised = cp.model_dump_json()
        restored = AutonomousCheckpoint.model_validate_json(serialised)
        assert restored.checkpoint_id == cp.checkpoint_id
        assert restored.session_id == cp.session_id
        assert restored.state.status == cp.state.status
        assert restored.elapsed_seconds == cp.elapsed_seconds
