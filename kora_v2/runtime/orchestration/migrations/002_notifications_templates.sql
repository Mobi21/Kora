-- Phase 7.5 Orchestration Layer — two-tier notifications.
--
-- Adds the three columns the NotificationGate needs on the existing
-- `notifications` table (owned by kora_v2/core/db.py). The base table
-- is created by the operational-DB initialiser, so this migration only
-- adds columns. ALTER TABLE ADD COLUMN is not idempotent in SQLite, so
-- the Python migration runner checks `pragma table_info(notifications)`
-- before issuing these statements. See
-- `kora_v2/runtime/orchestration/registry.py` for the dispatcher.
--
-- Columns per spec §16.1 lines 1539-1541:
--   delivery_tier  — "llm" | "templated"
--   template_id    — matches TemplateRegistry entry id
--   template_vars  — JSON-encoded variables dict

ALTER TABLE notifications ADD COLUMN delivery_tier TEXT DEFAULT 'llm';
ALTER TABLE notifications ADD COLUMN template_id TEXT;
ALTER TABLE notifications ADD COLUMN template_vars TEXT;
ALTER TABLE notifications ADD COLUMN reason TEXT;
