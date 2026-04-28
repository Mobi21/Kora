from __future__ import annotations

from kora_v2.graph.supervisor import (
    _forced_tool_call_for_turn,
    _forced_tool_calls_for_turn,
)


def test_pause_context_plus_focus_request_starts_focus_block_not_cancel() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-1",
                "state": "running",
                "stage": "dashboard research",
                "goal": "prepare dashboard plan",
            }
        ]
    }

    call = _forced_tool_call_for_turn(
        "ok i'm back. before the pause we were trying to turn my three tracks "
        "into a real plan. what do you remember, and can you start a focus "
        "block for the dashboard deep work now?",
        state,
    )

    assert call is not None
    assert call["name"] == "start_focus_block"
    assert call["arguments"]["label"] == "Dashboard deep work"


def test_positive_meal_mention_logs_meal_before_general_planning() -> None:
    call = _forced_tool_call_for_turn(
        "slept well, feeling focused. took my adderall already. had coffee "
        "and a bagel.",
        {},
    )

    assert call is not None
    assert call["name"] == "log_meal"
    assert call["arguments"]["meal_type"] == "meal"


def test_negative_meal_question_does_not_log_fake_meal() -> None:
    call = _forced_tool_call_for_turn(
        "feeling scattered. did i eat lunch? i don't think i ate lunch.",
        {},
    )

    assert call is None or call["name"] != "log_meal"


def test_future_dinner_reference_does_not_log_fake_meal() -> None:
    call = _forced_tool_call_for_turn(
        "alex asked about dinner but i'll figure that out later.",
        {},
    )

    assert call is None or call["name"] != "log_meal"


def test_create_that_architecture_file_does_not_match_ate() -> None:
    calls = _forced_tool_calls_for_turn(
        "okay i'm done, end the focus session. i'm gonna take my melatonin "
        "3mg and crash soon. did you actually create that architecture file?",
        {},
    )

    assert "log_meal" not in [call["name"] for call in calls]


def test_melatonin_statement_logs_medication() -> None:
    call = _forced_tool_call_for_turn(
        "ok i'm done for today. gonna take my melatonin 3mg and crash.",
        {},
    )

    assert call is not None
    assert call["name"] == "log_medication"
    assert call["arguments"]["medication_name"] == "melatonin"
    assert call["arguments"]["dose"] == "3mg"


def test_standup_reminder_statement_creates_reminder() -> None:
    call = _forced_tool_call_for_turn(
        "note to self: check the API docs tomorrow. remind me about standup "
        "tomorrow morning.",
        {},
    )

    assert call is not None
    assert call["name"] == "create_reminder"
    assert "standup" in call["arguments"]["title"]


def test_generic_pause_context_does_not_cancel_task() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-1",
                "state": "running",
                "stage": "research",
                "goal": "dashboard work",
            }
        ]
    }

    call = _forced_tool_call_for_turn(
        "before the pause we were doing dashboard work. what do you remember?",
        state,
    )

    assert call is not None
    assert call["name"] == "recall"


def test_file_deliverable_review_forces_directory_listing() -> None:
    call = _forced_tool_call_for_turn(
        "give me the weekly review now with actual deliverables for the "
        "dashboard and the files we created.",
        {},
    )

    assert call is not None
    assert call["name"] == "list_directory"
    assert call["arguments"]["path"] == "/tmp/focus-dashboard"


def test_explicit_file_path_listing_uses_parent_directory() -> None:
    call = _forced_tool_call_for_turn(
        "what files updated around /tmp/focus-dashboard/README.md?",
        {},
    )

    assert call is not None
    assert call["name"] == "list_directory"
    assert call["arguments"]["path"] == "/tmp/focus-dashboard"


def test_explicit_pause_that_task_cancels_nonterminal_task() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-1",
                "state": "running",
                "stage": "dashboard research",
                "goal": "prepare dashboard plan",
            }
        ]
    }

    call = _forced_tool_call_for_turn(
        "pause that background research task for now",
        state,
    )

    assert call is not None
    assert call["name"] == "cancel_task"
    assert call["arguments"]["task_id"] == "task-1"


def test_named_cancel_does_not_select_unrelated_single_task() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-research",
                "state": "running",
                "stage": "run",
                "goal": "proactive_research: Research local-first productivity tools",
                "pipeline_name": "proactive_research",
                "pipeline_goal": "Research local-first productivity tools",
            }
        ]
    }

    call = _forced_tool_call_for_turn(
        "actually cancel that launch-note background task. "
        "i don't want extra work running right now.",
        state,
    )

    assert call is None


def test_named_cancel_rejects_all_zero_score_candidates() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-research",
                "state": "running",
                "stage": "run",
                "goal": "research local-first dashboard tools",
                "pipeline_name": "proactive_research",
            },
            {
                "task_id": "task-dashboard",
                "state": "running",
                "stage": "run",
                "goal": "scaffold the dashboard app",
                "pipeline_name": "user_autonomous_task",
            },
        ]
    }

    call = _forced_tool_call_for_turn(
        "cancel the launch-note background task",
        state,
    )

    assert call is None


def test_start_task_named_cancel_probe_does_not_cancel_existing_research() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-research",
                "state": "running",
                "stage": "run",
                "goal": "proactive_research: Research local-first productivity tools",
                "pipeline_name": "proactive_research",
            }
        ]
    }

    call = _forced_tool_call_for_turn(
        "start a tiny disposable background task named cancel-probe and keep "
        "the main local-first research running",
        state,
    )

    assert call is None or call["name"] != "cancel_task"


