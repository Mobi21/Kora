"""Unit tests for Life OS crisis safety routing."""

from __future__ import annotations

import aiosqlite

from kora_v2.safety import CrisisSafetyRouter, ensure_crisis_safety_tables


async def _setup_db(path):
    async with aiosqlite.connect(str(path)) as db:
        await ensure_crisis_safety_tables(db)
        await db.commit()


def test_crisis_router_does_not_preempt_ordinary_planning_language():
    result = CrisisSafetyRouter().evaluate(
        "This deadline is a crisis but I just need to repair the day."
    )

    assert result.preempt is False
    assert result.severity == "none"
    assert result.next_action == "continue_life_os_flow"


def test_crisis_router_preempts_suicidal_language():
    result = CrisisSafetyRouter().evaluate("I want to die and might hurt myself.")

    assert result.preempt is True
    assert result.severity == "emergency"
    assert result.next_action == "preempt_life_os_and_show_crisis_support"
    assert "988" in result.user_message


async def test_crisis_router_persists_boundary_record_and_domain_event(tmp_path):
    db_path = tmp_path / "operational.db"
    await _setup_db(db_path)

    router = CrisisSafetyRouter(db_path)
    result = await router.route(
        "I can't keep myself safe tonight.",
        metadata={"turn_id": "turn-1"},
    )

    assert result.preempt is True
    assert result.record_id is not None

    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT severity, preempted, metadata FROM safety_boundary_records WHERE id = ?",
            (result.record_id,),
        )
        row = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT event_type, aggregate_id FROM domain_events WHERE aggregate_id = ?",
            (result.record_id,),
        )
        event = await cursor.fetchone()

    assert row[0] == "emergency"
    assert row[1] == 1
    assert '"turn_id":"turn-1"' in row[2]
    assert event == ("CRISIS_SAFETY_PREEMPTED", result.record_id)


async def test_record_boundary_helper_can_persist_non_preemptive_support_record(tmp_path):
    db_path = tmp_path / "operational.db"
    await _setup_db(db_path)

    record = await CrisisSafetyRouter(db_path).record_boundary(
        "User asked for a safety plan template.",
        severity="support",
        matched_terms=[],
        preempted=False,
        metadata={"source": "manual_review"},
    )

    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT severity, preempted FROM safety_boundary_records WHERE id = ?",
            (record.id,),
        )
        row = await cursor.fetchone()

    assert row == ("support", 0)
