"""Quality Tier 1: automatic per-turn metric collection.

In-memory storage with optional persistence to operational.db quality_metrics table.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import structlog

from kora_v2.core.models import QualityGateResult, QualityTurnMetrics

log = structlog.get_logger(__name__)


class QualityCollector:
    """Collects per-turn quality metrics in memory with optional DB persistence.

    Usage:
        collector = QualityCollector(db_path=Path("data/operational.db"))
        metrics = collector.record_turn(session_id="abc", turn=1, latency_ms=1200, ...)
        await collector.persist_turn(metrics)  # write to DB
        all_metrics = collector.get_session_metrics("abc")
        avg = collector.average_latency("abc")
    """

    def __init__(self, db_path: Path | None = None):
        self._metrics: dict[str, list[QualityTurnMetrics]] = {}  # session_id -> metrics list
        self._db_path = db_path

    def record_turn(
        self,
        session_id: str,
        turn: int,
        latency_ms: int,
        tool_calls: int = 0,
        worker_dispatches: int = 0,
        tokens_used: int = 0,
        gate_results: list[QualityGateResult] | None = None,
        compaction_triggered: bool = False,
    ) -> QualityTurnMetrics:
        """Record metrics for a single turn. Returns the created metrics object."""
        metrics = QualityTurnMetrics(
            session_id=session_id,
            turn=turn,
            latency_ms=latency_ms,
            tool_calls=tool_calls,
            worker_dispatches=worker_dispatches,
            tokens_used=tokens_used,
            gate_results=gate_results or [],
            compaction_triggered=compaction_triggered,
        )
        if session_id not in self._metrics:
            self._metrics[session_id] = []
        self._metrics[session_id].append(metrics)
        log.debug("quality_turn_recorded", session_id=session_id, turn=turn, latency_ms=latency_ms)
        return metrics

    async def persist_turn(self, metrics: QualityTurnMetrics) -> None:
        """Write turn metrics to operational.db quality_metrics table.

        Writes one row per scalar metric (latency_ms, tool_calls,
        worker_dispatches, tokens_used) plus one row per quality gate result.
        Silently skips when ``db_path`` is None or on DB errors.
        """
        if not self._db_path:
            return
        try:
            import aiosqlite

            async with aiosqlite.connect(str(self._db_path)) as db:
                now = datetime.now(UTC).isoformat()
                for name, value in [
                    ("latency_ms", metrics.latency_ms),
                    ("tool_calls", metrics.tool_calls),
                    ("worker_dispatches", metrics.worker_dispatches),
                    ("tokens_used", metrics.tokens_used),
                ]:
                    await db.execute(
                        "INSERT INTO quality_metrics (session_id, turn_number, metric_name, metric_value, recorded_at) VALUES (?,?,?,?,?)",
                        (metrics.session_id, metrics.turn, name, float(value), now),
                    )
                # Also write gate results
                for gate in metrics.gate_results:
                    await db.execute(
                        "INSERT INTO quality_metrics (session_id, turn_number, metric_name, metric_value, recorded_at) VALUES (?,?,?,?,?)",
                        (metrics.session_id, metrics.turn, f"gate_{gate.gate_name}", 1.0 if gate.passed else 0.0, now),
                    )
                await db.commit()
        except Exception:
            log.warning("quality_persist_failed", session_id=metrics.session_id)

    def get_session_metrics(self, session_id: str) -> list[QualityTurnMetrics]:
        """Get all metrics for a session."""
        return self._metrics.get(session_id, [])

    def average_latency(self, session_id: str) -> float:
        """Average latency across all turns in a session."""
        metrics = self.get_session_metrics(session_id)
        if not metrics:
            return 0.0
        return sum(m.latency_ms for m in metrics) / len(metrics)

    def total_tool_calls(self, session_id: str) -> int:
        """Total tool calls across all turns in a session."""
        return sum(m.tool_calls for m in self.get_session_metrics(session_id))

    def total_tokens(self, session_id: str) -> int:
        """Total tokens used across all turns in a session."""
        return sum(m.tokens_used for m in self.get_session_metrics(session_id))

    def gate_pass_rate(self, session_id: str) -> float:
        """Fraction of quality gates that passed across all turns."""
        all_gates = []
        for m in self.get_session_metrics(session_id):
            all_gates.extend(m.gate_results)
        if not all_gates:
            return 1.0  # No gates = all pass
        passed = sum(1 for g in all_gates if g.passed)
        return passed / len(all_gates)

    def clear_session(self, session_id: str) -> None:
        """Clear metrics for a session."""
        self._metrics.pop(session_id, None)
