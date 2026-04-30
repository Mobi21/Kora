"""Life OS day repair engine.

The repair engine turns plan/reality divergence into durable repair actions and
applies safe internal changes transactionally by creating a new day-plan
revision.  It does not call external calendars or notification transports.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, Field

RepairActionType = Literal[
    "reschedule_task",
    "shrink_task",
    "add_transition_buffer",
    "insert_recovery_block",
    "defer_nonessential",
    "enter_stabilization",
]


class PlanRealityDivergence(BaseModel):
    divergence_type: str
    title: str
    reason: str
    day_plan_id: str
    day_plan_entry_id: str | None = None
    item_id: str | None = None
    calendar_entry_id: str | None = None
    source_event_id: str | None = None
    severity: float = 0.0


class RepairEvaluation(BaseModel):
    day: date
    day_plan_id: str | None
    load_assessment_id: str | None = None
    divergences: list[PlanRealityDivergence] = Field(default_factory=list)


class PlanRepairAction(BaseModel):
    id: str
    day_plan_id: str
    action_type: RepairActionType
    status: str
    title: str
    reason: str
    source_event_id: str | None = None
    load_assessment_id: str | None = None
    target_calendar_entry_id: str | None = None
    target_item_id: str | None = None
    target_day_plan_entry_id: str | None = None
    proposed_changes: dict[str, Any]
    requires_confirmation: bool = False
    idempotency_key: str
    applied_at: datetime | None = None
    rejected_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RepairResult(BaseModel):
    applied_action_ids: list[str] = Field(default_factory=list)
    skipped_action_ids: list[str] = Field(default_factory=list)
    new_day_plan_id: str | None = None
    superseded_day_plan_id: str | None = None
    life_event_ids: list[str] = Field(default_factory=list)
    domain_event_ids: list[str] = Field(default_factory=list)


class DayRepairEngine:
    """Detects divergence, proposes repairs, and applies safe revisions."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    async def evaluate(self, day: date) -> RepairEvaluation:
        await self.ensure_schema()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            plan = await _active_day_plan(db, day)
            if plan is None:
                return RepairEvaluation(day=day, day_plan_id=None)

            entries = await _plan_entries(db, plan["id"])
            events = await _life_events_for_day(db, day)
            load = await _latest_load_for_day(db, day)
            divergences = self._detect_divergences(plan, entries, events, load)

            if divergences:
                await _record_domain_event(
                    db,
                    event_type="PLAN_REALITY_DIVERGED",
                    aggregate_type="day_plan",
                    aggregate_id=plan["id"],
                    source_service="DayRepairEngine",
                    payload={
                        "plan_date": day.isoformat(),
                        "divergence_count": len(divergences),
                        "divergence_types": [d.divergence_type for d in divergences],
                    },
                )
                await db.commit()

        return RepairEvaluation(
            day=day,
            day_plan_id=plan["id"],
            load_assessment_id=load["id"] if load else None,
            divergences=divergences,
        )

    async def propose(self, evaluation: RepairEvaluation) -> list[PlanRepairAction]:
        if evaluation.day_plan_id is None:
            return []
        await self.ensure_schema()
        actions = [
            self._action_from_divergence(evaluation, divergence)
            for divergence in evaluation.divergences
        ]
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            for action in actions:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO plan_repair_actions
                        (id, day_plan_id, action_type, status, title, reason,
                         source_event_id, load_assessment_id,
                         target_calendar_entry_id, target_item_id,
                         target_day_plan_entry_id, proposed_changes,
                         requires_confirmation, idempotency_key, applied_at,
                         rejected_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        action.id,
                        action.day_plan_id,
                        action.action_type,
                        action.status,
                        action.title,
                        action.reason,
                        action.source_event_id,
                        action.load_assessment_id,
                        action.target_calendar_entry_id,
                        action.target_item_id,
                        action.target_day_plan_entry_id,
                        _json(action.proposed_changes),
                        int(action.requires_confirmation),
                        action.idempotency_key,
                        action.created_at.isoformat(),
                        action.updated_at.isoformat(),
                    ),
                )
            if actions:
                await _record_domain_event(
                    db,
                    event_type="PLAN_REPAIR_ACTIONS_PROPOSED",
                    aggregate_type="day_plan",
                    aggregate_id=evaluation.day_plan_id,
                    source_service="DayRepairEngine",
                    payload={
                        "action_ids": [a.id for a in actions],
                        "action_types": [a.action_type for a in actions],
                    },
                )
            await db.commit()

        return actions

    async def apply(
        self,
        action_ids: list[str],
        *,
        user_confirmed: bool = False,
    ) -> RepairResult:
        await self.ensure_schema()
        if not action_ids:
            return RepairResult()

        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            actions = await _actions_by_ids(db, action_ids)
            if not actions:
                await db.commit()
                return RepairResult(skipped_action_ids=action_ids)

            applicable: list[aiosqlite.Row] = []
            skipped: list[str] = []
            for action in actions:
                if action["status"] == "applied":
                    skipped.append(action["id"])
                elif action["requires_confirmation"] and not user_confirmed:
                    await db.execute(
                        "UPDATE plan_repair_actions SET status = 'awaiting_confirmation', updated_at = ? WHERE id = ?",
                        (_now().isoformat(), action["id"]),
                    )
                    skipped.append(action["id"])
                else:
                    applicable.append(action)

            if not applicable:
                await db.commit()
                return RepairResult(skipped_action_ids=skipped)

            plan_id = applicable[0]["day_plan_id"]
            plan = await _day_plan_by_id(db, plan_id)
            if plan is None:
                await db.rollback()
                raise ValueError(f"Unknown day plan: {plan_id}")
            if any(action["day_plan_id"] != plan_id for action in applicable):
                await db.rollback()
                raise ValueError("Cannot apply repair actions from multiple day plans together")

            now = _now()
            new_plan_id = _id("dayplan")
            next_revision = int(plan["revision"]) + 1
            await db.execute(
                """
                UPDATE day_plans
                SET status = 'superseded', updated_at = ?
                WHERE id = ?
                """,
                (now.isoformat(), plan_id),
            )
            await db.execute(
                """
                INSERT INTO day_plans
                    (id, plan_date, revision, status, supersedes_day_plan_id,
                     generated_from, load_assessment_id, summary, created_at,
                     updated_at)
                VALUES (?, ?, ?, 'active', ?, 'repair', ?, ?, ?, ?)
                """,
                (
                    new_plan_id,
                    plan["plan_date"],
                    next_revision,
                    plan_id,
                    applicable[-1]["load_assessment_id"] or plan["load_assessment_id"],
                    f"Repair revision from {len(applicable)} action(s)",
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

            id_map = await _copy_entries(db, plan_id, new_plan_id, now)
            life_event_ids: list[str] = []
            domain_event_ids: list[str] = []
            for action in applicable:
                changes = _loads(action["proposed_changes"], {})
                target_entry = action["target_day_plan_entry_id"]
                new_entry_id = id_map.get(target_entry) if target_entry else None
                await self._apply_single_action(db, action, changes, new_entry_id, now)
                await db.execute(
                    """
                    UPDATE plan_repair_actions
                    SET status = 'applied', applied_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now.isoformat(), now.isoformat(), action["id"]),
                )
                life_event_id = await _record_life_event(
                    db,
                    event_type=f"repair.{action['action_type']}",
                    title=action["title"],
                    details=action["reason"],
                    day_plan_entry_id=new_entry_id,
                    item_id=action["target_item_id"],
                    calendar_entry_id=action["target_calendar_entry_id"],
                    metadata={
                        "repair_action_id": action["id"],
                        "new_day_plan_id": new_plan_id,
                        "proposed_changes": changes,
                    },
                )
                life_event_ids.append(life_event_id)

            event_id = await _record_domain_event(
                db,
                event_type="DAY_PLAN_REPAIRED",
                aggregate_type="day_plan",
                aggregate_id=new_plan_id,
                source_service="DayRepairEngine",
                payload={
                    "superseded_day_plan_id": plan_id,
                    "applied_action_ids": [a["id"] for a in applicable],
                    "life_event_ids": life_event_ids,
                },
            )
            domain_event_ids.append(event_id)
            await db.commit()

        return RepairResult(
            applied_action_ids=[a["id"] for a in applicable],
            skipped_action_ids=skipped,
            new_day_plan_id=new_plan_id,
            superseded_day_plan_id=plan_id,
            life_event_ids=life_event_ids,
            domain_event_ids=domain_event_ids,
        )

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(str(self.db_path)) as db:
            await _ensure_schema(db)
            await db.commit()

    def _detect_divergences(
        self,
        plan: aiosqlite.Row,
        entries: list[aiosqlite.Row],
        events: list[aiosqlite.Row],
        load: aiosqlite.Row | None,
    ) -> list[PlanRealityDivergence]:
        divergences: list[PlanRealityDivergence] = []
        now = _now()
        for entry in entries:
            status = entry["status"]
            reality = entry["reality_state"]
            intended_end = _parse_dt(entry["intended_end"])
            if status in {"planned", "active"} and reality in {"unknown", "not_done", "rejected_inference"}:
                if intended_end and intended_end < now:
                    divergences.append(
                        PlanRealityDivergence(
                            divergence_type="stale_planned_entry",
                            title=entry["title"],
                            reason="planned entry ended without confirmed reality",
                            day_plan_id=plan["id"],
                            day_plan_entry_id=entry["id"],
                            item_id=entry["item_id"],
                            calendar_entry_id=entry["calendar_entry_id"],
                            severity=0.45,
                        )
                    )
            normalized_reality = {
                "confirmed_skipped": "skipped",
                "confirmed_blocked": "blocked",
                "confirmed_partial": "partial",
            }.get(reality, reality)
            if normalized_reality in {"skipped", "blocked", "partial"}:
                divergences.append(
                    PlanRealityDivergence(
                        divergence_type=f"entry_{normalized_reality}",
                        title=entry["title"],
                        reason=f"user reality state is {reality}",
                        day_plan_id=plan["id"],
                        day_plan_entry_id=entry["id"],
                        item_id=entry["item_id"],
                        calendar_entry_id=entry["calendar_entry_id"],
                        severity=0.55,
                    )
                )

        for event in events:
            text = " ".join(
                str(event[key] or "")
                for key in ("event_type", "title", "details", "raw_text")
                if key in event.keys()
            ).lower()
            if "behind" in text or "late" in text:
                divergences.append(
                    PlanRealityDivergence(
                        divergence_type="behind_reported",
                        title=event["title"] or "Behind schedule",
                        reason=event["details"] or "user reported being behind",
                        day_plan_id=plan["id"],
                        source_event_id=event["id"],
                        severity=0.65,
                    )
                )

        if load and load["band"] in {"overloaded", "stabilization"}:
            divergences.append(
                PlanRealityDivergence(
                    divergence_type="load_over_limit",
                    title="Day load over limit",
                    reason=f"current load band is {load['band']}",
                    day_plan_id=plan["id"],
                    severity=0.80,
                )
            )
        return divergences

    def _action_from_divergence(
        self,
        evaluation: RepairEvaluation,
        divergence: PlanRealityDivergence,
    ) -> PlanRepairAction:
        now = _now()
        action_type: RepairActionType
        requires_confirmation = False
        changes: dict[str, Any]

        if divergence.divergence_type in {"load_over_limit"}:
            action_type = "enter_stabilization"
            changes = {"mode": "stabilization", "thin_optional": True}
        elif divergence.divergence_type == "behind_reported":
            action_type = "add_transition_buffer"
            changes = {"buffer_minutes": 15, "status": "rescheduled"}
        elif divergence.divergence_type == "entry_blocked":
            action_type = "defer_nonessential"
            changes = {"status": "deferred", "item_status": "deferred"}
        elif divergence.divergence_type == "entry_partial":
            action_type = "shrink_task"
            changes = {"title_suffix": " - first move", "status": "partial"}
        elif divergence.divergence_type == "entry_skipped":
            action_type = "defer_nonessential"
            changes = {"status": "deferred", "item_status": "deferred"}
        else:
            action_type = "shrink_task"
            changes = {"title_suffix": " - smaller next step", "status": "rescheduled"}

        if divergence.calendar_entry_id and action_type in {"defer_nonessential", "reschedule_task"}:
            requires_confirmation = True

        idem = "|".join(
            [
                evaluation.day_plan_id or "",
                divergence.divergence_type,
                divergence.day_plan_entry_id or "",
                divergence.source_event_id or "",
                action_type,
            ]
        )
        return PlanRepairAction(
            id=_id("repair"),
            day_plan_id=evaluation.day_plan_id or divergence.day_plan_id,
            action_type=action_type,
            status="awaiting_confirmation" if requires_confirmation else "proposed",
            title=f"Repair: {divergence.title}",
            reason=divergence.reason,
            source_event_id=divergence.source_event_id,
            load_assessment_id=evaluation.load_assessment_id,
            target_calendar_entry_id=divergence.calendar_entry_id,
            target_item_id=divergence.item_id,
            target_day_plan_entry_id=divergence.day_plan_entry_id,
            proposed_changes=changes,
            requires_confirmation=requires_confirmation,
            idempotency_key=idem,
            created_at=now,
            updated_at=now,
        )

    async def _apply_single_action(
        self,
        db: aiosqlite.Connection,
        action: aiosqlite.Row,
        changes: dict[str, Any],
        new_entry_id: str | None,
        now: datetime,
    ) -> None:
        action_type = action["action_type"]
        if new_entry_id:
            if changes.get("title_suffix"):
                await db.execute(
                    "UPDATE day_plan_entries SET title = title || ?, status = ?, updated_at = ? WHERE id = ?",
                    (
                        changes["title_suffix"],
                        changes.get("status", "rescheduled"),
                        now.isoformat(),
                        new_entry_id,
                    ),
                )
            else:
                await db.execute(
                    "UPDATE day_plan_entries SET status = ?, reality_state = ?, updated_at = ? WHERE id = ?",
                    (
                        changes.get("status", "rescheduled"),
                        changes.get("reality_state", "needs_confirmation"),
                        now.isoformat(),
                        new_entry_id,
                    ),
                )

        if action["target_item_id"] and changes.get("item_status"):
            if await _table_exists(db, "items"):
                await db.execute(
                    "UPDATE items SET status = ?, updated_at = ? WHERE id = ?",
                    (changes["item_status"], now.isoformat(), action["target_item_id"]),
                )
                if await _table_exists(db, "item_state_history"):
                    await db.execute(
                        """
                        INSERT INTO item_state_history
                            (item_id, from_status, to_status, reason, recorded_at)
                        VALUES (?, NULL, ?, ?, ?)
                        """,
                        (
                            action["target_item_id"],
                            changes["item_status"],
                            action["reason"],
                            now.isoformat(),
                        ),
                    )

        if action_type == "add_transition_buffer" and await _table_exists(db, "calendar_entries"):
            start = now + timedelta(minutes=5)
            end = start + timedelta(minutes=int(changes.get("buffer_minutes", 15)))
            if not await _transition_buffer_exists(db, action["reason"], now):
                await db.execute(
                    """
                    INSERT INTO calendar_entries
                        (id, kind, title, description, starts_at, ends_at, source,
                         metadata, status, created_at, updated_at)
                    VALUES (?, 'buffer', 'Transition buffer', ?, ?, ?, 'kora',
                            ?, 'active', ?, ?)
                    """,
                    (
                        _id("cal"),
                        action["reason"],
                        start.isoformat(),
                        end.isoformat(),
                        _json({"repair_action_id": action["id"]}),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )

        if action_type == "enter_stabilization":
            await db.execute(
                """
                INSERT INTO support_mode_state
                    (id, mode, status, started_at, ended_at, trigger_event_id,
                     load_assessment_id, reason, user_confirmed, metadata)
                VALUES (?, 'stabilization', 'active', ?, NULL, ?, ?, ?, 0, ?)
                """,
                (
                    _id("mode"),
                    now.isoformat(),
                    action["source_event_id"],
                    action["load_assessment_id"],
                    action["reason"],
                    _json({"repair_action_id": action["id"]}),
                ),
            )


async def _ensure_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS day_plans (
            id TEXT PRIMARY KEY,
            plan_date TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active',
            supersedes_day_plan_id TEXT,
            generated_from TEXT NOT NULL DEFAULT 'conversation',
            load_assessment_id TEXT,
            summary TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS day_plan_entries (
            id TEXT PRIMARY KEY,
            day_plan_id TEXT NOT NULL,
            calendar_entry_id TEXT,
            item_id TEXT,
            title TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            intended_start TEXT,
            intended_end TEXT,
            expected_effort TEXT,
            support_tags TEXT,
            status TEXT NOT NULL DEFAULT 'planned',
            reality_state TEXT NOT NULL DEFAULT 'unknown',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS life_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            event_time TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            confirmation_state TEXT NOT NULL DEFAULT 'confirmed',
            calendar_entry_id TEXT,
            item_id TEXT,
            day_plan_entry_id TEXT,
            support_module TEXT,
            title TEXT,
            details TEXT,
            raw_text TEXT,
            metadata TEXT,
            supersedes_event_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS plan_repair_actions (
            id TEXT PRIMARY KEY,
            day_plan_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            title TEXT NOT NULL,
            reason TEXT NOT NULL,
            source_event_id TEXT,
            load_assessment_id TEXT,
            target_calendar_entry_id TEXT,
            target_item_id TEXT,
            target_day_plan_entry_id TEXT,
            proposed_changes TEXT NOT NULL,
            requires_confirmation INTEGER NOT NULL DEFAULT 0,
            idempotency_key TEXT NOT NULL,
            applied_at TEXT,
            rejected_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_plan_repair_actions_plan_status
            ON plan_repair_actions(day_plan_id, status);
        CREATE TABLE IF NOT EXISTS load_assessments (
            id TEXT PRIMARY KEY,
            assessment_date TEXT NOT NULL,
            score REAL NOT NULL,
            band TEXT NOT NULL,
            confidence REAL NOT NULL,
            factors TEXT NOT NULL,
            recommended_mode TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            confirmed_by_user INTEGER DEFAULT 0
        );
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
        );
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
        );
        """
    )


async def _transition_buffer_exists(
    db: aiosqlite.Connection,
    reason: str,
    now: datetime,
) -> bool:
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    async with db.execute(
        """
        SELECT COUNT(*) AS count
        FROM calendar_entries
        WHERE kind = 'buffer'
          AND title = 'Transition buffer'
          AND source = 'kora'
          AND status = 'active'
          AND created_at >= ?
        """,
        (day_start,),
    ) as cursor:
        row = await cursor.fetchone()
    today_count = int(row["count"] if row is not None else 0)
    if today_count >= 3:
        return True
    async with db.execute(
        """
        SELECT 1
        FROM calendar_entries
        WHERE kind = 'buffer'
          AND title = 'Transition buffer'
          AND source = 'kora'
          AND status = 'active'
          AND description = ?
          AND created_at >= ?
        LIMIT 1
        """,
        (reason, day_start),
    ) as cursor:
        return await cursor.fetchone() is not None


async def _active_day_plan(db: aiosqlite.Connection, day: date) -> aiosqlite.Row | None:
    return await (
        await db.execute(
            """
            SELECT * FROM day_plans
            WHERE plan_date = ? AND status = 'active'
            ORDER BY revision DESC
            LIMIT 1
            """,
            (day.isoformat(),),
        )
    ).fetchone()


async def _day_plan_by_id(db: aiosqlite.Connection, day_plan_id: str) -> aiosqlite.Row | None:
    return await (
        await db.execute("SELECT * FROM day_plans WHERE id = ?", (day_plan_id,))
    ).fetchone()


async def _plan_entries(db: aiosqlite.Connection, day_plan_id: str) -> list[aiosqlite.Row]:
    return await (
        await db.execute(
            "SELECT * FROM day_plan_entries WHERE day_plan_id = ? ORDER BY intended_start, created_at",
            (day_plan_id,),
        )
    ).fetchall()


async def _life_events_for_day(db: aiosqlite.Connection, day: date) -> list[aiosqlite.Row]:
    start = datetime.combine(day, time.min, tzinfo=UTC).isoformat()
    end = datetime.combine(day, time.max, tzinfo=UTC).isoformat()
    return await (
        await db.execute(
            "SELECT * FROM life_events WHERE event_time >= ? AND event_time < ?",
            (start, end),
        )
    ).fetchall()


async def _latest_load_for_day(db: aiosqlite.Connection, day: date) -> aiosqlite.Row | None:
    return await (
        await db.execute(
            """
            SELECT * FROM load_assessments
            WHERE assessment_date = ?
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            (day.isoformat(),),
        )
    ).fetchone()


async def _actions_by_ids(
    db: aiosqlite.Connection,
    action_ids: list[str],
) -> list[aiosqlite.Row]:
    placeholders = ",".join("?" for _ in action_ids)
    return await (
        await db.execute(
            f"SELECT * FROM plan_repair_actions WHERE id IN ({placeholders})",
            tuple(action_ids),
        )
    ).fetchall()


async def _copy_entries(
    db: aiosqlite.Connection,
    old_plan_id: str,
    new_plan_id: str,
    now: datetime,
) -> dict[str, str]:
    id_map: dict[str, str] = {}
    for row in await _plan_entries(db, old_plan_id):
        new_id = _id("entry")
        id_map[row["id"]] = new_id
        await db.execute(
            """
            INSERT INTO day_plan_entries
                (id, day_plan_id, calendar_entry_id, item_id, title,
                 entry_type, intended_start, intended_end, expected_effort,
                 support_tags, status, reality_state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id,
                new_plan_id,
                row["calendar_entry_id"],
                row["item_id"],
                row["title"],
                row["entry_type"],
                row["intended_start"],
                row["intended_end"],
                row["expected_effort"],
                row["support_tags"],
                row["status"],
                row["reality_state"],
                now.isoformat(),
                now.isoformat(),
            ),
        )
    return id_map


async def _record_life_event(
    db: aiosqlite.Connection,
    *,
    event_type: str,
    title: str,
    details: str,
    day_plan_entry_id: str | None,
    item_id: str | None,
    calendar_entry_id: str | None,
    metadata: dict[str, Any],
) -> str:
    event_id = _id("lifeevt")
    now = _now()
    await db.execute(
        """
        INSERT INTO life_events
            (id, event_type, event_time, source, confidence,
             confirmation_state, calendar_entry_id, item_id,
             day_plan_entry_id, support_module, title, details, raw_text,
             metadata, supersedes_event_id, created_at)
        VALUES (?, ?, ?, 'tool', 1.0, 'confirmed', ?, ?, ?, NULL, ?, ?, NULL, ?, NULL, ?)
        """,
        (
            event_id,
            event_type,
            now.isoformat(),
            calendar_entry_id,
            item_id,
            day_plan_entry_id,
            title,
            details,
            _json(metadata),
            now.isoformat(),
        ),
    )
    return event_id


async def _record_domain_event(
    db: aiosqlite.Connection,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str | None,
    source_service: str,
    payload: dict[str, Any],
) -> str:
    event_id = _id("evt")
    await db.execute(
        """
        INSERT INTO domain_events
            (id, event_type, aggregate_type, aggregate_id, source_service,
             correlation_id, causation_id, payload, created_at)
        VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
        """,
        (
            event_id,
            event_type,
            aggregate_type,
            aggregate_id,
            source_service,
            _json(payload),
            _now().isoformat(),
        ),
    )
    return event_id


async def _table_exists(db: aiosqlite.Connection, table: str) -> bool:
    row = await (
        await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        )
    ).fetchone()
    return row is not None


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default
