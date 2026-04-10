"""Tests for AutonomousUpdateStore — record, get_undelivered, mark_delivered.

Verifies the foreground update delivery pipeline used to surface autonomous
background work to the user when they return from an idle period.
"""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from kora_v2.core.db import init_operational_db
from kora_v2.runtime.stores import AutonomousUpdateStore


# ── Fixture ───────────────────────────────────────────────────────────────


@pytest.fixture
async def db(tmp_path: Path) -> aiosqlite.Connection:
    db_path = tmp_path / "operational.db"
    await init_operational_db(db_path)
    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    yield conn
    await conn.close()


@pytest.fixture
def store(db: aiosqlite.Connection) -> AutonomousUpdateStore:
    return AutonomousUpdateStore(db)


# ── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_inserts_correctly(store: AutonomousUpdateStore, db: aiosqlite.Connection) -> None:
    """record() inserts a row with the expected column values."""
    await store.record(
        session_id="sess-1",
        plan_id="plan-abc",
        update_type="checkpoint",
        summary="Checkpoint after step 2 of 5",
        payload={"steps_completed": 2, "steps_pending": 3},
    )

    async with db.execute("SELECT * FROM autonomous_updates") as cursor:
        rows = await cursor.fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["session_id"] == "sess-1"
    assert row["plan_id"] == "plan-abc"
    assert row["update_type"] == "checkpoint"
    assert row["summary"] == "Checkpoint after step 2 of 5"
    assert row["delivered"] == 0
    payload = json.loads(row["payload"])
    assert payload["steps_completed"] == 2
    assert payload["steps_pending"] == 3


@pytest.mark.asyncio
async def test_get_undelivered_returns_only_undelivered(
    store: AutonomousUpdateStore,
    db: aiosqlite.Connection,
) -> None:
    """get_undelivered() returns only records with delivered=0, oldest first."""
    # Insert 2 undelivered and 1 already-delivered record
    await store.record(
        session_id="sess-2",
        plan_id="p1",
        update_type="checkpoint",
        summary="first checkpoint",
    )
    await store.record(
        session_id="sess-2",
        plan_id="p1",
        update_type="checkpoint",
        summary="second checkpoint",
    )
    # Manually mark one as delivered
    await db.execute(
        "UPDATE autonomous_updates SET delivered=1 WHERE summary='first checkpoint'"
    )
    await db.commit()

    results = await store.get_undelivered("sess-2")
    assert len(results) == 1
    assert results[0]["summary"] == "second checkpoint"


@pytest.mark.asyncio
async def test_get_undelivered_filters_by_session(
    store: AutonomousUpdateStore,
) -> None:
    """get_undelivered() only returns records for the requested session."""
    await store.record(session_id="A", plan_id="p1", update_type="completion", summary="done A")
    await store.record(session_id="B", plan_id="p2", update_type="completion", summary="done B")

    results_a = await store.get_undelivered("A")
    results_b = await store.get_undelivered("B")
    assert len(results_a) == 1
    assert results_a[0]["summary"] == "done A"
    assert len(results_b) == 1
    assert results_b[0]["summary"] == "done B"


@pytest.mark.asyncio
async def test_mark_delivered_marks_all(
    store: AutonomousUpdateStore,
) -> None:
    """mark_delivered() flips delivered=1 for all undelivered records in a session."""
    await store.record(session_id="sess-3", plan_id="p", update_type="checkpoint", summary="c1")
    await store.record(session_id="sess-3", plan_id="p", update_type="checkpoint", summary="c2")
    await store.record(session_id="sess-3", plan_id="p", update_type="completion", summary="done")

    # Pre-condition: 3 undelivered
    undelivered = await store.get_undelivered("sess-3")
    assert len(undelivered) == 3

    await store.mark_delivered("sess-3")

    # Post-condition: 0 undelivered
    undelivered = await store.get_undelivered("sess-3")
    assert len(undelivered) == 0


@pytest.mark.asyncio
async def test_record_with_none_payload(
    store: AutonomousUpdateStore,
    db: aiosqlite.Connection,
) -> None:
    """record() stores NULL payload when payload is None."""
    await store.record(
        session_id="sess-4",
        plan_id=None,
        update_type="completion",
        summary="completed with no payload",
    )

    async with db.execute("SELECT payload FROM autonomous_updates") as cursor:
        row = await cursor.fetchone()
    assert row["payload"] is None


@pytest.mark.asyncio
async def test_graceful_when_table_missing(tmp_path: Path) -> None:
    """Store methods handle missing table without raising."""
    db_path = tmp_path / "empty.db"
    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row
    try:
        store = AutonomousUpdateStore(db)

        # record should not raise — table missing is a soft warning
        await store.record(
            session_id="x", plan_id=None, update_type="checkpoint", summary="test"
        )

        # get_undelivered should return []
        results = await store.get_undelivered("x")
        assert results == []

        # mark_delivered should not raise
        await store.mark_delivered("x")
    finally:
        await db.close()
