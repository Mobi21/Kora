"""Central Life OS proactivity policy.

Every candidate nudge should leave a durable decision, including suppressions.
Transport-specific delivery remains outside this module.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, Field

NudgeDecisionValue = Literal[
    "send_now",
    "queue_for_turn",
    "defer",
    "suppress",
    "ask_confirmation",
    "enter_stabilization",
]
Urgency = Literal["low", "normal", "high", "critical"]


class NudgeCandidate(BaseModel):
    candidate_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    urgency: Urgency = "normal"
    support_tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NudgeDecision(BaseModel):
    id: str
    candidate_type: str
    candidate_payload: dict[str, Any]
    decision: NudgeDecisionValue
    reason: str
    urgency: Urgency
    support_tags: list[str] = Field(default_factory=list)
    load_assessment_id: str | None = None
    notification_id: str | None = None
    created_at: datetime


class NudgeFeedbackInput(BaseModel):
    feedback: Literal[
        "helpful",
        "too_much",
        "wrong",
        "bad_timing",
        "done",
        "not_done",
        "reschedule",
        "stop_this_type",
    ]
    details: str = ""


class NotificationResult(BaseModel):
    notification_id: str | None = None
    delivered: bool = False
    reason: str = ""


class ProactivityPolicyEngine:
    """Decides whether Kora should interrupt, queue, defer, or stay quiet."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    async def decide(self, candidate: NudgeCandidate) -> NudgeDecision:
        await self.ensure_schema()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            load = await _latest_load(db)
            mode = await _active_mode(db)
            feedback = await _recent_feedback_for_type(db, candidate.candidate_type)
            decision, reason = _decide(candidate, load, mode, feedback)
            decision_id = _id("nudge")
            created_at = _now()
            await db.execute(
                """
                INSERT INTO nudge_decisions
                    (id, candidate_type, candidate_payload, decision, reason,
                     urgency, support_tags, load_assessment_id, notification_id,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    decision_id,
                    candidate.candidate_type,
                    _json(candidate.payload),
                    decision,
                    reason,
                    candidate.urgency,
                    _json(candidate.support_tags),
                    load["id"] if load else None,
                    created_at.isoformat(),
                ),
            )
            await _record_domain_event(
                db,
                event_type="NUDGE_DECISION_RECORDED",
                aggregate_type="nudge_decision",
                aggregate_id=decision_id,
                source_service="ProactivityPolicyEngine",
                payload={
                    "candidate_type": candidate.candidate_type,
                    "decision": decision,
                    "reason": reason,
                    "urgency": candidate.urgency,
                },
            )
            await db.commit()
        return NudgeDecision(
            id=decision_id,
            candidate_type=candidate.candidate_type,
            candidate_payload=candidate.payload,
            decision=decision,
            reason=reason,
            urgency=candidate.urgency,
            support_tags=candidate.support_tags,
            load_assessment_id=load["id"] if load else None,
            created_at=created_at,
        )

    async def reconcile_delivery(
        self,
        decision_id: str,
        delivery_result: NotificationResult,
    ) -> NudgeDecision:
        await self.ensure_schema()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            row = await _get_decision(db, decision_id)
            if row is None:
                raise ValueError(f"Unknown nudge decision: {decision_id}")
            reason = row["reason"]
            if delivery_result.reason:
                reason = f"{reason}; delivery: {delivery_result.reason}"
            await db.execute(
                "UPDATE nudge_decisions SET notification_id = ?, reason = ? WHERE id = ?",
                (delivery_result.notification_id, reason, decision_id),
            )
            await _record_domain_event(
                db,
                event_type="NUDGE_DELIVERY_RECONCILED",
                aggregate_type="nudge_decision",
                aggregate_id=decision_id,
                source_service="ProactivityPolicyEngine",
                payload=delivery_result.model_dump(),
            )
            await db.commit()
            updated = await _get_decision(db, decision_id)
        if updated is None:
            raise RuntimeError("Nudge decision disappeared during reconciliation")
        return _decision_from_row(updated)

    async def record_feedback(
        self,
        decision_id: str,
        feedback: NudgeFeedbackInput,
    ) -> None:
        await self.ensure_schema()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            if await _get_decision(db, decision_id) is None:
                raise ValueError(f"Unknown nudge decision: {decision_id}")
            feedback_id = _id("nfb")
            await db.execute(
                """
                INSERT INTO nudge_feedback
                    (id, nudge_decision_id, feedback, details, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (feedback_id, decision_id, feedback.feedback, feedback.details, _now().isoformat()),
            )
            await _record_domain_event(
                db,
                event_type="NUDGE_FEEDBACK_RECORDED",
                aggregate_type="nudge_decision",
                aggregate_id=decision_id,
                source_service="ProactivityPolicyEngine",
                payload=feedback.model_dump(),
            )
            await db.commit()

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(str(self.db_path)) as db:
            await _ensure_schema(db)
            await db.commit()


def _decide(
    candidate: NudgeCandidate,
    load: aiosqlite.Row | None,
    mode: aiosqlite.Row | None,
    feedback: list[aiosqlite.Row],
) -> tuple[NudgeDecisionValue, str]:
    if candidate.urgency == "critical":
        return "send_now", "critical urgency bypasses quiet suppression"

    mode_name = mode["mode"] if mode else "normal"
    if mode_name in {"quiet", "stabilization", "recovery"}:
        if candidate.urgency == "high" and _has_tag(candidate, {"health", "safety", "fixed_commitment"}):
            return "queue_for_turn", f"{mode_name} mode allows only essential queued nudges"
        return "suppress", f"{mode_name} mode suppresses optional proactivity"

    if any(row["feedback"] == "stop_this_type" for row in feedback):
        return "suppress", "user asked to stop this nudge type"
    negative = sum(1 for row in feedback if row["feedback"] in {"too_much", "wrong", "bad_timing"})
    if negative >= 2 and candidate.urgency in {"low", "normal"}:
        return "defer", "recent feedback says this nudge type needs less pressure"

    if load and load["band"] in {"stabilization", "overloaded"}:
        if _has_tag(candidate, {"health", "medication", "meal", "fixed_commitment"}):
            return "queue_for_turn", "high load: keep essential nudge visible without hard interrupt"
        return "suppress", f"current load band is {load['band']}"

    if candidate.urgency == "high":
        return "send_now", "high urgency with no active suppression"
    if candidate.urgency == "low":
        return "queue_for_turn", "low urgency should wait for next user turn"
    return "send_now", "normal mode allows timely nudge"


def _has_tag(candidate: NudgeCandidate, tags: set[str]) -> bool:
    return bool(tags.intersection(set(candidate.support_tags)))


async def _latest_load(db: aiosqlite.Connection) -> aiosqlite.Row | None:
    if not await _table_exists(db, "load_assessments"):
        return None
    return await (
        await db.execute(
            "SELECT * FROM load_assessments ORDER BY generated_at DESC LIMIT 1"
        )
    ).fetchone()


async def _active_mode(db: aiosqlite.Connection) -> aiosqlite.Row | None:
    if not await _table_exists(db, "support_mode_state"):
        return None
    return await (
        await db.execute(
            """
            SELECT * FROM support_mode_state
            WHERE status = 'active' AND ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
    ).fetchone()


async def _recent_feedback_for_type(
    db: aiosqlite.Connection,
    candidate_type: str,
) -> list[aiosqlite.Row]:
    if not await _table_exists(db, "nudge_feedback"):
        return []
    since = (_now() - timedelta(days=14)).isoformat()
    return await (
        await db.execute(
            """
            SELECT nf.feedback
            FROM nudge_feedback nf
            JOIN nudge_decisions nd ON nd.id = nf.nudge_decision_id
            WHERE nd.candidate_type = ? AND nf.created_at >= ?
            ORDER BY nf.created_at DESC
            LIMIT 10
            """,
            (candidate_type, since),
        )
    ).fetchall()


async def _get_decision(
    db: aiosqlite.Connection,
    decision_id: str,
) -> aiosqlite.Row | None:
    return await (
        await db.execute("SELECT * FROM nudge_decisions WHERE id = ?", (decision_id,))
    ).fetchone()


def _decision_from_row(row: aiosqlite.Row) -> NudgeDecision:
    return NudgeDecision(
        id=row["id"],
        candidate_type=row["candidate_type"],
        candidate_payload=_loads(row["candidate_payload"], {}),
        decision=row["decision"],
        reason=row["reason"],
        urgency=row["urgency"],
        support_tags=_loads(row["support_tags"], []),
        load_assessment_id=row["load_assessment_id"],
        notification_id=row["notification_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


async def _ensure_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS nudge_decisions (
            id TEXT PRIMARY KEY,
            candidate_type TEXT NOT NULL,
            candidate_payload TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            urgency TEXT NOT NULL,
            support_tags TEXT,
            load_assessment_id TEXT,
            notification_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nudge_feedback (
            id TEXT PRIMARY KEY,
            nudge_decision_id TEXT NOT NULL,
            feedback TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL
        );
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


async def _record_domain_event(
    db: aiosqlite.Connection,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str | None,
    source_service: str,
    payload: dict[str, Any],
) -> None:
    await db.execute(
        """
        INSERT INTO domain_events
            (id, event_type, aggregate_type, aggregate_id, source_service,
             correlation_id, causation_id, payload, created_at)
        VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
        """,
        (
            _id("evt"),
            event_type,
            aggregate_type,
            aggregate_id,
            source_service,
            _json(payload),
            _now().isoformat(),
        ),
    )


async def _table_exists(db: aiosqlite.Connection, table: str) -> bool:
    row = await (
        await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        )
    ).fetchone()
    return row is not None


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(UTC)


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default
