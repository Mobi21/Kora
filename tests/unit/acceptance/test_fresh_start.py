from __future__ import annotations

import json
from pathlib import Path

import pytest

import tests.acceptance._harness_server as harness_server
from tests.acceptance import automated
from tests.acceptance._harness_server import (
    HarnessServer,
    _clean_acceptance_persona_residue,
    _reset_daemon_runtime_files,
)


def _patch_acceptance_paths(
    monkeypatch,
    *,
    project_root: Path,
    accept_dir: Path,
    persistent_memory_root: Path | None = None,
) -> None:
    output_dir = accept_dir / "acceptance_output"
    monkeypatch.setattr(automated, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(automated, "ACCEPT_DIR", accept_dir)
    monkeypatch.setattr(automated, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(automated, "SNAPSHOTS_DIR", output_dir / "snapshots")
    monkeypatch.setattr(automated, "SESSION_FILE", accept_dir / "acceptance_session.json")
    monkeypatch.setattr(automated, "MONITOR_FILE", output_dir / "acceptance_monitor.md")
    monkeypatch.setattr(automated, "HARNESS_SOCK", accept_dir / "harness.sock")
    monkeypatch.setattr(automated, "HARNESS_PID_FILE", accept_dir / "harness.pid")
    monkeypatch.setattr(automated, "LOCKFILE", project_root / "data" / "kora.lock")
    monkeypatch.setattr(automated, "TOKEN_FILE", project_root / "data" / ".api_token")
    if persistent_memory_root is not None:
        monkeypatch.setattr(
            automated,
            "PERSISTENT_MEMORY_ROOT",
            persistent_memory_root,
        )


def test_reset_acceptance_artifacts_removes_scratch_and_memory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "repo"
    accept_dir = tmp_path / "kora_acceptance"
    _patch_acceptance_paths(monkeypatch, project_root=project_root, accept_dir=accept_dir)

    stale_memory = accept_dir / "memory" / "Long-Term" / "old.md"
    stale_output = accept_dir / "acceptance_output" / "old.json"
    stale_memory.parent.mkdir(parents=True)
    stale_output.parent.mkdir(parents=True)
    stale_memory.write_text("old acceptance memory", encoding="utf-8")
    stale_output.write_text("{}", encoding="utf-8")

    result = automated._reset_acceptance_artifacts()

    assert result["acceptance_dir_removed"] is True
    assert not stale_memory.exists()
    assert not stale_output.exists()
    assert (accept_dir / "acceptance_output" / "snapshots").exists()


def test_seed_run_start_marks_first_run_required(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "repo"
    accept_dir = tmp_path / "kora_acceptance"
    persistent = tmp_path / "persistent-memory"
    _patch_acceptance_paths(
        monkeypatch,
        project_root=project_root,
        accept_dir=accept_dir,
        persistent_memory_root=persistent,
    )
    automated._ensure_dirs()

    automated._seed_run_start(
        fast=True,
        clean_start={"runtime_state": {"identity_files": {"thread_id": "removed"}}},
    )

    state = json.loads((accept_dir / "acceptance_session.json").read_text())
    assert state["mode"] == "fast"
    assert state["messages"] == []
    assert state["first_run"]["required"] is True
    assert state["first_run"]["status"] == "required_pending"
    assert state["clean_start"]["required"] is True
    assert state["clean_start"]["isolated_memory_root"] == str(accept_dir / "memory")
    assert state["clean_start"]["runtime_state"]["identity_files"]["thread_id"] == "removed"


def test_clean_acceptance_persona_residue_quarantines_only_matching_notes(
    tmp_path: Path,
) -> None:
    memory_root = tmp_path / "memory"
    quarantine = tmp_path / "quarantine"
    old_note = memory_root / "Long-Term" / "old-jordan.md"
    real_note = memory_root / "Long-Term" / "real-note.md"
    old_note.parent.mkdir(parents=True)
    old_note.write_text(
        "Jordan asked Kora acceptance to remember Alex, Mochi, Adderall, "
        "trusted support, and local-first preferences.",
        encoding="utf-8",
    )
    real_note.write_text(
        "Jordan is a common name in this unrelated note.",
        encoding="utf-8",
    )

    result = _clean_acceptance_persona_residue(memory_root, quarantine)

    assert result["status"] == "ok"
    assert result["quarantined_count"] == 1
    assert not old_note.exists()
    assert real_note.exists()
    moved_to = Path(result["quarantined_files"][0]["to"])
    assert moved_to.exists()
    assert moved_to.read_text(encoding="utf-8").startswith("Jordan asked")


def test_reset_daemon_runtime_files_reports_removed_and_absent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "thread_id").write_text("old-thread", encoding="utf-8")

    result = _reset_daemon_runtime_files(data_dir)

    assert result == {"thread_id": "removed", "session_id": "absent"}
    assert not (data_dir / "thread_id").exists()
    assert not (data_dir / "session_id").exists()


@pytest.mark.asyncio
async def test_clean_start_status_command_reports_first_run_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    accept_dir = tmp_path / "kora_acceptance"
    output_dir = accept_dir / "acceptance_output"
    session_file = accept_dir / "acceptance_session.json"
    monkeypatch.setattr(harness_server, "ACCEPT_DIR", accept_dir)
    monkeypatch.setattr(harness_server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(harness_server, "SNAPSHOTS_DIR", output_dir / "snapshots")
    monkeypatch.setattr(harness_server, "SESSION_FILE", session_file)
    output_dir.mkdir(parents=True)
    (accept_dir / "memory").mkdir()
    session_file.write_text(
        json.dumps({
            "run_id": "acceptance-test",
            "started_at": "2026-04-30T00:00:00+00:00",
            "mode": "full",
            "messages": [],
            "first_run": {"required": True, "status": "required_pending"},
            "clean_start": {
                "required": True,
                "isolated_memory_root": str(accept_dir / "memory"),
            },
        }),
        encoding="utf-8",
    )

    result = await HarnessServer().cmd_clean_start_status()

    assert result["run_id"] == "acceptance-test"
    assert result["first_run"]["required"] is True
    assert result["checks"]["memory_root_is_isolated"] is True
    assert result["checks"]["messages_count"] == 0
