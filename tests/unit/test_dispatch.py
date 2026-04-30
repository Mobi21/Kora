"""Tests for kora_v2.graph.dispatch -- tool definitions and execution.

Phase 3: dispatch_worker delegates to real worker harnesses.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kora_v2.graph.dispatch import SUPERVISOR_TOOLS, execute_tool


class TestSupervisorToolDefinitions:
    """Verify SUPERVISOR_TOOLS schema correctness."""

    def test_tools_is_nonempty_list(self) -> None:
        assert isinstance(SUPERVISOR_TOOLS, list)
        # dispatch_worker, recall, search_web + fetch_url (WS2), plus
        # 7 orchestration tools added in Slice 7.5b (spec §17.9):
        # decompose_and_dispatch, get_running_tasks, get_task_progress,
        # get_working_doc, cancel_task, modify_task, record_decision.
        # Slice 7.5c §17.7c removed the legacy ``start_autonomous`` tool.
        assert len(SUPERVISOR_TOOLS) == 11

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

    def test_start_autonomous_removed(self) -> None:
        """Slice 7.5c §17.7c: the legacy ``start_autonomous`` tool is gone.

        Autonomous goals now flow through ``decompose_and_dispatch`` with
        ``pipeline_name="user_autonomous_task"`` so the supervisor never
        talks to the legacy runtime directly.
        """
        names = {t["name"] for t in SUPERVISOR_TOOLS}
        assert "start_autonomous" not in names


class TestExecuteTool:
    """Verify tool execution routing."""

    def test_user_autonomous_task_goal_can_reference_cancel_probe(self) -> None:
        from kora_v2.graph.dispatch import _is_cancel_probe_request

        assert not _is_cancel_probe_request(
            "user_autonomous_task",
            "Prepare the useful doctor checklist and also mention cancel-probe.",
        )
        assert _is_cancel_probe_request(
            "cancel_probe",
            "Disposable broad generic prep helper.",
        )

    @pytest.mark.asyncio
    async def test_get_running_tasks_returns_summary_and_acknowledges_terminal(
        self,
    ) -> None:
        from kora_v2.graph.dispatch import _orch_get_running_tasks

        task = SimpleNamespace(
            id="task-1",
            stage_name="research",
            state=SimpleNamespace(value="completed"),
            goal="local-first note tools",
            result_summary="research: report written with 5 sources",
            error_message=None,
            pipeline_instance_id="pipe-1",
        )
        engine = SimpleNamespace(
            list_tasks=AsyncMock(return_value=[task]),
            acknowledge_task=AsyncMock(return_value=True),
        )

        result = json.loads(
            await _orch_get_running_tasks(
                engine,
                {"relevant_to_session": True, "user_message": "what finished?"},
                session_id="sess-1",
            )
        )

        assert result["tasks"][0]["result_summary"] == (
            "research: report written with 5 sources"
        )
        engine.acknowledge_task.assert_awaited_once_with("task-1")

    @pytest.mark.asyncio
    async def test_cancel_task_preserves_research_when_user_says_keep_it(
        self,
    ) -> None:
        from kora_v2.graph.dispatch import _orch_cancel_task

        task = SimpleNamespace(
            id="task-1",
            stage_name="run",
            goal="proactive_research: Research local-first tools",
            result_summary=None,
            pipeline_instance_id="pipe-1",
        )
        instance = SimpleNamespace(
            pipeline_name="proactive_research",
            goal="Research top tools",
        )
        registry = SimpleNamespace(load=AsyncMock(return_value=instance))
        engine = SimpleNamespace(
            get_task=AsyncMock(return_value=task),
            instance_registry=registry,
            cancel_task=AsyncMock(return_value=True),
        )

        result = json.loads(
            await _orch_cancel_task(
                engine,
                {
                    "task_id": "task-1",
                    "reason": "stop the writing task, keep the research task",
                },
            )
        )

        assert result["status"] == "ok"
        assert result["cancelled"] is False
        engine.cancel_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_task_rejects_reason_target_mismatch(
        self,
    ) -> None:
        from kora_v2.graph.dispatch import _orch_cancel_task

        task = SimpleNamespace(
            id="task-1",
            stage_name="run",
            goal="proactive_research: Research local-first tools",
            result_summary=None,
            error_message=None,
            pipeline_instance_id="pipe-1",
        )
        instance = SimpleNamespace(
            pipeline_name="proactive_research",
            goal="Research top tools",
        )
        registry = SimpleNamespace(load=AsyncMock(return_value=instance))
        engine = SimpleNamespace(
            get_task=AsyncMock(return_value=task),
            instance_registry=registry,
            cancel_task=AsyncMock(return_value=True),
        )

        result = json.loads(
            await _orch_cancel_task(
                engine,
                {
                    "task_id": "task-1",
                    "reason": "cancel that launch-note background task",
                },
            )
        )

        assert result["status"] == "ok"
        assert result["cancelled"] is False
        assert "does not match" in result["message"]
        engine.cancel_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_only_probe_reason_does_not_cancel_research_task(
        self,
    ) -> None:
        from kora_v2.graph.dispatch import _orch_cancel_task

        task = SimpleNamespace(
            id="task-research",
            stage_name="run",
            goal="proactive_research: Research local-first productivity tools",
            result_summary=None,
            error_message=None,
            pipeline_instance_id="pipe-1",
        )
        instance = SimpleNamespace(
            pipeline_name="proactive_research",
            goal="Research local-first tools",
        )
        registry = SimpleNamespace(load=AsyncMock(return_value=instance))
        engine = SimpleNamespace(
            get_task=AsyncMock(return_value=task),
            instance_registry=registry,
            cancel_task=AsyncMock(return_value=True),
        )

        result = json.loads(
            await _orch_cancel_task(
                engine,
                {
                    "task_id": "task-research",
                    "reason": (
                        "cancel only cancel-probe right now. do not cancel "
                        "or disturb the unrelated local-first research task."
                    ),
                },
            )
        )

        assert result["status"] == "ok"
        assert result["cancelled"] is False
        engine.cancel_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_only_probe_reason_does_not_cancel_routine_task(
        self,
    ) -> None:
        from kora_v2.graph.dispatch import _orch_cancel_task

        task = SimpleNamespace(
            id="task-routine",
            stage_name="routine",
            goal="routine_tiny_morning_reset: run the morning routine",
            result_summary=None,
            error_message=None,
            pipeline_instance_id="pipe-routine",
        )
        instance = SimpleNamespace(
            pipeline_name="routine_tiny_morning_reset",
            goal="Tiny Morning Reset",
        )
        registry = SimpleNamespace(load=AsyncMock(return_value=instance))
        engine = SimpleNamespace(
            get_task=AsyncMock(return_value=task),
            instance_registry=registry,
            cancel_task=AsyncMock(return_value=True),
        )

        result = json.loads(
            await _orch_cancel_task(
                engine,
                {
                    "task_id": "task-routine",
                    "reason": (
                        "The cancel-probe helper is noisy now. Cancel only "
                        "cancel-probe right now."
                    ),
                },
            )
        )

        assert result["status"] == "ok"
        assert result["cancelled"] is False
        assert "protected system pipeline" in result["message"]
        engine.cancel_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_task_preserves_proactive_research_with_exact_task_id(
        self,
    ) -> None:
        from kora_v2.graph.dispatch import _orch_cancel_task

        task = SimpleNamespace(
            id="task-research-user-added",
            stage_name="user_added",
            goal="Compare one local-only option against one cloud option",
            result_summary=None,
            error_message=None,
            pipeline_instance_id="proactive_research-1",
        )
        instance = SimpleNamespace(
            pipeline_name="proactive_research",
            goal="Research local-first productivity tools",
        )
        registry = SimpleNamespace(load=AsyncMock(return_value=instance))
        engine = SimpleNamespace(
            get_task=AsyncMock(return_value=task),
            instance_registry=registry,
            cancel_task=AsyncMock(return_value=True),
        )

        result = json.loads(
            await _orch_cancel_task(
                engine,
                {
                    "task_id": "task-research-user-added",
                    "reason": (
                        "The exact cancel-probe worker task id is "
                        "task-probe. Cancel task-probe now. "
                        "Leave proactive_research alone."
                    ),
                },
            )
        )

        assert result["status"] == "ok"
        assert result["cancelled"] is False
        assert "proactive_research" in result["message"]
        engine.cancel_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_task_allows_explicit_proactive_research_cancel(
        self,
    ) -> None:
        from kora_v2.graph.dispatch import _orch_cancel_task

        task = SimpleNamespace(
            id="task-research-user-added",
            stage_name="user_added",
            goal="Compare one local-only option against one cloud option",
            result_summary=None,
            error_message=None,
            pipeline_instance_id="proactive_research-1",
        )
        instance = SimpleNamespace(
            pipeline_name="proactive_research",
            goal="Research local-first productivity tools",
        )
        registry = SimpleNamespace(load=AsyncMock(return_value=instance))
        engine = SimpleNamespace(
            get_task=AsyncMock(return_value=task),
            instance_registry=registry,
            cancel_task=AsyncMock(return_value=True),
        )

        result = json.loads(
            await _orch_cancel_task(
                engine,
                {
                    "task_id": "task-research-user-added",
                    "reason": "cancel the proactive_research task now",
                },
            )
        )

        assert result["status"] == "ok"
        assert result["cancelled"] is True
        engine.cancel_task.assert_awaited_once_with(
            "task-research-user-added",
            reason="cancel the proactive_research task now",
        )

    @pytest.mark.asyncio
    async def test_cancel_task_accepts_pipeline_instance_id(
        self,
    ) -> None:
        from kora_v2.graph.dispatch import _orch_cancel_task

        instance = SimpleNamespace(
            pipeline_name="cancel-probe",
            goal="Summarize throwaway cancellation testing",
        )
        task = SimpleNamespace(
            id="task-probe",
            stage_name="research_and_summarize",
            state="pending",
            goal="cancel-probe: throwaway cancellation testing",
        )
        instance_registry = SimpleNamespace(load=AsyncMock(return_value=instance))
        task_registry = SimpleNamespace(load_by_pipeline=AsyncMock(return_value=[task]))
        engine = SimpleNamespace(
            get_task=AsyncMock(return_value=None),
            instance_registry=instance_registry,
            task_registry=task_registry,
            cancel_task=AsyncMock(return_value=True),
        )

        result = json.loads(
            await _orch_cancel_task(
                engine,
                {
                    "task_id": "cancel-probe-123",
                    "reason": (
                        "User explicitly asked to cancel only the "
                        "cancel-probe pipeline, keep proactive_research running"
                    ),
                },
            )
        )

        assert result["status"] == "ok"
        assert result["cancelled"] is True
        assert result["cancelled_task_ids"] == ["task-probe"]
        engine.cancel_task.assert_awaited_once_with(
            "task-probe",
            reason=(
                "User explicitly asked to cancel only the cancel-probe "
                "pipeline, keep proactive_research running"
            ),
        )

    @pytest.mark.asyncio
    async def test_cancel_task_allows_exact_task_id_with_generic_reason(
        self,
    ) -> None:
        from kora_v2.graph.dispatch import _orch_cancel_task

        task = SimpleNamespace(
            id="task-probe",
            stage_name="throwaway_summary",
            goal="Disposable probe task",
            result_summary=None,
            error_message=None,
            pipeline_instance_id="pipe-probe",
        )
        instance = SimpleNamespace(
            pipeline_name="cancel_probe",
            goal="Disposable probe task",
        )
        registry = SimpleNamespace(load=AsyncMock(return_value=instance))
        engine = SimpleNamespace(
            get_task=AsyncMock(return_value=task),
            instance_registry=registry,
            cancel_task=AsyncMock(return_value=True),
        )

        result = json.loads(
            await _orch_cancel_task(
                engine,
                {
                    "task_id": "task-probe",
                    "reason": "Jordan explicitly requested cancellation of this specific task only",
                },
            )
        )

        assert result["status"] == "ok"
        assert result["cancelled"] is True
        engine.cancel_task.assert_awaited_once_with(
            "task-probe",
            reason="Jordan explicitly requested cancellation of this specific task only",
        )

    @pytest.mark.asyncio
    async def test_cancel_task_preserves_system_pipeline_without_explicit_name(
        self,
    ) -> None:
        from kora_v2.graph.dispatch import _orch_cancel_task

        task = SimpleNamespace(
            id="task-1",
            stage_name="consolidate",
            goal="Consolidate semantically related notes",
            result_summary=None,
            pipeline_instance_id="post_session_memory-1",
        )
        instance = SimpleNamespace(
            pipeline_name="post_session_memory",
            goal="Memory Steward: extract -> consolidate -> dedup.",
        )
        registry = SimpleNamespace(load=AsyncMock(return_value=instance))
        engine = SimpleNamespace(
            get_task=AsyncMock(return_value=task),
            instance_registry=registry,
            cancel_task=AsyncMock(return_value=True),
        )

        result = json.loads(
            await _orch_cancel_task(
                engine,
                {
                    "task_id": "task-1",
                    "reason": "stop that background research",
                },
            )
        )

        assert result["status"] == "ok"
        assert result["cancelled"] is False
        assert "protected system pipeline" in result["message"]
        engine.cancel_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_task_allows_explicit_system_pipeline_cancel(
        self,
    ) -> None:
        from kora_v2.graph.dispatch import _orch_cancel_task

        task = SimpleNamespace(
            id="task-1",
            stage_name="consolidate",
            goal="Consolidate semantically related notes",
            result_summary=None,
            pipeline_instance_id="post_session_memory-1",
        )
        instance = SimpleNamespace(
            pipeline_name="post_session_memory",
            goal="Memory Steward: extract -> consolidate -> dedup.",
        )
        registry = SimpleNamespace(load=AsyncMock(return_value=instance))
        engine = SimpleNamespace(
            get_task=AsyncMock(return_value=task),
            instance_registry=registry,
            cancel_task=AsyncMock(return_value=True),
        )

        result = json.loads(
            await _orch_cancel_task(
                engine,
                {
                    "task_id": "task-1",
                    "reason": "cancel the post_session_memory pipeline",
                },
            )
        )

        assert result["status"] == "ok"
        assert result["cancelled"] is True
        engine.cancel_task.assert_awaited_once_with(
            "task-1",
            reason="cancel the post_session_memory pipeline",
        )

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
