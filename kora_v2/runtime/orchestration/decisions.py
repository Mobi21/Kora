"""Kora V2 — Decision management and open-decisions tracker.

This module is the orchestration-layer home for two related primitives:

1. :class:`DecisionManager` — tracks transient ``PendingDecision`` objects
   held in memory for autonomous pauses. A plan that reaches a branch
   point parks here until the user answers (``submit_answer``) or the
   timeout auto-resolves (``check_timeout``). Moved verbatim from
   ``kora_v2.autonomous.decisions`` per spec §17.7a — no behaviour
   changes, just a relocation so non-autonomous pipelines can reuse it.

2. :class:`OpenDecisionsTracker` — persistent SQL-backed log of decisions
   the supervisor wants to follow up on. Used by §15 and by the
   Phase 8a ``DECISION_PENDING_3D`` trigger (publisher is deferred to
   8a; the tracker + schema ship here).

Policies for the in-memory manager:
    ``auto_select``  — auto-resolve to *recommendation* on timeout.
    ``never_auto``   — block indefinitely until the user responds; the
                       timeout is tracked but never triggers resolution.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import aiosqlite
import structlog
from pydantic import BaseModel, Field

from kora_v2.runtime.orchestration.ledger import LedgerEventType, WorkLedger

if TYPE_CHECKING:
    from kora_v2.core.events import EventEmitter

log = structlog.get_logger(__name__)


# ── In-memory decision manager (moved from autonomous/decisions.py) ──────


class PendingDecision(BaseModel):
    """A decision waiting for user or auto-resolution."""

    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    options: list[str]
    recommendation: str | None = None
    policy: Literal["auto_select", "never_auto"] = "auto_select"
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DecisionResult(BaseModel):
    """The resolved outcome of a ``PendingDecision``."""

    decision_id: str
    chosen: str
    method: Literal["user", "auto_select", "timeout"]
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DecisionManager:
    """Lifecycle manager for ``PendingDecision`` objects.

    Typical flow::

        decision = manager.create_decision(
            options=["proceed", "skip", "abort"],
            recommendation="proceed",
            policy="auto_select",
            timeout_minutes=10,
        )
        # ... later, on user input:
        result = manager.submit_answer(decision.decision_id, chosen="skip")
        # ... or, during a periodic check:
        result = manager.check_timeout(decision)  # None if not yet expired
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingDecision] = {}

    # ── Public API ────────────────────────────────────────────────────

    def create_decision(
        self,
        options: list[str],
        recommendation: str | None = None,
        policy: Literal["auto_select", "never_auto"] = "auto_select",
        timeout_minutes: int = 10,
    ) -> PendingDecision:
        """Create and register a new pending decision."""
        if not options:
            raise ValueError("options must be a non-empty list")
        if policy == "auto_select" and recommendation is not None:
            if recommendation not in options:
                raise ValueError(
                    f"recommendation {recommendation!r} is not in options {options!r}"
                )

        now = datetime.now(UTC)
        decision = PendingDecision(
            options=options,
            recommendation=recommendation,
            policy=policy,
            expires_at=now + timedelta(minutes=timeout_minutes),
            created_at=now,
        )
        self._pending[decision.decision_id] = decision
        log.info(
            "decision_created",
            decision_id=decision.decision_id,
            policy=policy,
            options=options,
            recommendation=recommendation,
            timeout_minutes=timeout_minutes,
        )
        return decision

    def submit_answer(self, decision_id: str, chosen: str) -> DecisionResult:
        """Record a user-provided answer for a pending decision."""
        decision = self._pending.get(decision_id)
        if decision is None:
            raise KeyError(f"No pending decision with id={decision_id!r}")
        if chosen not in decision.options:
            raise ValueError(
                f"{chosen!r} is not a valid option {decision.options!r}"
            )
        result = DecisionResult(
            decision_id=decision_id,
            chosen=chosen,
            method="user",
        )
        del self._pending[decision_id]
        log.info(
            "decision_resolved", decision_id=decision_id, chosen=chosen, method="user"
        )
        return result

    def check_timeout(self, decision: PendingDecision) -> DecisionResult | None:
        """Check whether *decision* has expired and can be auto-resolved."""
        if decision.policy == "never_auto":
            return None

        if not self.is_expired(decision):
            return None

        chosen = (
            decision.recommendation if decision.recommendation else decision.options[0]
        )
        result = DecisionResult(
            decision_id=decision.decision_id,
            chosen=chosen,
            method="timeout",
        )
        self._pending.pop(decision.decision_id, None)
        log.info(
            "decision_timeout_auto_resolved",
            decision_id=decision.decision_id,
            chosen=chosen,
        )
        return result

    def is_expired(self, decision: PendingDecision) -> bool:
        """Return ``True`` if *decision* has passed its expiry time."""
        return datetime.now(UTC) >= decision.expires_at

    def get_pending(self, decision_id: str) -> PendingDecision | None:
        """Look up a pending decision by ID (``None`` if not found)."""
        return self._pending.get(decision_id)


