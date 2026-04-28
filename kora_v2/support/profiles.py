"""SQLite-backed Life OS support profile contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, Field

ProfileStatus = Literal["active", "suggested", "disabled", "archived"]
ProfileKey = Literal[
    "general_life_management",
    "adhd",
    "anxiety",
    "autism_sensory",
    "low_energy",
    "burnout",
]


class SupportProfile(BaseModel):
    """Runtime source of truth for a user support profile."""

    id: str
    profile_key: str
    display_name: str
    status: ProfileStatus = "active"
    user_label: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SupportProfileSignal(BaseModel):
    """Evidence that can adjust profile behavior without activating diagnoses."""

    id: str
    profile_key: str
    signal_type: str
    weight: float
    source: str
    confidence: float = 1.0
    last_seen_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SupportProfileDefinition(BaseModel):
    """Shipped profile defaults used by bootstrap and registry."""

    profile_key: str
    display_name: str
    default_status: ProfileStatus
    settings: dict[str, Any] = Field(default_factory=dict)


class ProfileRuntimeConfig(BaseModel):
    """Aggregated active support settings for downstream Life OS engines."""

    active_profiles: list[str] = Field(default_factory=list)
    settings_by_profile: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def is_active(self, profile_key: str) -> bool:
        return profile_key in set(self.active_profiles)


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_json(data: dict[str, Any] | list[Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def from_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


async def ensure_support_profile_tables(db: aiosqlite.Connection) -> None:
    """Create support-profile tables needed by the self-contained service."""

    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS support_profiles (
            id TEXT PRIMARY KEY,
            profile_key TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            user_label TEXT,
            settings TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
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


def profile_from_row(row: aiosqlite.Row) -> SupportProfile:
    return SupportProfile(
        id=row["id"],
        profile_key=row["profile_key"],
        display_name=row["display_name"],
        status=row["status"],
        user_label=row["user_label"],
        settings=from_json(row["settings"], {}),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def signal_from_row(row: aiosqlite.Row) -> SupportProfileSignal:
    last_seen_at = row["last_seen_at"]
    return SupportProfileSignal(
        id=row["id"],
        profile_key=row["profile_key"],
        signal_type=row["signal_type"],
        weight=row["weight"],
        source=row["source"],
        confidence=row["confidence"],
        last_seen_at=datetime.fromisoformat(last_seen_at) if last_seen_at else None,
        metadata=from_json(row["metadata"], {}),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
