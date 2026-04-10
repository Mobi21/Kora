"""Phase 4 quality tier 1 tests."""
import pytest
from kora_v2.core.models import QualityTurnMetrics, QualityGateResult


class TestQualityCollector:
    def setup_method(self):
        from kora_v2.quality.tier1 import QualityCollector
        self.collector = QualityCollector()

    def test_record_turn(self):
        metrics = self.collector.record_turn(
            session_id="test", turn=1, latency_ms=1200,
            tool_calls=3, worker_dispatches=1, tokens_used=5000,
        )
        assert isinstance(metrics, QualityTurnMetrics)
        assert metrics.latency_ms == 1200
        assert metrics.tool_calls == 3

    def test_get_session_metrics(self):
        self.collector.record_turn("s1", 1, 1000, 2, 0, 3000)
        self.collector.record_turn("s1", 2, 800, 1, 1, 4000)
        metrics = self.collector.get_session_metrics("s1")
        assert len(metrics) == 2

    def test_get_session_metrics_empty(self):
        metrics = self.collector.get_session_metrics("nonexistent")
        assert metrics == []

    def test_average_latency(self):
        self.collector.record_turn("s1", 1, 1000, 0, 0, 0)
        self.collector.record_turn("s1", 2, 2000, 0, 0, 0)
        assert self.collector.average_latency("s1") == 1500.0

    def test_average_latency_empty(self):
        assert self.collector.average_latency("empty") == 0.0

    def test_total_tool_calls(self):
        self.collector.record_turn("s1", 1, 100, tool_calls=3)
        self.collector.record_turn("s1", 2, 100, tool_calls=5)
        assert self.collector.total_tool_calls("s1") == 8

    def test_total_tokens(self):
        self.collector.record_turn("s1", 1, 100, tokens_used=3000)
        self.collector.record_turn("s1", 2, 100, tokens_used=4000)
        assert self.collector.total_tokens("s1") == 7000

    def test_gate_pass_rate_all_pass(self):
        gates = [QualityGateResult(gate_name="schema", passed=True)]
        self.collector.record_turn("s1", 1, 100, gate_results=gates)
        assert self.collector.gate_pass_rate("s1") == 1.0

    def test_gate_pass_rate_mixed(self):
        gates = [
            QualityGateResult(gate_name="schema", passed=True),
            QualityGateResult(gate_name="rules", passed=False, reason="failed"),
        ]
        self.collector.record_turn("s1", 1, 100, gate_results=gates)
        assert self.collector.gate_pass_rate("s1") == 0.5

    def test_gate_pass_rate_no_gates(self):
        self.collector.record_turn("s1", 1, 100)
        assert self.collector.gate_pass_rate("s1") == 1.0

    def test_clear_session(self):
        self.collector.record_turn("s1", 1, 100)
        self.collector.clear_session("s1")
        assert self.collector.get_session_metrics("s1") == []

    def test_multiple_sessions_isolated(self):
        self.collector.record_turn("s1", 1, 100)
        self.collector.record_turn("s2", 1, 200)
        assert len(self.collector.get_session_metrics("s1")) == 1
        assert len(self.collector.get_session_metrics("s2")) == 1
