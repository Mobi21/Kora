"""WorkLedger append-only writer tests — covers acceptance item 45."""

from __future__ import annotations

from pathlib import Path

import pytest

from kora_v2.runtime.orchestration.ledger import LedgerEventType, WorkLedger
from kora_v2.runtime.orchestration.registry import init_orchestration_schema


@pytest.fixture
async def ledger_db(tmp_path: Path) -> Path:
    db = tmp_path / "ledger.db"
    await init_orchestration_schema(db)
    return db


async def test_record_and_read_task_events(ledger_db: Path) -> None:
    ledger = WorkLedger(ledger_db)
    await ledger.record(
        LedgerEventType.TASK_CREATED,
        worker_task_id="task-1",
        reason="test",
        metadata={"goal": "hello"},
    )
    await ledger.record(
        LedgerEventType.TASK_COMPLETED,
        worker_task_id="task-1",
        reason="done",
        metadata={"summary": "ok"},
    )
    events = await ledger.read_task_events("task-1")
    assert len(events) == 2
    assert events[0].event_type == LedgerEventType.TASK_CREATED
    assert events[0].metadata == {"goal": "hello"}
    assert events[1].event_type == LedgerEventType.TASK_COMPLETED


async def test_record_with_pipeline_scope(ledger_db: Path) -> None:
    ledger = WorkLedger(ledger_db)
    await ledger.record(
        LedgerEventType.PIPELINE_STARTED,
        pipeline_instance_id="pi-1",
        reason="boot",
    )
    await ledger.record(
        LedgerEventType.PIPELINE_COMPLETED,
        pipeline_instance_id="pi-1",
    )
    events = await ledger.read_pipeline_events("pi-1")
    assert [e.event_type for e in events] == [
        LedgerEventType.PIPELINE_STARTED,
        LedgerEventType.PIPELINE_COMPLETED,
    ]


async def test_read_recent_orders_desc(ledger_db: Path) -> None:
    ledger = WorkLedger(ledger_db)
    for i in range(5):
        await ledger.record(
            LedgerEventType.TASK_PROGRESS,
            worker_task_id=f"t{i}",
        )
    recent = await ledger.read_recent(limit=3)
    assert len(recent) == 3
    # Most recent first
    assert recent[0].worker_task_id == "t4"


async def test_metadata_is_json_roundtrip(ledger_db: Path) -> None:
    ledger = WorkLedger(ledger_db)
    payload = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
    await ledger.record(
        LedgerEventType.TASK_PROGRESS,
        worker_task_id="t-meta",
        metadata=payload,
    )
    events = await ledger.read_task_events("t-meta")
    assert events[0].metadata == payload


async def test_canonical_event_names_present() -> None:
    assert LedgerEventType.PIPELINE_STARTED == "pipeline_started"
    assert LedgerEventType.TASK_COMPLETED == "task_completed"
    assert LedgerEventType.RATE_LIMIT_REJECTED == "rate_limit_rejected"
    assert LedgerEventType.STATE_TRANSITION == "state_transition"
