"""Supervisor dispatch tools.

Defines the tool schemas (Anthropic format) that the supervisor LLM
can call, and an ``execute_tool`` function that routes to real
implementations.

Phase 4.67: dispatch_worker is real (delegates to worker harnesses).
            recall is real (hybrid memory search).
Phase 6:    autonomous dispatch originally lived here as the
            ``start_autonomous`` tool. Phase 7.5c retired that tool —
            autonomous goals now flow through
            ``decompose_and_dispatch(pipeline_name="user_autonomous_task")``
            and the orchestration engine. See spec §17.7c.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from kora_v2.tools.types import AuthLevel

log = structlog.get_logger(__name__)

_TOOL_RISK_LEVELS: dict[str, str] = {
    "recall": "low",
    "search_web": "low",
    "fetch_url": "low",
}

_PROTECTED_SYSTEM_PIPELINES: set[str] = {
    "post_session_memory",
    "post_memory_vault",
    "session_bridge_pruning",
    "skill_refinement",
    "weekly_adhd_profile",
    "wake_up_preparation",
    "continuity_check",
    "contextual_engagement",
    "proactive_pattern_scan",
    "anticipatory_prep",
    "commitment_tracking",
    "stuck_detection",
    "connection_making",
}

_PROTECTED_PIPELINE_ALIASES: dict[str, tuple[str, ...]] = {
    "post_session_memory": (
        "post session memory",
        "post_session_memory",
        "memory steward",
        "memory chain",
        "memory pipeline",
    ),
    "post_memory_vault": (
        "post memory vault",
        "post_memory_vault",
        "vault organizer",
        "vault pipeline",
    ),
    "session_bridge_pruning": (
        "session bridge pruning",
        "session_bridge_pruning",
    ),
    "skill_refinement": ("skill refinement", "skill_refinement"),
    "weekly_adhd_profile": ("weekly adhd profile", "weekly_adhd_profile"),
    "wake_up_preparation": ("wake up preparation", "wake_up_preparation"),
    "continuity_check": ("continuity check", "continuity_check"),
    "contextual_engagement": ("contextual engagement", "contextual_engagement"),
    "proactive_pattern_scan": (
        "proactive pattern scan",
        "proactive_pattern_scan",
    ),
    "anticipatory_prep": ("anticipatory prep", "anticipatory_prep"),
    "commitment_tracking": ("commitment tracking", "commitment_tracking"),
    "stuck_detection": ("stuck detection", "stuck_detection"),
    "connection_making": ("connection making", "connection_making"),
}

_CANCEL_CONTROL_WORDS = {
    "actually",
    "background",
    "cancel",
    "cancelled",
    "canceling",
    "cancelling",
    "don't",
    "extra",
    "keep",
    "pause",
    "paused",
    "right",
    "running",
    "stop",
    "stopped",
    "task",
    "tasks",
    "there",
    "waste",
    "work",
}


def _cancel_target_words(text: str) -> list[str]:
    normalized = text.lower().replace("_", " ").replace("-", " ")
    words: list[str] = []
    for raw in normalized.split():
        word = raw.strip(".,;:!?()[]{}\"'")
        if len(word) < 5 or word in _CANCEL_CONTROL_WORDS:
            continue
        words.append(word)
    return words


def _reason_preserves_research(text: str) -> bool:
    lowered = text.lower()
    return (
        "research" in lowered
        and any(
            phrase in lowered
            for phrase in (
                "keep",
                "preserve",
                "do not cancel",
                "don't cancel",
                "dont cancel",
                "do not disturb",
                "don't disturb",
                "dont disturb",
                "do not touch",
                "don't touch",
                "dont touch",
                "leave",
            )
        )
    )


def _reason_explicitly_cancels_research(text: str) -> bool:
    lowered = text.lower().replace("_", " ")
    if not re.search(r"\b(?:cancel|stop|pause|kill)\b", lowered):
        return False
    positive = re.split(
        r"\b(?:do not|don't|dont|keep|preserve|leave)\b",
        lowered,
        maxsplit=1,
    )[0]
    return "research" in positive or "proactive research" in positive


def _target_words_from_cancel_request(text: str) -> list[str]:
    lowered = text.lower()
    if not re.search(r"\b(?:cancel|stop|pause|kill)\b(?![-_])", lowered):
        return []
    first_sentence = re.split(r"[.;\n]", lowered, maxsplit=1)[0]
    only_match = re.search(
        r"\b(?:cancel|stop|pause|kill)\s+only\s+(.+?)(?:\s+right\s+now|\s+now|$)",
        first_sentence,
    )
    if only_match:
        return _cancel_target_words(only_match.group(1))
    positive = re.split(
        r"\b(?:do not|don't|dont|keep|preserve)\b",
        lowered,
        maxsplit=1,
    )[0]
    return _cancel_target_words(positive)


def _explicitly_mentions_protected_pipeline(reason: str, pipeline_name: str) -> bool:
    """Return True only when the user names a protected system pipeline."""
    lowered = reason.lower()
    aliases = _PROTECTED_PIPELINE_ALIASES.get(
        pipeline_name,
        (pipeline_name, pipeline_name.replace("_", " ")),
    )
    return any(alias.lower() in lowered for alias in aliases)


def _is_protected_system_pipeline_name(pipeline_name: str) -> bool:
    """Return True for internal pipelines users should not cancel casually."""
    normalized = str(pipeline_name or "").strip()
    return normalized in _PROTECTED_SYSTEM_PIPELINES or normalized.startswith(
        "routine_"
    )


def _reason_targets_cancel_probe(text: str) -> bool:
    lowered = text.lower().replace("_", "-")
    normalized = lowered.replace("-", " ")
    return "cancel-probe" in lowered or "cancel probe" in normalized


def _haystack_matches_cancel_probe(*values: Any) -> bool:
    haystack = " ".join(str(value or "") for value in values).lower()
    return _reason_targets_cancel_probe(haystack)


# =====================================================================
# Permission Grant Recording
# =====================================================================


async def _record_permission_grant(
    tool_name: str,
    auth_level: AuthLevel,
    decision: str,
    risk_level: str,
    session_id: str | None,
    container: Any,
) -> None:
    """Write permission_grants for auth audit trail."""
    try:
        settings = getattr(container, "settings", None)
        if settings is None:
            return
        db_path = getattr(settings, "data_dir", None)
        if db_path is None:
            return
        op_db = db_path / "operational.db"
        import aiosqlite

        async with aiosqlite.connect(str(op_db)) as db:
            await db.execute(
                "INSERT INTO permission_grants "
                "(id, tool_name, scope, risk_level, decision, granted_at, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex[:12],
                    tool_name,
                    auth_level.value,
                    risk_level,
                    decision,
                    datetime.now(UTC).isoformat(),
                    session_id,
                ),
            )
            await db.commit()
    except Exception:
        log.debug("permission_grant_write_failed", tool=tool_name)


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

    if "." in tool_name:
        try:
            from kora_v2.graph.capability_bridge import collect_capability_tools

            for tool in collect_capability_tools():
                if tool.get("name") != tool_name:
                    continue
                read_only = bool(tool.get("_read_only", True))
                requires_approval = bool(
                    tool.get("_requires_approval", False)
                )
                risk = "low" if read_only else "high"
                return (
                    AuthLevel.ASK_FIRST
                    if requires_approval
                    else AuthLevel.ALWAYS_ALLOWED,
                    risk,
                )
        except Exception:  # noqa: BLE001
            log.debug(
                "capability_auth_context_lookup_failed",
                tool=tool_name,
                exc_info=True,
            )

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
            "pipeline instance id and working-doc path. For open-ended "
            "background/rabbit-hole research, use "
            "pipeline_name='proactive_research' even when the topic is "
            "privacy, local-first tooling, or another specific domain."
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
                        "snake_case and unique-ish. For background research "
                        "requests, prefer the registered core pipeline name "
                        "'proactive_research' instead of inventing topic "
                        "names like 'privacy_research'."
                    ),
                },
                "stages": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "tool_scope": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "Optional tool allow-list for this "
                                            "stage. Must not include any "
                                            "ASK_FIRST tool or "
                                            "decompose_and_dispatch (sub-tasks "
                                            "cannot recurse)."
                                        ),
                                    },
                                    "depends_on": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "Optional list of earlier stage "
                                            "names this one depends on. Default "
                                            "is the previous stage in order."
                                        ),
                                    },
                                },
                                "required": ["name"],
                            },
                        ],
                    },
                    "description": (
                        "Ordered stage names (or stage objects with optional "
                        "tool_scope/depends_on). Each stage becomes a "
                        "PipelineStage with a default goal template."
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
                "intent_duration": {
                    "type": "string",
                    "enum": ["short", "indefinite", "long"],
                    "description": (
                        "How long the pipeline is expected to run. "
                        "'short' — completes within minutes (default for in_turn). "
                        "'long' — overnight / multi-hour background work "
                        "(default for non-in_turn dispatches). "
                        "'indefinite' — system pipelines without a clear end."
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
            "Use when the user says 'I'll figure this out later', asks "
            "Kora to remind them to decide, says they are weighing two "
            "options, or leaves a decision/open question unresolved."
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
_WORKER_DEPENDENT_TOOLS = {"dispatch_worker"}


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
    # Capability names are skill-gated too; otherwise every turn exposes
    # personal-account/browser/vault actions even when the active skill set
    # has nothing to do with them.
    try:
        from kora_v2.graph.capability_bridge import collect_capability_tools

        for cap_tool in collect_capability_tools(container):
            if cap_tool["name"] in seen_names:
                continue
            if allowed_names is not None and cap_tool["name"] not in allowed_names:
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
    Phase 7.5c: start_autonomous was retired — autonomous goals now
                flow through decompose_and_dispatch + the orchestration
                engine's ``user_autonomous_task`` pipeline. See spec §17.7c.

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

    # Persist the auth decision to permission_grants for audit.
    if auth_level != AuthLevel.ALWAYS_ALLOWED:
        await _record_permission_grant(
            tool_name=tool_name,
            auth_level=auth_level,
            decision="approved" if approved else "denied",
            risk_level=risk_level,
            session_id=session_id,
            container=container,
        )

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

        cap_args = dict(tool_args)
        if auth_level != AuthLevel.ALWAYS_ALLOWED:
            cap_args.setdefault("approved", approved)
        cap_result = await execute_capability_action(tool_name, cap_args, container)
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
    from kora_v2.runtime.orchestration.scope_validation import (
        ScopeValidationError,
        SubTaskSpec,
        validate_subtask_specs,
    )

    goal = str(tool_args.get("goal", "")).strip()
    pipeline_name = str(tool_args.get("pipeline_name", "")).strip()
    stages_in = tool_args.get("stages") or []
    in_turn = bool(tool_args.get("in_turn", False))
    intent_duration = str(tool_args.get("intent_duration") or "").strip()
    if intent_duration not in {"short", "indefinite", "long"}:
        # Default: in_turn → "short", non-in_turn background → "long"
        # so the supervisor contract (item 46) lines up: background
        # dispatches are long-lived by default and persist that fact.
        intent_duration = "short" if in_turn else "long"
    if not goal or not pipeline_name or not stages_in:
        return json.dumps(
            {
                "status": "error",
                "message": "goal, pipeline_name, and stages are required",
            }
        )

    preset = "in_turn" if in_turn else "bounded_background"

    # Normalise the stage list — entries may be plain strings (legacy
    # shape) or {name, tool_scope, depends_on} objects (Phase 8f).
    normalized: list[dict[str, Any]] = []
    prev_name: str | None = None
    for entry in stages_in:
        if isinstance(entry, str):
            name = entry.strip()
            tool_scope: list[str] = []
            depends_on = [prev_name] if prev_name else []
        elif isinstance(entry, dict):
            name = str(entry.get("name", "")).strip()
            raw_scope = entry.get("tool_scope") or []
            tool_scope = [
                str(t).strip() for t in raw_scope if str(t).strip()
            ]
            raw_deps = entry.get("depends_on")
            if raw_deps is None:
                depends_on = [prev_name] if prev_name else []
            else:
                depends_on = [
                    str(d).strip() for d in raw_deps if str(d).strip()
                ]
        else:
            continue
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "tool_scope": tool_scope,
                "depends_on": depends_on,
            }
        )
        prev_name = name

    # Phase 8f spec §4a — reject ASK_FIRST tools, recursive
    # decompose_and_dispatch, and cycles before the pipeline ever
    # reaches the registry. The supervisor LLM sees a structured
    # rejection it can recover from instead of a generic exception.
    sub_specs = [
        SubTaskSpec(
            task_id=item["name"],
            description=f"{goal} — {item['name']}",
            required_tools=item["tool_scope"],
            depends_on=item["depends_on"],
        )
        for item in normalized
    ]
    try:
        validate_subtask_specs(sub_specs)
    except ScopeValidationError as exc:
        log.info(
            "decompose_and_dispatch_rejected",
            reason=exc.reason,
            field=exc.offending_field,
        )
        return json.dumps(
            {
                "status": "error",
                "error_category": "validation",
                "rejection_reason": exc.reason,
                "offending_field": exc.offending_field,
                "message": exc.message,
            }
        )

    if _is_cancel_probe_request(pipeline_name, goal):
        pipeline_name = "cancel_probe"

    if _routes_to_registered_proactive_research(
        pipeline_name=pipeline_name,
        goal=goal,
        intent_duration=intent_duration,
        stages=normalized,
    ):
        from kora_v2.runtime.orchestration.core_pipelines import (
            register_core_pipelines,
        )

        registered_name = "proactive_research"
        if registered_name not in engine.pipelines:
            register_core_pipelines(engine)

        instance = await engine.start_triggered_pipeline(
            registered_name,
            goal=goal,
            parent_session_id=session_id,
            trigger_id="decompose_and_dispatch",
        )
        pipeline = engine.pipelines.get(registered_name)
        return json.dumps(
            {
                "status": "ok",
                "pipeline_instance_id": instance.id,
                "pipeline_name": instance.pipeline_name,
                "working_doc_path": instance.working_doc_path,
                "stage_count": len(pipeline.stages),
                "routing": "registered_pipeline",
                "requested_pipeline_name": pipeline_name,
            }
        )

    stages: list[PipelineStage] = []
    for item in normalized:
        stages.append(
            PipelineStage(
                name=item["name"],
                task_preset=preset,  # type: ignore[arg-type]
                goal_template=f"{goal} — {item['name']}",
                depends_on=list(item["depends_on"]),
                tool_scope=list(item["tool_scope"]),
            )
        )

    pipeline = Pipeline(
        name=pipeline_name,
        description=f"Runtime pipeline for: {goal}",
        stages=stages,
        triggers=[],
        interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
        failure_policy=FailurePolicy.FAIL_PIPELINE,
        intent_duration=intent_duration,
    )

    await engine.register_runtime_pipeline(
        pipeline, created_by_session=session_id
    )

    # Create the pipeline instance first so we know its real id, then
    # compute the working-doc path from that id. Previously this was
    # computed with ``instance_id=pipeline_name`` before the instance
    # existed, which baked a stale path into ``pipeline_instances`` and
    # the tool return value — the file actually written to disk used
    # the real instance id, so downstream ``get_working_doc`` calls
    # pointed at a non-existent file.
    instance = await engine.start_pipeline_instance(
        pipeline_name,
        goal=goal,
        working_doc_path="",
        parent_session_id=session_id,
    )
    working_doc_path_obj = engine.working_docs.doc_path(
        pipeline_name=pipeline_name,
        instance_id=instance.id,
        goal=goal,
    )
    instance.working_doc_path = str(working_doc_path_obj)
    await engine.instance_registry.save(instance)

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

    seeded_task_count = 0

    # Seed in-turn runtime pipelines with one runnable task per stage. The
    # previous path registered the PipelineInstance/working doc but left the
    # dispatcher with no WorkerTasks, so item 8 could only prove that Kora
    # acknowledged the request, not that it actually delegated anything.
    if in_turn:
        from kora_v2.runtime.orchestration.worker_task import StepResult

        async def _in_turn_stage_step(task: Any, ctx: Any) -> StepResult:
            return StepResult(
                outcome="complete",
                result_summary=f"completed:{task.stage_name or 'stage'}",
            )

        for stage in stages:
            await engine.dispatch_task(
                goal=stage.goal_template,
                system_prompt=pipeline.description,
                step_fn=_in_turn_stage_step,
                preset="in_turn",
                stage_name=stage.name,
                pipeline_instance_id=instance.id,
                depends_on=list(stage.depends_on),
                tool_scope=list(stage.tool_scope) or None,
            )
            seeded_task_count += 1

    # Seed the dispatcher with a real worker task for background
    # autonomous work. Without this, the pipeline instance and working
    # doc exist on disk but the scheduler has nothing to tick, so the
    # task never progresses beyond ``pipeline_started``.
    if not in_turn:
        try:
            from kora_v2.runtime.orchestration.worker_task import StepResult

            combined_scope: list[str] = []
            for item in normalized:
                for tool_name in item["tool_scope"]:
                    if tool_name not in combined_scope:
                        combined_scope.append(tool_name)

            stage_names = [item["name"] for item in normalized]
            stage_hint = ", ".join(stage_names)
            system_prompt = (
                "Use the working doc as the source of truth. Work through "
                f"the requested stages in order: {stage_hint}. Keep the "
                "Current Plan updated, checkpoint meaningful findings, and "
                "only claim completion when the working doc reflects real "
                "progress or an honest blocked state."
            )

            async def _practical_life_admin_step(
                task: Any, ctx: Any
            ) -> StepResult:
                if getattr(task, "pipeline_instance_id", None):
                    try:
                        instance = await engine.instance_registry.load(
                            task.pipeline_instance_id
                        )
                        if instance is not None and instance.working_doc_path:
                            from pathlib import Path as _Path

                            from kora_v2.runtime.orchestration.working_doc import (
                                WorkingDocUpdate,
                            )

                            doc_path = _Path(instance.working_doc_path)
                            if not doc_path.is_absolute():
                                doc_path = engine._memory_root / doc_path
                            checklist = _life_admin_checklist_text(goal)
                            await engine.working_docs.apply_update(
                                instance_id=task.pipeline_instance_id,
                                path=doc_path,
                                update=WorkingDocUpdate(
                                    summary=(
                                        "Practical life-admin checklist prepared "
                                        "from the user's current week."
                                    ),
                                    section_patches={
                                        "Findings": checklist,
                                        "Current Plan": (
                                            "- [x] Extract concrete tomorrow-morning "
                                            "life-admin steps\n"
                                            "- [x] Preserve open loose ends and "
                                            "blocked facts\n"
                                            "- [x] Keep the checklist local and "
                                            "low-pressure"
                                        ),
                                    },
                                    reason="practical_life_admin_complete",
                                ),
                            )
                    except Exception:  # noqa: BLE001
                        log.debug(
                            "practical_life_admin_doc_update_failed",
                            exc_info=True,
                        )
                return StepResult(
                    outcome="complete",
                    result_summary=(
                        "practical life-admin checklist completed; "
                        "working doc updated"
                    ),
                )

            from kora_v2.autonomous.pipeline_factory import (
                get_autonomous_step_fn,
            )

            step_fn = (
                _practical_life_admin_step
                if _is_practical_life_admin_goal(goal)
                else get_autonomous_step_fn()
            )

            await engine.dispatch_task(
                goal=goal,
                system_prompt=system_prompt,
                step_fn=step_fn,
                preset="long_background",
                stage_name=stage_names[0] if stage_names else pipeline_name,
                pipeline_instance_id=instance.id,
                tool_scope=combined_scope or None,
            )
            seeded_task_count += 1
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "decompose_and_dispatch_seed_task_failed",
                pipeline_instance_id=instance.id,
            )
            return json.dumps(
                {
                    "status": "error",
                    "error_category": "dispatch",
                    "pipeline_instance_id": instance.id,
                    "pipeline_name": pipeline_name,
                    "working_doc_path": str(working_doc_path_obj),
                    "message": (
                        "Pipeline was declared, but no runnable worker task "
                        "could be scheduled."
                    ),
                    "details": str(exc),
                }
            )

    return json.dumps(
        {
            "status": "ok",
            "pipeline_instance_id": instance.id,
            "pipeline_name": pipeline_name,
            "working_doc_path": str(working_doc_path_obj),
            "stage_count": len(stages),
            "seeded_task_count": seeded_task_count,
        }
    )


def _routes_to_registered_proactive_research(
    *,
    pipeline_name: str,
    goal: str,
    intent_duration: str,
    stages: list[dict[str, Any]],
) -> bool:
    """Return True for user-created background research pipelines.

    Acceptance conversations often ask for "privacy/local-first
    research" and the LLM names the runtime pipeline after the topic
    (for example ``privacy_research``). That should still use the
    registered ``proactive_research`` core pipeline instead of creating
    an ad-hoc autonomous pipeline, because Area C's handler owns the
    research/reporting semantics.
    """
    name = pipeline_name.strip().lower()
    goal_l = goal.strip().lower()
    if "cancel-probe" in name or "cancel_probe" in name:
        return False
    if "cancel-probe" in goal_l or "cancel_probe" in goal_l:
        return False
    if name == "proactive_research":
        return True
    if not (name.endswith("_research") or name.endswith("-research")):
        return False
    if intent_duration not in {"long", "indefinite", "short"}:
        return False

    haystack = " ".join(
        [
            goal,
            pipeline_name,
            " ".join(str(stage.get("name", "")) for stage in stages),
        ]
    ).lower()
    research_signals = (
        "research",
        "deep dive",
        "rabbit-hole",
        "rabbit hole",
        "look into",
        "compare",
        "privacy",
        "local-first",
        "data ownership",
    )
    return any(signal in haystack for signal in research_signals)


def _is_practical_life_admin_goal(goal: str) -> bool:
    lowered = goal.lower()
    return any(
        signal in lowered
        for signal in (
            "practical life-admin",
            "life-admin checklist",
            "local checklist",
            "doctor portal",
            "doctor-form",
            "grocery pickup",
            "appointment prep",
        )
    )


def _life_admin_checklist_text(goal: str) -> str:
    lowered = goal.lower()
    items: list[str] = []
    if "grocery" in lowered:
        items.extend([
            "- Confirm pickup window before leaving.",
            "- Put bags, wallet, keys, and headphones by the door.",
            "- If energy is low, slide pickup instead of adding extra errands.",
        ])
    if "doctor" in lowered or "portal" in lowered or "form" in lowered:
        items.extend([
            "- Open the doctor portal and find the form before answering anything.",
            "- Fill only known answers first; leave ambiguous questions flagged.",
            "- Use portal/message/app before phone calls unless the user chooses otherwise.",
        ])
    if "birthday" in lowered or "maya" in lowered:
        items.append("- Send Maya one sentence; no perfect wording pass required.")
    if not items:
        items.extend([
            "- Pick the one concrete next action with the least setup.",
            "- Keep unsupported external facts marked as unknown.",
            "- Carry unresolved items forward without shame language.",
        ])
    return (
        "## Practical Checklist\n\n"
        + "\n".join(items)
        + "\n\n## Open Facts\n\n"
        "- This was prepared from local conversation/runtime state.\n"
        "- External portals, stores, and calendars still require user confirmation.\n"
        "- Trusted support is not contacted automatically.\n"
    )


def _is_cancel_probe_request(pipeline_name: str, goal: str) -> bool:
    name = pipeline_name.strip().lower()
    if name == "user_autonomous_task":
        return False
    haystack = f"{pipeline_name} {goal}".lower()
    return _haystack_matches_cancel_probe(haystack)


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
    out: list[dict[str, Any]] = []
    terminal_task_ids: list[str] = []
    for t in tasks:
        state_value = (
            getattr(getattr(t, "state", None), "value", None)
            or str(getattr(t, "state", ""))
        )
        task_id = getattr(t, "id", None)
        pipeline_instance_id = getattr(t, "pipeline_instance_id", None)
        pipeline_name = ""
        if pipeline_instance_id:
            try:
                instance = await engine.instance_registry.load(pipeline_instance_id)
                pipeline_name = str(getattr(instance, "pipeline_name", "") or "")
            except Exception:  # noqa: BLE001
                log.debug(
                    "orchestration_get_running_tasks_instance_lookup_failed",
                    task_id=task_id,
                    exc_info=True,
                )
        if _is_protected_system_pipeline_name(
            pipeline_name
        ) and not _explicitly_mentions_protected_pipeline(
            str(user_message or ""),
            pipeline_name,
        ):
            continue
        out.append(
            {
                "task_id": task_id,
                "stage_name": getattr(t, "stage_name", None),
                "state": state_value,
                "goal": getattr(t, "goal", None),
                "result_summary": getattr(t, "result_summary", None),
                "error_message": getattr(t, "error_message", None),
                "pipeline_instance_id": pipeline_instance_id,
            }
        )
        if task_id and state_value in {"completed", "failed", "cancelled"}:
            terminal_task_ids.append(str(task_id))

    for task_id in terminal_task_ids:
        try:
            await engine.acknowledge_task(task_id)
        except Exception:  # noqa: BLE001
            log.debug(
                "orchestration_get_running_tasks_ack_failed",
                task_id=task_id,
                exc_info=True,
            )

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
    reason_lower = reason.lower()

    task = None
    instance = None
    pipeline_tasks: list[Any] = []
    try:
        task = await engine.get_task(task_id)
        pipeline_instance_id = getattr(task, "pipeline_instance_id", None)
        if pipeline_instance_id is not None:
            instance = await engine.instance_registry.load(pipeline_instance_id)
    except Exception:  # noqa: BLE001
        log.debug("cancel_task_context_lookup_failed", exc_info=True)

    if task is None:
        try:
            instance = await engine.instance_registry.load(task_id)
            pipeline_tasks = await engine.task_registry.load_by_pipeline(task_id)
        except Exception:  # noqa: BLE001
            instance = None
            pipeline_tasks = []

    pipeline_name = str(getattr(instance, "pipeline_name", "") or "")
    if _is_protected_system_pipeline_name(
        pipeline_name
    ) and not _explicitly_mentions_protected_pipeline(reason, pipeline_name):
        return json.dumps(
            {
                "status": "ok",
                "task_id": task_id,
                "cancelled": False,
                "message": (
                    f"Preserved protected system pipeline {pipeline_name}; "
                    "the user did not explicitly name it."
                ),
            }
        )

    target_words = _target_words_from_cancel_request(reason_lower)
    preserve_research = _reason_preserves_research(reason_lower)
    if (
        preserve_research
        and pipeline_name == "proactive_research"
        and not _reason_explicitly_cancels_research(reason_lower)
    ):
        return json.dumps(
            {
                "status": "ok",
                "task_id": task_id,
                "cancelled": False,
                "message": "Preserved because the user asked to keep proactive_research running.",
            }
        )

    if preserve_research and task is not None:
        try:
            haystack = " ".join(
                str(value or "").lower()
                for value in (
                    getattr(task, "stage_name", None),
                    getattr(task, "goal", None),
                    getattr(task, "result_summary", None),
                    getattr(instance, "goal", None),
                    pipeline_name,
                )
            ).replace("_", " ").replace("-", " ")
            target_score = sum(
                1 for word in target_words if word and word in haystack
            )
            if target_score == 0 and "research" in haystack:
                return json.dumps(
                    {
                        "status": "ok",
                        "task_id": task_id,
                        "cancelled": False,
                        "message": "Preserved because the user asked to keep the research task.",
                    }
                )
        except Exception:  # noqa: BLE001
            log.debug("cancel_task_preserve_research_check_failed", exc_info=True)

    if _reason_targets_cancel_probe(reason) and not _haystack_matches_cancel_probe(
        getattr(task, "id", None),
        getattr(task, "stage_name", None),
        getattr(task, "goal", None),
        getattr(task, "result_summary", None),
        getattr(task, "error_message", None),
        pipeline_name,
        getattr(instance, "goal", None),
    ):
        return json.dumps(
            {
                "status": "ok",
                "task_id": task_id,
                "cancelled": False,
                "message": (
                    "No cancellation applied: the user asked for cancel-probe, "
                    "and this task is not cancel-probe."
                ),
            }
        )

    if target_words and task is not None:
        task_haystack = " ".join(
            str(value or "").lower()
            for value in (
                getattr(task, "id", None),
                getattr(task, "stage_name", None),
                getattr(task, "goal", None),
                getattr(task, "result_summary", None),
                getattr(task, "error_message", None),
                pipeline_name,
                getattr(instance, "goal", None),
            )
        ).replace("_", " ").replace("-", " ")
        if not any(word in task_haystack for word in target_words):
            return json.dumps(
                {
                    "status": "ok",
                    "task_id": task_id,
                    "cancelled": False,
                    "message": (
                        "No cancellation applied: the requested target does "
                        "not match that task."
                    ),
                }
            )

    if task is None and instance is not None and pipeline_tasks:
        cancellable = [
            candidate for candidate in pipeline_tasks
            if str(getattr(candidate, "state", "")).lower().split(".")[-1]
            not in {"completed", "failed", "cancelled"}
        ]
        cancelled_ids: list[str] = []
        for candidate in cancellable:
            candidate_id = str(getattr(candidate, "id", "") or "")
            if not candidate_id:
                continue
            if await engine.cancel_task(candidate_id, reason=reason):
                cancelled_ids.append(candidate_id)
        return json.dumps(
            {
                "status": "ok" if cancelled_ids else "error",
                "task_id": task_id,
                "pipeline_instance_id": task_id,
                "cancelled": bool(cancelled_ids),
                "cancelled_task_ids": cancelled_ids,
            }
        )

    ok = await engine.cancel_task(task_id, reason=reason)
    return json.dumps(
        {"status": "ok" if ok else "error", "task_id": task_id, "cancelled": ok}
    )


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
