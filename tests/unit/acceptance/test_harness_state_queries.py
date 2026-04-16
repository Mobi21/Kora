"""AT2 state-query methods on ``HarnessServer``.

Seeds a temporary SQLite DB with the orchestration + memory migrations
and asserts that every new query method returns the documented shape,
handles missing tables gracefully, and never crashes the snapshot.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from tests.acceptance._harness_server import HarnessServer

# ── Migration helpers ────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ORCH_MIGRATION = _PROJECT_ROOT / "kora_v2" / "runtime" / "orchestration" / "migrations" / "001_orchestration.sql"
_NOTIF_MIGRATION = _PROJECT_ROOT / "kora_v2" / "runtime" / "orchestration" / "migrations" / "002_notifications_templates.sql"
_PROJECTION_MIGRATION = _PROJECT_ROOT / "kora_v2" / "memory" / "migrations" / "001_projection_schema.sql"
_SOFT_DELETE_MIGRATION = _PROJECT_ROOT / "kora_v2" / "memory" / "migrations" / "002_soft_delete.sql"


async def _init_operational(db_path: Path) -> None:
    """Create the minimum operational schema needed for AT2 queries.

    We avoid pulling the real ``init_operational_db`` because it would
    import too much of the runtime into these fast unit tests. The only
    thing the AT2 queries actually need from operational.db is:

      * notifications (with Phase 7.5 extra columns)
      * reminders (with Phase 8e extra columns)
      * signal_queue, session_transcripts, dedup_rejected_pairs
      * the 8 orchestration tables from 001_orchestration.sql
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as db:
        # notifications (base) + Phase 7.5 extension columns
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

        # reminders with Phase 8e columns
        await db.executescript(
            """
            CREATE TABLE reminders (
                id           TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                description  TEXT,
                remind_at    TEXT,
                recurring    TEXT,
                status       TEXT NOT NULL DEFAULT 'pending',
                session_id   TEXT,
                created_at   TEXT NOT NULL,
                due_at       TEXT,
                repeat_rule  TEXT,
                source       TEXT NOT NULL DEFAULT 'user',
                delivered_at TEXT,
                dismissed_at TEXT,
                metadata     TEXT
            );
            CREATE TABLE signal_queue (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                message_text TEXT NOT NULL,
                assistant_response TEXT,
                signal_types TEXT NOT NULL,
                priority INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                processed_at TEXT,
                error_message TEXT
            );
            CREATE TABLE session_transcripts (
                session_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                messages TEXT NOT NULL,
                tool_calls TEXT,
                emotional_trajectory TEXT,
                processed_at TEXT
            );
            CREATE TABLE dedup_rejected_pairs (
                id_a TEXT NOT NULL,
                id_b TEXT NOT NULL,
                rejected_at TEXT NOT NULL,
                PRIMARY KEY (id_a, id_b)
            );
            """
        )

        # 8 orchestration tables
        await db.executescript(_ORCH_MIGRATION.read_text())
        await db.commit()


