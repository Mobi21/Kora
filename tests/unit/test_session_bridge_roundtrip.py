"""Tests for the Phase 5 session bridge roundtrip (frontmatter + sidecar)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from kora_v2.core.models import (
    DayPlanItemSnapshot,
    DayPlanSnapshot,
    EmotionalState,
    EnergyEstimate,
    SessionBridge,
    SessionState,
    WorkingOnSnapshot,
)
from kora_v2.daemon.session import SessionManager


class _Settings:
    def __init__(self, base: Path) -> None:
        self.data_dir = base

        class _Memory:
            kora_memory_path = str(base / "mem")

        self.memory = _Memory()


class _Container:
    def __init__(self, base: Path) -> None:
        self.settings = _Settings(base)
        self.session_manager = None
        self.event_emitter = None
        self.signal_scanner = None


@pytest.fixture
def manager(tmp_path):
    container = _Container(tmp_path)
    sm = SessionManager(container)
    sm.active_session = SessionState(
        session_id="test",
        turn_count=2,
        started_at=datetime.now(UTC),
        emotional_state=EmotionalState(
            valence=0, arousal=0.3, dominance=0.5, mood_label="calm"
        ),
        energy_estimate=EnergyEstimate(
            level="medium",
            focus="moderate",
            confidence=0.5,
            source="time_of_day",
        ),
        pending_items=[],
    )
    return sm


async def test_bridge_frontmatter_roundtrips_all_scalars(manager):
    bridge = SessionBridge(
        session_id="abc",
        summary="summary",
        open_threads=["is X done?"],
        emotional_trajectory="trajectory",
        active_plan_id="plan42",
        continuation_checkpoint_id="ckpt9",
        working_on=WorkingOnSnapshot(
            last_tools=["create_item"],
            items_touched=["i1"],
            last_user_message="hi",
            last_assistant_summary_snippet="okay",
        ),
        energy_at_end="medium",
    )
    await manager._save_bridge(bridge)
    loaded = await manager.load_last_bridge()
    assert loaded is not None
    assert loaded.session_id == "abc"
    assert loaded.summary == "summary"
    assert loaded.open_threads == ["is X done?"]
    assert loaded.emotional_trajectory == "trajectory"
    assert loaded.active_plan_id == "plan42"
    assert loaded.continuation_checkpoint_id == "ckpt9"
    assert loaded.energy_at_end == "medium"
    assert loaded.working_on is not None
    assert loaded.working_on.last_tools == ["create_item"]


async def test_bridge_sidecar_roundtrips(manager):
    snap = DayPlanSnapshot(
        snapshot_at=datetime.now(UTC),
        items=[
            DayPlanItemSnapshot(
                item_id="i1",
                title="task1",
                status="planned",
                goal_scope="task",
            )
        ],
        counts={"planned": 1},
    )
    bridge = SessionBridge(
        session_id="snapid",
        summary="has sidecar",
        day_plan_snapshot=snap,
    )
    await manager._save_bridge(bridge)
    loaded = await manager.load_last_bridge()
    assert loaded.day_plan_snapshot is not None
    assert len(loaded.day_plan_snapshot.items) == 1
    assert loaded.day_plan_snapshot.items[0].title == "task1"


async def test_bridge_without_sidecar_still_loads(manager):
    bridge = SessionBridge(session_id="plain", summary="no sidecar")
    await manager._save_bridge(bridge)
    loaded = await manager.load_last_bridge()
    assert loaded is not None
    assert loaded.day_plan_snapshot is None
