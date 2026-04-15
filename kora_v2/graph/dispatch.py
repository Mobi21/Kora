"""Supervisor dispatch tools.

Defines the tool schemas (Anthropic format) that the supervisor LLM
can call, and an ``execute_tool`` function that routes to real
implementations.

Phase 4.67: dispatch_worker is real (delegates to worker harnesses).
            recall is real (hybrid memory search).
Phase 6:    start_autonomous is real (spawns AutonomousExecutionLoop
            as an asyncio background task).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog

from kora_v2.tools.types import AuthLevel

log = structlog.get_logger(__name__)

_TOOL_RISK_LEVELS: dict[str, str] = {
    "recall": "low",
    "search_web": "low",
    "fetch_url": "low",
}


# =====================================================================
# Auth Check
# =====================================================================


async def check_tool_auth(
    tool_name: str,
    tool_args: dict[str, Any],
    auth_level: AuthLevel,
    auth_relay: Any | None,
    auth_mode: str = "prompt",
    *,
    session_id: str | None = None,
    risk_level: str = "unknown",
) -> bool:
    """Check authorization for a tool call.

    Three-layer check:
    1. ALWAYS_ALLOWED -> True
    2. NEVER -> always False (even in trust_all mode)
    3. ASK_FIRST -> trust_all skips, else relay.request_permission()

    Args:
        tool_name: Name of the tool being authorized.
        tool_args: Arguments the tool will receive.
        auth_level: The tool's auth level from ToolDefinition.
        auth_relay: AuthRelay instance (None if not available).
        auth_mode: "prompt" or "trust_all" from settings.

    Returns:
        True if authorized, False if denied.
    """
    if auth_level == AuthLevel.ALWAYS_ALLOWED:
        return True

    if auth_level == AuthLevel.NEVER:
        log.info("auth_blocked_never", tool=tool_name)
        return False

    # ASK_FIRST
    if auth_mode == "trust_all":
        log.debug("auth_trust_all_skip", tool=tool_name)
        return True

    if auth_relay is None:
        log.warning("auth_no_relay_available", tool=tool_name)
        return False

    return await auth_relay.request_permission(
        tool_name,
        tool_args,
        session_id=session_id,
        risk_level=risk_level,
    )


def _resolve_auth_context(
    tool_name: str,
    tool_args: dict[str, Any],
) -> tuple[AuthLevel, str]:
    """Return the tool auth level and risk for the pending call.

    ``dispatch_worker`` is special-cased so planning/review work stays
    frictionless while executor runs still require confirmation.
    """
    if tool_name == "dispatch_worker":
        worker_name = str(tool_args.get("worker_name", "")).strip().lower()
        if worker_name == "executor":
            return AuthLevel.ASK_FIRST, "high"
        if worker_name in {"planner", "reviewer"}:
            return AuthLevel.ALWAYS_ALLOWED, "low"
        return AuthLevel.ALWAYS_ALLOWED, "unknown"

    if tool_name in {"search_web", "fetch_url"}:
        return AuthLevel.ALWAYS_ALLOWED, "low"

    # Check ToolRegistry for auth context (filesystem, life-management, etc.)
    from kora_v2.tools.registry import ToolRegistry

    definition = ToolRegistry.get_definition(tool_name)
    if definition is not None:
        risk = "low" if definition.is_read_only else "high"
        return definition.auth_level, risk

    return AuthLevel.ALWAYS_ALLOWED, _TOOL_RISK_LEVELS.get(tool_name, "unknown")


def _active_session_id(container: Any | None) -> str | None:
    """Return the active session id from the runtime container, if any."""
    if container is None:
        return None
    session_mgr = getattr(container, "session_manager", None)
    active_session = getattr(session_mgr, "active_session", None)
    session_id = getattr(active_session, "session_id", None)
    return str(session_id) if session_id else None


# =====================================================================
# Tool Definitions (Anthropic format)
# =====================================================================

SUPERVISOR_TOOLS: list[dict[str, Any]] = [
    {
        "name": "start_autonomous",
        "description": (
            "Start a multi-step autonomous background task. "
            "Returns immediately — the task runs in the background with "
            "periodic checkpoints. Use for complex goals that require "
            "multiple steps (research, code projects, analysis). "
            "Do NOT use for simple single-turn requests."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": (
                        "Clear natural-language description of what to accomplish. "
                        "Be specific about deliverables and constraints."
                    ),
                },
                "context": {
                    "type": "string",
                    "description": "Additional context for the task.",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "dispatch_worker",
        "description": (
            "Dispatch work to a specialized worker agent. "
            "The worker runs as a LangGraph subgraph and returns its "
            "typed output as JSON. Core workers available: planner, "
            "executor, reviewer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_name": {
                    "type": "string",
                    "enum": [
                        "planner",
                        "executor",
                        "reviewer",
                    ],
                    "description": "Which worker to dispatch to.",
                },
                "input_json": {
                    "type": "string",
                    "description": (
                        "JSON-serialized worker input matching the "
                        "worker's TInput Pydantic schema."
                    ),
                },
            },
            "required": ["worker_name", "input_json"],
        },
    },
    {
        "name": "recall",
        "description": (
            "Fast deterministic memory search (~0.3s, no LLM). "
            "Embeds the query and runs hybrid vector + FTS5 search "
            "across the specified memory layer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                },
                "layer": {
                    "type": "string",
                    "enum": ["all", "long_term", "user_model"],
                    "default": "all",
                    "description": "Which memory layer to search.",
                },
                "max_results": {
                    "type": "integer",
                    "default": 10,
                    "description": "Maximum number of results to return.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_web",
        "description": (
            "Search the web for current information. Use when you need "
            "2025/2026 data, recent events, prices, or anything your "
            "training data may not have. Returns a list of search results "
            "with titles, URLs, and snippets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "count": {
                    "type": "integer",
                    "default": 5,
                    "description": "Number of results (1-10)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Fetch the text content of a URL. Use after search_web to "
            "read full articles or pages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to fetch",
                },
                "max_chars": {
                    "type": "integer",
                    "default": 8000,
                    "description": "Max characters to return",
                },
            },
            "required": ["url"],
        },
    },
    # ── Phase 7.5b orchestration tools ──────────────────────────────
    {
        "name": "decompose_and_dispatch",
        "description": (
            "Break a user request into one or more pipeline stages and "
            "hand the resulting pipeline to the orchestration engine. "
            "Use this when the work is multi-step but does not need the "
            "full plan-execute-review autonomous loop. Returns the "
            "pipeline instance id and working-doc path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": (
                        "Natural-language description of the overall "
                        "goal — used as the pipeline instance goal and "
                        "as the working-doc title."
                    ),
                },
                "pipeline_name": {
                    "type": "string",
                    "description": (
                        "Machine name for the pipeline. Used as the "
                        "registry key and working-doc filename. Keep it "
                        "snake_case and unique-ish."
                    ),
                },
                "stages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Ordered stage names. Each stage becomes a "
                        "bounded_background PipelineStage with a default "
                        "goal template."
                    ),
                },
                "in_turn": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "When true, stages are dispatched with the "
                        "in_turn preset (must return within the current "
                        "turn budget). Default false = background."
                    ),
                },
            },
            "required": ["goal", "pipeline_name", "stages"],
        },
    },
    {
        "name": "get_running_tasks",
        "description": (
            "List currently running and recently finished worker tasks. "
            "Use at turn start to check whether the user is asking about "
            "something Kora is already working on, or at any time to "
            "report progress. Pass relevant_to_session=true to filter to "
            "tasks that belong to the current session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "relevant_to_session": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Filter to tasks where parent_session_id matches "
                        "the current session id."
                    ),
                },
                "user_message": {
                    "type": "string",
                    "description": (
                        "If provided, also include tasks whose goal "
                        "overlaps the user message semantically."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_task_progress",
        "description": (
            "Fetch a single task's state, elapsed time, and most recent "
            "ledger summary. Use when the user asks 'how's the X task "
            "going' or before deciding whether to cancel/modify it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The worker task id to inspect.",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "get_working_doc",
        "description": (
            "Read a pipeline instance's working document as markdown. "
            "Use to summarize progress to the user, or when the user "
            "explicitly asks to see the plan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pipeline_instance_id": {
                    "type": "string",
                    "description": (
                        "The pipeline instance id whose working doc "
                        "should be returned."
                    ),
                },
                "section": {
                    "type": "string",
                    "description": (
                        "Optional markdown heading to slice out — e.g. "
                        "'## Plan' or '## Progress'. Omit to return the "
                        "full doc."
                    ),
                },
            },
            "required": ["pipeline_instance_id"],
        },
    },
    {
        "name": "cancel_task",
        "description": (
            "Cancel a running worker task. Writes a ledger event, moves "
            "the task to CANCELLED, and emits TASK_CANCELLED. Use only "
            "when the user explicitly asks to stop a task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task id to cancel.",
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Short reason string written to the ledger. "
                        "Usually the user's words paraphrased."
                    ),
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "modify_task",
        "description": (
            "Patch the goal, system prompt, or tool scope of a running "
            "task. Use when the user says 'actually, focus on X instead' "
            "mid-task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task id to modify.",
                },
                "new_goal": {
                    "type": "string",
                    "description": "Replacement goal string (optional).",
                },
                "new_system_prompt": {
                    "type": "string",
                    "description": (
                        "Replacement system prompt (optional)."
                    ),
                },
                "tool_scope": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Replacement tool allow-list (optional)."
                    ),
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "record_decision",
        "description": (
            "Record an open decision the user has posed but not yet "
            "answered. The tracker will resurface it in later turns. "
            "Use when the user says 'I'll figure this out later' or "
            "asks Kora to remind them to decide."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "The decision question as phrased to the user."
                    ),
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional candidate options the user named."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Optional category — one of 'life', 'work', "
                        "'meta', 'task'."
                    ),
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Optional short context sentence for later "
                        "re-surfacing."
                    ),
                },
            },
            "required": ["prompt"],
        },
    },
]


# Tools that require initialized workers to be useful.
_WORKER_DEPENDENT_TOOLS = {"dispatch_worker", "start_autonomous"}


def get_available_tools(
    container: Any | None = None,
    active_skills: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return supervisor tools plus skill-gated registry tools.

    The base set is ``SUPERVISOR_TOOLS`` (recall, dispatch_worker, etc.).
    When workers are not initialized, worker-dependent tools are excluded.

    On top of the base set, tools from the ``ToolRegistry`` are included
    when a ``skill_loader`` is available on the container:
    - If *active_skills* is provided, only registry tools whose name
      appears in the skill loader's active-tool set are included.
    - If *active_skills* is ``None``, all registry tools are included
      for backward compatibility.

    Duplicate names (tools already in SUPERVISOR_TOOLS) are skipped so
    the LLM never sees two definitions for the same tool.
    """
    workers_ready = (
        container is not None
        and getattr(container, "_planner", None) is not None
    )
    if workers_ready:
        tools = list(SUPERVISOR_TOOLS)
    else:
        tools = [t for t in SUPERVISOR_TOOLS if t["name"] not in _WORKER_DEPENDENT_TOOLS]

    # Track names already present so we don't duplicate
    seen_names: set[str] = {t["name"] for t in tools}

    # Merge in ToolRegistry tools, gated by active skills
    skill_loader = getattr(container, "skill_loader", None) if container is not None else None
    if skill_loader is not None and active_skills is not None:
        allowed_names = set(skill_loader.get_active_tools(active_skills))
    else:
        allowed_names = None  # None means "allow all" for backward compat

    try:
        from kora_v2.tools.registry import ToolRegistry

        for tool_def in ToolRegistry.get_all():
            if tool_def.name in seen_names:
                continue
            if allowed_names is not None and tool_def.name not in allowed_names:
                continue
            tools.append(tool_def.to_anthropic_tool())
            seen_names.add(tool_def.name)
    except Exception:  # noqa: BLE001
        # ToolRegistry may not have any tools loaded yet -- that's fine
        log.debug("get_available_tools_registry_skip")

    # Merge in capability-pack actions (workspace, browser, vault, ...).
    # These are always included; the model's skill guidance controls when to
    # invoke them.  Duplicates are skipped to keep the list clean.
    try:
        from kora_v2.graph.capability_bridge import collect_capability_tools

        for cap_tool in collect_capability_tools(container):
            if cap_tool["name"] in seen_names:
                continue
            tools.append(cap_tool)
            seen_names.add(cap_tool["name"])
    except Exception:  # noqa: BLE001
        log.debug("get_available_tools_capability_skip")

    return tools