def _strip_vec0_statements(sql: str) -> str:
    """Drop ``CREATE VIRTUAL TABLE ... USING vec0(...);`` blocks.

    The real projection schema depends on the ``sqlite-vec`` extension
    for vector search. The AT2 state-query methods do not touch any
    ``*_vec`` table, so the unit tests strip those statements to keep
    the test schema loadable on environments without ``vec0``.
    """
    import re

    # Remove any ``CREATE VIRTUAL TABLE ... USING vec0(...);`` block,
    # including the parenthesised column list and trailing semicolon.
    pattern = re.compile(
        r"CREATE\s+VIRTUAL\s+TABLE[^;]*?USING\s+vec0\s*\([^;]*?\)\s*;",
        re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub("", sql)


async def _init_projection(db_path: Path) -> None:
    """Create projection.db with the soft-delete extensions.

    Strips ``USING vec0`` virtual-table statements from the migration so
    the test suite can run without the ``sqlite-vec`` loadable extension
    — the AT2 state queries never touch the vector tables.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as db:
        projection_sql = _strip_vec0_statements(_PROJECTION_MIGRATION.read_text())
        await db.executescript(projection_sql)
        await db.executescript(_SOFT_DELETE_MIGRATION.read_text())
        await db.commit()


async def _seed_orchestration(db_path: Path) -> None:
    now = datetime.now(UTC)
    later = now + timedelta(seconds=5)
    async with aiosqlite.connect(str(db_path)) as db:
        # Two pipelines: one completed, one running
        await db.execute(
            "INSERT INTO pipeline_instances "
            "(id, pipeline_name, working_doc_path, goal, state, started_at, "
            " updated_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("pi_1", "post_session_memory", "/x/1.md", "Goal 1", "completed",
             now.isoformat(), later.isoformat(), later.isoformat()),
        )
        await db.execute(
            "INSERT INTO pipeline_instances "
            "(id, pipeline_name, working_doc_path, goal, state, started_at, "
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("pi_2", "post_memory_vault", "/x/2.md", "Goal 2", "running",
             now.isoformat(), now.isoformat()),
        )
        # Worker tasks
        await db.execute(
            "INSERT INTO worker_tasks "
            "(id, pipeline_instance_id, stage_name, task_preset, state, "
            " tool_scope, system_prompt, created_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            ("wt_1", "pi_1", "extract", "memory_steward", "completed",
             "memory", "sp", now.isoformat()),
        )
        await db.execute(
            "INSERT INTO worker_tasks "
            "(id, pipeline_instance_id, stage_name, task_preset, state, "
            " tool_scope, system_prompt, created_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            ("wt_2", "pi_2", "reindex", "vault_organizer", "running",
             "vault", "sp", now.isoformat()),
        )
        # Work ledger
        for et in ("pipeline_started", "task_started", "task_completed",
                   "pipeline_completed"):
            await db.execute(
                "INSERT INTO work_ledger "
                "(timestamp, event_type, pipeline_instance_id) VALUES (?, ?, ?)",
                (now.isoformat(), et, "pi_1"),
            )
        # Trigger state
        await db.execute(
            "INSERT INTO trigger_state "
            "(trigger_id, pipeline_name, last_fired_at) VALUES (?, ?, ?)",
            ("t_session_end", "post_session_memory", now.isoformat()),
        )
        # System state log
        await db.execute(
            "INSERT INTO system_state_log "
            "(transitioned_at, previous_phase, new_phase, reason) "
            "VALUES (?, ?, ?, ?)",
            (now.isoformat(), "conversation", "active_idle", "session_ended"),
        )
        await db.execute(
            "INSERT INTO system_state_log "
            "(transitioned_at, previous_phase, new_phase, reason) "
            "VALUES (?, ?, ?, ?)",
            (later.isoformat(), "active_idle", "light_idle", "elapsed"),
        )
        # Request limiter
        for cls in ("CONVERSATION", "NOTIFICATION", "BOUNDED_BACKGROUND",
                    "LONG_BACKGROUND"):
            await db.execute(
                "INSERT INTO request_limiter_log (timestamp, class) "
                "VALUES (?, ?)",
                (now.isoformat(), cls),
            )
        # Open decisions
        await db.execute(
            "INSERT INTO open_decisions (id, topic, posed_at, status) "
            "VALUES (?, ?, ?, ?)",
            ("d_1", "Should we nudge?", now.isoformat(), "pending"),
        )
        # Runtime pipelines
        await db.execute(
            "INSERT INTO runtime_pipelines "
            "(name, declaration_json, created_at, enabled) VALUES "
            "(?, ?, ?, ?)",
            ("custom_pipeline", json.dumps({"stages": []}), now.isoformat(), 1),
        )
        await db.commit()


async def _seed_proactive(db_path: Path) -> None:
    now = datetime.now(UTC)
    async with aiosqlite.connect(str(db_path)) as db:
        for i, tier in enumerate(("templated", "llm", "templated")):
            await db.execute(
                "INSERT INTO notifications "
                "(id, priority, content, category, delivered_at, "
                " delivery_tier, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"n_{i}", "medium", f"nudge {i}", "general",
                 now.isoformat(), tier, "delivered"),
            )
        # Reminders: one delivered with a 30s slip, one pending
        delivered_at = (now + timedelta(seconds=30)).isoformat()
        await db.execute(
            "INSERT INTO reminders "
            "(id, title, status, due_at, delivered_at, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("r_1", "take meds", "delivered", now.isoformat(),
             delivered_at, "medication", now.isoformat()),
        )
        await db.execute(
            "INSERT INTO reminders "
            "(id, title, status, due_at, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("r_2", "pay bill", "pending", now.isoformat(),
             "user", now.isoformat()),
        )
        # Signal queue
        await db.execute(
            "INSERT INTO signal_queue "
            "(id, session_id, message_text, signal_types, priority, "
            " status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("s_1", "sess_x", "text", '["info"]', 1, "pending",
             now.isoformat()),
        )
        # Session transcripts (one processed, one unprocessed)
        await db.execute(
            "INSERT INTO session_transcripts "
            "(session_id, created_at, ended_at, message_count, messages, "
            " processed_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("sess_proc", now.isoformat(), now.isoformat(), 2, "[]",
             now.isoformat()),
        )
        await db.execute(
            "INSERT INTO session_transcripts "
            "(session_id, created_at, ended_at, message_count, messages) "
            "VALUES (?, ?, ?, ?, ?)",
            ("sess_unp", now.isoformat(), now.isoformat(), 3, "[]"),
        )
        # Dedup
        await db.execute(
            "INSERT INTO dedup_rejected_pairs (id_a, id_b, rejected_at) "
            "VALUES (?, ?, ?)",
            ("m_1", "m_2", now.isoformat()),
        )
        await db.commit()


async def _seed_projection(db_path: Path) -> None:
    now = datetime.now(UTC).isoformat()
    async with aiosqlite.connect(str(db_path)) as db:
        # Memories: 3 active, 1 soft_deleted, 1 consolidated
        await db.execute(
            "INSERT INTO memories "
            "(id, content, summary, importance, memory_type, created_at, "
            " updated_at, source_path, status) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("m_1", "active memory", "s", 0.5, "episodic", now, now,
             "Long-Term/Episodic/m1.md", "active"),
        )
        await db.execute(
            "INSERT INTO memories "
            "(id, content, summary, importance, memory_type, created_at, "
            " updated_at, source_path, status, consolidated_into) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("m_2", "old", "s", 0.3, "episodic", now, now,
             "Long-Term/Episodic/m2.md", "consolidated", "m_3"),
        )
        await db.execute(
            "INSERT INTO memories "
            "(id, content, summary, importance, memory_type, created_at, "
            " updated_at, source_path, status, merged_from) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("m_3", "merged", "s", 0.6, "episodic", now, now,
             "Long-Term/Episodic/m3.md", "active", '["m_2"]'),
        )
        # User model facts
        await db.execute(
            "INSERT INTO user_model_facts "
            "(id, domain, content, confidence, created_at, updated_at, "
            " source_path, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("f_1", "health", "uses CPAP", 0.9, now, now,
             "User Model/Health/cpap.md", "active"),
        )
        # Entities
        await db.execute(
            "INSERT INTO entities "
            "(id, name, canonical_name, entity_type) VALUES "
            "(?, ?, ?, ?)",
            ("e_1", "Sarah", "sarah", "person"),
        )
        await db.execute(
            "INSERT INTO entities "
            "(id, name, canonical_name, entity_type) VALUES "
            "(?, ?, ?, ?)",
            ("e_2", "Portland", "portland", "place"),
        )
        await db.execute(
            "INSERT INTO entity_links (entity_id, memory_id, relationship) "
            "VALUES (?, ?, ?)",
            ("e_1", "m_1", "mentions"),
        )
        await db.commit()


# ── Pytest helpers ───────────────────────────────────────────────────────


@pytest.fixture
def harness() -> HarnessServer:
    """A minimally-constructed HarnessServer — we only call query methods."""
    return HarnessServer()


# ── test_query_orchestration_state ───────────────────────────────────────


def test_query_orchestration_state_handles_missing_tables(
    tmp_path: Path, harness: HarnessServer,
) -> None:
    """Missing tables return ``{"error": "table_missing"}`` per table."""
    db_path = tmp_path / "operational.db"
    # Create empty DB, no tables.
    asyncio.run(
        (lambda: aiosqlite.connect(str(db_path)).__aenter__())()
    ).close() if False else None
    # Simpler: just connect/disconnect to create the file.
    async def _touch() -> None:
        async with aiosqlite.connect(str(db_path)):
            pass
    asyncio.run(_touch())

    result = asyncio.run(harness._query_orchestration_state(db_path))
    assert result["available"] is True
    for key in ("pipeline_instances", "worker_tasks", "work_ledger",
                "trigger_state", "system_state_log", "request_limiter",
                "open_decisions", "runtime_pipelines"):
        assert key in result, f"missing key {key}"
        entry = result[key]
        assert entry.get("error") == "table_missing"


def test_query_orchestration_state_shape(
    tmp_path: Path, harness: HarnessServer,
) -> None:
    """Fully seeded DB produces the documented shape."""
    db_path = tmp_path / "operational.db"

    async def _run() -> dict:
        await _init_operational(db_path)
        await _seed_orchestration(db_path)
        return await harness._query_orchestration_state(db_path)

    result = asyncio.run(_run())
    assert result["available"] is True

    pi = result["pipeline_instances"]
    assert pi["total"] == 2
    assert pi["by_state"]["completed"] == 1
    assert pi["by_state"]["running"] == 1
    assert pi["by_name"]["post_session_memory"] == 1
    assert len(pi["recent"]) == 2

    wt = result["worker_tasks"]
    assert wt["total"] == 2
    assert wt["active_count"] >= 1  # "running"

    wl = result["work_ledger"]
    assert wl["total"] == 4
    assert wl["by_event_type"]["pipeline_started"] == 1
    assert wl["by_event_type"]["pipeline_completed"] == 1
    assert len(wl["recent"]) == 4

    ts = result["trigger_state"]
    assert ts["total_triggers_tracked"] == 1
    assert len(ts["last_fires"]) == 1

    ss = result["system_state_log"]
    assert ss["transitions_total"] == 2
    assert ss["current_phase"] in ("active_idle", "light_idle")

    rl = result["request_limiter"]
    assert rl["total_requests_logged"] == 4
    assert rl["window_seconds"] == 18000
    for cls in ("CONVERSATION", "NOTIFICATION",
                "BOUNDED_BACKGROUND", "LONG_BACKGROUND"):
        assert rl["by_class"][cls] == 1

    od = result["open_decisions"]
    assert od["total"] == 1
    assert od["by_status"]["pending"] == 1

    rp = result["runtime_pipelines"]
    assert rp["total"] == 1
    assert "custom_pipeline" in rp["names"]


# ── test_query_memory_lifecycle ──────────────────────────────────────────


def test_query_memory_lifecycle_shape(
    tmp_path: Path, harness: HarnessServer,
) -> None:
    proj_path = tmp_path / "projection.db"
    op_path = tmp_path / "operational.db"

    async def _run() -> dict:
        await _init_projection(proj_path)
        await _init_operational(op_path)
        await _seed_projection(proj_path)
        await _seed_proactive(op_path)
        return await harness._query_memory_lifecycle_state(proj_path, op_path)

    result = asyncio.run(_run())
    assert result["available"] is True

    mem = result["memories"]
    assert mem["total"] == 3
    # by_status buckets: active=2 (m_1, m_3), consolidated=1 (m_2)
    assert mem["by_status"]["active"] == 2
    assert mem["by_status"]["consolidated"] == 1
    assert mem["with_consolidated_into"] == 1
    assert mem["with_merged_from"] == 1

    umf = result["user_model_facts"]
    assert umf["total"] == 1
    assert umf["by_status"]["active"] == 1

    ents = result["entities"]
    assert ents["total"] == 2
    assert ents["by_type"]["person"] == 1
    assert ents["by_type"]["place"] == 1

    assert result["entity_links"]["total"] == 1

    sess = result["sessions"]
    assert sess["transcripts_total"] == 2
    assert sess["processed"] == 1
    assert sess["unprocessed"] == 1

    sq = result["signal_queue"]
    assert sq["total"] == 1
    assert sq["by_status"]["pending"] == 1

    assert result["dedup_rejected_pairs"]["total"] == 1


# ── test_query_vault_state ───────────────────────────────────────────────


def _make_vault(root: Path) -> None:
    """Build a minimal _KoraMemory/ tree for the walker."""
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("Long-Term/Episodic", "Long-Term/Reflective", "Long-Term/Procedural",
                "User Model/Health", "Entities/People", "Entities/Places",
                "Entities/Projects", "Inbox", "References", "Ideas",
                "Sessions", "Maps of Content"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    # Memory notes with wikilinks
    (root / "Long-Term/Episodic/note1.md").write_text(
        "---\ntype: episodic\n---\n# Note 1\n\nSee [[Sarah]] and [[Portland]].\n"
    )
    (root / "Long-Term/Reflective/r1.md").write_text(
        "# R1\n\nReflection on [[work]].\n"
    )
    (root / "User Model/Health/cpap.md").write_text(
        "---\ndomain: health\n---\n# CPAP\n"
    )
    (root / "Entities/People/sarah.md").write_text("# Sarah\n")
    (root / "Sessions/2026-04-15.md").write_text("# Session\n")
    (root / "Maps of Content/MOC - People.md").write_text("# MOC People\n")

    # Working doc: pipeline frontmatter in Inbox
    (root / "Inbox/post_session_memory_abc.md").write_text(
        "---\n"
        "pipeline: post_session_memory\n"
        "instance_id: abc123\n"
        "status: in_progress\n"
        "---\n\n# Goal\nRun the steward.\n"
    )
    # Inbox non-working-doc (no frontmatter)
    (root / "Inbox/quick_note.md").write_text("# Quick note\nJust an idea.\n")


def test_query_vault_state_shape(tmp_path: Path, harness: HarnessServer) -> None:
    vault = tmp_path / "_KoraMemory"
    _make_vault(vault)

    result = asyncio.run(harness._query_vault_state(vault))
    assert result["exists"] is True
    assert result["folder_hierarchy_present"] is True

    counts = result["counts"]
    # 8 files total: 6 "real" notes + 2 Inbox entries (working doc + quick note).
    assert counts["total_notes"] == 8
    assert counts["long_term_episodic"] == 1
    assert counts["long_term_reflective"] == 1
    assert counts["user_model"] == 1
    assert counts["entities_people"] == 1
    assert counts["inbox"] == 2
    assert counts["sessions"] == 1
    assert counts["moc_pages"] == 1

    # Working doc detection
    docs = result["working_docs"]
    assert len(docs) == 1
    d = docs[0]
    assert d["pipeline_name"] == "post_session_memory"
    assert d["status"] == "in_progress"
    assert "post_session_memory_abc.md" in d["path"]
    assert d["size_bytes"] > 0
    assert d["mtime"]

    # Wikilinks: note1 has 2, r1 has 1
    dens = result["wikilink_density"]
    assert dens["notes_with_wikilinks"] == 2
    assert dens["total_wikilinks"] == 3


# ── test_query_proactive_state ───────────────────────────────────────────


def test_query_proactive_state_shape(
    tmp_path: Path, harness: HarnessServer,
) -> None:
    db_path = tmp_path / "operational.db"

    async def _run() -> dict:
        await _init_operational(db_path)
        await _seed_proactive(db_path)
        return await harness._query_proactive_state(db_path)

    result = asyncio.run(_run())
    assert result["available"] is True

    notif = result["notifications"]
    assert notif["total"] == 3
    assert notif["by_tier"]["templated"] == 2
    assert notif["by_tier"]["llm"] == 1
    assert notif["by_reason"]["delivered"] == 3

    rem = result["reminders"]
    assert rem["total"] == 2
    assert rem["by_status"]["delivered"] == 1
    assert rem["by_status"]["pending"] == 1
    assert rem["mean_delivery_slip_seconds"] == pytest.approx(30.0, abs=0.5)

    ins = result["insights"]
    # Phase 8: insights are emitted as events, not persisted.
    assert ins["persisted"] is False


# ── test_snapshot_full_state ─────────────────────────────────────────────


def test_snapshot_full_state_includes_all_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: HarnessServer,
) -> None:
    """``_snapshot_full_state()`` returns every AT2 dimension."""
    # Redirect the module-level PROJECT_ROOT so relative paths resolve
    # against tmp_path. This patches the in-module reference that the
    # no-arg query methods use as a default.
    from tests.acceptance import _harness_server as hs_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    op_path = data_dir / "operational.db"
    proj_path = data_dir / "projection.db"

    async def _setup() -> None:
        await _init_operational(op_path)
        await _init_projection(proj_path)
        await _seed_orchestration(op_path)
        await _seed_proactive(op_path)
        await _seed_projection(proj_path)

    asyncio.run(_setup())

    # Patch PROJECT_ROOT so default query paths resolve to tmp_path.
    monkeypatch.setattr(hs_mod, "PROJECT_ROOT", tmp_path)

    snap = asyncio.run(harness._snapshot_full_state())
    for key in ("autonomous_state", "orchestration_state",
                "memory_lifecycle", "vault_state", "proactive_state"):
        assert key in snap, f"missing {key}"
    # Orchestration snapshot should be available (tables seeded).
    assert snap["orchestration_state"]["available"] is True
    # Memory lifecycle queried projection.db.
    assert snap["memory_lifecycle"]["available"] is True
    assert snap["memory_lifecycle"]["memories"]["total"] == 3
    # Proactive queried operational.db.
    assert snap["proactive_state"]["available"] is True
    # Vault does not exist under tmp_path — should still return cleanly.
    assert snap["vault_state"]["exists"] is False
