"""Life OS Stabilization Mode service.

This module is intentionally local to ``kora_v2/life``. Core DI, DB
migrations, and tool registration are owned by the integration pass, but the
service still creates its required tables idempotently so unit tests and manual
runtime probes can prove durable behavior.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, Field

SupportMode = Literal["normal", "high_support", "stabilization", "quiet", "recovery", "prep"]
SupportModeStatus = Literal["active", "exited"]


class StabilizationReason(BaseModel):
    """Structured reason for entering or suggesting Stabilization Mode."""

    trigger: str
    user_report: str | None = None
    load_band: str | None = None
    warning_signs: list[str] = Field(default_factory=list)
    preserve_commitments: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SupportModeState(BaseModel):
    """Current or historical support-mode row."""

    id: str
    mode: SupportMode
    status: SupportModeStatus
    started_at: datetime
    ended_at: datetime | None = None
    trigger_event_id: str | None = None
    load_assessment_id: str | None = None
    reason: str | None = None
    user_confirmed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def suppress_optional_work(self) -> bool:
        return bool(self.metadata.get("suppress_optional_work", False))


class StabilizationModeService:
    """Enter, exit, and inspect Life OS Stabilization Mode."""

    source_service = "StabilizationModeService"

    def __init__(self, db_path: Path, day_plan_service: Any | None = None) -> None:
        self._db_path = Path(db_path)
        self._day_plan_service = day_plan_service

    async def maybe_enter(self, reason: StabilizationReason) -> SupportModeState | None:
        """Enter only when the reason crosses the stabilization threshold."""

        if not _should_enter(reason):
            return None
        return await self.enter(reason, user_confirmed=False)

    async def enter(
        self,
        reason: StabilizationReason,
        *,
        user_confirmed: bool,
    ) -> SupportModeState:
        """Enter Stabilization Mode and durably suppress optional work."""

        await self._ensure_schema()
        now = _now()
        state_id = _new_id("support-mode")
        metadata: dict[str, Any] = {
            "suppress_optional_work": True,
            "reduced_plan_created": False,
            "preserve_commitments": reason.preserve_commitments,
            **reason.metadata,
        }
        reduced_plan_id = await self._create_reduced_day_plan(reason)
        if reduced_plan_id:
            metadata["reduced_plan_created"] = True
            metadata["reduced_day_plan_id"] = reduced_plan_id

        state = SupportModeState(
            id=state_id,
            mode="stabilization",
            status="active",
            started_at=now,
            reason=reason.trigger,
            user_confirmed=user_confirmed,
            metadata=metadata,
        )

        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                UPDATE support_mode_state
                SET status = 'exited', ended_at = ?
                WHERE status = 'active'
                """,
                (now.isoformat(),),
            )
            await db.execute(
                """
                INSERT INTO support_mode_state
                    (id, mode, status, started_at, ended_at, trigger_event_id,
                     load_assessment_id, reason, user_confirmed, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.id,
                    state.mode,
                    state.status,
                    state.started_at.isoformat(),
                    None,
                    state.trigger_event_id,
                    state.load_assessment_id,
                    state.reason,
                    1 if state.user_confirmed else 0,
                    _json(state.metadata),
                ),
            )
            await _record_domain_event(
                db,
                event_type="STABILIZATION_MODE_ENTERED",
                aggregate_type="support_mode_state",
                aggregate_id=state.id,
                source_service=self.source_service,
                payload={
                    "reason": reason.model_dump(),
                    "user_confirmed": user_confirmed,
                    "suppress_optional_work": True,
                    "reduced_day_plan_id": reduced_plan_id,
                },
            )
            await db.commit()

        return state

    async def exit(self, reason: str) -> SupportModeState:
        """Exit the active support mode and return the updated state."""

        await self._ensure_schema()
        current = await self.current_mode()
        if current.mode == "normal" or current.status != "active":
            return current

        ended_at = _now()
        metadata = {
            **current.metadata,
            "suppress_optional_work": False,
            "exit_reason": reason,
        }
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                UPDATE support_mode_state
                SET status = 'exited', ended_at = ?, metadata = ?
                WHERE id = ?
                """,
                (ended_at.isoformat(), _json(metadata), current.id),
            )
            await _record_domain_event(
                db,
                event_type="STABILIZATION_MODE_EXITED",
                aggregate_type="support_mode_state",
                aggregate_id=current.id,
                source_service=self.source_service,
                payload={"reason": reason},
            )
            await db.commit()

        return current.model_copy(
            update={"status": "exited", "ended_at": ended_at, "metadata": metadata}
        )

    async def current_mode(self) -> SupportModeState:
        """Return the active support mode, or a synthetic normal state."""

        await self._ensure_schema()
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM support_mode_state
                WHERE status = 'active'
                ORDER BY started_at DESC
                LIMIT 1
                """
            )
            row = await cursor.fetchone()

        if row is None:
            return SupportModeState(
                id="support-mode-normal",
                mode="normal",
                status="active",
                started_at=_now(),
                metadata={"suppress_optional_work": False},
            )
        return _support_mode_from_row(row)

    async def suppress_optional_work(self) -> bool:
        """True when optional productivity work should be suppressed."""

        return (await self.current_mode()).suppress_optional_work

    async def _create_reduced_day_plan(self, reason: StabilizationReason) -> str | None:
        if self._day_plan_service is None:
            return None

        entries = [
            {"title": "Medication or health basics", "kind": "essential", "optional": False},
            {"title": "Food and hydration", "kind": "essential", "optional": False},
            {"title": "Body care", "kind": "maintenance", "optional": False},
            {"title": "One required obligation", "kind": "fixed", "optional": False},
            {"title": "One recovery action", "kind": "recovery", "optional": False},
            {"title": "One tiny next step", "kind": "tiny_next_step", "optional": True},
        ]
        payload = {
            "mode": "stabilization",
            "reason": reason.model_dump(),
            "entries": entries,
        }

        for method_name in (
            "create_stabilization_plan",
            "create_reduced_day_plan",
            "create_day_plan",
            "create_plan",
        ):
            method = getattr(self._day_plan_service, method_name, None)
            if method is None:
                continue
            result = await method(payload)
            return _extract_id(result)
        return None

    async def _ensure_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS support_mode_state (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    trigger_event_id TEXT,
                    load_assessment_id TEXT,
                    reason TEXT,
                    user_confirmed INTEGER DEFAULT 0,
                    metadata TEXT
                )
                """
            )
            await _ensure_domain_events_schema(db)
            await db.commit()


