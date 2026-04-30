"""Tests for QualityCollector DB persistence.

Validates that persist_turn writes correct rows to quality_metrics,
skips when db_path is None, and handles DB errors gracefully.
"""
from __future__ import annotations

import asyncio

import pytest

from kora_v2.core.models import QualityGateResult
from kora_v2.quality.tier1 import QualityCollector


@pytest.fixture
def db_path(tmp_path):
    """Create operational.db with the quality_metrics schema."""
    from kora_v2.core.db import init_operational_db

    path = tmp_path / "operational.db"
    asyncio.run(init_operational_db(path))
    return path


class TestPersistTurn:
    """persist_turn should write metric rows to quality_metrics table."""

    @pytest.mark.asyncio
    async def test_writes_scalar_metrics(self, db_path):
        collector = QualityCollector(db_path=db_path)
        metrics = collector.record_turn(
            session_id="sess-abc",
            turn=1,
            latency_ms=1200,
            tool_calls=3,
            worker_dispatches=1,
            tokens_used=4500,
        )
        await collector.persist_turn(metrics)

        import aiosqlite

        async with aiosqlite.connect(str(db_path)) as db:
            async with db.execute(
                "SELECT metric_name, metric_value FROM quality_metrics WHERE session_id='sess-abc' ORDER BY metric_name"
            ) as cur:
                rows = await cur.fetchall()

        names = {r[0] for r in rows}
        assert names == {"latency_ms", "tool_calls", "worker_dispatches", "tokens_used"}

        # Verify values
        by_name = {r[0]: r[1] for r in rows}
        assert by_name["latency_ms"] == 1200.0
        assert by_name["tool_calls"] == 3.0
        assert by_name["worker_dispatches"] == 1.0
        assert by_name["tokens_used"] == 4500.0

    @pytest.mark.asyncio
    async def test_writes_gate_results(self, db_path):
        collector = QualityCollector(db_path=db_path)
        metrics = collector.record_turn(
            session_id="sess-abc",
            turn=2,
            latency_ms=800,
            gate_results=[
                QualityGateResult(gate_name="coherence", passed=True),
                QualityGateResult(gate_name="safety", passed=False, reason="flagged"),
            ],
        )
        await collector.persist_turn(metrics)

        import aiosqlite

        async with aiosqlite.connect(str(db_path)) as db:
            async with db.execute(
                "SELECT metric_name, metric_value FROM quality_metrics WHERE session_id='sess-abc' AND metric_name LIKE 'gate_%'"
            ) as cur:
                rows = await cur.fetchall()

        by_name = {r[0]: r[1] for r in rows}
        assert by_name["gate_coherence"] == 1.0
        assert by_name["gate_safety"] == 0.0

    @pytest.mark.asyncio
    async def test_turn_number_recorded(self, db_path):
        collector = QualityCollector(db_path=db_path)
        metrics = collector.record_turn(
            session_id="sess-xyz",
            turn=5,
            latency_ms=600,
        )
        await collector.persist_turn(metrics)

        import aiosqlite

        async with aiosqlite.connect(str(db_path)) as db:
            async with db.execute(
                "SELECT DISTINCT turn_number FROM quality_metrics WHERE session_id='sess-xyz'"
            ) as cur:
                rows = await cur.fetchall()

        assert len(rows) == 1
        assert rows[0][0] == 5

    @pytest.mark.asyncio
    async def test_skips_when_no_db_path(self):
        collector = QualityCollector(db_path=None)
        metrics = collector.record_turn(
            session_id="sess-no-db",
            turn=1,
            latency_ms=500,
        )
        # Should return without error
        await collector.persist_turn(metrics)

    @pytest.mark.asyncio
    async def test_handles_db_error_gracefully(self, tmp_path):
        """persist_turn should not raise even when the DB path is bogus."""
        bad_path = tmp_path / "nonexistent_dir" / "operational.db"
        collector = QualityCollector(db_path=bad_path)
        metrics = collector.record_turn(
            session_id="sess-bad",
            turn=1,
            latency_ms=300,
        )
        # Should log warning but not raise
        await collector.persist_turn(metrics)

    @pytest.mark.asyncio
    async def test_in_memory_still_works(self, db_path):
        """In-memory storage should work alongside DB persistence."""
        collector = QualityCollector(db_path=db_path)
        collector.record_turn(session_id="sess-mem", turn=1, latency_ms=100)
        collector.record_turn(session_id="sess-mem", turn=2, latency_ms=200)

        assert len(collector.get_session_metrics("sess-mem")) == 2
        assert collector.average_latency("sess-mem") == 150.0
        assert collector.total_tokens("sess-mem") == 0


class TestQualityCollectorInit:
    """Verify backward compatibility of the __init__ signature."""

    def test_default_no_db_path(self):
        collector = QualityCollector()
        assert collector._db_path is None

    def test_explicit_db_path(self, db_path):
        collector = QualityCollector(db_path=db_path)
        assert collector._db_path == db_path