# =====================================================================
# Execution
# =====================================================================


async def _execute_registry_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    container: Any,
) -> str:
    """Execute a tool registered in ToolRegistry.

    Resolves the callable and input model from the registry,
    instantiates the input, and returns the JSON result string.
    """
    from kora_v2.tools.registry import ToolRegistry

    func = ToolRegistry.get_callable(tool_name)
    if func is None:
        return json.dumps({"status": "error", "message": f"Tool not found in registry: {tool_name}"})

    input_model_cls = ToolRegistry.get_input_model(tool_name)
    if input_model_cls is None:
        return json.dumps({"status": "error", "message": f"No input model for tool: {tool_name}"})

    try:
        input_obj = input_model_cls(**tool_args)
    except Exception as exc:
        return json.dumps({"status": "error", "message": f"Invalid arguments for {tool_name}: {exc}"})

    try:
        result = await func(input_obj, container)
        return result
    except Exception as exc:
        log.error("registry_tool_error", tool=tool_name, error=str(exc))
        return json.dumps({"status": "error", "message": f"Tool execution failed: {exc}"})


async def execute_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    container: Any = None,
    auth_relay: Any = None,
) -> str:
    """Execute a supervisor tool and return the result as a string.

    Phase 3: dispatch_worker delegates to real worker harnesses.
             recall() is real (Phase 2).
    Phase 6: start_autonomous() spawns AutonomousExecutionLoop.

    Args:
        tool_name: Name of the tool to execute.
        tool_args: Arguments passed by the LLM.
        container: Service container with workers and memory.
        auth_relay: AuthRelay instance for tool authorization (optional).

    Returns:
        JSON-encoded result string.
    """
    log.info("execute_tool", tool=tool_name, args=tool_args)

    auth_level, risk_level = _resolve_auth_context(tool_name, tool_args)
    settings = getattr(container, "settings", None) if container is not None else None
    security = getattr(settings, "security", None) if settings is not None else None
    auth_mode = getattr(security, "auth_mode", "prompt")
    session_id = _active_session_id(container)
    approved = await check_tool_auth(
        tool_name=tool_name,
        tool_args=tool_args,
        auth_level=auth_level,
        auth_relay=auth_relay,
        auth_mode=auth_mode,
        session_id=session_id,
        risk_level=risk_level,
    ) if auth_level != AuthLevel.ALWAYS_ALLOWED else True
    if not approved:
        log.info(
            "tool_auth_denied",
            tool=tool_name,
            risk_level=risk_level,
            session_id=session_id,
        )
        return json.dumps({
            "status": "error",
            "error_category": "permission",
            "message": f"Permission denied for tool: {tool_name}",
        })

    if tool_name == "start_autonomous":
        return await _execute_start_autonomous(tool_args, container)

    if tool_name == "dispatch_worker":
        return await _execute_dispatch_worker(tool_args, container)

    if tool_name == "recall":
        from kora_v2.tools.recall import recall

        return await recall(
            query=tool_args.get("query", ""),
            layer=tool_args.get("layer", "all"),
            max_results=tool_args.get("max_results", 10),
            container=container,
        )

    if tool_name == "search_web":
        return await _execute_search_web(tool_args, container)

    if tool_name == "fetch_url":
        return await _execute_fetch_url(tool_args, container)

    # Phase 7.5b: orchestration tools — all route to a single helper so
    # the engine lookup (and its None-handling) is in one place.
    if tool_name in _ORCHESTRATION_TOOL_NAMES:
        return await _execute_orchestration_tool(tool_name, tool_args, container)

    # Capability-pack actions: names contain a dot and start with a known
    # capability prefix (e.g. "workspace.gmail.search", "browser.open").
    if "." in tool_name:
        from kora_v2.graph.capability_bridge import execute_capability_action

        cap_result = await execute_capability_action(tool_name, tool_args, container)
        if cap_result is not None:
            return cap_result

    # Fallback: check ToolRegistry for dynamically registered tools
    from kora_v2.tools.registry import ToolRegistry

    if ToolRegistry.get(tool_name) is not None:
        return await _execute_registry_tool(tool_name, tool_args, container)

    return json.dumps({
        "status": "error",
        "message": f"Unknown tool: {tool_name}",
    })


