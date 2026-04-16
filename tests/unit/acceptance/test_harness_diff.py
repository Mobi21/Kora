"""AT2 snapshot-diff helpers on ``HarnessServer``.

Each test crafts two snapshot dicts that differ in exactly one AT2
dimension, invokes :meth:`HarnessServer.cmd_diff` via the on-disk path
the real harness uses, and asserts the diff output contains the
expected section + delta line.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tests.acceptance import _harness_server as hs_mod
from tests.acceptance._harness_server import HarnessServer


def _write_snapshot(
    snapshots_dir: Path, name: str, payload: dict[str, Any],
) -> None:
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    path = snapshots_dir / f"{name}.json"
    path.write_text(json.dumps(payload, default=str))


def _base_snapshot(name: str, captured_at: str = "2026-04-15T00:00:00+00:00") -> dict[str, Any]:
    """Return a minimal snapshot shell that :meth:`cmd_diff` tolerates."""
    return {
        "name": name,
        "captured_at": captured_at,
        "simulated_hours": 0,
        "conversation": {"message_count": 0, "last_3": []},
        "inspect_doctor": {"summary": "ok"},
        "inspect_phase-audit": {"summary": "ok"},
        "inspect_trace": {"trace_count": 0},
        "inspect_permissions": {"grant_count": 0},
        "autonomous_state": {"available": False},
        "orchestration_state": {"available": False},
        "memory_lifecycle": {"available": False},
        "vault_state": {"exists": False},
        "proactive_state": {"available": False},
    }


def _run_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    s1: dict[str, Any], s2: dict[str, Any],
) -> str:
    """Write s1/s2 to tmp snapshots dir and run cmd_diff; return diff text."""
    snapshots_dir = tmp_path / "snapshots"
    output_dir = tmp_path / "out"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(hs_mod, "SNAPSHOTS_DIR", snapshots_dir)
    monkeypatch.setattr(hs_mod, "OUTPUT_DIR", output_dir)

    _write_snapshot(snapshots_dir, "a", s1)
    _write_snapshot(snapshots_dir, "b", s2)

    harness = HarnessServer()
    result = asyncio.run(harness.cmd_diff("a", "b"))
    assert "diff" in result, f"cmd_diff missing 'diff': {result}"
    return result["diff"]


# ── test_diff_reports_pipeline_deltas ────────────────────────────────────


def test_diff_reports_pipeline_deltas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    s1 = _base_snapshot("a")
    s1["orchestration_state"] = {
        "available": True,
        "pipeline_instances": {
            "total": 1,
            "by_state": {"running": 1},
            "by_name": {"post_session_memory": 1},
            "recent": [],
        },
        "worker_tasks": {"total": 1, "by_lifecycle": {"running": 1}, "active_count": 1},
        "work_ledger": {"total": 1, "by_event_type": {"pipeline_started": 1}, "recent": []},
        "system_state_log": {"transitions_total": 0, "by_phase": {}, "current_phase": None, "recent_transitions": []},
        "request_limiter": {"total_requests_logged": 0, "by_class": {}, "window_seconds": 18000, "in_window": 0},
    }

    s2 = _base_snapshot("b", captured_at="2026-04-15T01:00:00+00:00")
    s2["orchestration_state"] = {
        "available": True,
        "pipeline_instances": {
            "total": 3,
            "by_state": {"running": 1, "completed": 2},
            "by_name": {
                "post_session_memory": 2,
                "post_memory_vault": 1,
            },
            "recent": [],
        },
        "worker_tasks": {"total": 4, "by_lifecycle": {"completed": 3, "running": 1}, "active_count": 1},
        "work_ledger": {"total": 5, "by_event_type": {"pipeline_started": 3, "pipeline_completed": 2}, "recent": []},
        "system_state_log": {"transitions_total": 0, "by_phase": {}, "current_phase": None, "recent_transitions": []},
        "request_limiter": {"total_requests_logged": 0, "by_class": {}, "window_seconds": 18000, "in_window": 0},
    }

    diff = _run_diff(tmp_path, monkeypatch, s1, s2)

    assert "## Orchestration (Phase 7.5)" in diff
    assert "Pipeline instances: 1 \u2192 3 (\u0394+2)" in diff
    assert "pipeline_instances[state=completed]: 0 \u2192 2 (\u0394+2)" in diff
    assert "pipeline_instances[name=post_memory_vault]" in diff
    assert "Worker tasks: 1 \u2192 4 (\u0394+3)" in diff


# ── test_diff_reports_ledger_deltas ──────────────────────────────────────


def test_diff_reports_ledger_deltas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    s1 = _base_snapshot("a")
    s1["orchestration_state"] = {
        "available": True,
        "work_ledger": {
            "total": 2,
            "by_event_type": {"pipeline_started": 1, "task_started": 1},
            "recent": [],
        },
    }
    s2 = _base_snapshot("b", captured_at="2026-04-15T02:00:00+00:00")
    s2["orchestration_state"] = {
        "available": True,
        "work_ledger": {
            "total": 7,
            "by_event_type": {
                "pipeline_started": 2,
                "task_started": 2,
                "task_completed": 2,
                "pipeline_completed": 1,
            },
            "recent": [],
        },
    }

    diff = _run_diff(tmp_path, monkeypatch, s1, s2)

    assert "Work ledger events: 2 \u2192 7 (\u0394+5)" in diff
    assert "work_ledger[task_completed]: 0 \u2192 2 (\u0394+2)" in diff
    assert "work_ledger[pipeline_completed]: 0 \u2192 1 (\u0394+1)" in diff


# ── test_diff_reports_phase_transitions ──────────────────────────────────


def test_diff_reports_phase_transitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    s1 = _base_snapshot("a")
    s1["orchestration_state"] = {
        "available": True,
        "system_state_log": {
            "transitions_total": 2,
            "by_phase": {"active_idle": 2},
            "current_phase": "active_idle",
            "recent_transitions": [],
        },
    }
    s2 = _base_snapshot("b", captured_at="2026-04-15T03:00:00+00:00")
    s2["orchestration_state"] = {
        "available": True,
        "system_state_log": {
            "transitions_total": 5,
            "by_phase": {"active_idle": 2, "light_idle": 2, "deep_idle": 1},
            "current_phase": "deep_idle",
            "recent_transitions": [],
        },
    }

    diff = _run_diff(tmp_path, monkeypatch, s1, s2)

    assert "Phase transitions: 2 \u2192 5 (\u0394+3)" in diff
    assert "Current phase: active_idle \u2192 deep_idle" in diff


# ── test_diff_reports_memory_lifecycle_deltas ────────────────────────────


def test_diff_reports_memory_lifecycle_deltas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    s1 = _base_snapshot("a")
    s1["memory_lifecycle"] = {
        "available": True,
        "memories": {
            "total": 3,
            "by_status": {"active": 3},
            "with_consolidated_into": 0,
            "with_merged_from": 0,
            "recent_active": [],
        },
        "user_model_facts": {"total": 1, "by_status": {"active": 1}},
        "entities": {"total": 2, "by_type": {"person": 2}},
        "entity_links": {"total": 1},
        "sessions": {"transcripts_total": 1, "processed": 1, "unprocessed": 0},
        "signal_queue": {"total": 0, "by_status": {}},
        "dedup_rejected_pairs": {"total": 0},
    }
    s2 = _base_snapshot("b", captured_at="2026-04-15T04:00:00+00:00")
    s2["memory_lifecycle"] = {
        "available": True,
        "memories": {
            "total": 5,
            "by_status": {"active": 4, "consolidated": 1},
            "with_consolidated_into": 1,
            "with_merged_from": 1,
            "recent_active": [],
        },
        "user_model_facts": {"total": 2, "by_status": {"active": 2}},
        "entities": {"total": 3, "by_type": {"person": 2, "project": 1}},
        "entity_links": {"total": 3},
        "sessions": {"transcripts_total": 2, "processed": 2, "unprocessed": 0},
        "signal_queue": {"total": 1, "by_status": {"pending": 1}},
        "dedup_rejected_pairs": {"total": 2},
    }

    diff = _run_diff(tmp_path, monkeypatch, s1, s2)

    assert "## Memory lifecycle (Phase 8)" in diff
    assert "memories: 3 \u2192 5 (\u0394+2)" in diff
    assert "memories[consolidated]: 0 \u2192 1 (\u0394+1)" in diff
    assert "entities: 2 \u2192 3 (\u0394+1)" in diff
    assert "entity_links: 1 \u2192 3 (\u0394+2)" in diff
    assert "session_transcripts: 1 \u2192 2 (\u0394+1)" in diff
    assert "dedup_rejected_pairs: 0 \u2192 2 (\u0394+2)" in diff


# ── test_diff_reports_vault_deltas ───────────────────────────────────────


def test_diff_reports_vault_deltas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    s1 = _base_snapshot("a")
    s1["vault_state"] = {
        "root": "/vault",
        "exists": True,
        "counts": {
            "total_notes": 3,
            "long_term_episodic": 1,
            "inbox": 1,
            "sessions": 1,
        },
        "working_docs": [
            {"path": "/vault/Inbox/stale.md", "pipeline_name": "post_session_memory"},
        ],
        "wikilink_density": {"notes_with_wikilinks": 1, "total_wikilinks": 2},
        "folder_hierarchy_present": True,
    }
    s2 = _base_snapshot("b", captured_at="2026-04-15T05:00:00+00:00")
    s2["vault_state"] = {
        "root": "/vault",
        "exists": True,
        "counts": {
            "total_notes": 6,
            "long_term_episodic": 2,
            "inbox": 2,
            "sessions": 2,
            "moc_pages": 1,
        },
        "working_docs": [
            {"path": "/vault/Inbox/new1.md", "pipeline_name": "post_session_memory"},
            {"path": "/vault/Inbox/new2.md", "pipeline_name": "post_memory_vault"},
        ],
        "wikilink_density": {"notes_with_wikilinks": 3, "total_wikilinks": 8},
        "folder_hierarchy_present": True,
    }

    diff = _run_diff(tmp_path, monkeypatch, s1, s2)

    assert "## Vault (_KoraMemory/)" in diff
    assert "notes[total_notes]: 3 \u2192 6 (\u0394+3)" in diff
    assert "notes[long_term_episodic]: 1 \u2192 2 (\u0394+1)" in diff
    assert "notes[moc_pages]: 0 \u2192 1 (\u0394+1)" in diff
    # Working doc appeared/disappeared buckets
    assert "working docs appeared: 2" in diff
    assert "/vault/Inbox/new1.md" in diff
    assert "working docs disappeared: 1" in diff
    assert "/vault/Inbox/stale.md" in diff


# Pytest collection marker for direct runs.
if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
