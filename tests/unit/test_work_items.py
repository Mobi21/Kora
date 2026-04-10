"""Unit tests for kora_v2.daemon.work_items — background work item factories."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kora_v2.daemon.work_items import (
    make_autonomous_update_item,
    make_bridge_pruning_item,
    make_memory_consolidation_item,
    make_signal_scanner_item,
    make_skill_refinement_item,
)
from kora_v2.daemon.worker import WorkItem


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_container(**overrides: Any) -> MagicMock:
    """Build a minimal mock container that won't crash handlers."""
    container = MagicMock()
    container.settings = MagicMock()
    container.settings.data_dir = Path("/tmp/kora_test_data")
    container.settings.memory = MagicMock()
    container.settings.memory.kora_memory_path = "/tmp/kora_test_memory"
    # Defaults: no services present
    container.memory_store = None
    container.projection_db = None
    container.embedding_service = None
    container.signal_scanner = None
    container.session_manager = None
    container.event_emitter = None
    container.llm = None
    container._skill_loader = None
    container.settings.daemon = MagicMock()
    container.settings.daemon.idle_check_interval = 300
    container.settings.daemon.background_safe_interval = 60
    for key, val in overrides.items():
        setattr(container, key, val)
    return container


# =====================================================================
# 1. Factory return types and metadata
# =====================================================================


class TestFactoryMetadata:
    """All 5 factories return valid WorkItem instances with correct metadata."""

    def test_memory_consolidation_metadata(self) -> None:
        item = make_memory_consolidation_item(_make_container())
        assert isinstance(item, WorkItem)
        assert item.name == "memory_consolidation"
        assert item.priority == 1
        assert item.tier == "idle"
        assert item.interval_seconds == 600

    def test_signal_scanner_metadata(self) -> None:
        item = make_signal_scanner_item(_make_container())
        assert isinstance(item, WorkItem)
        assert item.name == "signal_scanner"
        assert item.priority == 2
        assert item.tier == "safe"
        assert item.interval_seconds == 120

    def test_autonomous_update_metadata(self) -> None:
        item = make_autonomous_update_item(_make_container())
        assert isinstance(item, WorkItem)
        assert item.name == "autonomous_update_delivery"
        assert item.priority == 3
        assert item.tier == "safe"
        assert item.interval_seconds == 30

    def test_bridge_pruning_metadata(self) -> None:
        item = make_bridge_pruning_item(_make_container())
        assert isinstance(item, WorkItem)
        assert item.name == "session_bridge_pruning"
        assert item.priority == 4
        assert item.tier == "idle"
        assert item.interval_seconds == 3600

    def test_skill_refinement_metadata(self) -> None:
        item = make_skill_refinement_item(_make_container())
        assert isinstance(item, WorkItem)
        assert item.name == "skill_refinement"
        assert item.priority == 5
        assert item.tier == "idle"
        assert item.interval_seconds == 86400


# =====================================================================
# 2. Handlers with no services (graceful no-op)
# =====================================================================


class TestHandlersNoServices:
    """Handlers should not crash when the container has no services."""

    @pytest.mark.asyncio
    async def test_memory_consolidation_noop(self) -> None:
        item = make_memory_consolidation_item(_make_container())
        await item.handler()  # must not raise

    @pytest.mark.asyncio
    async def test_signal_scanner_noop(self) -> None:
        item = make_signal_scanner_item(_make_container())
        await item.handler()

    @pytest.mark.asyncio
    async def test_autonomous_update_noop_no_db(self) -> None:
        """Handler skips when operational.db does not exist."""
        item = make_autonomous_update_item(_make_container())
        await item.handler()

    @pytest.mark.asyncio
    async def test_bridge_pruning_noop_no_dir(self) -> None:
        item = make_bridge_pruning_item(_make_container())
        await item.handler()

    @pytest.mark.asyncio
    async def test_skill_refinement_noop(self) -> None:
        item = make_skill_refinement_item(_make_container())
        await item.handler()


# =====================================================================
# 3. Handlers with services present
# =====================================================================


