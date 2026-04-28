"""Trusted support exports plus social/sensory planning helpers."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, Field

from kora_v2.life.stabilization import _ensure_domain_events_schema, _record_domain_event

ExportStatus = Literal["draft", "reviewed"]


class TrustedSupportExportDraft(BaseModel):
    id: str
    status: ExportStatus
    title: str
    selected_sections: dict[str, Any]
    excluded_sections: list[str]
    created_at: datetime
    reviewed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrustedSupportExportService:
    """Create user-reviewed support exports with selected sections only."""

    source_service = "TrustedSupportExportService"

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    async def create_draft(
        self,
        *,
        title: str,
        available_sections: dict[str, Any],
        selected_section_names: list[str],
        sensitive_section_names: list[str] | None = None,
    ) -> TrustedSupportExportDraft:
        await self._ensure_schema()
        sensitive = set(sensitive_section_names or [])
        selected = {
            key: available_sections[key]
            for key in selected_section_names
            if key in available_sections
        }
        excluded = sorted(set(available_sections) - set(selected))
        leaked_sensitive = sensitive.intersection(excluded).intersection(selected)
        if leaked_sensitive:
            raise ValueError(f"Sensitive sections leaked into export: {sorted(leaked_sensitive)}")

        now = _now()
        draft = TrustedSupportExportDraft(
            id=_new_id("trusted-export"),
            status="draft",
            title=title,
            selected_sections=selected,
            excluded_sections=excluded,
            created_at=now,
            metadata={"sensitive_sections_excluded": sorted(sensitive - set(selected))},
        )

        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                INSERT INTO trusted_support_exports
                    (id, status, title, selected_sections, excluded_sections,
                     created_at, reviewed_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.id,
                    draft.status,
                    draft.title,
                    _json(draft.selected_sections),
                    _json(draft.excluded_sections),
                    draft.created_at.isoformat(),
                    None,
                    _json(draft.metadata),
                ),
            )
            await _record_domain_event(
                db,
                event_type="TRUSTED_SUPPORT_EXPORT_DRAFTED",
                aggregate_type="trusted_support_export",
                aggregate_id=draft.id,
                source_service=self.source_service,
                payload={
                    "selected_sections": sorted(draft.selected_sections),
                    "excluded_sections": draft.excluded_sections,
                    "requires_user_review": True,
                },
            )
            await db.commit()

        return draft

    async def mark_reviewed(self, export_id: str) -> TrustedSupportExportDraft:
        await self._ensure_schema()
        draft = await self.get_draft(export_id)
        if draft is None:
            raise ValueError(f"Unknown trusted support export: {export_id}")
        reviewed_at = _now()
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                UPDATE trusted_support_exports
                SET status = 'reviewed', reviewed_at = ?
                WHERE id = ?
                """,
                (reviewed_at.isoformat(), export_id),
            )
            await _record_domain_event(
                db,
                event_type="TRUSTED_SUPPORT_EXPORT_REVIEWED",
                aggregate_type="trusted_support_export",
                aggregate_id=export_id,
                source_service=self.source_service,
                payload={"reviewed_at": reviewed_at.isoformat()},
            )
            await db.commit()
        return draft.model_copy(update={"status": "reviewed", "reviewed_at": reviewed_at})

    async def get_draft(self, export_id: str) -> TrustedSupportExportDraft | None:
        await self._ensure_schema()
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM trusted_support_exports WHERE id = ?",
                (export_id,),
            )
            row = await cursor.fetchone()
        return _export_from_row(row) if row else None

    async def _ensure_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS trusted_support_exports (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    selected_sections TEXT NOT NULL,
                    excluded_sections TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    metadata TEXT
                )
                """
            )
            await _ensure_domain_events_schema(db)
            await db.commit()


class SocialSensoryInput(BaseModel):
    social_commitments: int = 0
    social_intensity: int = Field(default=0, ge=0, le=5)
    sensory_intensity: int = Field(default=0, ge=0, le=5)
    transition_count: int = 0
    user_social_energy: int = Field(default=3, ge=0, le=5)
    recovery_debt: int = Field(default=0, ge=0, le=5)
    emotional_stakes: int = Field(default=0, ge=0, le=5)
    notes: str | None = None


class SocialSensoryAssessment(BaseModel):
    id: str | None = None
    social_load_score: int
    sensory_load_score: int
    band: Literal["low", "medium", "high", "overload"]
    needs_decompression: bool
    planning_rules: list[str]
    recovery_recommendations: list[str]
    communication_scripts: list[str]
    created_at: datetime | None = None


class SocialSensorySupportService:
    """Small deterministic helper used by load and planning integrations."""

    source_service = "SocialSensorySupportService"

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else None

    def assess(self, data: SocialSensoryInput) -> SocialSensoryAssessment:
        social_score = (
            data.social_commitments * 2
            + data.social_intensity * 3
            + data.emotional_stakes * 2
            + data.recovery_debt * 2
            - data.user_social_energy
        )
        sensory_score = data.sensory_intensity * 4 + data.transition_count * 2 + data.recovery_debt
        total = max(0, social_score) + max(0, sensory_score)
        if total >= 34:
            band = "overload"
        elif total >= 22:
            band = "high"
        elif total >= 10:
            band = "medium"
        else:
            band = "low"
        needs_decompression = band in {"high", "overload"} or data.sensory_intensity >= 4
        planning_rules = []
        if needs_decompression:
            planning_rules.append("add_decompression_block")
        if band in {"high", "overload"}:
            planning_rules.append("avoid_optional_social_commitments")
        if data.transition_count >= 3:
            planning_rules.append("add_transition_buffers")

        return SocialSensoryAssessment(
            social_load_score=max(0, social_score),
            sensory_load_score=max(0, sensory_score),
            band=band,
            needs_decompression=needs_decompression,
            planning_rules=planning_rules,
            recovery_recommendations=_recovery_recommendations(needs_decompression, band),
            communication_scripts=_communication_scripts(band),
        )

    async def record_assessment(self, data: SocialSensoryInput) -> SocialSensoryAssessment:
        if self._db_path is None:
            raise ValueError("db_path is required to record social/sensory assessments")
        assessment = self.assess(data).model_copy(
            update={"id": _new_id("social-sensory"), "created_at": _now()}
        )
        await self._ensure_schema()
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                INSERT INTO social_sensory_assessments
                    (id, social_load_score, sensory_load_score, band,
                     needs_decompression, planning_rules, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assessment.id,
                    assessment.social_load_score,
                    assessment.sensory_load_score,
                    assessment.band,
                    1 if assessment.needs_decompression else 0,
                    _json(assessment.planning_rules),
                    _json({"input": data.model_dump(), "assessment": assessment.model_dump()}),
                    assessment.created_at.isoformat(),
                ),
            )
            await _record_domain_event(
                db,
                event_type="SOCIAL_SENSORY_LOAD_ASSESSED",
                aggregate_type="social_sensory_assessment",
                aggregate_id=assessment.id,
                source_service=self.source_service,
                payload=assessment.model_dump(),
            )
            await db.commit()
        return assessment

    async def _ensure_schema(self) -> None:
        assert self._db_path is not None
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS social_sensory_assessments (
                    id TEXT PRIMARY KEY,
                    social_load_score INTEGER NOT NULL,
                    sensory_load_score INTEGER NOT NULL,
                    band TEXT NOT NULL,
                    needs_decompression INTEGER NOT NULL,
                    planning_rules TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await _ensure_domain_events_schema(db)
            await db.commit()


def _recovery_recommendations(needs_decompression: bool, band: str) -> list[str]:
    if band == "overload":
        return ["protect a quiet recovery block", "drop optional social/admin work"]
    if needs_decompression:
        return ["add a decompression block after the commitment", "reduce transition friction"]
    return ["keep a small buffer between commitments"]


def _communication_scripts(band: str) -> list[str]:
    if band in {"high", "overload"}:
        return [
            "I am at capacity today, so I need to keep this short.",
            "Can we move this to a lower-energy time?",
        ]
    return ["I may need a buffer before the next thing."]


def _export_from_row(row: aiosqlite.Row) -> TrustedSupportExportDraft:
    return TrustedSupportExportDraft(
        id=row["id"],
        status=row["status"],
        title=row["title"],
        selected_sections=json.loads(row["selected_sections"] or "{}"),
        excluded_sections=json.loads(row["excluded_sections"] or "[]"),
        created_at=datetime.fromisoformat(row["created_at"]),
        reviewed_at=datetime.fromisoformat(row["reviewed_at"]) if row["reviewed_at"] else None,
        metadata=json.loads(row["metadata"] or "{}"),
    )


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(UTC)
