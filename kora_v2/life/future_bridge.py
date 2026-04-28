"""Future Self Bridge service for shame-safe end-of-day carryover."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, Field

from kora_v2.life.stabilization import _ensure_domain_events_schema, _record_domain_event

OutcomeStatus = Literal["done", "partial", "skipped", "blocked", "dropped"]


class DayOutcomeItem(BaseModel):
    title: str
    status: OutcomeStatus
    carryover_reason: str | None = None
    next_move: str | None = None
    item_id: str | None = None
    sensitive: bool = False


class FutureSelfBridge(BaseModel):
    id: str
    bridge_date: date
    source_day_plan_id: str | None = None
    load_assessment_id: str | None = None
    summary: str
    carryovers: list[dict[str, Any]]
    first_moves: list[str]
    content_path: str | None = None
    created_at: datetime
    outcomes: dict[str, list[DayOutcomeItem]] = Field(default_factory=dict)


class FutureSelfBridgeService:
    """Generate and look up next-day bridges."""

    source_service = "FutureSelfBridgeService"

    def __init__(self, db_path: Path, memory_root: Path, day_plan_service: Any | None = None) -> None:
        self._db_path = Path(db_path)
        self._memory_root = Path(memory_root)
        self._day_plan_service = day_plan_service

    async def build_bridge(
        self,
        day: date,
        *,
        source_day_plan_id: str | None = None,
        load_assessment_id: str | None = None,
        outcomes: list[DayOutcomeItem] | None = None,
        notes: str | None = None,
    ) -> FutureSelfBridge:
        await self._ensure_schema()
        outcomes = outcomes or []
        now = _now()
        bridge_id = _new_id("future-bridge")
        buckets = _bucket_outcomes(outcomes)
        carryovers = _carryovers(outcomes)
        first_moves = _first_moves(carryovers)
        summary = _summary(day, buckets, notes)
        artifact_path = self._artifact_path(bridge_id, day)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)

        bridge = FutureSelfBridge(
            id=bridge_id,
            bridge_date=day,
            source_day_plan_id=source_day_plan_id,
            load_assessment_id=load_assessment_id,
            summary=summary,
            carryovers=carryovers,
            first_moves=first_moves,
            content_path=str(artifact_path),
            created_at=now,
            outcomes=buckets,
        )
        artifact_path.write_text(_render_markdown(bridge), encoding="utf-8")

        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                INSERT INTO future_self_bridges
                    (id, bridge_date, source_day_plan_id, load_assessment_id,
                     summary, carryovers, first_moves, content_path, created_at,
                     metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bridge.id,
                    bridge.bridge_date.isoformat(),
                    bridge.source_day_plan_id,
                    bridge.load_assessment_id,
                    bridge.summary,
                    _json(bridge.carryovers),
                    _json(bridge.first_moves),
                    bridge.content_path,
                    bridge.created_at.isoformat(),
                    _json({"outcomes": {k: [i.model_dump() for i in v] for k, v in buckets.items()}}),
                ),
            )
            await _record_domain_event(
                db,
                event_type="FUTURE_SELF_BRIDGE_CREATED",
                aggregate_type="future_self_bridge",
                aggregate_id=bridge.id,
                source_service=self.source_service,
                payload={
                    "bridge_date": bridge.bridge_date.isoformat(),
                    "source_day_plan_id": bridge.source_day_plan_id,
                    "carryover_count": len(bridge.carryovers),
                    "first_moves": bridge.first_moves,
                },
            )
            await db.commit()

        return bridge

    async def next_morning_lookup(self, morning: date) -> FutureSelfBridge | None:
        """Load the newest bridge from the previous calendar day."""

        await self._ensure_schema()
        target_day = morning - timedelta(days=1)
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM future_self_bridges
                WHERE bridge_date = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (target_day.isoformat(),),
            )
            row = await cursor.fetchone()
        return _bridge_from_row(row) if row else None

    async def apply_bridge_to_tomorrow(self, bridge_id: str, *, user_confirmed: bool) -> Any:
        """Hand first moves to an injected DayPlanService when available."""

        await self._ensure_schema()
        bridge = await self.get_bridge(bridge_id)
        if bridge is None:
            raise ValueError(f"Unknown future bridge: {bridge_id}")
        if not user_confirmed:
            raise ValueError("Future bridge application requires user confirmation")
        if self._day_plan_service is None:
            return {"bridge_id": bridge.id, "first_moves": bridge.first_moves, "applied": False}

        payload = {
            "source": "future_self_bridge",
            "bridge_id": bridge.id,
            "date": (bridge.bridge_date + timedelta(days=1)).isoformat(),
            "first_moves": bridge.first_moves,
            "carryovers": bridge.carryovers,
        }
        for method_name in ("apply_future_bridge", "apply_bridge_to_tomorrow", "create_day_plan"):
            method = getattr(self._day_plan_service, method_name, None)
            if method is not None:
                return await method(payload)
        return payload

    async def get_bridge(self, bridge_id: str) -> FutureSelfBridge | None:
        await self._ensure_schema()
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM future_self_bridges WHERE id = ?", (bridge_id,))
            row = await cursor.fetchone()
        return _bridge_from_row(row) if row else None

    async def _ensure_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS future_self_bridges (
                    id TEXT PRIMARY KEY,
                    bridge_date TEXT NOT NULL,
                    source_day_plan_id TEXT,
                    load_assessment_id TEXT,
                    summary TEXT NOT NULL,
                    carryovers TEXT NOT NULL,
                    first_moves TEXT NOT NULL,
                    content_path TEXT,
                    created_at TEXT NOT NULL,
                    metadata TEXT
                )
                """
            )
            await _ensure_domain_events_schema(db)
            await db.commit()

    def _artifact_path(self, bridge_id: str, day: date) -> Path:
        return self._memory_root / "Life OS" / "Future Self Bridges" / f"{day.isoformat()}-{_slug(bridge_id)}.md"


