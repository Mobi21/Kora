"""Truth-oriented acceptance report scoring tests."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import tests.acceptance._report as report
from tests.acceptance._report import (
    _apply_persona_completion_gate,
    _auto_mark_coverage,
    _merge_auth_evidence,
    _persona_run_completion,
    _with_startup_grace,
)


def _base_kwargs() -> dict:
    return {
        "tool_usage": {
            "tool_counts": {},
            "life_management": [],
            "filesystem": [],
            "mcp": [],
            "capability_browser": [],
            "capability_workspace": [],
            "capability_vault": [],
        },
        "life_data": {},
        "auto_state": {},
        "cap_health": {},
        "compaction_events": [],
        "messages": [],
        "auth_results": [],
        "latest_status": None,
        "orch_evidence": None,
    }


def test_decompose_tool_call_alone_is_only_partial_credit() -> None:
    kwargs = _base_kwargs()
    kwargs["tool_usage"]["tool_counts"] = {"decompose_and_dispatch": 1}

    marks = _auto_mark_coverage(**kwargs)

    assert marks[8] == "~"
    assert 21 not in marks


def test_incomplete_persona_run_downgrades_life_os_headline_markers() -> None:
    markers = {1: "x", 2: "x", 15: "x", 18: "x", 67: "x"}

    gated = _apply_persona_completion_gate(
        markers,
        {"present": True, "complete": False, "reason": "persona-run emitted run_failed"},
    )

    assert gated[1] == "~"
    assert gated[2] == "~"
    assert gated[67] == "~"
    assert gated[15] == "x"
    assert gated[18] == "x"


def test_error_recovery_requires_all_probe_messages_to_survive() -> None:
    kwargs = _base_kwargs()
    kwargs["error_results"] = [
        {"test": "malformed_json_frame", "survived": True},
        {"test": "empty_chat_content", "survived": True},
        {"test": "special_chars", "survived": False},
        {"test": "long_message", "survived": True},
        {"test": "unicode", "survived": False},
        {"test": "normal_after_errors", "survived": True},
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert marks[18] == "~"

    for result in kwargs["error_results"]:
        result["survived"] = True
    marks = _auto_mark_coverage(**kwargs)

    assert marks[18] == "x"


def test_filesystem_item_requires_read_write_and_list() -> None:
    kwargs = _base_kwargs()
    kwargs["tool_usage"]["tool_counts"] = {
        "read_file": 1,
        "write_file": 1,
    }
    kwargs["tool_usage"]["filesystem"] = ["read_file", "write_file"]

    marks = _auto_mark_coverage(**kwargs)

    assert 22 not in marks

    kwargs["tool_usage"]["tool_counts"]["list_directory"] = 1
    marks = _auto_mark_coverage(**kwargs)

    assert marks[22] == "x"


def test_life_os_context_item_requires_support_and_local_first() -> None:
    kwargs = _base_kwargs()
    kwargs["messages"] = [
        {
            "role": "user",
            "content": "ADHD, Talia, and Maya's school schedule are established.",
        }
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert 2 not in marks

    kwargs["messages"].append(
        {
            "role": "assistant",
            "content": (
                "Maya has ADHD support needs, Talia is trusted support, "
                "and Kora should keep this local-first for privacy."
            ),
        }
    )

    marks = _auto_mark_coverage(**kwargs)

    assert marks[2] == "x"


def test_life_db_persistence_requires_medication_meal_and_reminder() -> None:
    kwargs = _base_kwargs()
    kwargs["life_data"] = {
        "available": True,
        "medication_count": 1,
        "meal_count": 0,
        "reminder_count": 0,
    }

    marks = _auto_mark_coverage(**kwargs)

    assert marks[23] == "~"

    kwargs["life_data"]["meal_count"] = 1
    kwargs["life_data"]["reminder_count"] = 1
    marks = _auto_mark_coverage(**kwargs)

    assert marks[23] == "x"


def test_wrong_inference_can_be_scored_from_durable_correction_events() -> None:
    kwargs = _base_kwargs()
    kwargs["life_data"] = {"correction_event_count": 1}

    marks = _auto_mark_coverage(**kwargs)

    assert marks[11] == "x"


def test_external_capability_disclosure_satisfies_optional_web_item() -> None:
    kwargs = _base_kwargs()
    kwargs["cap_health"] = {
        "browser": {
            "status": "unconfigured",
            "remediation": "set up browser connector",
        }
    }
    kwargs["messages"] = [
        {
            "role": "assistant",
            "content": (
                "MCP web-search path failed because the browser is unconfigured; "
                "I will use local state instead."
            ),
        }
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert marks[9] == "x"
    assert marks[101] == "x"


def test_decompose_with_user_pipeline_and_tasks_gets_dispatch_credit() -> None:
    kwargs = _base_kwargs()
    kwargs["tool_usage"]["tool_counts"] = {"decompose_and_dispatch": 1}
    kwargs["orch_evidence"] = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "pipe-1",
                "state": "running",
                "parent_session_id": "sess",
            }
        ],
        "worker_tasks": [{"pipeline_instance_id": "pipe-1"}],
        "ledger_events": [],
        "system_phases_observed": [],
    }

    marks = _auto_mark_coverage(**kwargs)

    assert marks[8] == "x"


def test_phase_item_requires_light_idle_not_any_three_phases() -> None:
    evidence = {
        "available": True,
        "pipeline_instances": [],
        "worker_tasks": [],
        "ledger_events": [],
        "turn_traces": [],
        "notifications": [],
        "system_phases_observed": [
            "conversation",
            "active_idle",
            "deep_idle",
            "wake_up_window",
        ],
    }

    marks = report._derive_orchestration_markers(evidence)

    assert marks[24] == "~"

    evidence["system_phases_observed"].append("light_idle")
    marks = report._derive_orchestration_markers(evidence)

    assert marks[24] == "x"


def test_moc_stage_without_pages_does_not_score_moc_item() -> None:
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "vault-1",
                "pipeline_name": "post_memory_vault",
                "state": "completed",
            }
        ],
        "worker_tasks": [
            {
                "id": "task-1",
                "pipeline_instance_id": "vault-1",
                "stage_name": "moc_sessions",
                "state": "completed",
                "task_preset": "bounded_background",
            }
        ],
        "ledger_events": [],
        "turn_traces": [],
        "notifications": [],
        "system_phases_observed": [],
    }

    marks = report._derive_orchestration_markers(evidence)

    assert 56 not in marks


def test_long_autonomous_requires_runtime_progress_evidence() -> None:
    kwargs = _base_kwargs()
    kwargs["orch_evidence"] = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "pipe-1",
                "intent_duration": "long",
                "state": "running",
                "parent_session_id": "sess",
            }
        ],
        "worker_tasks": [{"pipeline_instance_id": "pipe-1"}],
        "ledger_events": [
            {
                "pipeline_instance_id": "pipe-1",
                "event_type": "task_progress",
            }
        ],
        "system_phases_observed": [],
    }

    marks = _auto_mark_coverage(**kwargs)

    assert marks[21] == "~"


def test_deep_idle_housekeeping_marks_background_pipeline_item() -> None:
    kwargs = _base_kwargs()
    kwargs["orch_evidence"] = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "pipe-1",
                "pipeline_name": "session_bridge_pruning",
                "state": "completed",
            },
            {
                "id": "pipe-2",
                "pipeline_name": "skill_refinement",
                "state": "completed",
            },
        ],
        "worker_tasks": [],
        "ledger_events": [],
        "system_phases_observed": ["conversation", "active_idle", "deep_idle"],
    }

    marks = _auto_mark_coverage(**kwargs)

    assert marks[12] == "x"


def test_get_task_progress_tool_call_marks_midflight_progress_item() -> None:
    kwargs = _base_kwargs()
    kwargs["tool_usage"]["tool_counts"] = {"get_task_progress": 1}

    marks = _auto_mark_coverage(**kwargs)

    assert marks[29] == "x"


def test_get_running_tasks_with_completed_task_marks_reengagement() -> None:
    kwargs = _base_kwargs()
    kwargs["tool_usage"]["tool_counts"] = {"get_running_tasks": 1}
    kwargs["orch_evidence"] = {
        "available": True,
        "pipeline_instances": [],
        "worker_tasks": [{"state": "completed"}],
        "ledger_events": [],
        "system_phases_observed": [],
    }

    marks = _auto_mark_coverage(**kwargs)

    assert marks[37] == "x"


def test_progress_tools_with_completed_task_mark_reengagement() -> None:
    kwargs = _base_kwargs()
    kwargs["tool_usage"]["tool_counts"] = {"get_task_progress": 1}
    kwargs["orch_evidence"] = {
        "available": True,
        "pipeline_instances": [],
        "worker_tasks": [{"state": "completed"}],
        "ledger_events": [],
        "system_phases_observed": [],
    }

    marks = _auto_mark_coverage(**kwargs)

    assert marks[37] == "x"


def test_decision_aging_requires_open_decision_and_trigger_evidence() -> None:
    kwargs = _base_kwargs()
    kwargs["orch_evidence"] = {
        "available": True,
        "open_decision_count": 1,
        "pipeline_instances": [],
        "worker_tasks": [],
        "ledger_events": [],
        "system_phases_observed": [],
    }

    marks = _auto_mark_coverage(**kwargs)
    assert marks[43] == "~"

    kwargs["orch_evidence"]["ledger_events"] = [
        {
            "event_type": "trigger_fired",
            "trigger_name": "DECISION_PENDING_3D",
        }
    ]
    marks = _auto_mark_coverage(**kwargs)
    assert marks[43] == "x"


def test_wrong_inference_scorer_continues_past_failed_prompt() -> None:
    kwargs = _base_kwargs()
    kwargs["messages"] = [
        {"role": "user", "content": "please replan"},
        {"role": "assistant", "content": "I replanned."},
        {
            "role": "user",
            "content": (
                "you assumed i wanted a phone call, but that's not what i meant. "
                "calls are the hard part. correct that and replan."
            ),
        },
        {
            "role": "assistant",
            "content": "You're right; I updated that assumption and replanned around a short message draft instead.",
        },
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert marks[11] == "x"


def test_calendar_spine_marks_dated_life_plan() -> None:
    kwargs = _base_kwargs()
    kwargs["messages"] = [
        {
            "role": "assistant",
            "content": (
                "Today has the doctor portal form, tomorrow has the pharmacy call, "
                "and Tuesday trash night needs a reminder on the calendar."
            ),
        }
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert marks[3] == "x"


def test_wrong_inference_repair_accepts_support_preference_correction() -> None:
    kwargs = _base_kwargs()
    kwargs["messages"] = [
        {
            "role": "user",
            "content": (
                "wrong assumption: don't ask Talia automatically. correct that "
                "support boundary and replan."
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Corrected: I will not contact Talia automatically. I updated "
                "the support boundary and replanned around asking only if you choose it."
            ),
        },
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert marks[11] == "x"


def test_weekly_review_requires_life_os_state() -> None:
    kwargs = _base_kwargs()
    kwargs["messages"] = [
        {"role": "user", "content": "give me the weekly review"},
        {
            "role": "assistant",
            "content": (
                "Weekly review: missed lunch was repaired, the calendar still "
                "has tomorrow's pharmacy call, and the reminder/routine state "
                "shows what carries into next week."
            ),
        },
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert marks[14] == "x"


def test_weekly_review_scan_continues_past_earlier_summary() -> None:
    kwargs = _base_kwargs()
    kwargs["messages"] = [
        {"role": "user", "content": "can you give me a quick summary?"},
        {
            "role": "assistant",
            "content": "Medication logged, lunch still missing.",
        },
        {
            "role": "user",
            "content": (
                "give me the comprehensive weekly review. What actually happened, "
                "what was missed, repaired, and what state backs it?"
            ),
        },
        {
            "role": "assistant",
            "content": (
                "The calendar still has tomorrow's pharmacy call. The reminder "
                "and routine records back that up. Lunch was missed, repaired, "
                "and the next week plan keeps the essentials only."
            ),
        },
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert marks[14] == "x"


def test_weekly_review_rejects_thursday_only_claim() -> None:
    kwargs = _base_kwargs()
    kwargs["messages"] = [
        {
            "role": "user",
            "content": (
                "weekly review: what actually happened this week, what got missed, "
                "what got repaired, and what state backs it?"
            ),
        },
        {
            "role": "assistant",
            "content": (
                "What I can prove: this was a Thursday-only session. I have no "
                "data for Monday through Wednesday. Lunch was missed and repaired; "
                "the calendar has tomorrow's reminder and routine state."
            ),
        },
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert 14 not in marks


def test_restart_scorer_requires_restart_prompt_and_continues_scan(
    tmp_path: Path,
) -> None:
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "pre_restart.json").write_text("{}", encoding="utf-8")
    (snapshots / "post_restart.json").write_text(
        json.dumps({"status": {"status": "running"}}),
        encoding="utf-8",
    )
    kwargs = _base_kwargs()
    kwargs["snapshots_dir"] = snapshots
    kwargs["messages"] = [
        {"role": "user", "content": "what do you remember"},
        {"role": "assistant", "content": "The calendar and reminders are here."},
        {"role": "user", "content": "before the restart, what survived restart?"},
        {
            "role": "assistant",
            "content": "Calendar, reminders, support profile, routine, and tomorrow carryover survived restart.",
        },
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert marks[13] == "x"


def test_restart_scorer_accepts_calendar_support_wording(
    tmp_path: Path,
) -> None:
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "pre_restart.json").write_text("{}", encoding="utf-8")
    (snapshots / "post_restart.json").write_text(
        json.dumps({"status": {"status": "running"}}),
        encoding="utf-8",
    )
    kwargs = _base_kwargs()
    kwargs["snapshots_dir"] = snapshots
    kwargs["messages"] = [
        {"role": "user", "content": "before the restart, what survived restart?"},
        {
            "role": "assistant",
            "content": "Calendar, reminder, support profile, and routine survived restart.",
        },
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert marks[13] == "x"


def test_restart_scorer_accepts_tomorrow_unfinished_wording(
    tmp_path: Path,
) -> None:
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "pre_restart.json").write_text("{}", encoding="utf-8")
    (snapshots / "post_restart.json").write_text(
        json.dumps({"status": {"status": "running"}}),
        encoding="utf-8",
    )
    kwargs = _base_kwargs()
    kwargs["snapshots_dir"] = snapshots
    kwargs["messages"] = [
        {"role": "user", "content": "what survived restart?"},
        {
            "role": "assistant",
            "content": (
                "The calendar survived. The unfinished landlord email carried "
                "to tomorrow, and the reminder state survived."
            ),
        },
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert marks[13] == "x"


def test_restart_scorer_accepts_durable_post_restart_state(
    tmp_path: Path,
) -> None:
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "pre_restart.json").write_text("{}", encoding="utf-8")
    (snapshots / "post_restart.json").write_text(
        json.dumps({
            "status": {"status": "running"},
            "vault_state": {
                "working_docs": [
                    {"pipeline_name": "routine_stabilization_basics"}
                ]
            },
        }),
        encoding="utf-8",
    )
    kwargs = _base_kwargs()
    kwargs["snapshots_dir"] = snapshots
    kwargs["life_data"] = {
        "reminder_count": 3,
        "support_profile_count": 2,
        "routine_count": 1,
    }
    kwargs["orch_evidence"] = {
        "open_decision_count": 1,
        "worker_tasks": [],
        "pipeline_instances": [],
    }

    marks = _auto_mark_coverage(**kwargs)

    assert marks[13] == "x"


def test_final_life_os_recap_marks_weekly_review() -> None:
    kwargs = _base_kwargs()
    kwargs["messages"] = [
        {"role": "user", "content": "anything else before you end today?"},
        {
            "role": "assistant",
            "content": (
                "Quick recap: lunch was missed and repaired, the calendar has "
                "tomorrow's pharmacy reminder, and the routine state gives the next step."
            ),
        },
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert marks[14] == "x"


def test_pattern_scan_requires_pattern_nudge_for_item_59() -> None:
    kwargs = _base_kwargs()
    kwargs["orch_evidence"] = {
        "available": True,
        "pipeline_instances": [
            {"pipeline_name": "proactive_pattern_scan", "state": "completed"}
        ],
        "worker_tasks": [],
        "ledger_events": [
            {
                "event_type": "trigger_fired",
                "trigger_name": "INSIGHT_AVAILABLE",
            }
        ],
        "notifications": [],
        "system_phases_observed": [],
    }

    marks = _auto_mark_coverage(**kwargs)

    assert marks[42] == "x"
    assert 59 not in marks

    kwargs["orch_evidence"]["notifications"] = [
        {
            "delivery_tier": "templated",
            "template_id": "pattern_nudge",
        }
    ]
    marks = _auto_mark_coverage(**kwargs)
    assert marks[59] == "x"


def test_pattern_scan_accepts_current_context_triggers() -> None:
    kwargs = _base_kwargs()
    kwargs["orch_evidence"] = {
        "available": True,
        "pipeline_instances": [
            {"pipeline_name": "proactive_pattern_scan", "state": "completed"}
        ],
        "worker_tasks": [],
        "ledger_events": [
            {
                "event_type": "trigger_fired",
                "trigger_name": "proactive_pattern_scan.event.MEMORY_STORED",
                "metadata_json": '{"event_names":["MEMORY_STORED"]}',
            }
        ],
        "notifications": [],
        "system_phases_observed": [],
    }

    marks = _auto_mark_coverage(**kwargs)

    assert marks[42] == "x"
    assert marks[58] == "x"


def test_cancel_probe_accepts_phone_fallback_pipeline() -> None:
    kwargs = _base_kwargs()
    kwargs["orch_evidence"] = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "pharmacy_phone_fallback-1",
                "pipeline_name": "pharmacy_phone_fallback",
                "goal": "Start a noisy helper for phone fallback only",
                "state": "cancelled",
            },
            {
                "id": "proactive_research-1",
                "pipeline_name": "proactive_research",
                "goal": "Doctor portal practical prep",
                "state": "completed",
            },
        ],
        "worker_tasks": [
            {
                "task_id": "task-phone",
                "pipeline_instance_id": "pharmacy_phone_fallback-1",
                "state": "cancelled",
            }
        ],
        "ledger_events": [
            {
                "event_type": "task_cancelled",
                "pipeline_instance_id": "pharmacy_phone_fallback-1",
                "worker_task_id": "task-phone",
            }
        ],
        "notifications": [],
        "system_phases_observed": [],
    }

    marks = _auto_mark_coverage(**kwargs)

    assert marks[30] == "x"


def test_orchestration_evidence_counts_backdated_open_decisions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    db_path = db_dir / "operational.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE open_decisions (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                status TEXT NOT NULL,
                posed_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE work_ledger (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                pipeline_instance_id TEXT,
                worker_task_id TEXT,
                trigger_name TEXT,
                reason TEXT,
                metadata_json TEXT
            )
            """
        )
        db.execute(
            "INSERT INTO open_decisions (id, topic, status, posed_at) "
            "VALUES ('dec-old', 'Choose scheduler', 'open', "
            "'2026-04-20T00:00:00+00:00')"
        )
        db.execute(
            "INSERT INTO work_ledger "
            "(timestamp, event_type, trigger_name, reason) VALUES "
            "('2026-04-25T17:00:00+00:00', 'trigger_fired', "
            "'DECISION_PENDING_3D', 'open_decision_aged')"
        )

    monkeypatch.setattr(report, "_PROJECT_ROOT", tmp_path)

    evidence = asyncio.run(
        report._query_orchestration_evidence("2026-04-25T16:00:00+00:00")
    )
    marks = report._derive_orchestration_markers(evidence)

    assert evidence["open_decision_count"] == 1
    assert marks[43] == "x"


