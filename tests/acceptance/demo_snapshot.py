"""Sanitized acceptance transcript and demo-snapshot exports.

The browser demo consumes these artifacts instead of calling a live daemon.
Everything here is derived from acceptance session state, captured snapshots,
and report-time DB evidence.
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).parents[2].resolve()

_SECRET_KEY_RE = re.compile(r"(token|secret|password|api[_-]?key|auth)", re.I)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b(token|secret|password|api[_-]?key)\b([\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+"
)
_HOME_PATH_RE = re.compile(r"/Users/[^\\s\"'`),]+")
_LOCAL_TMP_PATH_RE = re.compile(r"/(?:private/)?(?:tmp|var/folders)/[^\\s\"'`),]+")
_TMP_ACCEPTANCE_RE = re.compile(r"/tmp/claude/kora_acceptance[^\\s\"'`),]*")


def sanitize_for_demo(value: Any) -> Any:
    """Return a JSON-safe value with local paths and secret-like fields redacted."""

    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _SECRET_KEY_RE.search(key_str):
                sanitized[key_str] = "<redacted>"
            else:
                sanitized[key_str] = sanitize_for_demo(item)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_demo(item) for item in value]
    if isinstance(value, Path):
        return _sanitize_string(str(value))
    if isinstance(value, str):
        return _sanitize_string(value)
    return value


def write_acceptance_exports(
    *,
    session_state: dict[str, Any],
    snapshots_dir: Path,
    output_dir: Path,
    life_data: dict[str, Any],
    orch_evidence: dict[str, Any],
    tool_usage: dict[str, Any],
    cap_health: dict[str, Any],
    life_os_summary: Any,
    coverage_summary: dict[str, Any],
    report_path: Path,
) -> dict[str, Path]:
    """Write full transcript JSON/Markdown plus GUI-shaped demo snapshot."""

    output_dir.mkdir(parents=True, exist_ok=True)
    transcript = build_sanitized_transcript(session_state)

    transcript_json_path = output_dir / "acceptance_conversation.json"
    transcript_json_path.write_text(
        json.dumps(
            {
                "schema_version": "acceptance.conversation.v1",
                "generated_at": datetime.now(UTC).isoformat(),
                "message_count": len(transcript),
                "messages": transcript,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    transcript_md_path = output_dir / "acceptance_conversation.md"
    transcript_md_path.write_text(
        render_transcript_markdown(session_state, transcript),
        encoding="utf-8",
    )

    demo_snapshot = build_demo_snapshot(
        session_state=session_state,
        snapshots_dir=snapshots_dir,
        output_dir=output_dir,
        transcript=transcript,
        transcript_json_path=transcript_json_path,
        transcript_md_path=transcript_md_path,
        life_data=life_data,
        orch_evidence=orch_evidence,
        tool_usage=tool_usage,
        cap_health=cap_health,
        life_os_summary=life_os_summary,
        coverage_summary=coverage_summary,
        report_path=report_path,
    )
    demo_snapshot_path = output_dir / "acceptance_demo_snapshot.json"
    demo_snapshot_path.write_text(
        json.dumps(sanitize_for_demo(demo_snapshot), indent=2, default=str),
        encoding="utf-8",
    )

    return {
        "conversation_json": transcript_json_path,
        "conversation_markdown": transcript_md_path,
        "demo_snapshot": demo_snapshot_path,
    }


def build_sanitized_transcript(
    session_state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert full harness messages into a stable transcript contract."""

    messages = session_state.get("messages") or []
    transcript: list[dict[str, Any]] = []
    for index, message in enumerate(messages, start=1):
        role = str(message.get("role") or "unknown")
        entry: dict[str, Any] = {
            "message_index": index,
            "role": role,
            "speaker": "Kora" if role == "assistant" else "Persona",
            "timestamp": message.get("ts"),
            "content": message.get("content") or "",
        }
        for key in (
            "trace_id",
            "tool_calls",
            "latency_ms",
            "token_count",
            "prompt_tokens",
            "completion_tokens",
            "compaction_tier",
            "is_response",
        ):
            if key in message:
                entry[key] = message[key]
        transcript.append(sanitize_for_demo(entry))
    return transcript


