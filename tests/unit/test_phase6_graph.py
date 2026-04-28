"""Tests for kora_v2/autonomous/graph.py — Phase 6A node functions."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kora_v2.autonomous.graph import (
    TERMINAL_STATUSES,
    classify_request,
    complete,
    decision_request,
    execute_step,
    failed,
    paused_for_overlap,
    persist_plan,
    reflect,
    route_next_node,
)
from kora_v2.autonomous.state import AutonomousState
from kora_v2.core.exceptions import LLMTimeoutError

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _make_state(**kwargs) -> AutonomousState:
    """Construct a minimal AutonomousState for testing."""
    defaults = dict(
        session_id="test-session",
        plan_id="test-plan",
        status="planned",
        started_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    return AutonomousState(**defaults)


class _TimeoutExecutor:
    async def execute(self, _input_data):
        raise LLMTimeoutError("MiniMax request timed out after 120s")


class _ContainerWithExecutor:
    def resolve_worker(self, name: str):
        assert name == "executor"
        return _TimeoutExecutor()


# ─────────────────────────────────────────────────────────────────────────────
# classify_request
# ─────────────────────────────────────────────────────────────────────────────


class TestClassifyRequest:
    def test_returns_autonomous_state(self):
        state = classify_request("Research cloud storage options", "sess1")
        assert isinstance(state, AutonomousState)

    def test_mode_task_for_general_goal(self):
        state = classify_request("Analyze the quarterly sales data", "sess1")
        assert state.mode == "task"

    def test_mode_routine_for_routine_keywords(self):
        state = classify_request("Walk me through my morning routine", "sess1")
        assert state.mode == "routine"

    def test_mode_routine_for_habit_keyword(self):
        state = classify_request("Set up a daily habit tracker", "sess1")
        assert state.mode == "routine"

    def test_initial_status_is_planned(self):
        state = classify_request("Do something", "sess1")
        assert state.status == "planned"

    def test_goal_stored_in_metadata(self):
        goal = "Write a Python script to parse CSV files"
        state = classify_request(goal, "sess1")
        assert state.metadata["goal"] == goal

    def test_session_id_set(self):
        state = classify_request("Test goal", "my-session")
        assert state.session_id == "my-session"

    def test_plan_id_is_uuid_format(self):
        state = classify_request("Test goal", "sess1")
        assert len(state.plan_id) == 36  # UUID4 with hyphens

    def test_unique_plan_ids(self):
        s1 = classify_request("goal", "sess1")
        s2 = classify_request("goal", "sess1")
        assert s1.plan_id != s2.plan_id


# ─────────────────────────────────────────────────────────────────────────────
# reflect
# ─────────────────────────────────────────────────────────────────────────────


class TestReflect:
    def test_complete_when_no_pending_steps(self):
        state = _make_state(
            status="checkpointing",
            pending_step_ids=[],
            completed_step_ids=["s1"],
        )
        updated, action = reflect(state)
        assert action == "complete"
        assert "completed" in updated.latest_reflection.lower()

    def test_pause_for_overlap_when_score_high(self):
        state = _make_state(
            status="checkpointing",
            pending_step_ids=["s2"],
            overlap_score=0.75,
        )
        updated, action = reflect(state)
        assert action == "paused_for_overlap"

    def test_decision_request_when_queue_not_empty(self):
        state = _make_state(
            status="checkpointing",
            pending_step_ids=["s2"],
            decision_queue=["dec-123"],
        )
        updated, action = reflect(state)
        assert action == "decision_request"

    def test_replan_when_confidence_low(self):
        state = _make_state(
            status="checkpointing",
            pending_step_ids=["s2"],
            completed_step_ids=["s1"],
            quality_summary={"s1": {"confidence": 0.2, "label": "low"}},
        )
        updated, action = reflect(state)
        assert action == "replan"

    def test_continue_on_good_quality(self):
        state = _make_state(
            status="checkpointing",
            pending_step_ids=["s2"],
            quality_summary={"s1": {"confidence": 0.85, "label": "high"}},
        )
        updated, action = reflect(state)
        assert action == "continue"

    def test_latest_reflection_set(self):
        state = _make_state(status="checkpointing", pending_step_ids=["s2"])
        updated, _ = reflect(state)
        assert updated.latest_reflection is not None
        assert len(updated.latest_reflection) > 0

    def test_overlap_threshold_exactly_070(self):
        state = _make_state(
            status="checkpointing",
            pending_step_ids=["s2"],
            overlap_score=0.70,
        )
        _, action = reflect(state)
        assert action == "paused_for_overlap"

    def test_overlap_below_threshold_continues(self):
        state = _make_state(
            status="checkpointing",
            pending_step_ids=["s2"],
            overlap_score=0.69,
        )
        _, action = reflect(state)
        assert action == "continue"

    def test_no_mutation_of_input_state(self):
        state = _make_state(status="checkpointing", pending_step_ids=["s2"])
        original_status = state.status
        reflect(state)
        assert state.status == original_status


@pytest.mark.asyncio
async def test_execute_step_checkpoints_and_retries_transient_minimax_timeout():
    state = _make_state(
        status="planned",
        pending_step_ids=["s1"],
        metadata={
            "goal": "Research a topic",
            "steps": {
                "s1": {
                    "id": "s1",
                    "title": "Research",
                    "description": "Research the topic",
                    "worker": "executor",
                }
            },
        },
    )

    updated = await execute_step(state, _ContainerWithExecutor())

    assert updated.status == "checkpointing"
    assert updated.pending_step_ids == ["s1"]
    assert updated.completed_step_ids == []
    assert updated.metadata["step_retry_counts"]["s1"] == 1
    assert updated.metadata["retry_pending_step_id"] == "s1"
    assert "MiniMax request timed out" in updated.metadata["last_transient_error"]
    assert updated.safe_resume_token


@pytest.mark.asyncio
async def test_execute_step_fails_after_transient_timeout_retry_limit():
    state = _make_state(
        status="planned",
        pending_step_ids=["s1"],
        metadata={
            "goal": "Research a topic",
            "step_retry_counts": {"s1": 3},
            "steps": {
                "s1": {
                    "id": "s1",
                    "title": "Research",
                    "description": "Research the topic",
                    "worker": "executor",
                }
            },
        },
    )

    updated = await execute_step(state, _ContainerWithExecutor())

    assert updated.status == "failed"
    assert updated.metadata["failed_step_id"] == "s1"
    assert updated.metadata["step_retry_counts"]["s1"] == 4
    assert "Step execution failed" in updated.metadata["failure_reason"]


# ─────────────────────────────────────────────────────────────────────────────
# decision_request
# ─────────────────────────────────────────────────────────────────────────────


class TestDecisionRequest:
    def test_status_set_to_waiting_on_user(self):
        from kora_v2.autonomous.decisions import DecisionManager

        state = _make_state()
        dm = DecisionManager()
        updated, decision = decision_request(
            state, dm, options=["proceed", "skip"]
        )
        assert updated.status == "waiting_on_user"

    def test_decision_id_added_to_queue(self):
        from kora_v2.autonomous.decisions import DecisionManager

        state = _make_state()
        dm = DecisionManager()
        updated, decision = decision_request(state, dm, options=["yes", "no"])
        assert decision.decision_id in updated.decision_queue

    def test_never_auto_policy_passed_through(self):
        from kora_v2.autonomous.decisions import DecisionManager

        state = _make_state()
        dm = DecisionManager()
        _, decision = decision_request(
            state, dm, options=["yes", "no"], policy="never_auto"
        )
        assert decision.policy == "never_auto"

    def test_recommendation_passed_through(self):
        from kora_v2.autonomous.decisions import DecisionManager

        state = _make_state()
        dm = DecisionManager()
        _, decision = decision_request(
            state, dm, options=["yes", "no"], recommendation="yes"
        )
        assert decision.recommendation == "yes"


# ─────────────────────────────────────────────────────────────────────────────
# paused_for_overlap
# ─────────────────────────────────────────────────────────────────────────────


class TestPausedForOverlap:
    def test_status_set(self):
        state = _make_state(status="executing")
        updated = paused_for_overlap(state)
        assert updated.status == "paused_for_overlap"

    def test_input_not_mutated(self):
        state = _make_state(status="executing")
        paused_for_overlap(state)
        assert state.status == "executing"


# ─────────────────────────────────────────────────────────────────────────────
# route_next_node
# ─────────────────────────────────────────────────────────────────────────────


class TestRouteNextNode:
    def test_terminal_returns_end(self):
        for status in ["completed", "cancelled", "failed"]:
            state = _make_state(status=status)
            assert route_next_node(state) == "END"

    def test_planned_no_steps_routes_to_plan(self):
        state = _make_state(status="planned", pending_step_ids=[])
        assert route_next_node(state) == "plan"

    def test_planned_steps_no_root_routes_to_persist(self):
        state = _make_state(
            status="planned",
            pending_step_ids=["s1"],
            root_item_id=None,
        )
        assert route_next_node(state) == "persist_plan"

    def test_planned_steps_with_root_routes_to_execute(self):
        state = _make_state(
            status="planned",
            pending_step_ids=["s1"],
            root_item_id="root-123",
        )
        assert route_next_node(state) == "execute_step"

    def test_executing_routes_to_review(self):
        state = _make_state(status="executing")
        assert route_next_node(state) == "review_step"

    def test_reviewing_with_pending_routes_to_checkpoint(self):
        state = _make_state(status="reviewing", pending_step_ids=["s2"])
        assert route_next_node(state) == "checkpoint"

    def test_reviewing_no_pending_routes_to_checkpoint(self):
        # "reviewing" always routes through checkpoint so reflect() can run
        # quality checks and decide whether to complete or replan.
        state = _make_state(status="reviewing", pending_step_ids=[])
        assert route_next_node(state) == "checkpoint"

    def test_checkpointing_routes_to_reflect(self):
        state = _make_state(status="checkpointing")
        assert route_next_node(state) == "reflect"

    def test_waiting_on_user_stays(self):
        state = _make_state(status="waiting_on_user")
        assert route_next_node(state) == "waiting_on_user"


# ─────────────────────────────────────────────────────────────────────────────
# complete / failed (DB paths mocked)
# ─────────────────────────────────────────────────────────────────────────────


class TestComplete:
    @pytest.mark.asyncio
    async def test_status_set_to_completed(self):
        state = _make_state(
            completed_step_ids=["s1", "s2"],
            pending_step_ids=[],
        )
        updated = await complete(state, db_path=None)
        assert updated.status == "completed"

    @pytest.mark.asyncio
    async def test_completion_summary_in_metadata(self):
        state = _make_state(completed_step_ids=["s1"])
        updated = await complete(state, db_path=None)
        assert "completion_summary" in updated.metadata
        assert updated.metadata["completion_summary"]["steps_completed"] == 1


class TestFailed:
    @pytest.mark.asyncio
    async def test_status_set_to_failed(self):
        state = _make_state()
        updated = await failed(state, "Test error", db_path=None)
        assert updated.status == "failed"

    @pytest.mark.asyncio
    async def test_error_in_metadata(self):
        state = _make_state()
        updated = await failed(state, "Something went wrong", db_path=None)
        assert "Something went wrong" in updated.metadata.get("failure_reason", "")


# ─────────────────────────────────────────────────────────────────────────────
# persist_plan (with real temp DB)
# ─────────────────────────────────────────────────────────────────────────────


class TestPersistPlan:
    @pytest.mark.asyncio
    async def test_root_item_id_set(self, tmp_path):
        from kora_v2.core.db import init_operational_db

        db_path = tmp_path / "operational.db"
        await init_operational_db(db_path)

        state = _make_state(
            metadata={
                "goal": "Test goal",
                "steps": {
                    "step-1": {
                        "title": "Step 1",
                        "description": "Do thing 1",
                        "energy_level": "medium",
                        "estimated_minutes": 5,
                        "tools_needed": [],
                    }
                },
                "plan_confidence": 0.8,
            }
        )
        updated = await persist_plan(state, db_path)
        assert updated.root_item_id is not None

    @pytest.mark.asyncio
    async def test_item_ids_in_metadata(self, tmp_path):
        from kora_v2.core.db import init_operational_db

        db_path = tmp_path / "operational.db"
        await init_operational_db(db_path)

        state = _make_state(
            metadata={
                "goal": "Test goal",
                "steps": {
                    "step-1": {"title": "Step 1", "description": "Desc"},
                    "step-2": {"title": "Step 2", "description": "Desc2"},
                },
                "plan_confidence": 0.7,
            }
        )
        updated = await persist_plan(state, db_path)
        assert "step-1" in updated.metadata["item_ids"]
        assert "step-2" in updated.metadata["item_ids"]


# ─────────────────────────────────────────────────────────────────────────────
# TERMINAL_STATUSES constant
# ─────────────────────────────────────────────────────────────────────────────


class TestTerminalStatuses:
    def test_contains_expected_values(self):
        assert "completed" in TERMINAL_STATUSES
        assert "cancelled" in TERMINAL_STATUSES
        assert "failed" in TERMINAL_STATUSES

    def test_planned_not_terminal(self):
        assert "planned" not in TERMINAL_STATUSES

    def test_executing_not_terminal(self):
        assert "executing" not in TERMINAL_STATUSES