# =====================================================================
# Worker Dispatch (Phase 3)
# =====================================================================

# Maps worker names to their Pydantic input model classes.
# Lazy-imported to avoid circular imports.
_WORKER_INPUT_MODELS: dict[str, str] = {
    "planner": "PlanInput",
    "executor": "ExecutionInput",
    "reviewer": "ReviewInput",
}

_EXECUTOR_RESERVED_FIELDS = {
    "task",
    "action",
    "operation",
    "params",
    "tools_available",
    "context",
    "constraints",
    "energy_level",
    "estimated_minutes",
}


def _get_input_model(worker_name: str) -> type:
    """Lazy-import the input model for a worker."""
    from kora_v2.core import models

    model_name = _WORKER_INPUT_MODELS.get(worker_name)
    if model_name is None:
        return None
    return getattr(models, model_name, None)


def _coerce_executor_input(raw_input: dict[str, Any]) -> dict[str, Any]:
    """Preserve executor params from supervisor JSON payloads.

    Supervisor generations often emit ``task`` plus direct args such as
    ``path``/``content``. ExecutionInput needs those values under ``params``
    so the executor can perform the side effect deterministically.
    """
    task = (
        raw_input.get("task")
        or raw_input.get("action")
        or raw_input.get("operation")
        or "execute"
    )
    params = dict(raw_input.get("params") or {})
    for key, value in raw_input.items():
        if key not in _EXECUTOR_RESERVED_FIELDS:
            params.setdefault(key, value)

    context = raw_input.get("context", "")
    if not context and isinstance(params.get("user_input"), str):
        context = params["user_input"]

    return {
        "task": task,
        "params": params,
        "tools_available": raw_input.get("tools_available", []),
        "context": context,
        "constraints": raw_input.get("constraints", {}),
        "energy_level": raw_input.get("energy_level"),
        "estimated_minutes": raw_input.get("estimated_minutes"),
    }


