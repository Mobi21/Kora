"""Unit tests for OpenDecisionsTracker (spec §15) and DecisionManager."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from kora_v2.runtime.orchestration import init_orchestration_schema
from kora_v2.runtime.orchestration.decisions import (
    DecisionManager,
    OpenDecision,
    OpenDecisionsTracker,
    PendingDecision,
)


@pytest.fixture
async def tracker(tmp_path: Path) -> OpenDecisionsTracker:
    db_path = tmp_path / "operational.db"
    await init_orchestration_schema(db_path)
    return OpenDecisionsTracker(db_path)


# ── OpenDecisionsTracker ────────────────────────────────────────────────


async def test_record_returns_open_decision(tracker: OpenDecisionsTracker) -> None:
    dec = await tracker.record(
        topic="Pick a meal for Thursday",
        context="The user is weighing two entrees.",
        posed_in_session="sess-abc",
    )
    assert isinstance(dec, OpenDecision)
    assert dec.topic == "Pick a meal for Thursday"
    assert dec.posed_in_session == "sess-abc"
    assert dec.status == "open"
    assert dec.id.startswith("dec-")


async def test_record_kwargs_only(tracker: OpenDecisionsTracker) -> None:
    # Positional args should be rejected (kwargs-only contract).
    with pytest.raises(TypeError):
        await tracker.record("topic", "context")  # type: ignore[misc]


async def test_get_pending_returns_open_decisions(
    tracker: OpenDecisionsTracker,
) -> None:
    d1 = await tracker.record(topic="A", context="ctx-a")
    d2 = await tracker.record(topic="B", context="ctx-b")
    pending = await tracker.get_pending()
    ids = {p.id for p in pending}
    assert d1.id in ids
    assert d2.id in ids


async def test_resolve_flips_status(tracker: OpenDecisionsTracker) -> None:
    dec = await tracker.record(topic="X", context="c")
    await tracker.resolve(dec.id, resolution="chose X")
    pending = await tracker.get_pending()
    assert all(p.id != dec.id for p in pending)


async def test_dismiss_flips_status(tracker: OpenDecisionsTracker) -> None:
    dec = await tracker.record(topic="Y", context="c")
    await tracker.dismiss(dec.id)
    pending = await tracker.get_pending()
    assert all(p.id != dec.id for p in pending)


async def test_record_emits_open_decision_posed_event(tmp_path: Path) -> None:
    """Spec §15: recording an open decision must publish the
    OPEN_DECISION_POSED event so the contextual_engagement trigger
    (and any other subscribers) sees it.
    """
    from kora_v2.core.events import EventEmitter, EventType

    db_path = tmp_path / "operational.db"
    await init_orchestration_schema(db_path)
    emitter = EventEmitter()
    received: list[dict] = []

    async def handler(payload: dict) -> None:
        received.append(payload)

    emitter.on(EventType.OPEN_DECISION_POSED, handler)
    tracker_with_emitter = OpenDecisionsTracker(db_path, event_emitter=emitter)
    dec = await tracker_with_emitter.record(
        topic="Pick lunch",
        context="user can't decide",
        posed_in_session="sess-1",
    )
    assert len(received) == 1
    payload = received[0]
    assert payload["event_type"] is EventType.OPEN_DECISION_POSED
    assert payload["decision_id"] == dec.id
    assert payload["topic"] == "Pick lunch"
    assert payload["posed_in_session"] == "sess-1"
    assert payload["context"] == "user can't decide"


async def test_record_without_emitter_does_not_emit_or_raise(
    tracker: OpenDecisionsTracker,
) -> None:
    """A tracker constructed without an emitter must still record cleanly."""
    dec = await tracker.record(topic="No bus", context="ctx")
    assert dec.topic == "No bus"


async def test_expire_older_than_hides_stale_decisions(
    tracker: OpenDecisionsTracker,
) -> None:
    fresh = await tracker.record(topic="fresh", context="ctx")
    stale = await tracker.record(topic="stale", context="ctx")

    # Backdate `stale` manually so it's older than the cutoff.
    async with aiosqlite.connect(str(tracker._db_path)) as db:
        old = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        await db.execute(
            "UPDATE open_decisions SET posed_at=? WHERE id=?",
            (old, stale.id),
        )
        await db.commit()

    expired = await tracker.expire_older_than(days=3)
    assert stale.id in expired
    assert fresh.id not in expired

    pending = await tracker.get_pending()
    pending_ids = {p.id for p in pending}
    assert fresh.id in pending_ids
    assert stale.id not in pending_ids


# ── DecisionManager (in-memory, spec §17.7a relocation) ─────────────────


def test_manager_create_decision_validates_recommendation() -> None:
    mgr = DecisionManager()
    with pytest.raises(ValueError):
        mgr.create_decision(
            options=["a", "b"], recommendation="c", policy="auto_select"
        )


def test_manager_submit_answer_resolves() -> None:
    mgr = DecisionManager()
    decision = mgr.create_decision(options=["a", "b"], recommendation="a")
    result = mgr.submit_answer(decision.decision_id, chosen="b")
    assert result.chosen == "b"
    assert result.method == "user"
    assert mgr.get_pending(decision.decision_id) is None


def test_manager_check_timeout_never_auto_never_resolves() -> None:
    mgr = DecisionManager()
    decision = PendingDecision(
        options=["a", "b"],
        policy="never_auto",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert mgr.check_timeout(decision) is None


def test_manager_check_timeout_auto_selects_recommendation() -> None:
    mgr = DecisionManager()
    decision = mgr.create_decision(
        options=["proceed", "skip"],
        recommendation="skip",
        policy="auto_select",
        timeout_minutes=0,
    )
    # Force expiry
    decision.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    result = mgr.check_timeout(decision)
    assert result is not None
    assert result.chosen == "skip"
    assert result.method == "timeout"