# ── Persistent open-decisions tracker (spec §15) ─────────────────────────


@dataclass
class OpenDecision:
    """A decision the supervisor has recorded for follow-up."""

    id: str
    topic: str
    posed_at: datetime
    posed_in_session: str | None
    context: str | None
    status: Literal["open", "resolved", "expired", "dismissed"] = "open"
    resolved_at: datetime | None = None
    resolution: str | None = None


class OpenDecisionsTracker:
    """SQL-backed tracker for decisions the user is weighing.

    Schema lives in ``migrations/001_orchestration.sql`` → ``open_decisions``.
    The Phase 8a `DECISION_PENDING_3D` publisher will call
    :meth:`get_pending` with ``older_than_days=3`` to fire its trigger.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        event_emitter: EventEmitter | None = None,
    ) -> None:
        self._db_path = db_path
        self._event_emitter = event_emitter

    async def record(
        self,
        *,
        topic: str,
        context: str,
        posed_in_session: str | None = None,
    ) -> OpenDecision:
        """Insert a new open decision and return it.

        Kwargs-only keeps the engine wrapper and the dispatcher tool
        consistent, and returning the :class:`OpenDecision` itself
        spares callers a second round-trip to pick up the id and
        timestamp.

        Emits :attr:`EventType.OPEN_DECISION_POSED` after the SQL commit
        so the contextual_engagement trigger and any other subscribers
        can react to a freshly-recorded open decision.
        """
        decision_id = f"dec-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                "INSERT INTO open_decisions "
                "(id, topic, posed_at, posed_in_session, context, status) "
                "VALUES (?, ?, ?, ?, ?, 'open')",
                (decision_id, topic, now.isoformat(), posed_in_session, context),
            )
            await db.commit()
        log.info(
            "open_decision_recorded",
            decision_id=decision_id,
            topic=topic,
            session_id=posed_in_session,
        )
        if self._event_emitter is not None:
            # Deferred import keeps the autonomous-runtime package free
            # of core-events import cost for callers that construct a
            # tracker without an emitter (tests, migrations).
            from kora_v2.core.events import EventType

            await self._event_emitter.emit(
                EventType.OPEN_DECISION_POSED,
                decision_id=decision_id,
                topic=topic,
                posed_in_session=posed_in_session,
                context=context,
            )
        return OpenDecision(
            id=decision_id,
            topic=topic,
            posed_at=now,
            posed_in_session=posed_in_session,
            context=context,
        )

    async def resolve(self, decision_id: str, resolution: str) -> None:
        """Mark *decision_id* as resolved with the given resolution text."""
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                "UPDATE open_decisions SET status='resolved', resolved_at=?, "
                "resolution=? WHERE id=?",
                (now, resolution, decision_id),
            )
            await db.commit()
        log.info("open_decision_resolved", decision_id=decision_id)

    async def dismiss(self, decision_id: str) -> None:
        """Mark *decision_id* as dismissed — user rejected the prompt."""
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                "UPDATE open_decisions SET status='dismissed', resolved_at=? "
                "WHERE id=?",
                (now, decision_id),
            )
            await db.commit()
        log.info("open_decision_dismissed", decision_id=decision_id)

    async def expire_older_than(self, days: int) -> list[str]:
        """Flip ``status='expired'`` on all open decisions older than *days*.

        Returns the list of IDs that were expired so callers can emit
        follow-up events or notifications if they want.
        """
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        expired_ids: list[str] = []
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                "SELECT id FROM open_decisions WHERE status='open' AND posed_at < ?",
                (cutoff,),
            )
            rows = await cursor.fetchall()
            expired_ids = [row[0] for row in rows]
            if expired_ids:
                await db.executemany(
                    "UPDATE open_decisions SET status='expired', resolved_at=? "
                    "WHERE id=?",
                    [(datetime.now(UTC).isoformat(), eid) for eid in expired_ids],
                )
                await db.commit()
        if expired_ids:
            log.info(
                "open_decisions_expired",
                count=len(expired_ids),
                days=days,
            )
        return expired_ids

    async def get_pending(
        self,
        older_than_days: int = 0,
        *,
        limit: int | None = None,
    ) -> list[OpenDecision]:
        """Return pending (``status='open'``) decisions older than *older_than_days* days."""
        cutoff = (
            datetime.now(UTC) - timedelta(days=older_than_days)
        ).isoformat() if older_than_days > 0 else datetime.now(UTC).isoformat()

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            limit_clause = " LIMIT ?" if limit is not None else ""
            params: tuple[Any, ...] = (
                (cutoff, limit) if limit is not None else (cutoff,)
            )
            cursor = await db.execute(
                "SELECT id, topic, posed_at, posed_in_session, context, status, "
                "resolved_at, resolution FROM open_decisions "
                "WHERE status='open' AND posed_at <= ? "
                f"ORDER BY posed_at ASC{limit_clause}",
                params,
            )
            rows = await cursor.fetchall()

        results: list[OpenDecision] = []
        for row in rows:
            results.append(
                OpenDecision(
                    id=row["id"],
                    topic=row["topic"],
                    posed_at=datetime.fromisoformat(row["posed_at"]),
                    posed_in_session=row["posed_in_session"],
                    context=row["context"],
                    status=row["status"],
                    resolved_at=(
                        datetime.fromisoformat(row["resolved_at"])
                        if row["resolved_at"]
                        else None
                    ),
                    resolution=row["resolution"],
                )
            )
        return results

    async def record_aging_evidence(
        self,
        *,
        older_than_days: int = 3,
        ledger: WorkLedger,
        limit: int | None = None,
        trigger_name: str = "DECISION_PENDING_3D",
    ) -> list[OpenDecision]:
        """Record evidence for open decisions pending past the aging window."""
        pending = await self.get_pending(
            older_than_days=older_than_days,
            limit=limit,
        )
        for decision in pending:
            if await self._aging_evidence_exists(
                decision.id,
                trigger_name=trigger_name,
            ):
                continue
            await ledger.record(
                LedgerEventType.TRIGGER_FIRED,
                trigger_name=trigger_name,
                reason="open_decision_aged",
                metadata={
                    "decision_id": decision.id,
                    "topic": decision.topic,
                    "posed_at": decision.posed_at.isoformat(),
                    "older_than_days": older_than_days,
                },
            )
            if self._event_emitter is not None:
                from kora_v2.core.events import EventType

                await self._event_emitter.emit(
                    EventType.TRIGGER_FIRED,
                    trigger_name=trigger_name,
                    decision_id=decision.id,
                    topic=decision.topic,
                    posed_at=decision.posed_at.isoformat(),
                    older_than_days=older_than_days,
                )
        return pending

    async def _aging_evidence_exists(
        self,
        decision_id: str,
        *,
        trigger_name: str,
    ) -> bool:
        """Return True when this decision already emitted aging evidence."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                "SELECT 1 FROM work_ledger "
                "WHERE trigger_name = ? AND metadata_json LIKE ? "
                "LIMIT 1",
                (trigger_name, f'%"{decision_id}"%'),
            )
            return await cursor.fetchone() is not None


# Keep an unused import check happy (json is used in callers that extend this);
# referenced here so that imports do not get pruned by tools.
_ = json
