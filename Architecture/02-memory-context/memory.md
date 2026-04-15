# Memory Subsystem (`kora_v2/memory/`)

The memory subsystem implements Kora's durable, searchable long-term memory. It is built around a filesystem-canonical model: every note is a plain Markdown file with YAML frontmatter; the SQLite projection database is a derived index that enables fast hybrid search. Writes always flow through the `WritePipeline`, which maintains consistency between the two stores. Reads use the `hybrid_search` function, which fans out to both vector and FTS5 search and merges results by weighted score.

---

## Files in this module

| File | Role |
|---|---|
| [`store.py`](../../kora_v2/memory/store.py) | `FilesystemMemoryStore` — read/write canonical `.md` notes |
| [`projection.py`](../../kora_v2/memory/projection.py) | `ProjectionDB` — async SQLite wrapper with sqlite-vec + FTS5 |
| [`migrations/001_projection_schema.sql`](../../kora_v2/memory/migrations/001_projection_schema.sql) | Schema DDL — tables, FTS5 virtual tables, vec0 tables, triggers |
| [`embeddings.py`](../../kora_v2/memory/embeddings.py) | `LocalEmbeddingModel` — nomic-embed-text-v1.5 via sentence-transformers |
| [`retrieval.py`](../../kora_v2/memory/retrieval.py) | Hybrid search: `vector_search`, `fts5_search`, `merge_and_rank`, `hybrid_search` |
| [`write_pipeline.py`](../../kora_v2/memory/write_pipeline.py) | `WritePipeline` — single orchestrated path from content to indexed storage |
| [`dedup.py`](../../kora_v2/memory/dedup.py) | `dedup_check` — FTS5 candidate search + LLM judgment |
| [`signal_scanner.py`](../../kora_v2/memory/signal_scanner.py) | `SignalScanner` — rule-based priority assignment for memory queue |
| [`worker_prompt.py`](../../kora_v2/memory/worker_prompt.py) | System prompt and I/O models for the Memory Worker agent |

---

## `store.py` — FilesystemMemoryStore

The canonical store. Every note is a `.md` file with a YAML block at the top:

```yaml
---
id: 018fa3b1c2d0-a4f2e1b0
memory_type: episodic
importance: 0.7
entities:
  - Sarah
  - Stripe
tags:
  - work
  - life_event
created_at: "2026-04-14T10:23:00+00:00"
updated_at: "2026-04-14T10:23:00+00:00"
---

I just started a new job at Stripe. Sarah said she was proud of me.
```

### `generate_note_id() -> str`

Generates sortable unique IDs with format `{13-hex-char-ms-timestamp}-{8-hex-random}`. Lexicographic sort order preserves creation time. Example: `018fa3b1c2d0-a4f2e1b0`.

### `NoteMetadata` (Pydantic model)

Fields: `id`, `memory_type`, `importance` (0.0–1.0), `entities: list[str]`, `tags: list[str]`, `created_at`, `updated_at`, `source_path`.

### `NoteContent` (Pydantic model)

Wraps `NoteMetadata` and adds `body: str` — the text after the frontmatter delimiter.

### `USER_MODEL_DOMAINS` (frozenset)

The 21 canonical subdomain names:
`identity`, `preferences`, `relationships`, `routines`, `health`, `work`, `education`, `finances`, `hobbies`, `goals`, `values`, `communication_style`, `emotional_patterns`, `triggers`, `strengths`, `challenges`, `medications`, `pets`, `living_situation`, `diet`, `adhd_profile`.

Any `write_note()` call with `memory_type="user_model"` and an unrecognized `domain` falls back to `_KoraMemory/User Model/` directly.

### `FilesystemMemoryStore`

```python
class FilesystemMemoryStore:
    def __init__(self, base_path: Path) -> None
    async def write_note(content, memory_type, domain, entities, tags, importance, note_id) -> NoteMetadata
    async def read_note(note_id) -> NoteContent | None
    async def update_note(note_id, content, updated_at) -> NoteMetadata | None
    async def list_notes(layer, domain) -> list[NoteMetadata]
    async def delete_note(note_id) -> bool
```

