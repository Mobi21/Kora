"""Routine management tools for Kora V2.

Provides 4 tools for guided routine lifecycle:
  list_routines, start_routine, advance_routine, routine_progress.

Note: from __future__ import annotations is intentionally omitted.
The @tool decorator inspects runtime type annotations via inspect.signature(),
and PEP 563 (stringified annotations) breaks issubclass(input_type, BaseModel).
"""

import json
from typing import Any

import structlog
from pydantic import BaseModel, Field

from kora_v2.tools.registry import tool
from kora_v2.tools.types import AuthLevel, ToolCategory

log = structlog.get_logger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _ok(payload: dict[str, Any]) -> str:
    payload.setdefault("success", True)
    return json.dumps(payload)


def _err(message: str) -> str:
    return json.dumps({"success": False, "error": message})


def _get_routine_manager(container: Any):
    """Return the RoutineManager from container, or None."""
    return getattr(container, "routine_manager", None)


# ── Input models ─────────────────────────────────────────────────────────────


class ListRoutinesInput(BaseModel):
    tags: str = Field("", description="Comma-separated tags to filter by (empty = all)")


class StartRoutineInput(BaseModel):
    routine_id: str = Field(..., description="ID of the routine template to start")
    session_id: str = Field(..., description="Unique ID for this routine session")
    variant: str = Field("standard", description="Variant to use: 'standard' or 'low_energy'")


class AdvanceRoutineInput(BaseModel):
    session_id: str = Field(..., description="Routine session ID to advance")
    step_index: int = Field(..., description="Index of the step being completed or skipped")
    skipped: bool = Field(False, description="True to skip this step instead of completing it")


class RoutineProgressInput(BaseModel):
    session_id: str = Field(..., description="Routine session ID to check progress for")


# ── Tool implementations ─────────────────────────────────────────────────────


@tool(
    name="list_routines",
    description=(
        "List available routines, optionally filtered by tags. "
        "Returns routine templates with their IDs, names, descriptions, and tags."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
)
async def list_routines(input: ListRoutinesInput, container: Any) -> str:
    """Query available routine templates."""
    mgr = _get_routine_manager(container)
    if mgr is None:
        return _err("Routine manager not available")

    tags: list[str] | None = None
    if input.tags:
        tags = [t.strip() for t in input.tags.split(",") if t.strip()]

    try:
        routines = await mgr.list_routines(tags=tags)
        items = [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "tags": r.tags,
            }
            for r in routines
        ]
        log.info("list_routines.ok", count=len(items))
        return _ok({"routines": items, "count": len(items)})
    except Exception as exc:
        log.warning("list_routines.error", error=str(exc))
        return _err(f"failed to list routines: {exc}")


@tool(
    name="start_routine",
    description=(
        "Begin a guided routine session. Creates a new session record "
        "for the specified routine template and variant. Returns the "
        "session ID and initial status."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def start_routine(input: StartRoutineInput, container: Any) -> str:
    """Start a new routine session."""
    mgr = _get_routine_manager(container)
    if mgr is None:
        return _err("Routine manager not available")

    try:
        session = await mgr.start_session(
            routine_id=input.routine_id,
            session_id=input.session_id,
            variant=input.variant,
        )
        log.info(
            "start_routine.ok",
            session_id=session.session_id,
            routine_id=input.routine_id,
            variant=session.variant,
        )
        return _ok({
            "status": "started",
            "session_id": session.session_id,
            "routine_id": session.routine_id,
            "variant": session.variant,
            "message": f"Routine session started ({session.variant} variant)",
        })
    except Exception as exc:
        log.warning("start_routine.error", error=str(exc))
        return _err(f"failed to start routine: {exc}")


@tool(
    name="advance_routine",
    description=(
        "Advance a routine by completing or skipping a step. "
        "Updates the session progress and returns current completion status."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def advance_routine(input: AdvanceRoutineInput, container: Any) -> str:
    """Complete or skip a step in an active routine session."""
    mgr = _get_routine_manager(container)
    if mgr is None:
        return _err("Routine manager not available")

    try:
        session = await mgr.advance_step(
            session_id=input.session_id,
            step_index=input.step_index,
            skipped=input.skipped,
        )
        # Compute progress if we can load the routine
        routine = await mgr.get_routine(session.routine_id)
        if routine is not None:
            progress = mgr.get_progress(session, routine)
            log.info(
                "advance_routine.ok",
                session_id=input.session_id,
                step_index=input.step_index,
                skipped=input.skipped,
                completion_pct=progress.completion_pct,
            )
            return _ok({
                "status": session.status,
                "step_index": session.current_step_index,
                "completion_pct": progress.completion_pct,
                "completed": progress.completed,
                "remaining": progress.remaining,
                "message": progress.message,
            })
        log.info(
            "advance_routine.ok",
            session_id=input.session_id,
            step_index=input.step_index,
        )
        return _ok({
            "status": session.status,
            "step_index": session.current_step_index,
        })
    except Exception as exc:
        log.warning("advance_routine.error", error=str(exc))
        return _err(f"failed to advance routine: {exc}")


@tool(
    name="routine_progress",
    description=(
        "Check current progress on an active routine session. "
        "Returns step counts, completion percentage, and a shame-free progress message."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
)
async def routine_progress(input: RoutineProgressInput, container: Any) -> str:
    """Get progress for an active routine session."""
    mgr = _get_routine_manager(container)
    if mgr is None:
        return _err("Routine manager not available")

    try:
        session = await mgr.get_session(input.session_id)
        if session is None:
            return _err(f"No active routine session: {input.session_id}")

        routine = await mgr.get_routine(session.routine_id)
        if routine is None:
            return _err(f"Routine not found: {session.routine_id}")

        progress = mgr.get_progress(session, routine)
        log.info(
            "routine_progress.ok",
            session_id=input.session_id,
            completion_pct=progress.completion_pct,
        )
        return _ok({
            "routine_name": progress.routine_name,
            "variant": progress.variant,
            "total_steps": progress.total_steps,
            "completed": progress.completed,
            "skipped": progress.skipped,
            "remaining": progress.remaining,
            "completion_pct": progress.completion_pct,
            "message": progress.message,
        })
    except Exception as exc:
        log.warning("routine_progress.error", error=str(exc))
        return _err(f"failed to get routine progress: {exc}")
