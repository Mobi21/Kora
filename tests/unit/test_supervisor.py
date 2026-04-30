"""Tests for kora_v2.graph.supervisor -- 5-node supervisor graph."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from kora_v2.core.models import EmotionalState, EnergyEstimate, SessionState
from kora_v2.emotion.fast_assessor import FastEmotionAssessor
from kora_v2.graph.supervisor import (
    build_suffix,
    build_supervisor_graph,
    receive,
    should_continue,
    synthesize,
)
from kora_v2.llm.types import GenerationResult

# =====================================================================
# Helpers
# =====================================================================


def _make_container(
    llm_mock: AsyncMock | None = None,
    *,
    with_phase4: bool = False,
) -> SimpleNamespace:
    """Build a minimal container with a mock LLM provider.

    If *with_phase4* is True, adds fast_emotion, llm_emotion, and
    session_manager stubs so the receive-node wiring exercises
    emotion / energy population.
    """
    if llm_mock is None:
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools = AsyncMock(
            return_value=GenerationResult(content="Hello!", tool_calls=[])
        )

    container = SimpleNamespace(
        llm=llm_mock,
        settings=SimpleNamespace(),
        event_emitter=SimpleNamespace(),
        fast_emotion=None,
        llm_emotion=None,
        session_manager=None,
    )

    if with_phase4:
        container.fast_emotion = FastEmotionAssessor()
        container.llm_emotion = None  # no LLM tier in unit tests

        from datetime import UTC, datetime
        session = SessionState(
            session_id="s-test",
            turn_count=0,
            started_at=datetime.now(UTC),
            emotional_state=EmotionalState(
                valence=0.1, arousal=0.4, dominance=0.5, confidence=0.6,
            ),
            energy_estimate=EnergyEstimate(
                level="medium", focus="moderate", confidence=0.4, source="time_of_day",
            ),
            pending_items=[{"source": "bridge", "content": "test item", "priority": 1}],
        )
        mgr = SimpleNamespace(
            active_session=session,
            load_last_bridge=AsyncMock(return_value=None),
        )
        container.session_manager = mgr

    return container


# =====================================================================
# Node Unit Tests
# =====================================================================


class TestReceiveNode:
    """Tests for the receive node."""

    @pytest.mark.asyncio
    async def test_increments_turn_count(self) -> None:
        state: dict[str, Any] = {"turn_count": 2, "session_id": "s1"}
        result = await receive(state)
        assert result["turn_count"] == 3

    @pytest.mark.asyncio
    async def test_first_turn_starts_at_one(self) -> None:
        state: dict[str, Any] = {}
        result = await receive(state)
        assert result["turn_count"] == 1

    @pytest.mark.asyncio
    async def test_resets_per_turn_state(self) -> None:
        state: dict[str, Any] = {
            "turn_count": 1,
            "session_id": "s1",
            "active_workers": [{"worker": "memory"}],
            "tool_call_records": [{"tool": "recall"}],
        }
        result = await receive(state)
        assert result["active_workers"] == []
        assert result["tool_call_records"] == []
        assert result["response_content"] == ""

    @pytest.mark.asyncio
    async def test_generates_session_id_if_missing(self) -> None:
        state: dict[str, Any] = {}
        result = await receive(state)
        assert "session_id" in result
        assert len(result["session_id"]) > 0


class TestBuildSuffixNode:
    """Tests for the build_suffix node."""

    @pytest.mark.asyncio
    async def test_builds_frozen_prefix_on_first_call(self) -> None:
        state: dict[str, Any] = {"turn_count": 1, "session_id": "s1"}
        result = await build_suffix(state)
        assert "frozen_prefix" in result
        assert len(result["frozen_prefix"]) > 100
        assert "Kora" in result["frozen_prefix"]

    @pytest.mark.asyncio
    async def test_preserves_existing_frozen_prefix(self) -> None:
        state: dict[str, Any] = {
            "turn_count": 2,
            "session_id": "s2",
            "frozen_prefix": "EXISTING PREFIX",
        }
        result = await build_suffix(state)
        assert result["frozen_prefix"] == "EXISTING PREFIX"

    @pytest.mark.asyncio
    async def test_produces_dynamic_suffix(self) -> None:
        state: dict[str, Any] = {"turn_count": 3, "session_id": "s3"}
        result = await build_suffix(state)
        assert "_dynamic_suffix" in result
        assert "Turn: 3" in result["_dynamic_suffix"]

    @pytest.mark.asyncio
    async def test_accepts_container_param(self) -> None:
        """build_suffix works with container=None (backward compat)."""
        state: dict[str, Any] = {"turn_count": 1, "session_id": "s1"}
        result = await build_suffix(state, container=None)
        assert "frozen_prefix" in result

    @pytest.mark.asyncio
    async def test_no_compaction_with_few_messages(self) -> None:
        """No compaction runs when messages are within budget (NORMAL tier)."""
        state: dict[str, Any] = {
            "turn_count": 1,
            "session_id": "s1",
            "messages": [{"role": "user", "content": "hello"}],
        }
        result = await build_suffix(state)
        # Should not have compaction_summary for tiny conversations
        assert result.get("compaction_summary") is None or "compaction_summary" not in result

    @pytest.mark.asyncio
    async def test_hard_stop_uses_replacement_compaction_before_llm(self) -> None:
        state: dict[str, Any] = {
            "turn_count": 40,
            "session_id": "s-hard",
            "frozen_prefix": "prefix",
            "messages": [
                {"role": "user", "content": "Maya schedule context " + ("token " * 1200)}
                for _ in range(180)
            ],
        }

        result = await build_suffix(state, container=_make_container())

        assert result["compaction_tier"] == "HARD_STOP"
        assert result["messages"][0]["role"] == "__replace_messages__"
        assert "Emergency Context Bridge" in result["compaction_summary"]


class TestShouldContinue:
    """Tests for the routing function."""

    def test_routes_to_tool_loop_when_tools_pending(self) -> None:
        state: dict[str, Any] = {
            "_pending_tool_calls": [
                {"id": "t1", "name": "recall", "arguments": {"query": "test"}}
            ]
        }
        assert should_continue(state) == "tool_loop"

    def test_routes_to_synthesize_when_no_tools(self) -> None:
        state: dict[str, Any] = {"_pending_tool_calls": []}
        assert should_continue(state) == "synthesize"

    def test_routes_to_synthesize_when_key_missing(self) -> None:
        state: dict[str, Any] = {}
        assert should_continue(state) == "synthesize"


class TestSynthesizeNode:
    """Tests for the synthesize node."""

    @pytest.mark.asyncio
    async def test_passthrough_when_content_exists(self) -> None:
        state: dict[str, Any] = {"response_content": "Already here"}
        result = await synthesize(state)
        assert result["response_content"] == "Already here"

    @pytest.mark.asyncio
    async def test_extracts_from_last_assistant_message(self) -> None:
        state: dict[str, Any] = {
            "response_content": "",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "Hello there!"},
            ],
        }
        result = await synthesize(state)
        assert result["response_content"] == "Hello there!"

    @pytest.mark.asyncio
    async def test_strips_raw_minimax_tool_markup(self) -> None:
        state: dict[str, Any] = {
            "response_content": (
                "<minimax:tool_call><invoke name=\"write_file\"></invoke>"
                "</minimax:tool_call>"
            ),
            "messages": [{"role": "user", "content": "save the file"}],
        }

        result = await synthesize(state)

        assert "<minimax:tool_call>" not in result["response_content"]
        assert "confirm the next batch" in result["response_content"]

    @pytest.mark.asyncio
    async def test_sanitizes_trusted_support_phone_wording(self) -> None:
        state: dict[str, Any] = {
            "response_content": "Hey Talia. Can we talk this weekend? No automatic contact.",
            "messages": [
                {
                    "role": "user",
                    "content": "Draft a trusted support ask for Talia, permission first.",
                },
            ],
        }

        result = await synthesize(state)

        assert "Can we talk this weekend" not in result["response_content"]
        assert "Could I text you this weekend?" in result["response_content"]


# =====================================================================
# Graph Build Test
# =====================================================================


class TestBuildSupervisorGraph:
    """Tests for graph construction."""

    def test_graph_builds_without_error(self) -> None:
        container = _make_container()
        graph = build_supervisor_graph(container)
        assert graph is not None

    def test_graph_has_expected_nodes(self) -> None:
        container = _make_container()
        graph = build_supervisor_graph(container)
        # CompiledGraph nodes are accessible; verify key ones exist
        node_names = set(graph.nodes.keys())
        # LangGraph adds __start__ and __end__ as special nodes
        assert "receive" in node_names
        assert "build_suffix" in node_names
        assert "think" in node_names
        assert "tool_loop" in node_names
        assert "synthesize" in node_names


# =====================================================================
# Full Graph Integration (mock LLM)
# =====================================================================


class TestSupervisorGraphIntegration:
    """End-to-end graph invocation with mocked LLM."""

    @pytest.mark.asyncio
    async def test_simple_direct_response(self) -> None:
        """Graph processes a user message and produces a response (no tools)."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools = AsyncMock(
            return_value=GenerationResult(
                content="Hey! How can I help you today?",
                tool_calls=[],
                finish_reason="stop",
            )
        )

        container = _make_container(llm_mock)
        graph = build_supervisor_graph(container)

        # Invoke the graph
        config = {"configurable": {"thread_id": "test-session-1"}}
        input_state = {
            "messages": [{"role": "user", "content": "Hello Kora!"}],
        }

        result = await graph.ainvoke(input_state, config=config)

        # Verify response was produced
        assert result["response_content"] == "Hey! How can I help you today?"
        assert result["turn_count"] == 1
        assert len(result["session_id"]) > 0

        # Verify LLM was called
        llm_mock.generate_with_tools.assert_called_once()

    @pytest.mark.asyncio
    async def test_emotion_wired_on_first_turn(self) -> None:
        """Gap 1+2: First turn populates emotional_state from session manager
        and runs the fast emotion assessor on the user message."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools = AsyncMock(
            return_value=GenerationResult(
                content="Glad to hear that!",
                tool_calls=[],
                finish_reason="stop",
            )
        )

        container = _make_container(llm_mock, with_phase4=True)
        graph = build_supervisor_graph(container)

        config = {"configurable": {"thread_id": "test-emo-1"}}
        input_state = {
            "messages": [{"role": "user", "content": "I'm so happy today!"}],
        }

        result = await graph.ainvoke(input_state, config=config)

        # emotional_state must be populated (not None)
        emo = result.get("emotional_state")
        assert emo is not None, "emotional_state should be populated after first turn"
        assert isinstance(emo, dict)
        # The user said 'happy' — valence should be positive
        assert emo["valence"] > 0

    @pytest.mark.asyncio
    async def test_energy_wired_on_first_turn(self) -> None:
        """Gap 1+6: Energy estimate is populated on the first turn."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools = AsyncMock(
            return_value=GenerationResult(
                content="OK!",
                tool_calls=[],
                finish_reason="stop",
            )
        )

        container = _make_container(llm_mock, with_phase4=True)
        graph = build_supervisor_graph(container)

        config = {"configurable": {"thread_id": "test-energy-1"}}
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "Hello"}]},
            config=config,
        )

        energy = result.get("energy_estimate")
        assert energy is not None, "energy_estimate should be populated"
        assert isinstance(energy, dict)
        assert energy["level"] in ("low", "medium", "high")
        assert energy["focus"] in ("scattered", "moderate", "locked_in")

    @pytest.mark.asyncio
    async def test_pending_items_wired_on_first_turn(self) -> None:
        """Gap 1: Pending items from session manager appear in state."""
        llm_mock = AsyncMock()
        llm_mock.generate_with_tools = AsyncMock(
            return_value=GenerationResult(content="Hi!", tool_calls=[], finish_reason="stop")
        )

        container = _make_container(llm_mock, with_phase4=True)
        graph = build_supervisor_graph(container)

        config = {"configurable": {"thread_id": "test-pending-1"}}
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "hi"}]},
            config=config,
        )

        pending = result.get("pending_items")
        assert pending is not None, "pending_items should be populated from session"
        assert len(pending) >= 1

    @pytest.mark.asyncio
    async def test_tool_call_then_response(self) -> None:
        """Graph handles one tool call then a final response."""
        call_count = {"n": 0}

        async def mock_generate_with_tools(**kwargs: Any) -> GenerationResult:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call: LLM wants to recall
                from kora_v2.llm.types import ToolCall

                return GenerationResult(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="tc_001",
                            name="recall",
                            arguments={"query": "user's name"},
                        )
                    ],
                    finish_reason="tool_use",
                )
            # Second call: LLM responds with final answer
            return GenerationResult(
                content="Based on my search, I found your info!",
                tool_calls=[],
                finish_reason="stop",
            )

        llm_mock = AsyncMock()
        llm_mock.generate_with_tools = AsyncMock(side_effect=mock_generate_with_tools)

        container = _make_container(llm_mock)
        graph = build_supervisor_graph(container)

        config = {"configurable": {"thread_id": "test-session-2"}}
        input_state = {
            "messages": [{"role": "user", "content": "What's my name?"}],
        }

        result = await graph.ainvoke(input_state, config=config)

        assert result["response_content"] == "Based on my search, I found your info!"
        assert llm_mock.generate_with_tools.call_count == 2
        # Should have tool call records from the recall stub
        assert len(result.get("tool_call_records", [])) >= 1
