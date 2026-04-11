"""Kora V2 — Operational database (aiosqlite).

Creates and manages the ``operational.db`` schema: sessions, quality
metrics, autonomous checkpoints, notifications, telemetry, and audit log.
"""

from __future__ import annotations

import re
from pathlib import Path

import aiosqlite

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# ── Schema DDL ───────────────────────────────────────────────────────────

_SCHEMA_SQL = """\
-- Sessions -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id                   TEXT PRIMARY KEY,
    started_at           TEXT NOT NULL,
    ended_at             TEXT,
    turn_count           INTEGER DEFAULT 0,
    duration_seconds     INTEGER,
    emotional_state_start TEXT,
    emotional_state_end  TEXT,
    continuation_of      TEXT,
    bridge_note_path     TEXT
);

-- Quality metrics ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quality_metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT REFERENCES sessions(id),
    turn_number  INTEGER,
    metric_name  TEXT NOT NULL,
    metric_value REAL NOT NULL,
    recorded_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quality_metrics_session
    ON quality_metrics(session_id, metric_name);
CREATE INDEX IF NOT EXISTS idx_quality_metrics_time
    ON quality_metrics(recorded_at);

-- Quality evaluations --------------------------------------------------------
CREATE TABLE IF NOT EXISTS quality_evaluations (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id                TEXT REFERENCES sessions(id),
    turn_number               INTEGER,
    relevance                 REAL,
    personality_consistency   REAL,
    emotional_appropriateness REAL,
    adhd_friendliness         REAL,
    completeness              REAL,
    overall                   REAL,
    evaluated_at              TEXT NOT NULL
);

-- Autonomous checkpoints -----------------------------------------------------
CREATE TABLE IF NOT EXISTS autonomous_checkpoints (
    id                  TEXT PRIMARY KEY,
    plan_id             TEXT NOT NULL,
    plan_json           TEXT NOT NULL,
    completed_steps     TEXT NOT NULL,
    current_step        TEXT,
    accumulated_context TEXT,
    artifacts           TEXT,
    elapsed_minutes     INTEGER,
    reflection          TEXT,
    created_at          TEXT NOT NULL
);

-- Notifications --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notifications (
    id               TEXT PRIMARY KEY,
    priority         TEXT NOT NULL,
    content          TEXT NOT NULL,
    category         TEXT,
    delivered_at     TEXT NOT NULL,
    acknowledged_at  TEXT,
    delivery_channel TEXT
);

-- Notification engagement ----------------------------------------------------
CREATE TABLE IF NOT EXISTS notification_engagement (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id          TEXT NOT NULL,
    category                 TEXT,
    window_day               INTEGER NOT NULL,
    window_hour              INTEGER NOT NULL,
    delivered_at             TEXT NOT NULL,
    responded_at             TEXT,
    response_latency_seconds INTEGER,
    metadata_json            TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_engagement_notification
    ON notification_engagement(notification_id);
CREATE INDEX IF NOT EXISTS idx_notification_engagement_window
    ON notification_engagement(window_day, window_hour, category);

-- Telemetry ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         TEXT,
    agent_name         TEXT NOT NULL,
    tokens_in          INTEGER,
    tokens_out         INTEGER,
    latency_ms         INTEGER,
    tool_calls         INTEGER,
    quality_gate_passed BOOLEAN,
    recorded_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telemetry_agent
    ON telemetry(agent_name, recorded_at);

-- Audit log ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT NOT NULL,
    details     TEXT,
    actor       TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

-- Turn traces ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS turn_traces (
    id              TEXT PRIMARY KEY,
    session_id      TEXT REFERENCES sessions(id),
    turn_number     INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    latency_ms      INTEGER,
    succeeded       INTEGER DEFAULT 0,
    response_length INTEGER,
    tool_call_count INTEGER DEFAULT 0,
    user_input      TEXT,
    final_output    TEXT,
    error_text      TEXT,
    interrupted     INTEGER DEFAULT 0,
    resolved_route  TEXT,
    tools_invoked   TEXT,
    workers_invoked TEXT,
    retries         INTEGER DEFAULT 0,
    artifact_references TEXT,
    permission_events   TEXT,
    compaction_failure_count INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_turn_traces_session
    ON turn_traces(session_id, turn_number);

-- Turn trace events (streaming record of what happened within a turn) -------
CREATE TABLE IF NOT EXISTS turn_trace_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id    TEXT NOT NULL REFERENCES turn_traces(id),
    event_type  TEXT NOT NULL,
    payload     TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turn_trace_events_trace
    ON turn_trace_events(trace_id);

-- Permission grants ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS permission_grants (
    id          TEXT PRIMARY KEY,
    tool_name   TEXT NOT NULL,
    scope       TEXT NOT NULL,
    risk_level  TEXT NOT NULL,
    decision    TEXT NOT NULL,
    reason      TEXT,
    provenance  TEXT,
    recorded_by TEXT,
    granted_at  TEXT NOT NULL,
    expires_at  TEXT,
    session_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_permission_grants_tool
    ON permission_grants(tool_name, scope, decision);
CREATE INDEX IF NOT EXISTS idx_permission_grants_session
    ON permission_grants(session_id);

-- Autonomous plans (goal-level items) ---------------------------------------
CREATE TABLE IF NOT EXISTS autonomous_plans (
    id          TEXT PRIMARY KEY,
    session_id  TEXT REFERENCES sessions(id),
    goal        TEXT NOT NULL,
    mode        TEXT NOT NULL DEFAULT 'task',
    status      TEXT NOT NULL DEFAULT 'planned',
    confidence  REAL,
    created_at  TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_autonomous_plans_session
    ON autonomous_plans(session_id);

-- Items (task/step persistence) -----------------------------------------------
CREATE TABLE IF NOT EXISTS items (
    id                    TEXT PRIMARY KEY,
    parent_id             TEXT REFERENCES items(id),
    autonomous_plan_id    TEXT REFERENCES autonomous_plans(id),
    type                  TEXT NOT NULL DEFAULT 'task',
    owner                 TEXT NOT NULL DEFAULT 'kora',
    title                 TEXT NOT NULL,
    description           TEXT,
    status                TEXT NOT NULL DEFAULT 'planned',
    energy_level          TEXT,
    estimated_minutes     INTEGER,
    confidence            REAL,
    next_recommended_move TEXT,
    spawned_from          TEXT,
    context_tags          TEXT,   -- JSON array
    progress_pct          REAL DEFAULT 0.0,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_parent
    ON items(parent_id);
CREATE INDEX IF NOT EXISTS idx_items_plan
    ON items(autonomous_plan_id);
CREATE INDEX IF NOT EXISTS idx_items_status
    ON items(status);

-- Item state history -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS item_state_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT NOT NULL REFERENCES items(id),
    from_status TEXT,
    to_status   TEXT NOT NULL,
    reason      TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_item_state_history_item
    ON item_state_history(item_id);

-- Item artifact links ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS item_artifact_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT NOT NULL REFERENCES items(id),
    artifact_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,  -- file, url, data, report, code
    uri         TEXT NOT NULL,
    label       TEXT,
    size_bytes  INTEGER,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_item_artifact_links_item
    ON item_artifact_links(item_id);

-- Item dependency links -------------------------------------------------------
CREATE TABLE IF NOT EXISTS item_deps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_item   TEXT NOT NULL REFERENCES items(id),
    to_item     TEXT NOT NULL REFERENCES items(id),
    rel_type    TEXT NOT NULL DEFAULT 'blocks'  -- blocks, depends_on, contains
);
CREATE INDEX IF NOT EXISTS idx_item_deps_from
    ON item_deps(from_item);
CREATE INDEX IF NOT EXISTS idx_item_deps_to
    ON item_deps(to_item);

-- Routines (Phase 6B templates) -----------------------------------------------
CREATE TABLE IF NOT EXISTS routines (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT,
    steps_json    TEXT NOT NULL,   -- JSON array of step objects
    low_energy_variant_json TEXT,  -- JSON array for low-energy version
    tags          TEXT,            -- JSON array
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Routine sessions (Phase 6B partial completion) ------------------------------
CREATE TABLE IF NOT EXISTS routine_sessions (
    id                   TEXT PRIMARY KEY,
    routine_id           TEXT NOT NULL REFERENCES routines(id),
    session_id           TEXT REFERENCES sessions(id),
    variant              TEXT NOT NULL DEFAULT 'standard',  -- standard/low_energy
    current_step_index   INTEGER DEFAULT 0,
    completed_steps      TEXT NOT NULL DEFAULT '[]',  -- JSON array of step indices
    skipped_steps        TEXT NOT NULL DEFAULT '[]',  -- JSON array of step indices
    checkpoint_state     TEXT,                         -- JSON AutonomousState snapshot
    last_nudge_at        TEXT,
    completion_confidence REAL DEFAULT 0.0,
    status               TEXT NOT NULL DEFAULT 'active',  -- active/completed/abandoned
    started_at           TEXT NOT NULL,
    completed_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_routine_sessions_routine
    ON routine_sessions(routine_id);
CREATE INDEX IF NOT EXISTS idx_routine_sessions_session
    ON routine_sessions(session_id);

-- Autonomous updates (unread summaries for foreground delivery) --------
CREATE TABLE IF NOT EXISTS autonomous_updates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    plan_id     TEXT,
    update_type TEXT NOT NULL,   -- 'checkpoint' or 'completion'
    summary     TEXT NOT NULL,   -- human-readable summary
    payload     TEXT,            -- JSON with details
    delivered   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autonomous_updates_session
    ON autonomous_updates(session_id, delivered);

-- Life management: reminders ---------------------------------------------------
CREATE TABLE IF NOT EXISTS reminders (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT,
    remind_at    TEXT,
    recurring    TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    session_id   TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status, remind_at);

-- Life management: medication log ---------------------------------------------
CREATE TABLE IF NOT EXISTS medication_log (
    id               TEXT PRIMARY KEY,
    medication_name  TEXT NOT NULL,
    dose             TEXT,
    taken_at         TEXT NOT NULL,
    notes            TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_medication_log_taken ON medication_log(taken_at);

-- Life management: meal log ----------------------------------------------------
CREATE TABLE IF NOT EXISTS meal_log (
    id           TEXT PRIMARY KEY,
    meal_type    TEXT NOT NULL DEFAULT 'meal',
    description  TEXT NOT NULL,
    calories     INTEGER,
    tags         TEXT,
    logged_at    TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_meal_log_logged ON meal_log(logged_at);

-- Life management: focus blocks ------------------------------------------------
CREATE TABLE IF NOT EXISTS focus_blocks (
    id          TEXT PRIMARY KEY,
    label       TEXT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    notes       TEXT,
    completed   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_focus_blocks_status ON focus_blocks(ended_at);

-- Life management: quick notes -------------------------------------------------
CREATE TABLE IF NOT EXISTS quick_notes (
    id         TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    tags       TEXT,
    created_at TEXT NOT NULL
);

-- Autonomous plan budget columns: request_count, token_estimate, cost_estimate
-- Added as ALTER TABLE below since autonomous_plans was created without them.
"""