async def _execute_dispatch_worker(
    tool_args: dict[str, Any],
    container: Any,
) -> str:
    """Dispatch work to a real worker agent harness.

    1. Resolve worker from container
    2. Parse input_json into worker's TInput model
    3. Execute the worker harness
    4. Return the output as JSON

    Args:
        tool_args: Must contain ``worker_name`` and ``input_json``.
        container: DI container with ``resolve_worker(name)`` method.

    Returns:
        JSON string with worker output or error.
    """
    worker_name = tool_args.get("worker_name", "unknown")
    input_json = tool_args.get("input_json", "{}")

    log.info("dispatch_worker", worker=worker_name)
    start_time = time.monotonic()

    try:
        # 1. Resolve the worker harness
        worker = container.resolve_worker(worker_name)

        # 2. Parse input JSON into the worker's input model
        input_model_cls = _get_input_model(worker_name)
        if input_model_cls is not None:
            raw_input = json.loads(input_json)
            if worker_name == "executor" and isinstance(raw_input, dict):
                raw_input = _coerce_executor_input(raw_input)
            input_data = input_model_cls.model_validate(raw_input)
        else:
            # For workers without typed input (e.g., future on-demand agents),
            # pass raw JSON dict
            input_data = json.loads(input_json)

        # 3. Execute through the harness (middleware + quality gates)
        output = await worker.execute(input_data)

        # 4. Serialize output
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        log.info(
            "dispatch_worker_complete",
            worker=worker_name,
            elapsed_ms=elapsed_ms,
        )

        if hasattr(output, "model_dump_json"):
            return output.model_dump_json()
        return json.dumps({"result": str(output)})

    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        log.error(
            "dispatch_worker_error",
            worker=worker_name,
            error=str(exc),
            elapsed_ms=elapsed_ms,
        )
        return json.dumps({
            "status": "error",
            "worker": worker_name,
            "error": str(exc),
        })


