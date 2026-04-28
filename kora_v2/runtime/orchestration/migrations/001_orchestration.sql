-- Phase 7.5 Orchestration Layer schema.
--
-- Creates the eight tables the orchestration engine needs on top of the
-- shared operational.db, plus three additive columns on the existing
-- `notifications` table for two-tier delivery tracking.
--
-- All statements are idempotent (CREATE TABLE IF NOT EXISTS / CREATE
-- INDEX IF NOT EXISTS) so `init_orchestration_schema` can run on every
-- boot without failing on re-application.

-- Pipeline instances (pipeline runs, active or historical)
CREATE TABLE IF NOT EXISTS pipeline_instances (
    id                     TEXT PRIMARY KEY,
    pipeline_name          TEXT NOT NULL,
    working_doc_path       TEXT NOT NULL,
    parent_session_id      TEXT,
    parent_task_id         TEXT,
    goal                   TEXT NOT NULL,
    state                  TEXT NOT NULL,
    intent_duration        TEXT,
    started_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    completed_at           TEXT,
    completion_reason      TEXT
);
CREATE INDEX IF NOT EXISTS idx_pipeline_instances_state
    ON pipeline_instances(state);
CREATE INDEX IF NOT EXISTS idx_pipeline_instances_session
    ON pipeline_instances(parent_session_id);

-- Worker tasks (scheduling mirror of Current Plan in working docs).
-- pipeline_instance_id is intentionally nullable: standalone tasks
-- (spec §3.1: "None for standalone tasks") do not belong to a
-- pipeline. Spec §16.1's SQL block has a stale `NOT NULL` here that
-- should be ignored — the dataclass and the unit tests rely on
-- pipeline_instance_id being optional.
CREATE TABLE IF NOT EXISTS worker_tasks (
    id                     TEXT PRIMARY KEY,
    pipeline_instance_id   TEXT REFERENCES pipeline_instances(id) ON DELETE CASCADE,
    parent_task_id         TEXT,
    stage_name             TEXT NOT NULL,
    task_preset            TEXT NOT NULL,
    state                  TEXT NOT NULL,
    depends_on             TEXT,
    tool_scope             TEXT NOT NULL,
    system_prompt          TEXT NOT NULL,
    goal                   TEXT NOT NULL DEFAULT '',
    checkpoint_blob        TEXT,
    request_count          INTEGER NOT NULL DEFAULT 0,
    agent_turn_count       INTEGER NOT NULL DEFAULT 0,
    cancellation_requested INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL,
    last_step_at           TEXT,
    last_checkpoint_at     TEXT,
    completed_at           TEXT,
    result_summary         TEXT,
    error_message          TEXT,
    result_acknowledged_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_worker_tasks_pipeline
    ON worker_tasks(pipeline_instance_id);
CREATE INDEX IF NOT EXISTS idx_worker_tasks_state
    ON worker_tasks(state);

-- Work ledger (append-only audit trail)
CREATE TABLE IF NOT EXISTS work_ledger (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp              TEXT NOT NULL,
    event_type             TEXT NOT NULL,
    pipeline_instance_id   TEXT,
    worker_task_id         TEXT,
    trigger_name           TEXT,
    reason                 TEXT,
    metadata_json          TEXT
);
CREATE INDEX IF NOT EXISTS idx_work_ledger_pipeline
    ON work_ledger(pipeline_instance_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_work_ledger_task
    ON work_ledger(worker_task_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_work_ledger_type
    ON work_ledger(event_type, timestamp);

-- Trigger state (persistent last-fire)
CREATE TABLE IF NOT EXISTS trigger_state (
    trigger_id             TEXT PRIMARY KEY,
    pipeline_name          TEXT,
    last_fired_at          TEXT NOT NULL,
    last_fire_reason       TEXT,
    next_eligible_at       TEXT
);

-- Request limiter log (sliding window)
CREATE TABLE IF NOT EXISTS request_limiter_log (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp              TEXT NOT NULL,
    class                  TEXT NOT NULL,
    worker_task_id         TEXT,
    request_count          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_request_limiter_time
    ON request_limiter_log(timestamp);

-- System state log (transition history)
CREATE TABLE IF NOT EXISTS system_state_log (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    transitioned_at        TEXT NOT NULL,
    previous_phase         TEXT NOT NULL,
    new_phase              TEXT NOT NULL,
    reason                 TEXT,
    context_json           TEXT
);

-- Open decisions tracker
CREATE TABLE IF NOT EXISTS open_decisions (
    id                     TEXT PRIMARY KEY,
    topic                  TEXT NOT NULL,
    posed_at               TEXT NOT NULL,
    posed_in_session       TEXT,
    context                TEXT,
    status                 TEXT NOT NULL DEFAULT 'open',
    resolved_at            TEXT,
    resolution             TEXT
);
CREATE INDEX IF NOT EXISTS idx_open_decisions_status
    ON open_decisions(status, posed_at);

-- Runtime pipelines (user-created, persisted)
CREATE TABLE IF NOT EXISTS runtime_pipelines (
    name                   TEXT PRIMARY KEY,
    declaration_json       TEXT NOT NULL,
    created_at             TEXT NOT NULL,
    created_by_session     TEXT,
    enabled                INTEGER NOT NULL DEFAULT 1
);