_TURN_TRACE_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("user_input", "ALTER TABLE turn_traces ADD COLUMN user_input TEXT"),
    ("final_output", "ALTER TABLE turn_traces ADD COLUMN final_output TEXT"),
    ("error_text", "ALTER TABLE turn_traces ADD COLUMN error_text TEXT"),
    ("interrupted", "ALTER TABLE turn_traces ADD COLUMN interrupted INTEGER DEFAULT 0"),
    ("resolved_route", "ALTER TABLE turn_traces ADD COLUMN resolved_route TEXT"),
    ("tools_invoked", "ALTER TABLE turn_traces ADD COLUMN tools_invoked TEXT"),
    ("workers_invoked", "ALTER TABLE turn_traces ADD COLUMN workers_invoked TEXT"),
    ("retries", "ALTER TABLE turn_traces ADD COLUMN retries INTEGER DEFAULT 0"),
    (
        "artifact_references",
        "ALTER TABLE turn_traces ADD COLUMN artifact_references TEXT",
    ),
    ("permission_events", "ALTER TABLE turn_traces ADD COLUMN permission_events TEXT"),
    (
        "compaction_failure_count",
        "ALTER TABLE turn_traces ADD COLUMN compaction_failure_count INTEGER DEFAULT 0",
    ),
)