# =====================================================================
# Autonomous Task Dispatch (Phase 6)
# =====================================================================


async def _execute_start_autonomous(
    tool_args: dict[str, Any],
    container: Any,
) -> str:
    """Start a multi-step autonomous background task.

    Creates an AutonomousExecutionLoop, spawns it as an asyncio Task,
    and stores it on the container so the server can track it.

    Returns immediately with a confirmation message.

    Args:
        tool_args: Must contain ``goal``, optionally ``context``.
        container: DI container; must expose ``settings.data_dir``.

    Returns:
        JSON-encoded confirmation with session tracking info.
    """
    from pathlib import Path

    from kora_v2.autonomous.loop import AutonomousExecutionLoop

    goal = tool_args.get("goal", "").strip()
    context = tool_args.get("context", "")
    if not goal:
        return json.dumps({"status": "error", "error": "goal is required"})

    # Determine session ID from the active session manager
    session_id = _active_session_id(container) or f"auto_{time.monotonic_ns()}"

    # Determine DB path
    settings = getattr(container, "settings", None)
    data_dir = getattr(settings, "data_dir", None) or Path("data")
    db_path = Path(data_dir) / "operational.db"

    # Check if autonomous is enabled
    auto_settings = getattr(settings, "autonomous", None)
    if auto_settings is not None and not getattr(auto_settings, "enabled", True):
        return json.dumps({
            "status": "error",
            "error": "Autonomous execution is disabled in settings.",
        })

    # Inject context into goal
    full_goal = goal
    if context:
        full_goal = f"{goal}\n\nAdditional context: {context}"

    # Create loop
    loop = AutonomousExecutionLoop(
        goal=full_goal,
        session_id=session_id,
        container=container,
        db_path=db_path,
        checkpoint_interval_minutes=getattr(
            auto_settings, "checkpoint_interval_minutes", 30
        ),
        auto_continue_seconds=getattr(auto_settings, "auto_continue_seconds", 30),
    )

    # Track active loops on container
    if not hasattr(container, "_autonomous_loops"):
        container._autonomous_loops = {}

    # Guard against leaking the previous task when called twice in the same session.
    existing = container._autonomous_loops.get(session_id)
    if existing:
        existing_task = existing.get("task")
        existing_loop = existing.get("loop")
        if existing_task and not existing_task.done():
            # Actually running — tell user
            return json.dumps({
                "status": "already_running",
                "message": (
                    "An autonomous task is already running for this session. "
                    "It will checkpoint at the next safe boundary."
                ),
                "goal": existing.get("goal", ""),
            })
        # Completed/failed/cancelled — clean up any 'planned' orphan plans in the DB
        if existing_loop and existing_loop.state:
            plan_id = existing_loop.state.plan_id
            if plan_id:
                try:
                    import aiosqlite as _aiosqlite

                    async with _aiosqlite.connect(str(db_path)) as _db:
                        await _db.execute(
                            "UPDATE autonomous_plans SET status='superseded' "
                            "WHERE id=? AND status='planned'",
                            (plan_id,),
                        )
                        await _db.commit()
                except Exception:
                    pass
        # Allow new run — fall through

    # Spawn background task
    task = asyncio.create_task(loop.run(), name=f"autonomous_{session_id}")
    container._autonomous_loops[session_id] = {
        "loop": loop,
        "task": task,
        "goal": goal,
        "started_at": time.monotonic(),
    }

    log.info(
        "start_autonomous_dispatched",
        session_id=session_id,
        goal=goal[:80],
    )

    return json.dumps({
        "status": "started",
        "session_id": session_id,
        "goal": goal,
        "message": (
            "I'll work on this in the background. "
            "I'll check in at each checkpoint and let you know how it's going. "
            "You can keep chatting while I work."
        ),
    })


# =====================================================================
# Web Tools — search_web + fetch_url (Workstream 2)
# =====================================================================


def _mcp_manager(container: Any | None) -> Any | None:
    if container is None:
        return None
    return getattr(container, "mcp_manager", None)


def _search_web_mcp_unavailable(query: str, reason: str = "brave_search server unavailable") -> str:
    """Return an explicit structured failure when the MCP web-search path is unavailable.

    This replaces the old silent DuckDuckGo fallback. The model is instructed
    (via the supervisor prompt) to handle this and can naturally choose
    ``browser.open`` as an alternative for read access.
    """
    log.info("search_web_mcp_unavailable", query=query[:80], reason=reason)
    return json.dumps({
        "results": [],
        "query": query,
        "error": f"MCP web-search path failed: {reason}",
        "failed_path": "mcp.brave_search.brave_web_search",
        "degraded": True,
        "recoverable": True,
        "next_options": ["browser.open"],
    })


