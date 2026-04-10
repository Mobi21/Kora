"""Phase 6B: Guided Routines.

Routines are specialized autonomous plan types that run through the same
Phase 6A runtime graph with mode='routine'. They provide step-by-step
guidance with partial completion tracking and energy-adapted variants.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import aiosqlite
import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)


# ── Models ────────────────────────────────────────────────────────────────


class RoutineStep(BaseModel):
    """A single step in a routine."""

    index: int
    title: str
    description: str
    estimated_minutes: int = 5
    energy_required: Literal["low", "medium", "high"] = "medium"
    skippable: bool = True
    cue: str = ""  # ADHD-friendly cue/reminder for this step


class RoutineVariant(BaseModel):
    """A variant of a routine (standard or low_energy)."""

    name: str  # "standard" or "low_energy"
    steps: list[RoutineStep]
    estimated_total_minutes: int


class Routine(BaseModel):
    """A routine template."""

    id: str
    name: str
    description: str
    standard: RoutineVariant
    low_energy: RoutineVariant | None = None
    tags: list[str] = []
    created_at: datetime
    updated_at: datetime


class RoutineSessionState(BaseModel):
    """Partial completion state for a routine session."""

    session_id: str          # routine_sessions.id
    routine_id: str
    variant: Literal["standard", "low_energy"] = "standard"
    current_step_index: int = 0
    completed_steps: list[int] = []
    skipped_steps: list[int] = []
    status: Literal["active", "completed", "abandoned"] = "active"
    started_at: datetime
    completed_at: datetime | None = None
    completion_confidence: float = 0.0


class RoutineProgress(BaseModel):
    """User-facing progress summary."""

    routine_name: str
    variant: str
    total_steps: int
    completed: int
    skipped: int
    remaining: int
    completion_pct: float
    message: str  # shame-free progress description


# ── Helpers ───────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _steps_to_json(steps: list[RoutineStep]) -> str:
    return json.dumps([s.model_dump() for s in steps])


def _steps_from_json(raw: str) -> list[RoutineStep]:
    data = json.loads(raw)
    return [RoutineStep(**item) for item in data]


def _routine_to_row(routine: Routine) -> dict:
    low_energy_json: str | None = None
    if routine.low_energy is not None:
        low_energy_json = _steps_to_json(routine.low_energy.steps)

    return {
        "id": routine.id,
        "name": routine.name,
        "description": routine.description,
        "steps_json": _steps_to_json(routine.standard.steps),
        "low_energy_variant_json": low_energy_json,
        "tags": json.dumps(routine.tags),
        "created_at": routine.created_at.isoformat(),
        "updated_at": routine.updated_at.isoformat(),
    }


def _routine_from_row(row: aiosqlite.Row) -> Routine:
    standard_steps = _steps_from_json(row["steps_json"])
    estimated_std = sum(s.estimated_minutes for s in standard_steps)
    standard = RoutineVariant(
        name="standard",
        steps=standard_steps,
        estimated_total_minutes=estimated_std,
    )

    low_energy: RoutineVariant | None = None
    if row["low_energy_variant_json"]:
        le_steps = _steps_from_json(row["low_energy_variant_json"])
        low_energy = RoutineVariant(
            name="low_energy",
            steps=le_steps,
            estimated_total_minutes=sum(s.estimated_minutes for s in le_steps),
        )

    tags: list[str] = json.loads(row["tags"]) if row["tags"] else []

    return Routine(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        standard=standard,
        low_energy=low_energy,
        tags=tags,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _session_from_row(row: aiosqlite.Row) -> RoutineSessionState:
    completed_at: datetime | None = None
    if row["completed_at"]:
        completed_at = datetime.fromisoformat(row["completed_at"])

    return RoutineSessionState(
        session_id=row["id"],
        routine_id=row["routine_id"],
        variant=row["variant"],
        current_step_index=row["current_step_index"],
        completed_steps=json.loads(row["completed_steps"]),
        skipped_steps=json.loads(row["skipped_steps"]),
        status=row["status"],
        started_at=datetime.fromisoformat(row["started_at"]),
        completed_at=completed_at,
        completion_confidence=row["completion_confidence"],
    )


# ── RoutineManager ────────────────────────────────────────────────────────


class RoutineManager:
    """Manages routine templates and session tracking."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    # ── Routine CRUD ─────────────────────────────────────────────────

    async def create_routine(self, routine: Routine) -> str:
        """Persist a routine template. Returns routine.id."""
        row = _routine_to_row(routine)
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                INSERT INTO routines
                    (id, name, description, steps_json,
                     low_energy_variant_json, tags, created_at, updated_at)
                VALUES
                    (:id, :name, :description, :steps_json,
                     :low_energy_variant_json, :tags, :created_at, :updated_at)
                """,
                row,
            )
            await db.commit()
        log.info("routine.created", routine_id=routine.id, name=routine.name)
        return routine.id

    async def get_routine(self, routine_id: str) -> Routine | None:
        """Load a routine template by ID."""
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM routines WHERE id = ?", (routine_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return _routine_from_row(row)

    async def list_routines(self, tags: list[str] | None = None) -> list[Routine]:
        """List available routines, optionally filtered by tag."""
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM routines ORDER BY name") as cursor:
                rows = await cursor.fetchall()

        routines = [_routine_from_row(r) for r in rows]

        if tags:
            tag_set = set(tags)
            routines = [r for r in routines if tag_set.intersection(r.tags)]

        return routines

    # ── Session management ───────────────────────────────────────────

    async def start_session(
        self,
        routine_id: str,
        session_id: str,
        variant: Literal["standard", "low_energy"] = "standard",
        parent_session_id: str | None = None,
    ) -> RoutineSessionState:
        """Start a new routine session. Returns the session state.

        Args:
            routine_id: ID of the routine template to run.
            session_id: Primary key for this routine session record.
            variant: Which routine variant to use.
            parent_session_id: FK to the conversation sessions table (optional).
        """
        now = _now_iso()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                INSERT INTO routine_sessions
                    (id, routine_id, session_id, variant, current_step_index,
                     completed_steps, skipped_steps, completion_confidence,
                     status, started_at)
                VALUES
                    (?, ?, ?, ?, 0, '[]', '[]', 0.0, 'active', ?)
                """,
                (session_id, routine_id, parent_session_id, variant, now),
            )
            await db.commit()

        log.info(
            "routine_session.started",
            session_id=session_id,
            routine_id=routine_id,
            variant=variant,
        )
        return RoutineSessionState(
            session_id=session_id,
            routine_id=routine_id,
            variant=variant,
            current_step_index=0,
            completed_steps=[],
            skipped_steps=[],
            status="active",
            started_at=datetime.fromisoformat(now),
        )

    async def get_session(self, session_id: str) -> RoutineSessionState | None:
        """Load a routine session by its session_id (routine_sessions.id)."""
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM routine_sessions WHERE id = ?", (session_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return _session_from_row(row)

    async def advance_step(
        self,
        session_id: str,
        step_index: int,
        skipped: bool = False,
    ) -> RoutineSessionState:
        """Mark a step completed or skipped and advance to next."""
        state = await self.get_session(session_id)
        if state is None:
            raise ValueError(f"Routine session not found: {session_id}")

        if skipped:
            if step_index not in state.skipped_steps:
                state.skipped_steps.append(step_index)
        else:
            if step_index not in state.completed_steps:
                state.completed_steps.append(step_index)

        # Advance the pointer past the current step if it matches
        next_index = state.current_step_index
        if step_index >= state.current_step_index:
            next_index = step_index + 1

        # Compute confidence: fraction of non-skipped steps completed
        routine = await self.get_routine(state.routine_id)
        confidence = 0.0
        if routine is not None:
            variant_obj = (
                routine.low_energy
                if state.variant == "low_energy" and routine.low_energy
                else routine.standard
            )
            total = len(variant_obj.steps)
            if total > 0:
                confidence = len(state.completed_steps) / total

        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                UPDATE routine_sessions
                SET current_step_index = ?,
                    completed_steps = ?,
                    skipped_steps = ?,
                    completion_confidence = ?
                WHERE id = ?
                """,
                (
                    next_index,
                    json.dumps(state.completed_steps),
                    json.dumps(state.skipped_steps),
                    confidence,
                    session_id,
                ),
            )
            await db.commit()

        state.current_step_index = next_index
        state.completion_confidence = confidence
        log.debug(
            "routine_session.step_advanced",
            session_id=session_id,
            step_index=step_index,
            skipped=skipped,
            next_index=next_index,
        )
        return state

    async def complete_session(self, session_id: str) -> RoutineSessionState:
        """Mark a routine session as completed."""
        now = _now_iso()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                UPDATE routine_sessions
                SET status = 'completed', completed_at = ?
                WHERE id = ?
                """,
                (now, session_id),
            )
            await db.commit()

        state = await self.get_session(session_id)
        if state is None:
            raise ValueError(f"Routine session not found: {session_id}")
        log.info("routine_session.completed", session_id=session_id)
        return state

    async def abandon_session(self, session_id: str) -> RoutineSessionState:
        """Mark a routine session as abandoned (no judgment)."""
        now = _now_iso()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                UPDATE routine_sessions
                SET status = 'abandoned', completed_at = ?
                WHERE id = ?
                """,
                (now, session_id),
            )
            await db.commit()

        state = await self.get_session(session_id)
        if state is None:
            raise ValueError(f"Routine session not found: {session_id}")
        log.info("routine_session.abandoned", session_id=session_id)
        return state

    def get_progress(
        self, session: RoutineSessionState, routine: Routine
    ) -> RoutineProgress:
        """Compute user-facing progress summary with shame-free message."""
        variant_obj = (
            routine.low_energy
            if session.variant == "low_energy" and routine.low_energy
            else routine.standard
        )
        total = len(variant_obj.steps)
        completed = len(session.completed_steps)
        skipped = len(session.skipped_steps)
        remaining = max(0, total - completed - skipped)
        completion_pct = (completed / total * 100) if total > 0 else 0.0

        if completion_pct >= 100:
            message = "You did it. Routine complete."
        elif completion_pct >= 60:
            message = f"Good progress — {completed}/{total} steps done."
        elif completion_pct >= 30:
            message = "Got partway through — that counts."
        elif completed > 0:
            message = "You started. That's the hardest part."
        else:
            message = "Ready when you are."

        return RoutineProgress(
            routine_name=routine.name,
            variant=session.variant,
            total_steps=total,
            completed=completed,
            skipped=skipped,
            remaining=remaining,
            completion_pct=round(completion_pct, 1),
            message=message,
        )