def render_transcript_markdown(
    session_state: dict[str, Any],
    transcript: list[dict[str, Any]],
) -> str:
    """Readable full transcript for judges/operators."""

    lines = [
        "# Kora Acceptance Conversation",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Started: {sanitize_for_demo(session_state.get('started_at', 'unknown'))}",
        f"Messages: {len(transcript)}",
        "",
    ]
    for message in transcript:
        timestamp = str(message.get("timestamp") or "")[:19]
        speaker = message.get("speaker") or message.get("role") or "Unknown"
        header = f"## {message.get('message_index')}. {speaker}"
        if timestamp:
            header += f" [{timestamp}]"
        lines.append(header)
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            lines.append(f"_Tools: {', '.join(str(t) for t in tool_calls)}_")
            lines.append("")
        lines.append(str(message.get("content") or "").strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_demo_snapshot(
    *,
    session_state: dict[str, Any],
    snapshots_dir: Path,
    output_dir: Path,
    transcript: list[dict[str, Any]],
    transcript_json_path: Path,
    transcript_md_path: Path,
    life_data: dict[str, Any],
    orch_evidence: dict[str, Any],
    tool_usage: dict[str, Any],
    cap_health: dict[str, Any],
    life_os_summary: Any,
    coverage_summary: dict[str, Any],
    report_path: Path,
) -> dict[str, Any]:
    """Build the stable fake-GUI data contract from acceptance evidence."""

    latest_snapshot = _latest_snapshot(snapshots_dir)
    latest_snapshot_name = latest_snapshot.get("name")
    records = life_data.get("records") or {}
    calendar_events = _calendar_events(records)
    domain_events = records.get("domain_events") or []
    day_plan_entries = records.get("day_plan_entries") or []
    repair_actions = records.get("plan_repair_actions") or []

    generated_at = datetime.now(UTC).isoformat()
    return {
        "demo_meta": {
            "schema_version": "kora.acceptance_demo_snapshot.v1",
            "label": (
                "Demo mode · sanitized acceptance snapshot · "
                "not connected to your local daemon"
            ),
            "generated_at": generated_at,
            "run_started_at": session_state.get("started_at"),
            "current_day": session_state.get("current_day"),
            "simulated_hours_elapsed": session_state.get(
                "simulated_hours_offset", 0
            ),
            "sanitized": True,
            "live_daemon_required": False,
            "source": "acceptance report export",
            "artifacts": {
                "report": str(report_path),
                "conversation_json": str(transcript_json_path),
                "conversation_markdown": str(transcript_md_path),
                "output_dir": str(output_dir),
            },
        },
        "persona": _persona_contract(),
        "today": {
            "latest_snapshot": latest_snapshot_name,
            "latest_snapshot_captured_at": latest_snapshot.get("captured_at"),
            "health": _snapshot_health(latest_snapshot),
            "life_counts": _life_counts(life_data),
            "active_day_plan": _active_day_plan(records),
            "recent_conversation": transcript[-6:],
        },
        "calendar": {
            "events": calendar_events,
            "source_tables": [
                "calendar_entries",
                "day_plan_entries",
                "reminders",
                "focus_blocks",
                "medication_log",
                "meal_log",
                "routines",
            ],
        },
        "confirm_reality": {
            "entries": [
                row
                for row in day_plan_entries
                if str(row.get("reality_state") or "unknown") != "unknown"
            ],
            "events": _filter_domain_events(
                domain_events, ("REALITY", "CONFIRM", "CORRECT", "INFERENCE")
            ),
            "life_events": [
                row
                for row in records.get("life_events", [])
                if str(row.get("confirmation_state") or "").lower()
                in {"confirmed", "corrected", "rejected", "needs_confirmation"}
            ],
        },
        "repair": {
            "actions": repair_actions,
            "events": _filter_domain_events(
                domain_events, ("REPAIR", "CORRECT", "STABILIZATION")
            ),
        },
        "tomorrow_bridge": {
            "context_packs": records.get("context_packs", []),
            "events": _filter_domain_events(
                domain_events, ("BRIDGE", "CONTEXT_PACK", "TOMORROW")
            ),
            "conversation_mentions": _message_mentions(
                transcript, ("tomorrow", "bridge", "future-self", "future self")
            ),
        },
        "memory": {
            "memory_lifecycle": latest_snapshot.get("memory_lifecycle", {}),
            "vault_state": latest_snapshot.get("vault_state", {}),
            "projection_counts": {
                "notes_total": orch_evidence.get("notes_total", 0),
                "entities_total": orch_evidence.get("entities_total", 0),
            },
            "session_transcripts": orch_evidence.get("session_transcripts", 0),
            "signal_queue_count": orch_evidence.get("signal_queue_count", 0),
            "tool_calls": tool_usage.get("memory", []),
        },
        "conversation": {
            "schema_version": "acceptance.conversation.v1",
            "message_count": len(transcript),
            "json_path": str(transcript_json_path),
            "markdown_path": str(transcript_md_path),
            "messages": transcript,
        },
        "acceptance_proof": {
            "coverage": coverage_summary,
            "life_os": _life_os_contract(life_os_summary),
            "tool_usage": tool_usage,
            "capability_health": cap_health,
            "orchestration": _orchestration_contract(orch_evidence),
            "snapshots": _snapshot_index(snapshots_dir),
            "db_evidence_counts": {
                table: len(rows)
                for table, rows in records.items()
                if isinstance(rows, list)
            },
        },
    }


def _sanitize_string(value: str) -> str:
    sanitized = value.replace(str(_PROJECT_ROOT), "<repo>")
    sanitized = _TMP_ACCEPTANCE_RE.sub("<acceptance_dir>", sanitized)
    sanitized = _HOME_PATH_RE.sub("<local_path>", sanitized)
    sanitized = _LOCAL_TMP_PATH_RE.sub("<local_path>", sanitized)
    sanitized = _BEARER_RE.sub("Bearer <redacted>", sanitized)
    sanitized = _ASSIGNMENT_SECRET_RE.sub(r"\1\2<redacted>", sanitized)
    return sanitized


def _latest_snapshot(snapshots_dir: Path) -> dict[str, Any]:
    if not snapshots_dir.exists():
        return {}
    candidates = sorted(
        (p for p in snapshots_dir.glob("*.json") if not p.name.endswith(".benchmarks.json")),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        return {}
    try:
        return sanitize_for_demo(json.loads(candidates[-1].read_text()))
    except Exception:
        return {"name": candidates[-1].stem, "error": "unreadable snapshot"}


def _snapshot_index(snapshots_dir: Path) -> list[dict[str, Any]]:
    if not snapshots_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(snapshots_dir.glob("*.json")):
        if path.name.endswith(".benchmarks.json"):
            continue
        try:
            payload = json.loads(path.read_text())
        except Exception:
            payload = {}
        items.append(
            sanitize_for_demo(
                {
                    "name": payload.get("name") or path.stem,
                    "captured_at": payload.get("captured_at"),
                    "path": str(path),
                    "message_count": (payload.get("conversation") or {}).get(
                        "message_count", 0
                    ),
                    "health": _snapshot_health(payload),
                }
            )
        )
    return items


def _snapshot_health(snapshot: dict[str, Any]) -> str:
    doctor = snapshot.get("inspect_doctor") or {}
    if isinstance(doctor, dict):
        if doctor.get("summary"):
            return str(doctor.get("summary"))
        if doctor.get("status"):
            return str(doctor.get("status"))
    status = snapshot.get("status") or {}
    if isinstance(status, dict) and status.get("status"):
        return str(status.get("status"))
    if snapshot.get("error"):
        return "snapshot_error"
    return "unknown"


def _persona_contract() -> dict[str, Any]:
    try:
        from tests.acceptance.scenario.persona import FIRST_RUN_ANSWERS, PERSONA
    except Exception:
        return {"source": "tests.acceptance.scenario.persona", "available": False}
    return sanitize_for_demo(
        {
            "source": "tests.acceptance.scenario.persona",
            "available": True,
            "profile": PERSONA,
            "first_run_answers": FIRST_RUN_ANSWERS,
        }
    )


def _life_counts(life_data: dict[str, Any]) -> dict[str, int]:
    keys = (
        "medication_count",
        "meal_count",
        "reminder_count",
        "quick_note_count",
        "focus_block_count",
        "routine_count",
        "support_profile_count",
        "correction_event_count",
    )
    return {key: int(life_data.get(key, 0) or 0) for key in keys}


def _active_day_plan(records: dict[str, Any]) -> dict[str, Any] | None:
    plans = records.get("day_plans") or []
    active = [row for row in plans if row.get("status") == "active"]
    if not active:
        return plans[0] if plans else None
    return active[0]


def _calendar_events(records: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in records.get("calendar_entries", []):
        events.append(
            _event(
                source_table="calendar_entries",
                source_id=row.get("id"),
                kind=row.get("kind") or "event",
                title=row.get("title"),
                starts_at=row.get("starts_at"),
                ends_at=row.get("ends_at"),
                status=row.get("status"),
                payload=row,
            )
        )
    for row in records.get("day_plan_entries", []):
        events.append(
            _event(
                source_table="day_plan_entries",
                source_id=row.get("id"),
                kind=row.get("entry_type") or "day_plan",
                title=row.get("title"),
                starts_at=row.get("intended_start") or row.get("created_at"),
                ends_at=row.get("intended_end"),
                status=row.get("status"),
                payload=row,
            )
        )
    for row in records.get("reminders", []):
        events.append(
            _event(
                source_table="reminders",
                source_id=row.get("id"),
                kind="reminder",
                title=row.get("title"),
                starts_at=row.get("due_at") or row.get("remind_at") or row.get("created_at"),
                ends_at=None,
                status=row.get("status"),
                payload=row,
            )
        )
    for row in records.get("focus_blocks", []):
        events.append(
            _event(
                source_table="focus_blocks",
                source_id=row.get("id"),
                kind="focus_block",
                title=row.get("label") or "Focus block",
                starts_at=row.get("started_at") or row.get("created_at"),
                ends_at=row.get("ended_at"),
                status="completed" if row.get("completed") else "active",
                payload=row,
            )
        )
    for row in records.get("medication_log", []):
        events.append(
            _event(
                source_table="medication_log",
                source_id=row.get("id"),
                kind="medication",
                title=row.get("medication_name"),
                starts_at=row.get("taken_at") or row.get("created_at"),
                ends_at=None,
                status="logged",
                payload=row,
            )
        )
    for row in records.get("meal_log", []):
        events.append(
            _event(
                source_table="meal_log",
                source_id=row.get("id"),
                kind="meal",
                title=row.get("meal_type") or "Meal",
                starts_at=row.get("logged_at") or row.get("created_at"),
                ends_at=None,
                status="logged",
                payload=row,
            )
        )
    for row in records.get("routines", []):
        events.append(
            _event(
                source_table="routines",
                source_id=row.get("id"),
                kind="routine",
                title=row.get("name"),
                starts_at=row.get("created_at"),
                ends_at=row.get("updated_at"),
                status="available",
                payload=row,
            )
        )
    return sorted(
        [event for event in events if event.get("title")],
        key=lambda event: str(event.get("starts_at") or ""),
    )


def _event(
    *,
    source_table: str,
    source_id: Any,
    kind: Any,
    title: Any,
    starts_at: Any,
    ends_at: Any,
    status: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return sanitize_for_demo(
        {
            "source_table": source_table,
            "source_id": source_id,
            "kind": kind,
            "title": title,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "status": status,
            "payload": payload,
        }
    )


def _filter_domain_events(
    events: list[dict[str, Any]],
    terms: tuple[str, ...],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in events:
        event_type = str(event.get("event_type") or "").upper()
        if any(term in event_type for term in terms):
            result.append(event)
    return result


def _message_mentions(
    transcript: list[dict[str, Any]],
    terms: tuple[str, ...],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    lowered_terms = tuple(term.lower() for term in terms)
    for message in transcript:
        content = str(message.get("content") or "").lower()
        if any(term in content for term in lowered_terms):
            matches.append(
                {
                    "message_index": message.get("message_index"),
                    "role": message.get("role"),
                    "timestamp": message.get("timestamp"),
                    "excerpt": str(message.get("content") or "")[:400],
                }
            )
    return matches


def _life_os_contract(summary: Any) -> dict[str, Any]:
    if summary is None:
        return {"available": False}
    scenarios = []
    for scenario in getattr(summary, "scenarios", ()) or ():
        scenarios.append(
            {
                "key": getattr(scenario, "key", None),
                "title": getattr(scenario, "title", None),
                "acceptance_verified": getattr(
                    scenario, "acceptance_verified", False
                ),
                "implemented": getattr(scenario, "implemented", False),
                "tool_calls": list(getattr(scenario, "tool_calls", ()) or ()),
                "evidence": [
                    {
                        "label": getattr(item, "label", None),
                        "satisfied": getattr(item, "satisfied", False),
                        "source": getattr(item, "source", None),
                        "detail": getattr(item, "detail", None),
                        "required": getattr(item, "required", True),
                    }
                    for item in getattr(scenario, "evidence", ()) or ()
                ],
            }
        )
    return sanitize_for_demo(
        {
            "available": getattr(summary, "available", False),
            "db_path": getattr(summary, "db_path", None),
            "acceptance_verified_count": getattr(
                summary, "acceptance_verified_count", 0
            ),
            "implemented_count": getattr(summary, "implemented_count", 0),
            "error": getattr(summary, "error", None),
            "scenarios": scenarios,
        }
    )


def _orchestration_contract(orch_evidence: dict[str, Any]) -> dict[str, Any]:
    return sanitize_for_demo(
        {
            "available": orch_evidence.get("available", False),
            "pipeline_instances": orch_evidence.get("pipeline_instances", []),
            "worker_tasks": orch_evidence.get("worker_tasks", []),
            "ledger_events": orch_evidence.get("ledger_events", []),
            "system_phases_observed": orch_evidence.get(
                "system_phases_observed", []
            ),
            "notification_count": orch_evidence.get("notification_count", 0),
            "delivered_notifications": orch_evidence.get(
                "delivered_notifications", 0
            ),
        }
    )
