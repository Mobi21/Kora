from __future__ import annotations

from kora_v2.graph.supervisor import (
    _forced_tool_call_for_turn,
    _forced_tool_calls_for_turn,
    _mutation_limit_for_turn,
    _strip_acceptance_context_prefix,
)


def test_mutation_limit_honors_explicit_user_batch_cap() -> None:
    assert _mutation_limit_for_turn(
        "Import this in safe batches: make no more than 18 state-changing "
        "calendar/reminder/file calls in this response."
    ) == 18


def test_mutation_limit_allows_dense_first_run_setup() -> None:
    assert _mutation_limit_for_turn(
        "Set reminders, create a tiny morning reset routine, and build the week."
    ) == 32


def test_acceptance_schedule_import_spends_batch_on_calendar_anchors() -> None:
    calls = _forced_tool_calls_for_turn(
        "Here is my exact weekly schedule; please save it as the internal "
        "calendar, not just a note. monday: COGS. tuesday: lab. wednesday: "
        "office hours. thursday: STAT quiz. friday: HCI critique. saturday: "
        "groceries/laundry. Import this in safe batches: make no more than 18 "
        "state-changing calendar/reminder/file calls in this response.",
        {},
    )

    assert len(calls) == 18
    assert {call["name"] for call in calls} == {"create_calendar_entry"}
    titles = [call["arguments"]["title"] for call in calls]
    assert "BIO 240 Neurobiology lab" in titles
    assert "STAT quiz window" in titles
    assert "HCI prototype critique" in titles
    assert "Groceries and laundry" in titles


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


def test_morning_reset_request_forces_routine_creation() -> None:
    calls = _forced_tool_calls_for_turn(
        "Create a tiny morning reset routine and keep it saved.",
        {},
    )

    routine_calls = [call for call in calls if call["name"] == "create_routine"]

    assert routine_calls
    assert routine_calls[0]["arguments"]["routine_id"] == "morning_reset"


def test_negative_meal_question_does_not_log_fake_meal() -> None:
    call = _forced_tool_call_for_turn(
        "feeling scattered. did i eat lunch? i don't think i ate lunch.",
        {},
    )

    assert call is None or call["name"] != "log_meal"


