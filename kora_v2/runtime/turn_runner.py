"""Kora V2 — Turn runner: wraps supervisor graph invocation with tracing.

Provides :class:`GraphTurnRunner` which writes to ``turn_traces`` and
``turn_trace_events`` tables in ``operational.db``, and
:class:`CompactionCircuitBreaker` to prevent runaway compaction retries.

Usage::

    runner = GraphTurnRunner(container)
    result = await runner.run_turn(graph, graph_input, config)

    # Or streaming:
    async for event in runner.stream_turn(graph, graph_input, config):
        handle(event)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

log = structlog.get_logger(__name__)


class CompactionCircuitBreaker:
    """Prevents runaway compaction -- trips after *max_compactions* per session.

    Once tripped, ``check()`` returns ``False`` until ``reset()`` is called.
    """

    def __init__(self, max_compactions: int = 3) -> None:
        self._count = 0
        self._max = max_compactions
        self._tripped = False

    def check(self) -> bool:
        """Return ``True`` if compaction is still allowed."""
        return not self._tripped

    def record_compaction(self) -> None:
        """Record one compaction event; trip the breaker if at limit."""
        self._count += 1
        if self._count >= self._max:
            self._tripped = True

    def reset(self) -> None:
        """Reset the breaker for a new session."""
        self._count = 0
        self._tripped = False

    @property
    def count(self) -> int:
        return self._count

    @property
    def tripped(self) -> bool:
        return self._tripped


class GraphTurnRunner:
    """Wraps graph invocation with structured turn tracing.

    Writes to ``turn_traces`` and ``turn_trace_events`` tables in
    ``operational.db``.  Records tool-call metadata alongside each turn.
    Applies :class:`CompactionCircuitBreaker` to bound retries.

    All DB writes are best-effort: failures are logged as warnings and
    never bubble up to the caller.
    """

    def __init__(self, container: Any) -> None:
        self._container = container
        self._circuit_breaker = CompactionCircuitBreaker()
        self._db_path: Path = container.settings.data_dir / "operational.db"

    @property
    def circuit_breaker(self) -> CompactionCircuitBreaker:
        return self._circuit_breaker

    # ── Public entry points ─────────────────────────────────────────────

    async def run_turn(self, graph: Any, graph_input: dict, config: dict) -> dict:
        """Execute one conversation turn via ``graph.ainvoke`` with full tracing.

        Args:
            graph: The LangGraph supervisor graph.
            graph_input: Input state dict (must include ``messages``).
            config: LangGraph config (must include ``configurable.thread_id``).

        Returns:
            The graph result dict.

        Raises:
            Any exception raised by ``graph.ainvoke`` is re-raised after the
            trace is written.
        """
        trace_id = uuid.uuid4().hex[:16]
        session_id = graph_input.get("session_id", "unknown")
        turn_number = graph_input.get("turn_count", 0)
        started_at = datetime.now(UTC)
        user_input = self._extract_user_input(graph_input)

        await self._write_trace_start(
            trace_id, session_id, turn_number, started_at, user_input,
        )

        # Phase 7.5b: turn-start task query. Stash any live tasks the
        # four-case OR surfaces into graph_input so the supervisor's
        # dynamic suffix can mention them. Best-effort: if the engine
        # is missing or the query fails, the turn runs without the
        # hint and we log a debug line.
        await self._prefetch_relevant_tasks(
            graph_input, session_id=session_id, user_message=user_input,
        )

        try:
            result = await graph.ainvoke(graph_input, config=config)
            latency_ms = int(
                (datetime.now(UTC) - started_at).total_seconds() * 1000,
            )
            tool_calls = result.get("tool_call_records", [])
            response = result.get("response_content", "")

            await self._write_trace_complete(
                trace_id,
                latency_ms=latency_ms,
                succeeded=True,
                tool_call_count=len(tool_calls),
                response_length=len(response),
                final_output=response[:2000],
                tools_invoked=json.dumps(
                    [self._tool_name(tc) for tc in tool_calls],
                ),
            )

            # Phase 7.5b: turn-end acknowledgement. Any tasks we
            # surfaced at turn start are now "seen" and should not
            # re-surface next turn.
            await self._acknowledge_tasks(graph_input)

            return result

        except Exception as exc:
            latency_ms = int(
                (datetime.now(UTC) - started_at).total_seconds() * 1000,
            )
            await self._write_trace_complete(
                trace_id,
                latency_ms=latency_ms,
                succeeded=False,
                error_text=str(exc)[:1000],
            )
            raise

    async def stream_turn(
        self,
        graph: Any,
        graph_input: dict,
        config: dict,
    ):
        """Streaming variant -- yields events, writes trace on completion.

        Yields each event from ``graph.astream()``.  On completion (or
        error) writes the aggregated trace to ``turn_traces``.
        """
        trace_id = uuid.uuid4().hex[:16]
        session_id = graph_input.get("session_id", "unknown")
        turn_number = graph_input.get("turn_count", 0)
        started_at = datetime.now(UTC)
        user_input = self._extract_user_input(graph_input)

        await self._write_trace_start(
            trace_id, session_id, turn_number, started_at, user_input,
        )

        collected_response: list[str] = []
        tool_calls: list[Any] = []
        error_text: str | None = None

        try:
            async for event in graph.astream(graph_input, config=config):
                # Collect response content and tool calls from node outputs.
                if isinstance(event, dict):
                    for _node_name, node_output in event.items():
                        if isinstance(node_output, dict):
                            if "response_content" in node_output:
                                collected_response.append(
                                    node_output["response_content"],
                                )
                            if "tool_call_records" in node_output:
                                tool_calls.extend(
                                    node_output["tool_call_records"],
                                )
                yield event

        except Exception as exc:
            error_text = str(exc)[:1000]
            raise

        finally:
            latency_ms = int(
                (datetime.now(UTC) - started_at).total_seconds() * 1000,
            )
            response = "".join(collected_response)
            await self._write_trace_complete(
                trace_id,
                latency_ms=latency_ms,
                succeeded=error_text is None,
                tool_call_count=len(tool_calls),
                response_length=len(response),
                final_output=response[:2000],
                tools_invoked=json.dumps(
                    [self._tool_name(tc) for tc in tool_calls],
                ),
                error_text=error_text,
            )

    async def record_trace_event(
        self,
        trace_id: str,
        event_type: str,
        payload: str | None = None,
    ) -> None:
        """Record a within-turn event to ``turn_trace_events``."""
        try:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    "INSERT INTO turn_trace_events "
                    "(trace_id, event_type, payload, recorded_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        trace_id,
                        event_type,
                        payload,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                await db.commit()
        except Exception:
            log.warning(
                "trace_event_write_failed",
                trace_id=trace_id,
                event_type=event_type,
            )

    # ── Orchestration turn-start / turn-end hooks ──────────────────────

    async def _prefetch_relevant_tasks(
        self,
        graph_input: dict,
        *,
        session_id: str,
        user_message: str,
    ) -> None:
        """Pre-populate ``_orchestration_tasks`` on *graph_input*.

        Implements the spec §13.3 four-case OR by delegating to
        ``engine.list_tasks(relevant_to_session=..., user_message=...)``.
        We shape the response into a small list of dicts so the
        supervisor's dynamic suffix doesn't need to import engine
        types.
        """
        engine = getattr(self._container, "orchestration_engine", None)
        if engine is None:
            return
        try:
            tasks = await engine.list_tasks(
                relevant_to_session=session_id if session_id != "unknown" else None,
                user_message=user_message or None,
            )
        except Exception:  # noqa: BLE001
            log.debug("orchestration_prefetch_failed", exc_info=True)
            return

        shaped: list[dict[str, Any]] = []
        seen_ids: list[str] = []
        for task in tasks[:5]:
            state_obj = getattr(task, "state", None)
            state_val = getattr(state_obj, "value", None) or str(state_obj or "")
            shaped.append(
                {
                    "task_id": getattr(task, "id", None),
                    "stage": getattr(task, "stage_name", None),
                    "state": state_val,
                    "goal": getattr(task, "goal", None),
                    "pipeline_instance_id": getattr(task, "pipeline_instance_id", None),
                }
            )
            tid = getattr(task, "id", None)
            if tid:
                seen_ids.append(tid)
        if shaped:
            graph_input["_orchestration_tasks"] = shaped
            graph_input["_orchestration_seen_task_ids"] = seen_ids

    async def _acknowledge_tasks(self, graph_input: dict) -> None:
        """Mark any tasks we surfaced this turn as acknowledged."""
        seen_ids = graph_input.get("_orchestration_seen_task_ids") or []
        if not seen_ids:
            return
        engine = getattr(self._container, "orchestration_engine", None)
        if engine is None:
            return
        for task_id in seen_ids:
            try:
                await engine.acknowledge_task(task_id)
            except Exception:  # noqa: BLE001
                log.debug(
                    "orchestration_ack_failed",
                    task_id=task_id,
                    exc_info=True,
                )

    # ── Private helpers ─────────────────────────────────────────────────

    @staticmethod
    def _extract_user_input(graph_input: dict) -> str:
        """Pull the last message content from graph input."""
        messages = graph_input.get("messages", [])
        if not messages:
            return ""
        last_msg = messages[-1]
        if isinstance(last_msg, dict):
            return last_msg.get("content", "")
        return getattr(last_msg, "content", "")

    @staticmethod
    def _tool_name(tc: Any) -> str:
        """Extract tool name from a tool call record (dict or object)."""
        if isinstance(tc, dict):
            return tc.get("name", tc.get("tool_name", ""))
        return getattr(tc, "tool_name", getattr(tc, "name", ""))

    async def _write_trace_start(
        self,
        trace_id: str,
        session_id: str,
        turn_number: int,
        started_at: datetime,
        user_input: str,
    ) -> None:
        try:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    """INSERT INTO turn_traces
                    (id, session_id, turn_number, started_at, user_input)
                    VALUES (?, ?, ?, ?, ?)""",
                    (
                        trace_id,
                        session_id,
                        turn_number,
                        started_at.isoformat(),
                        user_input[:2000],
                    ),
                )
                await db.commit()
        except Exception:
            log.warning("trace_start_write_failed", trace_id=trace_id)

    async def _write_trace_complete(
        self,
        trace_id: str,
        *,
        latency_ms: int,
        succeeded: bool,
        tool_call_count: int = 0,
        response_length: int = 0,
        final_output: str = "",
        tools_invoked: str = "[]",
        error_text: str | None = None,
    ) -> None:
        try:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    """UPDATE turn_traces SET
                    completed_at = ?, latency_ms = ?, succeeded = ?,
                    tool_call_count = ?, response_length = ?,
                    final_output = ?, tools_invoked = ?, error_text = ?
                    WHERE id = ?""",
                    (
                        datetime.now(UTC).isoformat(),
                        latency_ms,
                        int(succeeded),
                        tool_call_count,
                        response_length,
                        final_output,
                        tools_invoked,
                        error_text,
                        trace_id,
                    ),
                )
                await db.commit()
        except Exception:
            log.warning("trace_complete_write_failed", trace_id=trace_id)