**Thread-safety note**: The docstring acknowledges that atomic write-then-rename is not implemented. For small files on a local filesystem, writes are effectively atomic, but concurrent writers must be serialized at a higher level (e.g., by `WritePipeline` holding a single async path).

**`_find_note_file(note_id)`** searches `Long-Term/` first (direct lookup `Long-Term/{note_id}.md`), then `User Model/` recursively via `rglob`. This means lookup is O(1) for long-term notes and O(n) for user-model notes when the domain is unknown.

**`list_notes(layer)`** calls `sorted(d.rglob("*.md"))` which relies on filename lexicographic order — since filenames begin with a millisecond-precision hex timestamp, this is equivalent to chronological order.

---

## `projection.py` — ProjectionDB

An async wrapper around `aiosqlite` that adds:
- WAL mode (`PRAGMA journal_mode=WAL`)
- Optional `sqlite-vec` extension for vector search
- Schema migrations via `MigrationRunner`

### Initialization

```python
db = await ProjectionDB.initialize(Path("data/projection.db"))
```

`initialize()` is the factory classmethod. It opens the database, attempts to load `sqlite-vec`, runs pending migrations, and returns a ready instance. If `sqlite-vec` is unavailable, the instance operates in FTS5-only mode (reported via `db.capabilities`).

**Important**: `pysqlite3` must be swapped into `sys.modules` before `aiosqlite` is imported. This is done in `kora_v2/__init__.py`, not in this file, because by the time this module loads, `aiosqlite.core` has already bound `import sqlite3`.

### Key methods

| Method | Description |
|---|---|
| `index_memory(...)` | INSERT into `memories` + `memories_vec` (if vector available) |
| `index_user_model_fact(...)` | INSERT into `user_model_facts` + `user_model_vec` |
| `update_memory_content(...)` | UPDATE content + delete/re-insert embedding |
| `update_user_model_fact(...)` | UPDATE content + confidence + delete/re-insert embedding |
| `delete_memory(memory_id)` | DELETE from `memories`, `memories_vec`, and `entity_links` |
| `get_memory_by_id(memory_id)` | SELECT by PK, returns `dict` |
| `get_fact_by_id(fact_id)` | SELECT by PK, returns `dict` |
| `find_or_create_entity(name, entity_type)` | Canonical name lookup with lowercased dedup |
| `link_entity(entity_id, memory_id, fact_id, relationship)` | Insert into `entity_links` |

### `serialize_float32(vec: list[float]) -> bytes`

Packs a Python list of floats to `struct.pack(f"{len(vec)}f", *vec)` — the binary format expected by `sqlite-vec`'s `vec0` virtual table.

---

## `migrations/001_projection_schema.sql` — Schema

### Tables

```sql
-- Long-term memories (from _KoraMemory/Long-Term/)
memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    summary TEXT,
    importance REAL DEFAULT 0.5,
    memory_type TEXT NOT NULL DEFAULT 'episodic',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    entities TEXT,          -- JSON array of entity names (not FTS-indexed)
    tags TEXT,              -- JSON array of tags (not FTS-indexed)
    source_path TEXT NOT NULL
)

-- User Model facts (from _KoraMemory/User Model/)
user_model_facts (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,        -- Bayesian: evidence/(evidence+contradiction+2)
    evidence_count INTEGER DEFAULT 1,
    contradiction_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_path TEXT NOT NULL
)

-- Named entities (people, places, medications, etc.)
entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    canonical_name TEXT NOT NULL,       -- lowercased + stripped
    entity_type TEXT NOT NULL,
    metadata TEXT
)

-- Many-to-many links: entity ↔ memory/fact
entity_links (
    entity_id TEXT REFERENCES entities(id),
    memory_id TEXT,
    user_model_fact_id TEXT,
    relationship TEXT,
    CHECK (memory_id IS NOT NULL OR user_model_fact_id IS NOT NULL)
)
```

### Virtual tables

