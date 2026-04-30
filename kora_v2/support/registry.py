"""SQLite-backed support profile registry and runtime module loader."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import aiosqlite
from pydantic import BaseModel, Field

from kora_v2.support.modules import (
    ADHDSupportModule,
    AnxietySupportModule,
    AutismSensorySupportModule,
    BurnoutSupportModule,
    LowEnergySupportModule,
)
from kora_v2.support.profiles import (
    ProfileRuntimeConfig,
    ProfileStatus,
    SupportProfile,
    SupportProfileDefinition,
    ensure_support_profile_tables,
    from_json,
    now_utc,
    profile_from_row,
    to_json,
)
from kora_v2.support.protocol import (
    ContextPackRule,
    FutureBridgeRule,
    LoadFactor,
    PlanningRule,
    ProactivityRule,
    RepairRule,
    StabilizationRule,
    SupportModule,
)

DEFAULT_PROFILE_DEFINITIONS: dict[str, SupportProfileDefinition] = {
    "general_life_management": SupportProfileDefinition(
        profile_key="general_life_management",
        display_name="General life management",
        default_status="active",
        settings={
            "nudge_tolerance": "medium",
            "protect_maintenance": True,
            "quiet_hours": {"start": "22:00", "end": "08:00"},
            "baseline_rules": {
                "protect_food_medication_sleep": True,
                "prefer_one_next_action": True,
                "shame_safe_review": True,
            },
        },
    ),
    "adhd": SupportProfileDefinition(
        profile_key="adhd",
        display_name="ADHD support",
        default_status="suggested",
        settings={"time_correction_factor": 1.5, "transition_buffer_minutes": 10},
    ),
    "anxiety": SupportProfileDefinition(
        profile_key="anxiety",
        display_name="Anxiety support",
        default_status="suggested",
        settings={"context_pack_before_admin": True, "reassurance_cooldown_minutes": 120},
    ),
    "autism_sensory": SupportProfileDefinition(
        profile_key="autism_sensory",
        display_name="Autism and sensory support",
        default_status="suggested",
        settings={"decompression_minutes": 20, "protect_routines": True},
    ),
    "low_energy": SupportProfileDefinition(
        profile_key="low_energy",
        display_name="Low-energy support",
        default_status="suggested",
        settings={"maintenance_first": True, "max_nonessential_low_energy": 1},
    ),
    "burnout": SupportProfileDefinition(
        profile_key="burnout",
        display_name="Burnout support",
        default_status="suggested",
        settings={"recovery_blocks_required": True, "max_priority_items": 3},
    ),
}


MODULE_FACTORIES = {
    "adhd": ADHDSupportModule,
    "anxiety": AnxietySupportModule,
    "autism_sensory": AutismSensorySupportModule,
    "low_energy": LowEnergySupportModule,
    "burnout": BurnoutSupportModule,
}


class RuntimeSupportRules(BaseModel):
    """Decision-changing rules contributed by active support modules."""

    active_profiles: list[str] = Field(default_factory=list)
    load_factors: list[LoadFactor] = Field(default_factory=list)
    planning_rules: list[PlanningRule] = Field(default_factory=list)
    repair_rules: list[RepairRule] = Field(default_factory=list)
    proactivity_rules: list[ProactivityRule] = Field(default_factory=list)
    stabilization_rules: list[StabilizationRule] = Field(default_factory=list)
    context_pack_rules: list[ContextPackRule] = Field(default_factory=list)
    future_bridge_rules: list[FutureBridgeRule] = Field(default_factory=list)
    supervisor_context: dict[str, Any] = Field(default_factory=dict)


class SupportRegistry:
    """Repository and module-loader for Life OS support profiles."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(str(self.db_path)) as db:
            await ensure_support_profile_tables(db)
            await db.commit()

    async def list_profiles(self, status: ProfileStatus | None = None) -> list[SupportProfile]:
        await self.ensure_schema()
        query = "SELECT * FROM support_profiles"
        args: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            args = (status,)
        query += " ORDER BY profile_key"

        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, args)
            rows = await cursor.fetchall()
        return [profile_from_row(row) for row in rows]

    async def get_profile(self, profile_key: str) -> SupportProfile | None:
        await self.ensure_schema()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM support_profiles WHERE profile_key = ?",
                (profile_key,),
            )
            row = await cursor.fetchone()
        return profile_from_row(row) if row else None

    async def upsert_profile(
        self,
        profile_key: str,
        display_name: str,
        *,
        status: ProfileStatus,
        settings: dict[str, Any],
        user_label: str | None = None,
    ) -> SupportProfile:
        await self.ensure_schema()
        now = now_utc().isoformat()
        existing = await self.get_profile(profile_key)
        profile_id = existing.id if existing else f"support-{uuid.uuid4().hex[:12]}"
        created_at = existing.created_at.isoformat() if existing else now

        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(
                """
                INSERT INTO support_profiles
                    (id, profile_key, display_name, status, user_label, settings,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    status = excluded.status,
                    user_label = excluded.user_label,
                    settings = excluded.settings,
                    updated_at = excluded.updated_at
                """,
                (
                    profile_id,
                    profile_key,
                    display_name,
                    status,
                    user_label,
                    to_json(settings),
                    created_at,
                    now,
                ),
            )
            await db.commit()

        profile = await self.get_profile(profile_key)
        if profile is None:
            raise RuntimeError(f"support profile {profile_key!r} was not persisted")
        return profile

    async def set_profile_status(
        self,
        profile_key: str,
        status: ProfileStatus,
        *,
        source: str = "support_registry",
        reason: str | None = None,
    ) -> SupportProfile:
        profile = await self.get_profile(profile_key)
        if profile is None:
            definition = DEFAULT_PROFILE_DEFINITIONS.get(profile_key)
            if definition is None:
                definition = SupportProfileDefinition(
                    profile_key=profile_key,
                    display_name=profile_key.replace("_", " ").title(),
                    default_status=status,
                    settings={},
                )
            profile = await self.upsert_profile(
                profile_key,
                definition.display_name,
                status=status,
                settings=definition.settings,
            )
        else:
            now = now_utc().isoformat()
            async with aiosqlite.connect(str(self.db_path)) as db:
                await db.execute(
                    "UPDATE support_profiles SET status = ?, updated_at = ? "
                    "WHERE profile_key = ?",
                    (status, now, profile_key),
                )
                await self._insert_domain_event(
                    db,
                    event_type="SUPPORT_PROFILE_STATUS_CHANGED",
                    aggregate_id=profile.id,
                    payload={
                        "profile_key": profile_key,
                        "status": status,
                        "source": source,
                        "reason": reason,
                    },
                )
                await db.commit()
            profile = await self.get_profile(profile_key)
            if profile is None:
                raise RuntimeError(f"support profile {profile_key!r} disappeared")
        return profile

    async def edit_profile_settings(
        self,
        profile_key: str,
        updates: dict[str, Any],
        *,
        source: str = "support_registry",
    ) -> SupportProfile:
        profile = await self.get_profile(profile_key)
        if profile is None:
            raise KeyError(profile_key)
        settings = {**profile.settings, **updates}
        now = now_utc().isoformat()
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(
                "UPDATE support_profiles SET settings = ?, updated_at = ? "
                "WHERE profile_key = ?",
                (to_json(settings), now, profile_key),
            )
            await self._insert_domain_event(
                db,
                event_type="SUPPORT_PROFILE_EDITED",
                aggregate_id=profile.id,
                payload={"profile_key": profile_key, "updates": updates, "source": source},
            )
            await db.commit()
        updated = await self.get_profile(profile_key)
        if updated is None:
            raise RuntimeError(f"support profile {profile_key!r} disappeared")
        return updated

    async def record_signal(
        self,
        profile_key: str,
        signal_type: str,
        *,
        weight: float,
        source: str,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        await self.ensure_schema()
        signal_id = f"support-signal-{uuid.uuid4().hex[:12]}"
        now = now_utc().isoformat()
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(
                """
                INSERT INTO support_profile_signals
                    (id, profile_key, signal_type, weight, source, confidence,
                     last_seen_at, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    profile_key,
                    signal_type,
                    weight,
                    source,
                    confidence,
                    now,
                    to_json(metadata or {}),
                    now,
                    now,
                ),
            )
            await self._insert_domain_event(
                db,
                event_type="SUPPORT_PROFILE_SIGNAL_RECORDED",
                aggregate_id=signal_id,
                payload={
                    "profile_key": profile_key,
                    "signal_type": signal_type,
                    "weight": weight,
                    "source": source,
                    "confidence": confidence,
                    "metadata": metadata or {},
                },
            )
            await db.commit()
        return signal_id

    async def runtime_config(self) -> ProfileRuntimeConfig:
        active = await self.list_profiles(status="active")
        return ProfileRuntimeConfig(
            active_profiles=[profile.profile_key for profile in active],
            settings_by_profile={
                profile.profile_key: profile.settings for profile in active
            },
        )

    async def active_modules(self) -> list[SupportModule]:
        config = await self.runtime_config()
        modules: list[SupportModule] = []
        for profile_key in config.active_profiles:
            factory = MODULE_FACTORIES.get(profile_key)
            if factory is None:
                continue
            modules.append(factory(config.settings_by_profile.get(profile_key, {})))
        return modules

    async def runtime_rules(
        self,
        *,
        day_context: Any | None = None,
        ledger: list[Any] | None = None,
        state: Any | None = None,
    ) -> RuntimeSupportRules:
        config = await self.runtime_config()
        modules = await self.active_modules()
        rules = RuntimeSupportRules(active_profiles=config.active_profiles)
        supervisor_modules: dict[str, Any] = {}
        for module in modules:
            rules.load_factors.extend(module.load_factors(day_context, ledger or []))
            rules.planning_rules.extend(module.planning_rules(day_context))
            rules.repair_rules.extend(module.repair_rules(state))
            rules.proactivity_rules.extend(module.proactivity_rules(state))
            rules.stabilization_rules.extend(module.stabilization_rules(state))
            rules.context_pack_rules.extend(module.context_pack_rules(state))
            rules.future_bridge_rules.extend(module.future_bridge_rules(state))
            supervisor_modules[module.name] = module.supervisor_context()

        rules.supervisor_context = {
            "active_support_profiles": config.active_profiles,
            "support_modules": supervisor_modules,
            "baseline": config.settings_by_profile.get("general_life_management", {}),
        }
        return rules

    async def _insert_domain_event(
        self,
        db: aiosqlite.Connection,
        *,
        event_type: str,
        aggregate_id: str | None,
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
                f"domain-{uuid.uuid4().hex[:12]}",
                event_type,
                "support_profile",
                aggregate_id,
                "SupportRegistry",
                to_json(payload),
                now_utc().isoformat(),
            ),
        )


def merged_profile_settings(profile: SupportProfile) -> dict[str, Any]:
    definition = DEFAULT_PROFILE_DEFINITIONS.get(profile.profile_key)
    if definition is None:
        return profile.settings
    return {**definition.settings, **from_json(to_json(profile.settings), {})}
