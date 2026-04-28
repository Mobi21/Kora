"""First-run bootstrap for Life OS support profiles."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import aiosqlite
from pydantic import BaseModel, Field

from kora_v2.support.profiles import SupportProfile, now_utc, to_json
from kora_v2.support.registry import DEFAULT_PROFILE_DEFINITIONS, SupportRegistry


class SupportBootstrapResult(BaseModel):
    baseline_profile: SupportProfile
    suggested_profiles: list[SupportProfile] = Field(default_factory=list)
    active_profiles: list[SupportProfile] = Field(default_factory=list)
    created_profile_keys: list[str] = Field(default_factory=list)


class SupportProfileBootstrapService:
    """Creates baseline and suggested Life OS support profiles on first run."""

    def __init__(self, db_path: Path, registry: SupportRegistry | None = None) -> None:
        self.db_path = db_path
        self.registry = registry or SupportRegistry(db_path)

    async def bootstrap(
        self,
        *,
        selected_profile_keys: list[str] | None = None,
        suggested_profile_keys: list[str] | None = None,
        onboarding_settings: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> SupportBootstrapResult:
        selected = set(selected_profile_keys or [])
        suggested = set(suggested_profile_keys or DEFAULT_PROFILE_DEFINITIONS.keys())
        suggested.discard("general_life_management")
        created: list[str] = []

        baseline_definition = DEFAULT_PROFILE_DEFINITIONS["general_life_management"]
        baseline_settings = {
            **baseline_definition.settings,
            **(onboarding_settings or {}),
        }
        baseline = await self._create_if_missing(
            baseline_definition.profile_key,
            status="active",
            settings=baseline_settings,
            created=created,
        )

        active_profiles: list[SupportProfile] = []
        suggested_profiles: list[SupportProfile] = []

        for profile_key, definition in DEFAULT_PROFILE_DEFINITIONS.items():
            if profile_key == "general_life_management":
                continue
            if profile_key in selected:
                profile = await self._create_if_missing(
                    profile_key,
                    status="active",
                    settings=definition.settings,
                    created=created,
                )
                active_profiles.append(profile)
            elif profile_key in suggested:
                profile = await self._create_if_missing(
                    profile_key,
                    status="suggested",
                    settings=definition.settings,
                    created=created,
                )
                suggested_profiles.append(profile)

        await self._record_bootstrap_event(
            baseline.id,
            selected_profile_keys=sorted(selected),
            suggested_profile_keys=sorted(suggested),
            created_profile_keys=created,
            correlation_id=correlation_id,
        )

        return SupportBootstrapResult(
            baseline_profile=baseline,
            suggested_profiles=suggested_profiles,
            active_profiles=active_profiles,
            created_profile_keys=created,
        )

    async def _create_if_missing(
        self,
        profile_key: str,
        *,
        status: str,
        settings: dict[str, Any],
        created: list[str],
    ) -> SupportProfile:
        existing = await self.registry.get_profile(profile_key)
        if existing is not None:
            return existing

        definition = DEFAULT_PROFILE_DEFINITIONS[profile_key]
        profile = await self.registry.upsert_profile(
            profile_key,
            definition.display_name,
            status=status,
            settings=settings,
        )
        created.append(profile_key)
        return profile

    async def _record_bootstrap_event(
        self,
        baseline_profile_id: str,
        *,
        selected_profile_keys: list[str],
        suggested_profile_keys: list[str],
        created_profile_keys: list[str],
        correlation_id: str | None,
    ) -> None:
        await self.registry.ensure_schema()
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(
                """
                INSERT INTO domain_events
                    (id, event_type, aggregate_type, aggregate_id, source_service,
                     correlation_id, causation_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    f"domain-{uuid.uuid4().hex[:12]}",
                    "SUPPORT_PROFILE_BOOTSTRAPPED",
                    "support_profile",
                    baseline_profile_id,
                    "SupportProfileBootstrapService",
                    correlation_id,
                    to_json(
                        {
                            "selected_profile_keys": selected_profile_keys,
                            "suggested_profile_keys": suggested_profile_keys,
                            "created_profile_keys": created_profile_keys,
                        }
                    ),
                    now_utc().isoformat(),
                ),
            )
            await db.commit()
