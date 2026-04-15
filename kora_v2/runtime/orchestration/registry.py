"""Pipeline and worker-task registries — spec §11.

The registries are the *only* place in the orchestration layer that
talks to SQL directly (aside from the ledger and limiter, which each
own their own narrow tables). They wrap ``pipeline_instances`` and
``worker_tasks`` and expose dataclass-friendly read/write operations
for the dispatcher and engine.

``init_orchestration_schema`` loads and applies
``migrations/001_orchestration.sql`` so callers can re-use the same
``operational.db`` that the core daemon already bootstraps.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

from kora_v2.runtime.orchestration.pipeline import (
    Pipeline,
    PipelineInstance,
    PipelineInstanceState,
)
from kora_v2.runtime.orchestration.worker_task import (
    WorkerTask,
    WorkerTaskConfig,
    WorkerTaskPreset,
    WorkerTaskState,
    get_preset,
)

log = structlog.get_logger(__name__)

_MIGRATION_PATH = Path(__file__).parent / "migrations" / "001_orchestration.sql"
_NOTIFICATIONS_MIGRATION_PATH = (
    Path(__file__).parent / "migrations" / "002_notifications_templates.sql"
)

_NOTIFICATION_EXTRA_COLUMNS: tuple[tuple[str, str], ...] = (
    ("delivery_tier", "TEXT DEFAULT 'llm'"),
    ("template_id", "TEXT"),
    ("template_vars", "TEXT"),
    ("reason", "TEXT"),
)


# ── Schema bootstrap ──────────────────────────────────────────────────────


async def init_orchestration_schema(db_path: Path) -> None:
    """Apply orchestration migrations to *db_path* (idempotent).

    Runs:
      * ``001_orchestration.sql`` — the eight Phase 7.5 tables.
      * ``002_notifications_templates.sql`` (conditionally) — adds the
        two-tier delivery columns to the existing ``notifications``
        table. ``ALTER TABLE ADD COLUMN`` is not idempotent in SQLite,
        so this function inspects ``pragma table_info`` first.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    async with aiosqlite.connect(str(db_path)) as db:
        await db.executescript(sql)
        await db.commit()

        # Only touch the notifications table if it already exists —
        # orchestration-only test databases don't bootstrap the
        # daemon's core schema.
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='notifications'"
        )
        row = await cursor.fetchone()
        if row is not None:
            info_cursor = await db.execute("PRAGMA table_info(notifications)")
            existing = {r[1] for r in await info_cursor.fetchall()}
            for col_name, col_decl in _NOTIFICATION_EXTRA_COLUMNS:
                if col_name not in existing:
                    await db.execute(
                        f"ALTER TABLE notifications ADD COLUMN {col_name} {col_decl}"
                    )
            await db.commit()
    log.debug("orchestration_schema_initialised", path=str(db_path))


# ── Pipeline registry (in-memory declarative store) ──────────────────────


class PipelineRegistry:
    """In-memory registry of :class:`Pipeline` declarations.

    Pipelines are stateless declarations so they live in a plain dict;
    only their *instances* (live runs) are persisted to SQL.
    """

    def __init__(self) -> None:
        self._pipelines: dict[str, Pipeline] = {}

    def register(self, pipeline: Pipeline) -> None:
        pipeline.validate()
        self._pipelines[pipeline.name] = pipeline
        log.debug("pipeline_registered", name=pipeline.name, stages=len(pipeline.stages))

    def unregister(self, name: str) -> None:
        self._pipelines.pop(name, None)

    def get(self, name: str) -> Pipeline:
        if name not in self._pipelines:
            raise KeyError(f"Pipeline {name!r} is not registered")
        return self._pipelines[name]

    def all(self) -> list[Pipeline]:
        return list(self._pipelines.values())

    def __contains__(self, name: str) -> bool:
        return name in self._pipelines


# ── Worker task registry (SQL-backed) ─────────────────────────────────────


