"""Idle-soak manifest evaluator (AT3).

Seeds two hand-built full-state snapshots — ``before`` / ``after`` —
and asserts that :func:`run_manifest` reports pass/fail correctly,
surfaces missing items, notices unexpected items, and honours
``tolerate_missing`` for documented absences.
"""

from __future__ import annotations

from typing import Any

from tests.acceptance.scenario.manifests import (
    SOAK_MANIFESTS,
    SoakManifest,
    run_manifest,
)


def _empty_state() -> dict[str, Any]:
    """Full-state snapshot shell with everything at zero."""
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
                "transitions_total": 0,
                "by_phase": {},
                "current_phase": None,
                "recent_transitions": [],
            },
            "request_limiter": {
                "total_requests_logged": 0,
                "by_class": {},
                "window_seconds": 18000,
                "in_window": 0,
            },
            "open_decisions": {"total": 0, "by_status": {}, "recent": []},
            "runtime_pipelines": {"total": 0, "by_type": {}, "names": []},
        },
        "memory_lifecycle": {
            "available": True,
            "memories": {
                "total": 0, "by_status": {}, "with_consolidated_into": 0,
                "with_merged_from": 0, "recent_active": [],
            },
            "user_model_facts": {"total": 0, "by_status": {}},
            "entities": {"total": 0, "by_type": {}},
            "entity_links": {"total": 0},
        },
        "vault_state": {
            "exists": True, "counts": {"total_notes": 0},
            "working_docs": [], "wikilink_density": {
                "notes_with_wikilinks": 0, "total_wikilinks": 0,
            },
            "folder_hierarchy_present": False,
        },
        "proactive_state": {
            "available": True,
            "notifications": {"total": 0, "by_tier": {}, "by_reason": {}},
            "reminders": {"total": 0, "by_status": {}},
            "insights": {"persisted": False, "total_if_persisted": None},
        },
    }


def _with_pipeline(state: dict[str, Any], name: str, count: int) -> dict[str, Any]:
    pi = state["orchestration_state"]["pipeline_instances"]
    pi["by_name"] = {**pi.get("by_name", {}), name: count}
    pi["total"] = sum(pi["by_name"].values())
    return state


def _with_ledger(state: dict[str, Any], event: str, count: int) -> dict[str, Any]:
    wl = state["orchestration_state"]["work_ledger"]
    wl["by_event_type"] = {**wl.get("by_event_type", {}), event: count}
    wl["total"] = sum(wl["by_event_type"].values())
    return state


def _with_phase(state: dict[str, Any], phase: str, count: int) -> dict[str, Any]:
    ssl = state["orchestration_state"]["system_state_log"]
    ssl["by_phase"] = {**ssl.get("by_phase", {}), phase: count}
    ssl["transitions_total"] = sum(ssl["by_phase"].values())
    return state


def _with_working_doc(state: dict[str, Any], path: str) -> dict[str, Any]:
    docs = list(state["vault_state"].get("working_docs") or [])
    docs.append({"path": path, "pipeline_name": "x", "status": "in_progress"})
    state["vault_state"]["working_docs"] = docs
    return state


def _with_memories(state: dict[str, Any], total: int) -> dict[str, Any]:
    state["memory_lifecycle"]["memories"]["total"] = total
    return state


# ── Pass / fail / tolerate / unexpected ──────────────────────────────────

def test_manifest_pass_when_all_expected_present() -> None:
    manifest = SoakManifest(
        phase_name="phase_alpha",
        min_soak_seconds=10, timeout_seconds=30,
        expected_pipelines=["post_session_memory"],
        expected_ledger_events=["pipeline_started"],
        expected_phase_transitions=["active_idle"],
        expected_memories_min=1,
    )
    before = _empty_state()
    after = _empty_state()
    _with_pipeline(after, "post_session_memory", 1)
    _with_ledger(after, "pipeline_started", 2)
    _with_phase(after, "active_idle", 1)
    _with_memories(after, 3)

    result = run_manifest(manifest, before, after)

    assert result.passed is True
    assert result.checks["pipeline:post_session_memory"] is True
    assert result.checks["ledger:pipeline_started"] is True
    assert result.checks["phase:active_idle"] is True
    assert result.checks["memories_min"] is True
    assert result.missing == []
    assert "PASS" in result.summary