async def _execute_search_web(
    tool_args: dict[str, Any],
    container: Any | None,
) -> str:
    """Run a web search via brave_search MCP when configured.

    When the MCP path is unavailable or fails, returns an explicit structured
    failure dict instead of silently falling back to DuckDuckGo.  The model
    reads the ``next_options`` field and can choose ``browser.open`` for read
    continuity after acknowledging the failure.
    """
    query = str(tool_args.get("query", "")).strip()
    count = int(tool_args.get("count", 5) or 5)
    count = max(1, min(count, 10))

    if not query:
        return json.dumps({"results": [], "error": "query is required"})

    mcp = _mcp_manager(container)
    if mcp is None:
        log.info("search_web_no_mcp_manager", query=query[:80])
        return _search_web_mcp_unavailable(query, "no MCP manager configured")

    # Check whether brave_search is in the configured server set.
    try:
        server_info = mcp.get_server_info("brave_search")
    except Exception:  # noqa: BLE001
        server_info = None
    if server_info is None:
        log.info("search_web_no_brave_search_server", query=query[:80])
        return _search_web_mcp_unavailable(query, "brave_search server unavailable")

    try:
        mcp_result = await mcp.call_tool(
            "brave_search",
            "brave_web_search",
            {"query": query, "count": count},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("search_web_mcp_failed", error=str(exc), query=query[:80])
        return _search_web_mcp_unavailable(query, f"brave_search call failed: {exc}")

    # Prefer structured_data if the server returned a native JSON block;
    # otherwise fall back to the joined text (which brave returns as JSON-in-text).
    raw: Any = mcp_result.structured_data or mcp_result.text
    results = _parse_brave_results(raw, count)
    return json.dumps({"results": results, "query": query})


def _parse_brave_results(raw: Any, count: int) -> list[dict[str, str]]:
    """Extract ``[{title, url, description}]`` from a brave_search result."""
    # raw may be a JSON string or a dict already.
    data: Any = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Return raw snippet in that case.
            return [{"title": "", "url": "", "description": raw[:500]}]

    if not isinstance(data, dict):
        return []

    web = data.get("web") or {}
    items = web.get("results") if isinstance(web, dict) else None
    if not isinstance(items, list):
        # Sometimes the shape is {"results": [...]} directly.
        items = data.get("results") if isinstance(data.get("results"), list) else []

    parsed: list[dict[str, str]] = []
    for entry in items[:count]:
        if not isinstance(entry, dict):
            continue
        parsed.append(
            {
                "title": str(entry.get("title", "")),
                "url": str(entry.get("url", "")),
                "description": str(
                    entry.get("description", entry.get("snippet", ""))
                ),
            }
        )
    return parsed


async def _execute_fetch_url(
    tool_args: dict[str, Any],
    container: Any | None,
) -> str:
    """Fetch a URL via the fetch MCP server.

    When the MCP fetch server is unavailable or the call fails, returns an
    explicit structured failure dict instead of silently falling back to
    urllib.  The model is told to acknowledge the failure and can choose
    ``browser.open`` as an alternative.
    """
    url = str(tool_args.get("url", "")).strip()
    max_chars = int(tool_args.get("max_chars", 8000) or 8000)
    max_chars = max(256, min(max_chars, 200_000))

    if not url:
        return json.dumps({"error": "url is required", "url": "", "content": "", "chars": 0})

    mcp = _mcp_manager(container)
    if mcp is None:
        log.info("fetch_url_no_mcp_manager", url=url[:120])
        return json.dumps({
            "url": url,
            "content": "",
            "chars": 0,
            "error": "MCP fetch path failed: no MCP manager configured",
            "failed_path": "mcp.fetch.fetch",
            "degraded": True,
            "recoverable": True,
            "next_options": ["browser.open"],
        })

    try:
        fetch_info = mcp.get_server_info("fetch")
    except Exception:  # noqa: BLE001
        fetch_info = None

    if fetch_info is None:
        log.info("fetch_url_no_fetch_server", url=url[:120])
        return json.dumps({
            "url": url,
            "content": "",
            "chars": 0,
            "error": "MCP fetch path failed: fetch server unavailable",
            "failed_path": "mcp.fetch.fetch",
            "degraded": True,
            "recoverable": True,
            "next_options": ["browser.open"],
        })

    try:
        fetch_result = await mcp.call_tool("fetch", "fetch", {"url": url})
        text = fetch_result.text
        truncated = text[:max_chars]
        return json.dumps({
            "url": url,
            "content": truncated,
            "chars": len(truncated),
            "source": "mcp",
        })
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch_url_mcp_failed", error=str(exc), url=url[:120])
        return json.dumps({
            "url": url,
            "content": "",
            "chars": 0,
            "error": f"MCP fetch path failed: {exc}",
            "failed_path": "mcp.fetch.fetch",
            "degraded": True,
            "recoverable": True,
            "next_options": ["browser.open"],
        })


# =====================================================================
# Phase 7.5b orchestration tools
# =====================================================================


_ORCHESTRATION_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "decompose_and_dispatch",
        "get_running_tasks",
        "get_task_progress",
        "get_working_doc",
        "cancel_task",
        "modify_task",
        "record_decision",
    }
)


