"""Life OS context pack generation and persistence."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, Field

from kora_v2.life.stabilization import _ensure_domain_events_schema, _record_domain_event

ContextPackType = Literal["anxiety", "admin", "sensory"]
ContextPackFeedback = Literal["useful", "wrong", "too_much"]


class ContextPackTarget(BaseModel):
    """Calendar/item target for a practical prep packet."""

    title: str
    pack_type: ContextPackType
    calendar_entry_id: str | None = None
    item_id: str | None = None
    description: str = ""
    people: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    known_uncertainties: list[str] = Field(default_factory=list)
    sensory_notes: list[str] = Field(default_factory=list)
    anxiety_points: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextPack(BaseModel):
    """Persisted context pack metadata plus generated content."""

    id: str
    title: str
    pack_type: ContextPackType
    status: str
    calendar_entry_id: str | None = None
    item_id: str | None = None
    content_path: str
    summary: str
    scripts: list[str]
    materials: list[str]
    uncertainty_list: list[str]
    first_step: str
    fallback_plan: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextPackService:
    """Build anxiety/admin/sensory context packs with DB and file proof."""

    source_service = "ContextPackService"

    def __init__(self, db_path: Path, memory_root: Path) -> None:
        self._db_path = Path(db_path)
        self._memory_root = Path(memory_root)

    async def build_pack(self, target: ContextPackTarget) -> ContextPack:
        await self._ensure_schema()
        now = _now()
        pack_id = _new_id("context-pack")
        content = _build_content(target)
        artifact_path = self._artifact_path(pack_id, target.title)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(_render_markdown(target, content), encoding="utf-8")

        pack = ContextPack(
            id=pack_id,
            title=target.title,
            pack_type=target.pack_type,
            status="ready",
            calendar_entry_id=target.calendar_entry_id,
            item_id=target.item_id,
            content_path=str(artifact_path),
            summary=content["summary"],
            scripts=content["scripts"],
            materials=content["materials"],
            uncertainty_list=content["uncertainty_list"],
            first_step=content["first_step"],
            fallback_plan=content["fallback_plan"],
            created_at=now,
            updated_at=now,
            metadata={"target": target.model_dump(), "feedback": []},
        )

        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                INSERT INTO context_packs
                    (id, calendar_entry_id, item_id, title, pack_type, status,
                     content_path, summary, created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pack.id,
                    pack.calendar_entry_id,
                    pack.item_id,
                    pack.title,
                    pack.pack_type,
                    pack.status,
                    pack.content_path,
                    pack.summary,
                    pack.created_at.isoformat(),
                    pack.updated_at.isoformat(),
                    _json(pack.metadata),
                ),
            )
            await _record_domain_event(
                db,
                event_type="CONTEXT_PACK_READY",
                aggregate_type="context_pack",
                aggregate_id=pack.id,
                source_service=self.source_service,
                payload={
                    "pack_type": pack.pack_type,
                    "calendar_entry_id": pack.calendar_entry_id,
                    "item_id": pack.item_id,
                    "content_path": pack.content_path,
                },
            )
            await db.commit()

        return pack

    async def refresh_pack(self, pack_id: str) -> ContextPack:
        await self._ensure_schema()
        pack = await self.get_pack(pack_id)
        if pack is None:
            raise ValueError(f"Unknown context pack: {pack_id}")

        target_data = pack.metadata.get("target") or {
            "title": pack.title,
            "pack_type": pack.pack_type,
            "calendar_entry_id": pack.calendar_entry_id,
            "item_id": pack.item_id,
        }
        target = ContextPackTarget(**target_data)
        content = _build_content(target)
        updated_at = _now()
        Path(pack.content_path).write_text(_render_markdown(target, content), encoding="utf-8")
        metadata = {**pack.metadata, "refreshed_at": updated_at.isoformat()}

        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                UPDATE context_packs
                SET summary = ?, updated_at = ?, metadata = ?
                WHERE id = ?
                """,
                (content["summary"], updated_at.isoformat(), _json(metadata), pack_id),
            )
            await _record_domain_event(
                db,
                event_type="CONTEXT_PACK_REFRESHED",
                aggregate_type="context_pack",
                aggregate_id=pack_id,
                source_service=self.source_service,
                payload={"content_path": pack.content_path},
            )
            await db.commit()

        return pack.model_copy(
            update={
                "summary": content["summary"],
                "scripts": content["scripts"],
                "materials": content["materials"],
                "uncertainty_list": content["uncertainty_list"],
                "first_step": content["first_step"],
                "fallback_plan": content["fallback_plan"],
                "updated_at": updated_at,
                "metadata": metadata,
            }
        )

    async def record_feedback(
        self,
        pack_id: str,
        feedback: ContextPackFeedback,
        note: str | None = None,
    ) -> None:
        await self._ensure_schema()
        pack = await self.get_pack(pack_id)
        if pack is None:
            raise ValueError(f"Unknown context pack: {pack_id}")

        entry = {"feedback": feedback, "note": note, "created_at": _now().isoformat()}
        metadata = {**pack.metadata, "feedback": [*pack.metadata.get("feedback", []), entry]}
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                INSERT INTO context_pack_feedback
                    (id, context_pack_id, feedback, note, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (_new_id("context-pack-feedback"), pack_id, feedback, note, entry["created_at"]),
            )
            await db.execute(
                "UPDATE context_packs SET metadata = ?, updated_at = ? WHERE id = ?",
                (_json(metadata), entry["created_at"], pack_id),
            )
            await _record_domain_event(
                db,
                event_type="CONTEXT_PACK_FEEDBACK_RECORDED",
                aggregate_type="context_pack",
                aggregate_id=pack_id,
                source_service=self.source_service,
                payload=entry,
            )
            await db.commit()

    async def get_pack(self, pack_id: str) -> ContextPack | None:
        await self._ensure_schema()
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM context_packs WHERE id = ?", (pack_id,))
            row = await cursor.fetchone()
        if row is None:
            return None
        return _pack_from_row(row)

    async def _ensure_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS context_packs (
                    id TEXT PRIMARY KEY,
                    calendar_entry_id TEXT,
                    item_id TEXT,
                    title TEXT NOT NULL,
                    pack_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    content_path TEXT,
                    summary TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS context_pack_feedback (
                    id TEXT PRIMARY KEY,
                    context_pack_id TEXT NOT NULL,
                    feedback TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            await _ensure_domain_events_schema(db)
            await db.commit()

    def _artifact_path(self, pack_id: str, title: str) -> Path:
        return self._memory_root / "Life OS" / "Context Packs" / f"{pack_id}-{_slug(title)}.md"


def _build_content(target: ContextPackTarget) -> dict[str, Any]:
    materials = target.materials or _default_materials(target.pack_type)
    uncertainty = target.known_uncertainties or _default_uncertainties(target.pack_type)
    first_step = _first_step(target)
    fallback = _fallback_plan(target.pack_type)
    scripts = _scripts(target)
    summary = f"{target.pack_type.title()} prep for {target.title}: {first_step}"
    return {
        "summary": summary,
        "scripts": scripts,
        "materials": materials,
        "uncertainty_list": uncertainty,
        "first_step": first_step,
        "fallback_plan": fallback,
    }


def _scripts(target: ContextPackTarget) -> list[str]:
    if target.pack_type == "admin":
        return [
            "Hi, I am calling about this item. Can you tell me the next step?",
            "Could you send that in writing so I can review it later?",
        ]
    if target.pack_type == "sensory":
        return [
            "I may need a short quiet break; I will step out and come back.",
            "Can we choose the lower-noise option if it is available?",
        ]
    return [
        "I am here to handle one step at a time.",
        "Can you repeat the key detail or write it down for me?",
    ]


def _default_materials(pack_type: ContextPackType) -> list[str]:
    if pack_type == "admin":
        return ["ID or account info", "relevant forms", "notes app for confirmation number"]
    if pack_type == "sensory":
        return ["headphones or earplugs", "comfort item", "water", "exit route"]
    return ["calendar details", "questions list", "water", "grounding note"]


def _default_uncertainties(pack_type: ContextPackType) -> list[str]:
    if pack_type == "admin":
        return ["which office owns the next step", "whether a form or call is required"]
    if pack_type == "sensory":
        return ["noise level", "wait time", "crowd size"]
    return ["what will be asked", "how long it will take", "what happens after"]


def _first_step(target: ContextPackTarget) -> str:
    if target.pack_type == "admin":
        return "Open the relevant account, form, or notes before starting."
    if target.pack_type == "sensory":
        return "Choose the lowest-friction arrival and exit plan."
    return "Write the first question or sentence before the event starts."


def _fallback_plan(pack_type: ContextPackType) -> str:
    if pack_type == "admin":
        return "If the process stalls, capture the blocker and ask for the next concrete action."
    if pack_type == "sensory":
        return "If overload rises, step away, reduce input, and keep only the required commitment."
    return "If anxiety spikes, pause, read the script, and complete only the smallest useful step."


def _render_markdown(target: ContextPackTarget, content: dict[str, Any]) -> str:
    lines = [
        f"# {target.title}",
        "",
        f"Type: {target.pack_type}",
        "",
        "## Summary",
        content["summary"],
        "",
        "## First Step",
        content["first_step"],
        "",
        "## Materials",
        *[f"- {item}" for item in content["materials"]],
        "",
        "## Uncertainty List",
        *[f"- {item}" for item in content["uncertainty_list"]],
        "",
        "## Scripts",
        *[f"- {item}" for item in content["scripts"]],
        "",
        "## Fallback Plan",
        content["fallback_plan"],
        "",
    ]
    return "\n".join(lines)


def _pack_from_row(row: aiosqlite.Row) -> ContextPack:
    metadata = json.loads(row["metadata"] or "{}")
    target = metadata.get("target", {})
    content = _build_content(ContextPackTarget(**target)) if target else _fallback_content(row)
    return ContextPack(
        id=row["id"],
        title=row["title"],
        pack_type=row["pack_type"],
        status=row["status"],
        calendar_entry_id=row["calendar_entry_id"],
        item_id=row["item_id"],
        content_path=row["content_path"] or "",
        summary=row["summary"] or "",
        scripts=content["scripts"],
        materials=content["materials"],
        uncertainty_list=content["uncertainty_list"],
        first_step=content["first_step"],
        fallback_plan=content["fallback_plan"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        metadata=metadata,
    )


def _fallback_content(row: aiosqlite.Row) -> dict[str, Any]:
    return _build_content(ContextPackTarget(title=row["title"], pack_type=row["pack_type"]))


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(UTC)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "pack"
