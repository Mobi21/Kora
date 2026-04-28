"""AT2 test-data cleanup on the acceptance harness.

Seeds every table that ``_clean_stale_test_data`` is supposed to clear
(legacy autonomous, Phase 7.5 orchestration, Phase 8 proactive +
lifecycle) and verifies the cleanup wipes them all without throwing,
then verifies a second run on the now-empty DB is a no-op.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from tests.acceptance._harness_server import (
    _LEGACY_AUTONOMOUS_TABLES,
    _LIFE_MANAGEMENT_TABLES,
    _LIFECYCLE_TABLES,
    _ORCHESTRATION_TABLES,
    _PROACTIVE_TABLES,
    _RUNTIME_STATE_TABLES,
    _clean_stale_autonomous_data,
    _clean_stale_projection_data,
    _clean_stale_test_data,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ORCH_MIGRATION = _PROJECT_ROOT / "kora_v2" / "runtime" / "orchestration" / "migrations" / "001_orchestration.sql"
_NOTIF_MIGRATION = _PROJECT_ROOT / "kora_v2" / "runtime" / "orchestration" / "migrations" / "002_notifications_templates.sql"


async def _init_full_schema(db_path: Path) -> None:
    """Create all tables that ``_clean_stale_test_data`` is supposed to wipe.

    Minimally schema-compatible — we only need the tables to exist with
    the right ``name`` so ``DELETE FROM <t>`` works. Columns are
    permissive to make seeding trivial.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as db:
        # Legacy autonomous tables
        await db.executescript(
            """
            CREATE TABLE autonomous_plans (
                id TEXT PRIMARY KEY,
                goal TEXT,
                status TEXT,
                created_at TEXT
            );
            CREATE TABLE autonomous_checkpoints (
                id TEXT PRIMARY KEY,
                plan_id TEXT,
                created_at TEXT
            );
            CREATE TABLE items (
                id TEXT PRIMARY KEY,
                title TEXT,
                status TEXT,
                owner TEXT,
                created_at TEXT
            );
            CREATE TABLE autonomous_updates (
                id TEXT PRIMARY KEY,
                plan_id TEXT,
                created_at TEXT
            );
            CREATE TABLE item_state_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT,
                created_at TEXT
            );
            CREATE TABLE item_artifact_links (
                item_id TEXT,
                artifact_path TEXT
            );
            CREATE TABLE item_deps (
                item_id TEXT,
                depends_on TEXT
            );
            """
        )
        # Phase 7.5 orchestration + proactive
        await db.executescript(_ORCH_MIGRATION.read_text())

        # Notifications (base shape) + Phase 7.5 extensions
        await db.executescript(
            """
            CREATE TABLE notifications (
                id               TEXT PRIMARY KEY,
                priority         TEXT NOT NULL,
                content          TEXT NOT NULL,
                category         TEXT,
                delivered_at     TEXT NOT NULL,
                acknowledged_at  TEXT,
                delivery_channel TEXT
            );
            """
        )
        await db.executescript(_NOTIF_MIGRATION.read_text())

        # Phase 8 adjuncts
        await db.executescript(
            """
            CREATE TABLE reminders (
                id TEXT PRIMARY KEY,
                title TEXT,
                status TEXT,
                created_at TEXT
            );
            CREATE TABLE signal_queue (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                message_text TEXT,
                signal_types TEXT,
                priority INTEGER,
                status TEXT,
                created_at TEXT
            );
            CREATE TABLE session_transcripts (
                session_id TEXT PRIMARY KEY,
                created_at TEXT,
                ended_at TEXT,
                message_count INTEGER,
                messages TEXT
            );
            CREATE TABLE dedup_rejected_pairs (
                id_a TEXT NOT NULL,
                id_b TEXT NOT NULL,
                rejected_at TEXT NOT NULL,
                PRIMARY KEY (id_a, id_b)
            );
            CREATE TABLE permission_grants (
                id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                scope TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                provenance TEXT,
                recorded_by TEXT,
                granted_at TEXT NOT NULL,
                expires_at TEXT,
                session_id TEXT
            );
            CREATE TABLE checkpoints (
                thread_id TEXT,
                checkpoint_ns TEXT,
                checkpoint_id TEXT PRIMARY KEY,
                parent_checkpoint_id TEXT,
                type TEXT,
                checkpoint BLOB,
                metadata BLOB
            );
            CREATE TABLE turn_traces (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                created_at TEXT
            );
            CREATE TABLE turn_trace_events (
                id TEXT PRIMARY KEY,
                trace_id TEXT,
                created_at TEXT
            );
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                started_at TEXT,
                ended_at TEXT
            );
            CREATE TABLE medication_log (
                id TEXT PRIMARY KEY,
                medication_name TEXT,
                taken_at TEXT
            );
            CREATE TABLE meal_log (
                id TEXT PRIMARY KEY,
                meal_type TEXT,
                logged_at TEXT
            );
            CREATE TABLE focus_blocks (
                id TEXT PRIMARY KEY,
                started_at TEXT
            );
            CREATE TABLE quick_notes (
                id TEXT PRIMARY KEY,
                content TEXT,
                created_at TEXT
            );
            CREATE TABLE routines (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                steps_json TEXT NOT NULL,
                low_energy_variant_json TEXT,
                tags TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE routine_sessions (
                id TEXT PRIMARY KEY,
                routine_id TEXT NOT NULL,
                session_id TEXT,
                variant TEXT NOT NULL DEFAULT 'standard',
                current_step_index INTEGER DEFAULT 0,
                completed_steps TEXT NOT NULL DEFAULT '[]',
                skipped_steps TEXT NOT NULL DEFAULT '[]',
                checkpoint_state TEXT,
                last_nudge_at TEXT,
                completion_confidence REAL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'active',
                started_at TEXT NOT NULL,
                completed_at TEXT
            );
            """
        )
        await db.commit()