def test_orchestration_evidence_includes_worker_result_summaries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    db_path = db_dir / "operational.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE pipeline_instances (
                id TEXT PRIMARY KEY,
                pipeline_name TEXT,
                state TEXT,
                intent_duration TEXT,
                parent_session_id TEXT,
                completion_reason TEXT,
                working_doc_path TEXT,
                started_at TEXT
            )
            """
        )
        db.execute(
            """
            CREATE TABLE worker_tasks (
                id TEXT,
                pipeline_instance_id TEXT,
                stage_name TEXT,
                state TEXT,
                task_preset TEXT,
                result_summary TEXT,
                error_message TEXT,
                cancellation_requested INTEGER DEFAULT 0
            )
            """
        )
        db.execute(
            "CREATE TABLE system_state_log (new_phase TEXT, transitioned_at TEXT)"
        )
        db.execute(
            """
            CREATE TABLE work_ledger (
                timestamp TEXT,
                event_type TEXT,
                pipeline_instance_id TEXT,
                worker_task_id TEXT,
                reason TEXT,
                trigger_name TEXT,
                metadata_json TEXT
            )
            """
        )
        db.execute(
            """
            CREATE TABLE turn_traces (
                user_input TEXT,
                tool_call_count INTEGER,
                tools_invoked TEXT,
                final_output TEXT,
                succeeded INTEGER,
                started_at TEXT
            )
            """
        )
        db.execute("CREATE TABLE request_limiter_log (class TEXT)")
        db.execute(
            """
            CREATE TABLE notifications (
                delivery_tier TEXT,
                template_id TEXT,
                reason TEXT,
                delivered_at TEXT
            )
            """
        )
        db.execute("CREATE TABLE runtime_pipelines (created_at TEXT)")
        db.execute("CREATE TABLE open_decisions (id TEXT)")
        db.execute("CREATE TABLE session_transcripts (created_at TEXT)")
        db.execute("CREATE TABLE signal_queue (created_at TEXT)")
        db.execute(
            "INSERT INTO pipeline_instances "
            "(id, pipeline_name, state, started_at) "
            "VALUES ('pipe-1', 'post_session_memory', 'completed', "
            "'2026-04-25T17:00:00+00:00')"
        )
        db.execute(
            "INSERT INTO worker_tasks "
            "(id, pipeline_instance_id, stage_name, state, task_preset, result_summary) "
            "VALUES ('task-1', 'pipe-1', 'entities', 'completed', "
            "'bounded_background', 'entity resolution: 1 merged, 0 distinct')"
        )

    monkeypatch.setattr(report, "_PROJECT_ROOT", tmp_path)

    evidence = asyncio.run(
        report._query_orchestration_evidence("2026-04-25T16:00:00+00:00")
    )
    marks = report._derive_orchestration_markers(evidence)

    assert evidence["worker_tasks"][0]["result_summary"].startswith(
        "entity resolution"
    )
    assert marks[50] == "x"


def test_proactive_research_cancelled_task_does_not_score_done(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "research.md"
    doc.write_text(
        "# Summary\nReal summary\n\n# Findings\nReal findings\n",
        encoding="utf-8",
    )
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "proactive_research-1",
                "pipeline_name": "proactive_research",
                "state": "completed",
                "intent_duration": "long",
                "parent_session_id": "sess",
                "working_doc_path": str(doc),
            }
        ],
        "worker_tasks": [
            {
                "id": "task-1",
                "pipeline_instance_id": "proactive_research-1",
                "stage_name": "run",
                "state": "completed",
                "task_preset": "long_background",
                "result_summary": "research: report written with 3 sources",
                "cancellation_requested": 1,
            }
        ],
        "ledger_events": [
            {
                "event_type": "task_cancelled",
                "worker_task_id": "task-1",
                "reason": "cancel that launch-note background task",
            }
        ],
        "system_phases_observed": [],
    }

    marks = report._derive_orchestration_markers(evidence)
    kwargs = _base_kwargs()
    kwargs["orch_evidence"] = evidence
    auto_marks = _auto_mark_coverage(**kwargs)

    assert auto_marks[21] == "~"
    assert 28 not in marks
    assert 61 not in marks


def test_cancel_probe_requires_isolated_cancellation() -> None:
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "probe-1",
                "pipeline_name": "cancel-probe",
                "state": "cancelled",
            },
            {
                "id": "research-1",
                "pipeline_name": "proactive_research",
                "state": "completed",
            },
        ],
        "worker_tasks": [
            {
                "id": "probe-task",
                "pipeline_instance_id": "probe-1",
                "state": "cancelled",
                "cancellation_requested": 1,
            },
            {
                "id": "research-task",
                "pipeline_instance_id": "research-1",
                "state": "completed",
                "cancellation_requested": 0,
            },
        ],
        "ledger_events": [{"event_type": "task_cancelled"}],
        "system_phases_observed": [],
    }

    marks = report._derive_orchestration_markers(evidence)

    assert marks[30] == "x"

    evidence["worker_tasks"][1]["cancellation_requested"] = 1
    marks = report._derive_orchestration_markers(evidence)

    assert marks[30] == "~"


def test_cancel_probe_accepts_underscore_pipeline_name() -> None:
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "probe-1",
                "pipeline_name": "cancel_probe",
                "state": "cancelled",
            },
            {
                "id": "research-1",
                "pipeline_name": "proactive_research",
                "state": "completed",
            },
        ],
        "worker_tasks": [
            {
                "id": "probe-task",
                "pipeline_instance_id": "probe-1",
                "state": "cancelled",
                "cancellation_requested": 1,
            },
            {
                "id": "research-task",
                "pipeline_instance_id": "research-1",
                "state": "completed",
                "cancellation_requested": 0,
            },
        ],
        "ledger_events": [
            {
                "event_type": "task_cancelled",
                "pipeline_instance_id": "probe-1",
                "worker_task_id": "probe-task",
            }
        ],
        "turn_traces": [],
        "notifications": [],
        "system_phases_observed": [],
    }

    marks = report._derive_orchestration_markers(evidence)

    assert marks[30] == "x"


def test_cancel_probe_accepts_goal_alias_when_model_used_research_pipeline() -> None:
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "proactive_research-probe",
                "pipeline_name": "proactive_research",
                "state": "cancelled",
                "goal": "cancel-probe: compare two launch-note options",
            },
            {
                "id": "research-1",
                "pipeline_name": "proactive_research",
                "state": "completed",
                "goal": "Research local-first productivity tools",
            },
        ],
        "worker_tasks": [
            {
                "id": "probe-task",
                "pipeline_instance_id": "proactive_research-probe",
                "state": "cancelled",
                "cancellation_requested": 1,
            },
            {
                "id": "research-task",
                "pipeline_instance_id": "research-1",
                "state": "completed",
                "cancellation_requested": 0,
            },
        ],
        "ledger_events": [{"event_type": "task_cancelled"}],
        "system_phases_observed": [],
    }

    marks = report._derive_orchestration_markers(evidence)

    assert marks[30] == "x"


def test_cancel_probe_accepts_broad_helper_life_admin_pipeline() -> None:
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "helper-1",
                "pipeline_name": "user_autonomous_task",
                "state": "running",
                "goal": "Broad helper research task for practical life-admin checklist",
            },
            {
                "id": "research-1",
                "pipeline_name": "proactive_research",
                "state": "completed",
                "goal": "Doctor portal practical prep",
            },
        ],
        "worker_tasks": [
            {
                "id": "helper-task",
                "pipeline_instance_id": "helper-1",
                "state": "cancelled",
                "cancellation_requested": 1,
            },
            {
                "id": "research-task",
                "pipeline_instance_id": "research-1",
                "state": "completed",
                "cancellation_requested": 0,
            },
        ],
        "ledger_events": [{"event_type": "task_cancelled"}],
        "system_phases_observed": [],
    }

    marks = report._derive_orchestration_markers(evidence)

    assert marks[30] == "x"


def test_cancel_task_accepts_generic_isolated_cancelled_pipeline() -> None:
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "continuity_check-1",
                "pipeline_name": "continuity_check",
                "state": "cancelled",
                "completion_reason": "task_cancelled",
                "working_doc_path": "/tmp/continuity.md",
            },
            {
                "id": "research-1",
                "pipeline_name": "proactive_research",
                "state": "completed",
                "goal": "Doctor portal practical prep",
            },
        ],
        "worker_tasks": [
            {
                "id": "continuity-task",
                "pipeline_instance_id": "continuity_check-1",
                "state": "cancelled",
                "cancellation_requested": 1,
            }
        ],
        "ledger_events": [
            {
                "event_type": "task_cancelled",
                "pipeline_instance_id": "continuity_check-1",
                "worker_task_id": "continuity-task",
            }
        ],
        "system_phases_observed": [],
    }

    marks = report._derive_orchestration_markers(evidence)

    assert marks[30] == "x"


def test_proactive_research_clean_completion_scores_done(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "research.md"
    doc.write_text(
        "# Summary\nReal summary\n\n# Findings\nReal findings\n",
        encoding="utf-8",
    )
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "proactive_research-1",
                "pipeline_name": "proactive_research",
                "state": "completed",
                "intent_duration": "long",
                "parent_session_id": "sess",
                "working_doc_path": str(doc),
            }
        ],
        "worker_tasks": [
            {
                "id": "task-1",
                "pipeline_instance_id": "proactive_research-1",
                "stage_name": "run",
                "state": "completed",
                "task_preset": "long_background",
                "result_summary": "research: report written with 3 sources",
                "cancellation_requested": 0,
            }
        ],
        "ledger_events": [],
        "system_phases_observed": [],
    }

    marks = report._derive_orchestration_markers(evidence)
    kwargs = _base_kwargs()
    kwargs["orch_evidence"] = evidence
    auto_marks = _auto_mark_coverage(**kwargs)

    assert auto_marks[21] == "x"
    assert marks[28] == "x"
    assert marks[61] == "x"


def test_proactive_research_report_with_embedded_headings_scores_done(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "research.md"
    doc.write_text(
        "# Summary\nResearch brief written\n\n"
        "# Findings\n## Research Document\n\n"
        "## Findings\nObsidian and local-first options compared.\n",
        encoding="utf-8",
    )
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "proactive_research-1",
                "pipeline_name": "proactive_research",
                "state": "completed",
                "intent_duration": "long",
                "parent_session_id": "sess",
                "working_doc_path": str(doc),
                "goal": "Compare Obsidian and local-first dashboard options",
            }
        ],
        "worker_tasks": [
            {
                "id": "task-1",
                "pipeline_instance_id": "proactive_research-1",
                "stage_name": "run",
                "state": "completed",
                "task_preset": "long_background",
                "result_summary": "research: Inbox report written",
                "cancellation_requested": 0,
            }
        ],
        "ledger_events": [],
        "system_phases_observed": [],
    }

    marks = report._derive_orchestration_markers(evidence)
    kwargs = _base_kwargs()
    kwargs["orch_evidence"] = evidence
    auto_marks = _auto_mark_coverage(**kwargs)

    assert auto_marks[21] == "x"
    assert marks[28] == "x"
    assert marks[61] == "x"


def test_proactive_research_legacy_noncanonical_report_scores_done(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "research.md"
    doc.write_text(
        "# Summary\nResearch brief written for local-first dashboard options\n\n"
        "# Current Plan\n- [ ] run\n\n"
        "# Findings\n\n"
        "# Notes\n\n"
        "# Completion\nPipeline completed.\n\n"
        "# Research Document: Local-First Focus Dashboard Approaches\n\n"
        "## Key Findings\n\n"
        "Obsidian/Dataview, a tiny local-first web app, and markdown scripts "
        "were compared for setup time, maintenance, flexibility, and ADHD fit. "
        "The recommendation favored Obsidian while noting source verification pending.\n",
        encoding="utf-8",
    )
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "proactive_research-1",
                "pipeline_name": "proactive_research",
                "state": "completed",
                "intent_duration": "long",
                "parent_session_id": "sess",
                "working_doc_path": str(doc),
                "goal": "Compare Obsidian and local-first dashboard options",
            }
        ],
        "worker_tasks": [
            {
                "id": "task-1",
                "pipeline_instance_id": "proactive_research-1",
                "stage_name": "run",
                "state": "completed",
                "task_preset": "long_background",
                "result_summary": "research: Inbox report written; source verification pending",
                "cancellation_requested": 0,
            }
        ],
        "ledger_events": [],
        "system_phases_observed": [],
    }

    marks = report._derive_orchestration_markers(evidence)
    kwargs = _base_kwargs()
    kwargs["orch_evidence"] = evidence
    auto_marks = _auto_mark_coverage(**kwargs)

    assert auto_marks[21] == "x"
    assert marks[28] == "x"
    assert marks[61] == "x"


def test_completed_degraded_research_scores_partial_not_red(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "research.md"
    doc.write_text(
        "# Summary\nResearch started\n\n"
        "# Current Plan\n- [ ] run\n- [ ] compare local-only against cloud\n\n"
        "# Findings\n\n"
        "# Notes\nsource verification pending\n",
        encoding="utf-8",
    )
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "proactive_research-1",
                "pipeline_name": "proactive_research",
                "state": "completed",
                "intent_duration": "long",
                "parent_session_id": "sess",
                "working_doc_path": str(doc),
                "goal": "Compare local-first dashboard options",
            }
        ],
        "worker_tasks": [
            {
                "id": "task-1",
                "pipeline_instance_id": "proactive_research-1",
                "stage_name": "run",
                "state": "completed",
                "task_preset": "long_background",
                "result_summary": "research: Inbox report written; source verification pending",
                "cancellation_requested": 0,
            },
            {
                "id": "task-2",
                "pipeline_instance_id": "proactive_research-1",
                "stage_name": "user_added",
                "state": "completed",
                "task_preset": "bounded_background",
                "result_summary": "research: source verification pending",
                "cancellation_requested": 0,
            },
        ],
        "ledger_events": [],
        "system_phases_observed": [],
    }

    marks = report._derive_orchestration_markers(evidence)
    kwargs = _base_kwargs()
    kwargs["orch_evidence"] = evidence
    auto_marks = _auto_mark_coverage(**kwargs)

    assert auto_marks[21] == "~"
    assert marks[61] == "~"


def test_proactive_research_wrong_topic_doc_does_not_score_area_c(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "research.md"
    doc.write_text(
        "# Summary\nKora profile framework\n\n# Findings\nInternal memory only\n",
        encoding="utf-8",
    )
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "proactive_research-1",
                "pipeline_name": "proactive_research",
                "state": "completed",
                "intent_duration": "long",
                "parent_session_id": "sess",
                "working_doc_path": str(doc),
                "goal": "Compare Obsidian, Logseq, Anytype, local-first privacy",
            }
        ],
        "worker_tasks": [
            {
                "id": "task-1",
                "pipeline_instance_id": "proactive_research-1",
                "stage_name": "run",
                "state": "completed",
                "task_preset": "long_background",
                "result_summary": "research: report written with 3 sources",
                "cancellation_requested": 0,
            }
        ],
        "ledger_events": [],
        "system_phases_observed": [],
    }

    marks = report._derive_orchestration_markers(evidence)

    assert 61 not in marks


def test_proactive_research_clean_slow_progress_is_partial(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "research.md"
    doc.write_text(
        "---\nstatus: in_progress\n---\n\n# Current Plan\n",
        encoding="utf-8",
    )
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "proactive_research-1",
                "pipeline_name": "proactive_research",
                "state": "running",
                "working_doc_path": str(doc),
            }
        ],
        "worker_tasks": [
            {
                "id": "task-1",
                "pipeline_instance_id": "proactive_research-1",
                "stage_name": "run",
                "state": "paused_for_rate_limit",
                "cancellation_requested": 0,
            }
        ],
        "ledger_events": [],
        "system_phases_observed": [],
    }

    marks = report._derive_orchestration_markers(evidence)

    assert marks[61] == "~"


def test_routine_reminder_partial_requires_reminder_row() -> None:
    evidence = {
        "available": True,
        "pipeline_instances": [
            {
                "id": "continuity_check-1",
                "pipeline_name": "continuity_check",
                "state": "completed",
            }
        ],
        "worker_tasks": [],
        "ledger_events": [],
        "system_phases_observed": [],
        "notification_count": 3,
        "reminder_count": 0,
    }

    marks = report._derive_orchestration_markers(evidence)

    assert 66 not in marks

    evidence["reminder_count"] = 1
    marks = report._derive_orchestration_markers(evidence)

    assert marks[66] == "~"


def test_persona_run_completion_detects_incomplete_phase_set(tmp_path: Path) -> None:
    output_dir = tmp_path / "acceptance_output"
    output_dir.mkdir()
    (output_dir / "persona_agent_summary.json").write_text(
        json.dumps({"status": "completed", "selected_phase_count": 3}),
        encoding="utf-8",
    )
    (output_dir / "persona_agent_events.jsonl").write_text(
        "\n".join([
            json.dumps({"event": "phase_complete", "phase_name": "one"}),
            json.dumps({"event": "phase_complete", "phase_name": "two"}),
        ]),
        encoding="utf-8",
    )

    result = _persona_run_completion(output_dir)

    assert result["present"] is True
    assert result["complete"] is False
    assert result["completed_phase_count"] == 2


def test_vault_benchmark_evidence_marks_entity_and_session_items() -> None:
    kwargs = _base_kwargs()
    kwargs["benchmark_state"] = {
        "vault_entity_pages": 3,
        "vault_moc_pages": 1,
        "vault_sessions": 2,
    }

    marks = _auto_mark_coverage(**kwargs)

    assert marks[55] == "x"
    assert marks[56] == "x"
    assert marks[57] == "x"


def test_current_vault_benchmark_state_counts_configured_memory_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "memory"
    for relative in [
        "Entities/People/Talia.md",
        "Entities/Places/Home.md",
        "Entities/Projects/Kora.md",
        "Maps of Content/Projects.md",
        "Sessions/session-1.md",
    ]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# note\n", encoding="utf-8")

    import kora_v2.core.settings as settings_module

    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(
            memory=SimpleNamespace(kora_memory_path=str(root))
        ),
    )

    state = report._current_vault_benchmark_state()

    assert state == {
        "vault_entity_pages": 3,
        "vault_moc_pages": 1,
        "vault_sessions": 1,
    }


def test_orchestration_start_filter_allows_daemon_startup_grace() -> None:
    assert (
        _with_startup_grace("2026-04-24T22:36:03+00:00", seconds=30)
        == "2026-04-24T22:35:33+00:00"
    )


def test_auth_relay_can_be_marked_from_durable_permission_rows() -> None:
    kwargs = _base_kwargs()
    kwargs["auth_results"] = [
        {"tool": "write_file", "approved": False},
        {"tool": "write_file", "approved": True},
    ]

    marks = _auto_mark_coverage(**kwargs)

    assert marks[17] == "x"


def test_auth_evidence_merge_keeps_denied_log_with_approved_db_rows() -> None:
    merged = _merge_auth_evidence(
        [
            {
                "tool": "write_file",
                "approved": False,
                "decision": "denied",
                "ts": "2026-04-30T08:00:00+00:00",
                "source": "test_log",
            }
        ],
        [
            {
                "tool": "write_file",
                "approved": True,
                "decision": "approved",
                "ts": "2026-04-30T08:01:00+00:00",
                "source": "permission_grants",
            }
        ],
    )

    kwargs = _base_kwargs()
    kwargs["auth_results"] = merged
    marks = _auto_mark_coverage(**kwargs)

    assert [row["approved"] for row in merged] == [False, True]
    assert marks[17] == "x"


def test_policy_grants_filter_to_current_run(
    tmp_path: Path, monkeypatch
) -> None:
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    db_path = db_dir / "operational.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE permission_grants (
                id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                scope TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                decision TEXT NOT NULL,
                granted_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            "INSERT INTO permission_grants "
            "(id, tool_name, scope, risk_level, decision, granted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("old", "write_file", "global", "high", "approved",
             "2026-04-20T00:00:00+00:00"),
        )
        db.execute(
            "INSERT INTO permission_grants "
            "(id, tool_name, scope, risk_level, decision, granted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("new", "write_file", "global", "high", "denied",
             "2026-04-24T22:00:00+00:00"),
        )

    monkeypatch.setattr(report, "_PROJECT_ROOT", tmp_path)

    rows = asyncio.run(
        report._query_policy_grants("2026-04-24T21:00:00+00:00")
    )

    assert [row["decision"] for row in rows] == ["denied"]
