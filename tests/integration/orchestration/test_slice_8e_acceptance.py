"""Phase 8e acceptance tests -- ProactiveAgent + reminder/wake-up infra.

Acceptance items:
- 59: ProactiveAgent Area A — pattern-based nudge delivered from insight
- 60: ProactiveAgent Area B — anticipatory prep briefing ready before event
- 61: ProactiveAgent Area C — rabbit-hole research, Kora-judged complete
- 62: ProactiveAgent Area D — contextual engagement on emotion shift
- 63: ProactiveAgent Area E — commitment tracking surfaces yesterday's promises
- 64: ProactiveAgent Area E — stuck detection offers help without being asked
- 65: ProactiveAgent Area E — connection making surfaces old vault notes
- 66: Reminders from routines fire via continuity_check pipeline
- 67: Wake-up briefing assembled overnight and delivered at wake time
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# pysqlite3 monkey-patch
try:
    import pysqlite3 as _pysqlite3  # type: ignore[import-untyped]

    sys.modules["sqlite3"] = _pysqlite3
except ImportError:
    pass

import aiosqlite

from kora_v2.core.calendar_models import CalendarEntry
from kora_v2.core.db import init_operational_db
from kora_v2.core.models import (
    DayContext,
    EnergyEstimate,
    Insight,
    MedicationStatus,
    RoutineStatus,
)
from kora_v2.life.reminders import Reminder, ReminderStore
from kora_v2.runtime.orchestration.worker_task import (
    StepContext,
    WorkerTask,
    WorkerTaskConfig,
)

_CTX_PATCH = (
    "kora_v2.agents.background.proactive_handlers.get_autonomous_context"
)


# ══════════════════════════════════════════════════════════════════════════
# Helpers (mirrors patterns from earlier slice acceptance suites)
# ══════════════════════════════════════════════════════════════════════════


def _make_task(stage_name: str = "run", goal: str = "acceptance test") -> WorkerTask:
    return WorkerTask(
        id=f"task-8e-{uuid.uuid4().hex[:6]}",
        pipeline_instance_id="8e-acceptance",
        stage_name=stage_name,
        config=WorkerTaskConfig(
            preset="bounded_background",
            max_duration_seconds=1800,
            checkpoint_every_seconds=300,
            request_class="background",  # type: ignore[arg-type]
            allowed_states=frozenset(),
            tool_scope=[],
            pause_on_conversation=False,
            pause_on_topic_overlap=False,
            report_via=frozenset(),
            blocks_parent=False,
        ),
        goal=goal,
        system_prompt="",
    )


def _make_ctx(task: WorkerTask) -> StepContext:
    return StepContext(
        task=task,
        limiter=MagicMock(),
        cancellation_flag=lambda: False,
        now=lambda: datetime.now(UTC),
    )


def _delivery(delivered: bool = True) -> MagicMock:
    r = MagicMock()
    r.delivered = delivered
    return r


def _empty_day_ctx() -> DayContext:
    today = datetime.now(UTC).date()
    return DayContext(
        date=today,
        day_of_week=today.strftime("%A"),
        schedule=[],
        medication_status=MedicationStatus(),
        routine_status=RoutineStatus(),
    )


def _calendar_entry(
    title: str,
    starts_at: datetime,
    *,
    kind: str = "event",
) -> CalendarEntry:
    now = datetime.now(UTC)
    return CalendarEntry(
        id=uuid.uuid4().hex[:8],
        kind=kind,  # type: ignore[arg-type]
        title=title,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        created_at=now,
        updated_at=now,
    )


def _make_container(tmp_path: Path) -> MagicMock:
    """Build a baseline mock container the proactive handlers can read."""
    container = MagicMock()

    container.llm = AsyncMock()
    container.llm.chat = AsyncMock(return_value="")

    from kora_v2.memory.store import FilesystemMemoryStore

    mem_path = tmp_path / "_KoraMemory"
    mem_path.mkdir(parents=True, exist_ok=True)
    container.memory_store = FilesystemMemoryStore(mem_path)

    gate = MagicMock()
    gate.send_templated = AsyncMock(return_value=_delivery(True))
    container.notification_gate = gate

    engine = MagicMock()
    engine.get_insights = AsyncMock(return_value=[])
    engine.build_day_context = AsyncMock(return_value=_empty_day_ctx())
    container.context_engine = engine

    rs = MagicMock()
    rs.get_due_reminders = AsyncMock(return_value=[])
    rs.get_pending = AsyncMock(return_value=[])
    rs.mark_delivered = AsyncMock()
    rs.reschedule_recurring = AsyncMock(return_value=None)
    rs.deliver_and_reschedule = AsyncMock(return_value=None)
    container.reminder_store = rs

    pdb = MagicMock()
    pdb.search = AsyncMock(return_value=[])
    container.projection_db = pdb

    emitter = MagicMock()
    emitter.emit = AsyncMock()
    container.event_emitter = emitter

    return container


async def _setup_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "operational.db"
    await init_operational_db(db_path)
    return db_path


async def _run(handler, task, ctx, container, db_path):
    with patch(_CTX_PATCH) as mock_ctx:
        mock_ctx.return_value = MagicMock(
            container=container, db_path=db_path
        )
        return await handler(task, ctx)


# ══════════════════════════════════════════════════════════════════════════
# 59: ProactiveAgent Area A — pattern-based nudge from insight
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance59_PatternBasedNudge:
    async def test_pattern_scan_delivers_nudge(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)
        container.context_engine.get_insights = AsyncMock(
            return_value=[
                Insight(
                    type="energy_calendar_mismatch",
                    title="Meetings during crash window",
                    description=(
                        "4 meetings landed in your 14:00-16:00 low-energy window"
                    ),
                    confidence=0.82,
                    domain="adhd",
                    evidence=["evt-1", "evt-2", "evt-3", "evt-4"],
                )
            ]
        )

        from kora_v2.agents.background.proactive_handlers import (
            proactive_pattern_scan_step,
        )

        task = _make_task()
        ctx = _make_ctx(task)
        result = await _run(
            proactive_pattern_scan_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        container.notification_gate.send_templated.assert_awaited()
        # The nudge text should reference the insight
        call = container.notification_gate.send_templated.await_args
        assert call.args[0] == "pattern_nudge"
        assert call.kwargs.get("title") == "Meetings during crash window"


# ══════════════════════════════════════════════════════════════════════════
# 60: ProactiveAgent Area B — anticipatory prep briefing
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance60_AnticipatoryPrep:
    async def test_briefing_written_before_event(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        # Schedule an event 2h from now
        now = datetime.now(UTC)
        evt = _calendar_entry("Important strategy meeting", now + timedelta(hours=2))
        container.context_engine.build_day_context = AsyncMock(
            return_value=_empty_day_ctx().model_copy(
                update={"schedule": [evt]}
            )
        )

        # ProjectionDB returns a related note
        from kora_v2.memory.projection import MemoryRecord

        container.projection_db.search = AsyncMock(
            return_value=[
                MemoryRecord(
                    id="m1",
                    content="Last quarter we discussed similar strategy points",
                )
            ]
        )

        from kora_v2.agents.background.proactive_handlers import (
            anticipatory_prep_step,
        )

        task = _make_task()
        ctx = _make_ctx(task)
        result = await _run(
            anticipatory_prep_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"

        # Briefing in Inbox
        inbox = tmp_path / "_KoraMemory" / "Inbox"
        files = list(inbox.glob("prep-briefing-*.md"))
        assert len(files) == 1
        body = files[0].read_text(encoding="utf-8")
        assert "Important strategy meeting" in body
        assert "Last quarter we discussed" in body

        # High-priority event within 4h triggers the nudge
        container.notification_gate.send_templated.assert_awaited()


# ══════════════════════════════════════════════════════════════════════════
# 61: ProactiveAgent Area C — rabbit-hole research
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance61_ProactiveResearch:
    async def test_research_completes_and_writes_report(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)
        container.llm.chat = AsyncMock(
            return_value=(
                "# Research Findings\n\n"
                "Synthesized cross-references across memory results."
            )
        )
        from kora_v2.memory.projection import MemoryRecord

        container.projection_db.search = AsyncMock(
            return_value=[
                MemoryRecord(id="m1", content="prior research note"),
                MemoryRecord(id="m2", content="related insight from last week"),
            ]
        )

        from kora_v2.agents.background.proactive_handlers import (
            proactive_research_step,
        )
        from kora_v2.runtime.orchestration.worker_task import Checkpoint

        # Step 1: drives memory search
        task = _make_task(goal="Investigate ADHD coping mechanisms")
        ctx = _make_ctx(task)
        scratch: dict = {}

        async def cb(state: dict) -> None:
            scratch.update(state)

        ctx.checkpoint_callback = cb

        result_a = await _run(
            proactive_research_step, task, ctx, container, db_path
        )
        assert result_a.outcome == "continue"
        assert scratch.get("step_index") == 1
        assert len(scratch.get("findings", [])) > 0

        # Step 2: drives synthesis + write
        task.checkpoint_blob = Checkpoint(
            task_id=task.id,
            created_at=datetime.now(UTC),
            state=task.state,
            current_step_index=1,
            scratch_state=scratch,
        )
        result_b = await _run(
            proactive_research_step, task, ctx, container, db_path
        )
        assert result_b.outcome == "complete"

        inbox = tmp_path / "_KoraMemory" / "Inbox"
        files = list(inbox.glob("research-*.md"))
        assert len(files) == 1
        body = files[0].read_text(encoding="utf-8")
        assert "Research Findings" in body
        assert "status: done" in body


# ══════════════════════════════════════════════════════════════════════════
# 62: ProactiveAgent Area D — contextual engagement on emotion shift
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance62_ContextualEngagement:
    async def test_emotion_shift_triggers_check_in(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        from kora_v2.agents.background.proactive_handlers import (
            contextual_engagement_step,
        )

        task = _make_task(goal="emotion_shift_detected: user energy dropped")
        ctx = _make_ctx(task)
        result = await _run(
            contextual_engagement_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        container.notification_gate.send_templated.assert_awaited()
        call = container.notification_gate.send_templated.await_args
        subject = call.kwargs.get("subject", "")
        # Emotional check-in message is empathetic
        assert (
            "shift in your energy" in subject
            or "How are you doing" in subject
        )


# ══════════════════════════════════════════════════════════════════════════
# 63: ProactiveAgent Area E — commitment tracking surfaces yesterday's promises
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance63_CommitmentTracking:
    async def test_extracts_yesterday_commitments(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        # Insert a session transcript from a few hours ago with commitments
        recent = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO session_transcripts "
                "(session_id, created_at, ended_at, message_count, messages) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "sess-yest",
                    recent,
                    recent,
                    2,
                    json.dumps(
                        [
                            {
                                "role": "user",
                                "content": (
                                    "I'll send Mark the design doc tomorrow morning."
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    "And I should call my sister this week."
                                ),
                            },
                        ]
                    ),
                ),
            )
            await db.commit()

        container.llm.chat = AsyncMock(
            return_value=json.dumps(
                [
                    {
                        "commitment": "Send Mark the design doc",
                        "urgency": "high",
                    },
                    {
                        "commitment": "Call sister this week",
                        "urgency": "medium",
                    },
                ]
            )
        )

        from kora_v2.agents.background.proactive_handlers import (
            commitment_tracking_step,
        )

        task = _make_task()
        ctx = _make_ctx(task)
        result = await _run(
            commitment_tracking_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        assert "2 commitments" in (result.result_summary or "")

        inbox = tmp_path / "_KoraMemory" / "Inbox"
        files = list(inbox.glob("commitments-*.md"))
        assert len(files) == 1
        body = files[0].read_text(encoding="utf-8")
        assert "Send Mark the design doc" in body
        assert "Call sister this week" in body
        # Urgency labels surfaced
        assert "[HIGH]" in body
        assert "[MEDIUM]" in body


# ══════════════════════════════════════════════════════════════════════════
# 64: ProactiveAgent Area E — stuck detection offers help unprompted
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance64_StuckDetection:
    async def test_stale_in_progress_item_triggers_help_offer(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        # Insert two stale tasks (3+ days old, in_progress)
        old_ts = (datetime.now(UTC) - timedelta(days=4)).isoformat()
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO items "
                "(id, type, owner, title, status, created_at, updated_at) "
                "VALUES (?, 'task', 'kora', ?, 'in_progress', ?, ?)",
                ("item-a", "Refactor auth module", old_ts, old_ts),
            )
            await db.execute(
                "INSERT INTO items "
                "(id, type, owner, title, status, created_at, updated_at) "
                "VALUES (?, 'task', 'kora', ?, 'in_progress', ?, ?)",
                ("item-b", "Plan Q3 OKRs", old_ts, old_ts),
            )
            await db.commit()

        from kora_v2.agents.background.proactive_handlers import (
            stuck_detection_step,
        )

        task = _make_task()
        ctx = _make_ctx(task)
        result = await _run(
            stuck_detection_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        # Each stale item triggers a help offer (cap is 3)
        assert container.notification_gate.send_templated.await_count >= 2
        # Verify it was offered without the user asking
        subjects = [
            c.kwargs.get("subject", "")
            for c in container.notification_gate.send_templated.await_args_list
        ]
        joined = " ".join(subjects)
        assert "Want help" in joined or "breaking" in joined


# ══════════════════════════════════════════════════════════════════════════
# 65: ProactiveAgent Area E — connection making surfaces old vault notes
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance65_ConnectionMaking:
    async def test_old_vault_note_surfaced_for_recent_topic(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        # Recent signal
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO signal_queue "
                "(id, session_id, message_text, signal_types, priority, "
                " status, created_at) "
                "VALUES (?, 'sess-1', ?, ?, 1, 'pending', ?)",
                (
                    "sig-1",
                    "Thinking about a meditation habit again",
                    json.dumps(["life_event"]),
                    now,
                ),
            )
            await db.commit()

        # Old vault record (>= 7 days ago)
        from kora_v2.memory.projection import MemoryRecord

        old_iso = (datetime.now(UTC) - timedelta(days=14)).isoformat()
        old_note = MemoryRecord(
            id="old-meditation",
            content=(
                "Tried morning meditation last month — kept it up for 5 days "
                "but lost the thread"
            ),
            created_at=old_iso,
        )
        container.projection_db.search = AsyncMock(return_value=[old_note])

        from kora_v2.agents.background.proactive_handlers import (
            connection_making_step,
        )

        task = _make_task()
        ctx = _make_ctx(task)
        result = await _run(
            connection_making_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        assert "1 connections" in (result.result_summary or "")
        container.notification_gate.send_templated.assert_awaited()
        call = container.notification_gate.send_templated.await_args
        subject = call.kwargs.get("subject", "")
        # Surfaces the old note content
        assert "morning meditation" in subject or "wrote about" in subject


# ══════════════════════════════════════════════════════════════════════════
# 66: Reminders from routines fire via continuity_check pipeline
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance66_RemindersFireViaContinuity:
    async def test_routine_reminder_fires_through_pipeline(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        # A real ReminderStore so the pipeline can mark delivered
        store = ReminderStore(db_path)
        container.reminder_store = store

        # Create a routine-source reminder due now
        now = datetime.now(UTC)
        rid = await store.create_reminder(
            title="Take morning meds",
            description="Adderall 20mg",
            due_at=now - timedelta(minutes=2),
            source="routine",
        )

        from kora_v2.agents.background.proactive_handlers import (
            continuity_check_step,
        )

        task = _make_task(stage_name="run")
        ctx = _make_ctx(task)
        result = await _run(
            continuity_check_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        # Pipeline delivered the reminder via NotificationGate
        container.notification_gate.send_templated.assert_awaited()

        # Reminder is marked delivered (no longer pending)
        pending = await store.get_pending()
        assert rid not in [r.id for r in pending]

    async def test_recurring_routine_reminder_reschedules(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)
        store = ReminderStore(db_path)
        container.reminder_store = store

        original_due = datetime.now(UTC) - timedelta(minutes=1)
        await store.create_reminder(
            title="Daily vitamins",
            due_at=original_due,
            source="routine",
            repeat_rule="daily",
        )

        from kora_v2.agents.background.proactive_handlers import (
            continuity_check_step,
        )

        task = _make_task()
        ctx = _make_ctx(task)
        result = await _run(
            continuity_check_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        # Original delivered, new occurrence scheduled
        pending = await store.get_pending()
        assert len(pending) == 1
        assert pending[0].title == "Daily vitamins"
        delta = pending[0].due_at - original_due
        assert abs(delta - timedelta(days=1)) < timedelta(seconds=5)

    async def test_continuity_check_uses_db_reminder_store_fallback(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)
        container.reminder_store = None
        store = ReminderStore(db_path)
        rid = await store.create_reminder(
            title="Morning standup",
            due_at=datetime.now(UTC) - timedelta(minutes=1),
            source="user",
        )

        from kora_v2.agents.background.proactive_handlers import (
            continuity_check_step,
        )

        task = _make_task()
        ctx = _make_ctx(task)
        result = await _run(
            continuity_check_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        pending = await store.get_pending()
        assert rid not in [r.id for r in pending]


# ══════════════════════════════════════════════════════════════════════════
# 67: Wake-up briefing assembled overnight and delivered at wake time
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance67_WakeUpBriefing:
    async def test_morning_briefing_assembled(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        now = datetime.now(UTC)
        today = now.date()

        # Today's schedule + insights + items_due
        schedule = [
            _calendar_entry("9am standup", now + timedelta(hours=1)),
            _calendar_entry("Lunch with Mark", now + timedelta(hours=4)),
        ]
        day_ctx = DayContext(
            date=today,
            day_of_week=today.strftime("%A"),
            schedule=schedule,
            energy=EnergyEstimate(
                level="medium",
                focus="normal",
                confidence=0.7,
                source="heuristic",
            ),
            items_due=[
                {"title": "Review the design doc", "status": "planned"},
            ],
        )
        container.context_engine.build_day_context = AsyncMock(
            return_value=day_ctx
        )
        container.context_engine.get_insights = AsyncMock(
            return_value=[
                Insight(
                    type="medication_focus",
                    title="Meds correlate with deep focus",
                    description=(
                        "Focus blocks are 3x longer on medicated days"
                    ),
                    confidence=0.88,
                    domain="adhd",
                    evidence=["med-1", "med-2"],
                )
            ]
        )

        # Reminder pending for today
        reminder = Reminder(
            id="rem-call",
            title="Call dentist for cleaning",
            due_at=now + timedelta(hours=2),
        )
        container.reminder_store.get_pending = AsyncMock(
            return_value=[reminder]
        )

        # Overnight completion
        last_night = (now - timedelta(hours=4)).isoformat()
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO items "
                "(id, type, owner, title, status, created_at, updated_at) "
                "VALUES (?, 'task', 'kora', ?, 'done', ?, ?)",
                ("item-done", "Closed the Phase 8d PR", last_night, last_night),
            )
            await db.commit()

        from kora_v2.agents.background.proactive_handlers import (
            wake_up_preparation_step,
        )

        task = _make_task()
        ctx = _make_ctx(task)
        result = await _run(
            wake_up_preparation_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"

        # Briefing assembled to Inbox
        inbox = tmp_path / "_KoraMemory" / "Inbox"
        files = list(inbox.glob("morning-briefing-*.md"))
        assert len(files) == 1
        body = files[0].read_text(encoding="utf-8")

        # All sections present
        assert "Good Morning" in body
        assert "9am standup" in body
        assert "Lunch with Mark" in body
        assert "Review the design doc" in body
        assert "Call dentist for cleaning" in body
        assert "Meds correlate with deep focus" in body
        assert "Closed the Phase 8d PR" in body

        # Delivered as the wake notification
        container.notification_gate.send_templated.assert_awaited()


# ══════════════════════════════════════════════════════════════════════════
# Smoke test: all 13 handlers are wired in core_pipelines as real funcs
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance_PipelinesWired:
    """Sanity check that the dispatcher will actually invoke the real
    handlers, not the no-op stubs, for the Phase 8e pipelines."""

    def test_all_thirteen_pipelines_use_real_handlers(self) -> None:
        from kora_v2.runtime.orchestration.core_pipelines import (
            _stub_step,
            build_core_pipelines,
            core_step_fns,
        )

        build_core_pipelines()
        fns = core_step_fns()

        wired = {
            "wake_up_preparation",
            "continuity_check",
            "proactive_pattern_scan",
            "anticipatory_prep",
            "proactive_research",
            "article_digest",
            "follow_through_draft",
            "contextual_engagement",
            "commitment_tracking",
            "stuck_detection",
            "weekly_triage",
            "draft_on_observation",
            "connection_making",
        }
        for name in wired:
            assert name in fns
            assert fns[name] is not _stub_step, (
                f"Pipeline {name} still uses _stub_step"
            )
