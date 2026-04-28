from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import aiosqlite
import pytest

from kora_v2.life.context_packs import ContextPackService, ContextPackTarget
from kora_v2.life.future_bridge import DayOutcomeItem, FutureSelfBridgeService
from kora_v2.life.stabilization import StabilizationModeService, StabilizationReason
from kora_v2.life.trusted_support import (
    SocialSensoryInput,
    SocialSensorySupportService,
    TrustedSupportExportService,
)

pytestmark = pytest.mark.asyncio


class FakeDayPlanService:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def create_stabilization_plan(self, payload: dict) -> dict[str, str]:
        self.payloads.append(payload)
        return {"id": "day-plan-stabilized"}


async def test_stabilization_enters_exits_and_creates_reduced_plan(tmp_path: Path) -> None:
    db_path = tmp_path / "operational.db"
    day_plans = FakeDayPlanService()
    service = StabilizationModeService(db_path, day_plan_service=day_plans)

    state = await service.enter(
        StabilizationReason(
            trigger="low energy overload",
            user_report="I cannot function",
            load_band="high",
            warning_signs=["skipped meal", "shutdown risk"],
        ),
        user_confirmed=True,
    )

    assert state.mode == "stabilization"
    assert state.suppress_optional_work is True
    assert state.metadata["reduced_day_plan_id"] == "day-plan-stabilized"
    assert day_plans.payloads[0]["mode"] == "stabilization"
    assert await service.suppress_optional_work() is True

    exited = await service.exit("capacity improved")
    assert exited.status == "exited"
    assert exited.suppress_optional_work is False

    events = await _domain_events(db_path)
    assert [row["event_type"] for row in events] == [
        "STABILIZATION_MODE_ENTERED",
        "STABILIZATION_MODE_EXITED",
    ]


async def test_context_pack_writes_db_artifact_and_feedback(tmp_path: Path) -> None:
    db_path = tmp_path / "operational.db"
    memory_root = tmp_path / "memory"
    service = ContextPackService(db_path, memory_root)

    pack = await service.build_pack(
        ContextPackTarget(
            title="Benefits office call",
            pack_type="admin",
            item_id="item-1",
            materials=["case number"],
            known_uncertainties=["which form is needed"],
        )
    )
    await service.record_feedback(pack.id, "too_much", "shorter next time")

    assert pack.status == "ready"
    assert Path(pack.content_path).exists()
    content = Path(pack.content_path).read_text(encoding="utf-8")
    assert "## Scripts" in content
    assert "case number" in content

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        feedback = await (
            await db.execute("SELECT * FROM context_pack_feedback WHERE context_pack_id = ?", (pack.id,))
        ).fetchone()
    assert feedback["feedback"] == "too_much"

    event_types = [row["event_type"] for row in await _domain_events(db_path)]
    assert "CONTEXT_PACK_READY" in event_types
    assert "CONTEXT_PACK_FEEDBACK_RECORDED" in event_types


async def test_future_bridge_buckets_carryovers_and_next_morning_lookup(tmp_path: Path) -> None:
    db_path = tmp_path / "operational.db"
    memory_root = tmp_path / "memory"
    service = FutureSelfBridgeService(db_path, memory_root)

    bridge = await service.build_bridge(
        date(2026, 4, 28),
        source_day_plan_id="plan-1",
        outcomes=[
            DayOutcomeItem(title="Take meds", status="done"),
            DayOutcomeItem(title="Finish form", status="partial", carryover_reason="too large", next_move="Open form"),
            DayOutcomeItem(title="Call office", status="blocked", carryover_reason="closed", next_move="Call at 9"),
            DayOutcomeItem(title="Optimize notes", status="dropped"),
        ],
    )

    assert "1 done" in bridge.summary
    assert [item["title"] for item in bridge.carryovers] == ["Finish form", "Call office"]
    assert bridge.first_moves == ["Open form", "Call at 9"]
    assert bridge.outcomes["dropped"][0].title == "Optimize notes"
    assert Path(bridge.content_path or "").exists()

    tomorrow = await service.next_morning_lookup(date(2026, 4, 29))
    assert tomorrow is not None
    assert tomorrow.id == bridge.id

    event_types = [row["event_type"] for row in await _domain_events(db_path)]
    assert "FUTURE_SELF_BRIDGE_CREATED" in event_types


async def test_trusted_support_export_excludes_unselected_sensitive_sections(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "operational.db"
    service = TrustedSupportExportService(db_path)

    draft = await service.create_draft(
        title="Support note for roommate",
        available_sections={
            "current_plan": "Need quiet evening and one meal reminder.",
            "medication": "Sensitive medication detail",
            "private_journal": "Sensitive private text",
        },
        selected_section_names=["current_plan"],
        sensitive_section_names=["medication", "private_journal"],
    )

    assert draft.status == "draft"
    assert draft.selected_sections == {"current_plan": "Need quiet evening and one meal reminder."}
    assert "medication" in draft.excluded_sections
    assert "private_journal" in draft.excluded_sections
    assert "Sensitive medication detail" not in json.dumps(draft.selected_sections)

    reviewed = await service.mark_reviewed(draft.id)
    assert reviewed.status == "reviewed"

    event_types = [row["event_type"] for row in await _domain_events(db_path)]
    assert "TRUSTED_SUPPORT_EXPORT_DRAFTED" in event_types
    assert "TRUSTED_SUPPORT_EXPORT_REVIEWED" in event_types


async def test_social_sensory_helper_records_planning_rules(tmp_path: Path) -> None:
    db_path = tmp_path / "operational.db"
    service = SocialSensorySupportService(db_path)

    assessment = await service.record_assessment(
        SocialSensoryInput(
            social_commitments=3,
            social_intensity=5,
            sensory_intensity=5,
            transition_count=4,
            user_social_energy=1,
            recovery_debt=4,
            emotional_stakes=4,
        )
    )

    assert assessment.band == "overload"
    assert assessment.needs_decompression is True
    assert "add_decompression_block" in assessment.planning_rules
    assert "avoid_optional_social_commitments" in assessment.planning_rules
    assert "add_transition_buffers" in assessment.planning_rules

    event_types = [row["event_type"] for row in await _domain_events(db_path)]
    assert "SOCIAL_SENSORY_LOAD_ASSESSED" in event_types


async def _domain_events(db_path: Path) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM domain_events ORDER BY created_at")
        return await cursor.fetchall()
