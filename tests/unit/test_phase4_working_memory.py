"""Tests for Phase 4: working memory loader and energy inference."""

import pytest
from unittest.mock import patch


class TestEnergyEstimate:
    def test_morning_energy(self):
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=9):
            est = estimate_energy()
        assert est.level in ("medium", "high")
        assert est.source == "time_of_day"

    def test_afternoon_crash(self):
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=14):
            est = estimate_energy()
        assert est.level in ("low", "medium")

    def test_late_night_low(self):
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=23):
            est = estimate_energy()
        assert est.level == "low"

    def test_peak_morning(self):
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=10):
            est = estimate_energy()
        assert est.level == "high"
        assert est.focus == "locked_in"

    def test_confidence_is_low_without_profile(self):
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=10):
            est = estimate_energy()
        assert est.confidence <= 0.5

    def test_early_morning_low(self):
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=3):
            est = estimate_energy()
        assert est.level == "low"
        assert est.focus == "scattered"

    def test_pre_morning_boundary(self):
        """Hour 6 is medium energy."""
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=6):
            est = estimate_energy()
        assert est.level == "medium"

    def test_post_lunch_dip(self):
        """Hour 13 (1pm) is post-lunch dip — medium energy."""
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=13):
            est = estimate_energy()
        assert est.level == "medium"

    def test_evening_medium(self):
        """Hour 20 (8pm) is medium energy."""
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=20):
            est = estimate_energy()
        assert est.level == "medium"

    def test_returns_energy_estimate_model(self):
        from kora_v2.context.working_memory import estimate_energy
        from kora_v2.core.models import EnergyEstimate
        with patch("kora_v2.context.working_memory._now_hour", return_value=10):
            est = estimate_energy()
        assert isinstance(est, EnergyEstimate)

    def test_source_field_is_time_of_day(self):
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=10):
            est = estimate_energy()
        assert est.source == "time_of_day"

    def test_confidence_is_0_4_without_profile(self):
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=10):
            est = estimate_energy()
        assert est.confidence == 0.4

    def test_adhd_profile_none_still_works(self):
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=10):
            est = estimate_energy(adhd_profile=None)
        assert est.level == "high"

    def test_midnight_is_low(self):
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=0):
            est = estimate_energy()
        assert est.level == "low"
        assert est.focus == "scattered"

    def test_recovery_window(self):
        """Hour 17 (5pm) is in recovery window — medium energy."""
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=17):
            est = estimate_energy()
        assert est.level == "medium"

    def test_adhd_crash_window_hour_15(self):
        """Hour 15 (3pm) is in ADHD crash window — low energy."""
        from kora_v2.context.working_memory import estimate_energy
        with patch("kora_v2.context.working_memory._now_hour", return_value=15):
            est = estimate_energy()
        assert est.level == "low"
        assert est.focus == "scattered"


class TestWorkingMemoryLoader:
    @pytest.mark.asyncio
    async def test_empty_when_no_data(self):
        from kora_v2.context.working_memory import WorkingMemoryLoader
        loader = WorkingMemoryLoader(projection_db=None, items_db=None)
        items = await loader.load()
        assert items == []

    @pytest.mark.asyncio
    async def test_includes_bridge_items(self):
        from kora_v2.context.working_memory import WorkingMemoryLoader
        from kora_v2.core.models import SessionBridge
        bridge = SessionBridge(
            session_id="prev",
            summary="Discussed morning routine",
            open_threads=["Pick alarm time", "Set up medication reminder"],
        )
        loader = WorkingMemoryLoader(projection_db=None, items_db=None, last_bridge=bridge)
        items = await loader.load()
        bridge_items = [i for i in items if i.source == "bridge"]
        assert len(bridge_items) == 2
        assert bridge_items[0].priority == 1

    @pytest.mark.asyncio
    async def test_max_5_items(self):
        from kora_v2.context.working_memory import WorkingMemoryLoader
        from kora_v2.core.models import SessionBridge
        bridge = SessionBridge(
            session_id="prev",
            summary="test",
            open_threads=[f"thread-{i}" for i in range(10)],
        )
        loader = WorkingMemoryLoader(projection_db=None, items_db=None, last_bridge=bridge)
        items = await loader.load()
        assert len(items) <= 5

    @pytest.mark.asyncio
    async def test_sorted_by_priority(self):
        from kora_v2.context.working_memory import WorkingMemoryLoader
        from kora_v2.core.models import SessionBridge
        bridge = SessionBridge(
            session_id="prev", summary="test",
            open_threads=["thread1"],
        )
        loader = WorkingMemoryLoader(projection_db=None, items_db=None, last_bridge=bridge)
        items = await loader.load()
        if len(items) > 1:
            for i in range(len(items) - 1):
                assert items[i].priority <= items[i + 1].priority

    @pytest.mark.asyncio
    async def test_bridge_with_empty_threads(self):
        from kora_v2.context.working_memory import WorkingMemoryLoader
        from kora_v2.core.models import SessionBridge
        bridge = SessionBridge(
            session_id="prev",
            summary="Nothing open",
            open_threads=[],
        )
        loader = WorkingMemoryLoader(projection_db=None, items_db=None, last_bridge=bridge)
        items = await loader.load()
        assert items == []

    @pytest.mark.asyncio
    async def test_bridge_thread_content_preserved(self):
        from kora_v2.context.working_memory import WorkingMemoryLoader
        from kora_v2.core.models import SessionBridge
        bridge = SessionBridge(
            session_id="prev",
            summary="test",
            open_threads=["Follow up on medication dosage"],
        )
        loader = WorkingMemoryLoader(projection_db=None, items_db=None, last_bridge=bridge)
        items = await loader.load()
        assert items[0].content == "Follow up on medication dosage"
        assert items[0].source == "bridge"

    @pytest.mark.asyncio
    async def test_exactly_5_bridge_threads_not_truncated(self):
        from kora_v2.context.working_memory import WorkingMemoryLoader
        from kora_v2.core.models import SessionBridge
        bridge = SessionBridge(
            session_id="prev",
            summary="test",
            open_threads=[f"thread-{i}" for i in range(5)],
        )
        loader = WorkingMemoryLoader(projection_db=None, items_db=None, last_bridge=bridge)
        items = await loader.load()
        assert len(items) == 5

    @pytest.mark.asyncio
    async def test_returns_working_memory_item_objects(self):
        from kora_v2.context.working_memory import WorkingMemoryLoader
        from kora_v2.core.models import SessionBridge, WorkingMemoryItem
        bridge = SessionBridge(
            session_id="prev",
            summary="test",
            open_threads=["thread1"],
        )
        loader = WorkingMemoryLoader(projection_db=None, items_db=None, last_bridge=bridge)
        items = await loader.load()
        assert all(isinstance(item, WorkingMemoryItem) for item in items)

    @pytest.mark.asyncio
    async def test_no_bridge_no_items(self):
        from kora_v2.context.working_memory import WorkingMemoryLoader
        loader = WorkingMemoryLoader()
        items = await loader.load()
        assert len(items) == 0
