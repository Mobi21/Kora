"""RequestLimiter unit tests — covers acceptance items 32 and 33."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from kora_v2.runtime.orchestration.limiter import (
    CONVERSATION_RESERVE,
    NOTIFICATION_RESERVE,
    WINDOW_CAPACITY,
    RequestLimiter,
)
from kora_v2.runtime.orchestration.registry import init_orchestration_schema
from kora_v2.runtime.orchestration.worker_task import RequestClass


@pytest.fixture
async def limiter_db(tmp_path: Path) -> Path:
    db = tmp_path / "limit.db"
    await init_orchestration_schema(db)
    return db


async def test_conversation_always_succeeds(limiter_db: Path) -> None:
    lim = RequestLimiter(limiter_db)
    await lim.replay_from_log()
    for _ in range(10):
        assert await lim.acquire(RequestClass.CONVERSATION) is True


async def test_background_reserves_enforced(limiter_db: Path) -> None:
    """Background dispatch must stop before eating into conv or notif reserves."""
    lim = RequestLimiter(
        limiter_db,
        capacity=400,
        conversation_reserve=100,
        notification_reserve=50,
    )
    await lim.replay_from_log()

    # We have 400 cap, 100 conv reserve, 50 notif reserve → 250 for BG.
    for _ in range(250):
        assert await lim.acquire(RequestClass.BACKGROUND) is True
    # 251st background request must be refused
    assert await lim.acquire(RequestClass.BACKGROUND) is False


async def test_notification_reserve_blocks_background_at_capacity(
    limiter_db: Path,
) -> None:
    lim = RequestLimiter(
        limiter_db,
        capacity=200,
        conversation_reserve=50,
        notification_reserve=20,
    )
    await lim.replay_from_log()

    # Burn 130 on background (cap=200, conv=50, notif=20 → bg budget=130)
    for _ in range(130):
        assert await lim.acquire(RequestClass.BACKGROUND) is True
    assert await lim.acquire(RequestClass.BACKGROUND) is False
    # Notifications still have 20 of their own room
    for _ in range(20):
        assert await lim.acquire(RequestClass.NOTIFICATION) is True
    assert await lim.acquire(RequestClass.NOTIFICATION) is False
    # Conversation still goes through
    assert await lim.acquire(RequestClass.CONVERSATION) is True


async def test_window_rollover(limiter_db: Path) -> None:
    """Acceptance item 33 — 5h window rollover."""
    lim = RequestLimiter(
        limiter_db,
        capacity=10,
        conversation_reserve=2,
        notification_reserve=1,
        window_seconds=2,
    )
    await lim.replay_from_log()
    for _ in range(7):
        assert await lim.acquire(RequestClass.BACKGROUND) is True
    assert await lim.acquire(RequestClass.BACKGROUND) is False

    # Wait for the window to roll over
    import asyncio
    await asyncio.sleep(2.1)
    assert await lim.acquire(RequestClass.BACKGROUND) is True


async def test_replay_from_log_restores_state(limiter_db: Path) -> None:
    """Crash recovery — rehydrate the window from the SQL log."""
    lim = RequestLimiter(
        limiter_db,
        capacity=50,
        conversation_reserve=10,
        notification_reserve=5,
    )
    await lim.replay_from_log()

    for _ in range(20):
        await lim.acquire(RequestClass.BACKGROUND)

    # New limiter instance reading the same DB
    lim2 = RequestLimiter(
        limiter_db,
        capacity=50,
        conversation_reserve=10,
        notification_reserve=5,
    )
    await lim2.replay_from_log()
    snap = await lim2.snapshot()
    assert snap.total_in_window == 20
    assert snap.by_class[RequestClass.BACKGROUND] == 20


async def test_log_writes_survive_restart(limiter_db: Path) -> None:
    lim = RequestLimiter(
        limiter_db,
        capacity=100,
        conversation_reserve=10,
        notification_reserve=5,
    )
    await lim.replay_from_log()
    await lim.acquire(RequestClass.BACKGROUND, worker_task_id="task-1")
    async with aiosqlite.connect(str(limiter_db)) as db:
        cursor = await db.execute(
            "SELECT class, worker_task_id FROM request_limiter_log"
        )
        rows = await cursor.fetchall()
    assert rows == [("background", "task-1")]


async def test_snapshot_reports_remaining_per_class(limiter_db: Path) -> None:
    lim = RequestLimiter(
        limiter_db,
        capacity=100,
        conversation_reserve=10,
        notification_reserve=5,
    )
    await lim.replay_from_log()
    snap = await lim.snapshot()
    assert snap.capacity == 100
    assert snap.remaining == 100
    assert snap.remaining_for(RequestClass.CONVERSATION) == 100
    assert snap.remaining_for(RequestClass.NOTIFICATION) == 90
    assert snap.remaining_for(RequestClass.BACKGROUND) == 85


def test_default_constants_match_spec() -> None:
    """Spec §9.1 — 5h window, 4500 total, 300 conv, 100 notif."""
    assert WINDOW_CAPACITY == 4500
    assert CONVERSATION_RESERVE == 300
    assert NOTIFICATION_RESERVE == 100
