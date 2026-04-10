"""Kora V2 — Executor worker agent.

Executes concrete tasks using real, callable filesystem tools. Results are
verifiable on disk before Kora claims success.

Execution flow
--------------
1. Fast path: if task matches known exact task names, call _execute_local_filesystem
   directly (no LLM needed).
2. Fuzzy path: if task description matches file-operation patterns, offer the five
   real filesystem tools + structured_execution_output (fallback) to the LLM via
   generate_with_tools().
3. If the LLM calls a filesystem tool, execute it for real via ToolRegistry.get_callable()
   and build ExecutionOutput from the actual on-disk result.
4. If the LLM calls structured_execution_output, use that as the result (handles
   non-file tasks).
5. If the LLM returns no tool call, raise ValueError (structured output is mandatory).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

# Trigger filesystem tool registration and import path safety helper
import kora_v2.tools.filesystem  # noqa: F401
from kora_v2.agents.harness import AgentHarness
from kora_v2.core.models import (
    Artifact,
    ExecutionInput,
    ExecutionOutput,
    ToolCallRecord,
)
from kora_v2.tools.filesystem import _resolve_safe
from kora_v2.tools.registry import ToolRegistry, get_schema_tool
from kora_v2.tools.types import ToolCategory

log = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

EXECUTOR_SYSTEM_PROMPT = """\
You are Kora's executor agent. Your job is to carry out tasks precisely and \
verifiably.

When a task involves writing to disk, call the appropriate filesystem tool \
(write_file, create_directory, etc.) with the exact path and content provided \
in the task parameters.

After completing the work (or if no filesystem action is needed), call \
structured_execution_output with the result.

