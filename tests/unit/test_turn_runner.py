"""Tests for kora_v2.runtime.turn_runner — circuit breaker and turn tracing."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from kora_v2.core.db import init_operational_db
from kora_v2.runtime.turn_runner import (
    CompactionCircuitBreaker,
    GraphTurnRunner,
    _extract_open_decision_topics,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_container(tmp_path):
    """Build a minimal container-like object with a real data dir."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return SimpleNamespace(settings=SimpleNamespace(data_dir=data_dir))


async def _init_db(tmp_path):
    """Create the operational DB schema in *tmp_path*/data."""
    db_path = tmp_path / "data" / "operational.db"
    await init_operational_db(db_path)
    return db_path


def _make_graph_input(content: str = "Hello Kora") -> dict:
    return {
        "messages": [{"role": "user", "content": content}],
        "session_id": "sess-1",
        "turn_count": 1,
    }


# ── CompactionCircuitBreaker ────────────────────────────────────────────


class TestCompactionCircuitBreaker:
    """Verify check/record/trip/reset cycle."""

    def test_initial_state_allows_compaction(self) -> None:
        cb = CompactionCircuitBreaker()
        assert cb.check() is True
        assert cb.count == 0
        assert cb.tripped is False

    def test_records_under_limit(self) -> None:
        cb = CompactionCircuitBreaker(max_compactions=3)
        cb.record_compaction()
        assert cb.check() is True
        assert cb.count == 1

    def test_trips_at_limit(self) -> None:
        cb = CompactionCircuitBreaker(max_compactions=2)
        cb.record_compaction()
        cb.record_compaction()
        assert cb.check() is False
        assert cb.tripped is True

    def test_trips_stays_tripped(self) -> None:
        cb = CompactionCircuitBreaker(max_compactions=1)
        cb.record_compaction()
        assert cb.check() is False
        # Another record doesn't un-trip
        cb.record_compaction()
        assert cb.check() is False

    def test_reset_clears_state(self) -> None:
        cb = CompactionCircuitBreaker(max_compactions=1)
        cb.record_compaction()
        assert cb.check() is False
        cb.reset()
        assert cb.check() is True
        assert cb.count == 0
        assert cb.tripped is False

    def test_custom_limit(self) -> None:
        cb = CompactionCircuitBreaker(max_compactions=5)
        for _ in range(4):
            cb.record_compaction()
        assert cb.check() is True
        cb.record_compaction()
        assert cb.check() is False


# ── GraphTurnRunner ─────────────────────────────────────────────────────


