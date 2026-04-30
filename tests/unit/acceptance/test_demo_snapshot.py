"""Acceptance demo snapshot export contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from tests.acceptance.demo_snapshot import (
    build_sanitized_transcript,
    sanitize_for_demo,
    write_acceptance_exports,
)


def test_sanitize_for_demo_redacts_local_paths_and_secrets(
    tmp_path: Path,
) -> None:
    value = {
        "path": "/Users/mobi/Documents/GitHub/Kora/data/.api_token",
        "Authorization": "Bearer abc.def-123",
        "nested": (
            "token='super-secret' lives in "
            f"/tmp/claude/kora_acceptance/out and {tmp_path}/artifact.json"
        ),
    }

    sanitized = sanitize_for_demo(value)
    blob = json.dumps(sanitized)

    assert "/Users/mobi" not in blob
    assert "/tmp/claude/kora_acceptance" not in blob
    assert str(tmp_path) not in blob
    assert "abc.def-123" not in blob
    assert "super-secret" not in blob
    assert "<redacted>" in blob


def test_write_acceptance_exports_writes_full_transcript_and_gui_contract(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "acceptance_output"
    snapshots_dir = output_dir / "snapshots"
    snapshots_dir.mkdir(parents=True)
    (snapshots_dir / "final.json").write_text(
        json.dumps(
            {
                "name": "final",
                "captured_at": "2026-04-29T15:00:00+00:00",
                "conversation": {"message_count": 4, "last_3": []},
                "status": {"status": "ok"},
                "memory_lifecycle": {"memories": {"total": 2}},
                "vault_state": {"files": {"total": 1}},
            }
        ),
        encoding="utf-8",
    )
    session_state = {
        "started_at": "2026-04-29T12:00:00+00:00",
        "current_day": 1,
        "simulated_hours_offset": 3,
        "messages": [
            {
                "role": "user",
                "content": "Please bridge tomorrow from /Users/mobi/private.txt",
                "ts": "2026-04-29T12:01:00+00:00",
            },
            {
                "role": "assistant",
                "content": "I saved the future self bridge.",
                "ts": "2026-04-29T12:02:00+00:00",
                "tool_calls": ["bridge_tomorrow"],
                "trace_id": "trace-1",
                "latency_ms": 12,
            },
        ],
    }
    report_path = output_dir / "acceptance_report.md"
    report_path.write_text("# report\n", encoding="utf-8")

    paths = write_acceptance_exports(
        session_state=session_state,
        snapshots_dir=snapshots_dir,
        output_dir=output_dir,
        life_data={
            "available": True,
            "medication_count": 1,
            "meal_count": 1,
            "reminder_count": 1,
            "records": {
                "calendar_entries": [
                    {
                        "id": "cal-1",
                        "kind": "class",
                        "title": "Biology lecture",
                        "starts_at": "2026-04-29T14:00:00+00:00",
                        "ends_at": "2026-04-29T15:15:00+00:00",
                        "status": "active",
                    }
                ],
                "day_plan_entries": [
                    {
                        "id": "dpe-1",
                        "title": "Lunch",
                        "entry_type": "meal",
                        "intended_start": "2026-04-29T16:00:00+00:00",
                        "status": "done",
                        "reality_state": "confirmed_done",
                    }
                ],
                "domain_events": [
                    {
                        "id": "de-1",
                        "event_type": "DAY_PLAN_REPAIRED",
                        "created_at": "2026-04-29T17:00:00+00:00",
                        "payload": '{"path": "/Users/mobi/nope"}',
                    }
                ],
                "plan_repair_actions": [],
            },
        },
        orch_evidence={
            "available": True,
            "session_transcripts": 1,
            "signal_queue_count": 2,
            "notes_total": 3,
            "entities_total": 4,
        },
        tool_usage={
            "tool_counts": {"bridge_tomorrow": 1},
            "memory": [],
            "total": 1,
            "unique": 1,
        },
        cap_health={},
        life_os_summary=None,
        coverage_summary={
            "active": {"satisfied": 1, "partial": 0, "total": 1},
            "items": [],
        },
        report_path=report_path,
    )

    assert paths["conversation_json"].exists()
    assert paths["conversation_markdown"].exists()
    assert paths["demo_snapshot"].exists()

    conversation = json.loads(paths["conversation_json"].read_text())
    assert conversation["message_count"] == 2
    assert len(conversation["messages"]) == 2

    snapshot = json.loads(paths["demo_snapshot"].read_text())
    assert set(snapshot) == {
        "demo_meta",
        "persona",
        "today",
        "calendar",
        "confirm_reality",
        "repair",
        "tomorrow_bridge",
        "memory",
        "conversation",
        "acceptance_proof",
    }
    assert snapshot["demo_meta"]["live_daemon_required"] is False
    assert snapshot["conversation"]["message_count"] == 2
    assert snapshot["calendar"]["events"][0]["title"] == "Biology lecture"
    assert snapshot["confirm_reality"]["entries"][0]["id"] == "dpe-1"

    blob = paths["demo_snapshot"].read_text()
    assert "/Users/mobi" not in blob
    assert str(tmp_path) not in blob
    assert "not connected to your local daemon" in blob


def test_build_sanitized_transcript_keeps_every_message() -> None:
    session_state = {
        "messages": [
            {"role": "user", "content": f"turn {idx}", "ts": "now"}
            for idx in range(25)
        ]
    }

    transcript = build_sanitized_transcript(session_state)

    assert len(transcript) == 25
    assert transcript[-1]["content"] == "turn 24"
