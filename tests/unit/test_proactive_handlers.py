"""Unit tests for Proactive Agent handlers -- Phase 8e.

Covers each of the 13 stage handlers registered by
:mod:`kora_v2.agents.background.proactive_handlers`. The handlers use
the same service-resolution pattern as Memory Steward / Vault
Organizer, so we mock the container and patch
``get_autonomous_context``.
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
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════


def _make_task(
    stage_name: str = "run",
    goal: str = "test goal",
    pipeline_instance_id: str | None = "test-pipeline-001",
) -> WorkerTask:
    return WorkerTask(
        id="task-test-001",
        pipeline_instance_id=pipeline_instance_id,
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


def _make_step_context(task: WorkerTask) -> StepContext:
    limiter = MagicMock()
    return StepContext(
        task=task,
        limiter=limiter,
        cancellation_flag=lambda: False,
        now=lambda: datetime.now(UTC),
    )


def _delivery_result(delivered: bool = True) -> MagicMock:
    result = MagicMock()
    result.delivered = delivered
    return result


def _make_container(
    tmp_path: Path,
    *,
    llm_response: str = "",
    engine_insights: list[Insight] | None = None,
    day_context: DayContext | None = None,
    search_results: list[object] | None = None,
) -> MagicMock:
    """Build a mock container shared by the proactive handler tests."""
    container = MagicMock()

    # LLM provider
    container.llm = AsyncMock()
    container.llm.chat = AsyncMock(return_value=llm_response)

    # Filesystem memory store
    from kora_v2.memory.store import FilesystemMemoryStore

    mem_path = tmp_path / "_KoraMemory"
    mem_path.mkdir(parents=True, exist_ok=True)
    store = FilesystemMemoryStore(mem_path)
    container.memory_store = store

    # NotificationGate mock
    gate = MagicMock()
    gate.send_templated = AsyncMock(return_value=_delivery_result(True))
    container.notification_gate = gate

    # ContextEngine mock
    engine = MagicMock()
    engine.get_insights = AsyncMock(return_value=engine_insights or [])
    engine.build_day_context = AsyncMock(
        return_value=day_context or _default_day_context()
    )
    container.context_engine = engine

    # ReminderStore mock (overridden per-test where needed)
    rs = MagicMock()
    rs.get_due_reminders = AsyncMock(return_value=[])
    rs.get_pending = AsyncMock(return_value=[])
    rs.mark_delivered = AsyncMock()
    rs.reschedule_recurring = AsyncMock(return_value=None)
    rs.deliver_and_reschedule = AsyncMock(return_value=None)
    container.reminder_store = rs

    # ProjectionDB mock
    pdb = MagicMock()
    pdb.search = AsyncMock(return_value=search_results or [])
    container.projection_db = pdb

    # Event emitter
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    container.event_emitter = emitter

    return container


def _default_day_context() -> DayContext:
    today = datetime.now(UTC).date()
    return DayContext(
        date=today,
        day_of_week=today.strftime("%A"),
        schedule=[],
        medication_status=MedicationStatus(),
        routine_status=RoutineStatus(),
    )


def _make_calendar_entry(
    title: str,
    starts_at: datetime,
    *,
    kind: str = "event",
    ends_at: datetime | None = None,
) -> CalendarEntry:
    now = datetime.now(UTC)
    return CalendarEntry(
        id=uuid.uuid4().hex[:8],
        kind=kind,  # type: ignore[arg-type]
        title=title,
        starts_at=starts_at,
        ends_at=ends_at or (starts_at + timedelta(hours=1)),
        created_at=now,
        updated_at=now,
    )


async def _setup_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "operational.db"
    await init_operational_db(db_path)
    return db_path


async def _run_handler(
    handler,
    task: WorkerTask,
    ctx: StepContext,
    container: MagicMock,
    db_path: Path,
):
    """Invoke *handler* with the runtime context patched, returning its
    awaited :class:`StepResult`."""
    with patch(_CTX_PATCH) as mock_ctx:
        mock_ctx.return_value = MagicMock(
            container=container, db_path=db_path
        )
        return await handler(task, ctx)


# ══════════════════════════════════════════════════════════════════════════
# Area A -- proactive_pattern_scan_step
# ══════════════════════════════════════════════════════════════════════════


class TestProactivePatternScan:
    async def test_calls_get_insights_and_sends_nudge(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        insights = [
            Insight(
                type="energy_calendar_mismatch",
                title="Afternoon crash",
                description="Meetings during low-energy window",
                confidence=0.8,
                domain="adhd",
                evidence=["evt-1", "evt-2"],
            )
        ]
        container = _make_container(tmp_path, engine_insights=insights)

        from kora_v2.agents.background.proactive_handlers import (
            proactive_pattern_scan_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            proactive_pattern_scan_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        container.context_engine.get_insights.assert_awaited_once()
        # One nudge per actionable insight
        assert container.notification_gate.send_templated.await_count == 1
        assert "1 nudges delivered" in (result.result_summary or "")

    async def test_no_insights_means_no_nudges(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path, engine_insights=[])

        from kora_v2.agents.background.proactive_handlers import (
            proactive_pattern_scan_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            proactive_pattern_scan_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        container.notification_gate.send_templated.assert_not_awaited()


# ══════════════════════════════════════════════════════════════════════════
# Area B -- anticipatory_prep_step
# ══════════════════════════════════════════════════════════════════════════


class TestAnticipatoryPrep:
    async def test_writes_briefing_for_upcoming_events(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        now = datetime.now(UTC)
        upcoming = [
            _make_calendar_entry("Team sync", now + timedelta(hours=2)),
            _make_calendar_entry("1:1 with Sarah", now + timedelta(hours=4)),
        ]
        day_ctx = _default_day_context().model_copy(
            update={"schedule": upcoming}
        )
        container = _make_container(tmp_path, day_context=day_ctx)

        from kora_v2.agents.background.proactive_handlers import (
            anticipatory_prep_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            anticipatory_prep_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        inbox = tmp_path / "_KoraMemory" / "Inbox"
        files = list(inbox.glob("prep-briefing-*.md"))
        assert len(files) == 1
        body = files[0].read_text(encoding="utf-8")
        assert "Team sync" in body
        assert "1:1 with Sarah" in body

    async def test_no_upcoming_events_short_circuits(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)  # empty schedule

        from kora_v2.agents.background.proactive_handlers import (
            anticipatory_prep_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            anticipatory_prep_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        assert "no upcoming events" in (result.result_summary or "")


# ══════════════════════════════════════════════════════════════════════════
# Area C -- proactive_research_step, article_digest_step,
#           follow_through_draft_step
# ══════════════════════════════════════════════════════════════════════════


class TestProactiveResearch:
    async def test_multi_step_produces_report(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)

        from kora_v2.memory.projection import MemoryRecord

        search_results = [
            MemoryRecord(id="m1", content="prior thoughts on the topic"),
            MemoryRecord(id="m2", content="another related insight"),
        ]
        container = _make_container(
            tmp_path,
            llm_response="# Research Report\n\nSynthesized findings.",
            search_results=search_results,
        )

        from kora_v2.agents.background.proactive_handlers import (
            proactive_research_step,
        )

        # Step 1: memory search -> should return "continue"
        task = _make_task(goal="Research ADHD medication interactions")
        ctx = _make_step_context(task)

        # Install an in-memory checkpoint callback so the step function
        # can persist progress between invocations.
        scratch: dict = {}

        async def checkpoint_cb(state: dict) -> None:
            scratch.update(state)

        ctx.checkpoint_callback = checkpoint_cb

        result_a = await _run_handler(
            proactive_research_step, task, ctx, container, db_path
        )
        assert result_a.outcome == "continue"

        # Simulate the checkpoint being rehydrated onto the task
        from kora_v2.runtime.orchestration.worker_task import Checkpoint

        task.checkpoint_blob = Checkpoint(
            task_id=task.id,
            created_at=datetime.now(UTC),
            state=task.state,
            current_step_index=1,
            scratch_state=scratch,
        )

        # Step 2: synthesizes report and writes it
        result_b = await _run_handler(
            proactive_research_step, task, ctx, container, db_path
        )
        assert result_b.outcome == "complete"

        inbox = tmp_path / "_KoraMemory" / "Inbox"
        files = list(inbox.glob("research-*.md"))
        assert len(files) == 1
        body = files[0].read_text(encoding="utf-8")
        assert "Research Report" in body

    async def test_missing_memory_still_completes(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path, search_results=[])

        from kora_v2.agents.background.proactive_handlers import (
            proactive_research_step,
        )

        # Force step_index = 1 immediately so the report is written
        from kora_v2.runtime.orchestration.worker_task import Checkpoint

        task = _make_task(goal="Novel topic with no memory")
        ctx = _make_step_context(task)
        task.checkpoint_blob = Checkpoint(
            task_id=task.id,
            created_at=datetime.now(UTC),
            state=task.state,
            current_step_index=1,
            scratch_state={"step_index": 1, "findings": []},
        )

        result = await _run_handler(
            proactive_research_step, task, ctx, container, db_path
        )
        assert result.outcome == "complete"
        inbox = tmp_path / "_KoraMemory" / "Inbox"
        assert list(inbox.glob("research-*.md"))


class TestArticleDigest:
    async def test_summarizes_articles_in_inbox(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(
            tmp_path, llm_response="- Point 1\n- Point 2\n- Point 3"
        )

        # Seed an article in the Inbox
        inbox = tmp_path / "_KoraMemory" / "Inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        article = inbox / "saved-article.md"
        article.write_text(
            "---\ntype: article\n---\n\nLong article body here.",
            encoding="utf-8",
        )

        from kora_v2.agents.background.proactive_handlers import (
            article_digest_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            article_digest_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        assert "1 articles summarized" in (result.result_summary or "")
        body = article.read_text(encoding="utf-8")
        assert "## Digest" in body
        assert "Point 1" in body
        assert "digested: true" in body

    async def test_empty_inbox_is_noop(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        from kora_v2.agents.background.proactive_handlers import (
            article_digest_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            article_digest_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        # Either "empty inbox" (no Inbox dir) or "0 articles summarized"
        summary = result.result_summary or ""
        assert "empty inbox" in summary or "0 articles" in summary


class TestFollowThroughDraft:
    async def test_creates_draft_on_user_intent(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(
            tmp_path,
            llm_response=(
                "1. First step\n2. Second step\n3. Third step"
            ),
        )

        from kora_v2.agents.background.proactive_handlers import (
            follow_through_draft_step,
        )

        task = _make_task(goal="Ship the Phase 8 release notes")
        ctx = _make_step_context(task)

        result = await _run_handler(
            follow_through_draft_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        inbox = tmp_path / "_KoraMemory" / "Inbox"
        files = list(inbox.glob("follow-through-*.md"))
        assert len(files) == 1
        body = files[0].read_text(encoding="utf-8")
        assert "First step" in body
        assert "Ship the Phase 8 release notes" in body
        container.notification_gate.send_templated.assert_awaited()


# ══════════════════════════════════════════════════════════════════════════
# Area D -- contextual_engagement_step
# ══════════════════════════════════════════════════════════════════════════


class TestContextualEngagement:
    async def _run(
        self, tmp_path: Path, goal: str
    ) -> tuple[MagicMock, object]:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        from kora_v2.agents.background.proactive_handlers import (
            contextual_engagement_step,
        )

        task = _make_task(goal=goal)
        ctx = _make_step_context(task)
        result = await _run_handler(
            contextual_engagement_step, task, ctx, container, db_path
        )
        return container, result

    async def test_emotion_shift(self, tmp_path: Path) -> None:
        container, result = await self._run(tmp_path, "emotion shift detected")
        assert result.outcome == "complete"
        assert container.notification_gate.send_templated.await_count == 1

    async def test_task_lingering(self, tmp_path: Path) -> None:
        container, result = await self._run(tmp_path, "task lingering > 3 days")
        assert result.outcome == "complete"
        assert container.notification_gate.send_templated.await_count == 1

    async def test_open_decision(self, tmp_path: Path) -> None:
        container, result = await self._run(
            tmp_path, "open decision needs revisiting"
        )
        assert result.outcome == "complete"
        assert container.notification_gate.send_templated.await_count == 1

    async def test_focus_block_ended(self, tmp_path: Path) -> None:
        container, result = await self._run(
            tmp_path, "long focus block just ended"
        )
        assert result.outcome == "complete"
        assert container.notification_gate.send_templated.await_count == 1


# ══════════════════════════════════════════════════════════════════════════
# Area E -- commitment_tracking, stuck_detection, weekly_triage,
#           draft_on_observation, connection_making
# ══════════════════════════════════════════════════════════════════════════


class TestCommitmentTracking:
    async def test_scans_recent_transcripts(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)

        # Insert a recent transcript with a commitment-ish message
        messages = [
            {"role": "user", "content": "I'll follow up on that PR tomorrow"},
        ]
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO session_transcripts "
                "(session_id, created_at, ended_at, message_count, messages) "
                "VALUES (?, ?, ?, ?, ?)",
                ("sess-1", now, now, 1, json.dumps(messages)),
            )
            await db.commit()

        llm_response = json.dumps(
            [{"commitment": "Follow up on PR", "urgency": "medium"}]
        )
        container = _make_container(tmp_path, llm_response=llm_response)

        from kora_v2.agents.background.proactive_handlers import (
            commitment_tracking_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            commitment_tracking_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        assert "1 commitments" in (result.result_summary or "")
        inbox = tmp_path / "_KoraMemory" / "Inbox"
        files = list(inbox.glob("commitments-*.md"))
        assert len(files) == 1
        body = files[0].read_text(encoding="utf-8")
        assert "Follow up on PR" in body

    async def test_no_transcripts_returns_early(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        from kora_v2.agents.background.proactive_handlers import (
            commitment_tracking_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            commitment_tracking_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        assert "no recent transcripts" in (result.result_summary or "")


class TestStuckDetection:
    async def test_finds_stale_items(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)

        # Insert a stale item (3 days old, in_progress)
        old_ts = (datetime.now(UTC) - timedelta(days=3)).isoformat()
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO items "
                "(id, type, owner, title, status, created_at, updated_at) "
                "VALUES (?, 'task', 'kora', ?, 'in_progress', ?, ?)",
                ("item-stuck", "Write Phase 9 spec", old_ts, old_ts),
            )
            await db.commit()

        container = _make_container(tmp_path)

        from kora_v2.agents.background.proactive_handlers import (
            stuck_detection_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            stuck_detection_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        assert "1 stuck" in (result.result_summary or "")
        container.notification_gate.send_templated.assert_awaited()

    async def test_no_stale_items(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        from kora_v2.agents.background.proactive_handlers import (
            stuck_detection_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            stuck_detection_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        assert "0 stuck" in (result.result_summary or "")


class TestWeeklyTriage:
    async def test_produces_summary(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)

        # Seed a session and an open item
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                ("sess-100", now),
            )
            await db.execute(
                "INSERT INTO items "
                "(id, type, owner, title, status, created_at, updated_at) "
                "VALUES (?, 'task', 'kora', ?, 'planned', ?, ?)",
                ("item-open", "Review design doc", now, now),
            )
            await db.commit()

        container = _make_container(tmp_path)

        from kora_v2.agents.background.proactive_handlers import (
            weekly_triage_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            weekly_triage_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        inbox = tmp_path / "_KoraMemory" / "Inbox"
        files = list(inbox.glob("weekly-triage-*.md"))
        assert len(files) == 1
        body = files[0].read_text(encoding="utf-8")
        assert "Weekly Triage" in body
        assert "Review design doc" in body


class TestDraftOnObservation:
    async def test_creates_draft_on_user_need(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(
            tmp_path,
            llm_response="# Draft\n\nPractical content ready to use.",
        )

        from kora_v2.agents.background.proactive_handlers import (
            draft_on_observation_step,
        )

        task = _make_task(goal="Need a standup update template")
        ctx = _make_step_context(task)

        result = await _run_handler(
            draft_on_observation_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        inbox = tmp_path / "_KoraMemory" / "Inbox"
        files = list(inbox.glob("draft-*.md"))
        assert len(files) == 1
        body = files[0].read_text(encoding="utf-8")
        assert "Practical content" in body
        assert "standup update" in body


class TestConnectionMaking:
    async def test_surfaces_old_vault_notes(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)

        # Seed signal_queue with a recent user topic
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO signal_queue "
                "(id, session_id, message_text, signal_types, priority, "
                " status, created_at) "
                "VALUES (?, 'sess-1', ?, ?, 1, 'pending', ?)",
                (
                    "sig-1",
                    "Working on sleep tracking",
                    json.dumps(["life_event"]),
                    now,
                ),
            )
            await db.commit()

        # Old vault note (created 10 days ago)
        from kora_v2.memory.projection import MemoryRecord

        old_created = (
            datetime.now(UTC) - timedelta(days=10)
        ).isoformat()
        old_record = MemoryRecord(
            id="old-1",
            content="Sleep tracking attempt from last month",
            created_at=old_created,
        )
        container = _make_container(
            tmp_path, search_results=[old_record]
        )

        from kora_v2.agents.background.proactive_handlers import (
            connection_making_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            connection_making_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        assert "1 connections" in (result.result_summary or "")
        container.notification_gate.send_templated.assert_awaited()

    async def test_no_recent_topics(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        from kora_v2.agents.background.proactive_handlers import (
            connection_making_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            connection_making_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        assert "0 connections" in (result.result_summary or "")


# ══════════════════════════════════════════════════════════════════════════
# Infrastructure -- continuity_check_step, wake_up_preparation_step
# ══════════════════════════════════════════════════════════════════════════


class TestContinuityCheck:
    async def test_delivers_due_reminders(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        # Override reminder_store with a real one populated with a due reminder
        store = ReminderStore(db_path)
        container.reminder_store = store
        now = datetime.now(UTC)
        rid = await store.create_reminder(
            title="Take meds",
            description="Morning",
            due_at=now - timedelta(minutes=1),
        )

        from kora_v2.agents.background.proactive_handlers import (
            continuity_check_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            continuity_check_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        # Reminder marked delivered
        pending = await store.get_pending()
        assert rid not in [r.id for r in pending]
        container.notification_gate.send_templated.assert_awaited()

    async def test_reschedules_recurring_after_delivery(
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
            repeat_rule="daily",
        )

        from kora_v2.agents.background.proactive_handlers import (
            continuity_check_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            continuity_check_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        # A new reminder should have been created for the next day
        pending = await store.get_pending()
        assert len(pending) == 1
        assert pending[0].title == "Daily vitamins"
        # Its due time should be ~1 day after the original due
        delta = pending[0].due_at - original_due
        assert abs(delta - timedelta(days=1)) < timedelta(seconds=5)

    async def test_no_due_reminders_no_delivery(
        self, tmp_path: Path
    ) -> None:
        db_path = await _setup_db(tmp_path)
        container = _make_container(tmp_path)

        # Empty ReminderStore mock (default); routine nudges none
        container.context_engine.build_day_context = AsyncMock(
            return_value=_default_day_context()
        )

        from kora_v2.agents.background.proactive_handlers import (
            continuity_check_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            continuity_check_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"


class TestWakeUpPreparation:
    async def test_assembles_morning_briefing(self, tmp_path: Path) -> None:
        db_path = await _setup_db(tmp_path)

        # DayContext with schedule + energy + items_due
        today = datetime.now(UTC).date()
        now = datetime.now(UTC)
        schedule = [
            _make_calendar_entry("Standup", now + timedelta(hours=1)),
            _make_calendar_entry("Lunch with Mark", now + timedelta(hours=5)),
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
                {"title": "Review PR", "status": "planned"},
            ],
        )

        container = _make_container(
            tmp_path,
            day_context=day_ctx,
            engine_insights=[
                Insight(
                    type="medication_focus",
                    title="Medication boosts focus",
                    description="When meds taken, focus blocks are longer",
                    confidence=0.9,
                    domain="adhd",
                    evidence=["med-1"],
                )
            ],
        )

        # Pending reminder for wake-up section
        reminder = Reminder(
            id="rem-x",
            title="Call dentist",
            due_at=now + timedelta(hours=3),
        )
        container.reminder_store.get_pending = AsyncMock(
            return_value=[reminder]
        )

        from kora_v2.agents.background.proactive_handlers import (
            wake_up_preparation_step,
        )

        task = _make_task()
        ctx = _make_step_context(task)

        result = await _run_handler(
            wake_up_preparation_step, task, ctx, container, db_path
        )

        assert result.outcome == "complete"
        inbox = tmp_path / "_KoraMemory" / "Inbox"
        files = list(inbox.glob("morning-briefing-*.md"))
        assert len(files) == 1
        body = files[0].read_text(encoding="utf-8")
        assert "Good Morning" in body
        assert "Standup" in body
        assert "Lunch with Mark" in body
        assert "Call dentist" in body
        assert "Medication boosts focus" in body
        # Summary notification sent
        container.notification_gate.send_templated.assert_awaited()
