"""Life OS acceptance proof collector tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tests.acceptance.life_os import (
    collect_life_os_acceptance,
    render_life_os_acceptance,
)


def _create_core_tables(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE day_plans (
                id TEXT PRIMARY KEY,
                plan_date TEXT,
                revision INTEGER,
                status TEXT
            );
            CREATE TABLE day_plan_entries (
                id TEXT PRIMARY KEY,
                day_plan_id TEXT,
                reality_state TEXT
            );
            CREATE TABLE load_assessments (
                id TEXT PRIMARY KEY,
                day_plan_id TEXT,
                band TEXT
            );
            CREATE TABLE life_events (
                id TEXT PRIMARY KEY,
                event_type TEXT,
                state TEXT
            );
            CREATE TABLE plan_repair_actions (
                id TEXT PRIMARY KEY,
                day_plan_id TEXT,
                status TEXT,
                source_event_id TEXT
            );
            CREATE TABLE future_self_bridges (
                id TEXT PRIMARY KEY,
                bridge_date TEXT
            );
            CREATE TABLE support_profiles (
                id TEXT PRIMARY KEY,
                profile_key TEXT,
                status TEXT
            );
            CREATE TABLE support_profile_signals (
                id TEXT PRIMARY KEY,
                profile_key TEXT,
                signal_type TEXT
            );
            CREATE TABLE nudge_decisions (
                id TEXT PRIMARY KEY,
                decision TEXT,
                source_event_id TEXT
            );
            CREATE TABLE context_packs (
                id TEXT PRIMARY KEY,
                target_type TEXT
            );
            CREATE TABLE domain_events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                aggregate_type TEXT,
                aggregate_id TEXT,
                source_service TEXT,
                payload TEXT,
                created_at TEXT
            );
            CREATE TABLE session_transcripts (
                session_id TEXT PRIMARY KEY,
                tool_calls TEXT
            );
            """
        )


def _insert_event(conn: sqlite3.Connection, event_id: str, event_type: str) -> None:
    conn.execute(
        """
        INSERT INTO domain_events
        (id, event_type, aggregate_type, aggregate_id, source_service, payload, created_at)
        VALUES (?, ?, 'acceptance', ?, 'test', '{}', '2026-04-28T00:00:00Z')
        """,
        (event_id, event_type, event_id),
    )


def _scenario(summary, key: str):
    return next(scenario for scenario in summary.scenarios if scenario.key == key)


def test_tool_calls_alone_do_not_verify_life_os_acceptance(tmp_path: Path) -> None:
    db_path = tmp_path / "operational.db"
    _create_core_tables(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO session_transcripts (session_id, tool_calls) VALUES (?, ?)",
            (
                "session-1",
                json.dumps(
                    [
                        {"name": "plan_today"},
                        {"name": "record_life_event"},
                        {"name": "repair_day"},
                        {"name": "build_future_self_bridge"},
                    ]
                ),
            ),
        )

    summary = collect_life_os_acceptance(db_path)

    assert _scenario(summary, "calendar_spine").tool_calls == (
        "plan_today",
    )
    assert not _scenario(summary, "calendar_spine").acceptance_verified
    assert not _scenario(summary, "confirm_reality").acceptance_verified
    assert summary.acceptance_verified_count == 0