async def _seed_one_row_per_table(db_path: Path) -> None:
    """Insert a single sentinel row into every table cleanup should clear."""
    now = datetime.now(UTC).isoformat()
    async with aiosqlite.connect(str(db_path)) as db:
        # Legacy autonomous
        await db.execute(
            "INSERT INTO autonomous_plans (id, goal, status, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("p_1", "g", "active", now),
        )
        await db.execute(
            "INSERT INTO autonomous_checkpoints (id, plan_id, created_at) "
            "VALUES (?, ?, ?)",
            ("c_1", "p_1", now),
        )
        await db.execute(
            "INSERT INTO items (id, title, status, owner, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("i_1", "t", "pending", "planner", now),
        )
        await db.execute(
            "INSERT INTO autonomous_updates (id, plan_id, created_at) "
            "VALUES (?, ?, ?)",
            ("u_1", "p_1", now),
        )
        await db.execute(
            "INSERT INTO item_state_history (item_id, created_at) VALUES (?, ?)",
            ("i_1", now),
        )
        await db.execute(
            "INSERT INTO item_artifact_links (item_id, artifact_path) VALUES (?, ?)",
            ("i_1", "/a"),
        )
        await db.execute(
            "INSERT INTO item_deps (item_id, depends_on) VALUES (?, ?)",
            ("i_1", "i_2"),
        )
        # Orchestration
        await db.execute(
            "INSERT INTO pipeline_instances "
            "(id, pipeline_name, working_doc_path, goal, state, started_at, "
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("pi_1", "post_session_memory", "/x", "g", "running", now, now),
        )
        await db.execute(
            "INSERT INTO worker_tasks "
            "(id, pipeline_instance_id, stage_name, task_preset, state, "
            " tool_scope, system_prompt, created_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            ("wt_1", "pi_1", "extract", "memory_steward", "running",
             "memory", "sp", now),
        )
        await db.execute(
            "INSERT INTO work_ledger (timestamp, event_type) VALUES (?, ?)",
            (now, "pipeline_started"),
        )
        await db.execute(
            "INSERT INTO trigger_state "
            "(trigger_id, pipeline_name, last_fired_at) VALUES (?, ?, ?)",
            ("t1", "post_session_memory", now),
        )
        await db.execute(
            "INSERT INTO system_state_log "
            "(transitioned_at, previous_phase, new_phase) VALUES (?, ?, ?)",
            (now, "conversation", "active_idle"),
        )
        await db.execute(
            "INSERT INTO request_limiter_log (timestamp, class) VALUES (?, ?)",
            (now, "CONVERSATION"),
        )
        await db.execute(
            "INSERT INTO open_decisions (id, topic, posed_at, status) "
            "VALUES (?, ?, ?, ?)",
            ("d_1", "x?", now, "pending"),
        )
        await db.execute(
            "INSERT INTO permission_grants "
            "(id, tool_name, scope, risk_level, decision, granted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("pg_1", "write_file", "global", "high", "approved", now),
        )
        await db.execute(
            "INSERT INTO runtime_pipelines "
            "(name, declaration_json, created_at, enabled) VALUES (?, ?, ?, ?)",
            ("p", "{}", now, 1),
        )
        # Runtime state
        await db.execute(
            "INSERT INTO checkpoints "
            "(thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("thread", "", "cp_1", "json", b"{}", b"{}"),
        )
        await db.execute(
            "INSERT INTO turn_traces (id, session_id, created_at) "
            "VALUES (?, ?, ?)",
            ("trace_1", "s_1", now),
        )
        await db.execute(
            "INSERT INTO turn_trace_events (id, trace_id, created_at) "
            "VALUES (?, ?, ?)",
            ("event_1", "trace_1", now),
        )
        await db.execute(
            "INSERT INTO sessions (id, started_at, ended_at) "
            "VALUES (?, ?, ?)",
            ("sess_1", now, now),
        )
        # Life management
        await db.execute(
            "INSERT INTO medication_log (id, medication_name, taken_at) "
            "VALUES (?, ?, ?)",
            ("med_1", "Adderall", now),
        )
        await db.execute(
            "INSERT INTO meal_log (id, meal_type, logged_at) VALUES (?, ?, ?)",
            ("meal_1", "breakfast", now),
        )
        await db.execute(
            "INSERT INTO focus_blocks (id, started_at) VALUES (?, ?)",
            ("focus_1", now),
        )
        await db.execute(
            "INSERT INTO quick_notes (id, content, created_at) VALUES (?, ?, ?)",
            ("note_1", "x", now),
        )
        await db.execute(
            "INSERT INTO routines "
            "(id, name, steps_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("routine_1", "morning reset", "[]", now, now),
        )
        await db.execute(
            "INSERT INTO routine_sessions "
            "(id, routine_id, session_id, started_at) VALUES (?, ?, ?, ?)",
            ("routine_session_1", "routine_1", "sess_1", now),
        )
        # Proactive / lifecycle adjuncts
        await db.execute(
            "INSERT INTO notifications "
            "(id, priority, content, category, delivered_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("n_1", "low", "hi", "general", now),
        )
        await db.execute(
            "INSERT INTO reminders (id, title, status, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("r_1", "t", "pending", now),
        )
        await db.execute(
            "INSERT INTO session_transcripts "
            "(session_id, created_at, ended_at, message_count, messages) "
            "VALUES (?, ?, ?, ?, ?)",
            ("s_1", now, now, 0, "[]"),
        )
        await db.execute(
            "INSERT INTO signal_queue "
            "(id, session_id, message_text, signal_types, priority, status, "
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("sq_1", "s_1", "t", "[]", 1, "pending", now),
        )
        await db.execute(
            "INSERT INTO dedup_rejected_pairs (id_a, id_b, rejected_at) "
            "VALUES (?, ?, ?)",
            ("a", "b", now),
        )
        await db.commit()


