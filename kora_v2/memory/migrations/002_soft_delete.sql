-- Soft-delete support for consolidation and deduplication
ALTER TABLE memories ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE memories ADD COLUMN consolidated_into TEXT;
ALTER TABLE memories ADD COLUMN merged_from TEXT;        -- JSON array
ALTER TABLE memories ADD COLUMN deleted_at TEXT;

ALTER TABLE user_model_facts ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE user_model_facts ADD COLUMN consolidated_into TEXT;
ALTER TABLE user_model_facts ADD COLUMN merged_from TEXT;
ALTER TABLE user_model_facts ADD COLUMN deleted_at TEXT;

CREATE INDEX idx_memories_status ON memories(status);
CREATE INDEX idx_user_model_facts_status ON user_model_facts(status);
