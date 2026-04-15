"""Tests for kora_v2.graph.dispatch -- tool definitions and execution.

Phase 3: dispatch_worker delegates to real worker harnesses.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from kora_v2.graph.dispatch import SUPERVISOR_TOOLS, execute_tool


class TestSupervisorToolDefinitions:
    """Verify SUPERVISOR_TOOLS schema correctness."""

    def test_tools_is_nonempty_list(self) -> None:
        assert isinstance(SUPERVISOR_TOOLS, list)
        # dispatch_worker, recall, start_autonomous (Phase 6),
        # search_web + fetch_url (WS2), plus 7 new orchestration tools
        # added in Slice 7.5b (spec §17.9): decompose_and_dispatch,
        # get_task_progress, cancel_task, list_tasks, pose_decision,
        # resolve_decision, create_routine.
        assert len(SUPERVISOR_TOOLS) == 12

    def test_all_tools_have_required_keys(self) -> None:
        for tool in SUPERVISOR_TOOLS:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool missing 'description': {tool}"
            assert "input_schema" in tool, f"Tool missing 'input_schema': {tool}"

    def test_input_schema_has_anthropic_format(self) -> None:
        for tool in SUPERVISOR_TOOLS:
            schema = tool["input_schema"]
            assert schema["type"] == "object", f"Schema type not 'object' for {tool['name']}"
            assert "properties" in schema, f"No properties for {tool['name']}"
            assert "required" in schema, f"No required for {tool['name']}"

    def test_dispatch_worker_tool_shape(self) -> None:
        tool = next(t for t in SUPERVISOR_TOOLS if t["name"] == "dispatch_worker")
        props = tool["input_schema"]["properties"]
        assert "worker_name" in props
        assert "input_json" in props
        assert "enum" in props["worker_name"]
        assert "planner" in props["worker_name"]["enum"]
        assert "executor" in props["worker_name"]["enum"]
        assert "reviewer" in props["worker_name"]["enum"]
        assert "memory" not in props["worker_name"]["enum"]
        assert tool["input_schema"]["required"] == ["worker_name", "input_json"]

    def test_recall_tool_shape(self) -> None:
        tool = next(t for t in SUPERVISOR_TOOLS if t["name"] == "recall")
        props = tool["input_schema"]["properties"]
        assert "query" in props
        assert "layer" in props
        assert "max_results" in props
        assert tool["input_schema"]["required"] == ["query"]

    def test_start_autonomous_in_tools(self) -> None:
        """start_autonomous must appear in SUPERVISOR_TOOLS (added back in Phase 6)."""
        names = {t["name"] for t in SUPERVISOR_TOOLS}
        assert "start_autonomous" in names

    def test_start_autonomous_tool_shape(self) -> None:
        """start_autonomous tool has correct schema."""
        tool = next(t for t in SUPERVISOR_TOOLS if t["name"] == "start_autonomous")
        props = tool["input_schema"]["properties"]
        assert "goal" in props
        assert tool["input_schema"]["required"] == ["goal"]


class TestExecuteTool:
    """Verify tool execution routing."""

    @pytest.mark.asyncio
    async def test_dispatch_worker_calls_real_worker(self) -> None:
        """dispatch_worker routes to a real worker harness via container."""
        from kora_v2.core.models import PlanOutput

        # Create mock output
        mock_output = MagicMock(spec=PlanOutput)
        mock_output.model_dump_json.return_value = '{"plan": "test"}'

        # Create mock worker
        mock_worker = AsyncMock()
        mock_worker.execute = AsyncMock(return_value=mock_output)

        # Create mock container
        mock_container = MagicMock()
        mock_container.resolve_worker = MagicMock(return_value=mock_worker)

        plan_input_json = json.dumps({"goal": "Plan a birthday party"})
        result = await execute_tool(
            "dispatch_worker",
            {"worker_name": "planner", "input_json": plan_input_json},
            container=mock_container,
        )

        mock_container.resolve_worker.assert_called_once_with("planner")
        mock_worker.execute.assert_awaited_once()
        assert '{"plan": "test"}' == result

    @pytest.mark.asyncio
    async def test_dispatch_worker_preserves_executor_params(self) -> None:
        """Executor dispatch should preserve side-effecting params under ExecutionInput.params."""
        mock_output = MagicMock()
        mock_output.model_dump_json.return_value = '{"ok": true}'

        captured_inputs = []

        async def capture_execute(input_data):
            captured_inputs.append(input_data)
            return mock_output

        mock_worker = MagicMock()
        mock_worker.execute = AsyncMock(side_effect=capture_execute)

        mock_container = MagicMock()
        mock_container.resolve_worker = MagicMock(return_value=mock_worker)
        mock_container.settings.security.auth_mode = "trust_all"
        mock_container.session_manager = None

        result = await execute_tool(
            "dispatch_worker",
            {
                "worker_name": "executor",
                "input_json": json.dumps({
                    "task": "write_file",
                    "path": "/tmp/demo.txt",
                    "content": "hello",
                }),
            },
            container=mock_container,
        )

        assert json.loads(result) == {"ok": True}
        assert captured_inputs[0].task == "write_file"
        assert captured_inputs[0].params["path"] == "/tmp/demo.txt"
        assert captured_inputs[0].params["content"] == "hello"

    @pytest.mark.asyncio
    async def test_dispatch_worker_returns_error_on_failure(self) -> None:
        """dispatch_worker returns JSON error when worker raises."""
        mock_container = MagicMock()
        mock_container.resolve_worker = MagicMock(
            side_effect=ValueError("Unknown worker: bogus")
        )

        result = await execute_tool(
            "dispatch_worker",
            {"worker_name": "bogus", "input_json": "{}"},
            container=mock_container,
        )
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "bogus" in parsed["error"]

    @pytest.mark.asyncio
    async def test_dispatch_worker_no_container(self) -> None:
        """dispatch_worker without container returns error."""
        result = await execute_tool(
            "dispatch_worker",
            {"worker_name": "planner", "input_json": "{}"},
            container=None,
        )
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_recall_no_container(self) -> None:
        """recall() without a container returns empty results gracefully."""
        result = await execute_tool(
            "recall",
            {"query": "test query", "layer": "all", "max_results": 5},
        )
        parsed = json.loads(result)
        assert parsed["results"] == []
        assert "container" in parsed["message"].lower() or "not" in parsed["message"].lower()

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        result = await execute_tool("nonexistent_tool", {})
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "Unknown tool" in parsed["message"]


class TestContainerWorkerResolution:
    """Verify Container.resolve_worker and initialize_workers."""

    def test_resolve_worker_before_init_raises(self) -> None:
        """resolve_worker for core workers before init raises RuntimeError."""
        from kora_v2.core.di import Container
        from kora_v2.core.settings import get_settings

        container = Container(get_settings())
        with pytest.raises(RuntimeError, match="not initialized"):
            container.resolve_worker("planner")

    def test_resolve_worker_unknown_raises(self) -> None:
        """resolve_worker raises ValueError for unknown worker."""
        from kora_v2.core.di import Container
        from kora_v2.core.settings import get_settings

        container = Container(get_settings())
        with pytest.raises(ValueError, match="Unknown worker"):
            container.resolve_worker("nonexistent")

    def test_resolve_worker_on_demand_raises_value_error(self) -> None:
        """resolve_worker raises ValueError for unknown/on-demand agent names."""
        from kora_v2.core.di import Container
        from kora_v2.core.settings import get_settings

        container = Container(get_settings())
        with pytest.raises(ValueError, match="Unknown worker"):
            container.resolve_worker("research")

    def test_initialize_workers_creates_all(self) -> None:
        """initialize_workers creates core workers and skill loader."""
        from kora_v2.core.di import Container
        from kora_v2.core.settings import get_settings

        container = Container(get_settings())
        container.initialize_workers()

        assert container._planner is not None
        assert container._executor is not None
        assert container._reviewer is not None
        assert container._skill_loader is not None
        # MCP manager + verb resolver are both Phase 3 infrastructure and
        # get created eagerly inside initialize_workers() (see di.py:247).
        assert container._mcp_manager is not None
        assert container._verb_resolver is not None

    def test_resolve_worker_after_init(self) -> None:
        """resolve_worker returns workers after initialization."""
        from kora_v2.agents.workers.planner import PlannerWorkerHarness
        from kora_v2.core.di import Container
        from kora_v2.core.settings import get_settings

        container = Container(get_settings())
        container.initialize_workers()

        planner = container.resolve_worker("planner")
        assert isinstance(planner, PlannerWorkerHarness)
