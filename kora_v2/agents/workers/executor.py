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
import re
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

_STRUCTURED_ARTIFACT_MARKERS: tuple[str, ...] = (
    "artifact",
    "deliverable",
    "document",
    "report",
    "write",
    "draft",
    "create",
    "save",
    "markdown",
    ".md",
)

_STRUCTURE_MARKERS: tuple[str, ...] = (
    "structured",
    "section",
    "sections",
    "heading",
    "headings",
    "bullet",
    "bullets",
    "bullet points",
    "checklist",
    "outline",
    "table",
)


def _looks_like_file_task(task_lower: str) -> bool:
    """Return True if task description matches common file-operation patterns."""
    return any(pattern in task_lower for pattern in _FILE_WRITE_PATTERNS)


def _normalize_tool_call_records(raw: Any) -> list[dict[str, Any]]:
    """Coerce loose LLM tool-call bookkeeping into ToolCallRecord dicts."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []

    normalized: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for item in raw:
        if not isinstance(item, dict):
            continue
        tool_name = item.get("tool_name") or item.get("name") or item.get("tool")
        if not tool_name:
            continue
        result_summary = (
            item.get("result_summary")
            or item.get("summary")
            or item.get("result")
            or item.get("output")
            or ""
        )
        normalized.append(
            {
                "tool_name": str(tool_name),
                "args": item.get("args") if isinstance(item.get("args"), dict) else {},
                "result_summary": str(result_summary)[:500],
                "success": bool(item.get("success", True)),
                "duration_ms": int(item.get("duration_ms") or 0),
                "timestamp": item.get("timestamp") or now,
            }
        )
    return normalized


def _absolute_paths_in_text(text: str) -> list[str]:
    """Extract absolute file paths mentioned in an executor task."""
    matches = re.findall(r"(?<![`\\w])/(?:[A-Za-z0-9._-]+/?)+", text)
    return [
        path for path in (m.rstrip(".,);:") for m in matches)
        if path.count("/") >= 2
    ]


def _requires_structured_artifact(task: str, context: str = "") -> bool:
    """Detect requests where a thin summary is not a valid artifact."""
    text = f"{task}\n{context}".lower()
    asks_for_artifact = (
        any(marker in text for marker in _STRUCTURED_ARTIFACT_MARKERS)
        or bool(_absolute_paths_in_text(text))
    )
    asks_for_structure = any(marker in text for marker in _STRUCTURE_MARKERS)
    return asks_for_artifact and asks_for_structure


def _structured_result_is_too_thin(result_text: str) -> bool:
    """Reject one-sentence fallback text for structured artifact requests."""
    stripped = result_text.strip()
    if not stripped:
        return True

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    word_count = len(re.findall(r"\b\w+\b", stripped))
    structure_lines = sum(
        1
        for line in lines
        if re.match(r"^(#{1,6}\s+|[-*]\s+|\d+[.)]\s+)", line)
        or line.endswith(":")
    )

    return word_count < 60 or len(lines) < 4 or structure_lines < 2


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
        tools_hint = {
            t.strip().lower()
            for t in (input_data.tools_available or [])
            if isinstance(t, str)
        }

        tool_defs: list[dict[str, Any]] = []
        seen_tool_names: set[str] = set()

        def _add_tool(tool_def: dict[str, Any]) -> None:
            name = tool_def.get("name")
            if not name or name in seen_tool_names:
                return
            tool_defs.append(tool_def)
            seen_tool_names.add(name)

        if _looks_like_file_task(task_lower) or any(
            t in task_lower for t in fs_tool_names
        ) or any(t in tools_hint for t in fs_tool_names):
            # Include real filesystem tools so LLM can actually invoke them
            for fs_def in ToolRegistry.get_anthropic_tools(
                categories={ToolCategory.FILESYSTEM}
            ):
                _add_tool(fs_def)
            log.debug(
                "executor_including_filesystem_tools",
                count=len(tool_defs),
                task=input_data.task[:60],
            )

        # Research-oriented tool surface. Background research pipelines
        # (e.g. proactive_research) route through the executor and ask for
        # web/browser work. Without these the LLM reaches for ``browser``
        # or ``web_search`` and the executor hard-fails with
        # ``executor_unexpected_tool``. The supervisor dispatch layer
        # already knows how to run these tools, so we reuse the same
        # schemas here and route the call back through ``execute_tool``
        # below.
        _RESEARCH_KEYWORDS = (
            "research",
            "search",
            "browse",
            "web",
            "internet",
            "article",
            "news",
            "find out",
            "look up",
            "investigate",
        )
        research_signal = (
            any(kw in task_lower for kw in _RESEARCH_KEYWORDS)
            or any(
                t in tools_hint
                for t in ("search_web", "fetch_url", "browser")
            )
            or any(t.startswith("browser.") for t in tools_hint)
        )
        if research_signal:
            from kora_v2.graph.capability_bridge import (
                collect_capability_tools,
            )
            from kora_v2.graph.dispatch import SUPERVISOR_TOOLS

            for tool in SUPERVISOR_TOOLS:
                if tool["name"] in {"search_web", "fetch_url"}:
                    _add_tool(dict(tool))
            for cap_tool in collect_capability_tools(self._container):
                if cap_tool["name"].startswith("browser."):
                    _add_tool(dict(cap_tool))
            log.debug(
                "executor_including_research_tools",
                count=len(tool_defs),
                task=input_data.task[:60],
            )

        # Always include structured_execution_output as final reporting tool
        _add_tool(_build_execution_output_tool())

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
            max_tokens=3000,
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
            data["tool_calls_made"] = _normalize_tool_call_records(
                data.get("tool_calls_made", [])
            )
            data.setdefault("artifacts", [])
            if bool(data.get("success", True)):
                result_text = str(data.get("result") or "").strip()
                if _requires_structured_artifact(input_data.task, input_data.context) and (
                    _structured_result_is_too_thin(result_text)
                ):
                    raise ValueError(
                        "Structured output claimed success for a structured "
                        "artifact request, but the result is too thin. Provide "
                        "the requested sections/bullets with substantive content "
                        "or mark success=false."
                    )

                mentioned_paths = _absolute_paths_in_text(
                    f"{input_data.task}\n{input_data.context}"
                )
                missing_paths = [
                    path for path in mentioned_paths
                    if Path(path).suffix and not Path(path).exists()
                ]
                if missing_paths:
                    writable = _resolve_safe(missing_paths[0])
                    if result_text and writable is not None:
                        writable.parent.mkdir(parents=True, exist_ok=True)
                        writable.write_text(result_text + "\n", encoding="utf-8")
                        data["tool_calls_made"].append(
                            {
                                "tool_name": "filesystem.write_file",
                                "args": {
                                    "path": str(writable),
                                    "content": result_text,
                                },
                                "result_summary": (
                                    f"Wrote {writable.stat().st_size} bytes "
                                    f"to {writable}"
                                ),
                                "success": True,
                                "duration_ms": 0,
                                "timestamp": datetime.now(UTC),
                            }
                        )
                        data["artifacts"] = list(data.get("artifacts") or [])
                        data["artifacts"].append(
                            {
                                "type": "file",
                                "uri": str(writable),
                                "label": writable.name,
                                "size_bytes": writable.stat().st_size,
                            }
                        )
                        missing_paths = [
                            path for path in missing_paths
                            if not Path(path).exists()
                        ]
                if missing_paths:
                    raise ValueError(
                        "Structured output claimed success, but expected "
                        f"file(s) do not exist: {', '.join(missing_paths[:3])}. "
                        "Call write_file/create_directory first, then report."
                    )
            exec_output = ExecutionOutput.model_validate(data)
            log.info(
                "executor_structured_output",
                success=exec_output.success,
                confidence=exec_output.confidence,
                tool_calls=len(exec_output.tool_calls_made),
            )
            return exec_output

        # Step 5: research/capability tool call — route through the
        # supervisor execute_tool surface which already handles
        # search_web, fetch_url, and capability-pack actions. Returns a
        # summarised ExecutionOutput built from the tool's JSON result.
        if tool_name in {"search_web", "fetch_url"} or "." in tool_name:
            from kora_v2.graph.dispatch import execute_tool

            timestamp = datetime.now(UTC)
            try:
                result_str = await execute_tool(
                    tool_name,
                    dict(args),
                    container=self._container,
                )
            except Exception as exc:
                log.warning(
                    "executor_research_tool_failed",
                    tool=tool_name,
                    error=str(exc),
                )
                return ExecutionOutput(
                    result=f"{tool_name} failed: {exc}",
                    tool_calls_made=[
                        ToolCallRecord(
                            tool_name=tool_name,
                            args=dict(args),
                            result_summary=str(exc)[:200],
                            success=False,
                            duration_ms=0,
                            timestamp=timestamp,
                        )
                    ],
                    artifacts=[],
                    success=False,
                    confidence=0.0,
                    error=str(exc),
                )

            try:
                payload = json.loads(result_str)
            except json.JSONDecodeError:
                payload = {"raw": result_str[:500]}

            success = not (
                isinstance(payload, dict)
                and (
                    payload.get("status") == "error"
                    or payload.get("error")
                )
            )
            summary = (
                payload.get("content")
                or payload.get("message")
                or payload.get("error")
                or result_str[:200]
                if isinstance(payload, dict)
                else result_str[:200]
            )
            log.info(
                "executor_research_tool_complete",
                tool=tool_name,
                success=success,
            )
            return ExecutionOutput(
                result=str(summary)[:2000],
                tool_calls_made=[
                    ToolCallRecord(
                        tool_name=tool_name,
                        args=dict(args),
                        result_summary=str(summary)[:200],
                        success=success,
                        duration_ms=0,
                        timestamp=timestamp,
                    )
                ],
                artifacts=[],
                success=success,
                confidence=0.8 if success else 0.0,
                error=None if success else str(summary)[:400],
            )

        # Step 6: unexpected tool — attempt best-effort structured parse
        log.warning("executor_unexpected_tool", tool=tool_name)
        raise ValueError(
            f"Executor LLM called unexpected tool '{tool_name}'. "
            "Expected a filesystem tool or structured_execution_output."
        )
