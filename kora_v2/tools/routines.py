"""Routine management tools for Kora V2.

Provides 4 tools for guided routine lifecycle:
  list_routines, start_routine, advance_routine, routine_progress.

Note: from __future__ import annotations is intentionally omitted.
The @tool decorator inspects runtime type annotations via inspect.signature(),
and PEP 563 (stringified annotations) breaks issubclass(input_type, BaseModel).
"""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
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


def _active_session_id(container: Any) -> str | None:
    session_mgr = getattr(container, "session_manager", None)
    active = getattr(session_mgr, "active_session", None)
    session_id = getattr(active, "session_id", None)
    return str(session_id) if session_id else None


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or f"routine_{uuid.uuid4().hex[:8]}"


async def _register_runtime_pipeline_for_routine(
    routine_id: str,
    container: Any,
) -> str | None:
    mgr = _get_routine_manager(container)
    engine = getattr(container, "orchestration_engine", None)
    if mgr is None or engine is None:
        return None
    routine = await mgr.get_routine(routine_id)
    if routine is None:
        return None
    try:
        from kora_v2.life.routines import register_routine_pipeline

        pipeline = await register_routine_pipeline(routine, engine)
        return pipeline.name
    except Exception as exc:
        log.warning(
            "routine_pipeline_register_failed",
            routine_id=routine_id,
            error=str(exc),
        )
        return None


async def _create_routine_reminder(
    routine_id: str,
    routine_name: str,
    container: Any,
) -> str | None:
    mgr = _get_routine_manager(container)
    if mgr is None:
        return None
    try:
        from kora_v2.life.reminders import ReminderStore

        reminder_store = ReminderStore(mgr.db_path)
        due_at = datetime.now(UTC) + timedelta(minutes=1)
        reminder_id = await reminder_store.create_reminder(
            title=f"Routine check-in: {routine_name}",
            description=(
                "Routine reminder created with the routine so continuity_check "
                "can surface the scheduled support."
            ),
            due_at=due_at,
            source="routine",
            metadata={"routine_id": routine_id},
        )
        engine = getattr(container, "orchestration_engine", None)
        if engine is not None:
            try:
                await engine.start_triggered_pipeline(
                    "continuity_check",
                    goal=f"Routine reminder created: {routine_name}",
                    trigger_id="create_routine",
                )
            except Exception:  # noqa: BLE001
                log.debug("routine_reminder_continuity_trigger_failed", exc_info=True)
        return reminder_id
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "routine_reminder_create_failed",
            routine_id=routine_id,
            error=str(exc),
        )
        return None


# ── Input models ─────────────────────────────────────────────────────────────


class ListRoutinesInput(BaseModel):
    tags: str = Field("", description="Comma-separated tags to filter by (empty = all)")


class CreateRoutineInput(BaseModel):
    name: str = Field(..., description="Human-readable routine name")
    description: str = Field("", description="Short routine purpose")
    steps: list[str] = Field(..., description="Ordered routine step titles")
    routine_id: str = Field("", description="Optional stable routine template ID")
    tags: str = Field("", description="Comma-separated routine tags")
    low_energy_steps: list[str] = Field(
        default_factory=list,
        description="Optional reduced step list for low-energy days",
    )


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
    name="create_routine",
    description=(
        "Create or persist a reusable guided routine template from ordered "
        "steps, then register its runtime pipeline so it can fire later."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def create_routine(input: CreateRoutineInput, container: Any) -> str:
    """Create a routine template and register its runtime pipeline."""
    mgr = _get_routine_manager(container)
    if mgr is None:
        return _err("Routine manager not available")

    step_titles = [s.strip() for s in input.steps if str(s).strip()]
    if not step_titles:
        return _err("routine requires at least one step")

    try:
        from kora_v2.life.routines import Routine, RoutineStep, RoutineVariant

        now = datetime.now(UTC)
        routine_id = input.routine_id.strip() or _slug(input.name)

        def make_steps(titles: list[str]) -> list[RoutineStep]:
            return [
                RoutineStep(
                    index=i,
                    title=title,
                    description=title,
                    estimated_minutes=5,
                    energy_required="low" if len(titles) <= 3 else "medium",
                    skippable=True,
                    cue=title,
                )
                for i, title in enumerate(titles)
            ]

        standard_steps = make_steps(step_titles)
        low_energy = None
        le_titles = [
            s.strip() for s in input.low_energy_steps if str(s).strip()
        ]
        if le_titles:
            le_steps = make_steps(le_titles)
            low_energy = RoutineVariant(
                name="low_energy",
                steps=le_steps,
                estimated_total_minutes=sum(s.estimated_minutes for s in le_steps),
            )

        routine = Routine(
            id=routine_id,
            name=input.name.strip(),
            description=input.description.strip(),
            standard=RoutineVariant(
                name="standard",
                steps=standard_steps,
                estimated_total_minutes=sum(
                    s.estimated_minutes for s in standard_steps
                ),
            ),
            low_energy=low_energy,
            tags=[t.strip() for t in input.tags.split(",") if t.strip()],
            created_at=now,
            updated_at=now,
        )
        created_new = True
        try:
            await mgr.create_routine(routine)
        except Exception as exc:
            if "UNIQUE constraint failed" not in str(exc):
                raise
            existing = await mgr.get_routine(routine_id)
            if existing is None:
                raise
            routine = existing
            created_new = False
        pipeline_name = await _register_runtime_pipeline_for_routine(
            routine.id, container
        )
        reminder_id = await _create_routine_reminder(
            routine.id,
            routine.name,
            container,
        )
        return _ok({
            "status": "created" if created_new else "existing",
            "routine_id": routine.id,
            "name": routine.name,
            "step_count": len(routine.standard.steps),
            "runtime_pipeline": pipeline_name,
            "runtime_pipeline_registered": bool(pipeline_name),
            "routine_reminder_id": reminder_id,
            "routine_reminder_created": bool(reminder_id),
            "message": (
                "Routine template ready and runtime pipeline registered: "
                f"{pipeline_name}"
            ),
        })
    except Exception as exc:
        log.warning("create_routine.error", error=str(exc))
        return _err(f"failed to create routine: {exc}")


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
            parent_session_id=_active_session_id(container),
        )
        pipeline_name = await _register_runtime_pipeline_for_routine(
            input.routine_id, container
        )
        log.info(
            "start_routine.ok",
            session_id=session.session_id,
            routine_id=input.routine_id,
            variant=session.variant,
            runtime_pipeline=pipeline_name,
        )
        return _ok({
            "status": "started",
            "session_id": session.session_id,
            "routine_id": session.routine_id,
            "variant": session.variant,
            "runtime_pipeline": pipeline_name,
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
