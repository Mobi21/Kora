"""Phase 8d acceptance tests -- ContextEngine insight generation.

Acceptance items:
- 58: ContextEngine produces cross-domain insights consumed by proactive pattern scan
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime, time, timedelta

# pysqlite3 monkey-patch
try:
    import pysqlite3 as _pysqlite3  # type: ignore[import-untyped]

    sys.modules["sqlite3"] = _pysqlite3
except ImportError:
    pass

import aiosqlite

from kora_v2.adhd import ADHDModule, ADHDProfile
from kora_v2.context.engine import ContextEngine
from kora_v2.core.db import init_operational_db
from kora_v2.core.events import EventEmitter, EventType
from kora_v2.core.models import Insight

# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════


async def _make_engine(
    tmp_path,
    crash_periods=None,
    peak_windows=None,
):
    """Create a ContextEngine with a freshly initialized DB."""
    db_path = tmp_path / "acceptance_8d.db"
    await init_operational_db(db_path)
    profile = ADHDProfile(
        crash_periods=crash_periods or [(14, 16)],
        peak_windows=peak_windows or [(9, 12)],
    )
    engine = ContextEngine(db_path, ADHDModule(profile), "UTC")
    return engine, db_path


async def _populate_cross_domain_data(db_path, *, days_back: int = 7):
    """Populate the operational DB with data that triggers cross-domain insights.

    Creates a scenario where:
    - Meetings are scheduled during crash periods (energy-calendar mismatch)
    - Focus blocks are longer on medicated days (medication-focus correlation)
    - Routine completion is declining over the window
    - Repeated low energy in afternoons (emotional pattern)
    """
    now = datetime.now(UTC)
    today = now.date()

    async with aiosqlite.connect(str(db_path)) as db:
        # --- Calendar entries during crash period (14:00-16:00) ---
        for i in range(4):
            day = today - timedelta(days=i)
            starts = datetime.combine(day, time(14, 30), tzinfo=UTC)
            ends = starts + timedelta(hours=1)
            await db.execute(
                "INSERT INTO calendar_entries "
                "(id, kind, title, starts_at, ends_at, source) "
                "VALUES (?, 'event', ?, ?, ?, 'kora')",
                (
                    uuid.uuid4().hex[:8],
                    f"Important meeting {i}",
                    starts.isoformat(),
                    ends.isoformat(),
                ),
            )

        # --- Medication + focus blocks (correlation data) ---
        for i in range(3):
            day = today - timedelta(days=i + 1)
            taken_at = datetime.combine(day, time(8, 0), tzinfo=UTC)
            await db.execute(
                "INSERT INTO medication_log "
                "(id, medication_name, taken_at, created_at) "
                "VALUES (?, 'Adderall', ?, ?)",
                (uuid.uuid4().hex[:8], taken_at.isoformat(), taken_at.isoformat()),
            )
            fb_start = datetime.combine(day, time(9, 0), tzinfo=UTC)
            fb_end = fb_start + timedelta(hours=2)
            await db.execute(
                "INSERT INTO focus_blocks "
                "(id, label, started_at, ended_at, created_at) "
                "VALUES (?, 'Deep work', ?, ?, datetime('now'))",
                (uuid.uuid4().hex[:8], fb_start.isoformat(), fb_end.isoformat()),
            )

        # Unmedicated days with short focus blocks
        for i in range(3):
            day = today - timedelta(days=i + 4)
            fb_start = datetime.combine(day, time(9, 0), tzinfo=UTC)
            fb_end = fb_start + timedelta(minutes=25)
            await db.execute(
                "INSERT INTO focus_blocks "
                "(id, label, started_at, ended_at, created_at) "
                "VALUES (?, 'Attempt', ?, ?, datetime('now'))",
                (uuid.uuid4().hex[:8], fb_start.isoformat(), fb_end.isoformat()),
            )

        # --- Routine sessions: declining completion ---
        routine_id = uuid.uuid4().hex[:8]
        now_iso = now.isoformat()
        await db.execute(
            "INSERT INTO routines "
            "(id, name, steps_json, created_at, updated_at) "
            "VALUES (?, 'Morning routine', '[]', ?, ?)",
            (routine_id, now_iso, now_iso),
        )
        # First half: completed
        for i in range(days_back, days_back // 2, -1):
            day = today - timedelta(days=i)
            started = datetime.combine(day, time(7, 0), tzinfo=UTC)
            await db.execute(
                "INSERT INTO routine_sessions "
                "(id, routine_id, status, started_at) "
                "VALUES (?, ?, 'completed', ?)",
                (uuid.uuid4().hex[:8], routine_id, started.isoformat()),
            )
        # Second half: abandoned
        for i in range(days_back // 2, 0, -1):
            day = today - timedelta(days=i)
            started = datetime.combine(day, time(7, 0), tzinfo=UTC)
            await db.execute(
                "INSERT INTO routine_sessions "
                "(id, routine_id, status, started_at) "
                "VALUES (?, ?, 'abandoned', ?)",
                (uuid.uuid4().hex[:8], routine_id, started.isoformat()),
            )

        # --- Energy self-reports: low afternoons ---
        for i in range(5):
            day = today - timedelta(days=i)
            logged = datetime.combine(day, time(15, 0), tzinfo=UTC)
            await db.execute(
                "INSERT INTO energy_log "
                "(id, level, focus, source, logged_at) "
                "VALUES (?, 'low', 'scattered', 'self_report', ?)",
                (uuid.uuid4().hex[:8], logged.isoformat()),
            )

        await db.commit()


# ══════════════════════════════════════════════════════════════════════════
# Acceptance test 58: ContextEngine produces cross-domain insights
#                     consumed by proactive pattern scan
# ══════════════════════════════════════════════════════════════════════════


class TestAcceptance58_CrossDomainInsights:
    """Item 58: ContextEngine produces cross-domain insights that the
    ProactiveAgent proactive_pattern_scan pipeline can subscribe to
    via INSIGHT_AVAILABLE events.
    """

    async def test_cross_domain_insights_produced(self, tmp_path):
        """ContextEngine generates structured Insight objects from
        cross-domain patterns in the operational data."""
        engine, db_path = await _make_engine(
            tmp_path, crash_periods=[(14, 16)]
        )
        await _populate_cross_domain_data(db_path, days_back=7)

        insights = await engine.get_insights(
            window_days=7, min_confidence=0.3
        )

        # Must produce at least 2 insights from different domains
        assert len(insights) >= 2, (
            f"Expected at least 2 cross-domain insights, got {len(insights)}: "
            f"{[i.type for i in insights]}"
        )

        # All insights are proper Insight models
        for insight in insights:
            assert isinstance(insight, Insight)
            assert insight.confidence >= 0.3
            assert insight.domain in (
                "adhd", "health", "productivity", "emotional"
            )
            assert len(insight.evidence) > 0
            assert insight.generated_at is not None

        # Should span multiple domains
        domains = {i.domain for i in insights}
        assert len(domains) >= 2, (
            f"Expected insights from 2+ domains, got {domains}"
        )

    async def test_insight_available_events_emitted(self, tmp_path):
        """INSIGHT_AVAILABLE events are emitted for each insight,
        making them consumable by proactive_pattern_scan pipeline."""
        engine, db_path = await _make_engine(
            tmp_path, crash_periods=[(14, 16)]
        )
        await _populate_cross_domain_data(db_path, days_back=7)

        emitter = EventEmitter()
        engine.subscribe_events(emitter)

        received_events: list[dict] = []

        async def capture_insight(payload: dict) -> None:
            received_events.append(payload)

        emitter.on(EventType.INSIGHT_AVAILABLE, capture_insight)

        insights = await engine.get_insights(
            window_days=7, min_confidence=0.3
        )

        # One event per insight
        assert len(received_events) == len(insights)
        assert len(received_events) >= 2

        # Events carry insight metadata
        for event in received_events:
            assert event["event_type"] == EventType.INSIGHT_AVAILABLE
            assert "insight_type" in event
            assert "confidence" in event
            assert "domain" in event

    async def test_day_context_updated_on_rebuild(self, tmp_path):
        """DAY_CONTEXT_UPDATED is emitted when DayContext is built,
        providing the trigger for downstream consumers."""
        engine, db_path = await _make_engine(tmp_path)

        emitter = EventEmitter()
        engine.subscribe_events(emitter)

        day_events: list[dict] = []

        async def capture_day(payload: dict) -> None:
            day_events.append(payload)

        emitter.on(EventType.DAY_CONTEXT_UPDATED, capture_day)

        await engine.build_day_context()
        assert len(day_events) == 1

        # Mark stale and rebuild -- should emit again
        engine.mark_stale()
        await engine.build_day_context()
        assert len(day_events) == 2

    async def test_staleness_driven_by_memory_stored(self, tmp_path):
        """MEMORY_STORED events from Memory Steward mark context stale,
        ensuring insights reflect new data."""
        engine, db_path = await _make_engine(tmp_path)

        emitter = EventEmitter()
        engine.subscribe_events(emitter)

        # Build initial context
        dc1 = await engine.build_day_context()
        assert not engine._stale

        # Simulate Memory Steward completing work
        await emitter.emit(EventType.MEMORY_STORED, memory_id="new-memory")
        assert engine._stale

        # Next build is fresh
        dc2 = await engine.build_day_context()
        assert dc1 is not dc2  # rebuilt, not cached

    async def test_end_to_end_insight_pipeline_flow(self, tmp_path):
        """Full flow: data -> ContextEngine -> INSIGHT_AVAILABLE -> consumer.

        This simulates what the proactive_pattern_scan pipeline does:
        subscribing to INSIGHT_AVAILABLE and receiving structured insights.
        """
        engine, db_path = await _make_engine(
            tmp_path, crash_periods=[(14, 16)]
        )
        await _populate_cross_domain_data(db_path, days_back=7)

        emitter = EventEmitter()
        engine.subscribe_events(emitter)

        # Simulate what proactive_pattern_scan does: subscribe and collect
        pattern_scan_inbox: list[dict] = []

        async def proactive_handler(payload: dict) -> None:
            # ProactiveAgent would process each insight here
            pattern_scan_inbox.append({
                "type": payload["insight_type"],
                "confidence": payload["confidence"],
                "domain": payload["domain"],
            })

        emitter.on(EventType.INSIGHT_AVAILABLE, proactive_handler)

        # Generate insights
        insights = await engine.get_insights(
            window_days=7, min_confidence=0.3
        )

        # The proactive handler received every insight
        assert len(pattern_scan_inbox) == len(insights)
        assert len(pattern_scan_inbox) >= 2

        # Verify the data is actionable
        for item in pattern_scan_inbox:
            assert isinstance(item["type"], str)
            assert isinstance(item["confidence"], float)
            assert item["confidence"] >= 0.3

    async def test_get_insights_api_contract(self, tmp_path):
        """get_insights(window_days, min_confidence) honors its API contract."""
        engine, db_path = await _make_engine(tmp_path)
        await _populate_cross_domain_data(db_path, days_back=14)

        # Narrow window should find fewer patterns
        narrow = await engine.get_insights(window_days=1, min_confidence=0.3)
        wide = await engine.get_insights(window_days=14, min_confidence=0.3)

        # Wide window should generally find at least as many insights
        # (or more) than a narrow window
        assert len(wide) >= len(narrow)

        # High confidence threshold filters aggressively
        loose = await engine.get_insights(window_days=14, min_confidence=0.1)
        strict = await engine.get_insights(window_days=14, min_confidence=0.95)
        assert len(loose) >= len(strict)
