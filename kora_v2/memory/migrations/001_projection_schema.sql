-- Memories table (from _KoraMemory/Long-Term/)
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    summary TEXT,
    importance REAL DEFAULT 0.5,
    memory_type TEXT NOT NULL DEFAULT 'episodic',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    entities TEXT,
    tags TEXT,
    source_path TEXT NOT NULL
);

-- FTS5 index on memories (entities and tags are UNINDEXED to prevent JSON syntax pollution)
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, summary, entities UNINDEXED, tags UNINDEXED,
    content=memories, content_rowid=rowid
);

-- Triggers to keep FTS5 in sync
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, summary, entities, tags)
    VALUES (new.rowid, new.content, new.summary, new.entities, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, summary, entities, tags)
    VALUES('delete', old.rowid, old.content, old.summary, old.entities, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, summary, entities, tags)
    VALUES('delete', old.rowid, old.content, old.summary, old.entities, old.tags);
    INSERT INTO memories_fts(rowid, content, summary, entities, tags)
    VALUES (new.rowid, new.content, new.summary, new.entities, new.tags);
END;

-- User Model facts table
CREATE TABLE IF NOT EXISTS user_model_facts (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    evidence_count INTEGER DEFAULT 1,
    contradiction_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_path TEXT NOT NULL
);

-- FTS5 index on user model
CREATE VIRTUAL TABLE IF NOT EXISTS user_model_fts USING fts5(
    content, domain,
    content=user_model_facts, content_rowid=rowid
);

-- Triggers to keep user_model FTS5 in sync
CREATE TRIGGER IF NOT EXISTS user_model_ai AFTER INSERT ON user_model_facts BEGIN
    INSERT INTO user_model_fts(rowid, content, domain)
    VALUES (new.rowid, new.content, new.domain);
END;

CREATE TRIGGER IF NOT EXISTS user_model_ad AFTER DELETE ON user_model_facts BEGIN
    INSERT INTO user_model_fts(user_model_fts, rowid, content, domain)
    VALUES('delete', old.rowid, old.content, old.domain);
END;

CREATE TRIGGER IF NOT EXISTS user_model_au AFTER UPDATE ON user_model_facts BEGIN
    INSERT INTO user_model_fts(user_model_fts, rowid, content, domain)
    VALUES('delete', old.rowid, old.content, old.domain);
    INSERT INTO user_model_fts(rowid, content, domain)
    VALUES (new.rowid, new.content, new.domain);
END;

-- Entity index
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    metadata TEXT
);

-- Entity links (cross-layer)
CREATE TABLE IF NOT EXISTS entity_links (
    entity_id TEXT REFERENCES entities(id),
    memory_id TEXT,
    user_model_fact_id TEXT,
    relationship TEXT,
    CHECK (memory_id IS NOT NULL OR user_model_fact_id IS NOT NULL)
);

-- sqlite-vec virtual tables for vector search (768-dim nomic-embed-text-v1.5)
CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec USING vec0(
    embedding float[768]
);

CREATE VIRTUAL TABLE IF NOT EXISTS user_model_vec USING vec0(
    embedding float[768]
);