def _orchestration_engine(container: Any | None) -> Any | None:
    """Return the :class:`OrchestrationEngine` from *container* or None."""
    if container is None:
        return None
    return getattr(container, "orchestration_engine", None)


async def _execute_orchestration_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    container: Any | None,
) -> str:
    """Route a Phase 7.5b orchestration tool call to the engine."""
    engine = _orchestration_engine(container)
    if engine is None:
        return json.dumps(
            {
                "status": "error",
                "error_category": "configuration",
                "message": (
                    "Orchestration engine not available — cannot run "
                    f"{tool_name} without container.orchestration_engine."
                ),
            }
        )

    session_id = _active_session_id(container)

    try:
        if tool_name == "decompose_and_dispatch":
            return await _orch_decompose_and_dispatch(
                engine, tool_args, session_id=session_id
            )
        if tool_name == "get_running_tasks":
            return await _orch_get_running_tasks(
                engine, tool_args, session_id=session_id
            )
        if tool_name == "get_task_progress":
            return await _orch_get_task_progress(engine, tool_args)
        if tool_name == "get_working_doc":
            return await _orch_get_working_doc(engine, tool_args)
        if tool_name == "cancel_task":
            return await _orch_cancel_task(engine, tool_args)
        if tool_name == "modify_task":
            return await _orch_modify_task(engine, tool_args)
        if tool_name == "record_decision":
            return await _orch_record_decision(
                engine, tool_args, session_id=session_id
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "orchestration_tool_failed",
            tool=tool_name,
            error=str(exc),
        )
        return json.dumps(
            {
                "status": "error",
                "error_category": "runtime",
                "message": f"{tool_name} failed: {exc}",
            }
        )

    return json.dumps(
        {"status": "error", "message": f"Unknown orchestration tool: {tool_name}"}
    )


async def _orch_decompose_and_dispatch(
    engine: Any,
    tool_args: dict[str, Any],
    *,
    session_id: str | None,
) -> str:
    from kora_v2.runtime.orchestration.pipeline import (
        FailurePolicy,
        InterruptionPolicy,
        Pipeline,
        PipelineStage,
    )

    goal = str(tool_args.get("goal", "")).strip()
    pipeline_name = str(tool_args.get("pipeline_name", "")).strip()
    stages_in = tool_args.get("stages") or []
    in_turn = bool(tool_args.get("in_turn", False))
    if not goal or not pipeline_name or not stages_in:
        return json.dumps(
            {
                "status": "error",
                "message": "goal, pipeline_name, and stages are required",
            }
        )

    preset = "in_turn" if in_turn else "bounded_background"
    stages: list[PipelineStage] = []
    prev_name: str | None = None
    for stage_name in stages_in:
        name = str(stage_name).strip()
        if not name:
            continue
        stages.append(
            PipelineStage(
                name=name,
                task_preset=preset,  # type: ignore[arg-type]
                goal_template=f"{goal} — {name}",
                depends_on=[prev_name] if prev_name else [],
            )
        )
        prev_name = name

    pipeline = Pipeline(
        name=pipeline_name,
        description=f"Runtime pipeline for: {goal}",
        stages=stages,
        triggers=[],
        interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
        failure_policy=FailurePolicy.FAIL_PIPELINE,
        intent_duration="short" if in_turn else "indefinite",
    )

    await engine.register_runtime_pipeline(
        pipeline, created_by_session=session_id
    )

    # Compute the doc path deterministically from the pipeline so we
    # can thread it into both the instance row and the working doc
    # create call — the store's `doc_path` helper uses the same
    # algorithm the create helper does internally.
    working_doc_path_obj = engine.working_docs.doc_path(
        pipeline_name=pipeline_name,
        instance_id=pipeline_name,
        goal=goal,
    )
    instance = await engine.start_pipeline_instance(
        pipeline_name,
        goal=goal,
        working_doc_path=str(working_doc_path_obj),
        parent_session_id=session_id,
    )

    # Now seed the working doc on disk so the user can peek at it
    # before the first stage runs. Best-effort — a missing doc does
    # not block dispatch.
    try:
        await engine.working_docs.create(
            instance_id=instance.id,
            task_id=instance.id,
            pipeline_name=pipeline_name,
            goal=goal,
            parent_session_id=session_id,
            seed_plan_items=[s.name for s in stages],
        )
    except Exception:  # noqa: BLE001
        log.debug("working_doc_create_failed", exc_info=True)

    return json.dumps(
        {
            "status": "ok",
            "pipeline_instance_id": instance.id,
            "pipeline_name": pipeline_name,
            "working_doc_path": str(working_doc_path_obj),
            "stage_count": len(stages),
        }
    )


