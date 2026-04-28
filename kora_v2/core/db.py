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
-- Note: idx_reminders_due is created post-migration (see init_operational_db)
-- because the due_at column is added by _REMINDERS_MIGRATIONS.

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

-- Phase 5: Calendar entries (unified timeline for events, meds, focus blocks) -
CREATE TABLE IF NOT EXISTS calendar_entries (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL DEFAULT 'event',
    title           TEXT NOT NULL,
    description     TEXT,
    starts_at       TEXT NOT NULL,
    ends_at         TEXT,
    all_day         INTEGER DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'kora',
    google_event_id TEXT,
    recurring_rule  TEXT,
    energy_match    TEXT,
    location        TEXT,
    metadata        TEXT,
    synced_at       TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    override_parent_id       TEXT,
    override_occurrence_date TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_calendar_entries_date
    ON calendar_entries(starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_calendar_entries_kind
    ON calendar_entries(kind, starts_at);
CREATE INDEX IF NOT EXISTS idx_calendar_entries_google
    ON calendar_entries(google_event_id) WHERE google_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_calendar_entries_override
    ON calendar_entries(override_parent_id, override_occurrence_date)
    WHERE override_parent_id IS NOT NULL;

-- Phase 5: Finance log ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS finance_log (
    id          TEXT PRIMARY KEY,
    amount      REAL NOT NULL,
    category    TEXT NOT NULL,
    description TEXT,
    is_impulse  INTEGER DEFAULT 0,
    logged_at   TEXT NOT NULL DEFAULT (datetime('now')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_finance_log_logged
    ON finance_log(logged_at);
CREATE INDEX IF NOT EXISTS idx_finance_log_category
    ON finance_log(category, logged_at);

-- Phase 5: Energy log ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS energy_log (
    id          TEXT PRIMARY KEY,
    level       TEXT NOT NULL,
    focus       TEXT,
    source      TEXT NOT NULL,
    notes       TEXT,
    logged_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_energy_log_logged
    ON energy_log(logged_at);

-- Life OS: durable product-domain proof --------------------------------------
CREATE TABLE IF NOT EXISTS domain_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT,
    source_service TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_domain_events_type_created
    ON domain_events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_domain_events_aggregate
    ON domain_events(aggregate_type, aggregate_id, created_at);

CREATE TABLE IF NOT EXISTS day_plans (
    id TEXT PRIMARY KEY,
    plan_date TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    supersedes_day_plan_id TEXT,
    generated_from TEXT NOT NULL DEFAULT 'conversation',
    load_assessment_id TEXT,
    summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_day_plans_one_active
    ON day_plans(plan_date)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_day_plans_date_revision
    ON day_plans(plan_date, revision);

CREATE TABLE IF NOT EXISTS day_plan_entries (
    id TEXT PRIMARY KEY,
    day_plan_id TEXT NOT NULL,
    calendar_entry_id TEXT,
    item_id TEXT,
    reminder_id TEXT,
    routine_id TEXT,
    title TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    intended_start TEXT,
    intended_end TEXT,
    expected_effort TEXT,
    support_tags TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    reality_state TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(day_plan_id) REFERENCES day_plans(id)
);
CREATE INDEX IF NOT EXISTS idx_day_plan_entries_plan_status
    ON day_plan_entries(day_plan_id, status, reality_state);
CREATE INDEX IF NOT EXISTS idx_day_plan_entries_sources
    ON day_plan_entries(calendar_entry_id, item_id);

CREATE TABLE IF NOT EXISTS life_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    event_time TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    confirmation_state TEXT NOT NULL DEFAULT 'confirmed',
    calendar_entry_id TEXT,
    item_id TEXT,
    day_plan_entry_id TEXT,
    support_module TEXT,
    title TEXT,
    details TEXT,
    raw_text TEXT,
    metadata TEXT,
    supersedes_event_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_life_events_time_type
    ON life_events(event_time, event_type);
CREATE INDEX IF NOT EXISTS idx_life_events_plan_entry
    ON life_events(day_plan_entry_id, confirmation_state);

CREATE TABLE IF NOT EXISTS load_assessments (
    id TEXT PRIMARY KEY,
    assessment_date TEXT NOT NULL,
    score REAL NOT NULL,
    band TEXT NOT NULL,
    confidence REAL NOT NULL,
    factors TEXT NOT NULL,
    recommended_mode TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    confirmed_by_user INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_load_assessments_date
    ON load_assessments(assessment_date, generated_at);

CREATE TABLE IF NOT EXISTS plan_repair_actions (
    id TEXT PRIMARY KEY,
    day_plan_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    title TEXT NOT NULL,
    reason TEXT NOT NULL,
    source_event_id TEXT,
    load_assessment_id TEXT,
    target_calendar_entry_id TEXT,
    target_item_id TEXT,
    target_day_plan_entry_id TEXT,
    proposed_changes TEXT NOT NULL,
    requires_confirmation INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL,
    applied_at TEXT,
    rejected_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_plan_repair_actions_plan_status
    ON plan_repair_actions(day_plan_id, status);

CREATE TABLE IF NOT EXISTS nudge_decisions (
    id TEXT PRIMARY KEY,
    candidate_type TEXT NOT NULL,
    candidate_payload TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    urgency TEXT NOT NULL,
    support_tags TEXT,
    load_assessment_id TEXT,
    notification_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nudge_decisions_created
    ON nudge_decisions(created_at, decision);

CREATE TABLE IF NOT EXISTS nudge_feedback (
    id TEXT PRIMARY KEY,
    nudge_decision_id TEXT NOT NULL,
    feedback TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nudge_feedback_decision
    ON nudge_feedback(nudge_decision_id, feedback);

CREATE TABLE IF NOT EXISTS support_mode_state (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    trigger_event_id TEXT,
    load_assessment_id TEXT,
    reason TEXT,
    user_confirmed INTEGER DEFAULT 0,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_support_mode_state_active
    ON support_mode_state(status, mode, started_at);

CREATE TABLE IF NOT EXISTS context_packs (
    id TEXT PRIMARY KEY,
    calendar_entry_id TEXT,
    item_id TEXT,
    title TEXT NOT NULL,
    pack_type TEXT NOT NULL,
    status TEXT NOT NULL,
    content_path TEXT,
    summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_context_packs_target
    ON context_packs(calendar_entry_id, item_id, status);

CREATE TABLE IF NOT EXISTS future_self_bridges (
    id TEXT PRIMARY KEY,
    bridge_date TEXT NOT NULL,
    source_day_plan_id TEXT,
    load_assessment_id TEXT,
    summary TEXT NOT NULL,
    carryovers TEXT NOT NULL,
    first_moves TEXT NOT NULL,
    content_path TEXT,
    created_at TEXT NOT NULL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_future_self_bridges_date
    ON future_self_bridges(bridge_date, created_at);

CREATE TABLE IF NOT EXISTS support_profiles (
    id TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    user_label TEXT,
    settings TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_support_profiles_status
    ON support_profiles(status, profile_key);

CREATE TABLE IF NOT EXISTS support_profile_signals (
    id TEXT PRIMARY KEY,
    profile_key TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    weight REAL NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    last_seen_at TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_support_profile_signals_profile
    ON support_profile_signals(profile_key, signal_type);

CREATE TABLE IF NOT EXISTS safety_boundary_records (
    id TEXT PRIMARY KEY,
    boundary_type TEXT NOT NULL,
    trigger_text TEXT,
    risk_level TEXT NOT NULL,
    preempted_flow TEXT,
    response_summary TEXT NOT NULL,
    input_excerpt TEXT,
    severity TEXT,
    matched_terms TEXT,
    preempted INTEGER,
    response_family TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_safety_boundary_records_created
    ON safety_boundary_records(boundary_type, created_at);

-- Autonomous plan budget columns: request_count, token_estimate, cost_estimate
-- Added as ALTER TABLE below since autonomous_plans was created without them.

-- Phase 8a: Signal extraction queue ----------------------------------------
CREATE TABLE IF NOT EXISTS signal_queue (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    message_text TEXT NOT NULL,
    assistant_response TEXT,
    signal_types TEXT NOT NULL,      -- JSON array of SignalType values
    priority INTEGER NOT NULL,       -- 1=highest
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    processed_at TEXT,
    error_message TEXT
);

-- Phase 8a: Session transcripts for Memory Steward consumption -------------
CREATE TABLE IF NOT EXISTS session_transcripts (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    messages TEXT NOT NULL,           -- JSON array of {role, content, timestamp}
    tool_calls TEXT,                  -- JSON array of tool invocations
    emotional_trajectory TEXT,        -- from session bridge
    processed_at TEXT                 -- NULL until extraction stage consumes it
);

-- Phase 8b: Dedup rejection pairs — persists LLM "distinct" verdicts so the
-- same pair is not re-evaluated every session.
CREATE TABLE IF NOT EXISTS dedup_rejected_pairs (
    id_a TEXT NOT NULL,
    id_b TEXT NOT NULL,
    rejected_at TEXT NOT NULL,
    PRIMARY KEY (id_a, id_b)
);
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
    # Phase 9 Task 4: capability/account/action/resource scoping
    ("capability", "ALTER TABLE permission_grants ADD COLUMN capability TEXT"),
    ("account", "ALTER TABLE permission_grants ADD COLUMN account TEXT"),
    ("action_key", "ALTER TABLE permission_grants ADD COLUMN action_key TEXT"),
    ("resource", "ALTER TABLE permission_grants ADD COLUMN resource TEXT"),
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

# Phase 5: items table extensions for planning (due_date/priority/goal_scope).
_ITEMS_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("due_date", "ALTER TABLE items ADD COLUMN due_date TEXT"),
    ("priority", "ALTER TABLE items ADD COLUMN priority INTEGER DEFAULT 3"),
    (
        "goal_scope",
        "ALTER TABLE items ADD COLUMN goal_scope TEXT NOT NULL DEFAULT 'task'",
    ),
)

# Phase 5: link focus_blocks rows to their pre-planned calendar_entry.
_FOCUS_BLOCK_MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "calendar_entry_id",
        "ALTER TABLE focus_blocks ADD COLUMN calendar_entry_id TEXT",
    ),
)

# Phase 8e: reminders table extensions for ReminderStore.
_REMINDERS_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("due_at", "ALTER TABLE reminders ADD COLUMN due_at TEXT"),
    ("repeat_rule", "ALTER TABLE reminders ADD COLUMN repeat_rule TEXT"),
    ("source", "ALTER TABLE reminders ADD COLUMN source TEXT NOT NULL DEFAULT 'user'"),
    ("delivered_at", "ALTER TABLE reminders ADD COLUMN delivered_at TEXT"),
    ("dismissed_at", "ALTER TABLE reminders ADD COLUMN dismissed_at TEXT"),
    ("metadata", "ALTER TABLE reminders ADD COLUMN metadata TEXT"),
)

_DAY_PLAN_ENTRY_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("reminder_id", "ALTER TABLE day_plan_entries ADD COLUMN reminder_id TEXT"),
    ("routine_id", "ALTER TABLE day_plan_entries ADD COLUMN routine_id TEXT"),
)

_CONTEXT_PACK_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("metadata", "ALTER TABLE context_packs ADD COLUMN metadata TEXT"),
)

_FUTURE_SELF_BRIDGE_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("metadata", "ALTER TABLE future_self_bridges ADD COLUMN metadata TEXT"),
)

_SAFETY_BOUNDARY_RECORD_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("input_excerpt", "ALTER TABLE safety_boundary_records ADD COLUMN input_excerpt TEXT"),
    ("severity", "ALTER TABLE safety_boundary_records ADD COLUMN severity TEXT"),
    ("matched_terms", "ALTER TABLE safety_boundary_records ADD COLUMN matched_terms TEXT"),
    ("preempted", "ALTER TABLE safety_boundary_records ADD COLUMN preempted INTEGER"),
    ("response_family", "ALTER TABLE safety_boundary_records ADD COLUMN response_family TEXT"),
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
        await _ensure_columns(db, "items", _ITEMS_MIGRATIONS)
        await _ensure_columns(db, "focus_blocks", _FOCUS_BLOCK_MIGRATIONS)
        await _ensure_columns(db, "reminders", _REMINDERS_MIGRATIONS)
        await _ensure_columns(db, "day_plan_entries", _DAY_PLAN_ENTRY_MIGRATIONS)
        await _ensure_columns(db, "context_packs", _CONTEXT_PACK_MIGRATIONS)
        await _ensure_columns(db, "future_self_bridges", _FUTURE_SELF_BRIDGE_MIGRATIONS)
        await _ensure_columns(
            db,
            "safety_boundary_records",
            _SAFETY_BOUNDARY_RECORD_MIGRATIONS,
        )
        # Phase 8e: idx_reminders_due references due_at, which is added by the
        # migration above — must be created AFTER the migration runs.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_due "
            "ON reminders(status, due_at)"
        )
        # Phase 9 Task 4: index on capability-scoped columns (idempotent via IF NOT EXISTS)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_permission_grants_capability "
            "ON permission_grants(capability, account, action_key)"
        )
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
