"""Life OS load assessment engine.

This module owns explainable day-load assessment for the Life OS pivot.  It is
kept independent from DI/tool wiring so integration can attach it to the active
runtime without making load computation depend on prompt text.
"""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, Field

LoadBand = Literal["light", "normal", "high", "overloaded", "stabilization"]


class LoadFactor(BaseModel):
    source: str
    label: str
    weight: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class LoadAssessment(BaseModel):
    id: str
    assessment_date: date
    score: float
    band: LoadBand
    confidence: float
    factors: list[LoadFactor]
    recommended_mode: str
    generated_at: datetime
    confirmed_by_user: bool = False


class LoadExplanation(BaseModel):
    assessment_id: str
    band: LoadBand
    score: float
    recommended_mode: str
    factors: list[LoadFactor]
    summary: str


class LoadCorrection(BaseModel):
    corrected_band: LoadBand | None = None
    corrected_score: float | None = None
    profile_key: str = "general_life_management"
    signal_type: str = "load_correction"
    weight: float = 0.0
    details: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class LifeLoadEngine:
    """Computes and persists explainable day-load assessments."""

    def __init__(self, db_path: str | Path, support_registry: Any | None = None) -> None:
        self.db_path = Path(db_path)
        self.support_registry = support_registry

    async def assess_day(self, day: date, *, force: bool = False) -> LoadAssessment:
        """Assess a local day from calendar, task, energy, meal, and ledger data."""
        await self.ensure_schema()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            if not force:
                existing = await _fetch_latest_assessment(db, day)
                if existing is not None:
                    return _assessment_from_row(existing)

            inputs = await self._load_inputs(db, day)
            factors = self._base_factors(inputs)
            factors.extend(await self._support_module_factors(day, inputs))

            score = min(1.0, max(0.0, 0.15 + sum(f.weight for f in factors)))
            band = _band_for_score(score)
            recommended_mode = _recommended_mode_for_band(band)
            confidence = self._confidence(inputs, factors)
            assessment_id = _id("load")
            now = _now()

            await db.execute(
                """
                INSERT INTO load_assessments
                    (id, assessment_date, score, band, confidence, factors,
                     recommended_mode, generated_at, confirmed_by_user)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    assessment_id,
                    day.isoformat(),
                    score,
                    band,
                    confidence,
                    _json([f.model_dump() for f in factors]),
                    recommended_mode,
                    now.isoformat(),
                ),
            )
            await _record_domain_event(
                db,
                event_type="LOAD_ASSESSMENT_UPDATED",
                aggregate_type="load_assessment",
                aggregate_id=assessment_id,
                source_service="LifeLoadEngine",
                payload={
                    "assessment_date": day.isoformat(),
                    "score": score,
                    "band": band,
                    "recommended_mode": recommended_mode,
                    "factor_count": len(factors),
                },
            )
            await db.commit()

        return LoadAssessment(
            id=assessment_id,
            assessment_date=day,
            score=score,
            band=band,
            confidence=confidence,
            factors=factors,
            recommended_mode=recommended_mode,
            generated_at=now,
        )

    async def explain(self, assessment_id: str) -> LoadExplanation:
        await self.ensure_schema()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT * FROM load_assessments WHERE id = ?",
                    (assessment_id,),
                )
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown load assessment: {assessment_id}")
        assessment = _assessment_from_row(row)
        strongest = sorted(assessment.factors, key=lambda f: abs(f.weight), reverse=True)
        labels = ", ".join(f.label for f in strongest[:3]) or "no major pressure signals"
        return LoadExplanation(
            assessment_id=assessment.id,
            band=assessment.band,
            score=assessment.score,
            recommended_mode=assessment.recommended_mode,
            factors=assessment.factors,
            summary=f"{assessment.band} load: {labels}.",
        )

    async def apply_user_correction(
        self,
        assessment_id: str,
        correction: LoadCorrection,
    ) -> LoadAssessment:
        """Persist a user correction and return the updated assessment row."""
        await self.ensure_schema()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT * FROM load_assessments WHERE id = ?",
                    (assessment_id,),
                )
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown load assessment: {assessment_id}")

            assessment = _assessment_from_row(row)
            score = (
                max(0.0, min(1.0, correction.corrected_score))
                if correction.corrected_score is not None
                else assessment.score
            )
            band = correction.corrected_band or _band_for_score(score)
            mode = _recommended_mode_for_band(band)
            await db.execute(
                """
                UPDATE load_assessments
                SET score = ?, band = ?, recommended_mode = ?, confirmed_by_user = 1
                WHERE id = ?
                """,
                (score, band, mode, assessment_id),
            )
            await db.execute(
                """
                INSERT INTO support_profile_signals
                    (id, profile_key, signal_type, weight, source, confidence,
                     last_seen_at, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'user_correction', 1.0, ?, ?, ?, ?)
                """,
                (
                    _id("sig"),
                    correction.profile_key,
                    correction.signal_type,
                    correction.weight,
                    _now().isoformat(),
                    _json(
                        {
                            "assessment_id": assessment_id,
                            "details": correction.details,
                            "corrected_band": correction.corrected_band,
                            "corrected_score": correction.corrected_score,
                            **correction.metadata,
                        }
                    ),
                    _now().isoformat(),
                    _now().isoformat(),
                ),
            )
            await _record_domain_event(
                db,
                event_type="LOAD_ASSESSMENT_CORRECTED",
                aggregate_type="load_assessment",
                aggregate_id=assessment_id,
                source_service="LifeLoadEngine",
                payload=correction.model_dump(),
            )
            await db.commit()
            updated = await (
                await db.execute(
                    "SELECT * FROM load_assessments WHERE id = ?",
                    (assessment_id,),
                )
            ).fetchone()

        if updated is None:
            raise RuntimeError("Corrected load assessment disappeared")
        return _assessment_from_row(updated)

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(str(self.db_path)) as db:
            await _ensure_common_schema(db)
            await db.commit()

    async def _load_inputs(self, db: aiosqlite.Connection, day: date) -> dict[str, Any]:
        start, end = _day_bounds(day)
        return {
            "calendar_entries": await _fetch_all_if_table(
                db,
                "calendar_entries",
                "SELECT * FROM calendar_entries "
                "WHERE status != 'cancelled' AND starts_at >= ? AND starts_at < ?",
                (start, end),
            ),
            "items": await _fetch_all_if_table(
                db,
                "items",
                "SELECT * FROM items WHERE status NOT IN "
                "('done', 'completed', 'cancelled', 'dropped')",
            ),
            "energy_log": await _fetch_all_if_table(
                db,
                "energy_log",
                "SELECT * FROM energy_log WHERE logged_at >= ? AND logged_at < ?",
                (start, end),
            ),
            "meal_log": await _fetch_all_if_table(
                db,
                "meal_log",
                "SELECT * FROM meal_log WHERE logged_at >= ? AND logged_at < ?",
                (start, end),
            ),
            "life_events": await _fetch_all_if_table(
                db,
                "life_events",
                "SELECT * FROM life_events WHERE event_time >= ? AND event_time < ?",
                (start, end),
            ),
            "support_profiles": await _fetch_all_if_table(
                db,
                "support_profiles",
                "SELECT * FROM support_profiles WHERE status = 'active'",
            ),
        }

    def _base_factors(self, inputs: dict[str, Any]) -> list[LoadFactor]:
        factors: list[LoadFactor] = []
        calendar = inputs["calendar_entries"]
        items = inputs["items"]
        energy = inputs["energy_log"]
        meals = inputs["meal_log"]
        events = inputs["life_events"]

        meeting_like = [r for r in calendar if r.get("kind", "event") in {"event", "meeting", "appointment"}]
        if len(calendar) >= 6:
            factors.append(LoadFactor(source="calendar", label="crowded calendar", weight=0.18))
        elif len(calendar) >= 3:
            factors.append(LoadFactor(source="calendar", label="several scheduled blocks", weight=0.10))
        if len(meeting_like) >= 3:
            factors.append(LoadFactor(source="social", label="multiple social or appointment blocks", weight=0.12))
        if _transition_count(calendar) >= 4:
            factors.append(LoadFactor(source="sensory", label="high transition count", weight=0.12))

        estimated = sum(int(r.get("estimated_minutes") or 0) for r in items)
        if len(items) >= 8:
            factors.append(LoadFactor(source="items", label="many open commitments", weight=0.16))
        if estimated >= 240:
            factors.append(LoadFactor(source="items", label="large estimated task load", weight=0.16))

        latest_energy = energy[-1]["level"].lower() if energy else ""
        if latest_energy in {"low", "very_low", "exhausted", "depleted"}:
            factors.append(LoadFactor(source="energy", label="self-reported low energy", weight=0.25))
        elif latest_energy in {"high", "good"}:
            factors.append(LoadFactor(source="energy", label="good energy reported", weight=-0.08))

        if not meals:
            factors.append(LoadFactor(source="meal", label="no meal logged today", weight=0.08))
        if any("skipped" in str(e.get("event_type", "")).lower() for e in events):
            factors.append(LoadFactor(source="ledger", label="skipped item logged", weight=0.08))
        if any("behind" in str(e.get("details", "")).lower() for e in events):
            factors.append(LoadFactor(source="ledger", label="user reported being behind", weight=0.14))
        return factors

    async def _support_module_factors(
        self,
        day: date,
        inputs: dict[str, Any],
    ) -> list[LoadFactor]:
        modules = await _active_support_modules(self.support_registry)
        active_keys = {
            str(row.get("profile_key"))
            for row in inputs.get("support_profiles", [])
            if row.get("status") == "active"
        }
        factors: list[LoadFactor] = []
        for module in modules:
            name = getattr(module, "name", None)
            if name and active_keys and name not in active_keys and name != "general_life_management":
                continue
            func = getattr(module, "load_factors", None)
            if func is None:
                continue
            result = func({"day": day, **inputs}, inputs.get("life_events", []))
            if inspect.isawaitable(result):
                result = await result
            for raw in result or []:
                if isinstance(raw, LoadFactor):
                    factors.append(raw)
                elif isinstance(raw, dict):
                    factors.append(LoadFactor(**raw))
                else:
                    factors.append(
                        LoadFactor(
                            source=name or "support",
                            label=str(raw),
                            weight=0.05,
                        )
                    )
        return factors

    def _confidence(self, inputs: dict[str, Any], factors: list[LoadFactor]) -> float:
        evidence_streams = sum(
            1
            for key in ("calendar_entries", "items", "energy_log", "meal_log", "life_events")
            if inputs.get(key)
        )
        return min(0.95, 0.45 + (evidence_streams * 0.08) + (min(len(factors), 6) * 0.02))


async def _active_support_modules(registry: Any | None) -> list[Any]:
    if registry is None:
        return []
    for attr in ("active_modules", "get_active_modules", "modules"):
        member = getattr(registry, attr, None)
        if member is None:
            continue
        result = member() if callable(member) else member
        if inspect.isawaitable(result):
            result = await result
        return list(result or [])
    return []


async def _fetch_latest_assessment(
    db: aiosqlite.Connection,
    day: date,
) -> aiosqlite.Row | None:
    if not await _table_exists(db, "load_assessments"):
        return None
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


def _assessment_from_row(row: aiosqlite.Row) -> LoadAssessment:
    factors = [LoadFactor(**item) for item in _loads(row["factors"], [])]
    return LoadAssessment(
        id=row["id"],
        assessment_date=date.fromisoformat(row["assessment_date"]),
        score=float(row["score"]),
        band=row["band"],
        confidence=float(row["confidence"]),
        factors=factors,
        recommended_mode=row["recommended_mode"],
        generated_at=datetime.fromisoformat(row["generated_at"]),
        confirmed_by_user=bool(row["confirmed_by_user"]),
    )


async def _ensure_common_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
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
        CREATE TABLE IF NOT EXISTS support_profile_signals (
            id TEXT PRIMARY KEY,
            profile_key TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            weight REAL NOT NULL,
            source TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            last_seen_at TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
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
        CREATE INDEX IF NOT EXISTS idx_domain_events_type_created
            ON domain_events(event_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_domain_events_aggregate
            ON domain_events(aggregate_type, aggregate_id, created_at);
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
) -> None:
    await db.execute(
        """
        INSERT INTO domain_events
            (id, event_type, aggregate_type, aggregate_id, source_service,
             correlation_id, causation_id, payload, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _id("evt"),
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


async def _fetch_all_if_table(
    db: aiosqlite.Connection,
    table: str,
    query: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    if not await _table_exists(db, table):
        return []
    rows = await (await db.execute(query, params)).fetchall()
    return [dict(row) for row in rows]


async def _table_exists(db: aiosqlite.Connection, table: str) -> bool:
    row = await (
        await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        )
    ).fetchone()
    return row is not None


def _transition_count(rows: list[dict[str, Any]]) -> int:
    timed = sorted([r for r in rows if r.get("starts_at")], key=lambda r: r["starts_at"])
    return max(0, len(timed) - 1)


def _band_for_score(score: float) -> LoadBand:
    if score >= 0.82:
        return "stabilization"
    if score >= 0.68:
        return "overloaded"
    if score >= 0.48:
        return "high"
    if score >= 0.25:
        return "normal"
    return "light"


def _recommended_mode_for_band(band: str) -> str:
    return {
        "light": "normal",
        "normal": "normal",
        "high": "high_support",
        "overloaded": "quiet",
        "stabilization": "stabilization",
    }[band]


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime.combine(day, time.min, tzinfo=UTC).isoformat()
    end = datetime.combine(day, time.max, tzinfo=UTC).isoformat()
    return start, end


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