async def _count_row(db_path: Path, table: str) -> int:
    async with aiosqlite.connect(str(db_path)) as db:
        try:
            cur = await db.execute(f"SELECT COUNT(*) FROM {table}")
            row = await cur.fetchone()
            return row[0] if row else 0
        except Exception:
            return -1


def test_clean_stale_test_data_clears_all_tables(tmp_path: Path) -> None:
    """Every table in the cleanup set is empty after cleanup runs."""
    db_path = tmp_path / "operational.db"

    async def _run() -> None:
        await _init_full_schema(db_path)
        await _seed_one_row_per_table(db_path)

        # Sanity: every table has a sentinel row.
        for t in (
            *_LEGACY_AUTONOMOUS_TABLES,
            *_ORCHESTRATION_TABLES,
            *_RUNTIME_STATE_TABLES,
            *_LIFE_MANAGEMENT_TABLES,
            *_LIFECYCLE_TABLES,
            *_PROACTIVE_TABLES,
        ):
            assert await _count_row(db_path, t) == 1, f"{t} not seeded"

        await _clean_stale_test_data(db_path)

        for t in (
            *_LEGACY_AUTONOMOUS_TABLES,
            *_ORCHESTRATION_TABLES,
            *_RUNTIME_STATE_TABLES,
            *_LIFE_MANAGEMENT_TABLES,
            *_LIFECYCLE_TABLES,
            *_PROACTIVE_TABLES,
        ):
            assert await _count_row(db_path, t) == 0, f"{t} still has rows"

        # Backward-compat alias should point to the same function.
        assert _clean_stale_autonomous_data is _clean_stale_test_data

    asyncio.run(_run())


