"""Checkpoint persistence — spec §7.

Checkpoints snapshot a :class:`WorkerTask`'s progress so the dispatcher
can resume work after a crash, a user-requested pause, or a state
transition that forced a task out of its allowed phase.

The storage format is JSON blobs written to
``worker_tasks.checkpoint_blob``. We keep things small on purpose
(typical <10KB) so writes are cheap and the dispatcher can checkpoint
aggressively — every ``checkpoint_every_seconds`` on the task's config,
plus opportunistically whenever a step function returns a "paused"
outcome.

Resume goes through :meth:`CheckpointStore.load`; the dispatcher feeds
the returned :class:`~kora_v2.runtime.orchestration.worker_task.Checkpoint`
back into the step function via :class:`StepContext.extras`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

from kora_v2.runtime.orchestration.worker_task import (
    Checkpoint,
    WorkerTaskState,
)

log = structlog.get_logger(__name__)


class CheckpointStore:
    """Read/write :class:`Checkpoint` rows on ``worker_tasks``."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def save(self, checkpoint: Checkpoint) -> None:
        """Write *checkpoint* as JSON into ``worker_tasks.checkpoint_blob``."""
        blob = json.dumps(
            self._to_dict(checkpoint),
            default=str,
        )
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                "UPDATE worker_tasks "
                "SET checkpoint_blob = ?, last_checkpoint_at = ? "
                "WHERE id = ?",
                (blob, now, checkpoint.task_id),
            )
            await db.commit()
        log.debug(
            "checkpoint_saved",
            task_id=checkpoint.task_id,
            step_index=checkpoint.current_step_index,
            size_bytes=len(blob),
        )

    async def load(self, task_id: str) -> Checkpoint | None:
        """Return the most recent checkpoint for *task_id*, or ``None``."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT checkpoint_blob FROM worker_tasks WHERE id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
        if row is None or not row["checkpoint_blob"]:
            return None
        try:
            data = json.loads(row["checkpoint_blob"])
        except json.JSONDecodeError:
            log.warning("checkpoint_blob_corrupt", task_id=task_id)
            return None
        return self._from_dict(task_id, data)

    async def clear(self, task_id: str) -> None:
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                "UPDATE worker_tasks SET checkpoint_blob = NULL WHERE id = ?",
                (task_id,),
            )
            await db.commit()

    # ── Serialisation helpers ────────────────────────────────────

    @staticmethod
    def _to_dict(checkpoint: Checkpoint) -> dict[str, Any]:
        return {
            "task_id": checkpoint.task_id,
            "created_at": checkpoint.created_at.isoformat(),
            "state": checkpoint.state.value,
            "current_step_index": checkpoint.current_step_index,
            "plan": checkpoint.plan,
            "accumulated_artifacts": list(checkpoint.accumulated_artifacts),
            "working_doc_mtime": checkpoint.working_doc_mtime,
            "scratch_state": checkpoint.scratch_state,
            "request_count": checkpoint.request_count,
            "agent_turn_count": checkpoint.agent_turn_count,
        }

    @staticmethod
    def _from_dict(task_id: str, data: dict[str, Any]) -> Checkpoint:
        return Checkpoint(
            task_id=data.get("task_id", task_id),
            created_at=datetime.fromisoformat(
                data.get("created_at", datetime.now(UTC).isoformat())
            ),
            state=WorkerTaskState(data.get("state", WorkerTaskState.PENDING.value)),
            current_step_index=int(data.get("current_step_index", 0)),
            plan=data.get("plan"),
            accumulated_artifacts=list(data.get("accumulated_artifacts", [])),
            working_doc_mtime=float(data.get("working_doc_mtime", 0.0)),
            scratch_state=dict(data.get("scratch_state", {})),
            request_count=int(data.get("request_count", 0)),
            agent_turn_count=int(data.get("agent_turn_count", 0)),
        )