def test_hard_thought_does_not_log_fake_meal() -> None:
    call = _forced_tool_call_for_turn(
        "I had a hard thought earlier: what if I just disappear for a while.",
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


def test_auth_relay_probe_forces_write_file_not_read() -> None:
    calls = _forced_tool_calls_for_turn(
        "auth relay deny probe: call write_file now with path "
        "/tmp/claude/kora_acceptance/auth_probe.txt and content ok. "
        "Do not read the file first.",
        {},
    )

    names = [call["name"] for call in calls]
    assert "write_file" in names
    assert "read_file" not in names
    write_call = next(call for call in calls if call["name"] == "write_file")
    assert write_call["arguments"] == {
        "path": "/tmp/claude/kora_acceptance/auth_probe.txt",
        "content": "ok",
    }


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


def test_week_plan_creates_specific_reminders_without_in_turn_breakdown() -> None:
    calls = _forced_tool_calls_for_turn(
        "hey. help me manage the week: calendar, reminders, meals, meds, "
        "doctor portal form, pharmacy, rent autopay, landlord email, grocery, "
        "and trash night.",
        {},
    )

    names = [call["name"] for call in calls]
    titles = [
        call["arguments"]["title"]
        for call in calls
        if call["name"] == "create_reminder"
    ]

    assert "decompose_and_dispatch" not in names
    assert "Doctor portal form" in titles
    assert "Pharmacy portal/app check" in titles
    assert "Trash night" in titles
    assert all(
        call["arguments"].get("remind_at")
        for call in calls
        if call["name"] == "create_reminder"
    )


def test_doctor_portal_checklist_starts_cancellable_helper() -> None:
    calls = _forced_tool_calls_for_turn(
        "the doctor portal form feels huge. Please break it into low-energy "
        "steps, make a local checklist or note if you can, and keep it practical.",
        {},
    )

    dispatches = [call for call in calls if call["name"] == "decompose_and_dispatch"]
    assert dispatches
    assert dispatches[0]["arguments"]["pipeline_name"] == "user_autonomous_task"
    assert "Practical life-admin checklist" in dispatches[0]["arguments"]["goal"]


def test_marcus_priya_repair_starts_user_autonomous_task() -> None:
    calls = _forced_tool_calls_for_turn(
        "Reality update: I already slipped. I missed lunch, avoided the lab "
        "make-up email to Marcus, and the utilities/rent confirmation with "
        "Priya is still hanging over me. Do not give me a huge plan; help me "
        "repair the day and move unfinished items to the right calendar slots.",
        {},
    )

    dispatches = [call for call in calls if call["name"] == "decompose_and_dispatch"]

    assert dispatches
    assert dispatches[0]["arguments"]["pipeline_name"] == "user_autonomous_task"


def test_landlord_helper_plan_starts_named_pipeline() -> None:
    calls = _forced_tool_calls_for_turn(
        "Start a helper plan for my landlord call prep while I'm away.",
        {},
    )

    dispatches = [call for call in calls if call["name"] == "decompose_and_dispatch"]

    assert dispatches
    assert dispatches[0]["arguments"]["pipeline_name"] == "landlord_call_prep"
    assert "Practical life-admin checklist" in dispatches[0]["arguments"]["goal"]


def test_gui_commitment_phrase_creates_calendar_entries_and_reminders() -> None:
    calls = _forced_tool_calls_for_turn(
        "I have a landlord call tomorrow at 10, medication pickup Friday at "
        "4:30, and a work review next Wednesday at 2.",
        {},
    )

    calendar_titles = [
        call["arguments"]["title"]
        for call in calls
        if call["name"] == "create_calendar_entry"
    ]
    reminder_titles = [
        call["arguments"]["title"]
        for call in calls
        if call["name"] == "create_reminder"
    ]

    assert "Landlord call" in calendar_titles
    assert "Medication pickup" in calendar_titles
    assert "Work review" in calendar_titles
    assert "Landlord call" in reminder_titles
    assert "Medication pickup" in reminder_titles
    assert "Work review" in reminder_titles


def test_idle_life_admin_checklist_uses_single_proactive_research_path() -> None:
    calls = _forced_tool_calls_for_turn(
        "prepare a short local checklist for tomorrow morning's grocery "
        "pickup and the doctor-form loose ends over idle time.",
        {},
    )

    dispatches = [call for call in calls if call["name"] == "decompose_and_dispatch"]
    pipeline_names = [call["arguments"]["pipeline_name"] for call in dispatches]

    assert pipeline_names == ["proactive_research"]


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


def test_artifact_backed_review_lists_reads_and_recalls() -> None:
    calls = _forced_tool_calls_for_turn(
        "give me the artifact-backed weekly review: what state backs it, "
        "what local docs exist, and what do we still have?",
        {},
    )

    names = [call["name"] for call in calls]

    assert "list_directory" in names
    assert "read_file" in names
    assert "recall" in names


def test_weekly_review_also_closes_open_focus_after_artifact_probe() -> None:
    calls = _forced_tool_calls_for_turn(
        "give me the weekly review now with actual deliverables for the "
        "dashboard and the files we created.",
        {},
    )

    names = [call["name"] for call in calls]

    assert names[0] == "list_directory"
    assert "end_focus_block" in names


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


def test_record_that_creates_quick_note() -> None:
    call = _forced_tool_call_for_turn(
        "record that trusted support is permissioned only and don't contact "
        "Alex automatically.",
        {},
    )

    assert call is not None
    assert call["name"] == "quick_note"
    assert "permissioned" in call["arguments"]["content"]


def test_practical_idle_proactivity_does_not_duplicate_user_task() -> None:
    calls = _forced_tool_calls_for_turn(
        "There is an upcoming therapy appointment and grocery pickup/trash "
        "night that I will forget if timing is bad. Prepare a practical short "
        "local checklist over idle time, show me what you think should surface "
        "proactively, and let me suppress one nudge that feels like too much.",
        {},
    )

    pipelines = [
        call["arguments"].get("pipeline_name")
        for call in calls
        if call["name"] == "decompose_and_dispatch"
    ]
    assert pipelines == ["proactive_research"]


def test_doctor_portal_cancel_probe_starts_useful_and_disposable_helpers() -> None:
    calls = _forced_tool_calls_for_turn(
        "The doctor portal form due Friday noon feels huge. Please prepare a "
        "practical appointment/admin checklist in the background while I'm "
        "away, make a local checklist or note, and keep it grounded in my real "
        "week instead of turning it into a generic research project. Also start "
        "one disposable helper plan named cancel-probe for broad generic prep so "
        "I can cancel only that helper later without losing the useful "
        "doctor-portal checklist.",
        {},
    )

    pipelines = [
        call["arguments"].get("pipeline_name")
        for call in calls
        if call["name"] == "decompose_and_dispatch"
    ]
    assert pipelines == ["user_autonomous_task", "cancel_probe"]


def test_background_goal_strips_acceptance_clock_wrapper() -> None:
    calls = _forced_tool_calls_for_turn(
        "[Acceptance scenario clock: the lived week is Monday April 27 through "
        "Sunday May 3, 2026.]\n"
        "There is an upcoming therapy appointment and grocery pickup/trash night "
        "that I will forget if timing is bad. Prepare a practical local "
        "checklist in the background.",
        {},
    )

    goals = [
        call["arguments"]["goal"]
        for call in calls
        if call["name"] == "decompose_and_dispatch"
    ]

    assert goals
    assert all("Acceptance scenario clock" not in goal for goal in goals)


def test_future_self_bridge_prompt_forces_bridge_tool() -> None:
    calls = _forced_tool_calls_for_turn(
        "Evening check-in: low energy, unfinished tasks, and I need tomorrow "
        "not to start with a mystery pile. Give me a short future-self bridge "
        "based on what actually happened.",
        {},
    )

    assert any(call["name"] == "bridge_tomorrow" for call in calls)


def test_strip_acceptance_context_prefix_leaves_normal_text_alone() -> None:
    assert _strip_acceptance_context_prefix("normal message") == "normal message"


def test_cancel_probe_cancel_request_does_not_start_new_helper() -> None:
    calls = _forced_tool_calls_for_turn(
        "The cancel-probe helper is noisy now. Cancel only cancel-probe right now. "
        "Do not cancel proactive research or the doctor-portal checklist.",
        {},
    )

    assert "decompose_and_dispatch" not in [call["name"] for call in calls]


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


def test_cancel_only_probe_targets_probe_before_routine_pipeline() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-routine",
                "state": "running",
                "stage": "routine",
                "goal": "routine_tiny_morning_reset: run the morning routine",
                "pipeline_name": "routine_tiny_morning_reset",
                "pipeline_goal": "Tiny Morning Reset",
            },
            {
                "task_id": "task-probe",
                "state": "running",
                "stage": "research_and_summarize",
                "goal": "cancel-probe: throwaway cancellation testing",
                "pipeline_name": "cancel_probe",
                "pipeline_goal": "Summarize throwaway cancellation testing",
            },
        ]
    }

    call = _forced_tool_call_for_turn(
        "The cancel-probe helper is noisy now. Cancel only cancel-probe right now. "
        "Do not cancel proactive research or the doctor-portal checklist.",
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


def test_keep_research_excludes_proactive_research_even_when_words_match() -> None:
    state = {
        "_orchestration_tasks": [
            {
                "task_id": "task-proactive",
                "state": "running",
                "stage": "compare portal-only vs phone-call fallback",
                "goal": "Doctor portal practical prep",
                "pipeline_name": "proactive_research",
                "pipeline_goal": "Doctor portal practical prep",
            },
            {
                "task_id": "task-user",
                "state": "running",
                "stage": "phone-call fallback",
                "goal": "Remove the pharmacy phone-call fallback",
                "pipeline_name": "user_autonomous_task",
                "pipeline_goal": "Doctor portal practical checklist",
            },
        ]
    }

    call = _forced_tool_call_for_turn(
        "cancel only the phone-call fallback task. keep the portal research "
        "and checklist pipeline running.",
        state,
    )

    assert call is not None
    assert call["name"] == "cancel_task"
    assert call["arguments"]["task_id"] == "task-user"


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