```sql
-- FTS5 full-text indexes (content tables — shadow the base tables)
memories_fts USING fts5(
    content, summary,
    entities UNINDEXED, tags UNINDEXED,   -- excluded to prevent JSON syntax pollution
    content=memories, content_rowid=rowid
)
user_model_fts USING fts5(
    content, domain,
    content=user_model_facts, content_rowid=rowid
)

-- sqlite-vec vector tables (768-dim float32)
memories_vec USING vec0(embedding float[768])
user_model_vec USING vec0(embedding float[768])
```

**FTS5 triggers**: Three triggers per table (`memories_ai`, `memories_ad`, `memories_au`) keep the FTS5 shadow tables in sync automatically on INSERT, DELETE, and UPDATE. The `WritePipeline` does not need to manage FTS5 explicitly.

**entities and tags are UNINDEXED** in `memories_fts`. This is intentional: these fields contain JSON arrays like `["Sarah", "Stripe"]`, and BM25 would index the JSON syntax characters as tokens. The fields are still accessible via the FTS5 content-table join but excluded from tokenization.

---

## `embeddings.py` — LocalEmbeddingModel

All embeddings are generated locally using `sentence-transformers` with `nomic-ai/nomic-embed-text-v1.5`. No API calls are made; the ~270MB model is loaded on first use.

### Key design choices