Rules:
- Never fabricate success — only report success=true if a tool confirmed it.
- Always use the structured_execution_output tool to report your result.
- Keep result strings concise and factual.
"""

# Verbs/phrases that indicate a file-write task
_FILE_WRITE_PATTERNS: tuple[str, ...] = (
    "write_file",
    "create_file",
    "use write_file",
    "save to",
    "save the",
    "create a file",
    "write to file",
    "write file",
    "create directory",
    "make directory",
    "mkdir",
)


def _looks_like_file_task(task_lower: str) -> bool:
    """Return True if task description matches common file-operation patterns."""
    return any(pattern in task_lower for pattern in _FILE_WRITE_PATTERNS)


# ── Adapter helper ────────────────────────────────────────────────────────────


def plan_step_to_execution_input(step: Any, plan_context: str = "") -> ExecutionInput:
    """Convert a PlanStep into an ExecutionInput.

    Args:
        step: A PlanStep model instance.
        plan_context: Optional broader plan context for the executor.

    Returns:
        ExecutionInput ready for the executor harness.
    """
    return ExecutionInput(
        task=f"{step.title}: {step.description}",
        tools_available=list(step.tools_needed),
        context=plan_context,
        energy_level=step.energy_level,
        estimated_minutes=step.estimated_minutes,
    )


# ── Structured output tool schema ─────────────────────────────────────────────


def _build_execution_output_tool() -> dict[str, Any]:
    """Build the Anthropic tool definition for structured execution output."""
    return get_schema_tool(
        name="structured_execution_output",
        description=(
            "Submit the final execution result. Call this after you have performed "
            "any necessary filesystem or other actions, or if the task requires no "
            "side effects. Fill in the actual result, tool_calls_made, and artifacts."
        ),
        schema=ExecutionOutput.model_json_schema(),
    )


# ── Executor Harness ──────────────────────────────────────────────────────────


class ExecutorWorkerHarness(AgentHarness[ExecutionInput, ExecutionOutput]):
    """Executor worker: runs concrete tasks with real callable tools.

    Uses the LLM with real filesystem tools + structured output forcing to
    produce an ExecutionOutput after executing the specified task.

    Filesystem tool results are verified on disk before claiming success.
    """

    input_schema = ExecutionInput
    output_schema = ExecutionOutput

    def __init__(self, container: Any, middleware: list | None = None) -> None:
        super().__init__(
            middleware=middleware or [],
            quality_gates=[],
            agent_name="executor",
        )
        self._container = container

    # ── Fast path: deterministic local filesystem ─────────────────────────────

    async def _execute_local_filesystem(
        self,
        input_data: ExecutionInput,
    ) -> ExecutionOutput | None:
        """Handle simple local filesystem actions deterministically.

        Returns ExecutionOutput if the task name is a recognised exact match,
        or None to fall through to the LLM path.
        """
        task = input_data.task.strip().lower()
        params: dict[str, Any] = {}
        if hasattr(input_data, "params") and input_data.params:
            params = dict(input_data.params)

        if task not in {"write_file", "create_directory"}:
            return None

        path_value = params.get("path") or params.get("file_path")
        if not isinstance(path_value, str) or not path_value.strip():
            return None

        # Security: validate path before any I/O
        path = _resolve_safe(path_value)
        if path is None:
            log.warning("executor_fast_path_blocked", path=path_value)
            return ExecutionOutput(
                result=f"Path '{path_value}' is blocked or invalid",
                tool_calls_made=[],
                artifacts=[],
                success=False,
                confidence=1.0,
                error=f"Path '{path_value}' is blocked or invalid",
            )

        timestamp = datetime.now(UTC)

        if task == "create_directory":
            path.mkdir(parents=True, exist_ok=True)
            return ExecutionOutput(
                result=f"Created directory {path}",
                tool_calls_made=[
                    ToolCallRecord(
                        tool_name="filesystem.create_directory",
                        args={"path": str(path)},
                        result_summary=f"Created directory {path}",
                        success=True,
                        duration_ms=0,
                        timestamp=timestamp,
                    )
                ],
                artifacts=[
                    Artifact(type="file", uri=str(path), label=path.name or str(path))
                ],
                success=True,
                confidence=1.0,
            )

        content = params.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        size_bytes = path.stat().st_size
        return ExecutionOutput(
            result=f"Wrote {size_bytes} bytes to {path}",
            tool_calls_made=[
                ToolCallRecord(
                    tool_name="filesystem.write_file",
                    args={"path": str(path), "content": content},
                    result_summary=f"Wrote {size_bytes} bytes to {path}",
                    success=True,
                    duration_ms=0,
                    timestamp=timestamp,
                )
            ],
            artifacts=[
                Artifact(
                    type="file",
                    uri=str(path),
                    label=path.name,
                    size_bytes=size_bytes,
                )
            ],
            success=True,
            confidence=1.0,
        )

    # ── Execute real filesystem tool call ─────────────────────────────────────

    async def _dispatch_filesystem_tool(
        self, tool_name: str, args: dict[str, Any]
    ) -> ExecutionOutput:
        """Execute *tool_name* via ToolRegistry and build a verified ExecutionOutput."""
        timestamp = datetime.now(UTC)
        func = ToolRegistry.get_callable(tool_name)
        if func is None:
            raise ValueError(f"Filesystem tool not found in registry: {tool_name!r}")

        # Get the input model and instantiate it
        input_model_cls = ToolRegistry.get_input_model(tool_name)
        if input_model_cls is None:
            raise ValueError(f"Input model not found for tool: {tool_name!r}")

        input_obj = input_model_cls(**args)
        result_str = await func(input_obj, self._container)

        try:
            result_data: dict[str, Any] = json.loads(result_str)
        except json.JSONDecodeError:
            result_data = {"success": False, "error": f"Non-JSON result: {result_str[:200]}"}

        success = bool(result_data.get("success", False))
        path_str = result_data.get("path", args.get("path", ""))
        size_bytes = result_data.get("size_bytes")
        message = result_data.get("message") or result_data.get("error") or result_str[:200]

        # Verify file actually exists for write operations
        if success and tool_name == "write_file":
            if not Path(path_str).exists():
                success = False
                message = f"write_file reported success but file not found on disk: {path_str}"
                log.warning("executor.write_file_verify_failed", path=path_str)

        record = ToolCallRecord(
            tool_name=f"filesystem.{tool_name}",
            args=args,
            result_summary=message[:200],
            success=success,
            duration_ms=0,
            timestamp=timestamp,
        )

        artifacts = []
        if success and tool_name == "write_file" and path_str:
            artifacts.append(
                Artifact(
                    type="file",
                    uri=path_str,
                    label=Path(path_str).name or path_str,
                    size_bytes=size_bytes,
                )
            )
        elif success and tool_name == "create_directory" and path_str:
            artifacts.append(
                Artifact(type="file", uri=path_str, label=Path(path_str).name or path_str)
            )

        return ExecutionOutput(
            result=message,
            tool_calls_made=[record],
            artifacts=artifacts,
            success=success,
            confidence=1.0 if success else 0.0,
            error=None if success else message,
        )

    # ── Main execute method ────────────────────────────────────────────────────

    async def _execute(self, input_data: ExecutionInput) -> ExecutionOutput:
        """Execute a task, preferring real tool calls over LLM fabrication.

        1. Try exact-match fast path (local filesystem).
        2. If the task looks like a file operation, build tool list with real
           filesystem tools + structured_execution_output and call generate_with_tools().
        3. If LLM calls a filesystem tool, dispatch it for real.
        4. If LLM calls structured_execution_output, return that (non-file tasks).
        5. If LLM returns no tool call, raise ValueError.
        """
        # Step 1: fast path
        local_result = await self._execute_local_filesystem(input_data)
        if local_result is not None:
            log.info(
                "executor_local_filesystem_complete",
                task=input_data.task,
                success=local_result.success,
            )
            return local_result

        # Build user message
        user_content = f"Task: {input_data.task}"
        if hasattr(input_data, "params") and input_data.params:
            user_content += f"\n\nParameters: {input_data.params}"
        if input_data.tools_available:
            user_content += f"\n\nAvailable tools: {', '.join(input_data.tools_available)}"
        if input_data.context:
            user_content += f"\n\nContext: {input_data.context}"
        if input_data.energy_level:
            user_content += f"\n\nUser energy level: {input_data.energy_level}"
        if input_data.estimated_minutes is not None:
            user_content += f"\nTime budget: {input_data.estimated_minutes} minutes"

        # Schema repair: append validation error from previous attempt
        repair_hint = getattr(self, "_schema_repair_hint", None)
        if repair_hint:
            user_content += (
                f"\n\n SCHEMA REPAIR REQUIRED: Your previous response failed validation.\n"
                f"Error: {repair_hint[:400]}\n"
                "Please call the structured_execution_output tool with a valid schema."
            )

        messages = [{"role": "user", "content": user_content}]

        # Step 2: build tool list
        task_lower = input_data.task.lower()
        fs_tool_names = [
            t.name
            for t in ToolRegistry.get_by_category(ToolCategory.FILESYSTEM)
        ]

        tool_defs: list[dict[str, Any]] = []
        if _looks_like_file_task(task_lower) or any(
            t in task_lower for t in fs_tool_names
        ):
            # Include real filesystem tools so LLM can actually invoke them
            fs_defs = ToolRegistry.get_anthropic_tools(
                categories={ToolCategory.FILESYSTEM}
            )
            tool_defs.extend(fs_defs)
            log.debug(
                "executor_including_filesystem_tools",
                count=len(fs_defs),
                task=input_data.task[:60],
            )

        # Always include structured_execution_output as final reporting tool
        tool_defs.append(_build_execution_output_tool())

        # Force the LLM to call one of the tools we offered. This prevents
        # prose responses AND cuts down on tool-name hallucinations: with
        # ``any`` the model still picks WHICH tool, but it must be one of
        # the declared tools (fs operation or structured_execution_output),
        # not a fabricated name like ``web_search``.
        log.info("executor_calling_llm", task=input_data.task[:80], tools=len(tool_defs))

        result = await self._container.llm.generate_with_tools(
            messages=messages,
            tools=tool_defs,
            system_prompt=EXECUTOR_SYSTEM_PROMPT,
            thinking_enabled=False,
            tool_choice="any",
        )

        if not result.tool_calls:
            raise ValueError(
                "Executor LLM did not produce any tool call. "
                "Structured output is required — prose responses are not accepted."
            )

        tc = result.tool_calls[0]
        tool_name = tc.name
        args = tc.arguments

        # Step 3: real filesystem tool dispatch
        if tool_name in fs_tool_names:
            log.info("executor_dispatching_real_tool", tool=tool_name)
            exec_output = await self._dispatch_filesystem_tool(tool_name, args)
            log.info(
                "executor_real_tool_complete",
                tool=tool_name,
                success=exec_output.success,
            )
            return exec_output

        # Step 4: structured_execution_output fallback (non-file tasks)
        if tool_name == "structured_execution_output":
            data = dict(args)
            data.setdefault("result", "")
            data.setdefault("success", True)
            data.setdefault("confidence", 0.7)
            data.setdefault("tool_calls_made", [])
            data.setdefault("artifacts", [])
            exec_output = ExecutionOutput.model_validate(data)
            log.info(
                "executor_structured_output",
                success=exec_output.success,
                confidence=exec_output.confidence,
                tool_calls=len(exec_output.tool_calls_made),
            )
            return exec_output

        # Step 5: unexpected tool — attempt best-effort structured parse
        log.warning("executor_unexpected_tool", tool=tool_name)
        raise ValueError(
            f"Executor LLM called unexpected tool '{tool_name}'. "
            "Expected a filesystem tool or structured_execution_output."
        )