def _bucket_outcomes(outcomes: list[DayOutcomeItem]) -> dict[str, list[DayOutcomeItem]]:
    return {status: [item for item in outcomes if item.status == status] for status in _STATUSES}


def _carryovers(outcomes: list[DayOutcomeItem]) -> list[dict[str, Any]]:
    carryover_statuses = {"partial", "skipped", "blocked"}
    result = []
    for item in outcomes:
        if item.status not in carryover_statuses:
            continue
        result.append(
            {
                "title": item.title,
                "status": item.status,
                "reason": item.carryover_reason or _default_reason(item.status),
                "next_move": item.next_move or f"Choose the first tiny step for {item.title}.",
                "item_id": item.item_id,
            }
        )
    return result


def _first_moves(carryovers: list[dict[str, Any]]) -> list[str]:
    if not carryovers:
        return ["Start with basic maintenance, then choose the lightest useful task."]
    return [str(item["next_move"]) for item in carryovers[:3]]


def _summary(day: date, buckets: dict[str, list[DayOutcomeItem]], notes: str | None) -> str:
    parts = [
        f"{len(buckets['done'])} done",
        f"{len(buckets['partial'])} partial",
        f"{len(buckets['skipped'])} skipped",
        f"{len(buckets['blocked'])} blocked",
        f"{len(buckets['dropped'])} dropped",
    ]
    suffix = f" {notes}" if notes else " The plan can be adjusted without blame."
    return f"Bridge for {day.isoformat()}: " + ", ".join(parts) + "." + suffix


def _render_markdown(bridge: FutureSelfBridge) -> str:
    lines = [
        f"# Future Self Bridge: {bridge.bridge_date.isoformat()}",
        "",
        bridge.summary,
        "",
        "## Carryovers",
    ]
    if bridge.carryovers:
        lines.extend(
            f"- {item['title']} ({item['status']}): {item['reason']} Next: {item['next_move']}"
            for item in bridge.carryovers
        )
    else:
        lines.append("- No unresolved carryovers recorded.")
    lines.extend(["", "## First Moves", *[f"- {move}" for move in bridge.first_moves], ""])
    return "\n".join(lines)


def _bridge_from_row(row: aiosqlite.Row) -> FutureSelfBridge:
    metadata = json.loads(row["metadata"] or "{}")
    outcome_data = metadata.get("outcomes", {})
    outcomes = {
        key: [DayOutcomeItem(**item) for item in value]
        for key, value in outcome_data.items()
    }
    return FutureSelfBridge(
        id=row["id"],
        bridge_date=date.fromisoformat(row["bridge_date"]),
        source_day_plan_id=row["source_day_plan_id"],
        load_assessment_id=row["load_assessment_id"],
        summary=row["summary"],
        carryovers=json.loads(row["carryovers"] or "[]"),
        first_moves=json.loads(row["first_moves"] or "[]"),
        content_path=row["content_path"],
        created_at=datetime.fromisoformat(row["created_at"]),
        outcomes=outcomes,
    )


def _default_reason(status: str) -> str:
    return {
        "partial": "partly complete and still worth continuing",
        "skipped": "skipped today; reassess tomorrow",
        "blocked": "blocked by a missing dependency or unclear next step",
    }.get(status, "still relevant")


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(UTC)


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-") or "bridge"


_STATUSES = ("done", "partial", "skipped", "blocked", "dropped")
