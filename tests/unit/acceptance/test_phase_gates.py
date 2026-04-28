"""Phase gate runner (AT3).

Each test hand-builds a minimal full-state snapshot and asserts
:func:`run_phase_gate` reports the expected satisfied / missing items.
Conversation-based items are silently skipped — not counted in
``items_checked`` — so the tests only exercise the state-evidence path.
"""

from __future__ import annotations

from typing import Any

from tests.acceptance.scenario.gates import run_phase_gate


def _base_state() -> dict[str, Any]:
    return {
        "orchestration_state": {
            "available": True,
            "pipeline_instances": {
                "total": 0, "by_state": {}, "by_name": {}, "recent": [],
            },
            "worker_tasks": {"total": 0, "by_lifecycle": {}, "active_count": 0},
            "work_ledger": {"total": 0, "by_event_type": {}, "recent": []},
            "trigger_state": {"total_triggers_tracked": 0, "last_fires": []},
            "system_state_log": {
                "transitions_total": 0, "by_phase": {},
                "current_phase": None, "recent_transitions": [],
            },
            "request_limiter": {"by_class": {}},
            "open_decisions": {"total": 0, "by_status": {}, "recent": []},
            "runtime_pipelines": {"total": 0},
        },
        "memory_lifecycle": {
            "memories": {
                "total": 0, "by_status": {}, "with_consolidated_into": 0,
                "with_merged_from": 0,
            },
            "user_model_facts": {"total": 0},
            "entities": {"total": 0},
        },
        "vault_state": {
            "exists": True, "counts": {},
            "working_docs": [],
            "wikilink_density": {"notes_with_wikilinks": 0, "total_wikilinks": 0},
            "folder_hierarchy_present": False,
        },
        "proactive_state": {
            "notifications": {"total": 0, "by_tier": {}, "by_reason": {}},
            "reminders": {"total": 0, "by_status": {}},
            "insights": {"persisted": False, "total_if_persisted": None},
        },
    }


def _add_pipeline(
    state: dict[str, Any], name: str, count: int,
    *, state_str: str = "completed",
) -> None:
    pi = state["orchestration_state"]["pipeline_instances"]
    pi["by_name"] = {**pi["by_name"], name: count}
    pi["total"] = sum(pi["by_name"].values())
    # Add a matching recent row so _pipeline_completed_for() sees it.
    recent = list(pi.get("recent") or [])
    recent.append({"pipeline_name": name, "state": state_str})
    pi["recent"] = recent
    # Also bump by_state counter
    bs = pi.get("by_state") or {}
    bs[state_str] = int(bs.get(state_str, 0) or 0) + 1
    pi["by_state"] = bs


# ── Individual-item regressions ──────────────────────────────────────────

def test_gate_satisfies_item_24_on_phase_transition() -> None:
    state = _base_state()
    state["orchestration_state"]["system_state_log"]["transitions_total"] = 3
    result = run_phase_gate("planning_idle", [24], state)
    assert result.items_checked == [24]
    assert result.items_satisfied == [24]
    assert result.items_missing == []
    assert "transitions_total" in result.details[24]


def test_gate_satisfies_item_25_on_long_background_dispatch() -> None:
    state = _base_state()
    _add_pipeline(state, "user_autonomous_task", 1, state_str="running")
    result = run_phase_gate("long_background_dispatch", [25], state)
    assert result.items_satisfied == [25]
    assert result.items_missing == []


def test_gate_satisfies_items_47_51_on_memory_steward_state() -> None:
    """Memory Steward items fire once the handlers have written state."""
    state = _base_state()
    _add_pipeline(state, "post_session_memory", 1, state_str="completed")
    _add_pipeline(state, "weekly_adhd_profile", 1, state_str="completed")
    state["memory_lifecycle"]["memories"]["total"] = 5
    state["memory_lifecycle"]["memories"]["with_consolidated_into"] = 2
    state["memory_lifecycle"]["memories"]["by_status"] = {
        "active": 3, "consolidated": 1, "soft_deleted": 1,
    }
    state["memory_lifecycle"]["entities"]["total"] = 2
    state["memory_lifecycle"]["entities"]["with_merged_from"] = 1
    state["memory_lifecycle"]["user_model_facts"]["total"] = 3

    result = run_phase_gate(
        "memory_steward_verification", [47, 48, 49, 50, 51], state,
    )
    assert set(result.items_satisfied) == {47, 48, 49, 50, 51}
    assert result.items_missing == []