_PERMISSION_GRANT_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("reason", "ALTER TABLE permission_grants ADD COLUMN reason TEXT"),
    ("provenance", "ALTER TABLE permission_grants ADD COLUMN provenance TEXT"),
    ("recorded_by", "ALTER TABLE permission_grants ADD COLUMN recorded_by TEXT"),
)

_AUTONOMOUS_PLAN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "request_count",
        "ALTER TABLE autonomous_plans ADD COLUMN request_count INTEGER DEFAULT 0",
    ),
    (
        "token_estimate",
        "ALTER TABLE autonomous_plans ADD COLUMN token_estimate INTEGER DEFAULT 0",
    ),
    (
        "cost_estimate",
        "ALTER TABLE autonomous_plans ADD COLUMN cost_estimate REAL DEFAULT 0.0",
    ),
    (
        "updated_at",
        "ALTER TABLE autonomous_plans ADD COLUMN updated_at TEXT",
    ),
)


async def _ensure_columns(
    db: aiosqlite.Connection,
    table: str,
    migrations: tuple[tuple[str, str], ...],
) -> None:
    """Apply additive column migrations for an existing table."""
    if not _IDENTIFIER_RE.match(table):
        raise ValueError(f"Invalid table identifier: {table!r}")
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        rows = await cursor.fetchall()
    existing = {row[1] for row in rows}

    for column_name, statement in migrations:
        if column_name in existing:
            continue
        await db.execute(statement)


# ── Public API ───────────────────────────────────────────────────────────

async def init_operational_db(db_path: Path) -> None:
    """Create all runtime tables (idempotent) in *db_path*.

    The parent directory is created if it does not exist.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(db_path)) as db:
        await db.executescript(_SCHEMA_SQL)
        await _ensure_columns(db, "turn_traces", _TURN_TRACE_MIGRATIONS)
        await _ensure_columns(db, "permission_grants", _PERMISSION_GRANT_MIGRATIONS)
        await _ensure_columns(db, "autonomous_plans", _AUTONOMOUS_PLAN_MIGRATIONS)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.commit()


async def get_db(db_path: Path) -> aiosqlite.Connection:
    """Open (or create) the database at *db_path* and return the connection.

    The caller is responsible for closing the connection, typically via
    ``async with``::

        db = await get_db(path)
        async with db:
            ...

    WAL mode is enabled on every fresh connection for concurrency.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(db_path))
    await db.execute("PRAGMA journal_mode=WAL")
    db.row_factory = aiosqlite.Row
    return db
