"""WorkerTask + preset unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

from kora_v2.runtime.orchestration.system_state import SystemStatePhase
from kora_v2.runtime.orchestration.worker_task import (
    BOUNDED_BACKGROUND,
    IN_TURN,
    LONG_BACKGROUND,
    PAUSED_STATES,
    PRESETS,
    TERMINAL_STATES,
    RequestClass,
    StepResult,
    WorkerTask,
    WorkerTaskState,
    get_preset,
)


def _make_task(preset: str = "bounded_background") -> WorkerTask:
    config = get_preset(preset)  # type: ignore[arg-type]
    return WorkerTask(
        id=f"task-{preset}",
        pipeline_instance_id=None,
        stage_name="stage",
        config=config,
        goal="goal",
        system_prompt="prompt",
        created_at=datetime.now(UTC),
    )


def test_presets_cover_all_three_profiles() -> None:
    assert set(PRESETS.keys()) == {"in_turn", "bounded_background", "long_background"}
    assert IN_TURN.preset == "in_turn"
    assert BOUNDED_BACKGROUND.preset == "bounded_background"
    assert LONG_BACKGROUND.preset == "long_background"


def test_in_turn_allowed_states_are_conversation_only() -> None:
    assert IN_TURN.allowed_states == frozenset({SystemStatePhase.CONVERSATION})
    assert IN_TURN.blocks_parent is True
    assert IN_TURN.request_class is RequestClass.CONVERSATION


def test_bounded_background_budgets() -> None:
    assert BOUNDED_BACKGROUND.max_duration_seconds == 1800
    assert BOUNDED_BACKGROUND.checkpoint_every_seconds == 300
    assert BOUNDED_BACKGROUND.max_requests_per_hour == 20
    assert BOUNDED_BACKGROUND.max_requests == 60
    assert BOUNDED_BACKGROUND.max_cost == 0.25
    assert BOUNDED_BACKGROUND.request_class is RequestClass.BACKGROUND


def test_long_background_allows_all_idle_phases() -> None:
    assert SystemStatePhase.LIGHT_IDLE in LONG_BACKGROUND.allowed_states
    assert SystemStatePhase.DEEP_IDLE in LONG_BACKGROUND.allowed_states
    assert SystemStatePhase.WAKE_UP_WINDOW in LONG_BACKGROUND.allowed_states
    assert LONG_BACKGROUND.pause_on_conversation is True


def test_get_preset_returns_fresh_copy() -> None:
    a = get_preset("bounded_background")
    b = get_preset("bounded_background")
    assert a is not b
    a.tool_scope.append("mutated")
    assert "mutated" not in b.tool_scope


def test_terminal_and_paused_sets() -> None:
    assert WorkerTaskState.COMPLETED in TERMINAL_STATES
    assert WorkerTaskState.FAILED in TERMINAL_STATES
    assert WorkerTaskState.CANCELLED in TERMINAL_STATES
    assert WorkerTaskState.PAUSED_FOR_STATE in PAUSED_STATES
    assert WorkerTaskState.PAUSED_FOR_RATE_LIMIT in PAUSED_STATES
    assert WorkerTaskState.PAUSED_FOR_DECISION in PAUSED_STATES
    assert WorkerTaskState.PAUSED_FOR_DEPENDENCY in PAUSED_STATES


def test_worker_task_helpers() -> None:
    task = _make_task()
    assert not task.is_terminal()
    assert not task.is_paused()
    task.state = WorkerTaskState.COMPLETED
    assert task.is_terminal()
    task.state = WorkerTaskState.PAUSED_FOR_STATE
    assert task.is_paused()


def test_apply_step_result_accumulates() -> None:
    task = _make_task()
    task.apply_step_result(
        StepResult(outcome="continue", request_count_delta=3, agent_turn_count_delta=1)
    )
    task.apply_step_result(
        StepResult(outcome="continue", request_count_delta=2, agent_turn_count_delta=2)
    )
    assert task.request_count == 5
    assert task.agent_turn_count == 3


def test_request_cancellation_respects_can_be_cancelled() -> None:
    task = _make_task()
    task.request_cancellation()
    assert task.cancellation_requested is True