class WorkerTaskRegistry:
    """SQL-backed registry for :class:`WorkerTask`."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def save(self, task: WorkerTask) -> None:
        """Upsert *task* into ``worker_tasks``."""
        depends_on_json = json.dumps(task.depends_on) if task.depends_on else None
        tool_scope_json = json.dumps(task.config.tool_scope)
        now = datetime.now(UTC).isoformat()

        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                """
                INSERT INTO worker_tasks (
                    id, pipeline_instance_id, parent_task_id, stage_name,
                    task_preset, state, depends_on, tool_scope, system_prompt,
                    goal, checkpoint_blob, request_count, agent_turn_count,
                    cancellation_requested, created_at, last_step_at,
                    last_checkpoint_at, completed_at, result_summary,
                    error_message, result_acknowledged_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(id) DO UPDATE SET
                    state = excluded.state,
                    depends_on = excluded.depends_on,
                    tool_scope = excluded.tool_scope,
                    system_prompt = excluded.system_prompt,
                    goal = excluded.goal,
                    checkpoint_blob = excluded.checkpoint_blob,
                    request_count = excluded.request_count,
                    agent_turn_count = excluded.agent_turn_count,
                    cancellation_requested = excluded.cancellation_requested,
                    last_step_at = excluded.last_step_at,
                    last_checkpoint_at = excluded.last_checkpoint_at,
                    completed_at = excluded.completed_at,
                    result_summary = excluded.result_summary,
                    error_message = excluded.error_message,
                    result_acknowledged_at = excluded.result_acknowledged_at
                """,
                (
                    task.id,
                    task.pipeline_instance_id,
                    task.parent_task_id,
                    task.stage_name,
                    task.config.preset,
                    task.state.value,
                    depends_on_json,
                    tool_scope_json,
                    task.system_prompt,
                    task.goal,
                    None,  # checkpoint blob written via CheckpointStore
                    task.request_count,
                    task.agent_turn_count,
                    int(task.cancellation_requested),
                    task.created_at.isoformat(),
                    task.last_step_at.isoformat() if task.last_step_at else None,
                    task.last_checkpoint_at.isoformat() if task.last_checkpoint_at else None,
                    task.completed_at.isoformat() if task.completed_at else None,
                    task.result_summary,
                    task.error_message,
                    task.result_acknowledged_at.isoformat() if task.result_acknowledged_at else None,
                ),
            )
            await db.commit()
        log.debug("worker_task_saved", id=task.id, state=task.state.value, updated_at=now)

    async def update_state(
        self,
        task_id: str,
        state: WorkerTaskState,
        *,
        error_message: str | None = None,
        result_summary: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        completed = state in {
            WorkerTaskState.COMPLETED,
            WorkerTaskState.FAILED,
            WorkerTaskState.CANCELLED,
        }
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                """
                UPDATE worker_tasks
                SET state = ?,
                    last_step_at = ?,
                    completed_at = CASE WHEN ? = 1 THEN ? ELSE completed_at END,
                    error_message = COALESCE(?, error_message),
                    result_summary = COALESCE(?, result_summary)
                WHERE id = ?
                """,
                (
                    state.value,
                    now,
                    int(completed),
                    now if completed else None,
                    error_message,
                    result_summary,
                    task_id,
                ),
            )
            await db.commit()

    async def load(self, task_id: str) -> WorkerTask | None:
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM worker_tasks WHERE id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    async def load_all_non_terminal(self) -> list[WorkerTask]:
        """Used at startup to rehydrate everything the dispatcher should care about."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM worker_tasks "
                "WHERE state NOT IN ('completed', 'failed', 'cancelled') "
                "ORDER BY created_at ASC",
            )
            rows = await cursor.fetchall()
        return [self._row_to_task(row) for row in rows]

    async def load_by_pipeline(self, pipeline_instance_id: str) -> list[WorkerTask]:
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM worker_tasks WHERE pipeline_instance_id = ? "
                "ORDER BY created_at ASC",
                (pipeline_instance_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_task(row) for row in rows]

    async def delete(self, task_id: str) -> None:
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("DELETE FROM worker_tasks WHERE id = ?", (task_id,))
            await db.commit()

    # ── Helpers ───────────────────────────────────────────────────

    def _row_to_task(self, row: aiosqlite.Row) -> WorkerTask:
        preset: WorkerTaskPreset = row["task_preset"]  # type: ignore[assignment]
        try:
            config = get_preset(preset)
        except ValueError:
            log.warning("unknown_preset_in_row", id=row["id"], preset=preset)
            config = get_preset("bounded_background")

        # Re-inflate tool_scope from JSON
        try:
            tool_scope = json.loads(row["tool_scope"]) if row["tool_scope"] else []
        except json.JSONDecodeError:
            tool_scope = []
        config = _replace_tool_scope(config, tool_scope)

        depends_on: list[str] = []
        if row["depends_on"]:
            try:
                depends_on = list(json.loads(row["depends_on"]))
            except json.JSONDecodeError:
                depends_on = []

        return WorkerTask(
            id=row["id"],
            pipeline_instance_id=row["pipeline_instance_id"],
            stage_name=row["stage_name"],
            config=config,
            goal=row["goal"] or "",
            system_prompt=row["system_prompt"] or "",
            parent_task_id=row["parent_task_id"],
            depends_on=depends_on,
            state=WorkerTaskState(row["state"]),
            request_count=row["request_count"] or 0,
            agent_turn_count=row["agent_turn_count"] or 0,
            cancellation_requested=bool(row["cancellation_requested"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            last_step_at=datetime.fromisoformat(row["last_step_at"]) if row["last_step_at"] else None,
            last_checkpoint_at=datetime.fromisoformat(row["last_checkpoint_at"]) if row["last_checkpoint_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            result_summary=row["result_summary"],
            error_message=row["error_message"],
            result_acknowledged_at=(
                datetime.fromisoformat(row["result_acknowledged_at"])
                if row["result_acknowledged_at"]
                else None
            ),
        )


def _replace_tool_scope(config: WorkerTaskConfig, tool_scope: list[str]) -> WorkerTaskConfig:
    from dataclasses import replace
    return replace(config, tool_scope=tool_scope)


# ── Pipeline instance registry ────────────────────────────────────────────


class PipelineInstanceRegistry:
    """SQL-backed registry for :class:`PipelineInstance`."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def save(self, instance: PipelineInstance) -> None:
        instance.touch()
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                """
                INSERT INTO pipeline_instances (
                    id, pipeline_name, working_doc_path, parent_session_id,
                    parent_task_id, goal, state, intent_duration, started_at,
                    updated_at, completed_at, completion_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    state = excluded.state,
                    updated_at = excluded.updated_at,
                    completed_at = excluded.completed_at,
                    completion_reason = excluded.completion_reason,
                    working_doc_path = excluded.working_doc_path,
                    goal = excluded.goal
                """,
                (
                    instance.id,
                    instance.pipeline_name,
                    instance.working_doc_path,
                    instance.parent_session_id,
                    instance.parent_task_id,
                    instance.goal,
                    instance.state.value,
                    instance.intent_duration,
                    instance.started_at.isoformat(),
                    instance.updated_at.isoformat(),
                    instance.completed_at.isoformat() if instance.completed_at else None,
                    instance.completion_reason,
                ),
            )
            await db.commit()

    async def load(self, instance_id: str) -> PipelineInstance | None:
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM pipeline_instances WHERE id = ?",
                (instance_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_instance(row)

    async def load_active(self) -> list[PipelineInstance]:
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM pipeline_instances "
                "WHERE state IN ('pending', 'running', 'paused') "
                "ORDER BY started_at ASC",
            )
            rows = await cursor.fetchall()
        return [self._row_to_instance(row) for row in rows]

    async def load_by_name(self, pipeline_name: str) -> list[PipelineInstance]:
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM pipeline_instances WHERE pipeline_name = ? "
                "ORDER BY started_at DESC",
                (pipeline_name,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_instance(row) for row in rows]

    @staticmethod
    def _row_to_instance(row: aiosqlite.Row) -> PipelineInstance:
        return PipelineInstance(
            id=row["id"],
            pipeline_name=row["pipeline_name"],
            working_doc_path=row["working_doc_path"],
            goal=row["goal"],
            state=PipelineInstanceState(row["state"]),
            parent_session_id=row["parent_session_id"],
            parent_task_id=row["parent_task_id"],
            intent_duration=row["intent_duration"] or "indefinite",
            started_at=datetime.fromisoformat(row["started_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            completion_reason=row["completion_reason"],
        )


# ── Trigger state persistence ────────────────────────────────────────────


class TriggerStateStore:
    """Persistence layer for ``trigger_state`` — last-fire rows per trigger."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def save_last_fired(
        self,
        trigger_id: str,
        pipeline_name: str,
        fired_at: datetime,
        *,
        reason: str | None = None,
        next_eligible_at: datetime | None = None,
    ) -> None:
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                """
                INSERT INTO trigger_state (
                    trigger_id, pipeline_name, last_fired_at,
                    last_fire_reason, next_eligible_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trigger_id) DO UPDATE SET
                    last_fired_at = excluded.last_fired_at,
                    last_fire_reason = excluded.last_fire_reason,
                    next_eligible_at = excluded.next_eligible_at
                """,
                (
                    trigger_id,
                    pipeline_name,
                    fired_at.isoformat(),
                    reason,
                    next_eligible_at.isoformat() if next_eligible_at else None,
                ),
            )
            await db.commit()

    async def load_all(self) -> dict[str, datetime]:
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                "SELECT trigger_id, last_fired_at FROM trigger_state"
            )
            rows = await cursor.fetchall()
        return {
            row[0]: datetime.fromisoformat(row[1]) for row in rows if row[1]
        }


# ── Convenience: load full system state for dispatcher resume ─────────────


async def snapshot_non_terminal_tasks(db_path: Path) -> Iterable[dict[str, Any]]:
    """Return raw rows of in-flight tasks (helper for tests/inspection)."""
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, state, stage_name, pipeline_instance_id, task_preset "
            "FROM worker_tasks "
            "WHERE state NOT IN ('completed', 'failed', 'cancelled')"
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]