class TestGraphTurnRunnerRunTurn:
    """Test run_turn success and error paths with a real SQLite DB."""

    async def test_success_path(self, tmp_path) -> None:
        container = _make_container(tmp_path)
        db_path = await _init_db(tmp_path)
        runner = GraphTurnRunner(container)

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "response_content": "Hey there!",
            "tool_call_records": [
                {"name": "recall", "args": {}, "result": "ok"},
            ],
            "messages": [],
        }

        result = await runner.run_turn(
            mock_graph, _make_graph_input(), {"configurable": {"thread_id": "t1"}},
        )

        assert result["response_content"] == "Hey there!"
        mock_graph.ainvoke.assert_awaited_once()

        # Verify trace was written
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute("SELECT * FROM turn_traces")
            rows = await cursor.fetchall()
        assert len(rows) == 1

    async def test_prefetched_task_summary_reaches_graph_input(self, tmp_path) -> None:
        container = _make_container(tmp_path)
        await _init_db(tmp_path)
        task = SimpleNamespace(
            id="task-1",
            stage_name="research",
            state=SimpleNamespace(value="completed"),
            goal="local-first productivity tools",
            result_summary="report written with 5 sources",
            error_message=None,
            completed_at=datetime(2026, 4, 25, tzinfo=UTC),
            pipeline_instance_id="pipe-1",
        )
        instance = SimpleNamespace(
            pipeline_name="proactive_research",
            goal="Research local-first productivity tools",
        )
        engine = SimpleNamespace(
            list_tasks=AsyncMock(return_value=[task]),
            acknowledge_task=AsyncMock(return_value=True),
            instance_registry=SimpleNamespace(
                load=AsyncMock(return_value=instance),
            ),
        )
        container.orchestration_engine = engine
        runner = GraphTurnRunner(container)
        graph_input = _make_graph_input("what did you finish?")

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "response_content": "The research finished.",
            "tool_call_records": [],
            "messages": [],
        }

        await runner.run_turn(
            mock_graph,
            graph_input,
            {"configurable": {"thread_id": "t1"}},
        )

        shaped = graph_input["_orchestration_tasks"][0]
        assert shaped["state"] == "completed"
        assert shaped["goal"] == "local-first productivity tools"
        assert shaped["pipeline_name"] == "proactive_research"
        assert shaped["pipeline_goal"] == "Research local-first productivity tools"
        assert shaped["result_summary"] == "report written with 5 sources"
        engine.acknowledge_task.assert_awaited_once_with("task-1")

    async def test_prefetched_running_task_is_not_acknowledged_before_result(
        self,
        tmp_path,
    ) -> None:
        container = _make_container(tmp_path)
        await _init_db(tmp_path)
        task = SimpleNamespace(
            id="task-1",
            stage_name="research",
            state=SimpleNamespace(value="running"),
            goal="local-first productivity tools",
            result_summary=None,
            error_message=None,
            completed_at=None,
            pipeline_instance_id="pipe-1",
        )
        instance = SimpleNamespace(
            pipeline_name="proactive_research",
            goal="Research local-first productivity tools",
        )
        engine = SimpleNamespace(
            list_tasks=AsyncMock(return_value=[task]),
            acknowledge_task=AsyncMock(return_value=True),
            instance_registry=SimpleNamespace(
                load=AsyncMock(return_value=instance),
            ),
        )
        container.orchestration_engine = engine
        runner = GraphTurnRunner(container)
        graph_input = _make_graph_input("how is the research going?")

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "response_content": "The research is still running.",
            "tool_call_records": [],
            "messages": [],
        }

        await runner.run_turn(
            mock_graph,
            graph_input,
            {"configurable": {"thread_id": "t1"}},
        )

        assert graph_input["_orchestration_tasks"][0]["state"] == "running"
        assert graph_input.get("_orchestration_seen_task_ids") == []
        engine.acknowledge_task.assert_not_awaited()

    async def test_response_open_decision_is_persisted(self, tmp_path) -> None:
        container = _make_container(tmp_path)
        await _init_db(tmp_path)
        engine = SimpleNamespace(
            record_open_decision=AsyncMock(),
        )
        container.orchestration_engine = engine
        runner = GraphTurnRunner(container)

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "response_content": (
                "One open decision: plain CSS vs. inline styles "
                "(never resolved)."
            ),
            "tool_call_records": [],
            "messages": [],
        }

        await runner.run_turn(
            mock_graph,
            _make_graph_input("what do you remember?"),
            {"configurable": {"thread_id": "t1"}},
        )

        engine.record_open_decision.assert_awaited_once()
        kwargs = engine.record_open_decision.await_args.kwargs
        assert kwargs["topic"] == "plain CSS vs. inline styles"
        assert kwargs["posed_in_session"] == "sess-1"

    async def test_response_decision_prompts_are_persisted(self, tmp_path) -> None:
        container = _make_container(tmp_path)
        await _init_db(tmp_path)
        engine = SimpleNamespace(
            record_open_decision=AsyncMock(),
        )
        container.orchestration_engine = engine
        runner = GraphTurnRunner(container)

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "response_content": (
                "One thing to decide: start with TaskRow or schema.sql.\n"
                "Pick one: TaskRow component or db.ts setup."
            ),
            "tool_call_records": [],
            "messages": [],
        }

        await runner.run_turn(
            mock_graph,
            _make_graph_input("what should i do first?"),
            {"configurable": {"thread_id": "t1"}},
        )

        topics = [
            call.kwargs["topic"]
            for call in engine.record_open_decision.await_args_list
        ]
        assert topics == [
            "start with TaskRow or schema.sql",
            "TaskRow component or db.ts setup",
        ]

    async def test_response_unlocked_decision_language_is_persisted(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        container = _make_container(tmp_path)
        await _init_db(tmp_path)
        monkeypatch.setenv("KORA_OPEN_DECISION_AGING_DAYS", "0")
        engine = SimpleNamespace(
            record_open_decision=AsyncMock(),
            record_pending_decision_aging=AsyncMock(),
        )
        container.orchestration_engine = engine
        runner = GraphTurnRunner(container)

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "response_content": (
                "The **brief topic was never locked in** — still open."
            ),
            "tool_call_records": [],
            "messages": [],
        }

        await runner.run_turn(
            mock_graph,
            _make_graph_input("what do you remember?"),
            {"configurable": {"thread_id": "t1"}},
        )

        engine.record_open_decision.assert_awaited_once()
        assert engine.record_open_decision.await_args.kwargs["topic"] == (
            "The brief topic"
        )
        engine.record_pending_decision_aging.assert_awaited_once_with(
            older_than_days=0,
            limit=10,
        )


    def test_extract_open_decisions_from_acceptance_phrasing(self) -> None:
        response = """
## One decision to make now

**Storage granularity:** Do you want tasks to live per day or globally?

| **Topic** | Still not decided — brief is blocked |

**SQLite + Markdown per day vs Dexie only?** — this is the only open architectural question that matters right now.
"""

        assert _extract_open_decision_topics(response) == [
            "Storage granularity: Do you want tasks to live per day or globally?",
            "Topic: Still not decided",
            "SQLite + Markdown per day vs Dexie only?",
        ]

    def test_extract_open_decisions_from_latest_acceptance_phrasing(self) -> None:
        response = """
## One Open Question for You

Do you want to **create blocks in the UI** or keep markdown-only?

| **Open** | Runtime (Bun/Node/Deno), blank slate vs existing repo, CRUD authorship surface |

**Still open (your tomorrow priorities):**
1. Decide reset time for the dashboard.
2. Confirm whether Alex gets the brief.
"""

        assert _extract_open_decision_topics(response) == [
            "Do you want to create blocks in the UI or keep markdown-only?",
            "Runtime (Bun/Node/Deno), blank slate vs existing repo, CRUD authorship surface",
            "Decide reset time for the dashboard",
            "Confirm whether Alex gets the brief",
        ]

    def test_extract_open_decisions_from_unresolved_status_table(self) -> None:
        response = """
| | Status |
|---|---|
| Dashboard direction | **Unresolved** — two specs, no pick |

## What to Decide First

The dashboard direction. Everything else can flow once that's locked.
"""

        assert _extract_open_decision_topics(response) == [
            "Dashboard direction: Unresolved",
            "The dashboard direction. Everything else can flow once that's locked",
        ]

    def test_extract_open_decisions_from_acceptance_status_summary(self) -> None:
        response = """
**Open decisions from you:**
1. Brief audience + purpose
2. Track priority order this week
3. Brief deadline

**Still unresolved from yesterday:**
- Dashboard reset time?
- Confirm whether Alex gets the brief.
"""

        assert _extract_open_decision_topics(response) == [
            "Brief audience + purpose",
            "Track priority order this week",
            "Brief deadline",
            "Dashboard reset time?",
            "Confirm whether Alex gets the brief",
        ]

    async def test_error_path_records_trace(self, tmp_path) -> None:
        container = _make_container(tmp_path)
        db_path = await _init_db(tmp_path)
        runner = GraphTurnRunner(container)

        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = RuntimeError("LLM timeout")

        with pytest.raises(RuntimeError, match="LLM timeout"):
            await runner.run_turn(
                mock_graph,
                _make_graph_input(),
                {"configurable": {"thread_id": "t1"}},
            )

        # Trace should still be recorded with succeeded=0
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT succeeded, error_text FROM turn_traces",
            )
            row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 0  # succeeded = False
        assert "LLM timeout" in row[1]

    async def test_extracts_user_input(self, tmp_path) -> None:
        container = _make_container(tmp_path)
        db_path = await _init_db(tmp_path)
        runner = GraphTurnRunner(container)

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "response_content": "reply",
            "tool_call_records": [],
        }

        await runner.run_turn(
            mock_graph,
            _make_graph_input("Plan my day"),
            {"configurable": {"thread_id": "t1"}},
        )

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute("SELECT user_input FROM turn_traces")
            row = await cursor.fetchone()
        assert row[0] == "Plan my day"

    async def test_handles_object_messages(self, tmp_path) -> None:
        """Messages can be LangChain message objects, not just dicts."""
        container = _make_container(tmp_path)
        db_path = await _init_db(tmp_path)
        runner = GraphTurnRunner(container)

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "response_content": "ok",
            "tool_call_records": [],
        }

        msg_obj = SimpleNamespace(content="Object message", type="human")
        graph_input = {
            "messages": [msg_obj],
            "session_id": "sess-obj",
            "turn_count": 0,
        }

        await runner.run_turn(
            mock_graph, graph_input, {"configurable": {"thread_id": "t1"}},
        )

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute("SELECT user_input FROM turn_traces")
            row = await cursor.fetchone()
        assert row[0] == "Object message"

    async def test_latency_and_tool_count(self, tmp_path) -> None:
        container = _make_container(tmp_path)
        db_path = await _init_db(tmp_path)
        runner = GraphTurnRunner(container)

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "response_content": "done",
            "tool_call_records": [
                {"name": "a"},
                {"name": "b"},
                {"name": "c"},
            ],
        }

        await runner.run_turn(
            mock_graph,
            _make_graph_input(),
            {"configurable": {"thread_id": "t1"}},
        )

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT latency_ms, tool_call_count, tools_invoked FROM turn_traces",
            )
            row = await cursor.fetchone()
        assert row[0] >= 0  # latency_ms
        assert row[1] == 3  # tool_call_count
        assert '"a"' in row[2]  # tools_invoked JSON