def test_cancel_word_inside_task_name_is_not_an_imperative() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-research",
                "state": "running",
                "stage": "run",
                "goal": "proactive_research: Research local-first productivity tools",
                "pipeline_name": "proactive_research",
            }
        ]
    }

    call = _forced_tool_call_for_turn(
        "please create a background task named cancel-probe and leave the "
        "research task alone",
        state,
    )

    assert call is None or call["name"] != "cancel_task"


def test_day2_life_opener_captures_meal_and_medication() -> None:
    calls = _forced_tool_calls_for_turn(
        "morning kora! took my adderall already. had a bagel and coffee. "
        "alex asked about dinner but i'll figure that out later.",
        {},
    )

    assert [call["name"] for call in calls] == ["log_meal", "log_medication"]
    assert calls[0]["arguments"]["description"] == "bagel and coffee"
    assert calls[0]["arguments"]["meal_type"] == "meal"


def test_evening_focus_close_captures_medication_and_focus_end() -> None:
    calls = _forced_tool_calls_for_turn(
        "ok i'm done, end the focus session. gonna take my melatonin 3mg "
        "and crash.",
        {},
    )

    assert [call["name"] for call in calls] == ["log_medication", "end_focus_block"]


def test_note_and_reminder_turn_captures_both_life_records() -> None:
    calls = _forced_tool_calls_for_turn(
        "note to self: check the API docs tomorrow. remind me about standup "
        "tomorrow morning.",
        {},
    )

    assert [call["name"] for call in calls] == ["create_reminder", "quick_note"]


def test_cancel_only_probe_ignores_do_not_cancel_research_clause() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-research",
                "state": "paused_for_state",
                "stage": "run",
                "goal": "proactive_research: Research local-first productivity tools",
                "pipeline_name": "proactive_research",
                "pipeline_goal": "Research local-first productivity tools",
            },
            {
                "task_id": "task-probe",
                "state": "pending",
                "stage": "research_and_summarize",
                "goal": "cancel-probe: throwaway cancellation testing",
                "pipeline_name": "cancel-probe",
                "pipeline_goal": "Summarize throwaway cancellation testing",
            },
        ]
    }

    call = _forced_tool_call_for_turn(
        "cancel only cancel-probe right now. do not cancel or disturb the "
        "unrelated local-first research task.",
        state,
    )

    assert call is not None
    assert call["name"] == "cancel_task"
    assert call["arguments"]["task_id"] == "task-probe"


def test_exact_task_id_cancel_uses_named_task_without_fuzzy_selection() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-research",
                "state": "paused_for_rate_limit",
                "stage": "user_added",
                "goal": "compare one local-only option against one cloud option",
                "pipeline_name": "proactive_research",
            },
            {
                "task_id": "task-28f2871ef075",
                "state": "pending",
                "stage": "Identify the two wording options",
                "goal": "Compare two launch-note wording options",
                "pipeline_name": "cancel_probe",
            },
        ]
    }

    call = _forced_tool_call_for_turn(
        "the exact cancel-probe worker task id is task-28f2871ef075. "
        "please cancel task-28f2871ef075 now. leave proactive_research alone.",
        state,
    )

    assert call is not None
    assert call["name"] == "cancel_task"
    assert call["arguments"]["task_id"] == "task-28f2871ef075"


def test_deictic_cancel_still_cancels_only_visible_task() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-1",
                "state": "running",
                "stage": "run",
                "goal": "research local-first dashboard tools",
                "pipeline_name": "proactive_research",
            }
        ]
    }

    call = _forced_tool_call_for_turn("cancel that task", state)

    assert call is not None
    assert call["name"] == "cancel_task"
    assert call["arguments"]["task_id"] == "task-1"


def test_keep_research_excludes_research_task_from_forced_cancel() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-research",
                "state": "running",
                "stage": "run",
                "goal": "proactive_research: local-first tools",
                "pipeline_goal": "Research local-first productivity tools",
            },
            {
                "task_id": "task-writing",
                "state": "running",
                "stage": "run",
                "goal": "proactive_research: README format decision",
                "pipeline_goal": "Decide whether BRIEF.md should become README.md",
            },
        ]
    }

    call = _forced_tool_call_for_turn(
        "actually stop that last writing background task so we do not waste "
        "time. keep the research task if it is still useful.",
        state,
    )

    assert call is not None
    assert call["name"] == "cancel_task"
    assert call["arguments"]["task_id"] == "task-writing"


def test_forced_cancel_skips_protected_system_pipeline() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-memory",
                "state": "pending",
                "stage": "consolidate",
                "goal": "Consolidate semantically related notes",
                "pipeline_name": "post_session_memory",
                "pipeline_goal": "Memory Steward: extract -> consolidate",
            },
            {
                "task_id": "task-user",
                "state": "running",
                "stage": "research",
                "goal": "research local-first dashboard tools",
                "pipeline_name": "proactive_research",
                "pipeline_goal": "Research local-first dashboard tools",
            },
        ]
    }

    call = _forced_tool_call_for_turn(
        "actually stop that background research; it is drifting too broad",
        state,
    )

    assert call is not None
    assert call["name"] == "cancel_task"
    assert call["arguments"]["task_id"] == "task-user"