def test_clean_stale_test_data_is_idempotent(tmp_path: Path) -> None:
    """Running cleanup twice in a row stays a no-op on the second pass.

    Also verifies that cleanup tolerates a DB that is missing half the
    tables without raising — the acceptance harness boots against
    older DBs during upgrade windows.
    """
    db_path = tmp_path / "operational.db"

    async def _run() -> None:
        await _init_full_schema(db_path)
        await _seed_one_row_per_table(db_path)

        # First pass: wipes everything.
        await _clean_stale_test_data(db_path)
        # Second pass: still a no-op, no exception.
        await _clean_stale_test_data(db_path)

        for t in (
            *_LEGACY_AUTONOMOUS_TABLES,
            *_ORCHESTRATION_TABLES,
            *_PROACTIVE_TABLES,
        ):
            assert await _count_row(db_path, t) == 0

        # Drop half the tables and run again — should not raise.
        async with aiosqlite.connect(str(db_path)) as db:
            for t in _LEGACY_AUTONOMOUS_TABLES:
                await db.execute(f"DROP TABLE IF EXISTS {t}")
            await db.commit()

        # Expected to succeed even though several tables no longer exist.
        await _clean_stale_test_data(db_path)

    asyncio.run(_run())


def test_clean_stale_test_data_tolerates_missing_db(tmp_path: Path) -> None:
    """Missing DB file is a silent no-op."""
    missing = tmp_path / "does-not-exist.db"
    # Must not raise.
    asyncio.run(_clean_stale_test_data(missing))
    assert not missing.exists()


def test_clean_stale_projection_data_clears_acceptance_memory_rows(
    tmp_path: Path,
) -> None:
    """Fresh acceptance runs must not inherit projection rows from old vaults."""
    import sqlite3

    db_path = tmp_path / "projection.db"
    with sqlite3.connect(str(db_path)) as db:
        db.executescript(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                content TEXT,
                source_path TEXT,
                status TEXT DEFAULT 'active'
            );
            CREATE TABLE user_model_facts (
                id TEXT PRIMARY KEY,
                content TEXT,
                source_path TEXT,
                status TEXT DEFAULT 'active'
            );
            CREATE TABLE entities (
                id TEXT PRIMARY KEY,
                name TEXT,
                canonical_name TEXT,
                entity_type TEXT
            );
            CREATE TABLE entity_links (
                entity_id TEXT,
                memory_id TEXT,
                user_model_fact_id TEXT,
                relationship TEXT
            );
            CREATE TABLE memories_vec (embedding BLOB);
            CREATE TABLE user_model_vec (embedding BLOB);
            INSERT INTO memories (id, content, source_path) VALUES
                ('m1', 'old memory', '/Users/mobi/.kora/memory/old.md');
            INSERT INTO user_model_facts (id, content, source_path) VALUES
                ('f1', 'old fact', '/Users/mobi/.kora/memory/fact.md');
            INSERT INTO entities (id, name, canonical_name, entity_type) VALUES
                ('e1', 'Alex', 'alex', 'person');
            INSERT INTO entity_links (entity_id, memory_id, relationship) VALUES
                ('e1', 'm1', 'mentions');
            INSERT INTO memories_vec (embedding) VALUES (x'00');
            INSERT INTO user_model_vec (embedding) VALUES (x'00');
            """
        )

    _clean_stale_projection_data(db_path)

    with sqlite3.connect(str(db_path)) as db:
        for table in (
            "entity_links",
            "memories_vec",
            "user_model_vec",
            "memories",
            "user_model_facts",
            "entities",
        ):
            count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0, f"{table} still has rows"


# Pytest collection marker — ensures the module is always pickable up by
# the unit acceptance suite.
if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
