"""Slice 7.5c §17.7b — idempotent legacy autonomous checkpoint migration.

Before the 7.5c rearchitecture the autonomous runtime owned its own
``autonomous_checkpoints`` table (see :mod:`kora_v2.autonomous.checkpoint`).
Every row on that table was a full JSON dump of an
:class:`~kora_v2.autonomous.state.AutonomousCheckpoint` — session id,
plan id, the latest :class:`AutonomousState`, and the
``resume_token``. That table remains on disk after the migration: we
do not drop it, because a user might still be mid-flight when the
upgrade lands and we need their state preserved exactly.

This module walks the legacy rows once at engine-start time and, for
each *unique* ``(session_id, plan_id)`` pair whose latest checkpoint is
**not** in a terminal status ("completed", "cancelled", "failed"),
writes one matching record into the new orchestration tables:

* a :class:`PipelineInstance` row in ``pipeline_instances`` with
  ``pipeline_name="user_autonomous_task"``, ``state=PAUSED``, and the
  user's goal lifted from ``state.metadata["goal"]``
* a :class:`WorkerTask` row in ``worker_tasks`` with
  ``preset="long_background"``, ``stage_name="plan"``,
  ``state=PAUSED_FOR_STATE``, pointing at the new instance
* a :class:`Checkpoint` blob via :class:`CheckpointStore` whose
  ``scratch_state`` matches exactly what
  :func:`kora_v2.autonomous.pipeline_factory._load_or_init_state`
  would produce on its first tick — that is, the
  ``_SCRATCH_INITIALISED_KEY`` flag is true, ``_SCRATCH_STATE_KEY``
  holds a ``state.model_dump(mode="json")``, and the wall-clock /
  watchdog bookkeeping is primed so the watchdog does not trip

Idempotence is provided by a marker row on ``work_ledger`` with
``event_type='autonomous_checkpoint_migration_complete'``. On the first
successful run we append the marker; on every subsequent run we see
the marker and return immediately. This makes the migration safe to
call from :meth:`OrchestrationEngine.start`, which runs on every
daemon boot.

If the ``autonomous_checkpoints`` table does not exist (new install,
orchestration-only test DB), the scan query raises
:class:`aiosqlite.OperationalError`; we swallow it, record the
marker, and return.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite
import structlog

from kora_v2.autonomous.state import AutonomousCheckpoint
from kora_v2.runtime.orchestration.ledger import WorkLedger
from kora_v2.runtime.orchestration.pipeline import (
    PipelineInstance,
    PipelineInstanceState,
)
from kora_v2.runtime.orchestration.worker_task import (
    Checkpoint,
    WorkerTask,
    WorkerTaskState,
    get_preset,
)

if TYPE_CHECKING:
    from kora_v2.runtime.orchestration.checkpointing import CheckpointStore
    from kora_v2.runtime.orchestration.registry import (
        PipelineInstanceRegistry,
        WorkerTaskRegistry,
    )

log = structlog.get_logger(__name__)


# Statuses that mean "do not resume" — the legacy loop has already
# reached a terminal state and there is nothing for the orchestration
# engine to pick up. Mirrors
# :data:`kora_v2.autonomous.graph.TERMINAL_STATUSES` but kept local so
# this module does not create a hard import dependency on ``graph.py``
# (which is scheduled for deletion in §17.10).
_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "cancelled", "failed"})


# Ledger event type used as the idempotency marker. Matches the
# convention in :class:`LedgerEventType` but stays local so reruns
# never hit a typo mismatch.
MIGRATION_MARKER_EVENT = "autonomous_checkpoint_migration_complete"
MIGRATION_MARKER_REASON = "idempotent_marker"


# Scratch state keys — kept in sync with
# :mod:`kora_v2.autonomous.pipeline_factory`. We copy the literals here
# instead of importing them so the two modules stay loosely coupled;
# any drift is caught by the preservation-contract test in
# ``tests/integration/orchestration/test_preservation_contract.py``.
_SCRATCH_STATE_KEY = "autonomous_state"
_SCRATCH_PREV_NODE_KEY = "prev_node"
_SCRATCH_SAME_NODE_REPEATS_KEY = "consecutive_same_node"
_SCRATCH_WALL_START_KEY = "wall_start_epoch"
_SCRATCH_LAST_CHECKPOINT_KEY = "last_checkpoint_epoch"
_SCRATCH_INITIALISED_KEY = "initialised"


async def migrate_legacy_autonomous_checkpoints(
    *,
    db_path: Path,
    ledger: WorkLedger,
    task_registry: WorkerTaskRegistry,
    instance_registry: PipelineInstanceRegistry,
    checkpoint_store: CheckpointStore,
) -> int:
    """Migrate in-flight legacy autonomous checkpoints into orchestration tables.

    Args:
        db_path: Operational DB path — the same database that owns
            both the legacy ``autonomous_checkpoints`` table and the
            new ``worker_tasks`` / ``pipeline_instances`` / ``work_ledger``
            tables.
        ledger: Orchestration work ledger, used to read/write the
            idempotency marker.
        task_registry: Where migrated :class:`WorkerTask` rows are
            persisted.
        instance_registry: Where migrated :class:`PipelineInstance`
            rows are persisted.
        checkpoint_store: Writes the task's scratch-state checkpoint.

    Returns:
        The number of ``(session_id, plan_id)`` pairs that were
        migrated. Returns ``0`` when the marker is already present,
        when the legacy table does not exist, or when the scan finds
        no in-flight rows.

    The whole function is best-effort: any unexpected exception is
    logged and swallowed, because engine start must never fail on a
    migration issue. The caller guards this with a ``try/except`` too,
    but we still catch inline so that a partial run does not leave
    the marker un-written.
    """
    if await _marker_exists(db_path):
        log.debug("autonomous_migration_already_complete")
        return 0

    try:
        legacy_rows = await _load_legacy_rows(db_path)
    except aiosqlite.OperationalError as exc:
        # Table does not exist (orchestration-only test DBs, fresh
        # install). Record the marker so we do not retry on every
        # boot.
        log.debug(
            "autonomous_migration_no_legacy_table",
            error=str(exc),
        )
        await _write_marker(ledger, migrated_count=0, note="no_legacy_table")
        return 0

    if not legacy_rows:
        log.debug("autonomous_migration_no_rows")
        await _write_marker(ledger, migrated_count=0, note="no_rows")
        return 0

    # Pick one checkpoint per (session_id, plan_id) — the legacy table
    # keeps history, we only care about the latest snapshot per pair.
    latest: dict[tuple[str, str], AutonomousCheckpoint] = {}
    for raw_json in legacy_rows:
        try:
            cp = AutonomousCheckpoint.model_validate_json(raw_json)
        except Exception:  # noqa: BLE001
            log.warning("autonomous_migration_row_parse_failed", exc_info=True)
            continue
        key = (cp.session_id, cp.plan_id)
        # ``load_legacy_rows`` orders DESC by ``created_at``, so the
        # first entry we see for a key is the newest.
        latest.setdefault(key, cp)

    migrated = 0
    for (session_id, plan_id), checkpoint in latest.items():
        state = checkpoint.state
        if state.status in _TERMINAL_STATUSES:
            log.debug(
                "autonomous_migration_skip_terminal",
                session_id=session_id,
                plan_id=plan_id,
                status=state.status,
            )
            continue

        try:
            await _migrate_one(
                checkpoint=checkpoint,
                instance_registry=instance_registry,
                task_registry=task_registry,
                checkpoint_store=checkpoint_store,
                ledger=ledger,
            )
            migrated += 1
        except Exception:  # noqa: BLE001
            log.exception(
                "autonomous_migration_row_failed",
                session_id=session_id,
                plan_id=plan_id,
            )
            # Keep going — one bad row should not block the rest.

    await _write_marker(
        ledger,
        migrated_count=migrated,
        note=f"migrated={migrated}",
    )
    log.info("autonomous_migration_complete", migrated=migrated)
    return migrated


# ── Internals ───────────────────────────────────────────────────────────


async def _marker_exists(db_path: Path) -> bool:
    """Return True if the idempotency marker is already in ``work_ledger``."""
    try:
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT 1 FROM work_ledger WHERE event_type = ? LIMIT 1",
                (MIGRATION_MARKER_EVENT,),
            )
            row = await cursor.fetchone()
            return row is not None
    except aiosqlite.OperationalError:
        # work_ledger table itself does not exist — engine has not
        # run init_orchestration_schema yet, which should not happen
        # because start() calls that first. Be defensive anyway.
        return False


async def _load_legacy_rows(db_path: Path) -> list[str]:
    """Return the ``plan_json`` column for every legacy checkpoint row.

    Ordered newest-first so the caller's ``setdefault`` picks the
    freshest state per ``(session_id, plan_id)`` pair.
    """
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT plan_json FROM autonomous_checkpoints "
            "ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
    return [row[0] for row in rows if row and row[0]]


async def _migrate_one(
    *,
    checkpoint: AutonomousCheckpoint,
    instance_registry: PipelineInstanceRegistry,
    task_registry: WorkerTaskRegistry,
    checkpoint_store: CheckpointStore,
    ledger: WorkLedger,
) -> None:
    """Emit the orchestration rows that resume one legacy session.

    The pipeline instance is created in :data:`PipelineInstanceState.PAUSED`
    and the worker task in :data:`WorkerTaskState.PAUSED_FOR_STATE`. The
    dispatcher's normal resume loop will pick them up on the next tick
    that satisfies the phase gate, at which point the
    ``_autonomous_step_fn`` will read the scratch state we seeded here
    and continue walking the 12-node graph.
    """
    state = checkpoint.state
    goal = state.metadata.get("goal", "")

    now = datetime.now(UTC)
    instance_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    # L3: ``completion_reason`` is semantically "why the instance
    # ended" and must stay ``None`` for in-flight migrated rows. We
    # still tag the migration provenance via the ledger row emitted
    # below (and this function's log line) so auditors can trace
    # where the instance came from without polluting the completion
    # field.
    instance = PipelineInstance(
        id=instance_id,
        pipeline_name="user_autonomous_task",
        working_doc_path="",
        goal=goal,
        state=PipelineInstanceState.PAUSED,
        parent_session_id=state.session_id,
        parent_task_id=None,
        intent_duration="long",
        started_at=now,
        updated_at=now,
        completed_at=None,
        completion_reason=None,
    )
    await instance_registry.save(instance)

    preset = get_preset("long_background")
    task = WorkerTask(
        id=task_id,
        pipeline_instance_id=instance_id,
        stage_name="plan",
        config=preset,
        goal=goal,
        system_prompt="",
        parent_task_id=None,
        depends_on=[],
        state=WorkerTaskState.PAUSED_FOR_STATE,
        request_count=state.request_count,
        agent_turn_count=0,
        cancellation_requested=False,
        created_at=now,
        last_step_at=None,
        last_checkpoint_at=now,
        completed_at=None,
        result_summary=None,
        error_message=None,
        result_acknowledged_at=None,
    )
    await task_registry.save(task)

    # Seed the scratch state exactly like
    # ``_load_or_init_state`` would on a fresh first tick — with
    # the legacy state.model_dump() substituted in so the first
    # real tick skips classify_request() and resumes inside the
    # existing plan. We use ``time.time()`` (wall-clock epoch) here
    # rather than ``time.monotonic()`` so the wall-clock value stays
    # meaningful across daemon restarts — monotonic() is
    # process-relative and resets on every boot, which would corrupt
    # the elapsed-seconds budget axis as soon as the migration
    # completes and the daemon restarts.
    now_epoch = time.time()
    scratch = {
        _SCRATCH_INITIALISED_KEY: True,
        _SCRATCH_STATE_KEY: state.model_dump(mode="json"),
        _SCRATCH_WALL_START_KEY: now_epoch - state.elapsed_seconds,
        _SCRATCH_LAST_CHECKPOINT_KEY: now_epoch,
        _SCRATCH_PREV_NODE_KEY: None,
        _SCRATCH_SAME_NODE_REPEATS_KEY: 0,
    }
    task_checkpoint = Checkpoint(
        task_id=task_id,
        created_at=now,
        state=WorkerTaskState.PAUSED_FOR_STATE,
        current_step_index=state.current_step_index,
        plan=None,
        accumulated_artifacts=list(state.produced_artifact_ids),
        working_doc_mtime=0.0,
        scratch_state=scratch,
        request_count=state.request_count,
        agent_turn_count=0,
    )
    await checkpoint_store.save(task_checkpoint)

    await ledger.record(
        "task_created",
        pipeline_instance_id=instance_id,
        worker_task_id=task_id,
        reason="autonomous_checkpoint_migration",
        metadata={
            "session_id": state.session_id,
            "plan_id": state.plan_id,
            "legacy_status": state.status,
            "elapsed_seconds": state.elapsed_seconds,
            "request_count": state.request_count,
            "resume_token": checkpoint.resume_token,
        },
    )

    log.info(
        "autonomous_migration_row_migrated",
        session_id=state.session_id,
        plan_id=state.plan_id,
        task_id=task_id,
        instance_id=instance_id,
        status=state.status,
    )


async def _write_marker(
    ledger: WorkLedger,
    *,
    migrated_count: int,
    note: str,
) -> None:
    """Append the idempotency marker row to ``work_ledger``."""
    await ledger.record(
        MIGRATION_MARKER_EVENT,
        reason=MIGRATION_MARKER_REASON,
        metadata={"migrated": migrated_count, "note": note},
    )
