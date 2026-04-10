"""Tests for kora_v2.autonomous.checkpoint — Phase 6 checkpoint persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from kora_v2.autonomous.checkpoint import CheckpointManager
from kora_v2.autonomous.state import AutonomousCheckpoint, AutonomousState
from kora_v2.core.db import init_operational_db

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    """Create and initialise a fresh operational.db in a temp directory."""
    path = tmp_path / "operational.db"
    await init_operational_db(path)
    return path


@pytest_asyncio.fixture
async def mgr(db_path: Path) -> CheckpointManager:
    return CheckpointManager(db_path)


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_state(session_id: str = "sess-1", plan_id: str = "plan-1") -> AutonomousState:
    return AutonomousState(
        session_id=session_id,
        plan_id=plan_id,
        status="executing",
        started_at=datetime.now(UTC),
        elapsed_seconds=300,
        request_count=5,
        token_estimate=2000,
        cost_estimate=0.02,
    )


def _make_checkpoint(
    checkpoint_id: str = "ckpt-1",
    session_id: str = "sess-1",
    plan_id: str = "plan-1",
    reason: str = "periodic",
) -> AutonomousCheckpoint:
    state = _make_state(session_id=session_id, plan_id=plan_id)
    return AutonomousCheckpoint(
        checkpoint_id=checkpoint_id,
        session_id=session_id,
        plan_id=plan_id,
        mode="task",
        state=state,
        resume_token=f"tok-{checkpoint_id}",
        elapsed_seconds=state.elapsed_seconds,
        request_count=state.request_count,
        token_estimate=state.token_estimate,
        cost_estimate=state.cost_estimate,
        completed_step_ids=["step-a", "step-b"],
        pending_step_ids=["step-c"],
        latest_reflection="Made good progress on auth module.",
        reason=reason,
    )


# ── save ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSave:
    async def test_save_returns_checkpoint_id(self, mgr: CheckpointManager):
        cp = _make_checkpoint()
        returned_id = await mgr.save(cp)
        assert returned_id == cp.checkpoint_id

    async def test_save_idempotent_overwrite(self, mgr: CheckpointManager):
        """Saving the same checkpoint_id twice should not raise."""
        cp = _make_checkpoint("ckpt-x")
        await mgr.save(cp)
        await mgr.save(cp)  # second save — overwrite

    async def test_save_multiple_checkpoints(self, mgr: CheckpointManager):
        cp1 = _make_checkpoint("ckpt-1")
        cp2 = _make_checkpoint("ckpt-2")
        await mgr.save(cp1)
        await mgr.save(cp2)


# ── load_by_id ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestLoadById:
    async def test_load_returns_checkpoint(self, mgr: CheckpointManager):
        cp = _make_checkpoint("ckpt-abc")
        await mgr.save(cp)
        loaded = await mgr.load_by_id("ckpt-abc")
        assert loaded is not None
        assert loaded.checkpoint_id == "ckpt-abc"

    async def test_load_unknown_id_returns_none(self, mgr: CheckpointManager):
        result = await mgr.load_by_id("nonexistent")
        assert result is None

    async def test_roundtrip_preserves_all_fields(self, mgr: CheckpointManager):
        cp = _make_checkpoint("ckpt-rt", session_id="s42", plan_id="p42", reason="overlap")
        await mgr.save(cp)
        loaded = await mgr.load_by_id("ckpt-rt")
        assert loaded is not None
        assert loaded.session_id == "s42"
        assert loaded.plan_id == "p42"
        assert loaded.reason == "overlap"
        assert loaded.completed_step_ids == ["step-a", "step-b"]
        assert loaded.pending_step_ids == ["step-c"]
        assert loaded.latest_reflection == "Made good progress on auth module."
        assert loaded.state.request_count == 5
        assert loaded.state.token_estimate == 2000

    async def test_roundtrip_state_status(self, mgr: CheckpointManager):
        cp = _make_checkpoint("ckpt-status")
        cp.state.status = "reflecting"
        await mgr.save(cp)
        loaded = await mgr.load_by_id("ckpt-status")
        assert loaded.state.status == "reflecting"


# ── load_latest ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestLoadLatest:
    async def test_load_latest_returns_most_recent(self, mgr: CheckpointManager):
        cp1 = _make_checkpoint("ckpt-old", session_id="sess-A")
        cp2 = _make_checkpoint("ckpt-new", session_id="sess-A")
        await mgr.save(cp1)
        # Small pause isn't needed since insert order matters in our scan
        await mgr.save(cp2)
        latest = await mgr.load_latest("sess-A")
        assert latest is not None
        assert latest.checkpoint_id == "ckpt-new"

    async def test_load_latest_no_checkpoints_returns_none(self, mgr: CheckpointManager):
        result = await mgr.load_latest("nonexistent-session")
        assert result is None

    async def test_load_latest_ignores_other_sessions(self, mgr: CheckpointManager):
        await mgr.save(_make_checkpoint("ckpt-sess-1", session_id="sess-1"))
        await mgr.save(_make_checkpoint("ckpt-sess-2", session_id="sess-2"))
        latest = await mgr.load_latest("sess-1")
        assert latest is not None
        assert latest.session_id == "sess-1"


# ── list_session_checkpoints ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestListSessionCheckpoints:
    async def test_returns_ids_for_session(self, mgr: CheckpointManager):
        for i in range(3):
            await mgr.save(_make_checkpoint(f"ckpt-{i}", session_id="sess-list"))
        ids = await mgr.list_session_checkpoints("sess-list")
        assert len(ids) == 3
        for cid in ids:
            assert cid.startswith("ckpt-")

    async def test_empty_for_unknown_session(self, mgr: CheckpointManager):
        ids = await mgr.list_session_checkpoints("no-such-session")
        assert ids == []

    async def test_ordered_newest_first(self, mgr: CheckpointManager):
        await mgr.save(_make_checkpoint("ckpt-first", session_id="sess-order"))
        await mgr.save(_make_checkpoint("ckpt-second", session_id="sess-order"))
        await mgr.save(_make_checkpoint("ckpt-third", session_id="sess-order"))
        ids = await mgr.list_session_checkpoints("sess-order")
        # Most recently inserted row has latest created_at
        assert ids[0] == "ckpt-third"

    async def test_only_returns_matching_session(self, mgr: CheckpointManager):
        await mgr.save(_make_checkpoint("ckpt-a", session_id="sess-X"))
        await mgr.save(_make_checkpoint("ckpt-b", session_id="sess-Y"))
        ids = await mgr.list_session_checkpoints("sess-X")
        assert ids == ["ckpt-a"]