def _should_enter(reason: StabilizationReason) -> bool:
    trigger = reason.trigger.lower()
    report = (reason.user_report or "").lower()
    if reason.load_band in {"high", "severe", "overload"}:
        return True
    if len(reason.warning_signs) >= 2:
        return True
    return any(
        token in f"{trigger} {report}"
        for token in (
            "overload",
            "shutdown",
            "low energy",
            "can't function",
            "cannot function",
            "burnout",
            "panic",
            "depression",
        )
    )


def _support_mode_from_row(row: aiosqlite.Row) -> SupportModeState:
    return SupportModeState(
        id=row["id"],
        mode=row["mode"],
        status=row["status"],
        started_at=datetime.fromisoformat(row["started_at"]),
        ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
        trigger_event_id=row["trigger_event_id"],
        load_assessment_id=row["load_assessment_id"],
        reason=row["reason"],
        user_confirmed=bool(row["user_confirmed"]),
        metadata=json.loads(row["metadata"] or "{}"),
    )


async def _ensure_domain_events_schema(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS domain_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            aggregate_type TEXT NOT NULL,
            aggregate_id TEXT,
            source_service TEXT NOT NULL,
            correlation_id TEXT,
            causation_id TEXT,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_domain_events_type_created
        ON domain_events(event_type, created_at)
        """
    )
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_domain_events_aggregate
        ON domain_events(aggregate_type, aggregate_id, created_at)
        """
    )


async def _record_domain_event(
    db: aiosqlite.Connection,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str | None,
    source_service: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> str:
    event_id = _new_id("domain-event")
    await db.execute(
        """
        INSERT INTO domain_events
            (id, event_type, aggregate_type, aggregate_id, source_service,
             correlation_id, causation_id, payload, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            aggregate_type,
            aggregate_id,
            source_service,
            correlation_id,
            causation_id,
            _json(payload),
            _now().isoformat(),
        ),
    )
    return event_id


def _extract_id(result: Any) -> str | None:
    if result is None:
        return None
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        value = result.get("id") or result.get("day_plan_id") or result.get("plan_id")
        return str(value) if value else None
    value = getattr(result, "id", None) or getattr(result, "day_plan_id", None)
    return str(value) if value else None


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(UTC)