def test_core_loop_turns_green_only_with_db_rows_and_domain_events(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "operational.db"
    _create_core_tables(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO day_plans (id, plan_date, revision, status) VALUES "
            "('plan-1', '2026-04-28', 1, 'superseded'), "
            "('plan-2', '2026-04-28', 2, 'active')"
        )
        conn.execute(
            "INSERT INTO day_plan_entries (id, day_plan_id, reality_state) VALUES "
            "('entry-1', 'plan-2', 'done')"
        )
        conn.execute(
            "INSERT INTO load_assessments (id, day_plan_id, band) VALUES "
            "('load-1', 'plan-2', 'high')"
        )
        conn.execute(
            "INSERT INTO life_events (id, event_type, state) VALUES "
            "('life-1', 'medication', 'confirmed'), "
            "('life-2', 'meal', 'skipped'), "
            "('life-3', 'task', 'partial')"
        )
        conn.execute(
            "INSERT INTO plan_repair_actions (id, day_plan_id, status) VALUES "
            "('repair-1', 'plan-1', 'applied')"
        )
        conn.execute(
            "INSERT INTO future_self_bridges (id, bridge_date) VALUES "
            "('bridge-1', '2026-04-28')"
        )
        _insert_event(conn, "evt-plan", "DAY_PLAN_CREATED")
        _insert_event(conn, "evt-life", "LIFE_EVENT_RECORDED")
        _insert_event(conn, "evt-diverged", "PLAN_REALITY_DIVERGED")
        _insert_event(conn, "evt-repaired", "DAY_PLAN_REPAIRED")
        _insert_event(conn, "evt-bridge", "FUTURE_SELF_BRIDGE_READY")

    summary = collect_life_os_acceptance(db_path)

    assert _scenario(summary, "calendar_spine").acceptance_verified
    assert _scenario(summary, "confirm_reality").acceptance_verified
    assert _scenario(summary, "repair_day").acceptance_verified
    assert _scenario(summary, "bridge_tomorrow").acceptance_verified


def test_separate_support_tracks_crisis_proactivity_and_context_pack_require_durable_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "operational.db"
    _create_core_tables(db_path)

    with sqlite3.connect(db_path) as conn:
        for key in (
            "adhd",
            "anxiety",
            "autism_sensory",
            "low_energy",
            "burnout",
            "trusted_support",
        ):
            conn.execute(
                "INSERT INTO support_profiles (id, profile_key, status) VALUES (?, ?, 'active')",
                (f"profile-{key}", key),
            )
        conn.execute(
            "INSERT INTO support_profile_signals (id, profile_key, signal_type) "
            "VALUES "
            "('signal-adhd', 'adhd', 'planning_rule'), "
            "('signal-autism', 'autism_sensory', 'sensory_load'), "
            "('signal-burnout', 'burnout', 'low_energy')"
        )
        conn.execute(
            "INSERT INTO life_events (id, event_type, state) VALUES "
            "('event-adhd', 'avoidance', 'recorded'), "
            "('event-sensory', 'sensory_overload', 'recorded')"
        )
        conn.execute(
            "INSERT INTO load_assessments (id, day_plan_id, band) VALUES "
            "('load-overloaded', 'plan-1', 'overloaded')"
        )
        conn.execute(
            "INSERT INTO day_plan_entries (id, day_plan_id, reality_state) "
            "VALUES ('entry-corrected', 'plan-1', 'corrected')"
        )
        conn.execute(
            "INSERT INTO nudge_decisions (id, decision) VALUES "
            "('nudge-1', 'suppressed')"
        )
        conn.execute(
            "INSERT INTO context_packs (id, target_type) VALUES "
            "('pack-1', 'admin_form')"
        )
        _insert_event(conn, "evt-support", "SUPPORT_SIGNAL_DETECTED")
        _insert_event(conn, "evt-stabilize", "STABILIZATION_MODE_ENTERED")
        _insert_event(conn, "evt-correct", "WRONG_INFERENCE_REPAIRED")
        _insert_event(conn, "evt-trusted", "TRUSTED_SUPPORT_CONSENT_RECORDED")
        _insert_event(conn, "evt-safety", "SAFETY_BOUNDARY_TRIGGERED")
        _insert_event(conn, "evt-nudge", "NUDGE_DECISION_RECORDED")
        _insert_event(conn, "evt-pack", "CONTEXT_PACK_READY")

    summary = collect_life_os_acceptance(db_path)

    assert _scenario(summary, "adhd_week").acceptance_verified
    assert _scenario(summary, "autism_sensory_week").acceptance_verified
    assert _scenario(summary, "burnout_anxiety_week").acceptance_verified
    assert _scenario(summary, "wrong_inference_recovery").acceptance_verified
    assert _scenario(summary, "trusted_support").acceptance_verified
    assert _scenario(summary, "crisis_boundary").acceptance_verified
    assert _scenario(summary, "proactivity_suppression").acceptance_verified
    assert _scenario(summary, "context_packs").acceptance_verified


def test_renderer_separates_life_os_and_old_capability_pack_status(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "operational.db"
    _create_core_tables(db_path)
    summary = collect_life_os_acceptance(
        db_path,
        capability_pack_status={
            "browser": {"status": "unconfigured", "summary": "no browser token"},
            "workspace": {"status": "healthy", "summary": "available"},
        },
    )

    rendered = "\n".join(
        render_life_os_acceptance(
            summary,
            manual_verification={"plan_today": "manual DB inspection passed"},
        )
    )

    assert "## Life OS Acceptance" in rendered
    assert "### Implemented" in rendered
    assert "### Manually Verified" in rendered
    assert "### Acceptance Verified" in rendered
    assert "### Remaining Debt" in rendered
    assert "### Old Suite Capability-Pack Status" in rendered
    assert "do not gate Life OS core" in rendered
    assert "plan_today: manual DB inspection passed" in rendered