def test_gate_satisfies_item_48_on_user_model_fact_consolidation() -> None:
    state = _base_state()
    _add_pipeline(state, "post_session_memory", 1, state_str="completed")
    state["memory_lifecycle"]["memories"]["total"] = 1
    state["memory_lifecycle"]["user_model_facts"]["total"] = 3
    state["memory_lifecycle"]["user_model_facts"]["with_consolidated_into"] = 2

    result = run_phase_gate("memory_steward_verification", [48], state)

    assert result.items_satisfied == [48]
    assert result.items_missing == []


def test_gate_satisfies_items_52_57_on_vault_organizer_state() -> None:
    """Vault Organizer items fire once the vault filesystem shows the work."""
    state = _base_state()
    _add_pipeline(state, "post_memory_vault", 1, state_str="completed")
    state["vault_state"]["folder_hierarchy_present"] = True
    state["vault_state"]["counts"] = {
        "total_notes": 20,
        "inbox": 1,
        "long_term_episodic": 5,
        "entities_people": 2,
        "entities_places": 1,
        "entities_projects": 1,
        "moc_pages": 2,
        "sessions": 3,
    }
    state["vault_state"]["wikilink_density"] = {
        "notes_with_wikilinks": 10, "total_wikilinks": 25,
    }

    result = run_phase_gate(
        "vault_organizer_verification", [52, 53, 54, 55, 56, 57], state,
    )
    assert set(result.items_satisfied) == {52, 53, 54, 55, 56, 57}
    assert result.items_missing == []


def test_gate_reports_missing_items() -> None:
    """Items without state evidence land in ``items_missing`` with detail."""
    state = _base_state()
    # Nothing to satisfy item 40 (wake_up_preparation).
    result = run_phase_gate("late_idle", [40, 24], state)
    assert 40 in result.items_missing
    # 24 fails because transitions_total == 0
    assert 24 in result.items_missing


def test_gate_skips_conversation_only_items() -> None:
    """Item 2 (context mentions) is message-based — gate silently skips it."""
    state = _base_state()
    result = run_phase_gate("first_launch", [2, 24], state)
    # Only 24 is state-based; 2 is skipped.
    assert result.items_checked == [24]
    assert 2 not in result.items_checked


# ── Determinism ──────────────────────────────────────────────────────────

def test_gate_is_deterministic() -> None:
    state = _base_state()
    _add_pipeline(state, "post_session_memory", 1, state_str="completed")
    state["memory_lifecycle"]["memories"]["total"] = 1

    a = run_phase_gate("phase_x", [47], state)
    b = run_phase_gate("phase_x", [47], state)
    assert a.items_checked == b.items_checked
    assert a.items_satisfied == b.items_satisfied
    assert a.items_missing == b.items_missing
    assert a.details == b.details


# ── Unknown phase name error ─────────────────────────────────────────────

def test_cmd_phase_gate_errors_on_unknown_phase_name() -> None:
    """cmd_phase_gate must surface an error for an unknown phase name.

    Silent "0 checked / 0 missing" would look identical to a real pass
    and mask typos / stale phase names in callers, so the harness
    command is expected to return an explicit error payload when the
    phase_name doesn't resolve to any declared phase in WEEK_PLAN or
    FAST_PLAN.
    """
    import asyncio

    from tests.acceptance._harness_server import HarnessServer

    harness = HarnessServer()
    result = asyncio.run(
        harness.cmd_phase_gate("nonexistent_phase_xyz")
    )
    assert "error" in result
    assert "phase_not_found" in result["error"]
    # Known phase list should be non-empty since WEEK_PLAN/FAST_PLAN
    # carry real phases.
    assert isinstance(result.get("known_phases"), list)
    assert len(result["known_phases"]) > 0


def test_cmd_phase_gate_accepts_explicit_coverage_items() -> None:
    """When coverage_items is passed explicitly, unknown phase is fine.

    Callers that pre-resolve coverage items should not hit the
    unknown-phase guard — the guard only fires when we have to look up
    the items ourselves.
    """
    import asyncio

    from tests.acceptance._harness_server import HarnessServer

    harness = HarnessServer()
    result = asyncio.run(
        harness.cmd_phase_gate("any_name", coverage_items=[24])
    )
    # No error — the gate runs against the provided items.
    assert "error" not in result
    assert "result" in result