class TestHandlersWithServices:
    """Handlers call expected service methods when services exist."""

    @pytest.mark.asyncio
    async def test_memory_consolidation_calls_services(self) -> None:
        store = AsyncMock()
        store.consolidate = AsyncMock()
        proj = AsyncMock()
        proj.deduplicate = AsyncMock()
        emb = AsyncMock()
        emb.backfill_missing = AsyncMock()

        container = _make_container(
            memory_store=store,
            projection_db=proj,
            embedding_service=emb,
        )
        item = make_memory_consolidation_item(container)
        await item.handler()

        store.consolidate.assert_awaited_once()
        proj.deduplicate.assert_awaited_once()
        emb.backfill_missing.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_signal_scanner_calls_scan_bridge(self) -> None:
        bridge = MagicMock()
        scanner = AsyncMock()
        scanner.scan_bridge = AsyncMock()
        session_mgr = AsyncMock()
        session_mgr.load_last_bridge = AsyncMock(return_value=bridge)

        container = _make_container(
            signal_scanner=scanner,
            session_manager=session_mgr,
        )
        item = make_signal_scanner_item(container)
        await item.handler()

        session_mgr.load_last_bridge.assert_awaited_once()
        scanner.scan_bridge.assert_awaited_once_with(bridge)

    @pytest.mark.asyncio
    async def test_signal_scanner_skips_no_bridge(self) -> None:
        scanner = AsyncMock()
        scanner.scan_bridge = AsyncMock()
        session_mgr = AsyncMock()
        session_mgr.load_last_bridge = AsyncMock(return_value=None)

        container = _make_container(
            signal_scanner=scanner,
            session_manager=session_mgr,
        )
        item = make_signal_scanner_item(container)
        await item.handler()

        scanner.scan_bridge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skill_refinement_calls_llm(self) -> None:
        skill = MagicMock()
        skill.name = "test_skill"
        skill.display_name = "Test Skill"
        skill.tools = ["tool_a"]
        skill.guidance = "Some guidance"

        loader = MagicMock()
        loader.get_all_skills.return_value = [skill]

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="assessment")

        container = _make_container(_skill_loader=loader, llm=llm)
        item = make_skill_refinement_item(container)
        await item.handler()

        llm.generate.assert_awaited_once()
        call_kwargs = llm.generate.call_args[1]
        assert call_kwargs["max_tokens"] == 500
        assert "test_skill" in call_kwargs["messages"][1]["content"]

    @pytest.mark.asyncio
    async def test_skill_refinement_rotates_skills(self) -> None:
        skills = [MagicMock(name=f"s{i}", display_name=f"S{i}", tools=[], guidance="") for i in range(3)]
        # MagicMock overrides .name, set it explicitly
        for i, s in enumerate(skills):
            s.name = f"skill_{i}"

        loader = MagicMock()
        loader.get_all_skills.return_value = skills

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="ok")

        container = _make_container(_skill_loader=loader, llm=llm)
        item = make_skill_refinement_item(container)

        # First call: index 0
        await item.handler()
        first_content = llm.generate.call_args[1]["messages"][1]["content"]
        assert "skill_0" in first_content

        llm.generate.reset_mock()

        # Second call: index 1
        await item.handler()
        second_content = llm.generate.call_args[1]["messages"][1]["content"]
        assert "skill_1" in second_content


# =====================================================================
# 4. Bridge pruning with real files
# =====================================================================


class TestBridgePruning:
    """Bridge pruning deletes old files and leaves recent ones."""

    @pytest.mark.asyncio
    async def test_prunes_old_bridges(self, tmp_path: Path) -> None:
        bridges_dir = tmp_path / ".kora" / "bridges"
        bridges_dir.mkdir(parents=True)

        # Create an old file (mtime 60 days ago)
        old_file = bridges_dir / "old_bridge.md"
        old_file.write_text("old bridge content")
        old_mtime = time.time() - (60 * 86400)
        import os
        os.utime(old_file, (old_mtime, old_mtime))

        # Create a recent file
        new_file = bridges_dir / "new_bridge.md"
        new_file.write_text("recent bridge content")

        container = _make_container()
        container.settings.memory.kora_memory_path = str(tmp_path)

        item = make_bridge_pruning_item(container)
        await item.handler()

        assert not old_file.exists(), "Old bridge should be deleted"
        assert new_file.exists(), "Recent bridge should be kept"

    @pytest.mark.asyncio
    async def test_noop_when_no_bridges_dir(self, tmp_path: Path) -> None:
        container = _make_container()
        container.settings.memory.kora_memory_path = str(tmp_path)

        item = make_bridge_pruning_item(container)
        await item.handler()  # must not raise

    @pytest.mark.asyncio
    async def test_noop_when_all_recent(self, tmp_path: Path) -> None:
        bridges_dir = tmp_path / ".kora" / "bridges"
        bridges_dir.mkdir(parents=True)

        recent = bridges_dir / "recent.md"
        recent.write_text("recent")

        container = _make_container()
        container.settings.memory.kora_memory_path = str(tmp_path)

        item = make_bridge_pruning_item(container)
        await item.handler()

        assert recent.exists()


# =====================================================================
# 5. Autonomous update handler
# =====================================================================


