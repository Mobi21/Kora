"""Unit tests for Life OS support profiles and runtime modules."""

from __future__ import annotations

import aiosqlite

from kora_v2.support import (
    SupportProfileBootstrapService,
    SupportRegistry,
    ensure_support_profile_tables,
)


async def _setup_db(path):
    async with aiosqlite.connect(str(path)) as db:
        await ensure_support_profile_tables(db)
        await db.commit()


async def test_bootstrap_creates_active_baseline_and_suggested_profiles(tmp_path):
    db_path = tmp_path / "operational.db"
    await _setup_db(db_path)

    result = await SupportProfileBootstrapService(db_path).bootstrap()

    assert result.baseline_profile.profile_key == "general_life_management"
    assert result.baseline_profile.status == "active"
    suggested = {profile.profile_key: profile for profile in result.suggested_profiles}
    assert set(suggested) == {
        "adhd",
        "anxiety",
        "autism_sensory",
        "low_energy",
        "burnout",
    }
    assert all(profile.status == "suggested" for profile in suggested.values())

    registry = SupportRegistry(db_path)
    runtime = await registry.runtime_rules()
    assert runtime.active_profiles == ["general_life_management"]
    assert runtime.load_factors == []
    assert runtime.planning_rules == []

    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT event_type FROM domain_events ORDER BY created_at"
        )
        event_types = [row[0] for row in await cursor.fetchall()]
    assert "SUPPORT_PROFILE_BOOTSTRAPPED" in event_types


async def test_active_condition_profiles_change_runtime_decision_rules(tmp_path):
    db_path = tmp_path / "operational.db"
    await _setup_db(db_path)

    await SupportProfileBootstrapService(db_path).bootstrap(
        selected_profile_keys=["adhd", "anxiety", "autism_sensory", "low_energy", "burnout"]
    )

    runtime = await SupportRegistry(db_path).runtime_rules()

    assert set(runtime.active_profiles) == {
        "general_life_management",
        "adhd",
        "anxiety",
        "autism_sensory",
        "low_energy",
        "burnout",
    }
    assert {rule.profile_key for rule in runtime.load_factors} == {
        "adhd",
        "anxiety",
        "autism_sensory",
        "low_energy",
        "burnout",
    }
    assert any(rule.effect == "create_context_pack_for_anxiety_admin" for rule in runtime.context_pack_rules)
    assert any(rule.effect == "enter_stabilization_on_very_low_energy" for rule in runtime.stabilization_rules)
    assert any(rule.effect == "carry_forward_first_move_not_whole_task" for rule in runtime.future_bridge_rules)


async def test_disabled_profile_stops_affecting_runtime(tmp_path):
    db_path = tmp_path / "operational.db"
    await _setup_db(db_path)

    registry = SupportRegistry(db_path)
    await SupportProfileBootstrapService(db_path, registry).bootstrap(
        selected_profile_keys=["anxiety"]
    )
    active_runtime = await registry.runtime_rules()
    assert any(rule.profile_key == "anxiety" for rule in active_runtime.context_pack_rules)

    await registry.set_profile_status(
        "anxiety",
        "disabled",
        source="unit_test",
        reason="user turned it off",
    )
    disabled_runtime = await registry.runtime_rules()

    assert "anxiety" not in disabled_runtime.active_profiles
    assert not any(rule.profile_key == "anxiety" for rule in disabled_runtime.context_pack_rules)


async def test_profile_settings_and_signals_are_durable(tmp_path):
    db_path = tmp_path / "operational.db"
    await _setup_db(db_path)

    registry = SupportRegistry(db_path)
    await SupportProfileBootstrapService(db_path, registry).bootstrap(
        selected_profile_keys=["adhd"]
    )

    updated = await registry.edit_profile_settings(
        "adhd",
        {"transition_buffer_minutes": 15},
        source="unit_test",
    )
    signal_id = await registry.record_signal(
        "adhd",
        "nudge_feedback",
        weight=-0.3,
        source="unit_test",
        confidence=0.9,
        metadata={"feedback": "too_much"},
    )

    assert updated.settings["transition_buffer_minutes"] == 15
    assert signal_id.startswith("support-signal-")

    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT settings FROM support_profiles WHERE profile_key = 'adhd'"
        )
        raw_settings = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM support_profile_signals WHERE profile_key = 'adhd'"
        )
        signal_count = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT event_type FROM domain_events WHERE event_type IN "
            "('SUPPORT_PROFILE_EDITED', 'SUPPORT_PROFILE_SIGNAL_RECORDED')"
        )
        events = {row[0] for row in await cursor.fetchall()}

    assert '"transition_buffer_minutes":15' in raw_settings
    assert signal_count == 1
    assert events == {"SUPPORT_PROFILE_EDITED", "SUPPORT_PROFILE_SIGNAL_RECORDED"}
