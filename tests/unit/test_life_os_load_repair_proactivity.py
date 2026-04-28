from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import aiosqlite
import pytest

from kora_v2.life.load import LifeLoadEngine, LoadCorrection
from kora_v2.life.proactivity_policy import (
    NotificationResult,
    NudgeCandidate,
    NudgeFeedbackInput,
    ProactivityPolicyEngine,
)
from kora_v2.life.repair import DayRepairEngine

pytestmark = pytest.mark.asyncio


class _SupportModule:
    name = "adhd"

    def load_factors(self, day_context, ledger):
        return [
            {
                "source": "support:adhd",
                "label": "active ADHD support adds transition pressure",
                "weight": 0.11,
            }
        ]


class _SupportRegistry:
    def active_modules(self):
        return [_SupportModule()]


async def _count(db_path, table: str, where: str = "1 = 1") -> int:
    async with aiosqlite.connect(str(db_path)) as db:
        row = await (await db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}")).fetchone()
    return int(row[0])


async def _fetch_one(db_path, query: str, params=()):
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        return await (await db.execute(query, params)).fetchone()


async def _seed_life_inputs(db_path, day: date) -> None:
    now = datetime.now(UTC)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.executescript(
            """
            CREATE TABLE calendar_entries (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL DEFAULT 'event',
                title TEXT NOT NULL,
                starts_at TEXT NOT NULL,
                ends_at TEXT,
                source TEXT NOT NULL DEFAULT 'kora',
                metadata TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE items (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'planned',
                estimated_minutes INTEGER,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE energy_log (
                id TEXT PRIMARY KEY,
                level TEXT NOT NULL,
                source TEXT NOT NULL,
                logged_at TEXT NOT NULL
            );
            CREATE TABLE meal_log (
                id TEXT PRIMARY KEY,
                meal_type TEXT NOT NULL,
                description TEXT NOT NULL,
                logged_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE support_profiles (
                id TEXT PRIMARY KEY,
                profile_key TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                status TEXT NOT NULL,
                settings TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        for idx in range(4):
            start = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=9 + idx)
            await db.execute(
                """
                INSERT INTO calendar_entries
                    (id, kind, title, starts_at, ends_at, created_at, updated_at)
                VALUES (?, 'meeting', ?, ?, ?, ?, ?)
                """,
                (
                    f"cal-{idx}",
                    f"Meeting {idx}",
                    start.isoformat(),
                    (start + timedelta(minutes=45)).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        for idx in range(8):
            await db.execute(
                "INSERT INTO items (id, title, status, estimated_minutes, updated_at) VALUES (?, ?, 'planned', 45, ?)",
                (f"item-{idx}", f"Task {idx}", now.isoformat()),
            )
        await db.execute(
            "INSERT INTO energy_log (id, level, source, logged_at) VALUES ('energy-1', 'low', 'user', ?)",
            (now.isoformat(),),
        )
        await db.execute(
            """
            INSERT INTO support_profiles
                (id, profile_key, display_name, status, settings, created_at, updated_at)
            VALUES ('profile-adhd', 'adhd', 'ADHD support', 'active', '{}', ?, ?)
            """,
            (now.isoformat(), now.isoformat()),
        )
        await db.commit()


async def test_life_load_engine_persists_explainable_assessment_and_correction(tmp_path):
    db_path = tmp_path / "life.db"
    today = date.today()
    await _seed_life_inputs(db_path, today)

    engine = LifeLoadEngine(db_path, support_registry=_SupportRegistry())
    assessment = await engine.assess_day(today, force=True)

    assert assessment.band in {"high", "overloaded", "stabilization"}
    assert any(f.source == "energy" for f in assessment.factors)
    assert any(f.source == "support:adhd" for f in assessment.factors)
    explanation = await engine.explain(assessment.id)
    assert explanation.assessment_id == assessment.id
    assert "load" in explanation.summary

    corrected = await engine.apply_user_correction(
        assessment.id,
        LoadCorrection(
            corrected_band="normal",
            corrected_score=0.35,
            profile_key="adhd",
            weight=-0.2,
            details="Meetings were easier than expected.",
        ),
    )
    assert corrected.band == "normal"
    assert corrected.confirmed_by_user is True
    assert await _count(db_path, "support_profile_signals") == 1
    assert await _count(db_path, "domain_events", "event_type = 'LOAD_ASSESSMENT_CORRECTED'") == 1


async def test_day_repair_engine_proposes_and_applies_revision_with_proof(tmp_path):
    db_path = tmp_path / "life.db"
    today = date.today()
    now = datetime.now(UTC)
    engine = DayRepairEngine(db_path)
    await engine.ensure_schema()
    async with aiosqlite.connect(str(db_path)) as db:
        await db.executescript(
            """
            CREATE TABLE items (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'planned',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE item_state_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                reason TEXT,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE calendar_entries (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL DEFAULT 'event',
                title TEXT NOT NULL,
                description TEXT,
                starts_at TEXT NOT NULL,
                ends_at TEXT,
                source TEXT NOT NULL DEFAULT 'kora',
                metadata TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        await db.execute(
            """
            INSERT INTO day_plans
                (id, plan_date, revision, status, generated_from, summary, created_at, updated_at)
            VALUES ('plan-1', ?, 1, 'active', 'test', 'Morning plan', ?, ?)
            """,
            (today.isoformat(), now.isoformat(), now.isoformat()),
        )
        await db.execute(
            "INSERT INTO items (id, title, status, updated_at) VALUES ('item-1', 'Submit form', 'planned', ?)",
            (now.isoformat(),),
        )
        await db.execute(
            """
            INSERT INTO day_plan_entries
                (id, day_plan_id, item_id, title, entry_type, intended_start,
                 intended_end, status, reality_state, created_at, updated_at)
            VALUES ('entry-1', 'plan-1', 'item-1', 'Submit form', 'task', ?, ?, 'planned', 'partial', ?, ?)
            """,
            (
                (now - timedelta(hours=2)).isoformat(),
                (now - timedelta(hours=1)).isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        await db.commit()

    evaluation = await engine.evaluate(today)
    assert evaluation.day_plan_id == "plan-1"
    assert any(d.divergence_type == "entry_partial" for d in evaluation.divergences)

    actions = await engine.propose(evaluation)
    assert actions
    assert actions[0].action_type == "shrink_task"
    assert await _count(db_path, "plan_repair_actions") == len(actions)

    result = await engine.apply([a.id for a in actions], user_confirmed=False)
    assert result.new_day_plan_id is not None
    assert result.superseded_day_plan_id == "plan-1"
    assert result.applied_action_ids == [a.id for a in actions]
    assert await _count(db_path, "day_plans", "status = 'active'") == 1
    assert await _count(db_path, "day_plans", "status = 'superseded'") == 1
    assert await _count(db_path, "life_events", "event_type LIKE 'repair.%'") == len(actions)
    assert await _count(db_path, "domain_events", "event_type = 'DAY_PLAN_REPAIRED'") == 1

    repaired_entry = await _fetch_one(
        db_path,
        "SELECT title, status FROM day_plan_entries WHERE day_plan_id = ?",
        (result.new_day_plan_id,),
    )
    assert "first move" in repaired_entry["title"]
    assert repaired_entry["status"] == "partial"


async def test_proactivity_policy_records_suppression_feedback_and_future_decision(tmp_path):
    db_path = tmp_path / "life.db"
    now = datetime.now(UTC)
    engine = ProactivityPolicyEngine(db_path)
    await engine.ensure_schema()
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """
            INSERT INTO support_mode_state
                (id, mode, status, started_at, reason)
            VALUES ('mode-1', 'quiet', 'active', ?, 'user asked for quiet')
            """,
            (now.isoformat(),),
        )
        await db.commit()

    quiet_decision = await engine.decide(
        NudgeCandidate(candidate_type="admin_followup", urgency="normal", support_tags=["admin"])
    )
    assert quiet_decision.decision == "suppress"
    assert "quiet" in quiet_decision.reason

    critical_decision = await engine.decide(
        NudgeCandidate(candidate_type="medication", urgency="critical", support_tags=["health"])
    )
    assert critical_decision.decision == "send_now"
    reconciled = await engine.reconcile_delivery(
        critical_decision.id,
        NotificationResult(notification_id="notif-1", delivered=True, reason="sent"),
    )
    assert reconciled.notification_id == "notif-1"

    await engine.record_feedback(
        critical_decision.id,
        NudgeFeedbackInput(feedback="too_much", details="bad moment"),
    )
    await engine.record_feedback(
        quiet_decision.id,
        NudgeFeedbackInput(feedback="wrong", details="not relevant"),
    )

    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("UPDATE support_mode_state SET status = 'ended', ended_at = ?", (now.isoformat(),))
        await db.commit()

    deferred = await engine.decide(
        NudgeCandidate(candidate_type="medication", urgency="normal", support_tags=["health"])
    )
    assert deferred.decision == "send_now"
    assert await _count(db_path, "nudge_decisions") == 3
    assert await _count(db_path, "nudge_feedback") == 2
    assert await _count(db_path, "domain_events", "event_type = 'NUDGE_FEEDBACK_RECORDED'") == 2


async def test_proactivity_policy_feedback_can_defer_future_low_pressure_nudges(tmp_path):
    db_path = tmp_path / "life.db"
    engine = ProactivityPolicyEngine(db_path)

    first = await engine.decide(NudgeCandidate(candidate_type="paperwork", urgency="normal"))
    second = await engine.decide(NudgeCandidate(candidate_type="paperwork", urgency="normal"))
    await engine.record_feedback(first.id, NudgeFeedbackInput(feedback="too_much"))
    await engine.record_feedback(second.id, NudgeFeedbackInput(feedback="bad_timing"))

    later = await engine.decide(NudgeCandidate(candidate_type="paperwork", urgency="normal"))
    assert later.decision == "defer"
    assert "less pressure" in later.reason

    row = await _fetch_one(
        db_path,
        "SELECT candidate_payload FROM nudge_decisions WHERE id = ?",
        (later.id,),
    )
    assert json.loads(row["candidate_payload"]) == {}