class TestAutonomousUpdateHandler:
    """Tests for the autonomous update delivery work item."""

    @pytest.mark.asyncio
    async def test_skips_when_no_db(self) -> None:
        container = _make_container()
        container.settings.data_dir = Path("/nonexistent/path")
        item = make_autonomous_update_item(container)
        await item.handler()  # must not raise

    @pytest.mark.asyncio
    async def test_skips_when_no_table(self, tmp_path: Path) -> None:
        """Handler returns early when autonomous_updates table doesn't exist."""
        import aiosqlite

        db_path = tmp_path / "operational.db"
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute("CREATE TABLE dummy (id INTEGER)")
            await db.commit()

        container = _make_container()
        container.settings.data_dir = tmp_path

        item = make_autonomous_update_item(container)
        await item.handler()  # must not raise

    @pytest.mark.asyncio
    async def test_delivers_unread_updates(self, tmp_path: Path) -> None:
        """Handler emits events and marks rows as delivered."""
        import aiosqlite

        from kora_v2.core.events import EventType

        db_path = tmp_path / "operational.db"
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                """
                CREATE TABLE autonomous_updates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    plan_id TEXT,
                    update_type TEXT,
                    summary TEXT,
                    payload TEXT,
                    delivered INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "INSERT INTO autonomous_updates "
                "(session_id, plan_id, update_type, summary, payload, delivered, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                ("sess1", "plan1", "checkpoint", "Step 1 done", "{}", "2026-01-01T00:00:00"),
            )
            await db.commit()

        emitter = AsyncMock()
        emitter.emit = AsyncMock()

        container = _make_container(event_emitter=emitter)
        container.settings.data_dir = tmp_path

        item = make_autonomous_update_item(container)
        await item.handler()

        emitter.emit.assert_awaited_once()
        call_kwargs = emitter.emit.call_args
        assert call_kwargs[0][0] == EventType.NOTIFICATION_SENT

        # Verify row marked as delivered
        async with aiosqlite.connect(str(db_path)) as db:
            cur = await db.execute(
                "SELECT delivered FROM autonomous_updates WHERE id = 1"
            )
            row = await cur.fetchone()
            assert row is not None
            assert row[0] == 1

    @pytest.mark.asyncio
    async def test_noop_when_all_delivered(self, tmp_path: Path) -> None:
        """Handler does nothing when all rows are already delivered."""
        import aiosqlite

        db_path = tmp_path / "operational.db"
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                """
                CREATE TABLE autonomous_updates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    plan_id TEXT,
                    update_type TEXT,
                    summary TEXT,
                    payload TEXT,
                    delivered INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "INSERT INTO autonomous_updates "
                "(session_id, plan_id, update_type, summary, payload, delivered, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                ("sess1", "plan1", "checkpoint", "Already done", "{}", "2026-01-01T00:00:00"),
            )
            await db.commit()

        emitter = AsyncMock()
        emitter.emit = AsyncMock()

        container = _make_container(event_emitter=emitter)
        container.settings.data_dir = tmp_path

        item = make_autonomous_update_item(container)
        await item.handler()

        emitter.emit.assert_not_awaited()


# =====================================================================
# 6. All items have unique names and correct tier distribution
# =====================================================================


class TestItemCollection:
    """Validate the full set of work items as a collection."""

    def test_unique_names(self) -> None:
        container = _make_container()
        items = [
            make_memory_consolidation_item(container),
            make_signal_scanner_item(container),
            make_autonomous_update_item(container),
            make_bridge_pruning_item(container),
            make_skill_refinement_item(container),
        ]
        names = [i.name for i in items]
        assert len(names) == len(set(names)), f"Duplicate names: {names}"

    def test_priority_ordering(self) -> None:
        container = _make_container()
        items = [
            make_memory_consolidation_item(container),
            make_signal_scanner_item(container),
            make_autonomous_update_item(container),
            make_bridge_pruning_item(container),
            make_skill_refinement_item(container),
        ]
        priorities = [i.priority for i in items]
        assert priorities == sorted(priorities), "Priorities should be ascending"

    def test_tier_distribution(self) -> None:
        container = _make_container()
        items = [
            make_memory_consolidation_item(container),
            make_signal_scanner_item(container),
            make_autonomous_update_item(container),
            make_bridge_pruning_item(container),
            make_skill_refinement_item(container),
        ]
        tiers = {i.tier for i in items}
        assert "safe" in tiers, "Should have at least one safe-tier item"
        assert "idle" in tiers, "Should have at least one idle-tier item"

    def test_all_handlers_callable(self) -> None:
        container = _make_container()
        items = [
            make_memory_consolidation_item(container),
            make_signal_scanner_item(container),
            make_autonomous_update_item(container),
            make_bridge_pruning_item(container),
            make_skill_refinement_item(container),
        ]
        for item in items:
            assert callable(item.handler), f"{item.name} handler is not callable"