def test_manifest_fail_when_pipeline_missing() -> None:
    manifest = SoakManifest(
        phase_name="phase_beta",
        min_soak_seconds=10, timeout_seconds=30,
        expected_pipelines=["post_session_memory", "post_memory_vault"],
    )
    before = _empty_state()
    after = _empty_state()
    # Only post_session_memory fired.
    _with_pipeline(after, "post_session_memory", 1)

    result = run_manifest(manifest, before, after)

    assert result.passed is False
    assert "pipeline:post_memory_vault" in result.missing
    assert result.checks["pipeline:post_session_memory"] is True
    assert result.checks["pipeline:post_memory_vault"] is False


def test_manifest_fail_when_ledger_event_missing() -> None:
    manifest = SoakManifest(
        phase_name="phase_gamma",
        min_soak_seconds=10, timeout_seconds=30,
        expected_ledger_events=["pipeline_started", "task_completed"],
    )
    before = _empty_state()
    after = _empty_state()
    _with_ledger(after, "pipeline_started", 1)

    result = run_manifest(manifest, before, after)
    assert result.passed is False
    assert "ledger:task_completed" in result.missing


def test_manifest_reports_unexpected_items() -> None:
    """State has pipelines that weren't declared — still passes but surfaces them."""
    manifest = SoakManifest(
        phase_name="phase_delta",
        min_soak_seconds=10, timeout_seconds=30,
        expected_pipelines=["post_session_memory"],
    )
    before = _empty_state()
    after = _empty_state()
    _with_pipeline(after, "post_session_memory", 1)
    _with_pipeline(after, "surprise_pipeline", 2)
    _with_ledger(after, "other_event", 3)

    result = run_manifest(manifest, before, after)
    assert result.passed is True
    assert "pipeline:surprise_pipeline" in result.unexpected
    assert "ledger:other_event" in result.unexpected


def test_manifest_tolerates_listed_absences() -> None:
    """``tolerate_missing`` entries don't fail the manifest when absent."""
    manifest = SoakManifest(
        phase_name="phase_eps",
        min_soak_seconds=10, timeout_seconds=30,
        expected_pipelines=["session_bridge_pruning"],
        expected_phase_transitions=["deep_idle"],
        tolerate_missing=["session_bridge_pruning", "deep_idle"],
    )
    before = _empty_state()
    after = _empty_state()

    result = run_manifest(manifest, before, after)
    assert result.passed is True  # both absences tolerated
    # Checks still record False for documentation
    assert result.checks["pipeline:session_bridge_pruning"] is False
    assert result.checks["phase:deep_idle"] is False
    assert result.missing == []


def test_manifest_working_docs_min_threshold() -> None:
    """expected_working_docs_min counts NEW paths between before and after."""
    manifest = SoakManifest(
        phase_name="phase_zeta",
        min_soak_seconds=10, timeout_seconds=30,
        expected_working_docs_min=2,
    )
    before = _empty_state()
    _with_working_doc(before, "/inbox/pre.md")  # already existed
    after = _empty_state()
    _with_working_doc(after, "/inbox/pre.md")  # unchanged
    _with_working_doc(after, "/inbox/new1.md")
    _with_working_doc(after, "/inbox/new2.md")

    result = run_manifest(manifest, before, after)
    assert result.passed is True
    assert result.checks["working_docs_min"] is True


def test_manifest_notifications_threshold() -> None:
    """expected_notifications_min respects the before/after delta."""
    manifest = SoakManifest(
        phase_name="phase_eta",
        min_soak_seconds=10, timeout_seconds=30,
        expected_notifications_min=1,
    )
    before = _empty_state()
    after = _empty_state()
    after["proactive_state"]["notifications"]["total"] = 2
    before["proactive_state"]["notifications"]["total"] = 0

    result = run_manifest(manifest, before, after)
    assert result.passed is True


# ── Registry sanity ──────────────────────────────────────────────────────

def test_soak_manifests_non_empty() -> None:
    assert len(SOAK_MANIFESTS) >= 3
    for name, manifest in SOAK_MANIFESTS.items():
        assert manifest.phase_name == name
        assert manifest.min_soak_seconds > 0
        assert manifest.timeout_seconds >= manifest.min_soak_seconds