class TestGraphTurnRunnerStreamTurn:
    """Test stream_turn yields events and writes trace."""

    async def test_yields_all_events(self, tmp_path) -> None:
        container = _make_container(tmp_path)
        await _init_db(tmp_path)
        runner = GraphTurnRunner(container)

        events = [
            {"node_a": {"response_content": "Hello"}},
            {"node_b": {"tool_call_records": [{"name": "recall"}]}},
            {"node_c": {"response_content": " world"}},
        ]

        mock_graph = AsyncMock()

        async def _fake_stream(*args, **kwargs):
            for e in events:
                yield e

        mock_graph.astream = _fake_stream

        collected = []
        async for event in runner.stream_turn(
            mock_graph,
            _make_graph_input(),
            {"configurable": {"thread_id": "t1"}},
        ):
            collected.append(event)

        assert len(collected) == 3
        assert collected[0] == events[0]

    async def test_writes_trace_on_completion(self, tmp_path) -> None:
        container = _make_container(tmp_path)
        db_path = await _init_db(tmp_path)
        runner = GraphTurnRunner(container)

        mock_graph = AsyncMock()

        async def _fake_stream(*args, **kwargs):
            yield {"node": {"response_content": "streamed reply"}}

        mock_graph.astream = _fake_stream

        async for _ in runner.stream_turn(
            mock_graph,
            _make_graph_input(),
            {"configurable": {"thread_id": "t1"}},
        ):
            pass

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT succeeded, response_length, final_output FROM turn_traces",
            )
            row = await cursor.fetchone()
        assert row[0] == 1  # succeeded
        assert row[1] == len("streamed reply")
        assert row[2] == "streamed reply"

    async def test_stream_error_writes_trace(self, tmp_path) -> None:
        container = _make_container(tmp_path)
        db_path = await _init_db(tmp_path)
        runner = GraphTurnRunner(container)

        mock_graph = AsyncMock()

        async def _failing_stream(*args, **kwargs):
            yield {"node": {"response_content": "partial"}}
            raise RuntimeError("stream broke")

        mock_graph.astream = _failing_stream

        with pytest.raises(RuntimeError, match="stream broke"):
            async for _ in runner.stream_turn(
                mock_graph,
                _make_graph_input(),
                {"configurable": {"thread_id": "t1"}},
            ):
                pass

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT succeeded, error_text FROM turn_traces",
            )
            row = await cursor.fetchone()
        assert row[0] == 0  # failed
        assert "stream broke" in row[1]