async def _orch_get_running_tasks(
    engine: Any,
    tool_args: dict[str, Any],
    *,
    session_id: str | None,
) -> str:
    relevant = bool(tool_args.get("relevant_to_session", False))
    user_message = tool_args.get("user_message")
    tasks = await engine.list_tasks(
        relevant_to_session=session_id if relevant else None,
        user_message=user_message,
    )
    out = [
        {
            "task_id": getattr(t, "id", None),
            "stage_name": getattr(t, "stage_name", None),
            "state": getattr(getattr(t, "state", None), "value", None)
            or str(getattr(t, "state", "")),
            "goal": getattr(t, "goal", None),
            "pipeline_instance_id": getattr(t, "pipeline_instance_id", None),
        }
        for t in tasks
    ]
    return json.dumps({"status": "ok", "tasks": out, "count": len(out)})


async def _orch_get_task_progress(
    engine: Any,
    tool_args: dict[str, Any],
) -> str:
    task_id = str(tool_args.get("task_id", "")).strip()
    if not task_id:
        return json.dumps({"status": "error", "message": "task_id is required"})
    progress = await engine.get_task_progress(task_id)
    if progress is None:
        return json.dumps({"status": "error", "message": f"task {task_id} not found"})
    return json.dumps({"status": "ok", "progress": progress})


async def _orch_get_working_doc(
    engine: Any,
    tool_args: dict[str, Any],
) -> str:
    """Read a working doc by task id or pipeline instance id.

    The engine exposes ``get_working_doc(task_id)`` returning a
    :class:`WorkingDocHandle`. Callers may pass either a task id or a
    pipeline instance id; we try task id first and fall back to a
    direct instance lookup so the tool surface is forgiving.
    """
    from pathlib import Path as _Path

    task_id = str(
        tool_args.get("task_id") or tool_args.get("pipeline_instance_id") or ""
    ).strip()
    if not task_id:
        return json.dumps(
            {"status": "error", "message": "task_id or pipeline_instance_id is required"}
        )
    section = tool_args.get("section")
    handle = await engine.get_working_doc(task_id)
    if handle is None:
        try:
            instance = await engine.instance_registry.load(task_id)
        except Exception:  # noqa: BLE001
            instance = None
        if instance is not None:
            doc_path = _Path(instance.working_doc_path)
            if not doc_path.is_absolute():
                doc_path = engine._memory_root / doc_path
            handle = await engine.working_docs.read(doc_path)
    if handle is None:
        return json.dumps(
            {"status": "error", "message": f"no working doc for {task_id}"}
        )
    if section:
        body = handle.section(section)
    else:
        # Rebuild a plain markdown body from the sections dict so the
        # LLM sees the full doc — cheap enough for a single response.
        body_parts: list[str] = []
        for name, content in handle.sections.items():
            body_parts.append(f"# {name}")
            if content.strip():
                body_parts.append(content.rstrip())
            body_parts.append("")
        body = "\n".join(body_parts).rstrip()
    return json.dumps(
        {
            "status": "ok",
            "path": str(handle.path),
            "doc_status": handle.status,
            "content": body,
        }
    )


async def _orch_cancel_task(
    engine: Any,
    tool_args: dict[str, Any],
) -> str:
    task_id = str(tool_args.get("task_id", "")).strip()
    reason = str(tool_args.get("reason", "user_requested")).strip() or "user_requested"
    if not task_id:
        return json.dumps({"status": "error", "message": "task_id is required"})
    ok = await engine.cancel_task(task_id, reason=reason)
    return json.dumps({"status": "ok" if ok else "error", "task_id": task_id})


async def _orch_modify_task(
    engine: Any,
    tool_args: dict[str, Any],
) -> str:
    task_id = str(tool_args.get("task_id", "")).strip()
    if not task_id:
        return json.dumps({"status": "error", "message": "task_id is required"})
    task = await engine.modify_task(
        task_id,
        goal=tool_args.get("new_goal"),
        system_prompt=tool_args.get("new_system_prompt"),
    )
    if task is None:
        return json.dumps(
            {"status": "error", "message": f"task {task_id} not found"}
        )
    return json.dumps(
        {
            "status": "ok",
            "task_id": task_id,
            "goal": task.goal,
        }
    )


async def _orch_record_decision(
    engine: Any,
    tool_args: dict[str, Any],
    *,
    session_id: str | None,
) -> str:
    prompt = str(tool_args.get("prompt", "")).strip()
    if not prompt:
        return json.dumps({"status": "error", "message": "prompt is required"})
    context_parts: list[str] = []
    if tool_args.get("context"):
        context_parts.append(str(tool_args["context"]))
    options_raw = tool_args.get("options") or []
    options = [str(o).strip() for o in options_raw if str(o).strip()]
    if options:
        context_parts.append("Options: " + "; ".join(options))
    if tool_args.get("category"):
        context_parts.append(f"Category: {tool_args['category']}")
    context = "\n".join(context_parts)

    decision = await engine.record_open_decision(
        topic=prompt,
        context=context,
        posed_in_session=session_id,
    )
    return json.dumps(
        {
            "status": "ok",
            "decision_id": getattr(decision, "id", None),
            "topic": getattr(decision, "topic", None),
        }
    )