- **768-dimensional** vectors (matches `vec0` virtual table schema)
- **Task-type prefixes** (nomic's asymmetric retrieval): documents stored with `"search_document: "` prefix, queries embedded with `"search_query: "` prefix
- **Lazy loading**: `load()` is called automatically on first `embed()` call; explicit `load()` lets callers control when GPU memory is allocated
- **Thread safety**: `model.encode()` is not thread-safe on MPS/CUDA. A `threading.Lock` (`_encode_lock`) serializes all encoding calls
- **Device priority**: MPS (Apple Silicon GPU) → CUDA (NVIDIA) → CPU
- **L2 normalization**: applied after encoding for consistent cosine similarity

### `LocalEmbeddingModel` API

```python
model = LocalEmbeddingModel(settings, device="auto")
model.load()                                   # explicit load (optional)
vec = model.embed(text, task_type="search_query")          # single, 768-dim list
vecs = model.embed_batch(texts, task_type="search_document", batch_size=64)
model.unload()                                 # free GPU memory
```

### Task type mapping

Gemini-style types (`RETRIEVAL_DOCUMENT`, `RETRIEVAL_QUERY`) are mapped to nomic types (`search_document`, `search_query`) for backwards compatibility via `GEMINI_TO_NOMIC_TASK_MAP`.

---

## `retrieval.py` — Hybrid Search

This module is the read engine. It provides three search functions and a final merge step.

### `vector_search(db, query_embedding, table, k) -> list[MemoryResult]`

Queries the `memories_vec` or `user_model_vec` virtual table using sqlite-vec's KNN syntax:
```sql
SELECT m.*, v.distance
FROM memories_vec v
INNER JOIN memories m ON m.rowid = v.rowid
WHERE v.embedding MATCH ? AND k = ?
ORDER BY v.distance
```
Distance is cosine distance. Score is converted to similarity: `similarity = 1.0 - distance`.

### `fts5_search(db, query, table, limit) -> list[MemoryResult]`

Queries FTS5 with BM25 ranking. Raw BM25 scores from SQLite are **negative** (more negative = more relevant). The function negates them to produce positive scores, then applies min-max normalization to 0–1 across the result set.

**FTS5 sanitization** (`_sanitize_fts5_query`): a defensive layer that:
1. Strips characters that break FTS5 syntax: `?`, `(`, `)`, `'`
2. Leaves already-quoted tokens untouched
3. Quotes FTS5 reserved operators: `OR`, `AND`, `NOT`, `NEAR`
4. Quotes any token containing non-alphanumeric characters (hyphens, colons, dots, slashes, Unicode) to prevent misparses as column specifiers or NOT operators

### `merge_and_rank(vec_results, fts_results, vec_weight=0.7, fts_weight=0.3) -> list[MemoryResult]`

Weighted merge:
1. Each source is min-max normalized independently
2. Combined score = `0.7 × vec_score + 0.3 × fts_score`
3. If one source is empty, the non-empty source gets effective weight 1.0 (no penalty for missing source)
4. Results are deduplicated by ID (FTS5 contribution added to existing vec score)

### `apply_time_weighting(results, decay_factor=0.1) -> list[MemoryResult]`

Applies exponential decay: `score *= exp(−decay_factor × days_old)`. Age is estimated by scanning `source_path` for an ISO date pattern (`YYYY-MM-DD`). If no date is found in the path, no decay is applied (the memory keeps its full score).

**Edge case**: This function reads dates from file paths, not from `created_at` metadata. The two are normally in sync but could diverge if notes are moved on disk.

### `hybrid_search(db, query, query_embedding, layer, memory_type, max_results) -> list[MemoryResult]`

The main entry point used by `recall()`:

```
┌─────────────────────────────────────────────────────────┐
│                    hybrid_search()                       │
│                                                         │
│  For each table in layer (memories / user_model_facts): │
│    ├─ vector_search(db, embedding, table)               │
│    ├─ fts5_search(db, query, table)                    │
│    └─ merge_and_rank(vec, fts)       vec:0.7, fts:0.3  │
│                                                         │
│  Concatenate results from all tables                    │
│  apply_time_weighting(all_results)                     │
│  Filter by memory_type (optional)                      │
│  Sort by score, slice to max_results                   │
└─────────────────────────────────────────────────────────┘
```

### `MemoryResult` (Pydantic model)

Fields: `id`, `content`, `summary`, `memory_type`, `importance`, `score` (0–1), `source` (`"long_term"` or `"user_model"`), `source_path`.

---

## `write_pipeline.py` — WritePipeline

The single orchestrated write path. All memory writes must go through this class to maintain consistency between filesystem and projection DB.

### `WriteResult` (Pydantic model)

Fields: `note_id`, `action` (`"created"`, `"merged"`, `"duplicate"`), `source_path`, `entities_extracted`, `message`.

### Entity extraction (`_extract_entities`)

Regex-based, no LLM. Three pattern categories:
- **People**: relationship words + capitalized name (e.g., "my wife Sarah", "boss John"), or "Name is my ..."
- **Locations**: movement patterns (e.g., "I moved to Austin", "based in London")
- **Medications**: named drug list (Adderall, Vyvanse, Lexapro, etc.)

Returns `list[tuple[str, str]]` — `(entity_name, entity_type)` pairs. Deduped by name.

### `WritePipeline.store()` — long-term memory

```
1. dedup_check() (skip if no LLM or skip_dedup=True)
   ├─ DUPLICATE → increment evidence, return early
   └─ MERGE     → _merge_memory(), re-embed, re-link entities

2. _extract_entities(content)

3. FilesystemMemoryStore.write_note()

4. LocalEmbeddingModel.embed(content, "search_document")

5. ProjectionDB.index_memory(...)
   └─ FTS5 triggers fire automatically

6. For each entity:
   find_or_create_entity() → link_entity(relationship="mentioned_in")
```

### `WritePipeline.store_user_model_fact()` — user model

Same pipeline as `store()`, but targets `user_model_facts` table and `User Model/{domain}/` filesystem path. For DUPLICATE hits, the `evidence_count` is incremented and confidence is recalculated: `new_confidence = new_evidence / (new_evidence + contradictions + 2)`.

### Merge helpers

`_merge_memory(existing_id, merged_content)`: Updates the filesystem note, re-embeds, updates projection DB, re-extracts and re-links entities. The existing note ID is preserved.

`_merge_fact(existing_id, merged_content, domain)`: Same as above but also bumps `evidence_count` and recalculates `confidence`.

---

## `dedup.py` — Deduplication

Prevents the memory store from accumulating redundant or near-identical notes.

### Architecture

```
new content
  │
  ├─ _fts5_candidate_search()
  │    OR-joined FTS5 query (tokenized content)
  │    BM25 negated + normalized, threshold 0.50
  │    Returns top-5 candidates
  │
  └─ For each candidate:
       LLM prompt: "EXISTING vs NEW — DUPLICATE, MERGE, or NEW?"
       Parse response for ACTION: and MERGED: lines
       ├─ DUPLICATE → DedupResult(action=DUPLICATE, existing_id=...)
       ├─ MERGE     → DedupResult(action=MERGE, existing_id=..., merged_content=...)
       └─ NEW       → (continue to next candidate)

If all candidates score NEW → DedupResult(action=NEW)
```

### `DedupAction` (StrEnum)

Values: `NEW`, `DUPLICATE`, `MERGE`.

### `DedupResult` (Pydantic model)

Fields: `action: DedupAction`, `existing_id: str | None`, `merged_content: str | None`.

### FTS5 sanitization in dedup

`_sanitize_fts5_query()` in `dedup.py` uses **OR semantics** (joins tokens with ` OR `) — unlike `retrieval.py`'s sanitizer which uses implicit AND. This is intentional: dedup wants to find any document sharing terms with the new content, not documents containing all terms.

### LLM prompt format

```
EXISTING: {existing_content}
NEW: {new_content}

ACTION: [DUPLICATE|MERGE|NEW]
MERGED: [combined text, only if MERGE]
```

**Edge case**: If the LLM call fails for a candidate, the exception is caught and logged, and the next candidate is tried. If all candidates fail, `DedupResult(action=NEW)` is returned — erring on the side of storing a potential duplicate rather than silently dropping content.

---

## `signal_scanner.py` — SignalScanner

A lightweight, stateless pre-filter that classifies each user message by memory relevance before any LLM work is done.

### Priority levels

| Priority | Meaning | Signal types |
|---|---|---|
| 1 (highest) | User corrections or contradictions | `CORRECTION`, `CONTRADICTION` |
| 2 | Life events, new people | `LIFE_EVENT`, `NEW_PERSON` |
| 3 | Preferences, facts, life management | `STRONG_PREFERENCE`, `EXPLICIT_FACT`, `MEDICATION`, `FINANCE`, `MEAL`, `TIME_BLOCK` |
| 4 | General substance (>50 chars, no specific signal) | `GENERAL` |
| 5 (lowest) | Low-signal (greetings, single-word responses) | (no signal types) |

### `SignalScanner.scan(user_message, assistant_response) -> ScanResult`

Currently only scans `user_message`; `assistant_response` is accepted for future use.

1. Early exit on empty string → priority 5
2. Low-signal pattern match (greetings, short acks) → priority 5
3. Check all pattern groups in order (correction, life_event, new_person, etc.)
4. Each pattern group uses `re.search()` across compiled patterns
5. **Negation check**: for life events, new persons, preferences, and facts — scans the 3 words before the match for negation words. This prevents "I don't love coffee" from being scored as `STRONG_PREFERENCE`.
6. Priority = minimum priority number among all detected signals

### Pattern coverage

- **CORRECTION**: "actually,", "I was wrong", "let me correct", "I meant", "that's not"
- **LIFE_EVENT**: engagement/marriage/divorce, job changes, relocation, graduation, diagnosis, breakup, pet adoption
- **NEW_PERSON**: relationship words + name, "met someone", "this person named X"
- **STRONG_PREFERENCE**: "I love/hate/adore/despise", "my favorite", "I always/never", "I prefer"
- **EXPLICIT_FACT**: "I am a ...", "I have ...", "I work/live/study at/in", "I'm from"
- **MEDICATION**: drug names, medication-taking patterns, refill/pharmacy/prescription
- **FINANCE**: dollar amounts, budget mentions, rent/bills/salary
- **MEAL**: eating patterns, meal types, hunger mentions
- **TIME_BLOCK**: focus/deep work patterns, distraction/procrastination, hyperfocus

**Performance**: Designed for <10ms. No I/O, no LLM, all regex.

---

## `worker_prompt.py` — Memory Worker

Defines the system prompt and I/O models for the Memory Worker agent — a specialist invoked by the supervisor for operations that require LLM reasoning.

### `MemoryWorkerInput` (Pydantic model)

Fields: `operation` (`"recall"`, `"store"`, `"update_fact"`), `content`, `layer` (`"all"`, `"long_term"`, `"user_model"`), `domain`, `memory_type`.

### `MemoryWorkerOutput` (Pydantic model)

Fields: `status` (`"success"`, `"duplicate"`, `"merged"`, `"error"`), `results`, `memory_id`, `entities_extracted`, `message`.

### Routing: simple recall vs Memory Worker

- **Simple recall** → `tools/recall.py` → `hybrid_search()` — no LLM, <500ms target
- **Complex recall** → Memory Worker — cross-referencing, temporal reasoning, synthesis
- **Storage with dedup** → Memory Worker → `WritePipeline` — entity extraction, classification

The system prompt instructs the Memory Worker on:
- Memory type classification (episodic / reflective / procedural)
- Importance scoring (0.8–1.0 for life events, 0.3–0.5 for casual mentions)
- Entity canonicalization ("my wife Sarah" → entity "Sarah", relationship "wife")
- Bayesian confidence for user model facts
- What NOT to store (meta-conversation, conversation mechanics)

---

## Integration points

**Called by:**
- `kora_v2/tools/recall.py` — calls `hybrid_search()` directly
- `kora_v2/agents/workers/memory.py` — uses `WritePipeline`, `hybrid_search`, `FilesystemMemoryStore`
- `kora_v2/core/di.py` — instantiates `FilesystemMemoryStore`, `ProjectionDB`, `LocalEmbeddingModel`, `WritePipeline`

**Calls:**
- `kora_v2/core/settings.py` — `MemorySettings` for model name, embedding dims
- `kora_v2/core/migrations.py` — `MigrationRunner` for SQL schema migration
- `sentence_transformers.SentenceTransformer` — external model loader
- `sqlite_vec` — optional C extension for vector search
- `aiosqlite` — async SQLite driver

---

## Memory events

`WritePipeline` and the projection layer emit three `EventType` values on the shared `EventEmitter`:

| Event | When it fires |
|-------|---------------|
| `MEMORY_STORED` | A new note is written to `_KoraMemory/` and projected into `projection.db`. Consumed by `proactive_pattern_scan` (wakes pattern-scan triggers) and the `post_session_memory` pipeline. |
| `MEMORY_SOFT_DELETED` | A note is tombstoned rather than physically deleted. The filesystem copy stays put; the projection row is flipped to `deleted=1`. |
| `ENTITY_MERGED` | Two entity rows are merged into one. Emits the surviving id + the merged-in id so downstream consumers can rebind references. |

These three live under the `# Memory` block of `kora_v2/core/events.py` alongside the older `MEMORY_STORED` event. See [core.md § core/events.py](../01-runtime-core/core.md#coreeventspy--eventemitter) for the full enum.

---

## Working documents — `_KoraMemory/Inbox/`

Long-running orchestration tasks (`LONG_BACKGROUND` preset, e.g. `user_autonomous_task`) write a per-instance working document to `_KoraMemory/Inbox/<task_id>.md`. These are intentionally stored alongside user memory rather than in the SQL layer so the supervisor can quote section contents back to the user in the turn response and so the user themselves can edit the file to steer an in-flight task.

Each document has:

- YAML frontmatter with `task_id`, `pipeline`, `created_at`, `last_updated_at`, `status`
- Free-form markdown sections the step function owns (e.g. `## Plan`, `## Progress`, `## Open Decisions`)
- A `status: done` sentinel written once the task reaches a terminal state — the supervisor tool `get_working_doc` uses this to decide whether to show a live or archived view

Writes are atomic (temp file + rename) and serialised through a per-instance `asyncio.Lock` held by the `WorkingDocStore` class in `kora_v2/runtime/orchestration/working_doc.py`. See [orchestration.md § Working documents](../01-runtime-core/orchestration.md#working-documents--_koramemoryinbox) for the full lifecycle.
