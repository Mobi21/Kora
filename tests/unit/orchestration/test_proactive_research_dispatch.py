"""Focused dispatch tests for proactive_research routing."""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from kora_v2.agents.background.proactive_handlers import proactive_research_step
from kora_v2.graph.dispatch import _orch_decompose_and_dispatch
from kora_v2.runtime.orchestration import (
    OrchestrationEngine,
    UserScheduleProfile,
    init_orchestration_schema,
)


@pytest.mark.asyncio
async def test_decompose_and_dispatch_uses_registered_proactive_research_pipeline(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "operational.db"
    await init_orchestration_schema(db_path)
    engine = OrchestrationEngine(
        db_path,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        memory_root=tmp_path / "_KoraMemory",
        tick_interval=0.01,
    )
    engine.working_docs.ensure_inbox()
    engine.templates.ensure_defaults()
    engine.templates.reload_if_changed()
    await engine.limiter.replay_from_log()

    raw = await _orch_decompose_and_dispatch(
        engine,
        {
            "goal": "Research better local-first reminders",
            "pipeline_name": "proactive_research",
            "stages": [{"name": "run", "tool_scope": ["recall"]}],
            "in_turn": False,
        },
        session_id="sess-proactive",
    )
    parsed = json.loads(raw)

    assert parsed["status"] == "ok"
    assert parsed["pipeline_name"] == "proactive_research"
    assert parsed["routing"] == "registered_pipeline"

    live_tasks = await engine.list_live_tasks()
    assert len(live_tasks) == 1
    task = live_tasks[0]
    assert task.pipeline_instance_id == parsed["pipeline_instance_id"]
    assert task.stage_name == "run"
    assert task.step_fn is proactive_research_step

    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM runtime_pipelines WHERE name = ?",
            ("proactive_research",),
        )
        row = await cursor.fetchone()
    assert row[0] == 0


@pytest.mark.asyncio
async def test_research_named_runtime_pipeline_routes_to_proactive_research(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "operational.db"
    await init_orchestration_schema(db_path)
    engine = OrchestrationEngine(
        db_path,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        memory_root=tmp_path / "_KoraMemory",
        tick_interval=0.01,
    )
    engine.working_docs.ensure_inbox()
    engine.templates.ensure_defaults()
    engine.templates.reload_if_changed()
    await engine.limiter.replay_from_log()

    raw = await _orch_decompose_and_dispatch(
        engine,
        {
            "goal": (
                "Research top developer productivity tools with privacy "
                "and local-first as the primary lens."
            ),
            "pipeline_name": "privacy_research",
            "intent_duration": "long",
            "stages": [
                {
                    "name": "Search and read current info on privacy",
                    "tool_scope": ["recall"],
                }
            ],
            "in_turn": False,
        },
        session_id="sess-privacy",
    )
    parsed = json.loads(raw)

    assert parsed["status"] == "ok"
    assert parsed["pipeline_name"] == "proactive_research"
    assert parsed["requested_pipeline_name"] == "privacy_research"
    assert parsed["routing"] == "registered_pipeline"

    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM pipeline_instances WHERE pipeline_name = ?",
            ("proactive_research",),
        )
        proactive_count = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM runtime_pipelines WHERE name = ?",
            ("privacy_research",),
        )
        runtime_count = (await cursor.fetchone())[0]

    assert proactive_count == 1
    assert runtime_count == 0


@pytest.mark.asyncio
async def test_cancel_probe_is_not_routed_to_proactive_research(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "operational.db"
    await init_orchestration_schema(db_path)
    engine = OrchestrationEngine(
        db_path,
        schedule_profile=UserScheduleProfile(timezone="UTC"),
        memory_root=tmp_path / "_KoraMemory",
        tick_interval=0.01,
    )
    engine.working_docs.ensure_inbox()
    engine.templates.ensure_defaults()
    engine.templates.reload_if_changed()
    await engine.limiter.replay_from_log()

    raw = await _orch_decompose_and_dispatch(
        engine,
        {
            "goal": (
                "cancel-probe: compare two launch-note wording options and "
                "keep it running in the background"
            ),
            "pipeline_name": "proactive_research",
            "intent_duration": "long",
            "stages": [{"name": "run", "tool_scope": ["read_file"]}],
            "in_turn": False,
        },
        session_id="sess-cancel-probe",
    )
    parsed = json.loads(raw)

    assert parsed["status"] == "ok"
    assert parsed["pipeline_name"] == "cancel_probe"
    assert "routing" not in parsed

    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM pipeline_instances WHERE pipeline_name = ?",
            ("cancel_probe",),
        )
        cancel_probe_count = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM pipeline_instances WHERE pipeline_name = ?",
            ("proactive_research",),
        )
        proactive_count = (await cursor.fetchone())[0]

    assert cancel_probe_count == 1
    assert proactive_count == 0