class TestRecordTraceEvent:
    """Test writing to turn_trace_events."""

    async def test_writes_event(self, tmp_path) -> None:
        container = _make_container(tmp_path)
        db_path = await _init_db(tmp_path)
        runner = GraphTurnRunner(container)

        # First create a parent trace (foreign key reference)
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO turn_traces (id, session_id, turn_number, started_at) "
                "VALUES (?, ?, ?, ?)",
                ("trace-abc", "sess-1", 0, "2026-04-06T00:00:00"),
            )
            await db.commit()

        await runner.record_trace_event("trace-abc", "tool_start", '{"tool": "recall"}')

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT trace_id, event_type, payload FROM turn_trace_events",
            )
            row = await cursor.fetchone()

        assert row[0] == "trace-abc"
        assert row[1] == "tool_start"
        assert "recall" in row[2]


class TestDBWriteFailureResilience:
    """DB write failures must not crash the turn."""

    async def test_run_turn_succeeds_when_db_missing(self, tmp_path) -> None:
        """If DB schema is missing, run_turn still returns the graph result."""
        container = _make_container(tmp_path)
        # Deliberately NOT initializing the DB schema
        runner = GraphTurnRunner(container)

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "response_content": "works",
            "tool_call_records": [],
        }

        result = await runner.run_turn(
            mock_graph,
            _make_graph_input(),
            {"configurable": {"thread_id": "t1"}},
        )
        assert result["response_content"] == "works"

    async def test_stream_turn_succeeds_when_db_missing(self, tmp_path) -> None:
        """Streaming also works when DB writes fail."""
        container = _make_container(tmp_path)
        runner = GraphTurnRunner(container)

        mock_graph = AsyncMock()

        async def _fake_stream(*args, **kwargs):
            yield {"node": {"response_content": "ok"}}

        mock_graph.astream = _fake_stream

        collected = []
        async for event in runner.stream_turn(
            mock_graph,
            _make_graph_input(),
            {"configurable": {"thread_id": "t1"}},
        ):
            collected.append(event)

        assert len(collected) == 1

    async def test_record_trace_event_swallows_error(self, tmp_path) -> None:
        """record_trace_event should not raise on DB failure."""
        container = _make_container(tmp_path)
        # No DB init — table doesn't exist
        runner = GraphTurnRunner(container)

        # Should not raise
        await runner.record_trace_event("nonexistent", "test", "payload")


class TestCircuitBreakerIntegration:
    """Verify the runner exposes the circuit breaker properly."""

    def test_runner_has_circuit_breaker(self, tmp_path) -> None:
        container = _make_container(tmp_path)
        runner = GraphTurnRunner(container)
        assert runner.circuit_breaker is not None
        assert isinstance(runner.circuit_breaker, CompactionCircuitBreaker)
        assert runner.circuit_breaker.check() is True
