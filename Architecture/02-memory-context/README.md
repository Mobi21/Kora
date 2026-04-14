# Memory, Context, and Tools — Cluster Overview

Kora remembers things through three cooperating subsystems. The **memory** subsystem provides durable, searchable storage in a filesystem-canonical + projection-DB model. The **context** subsystem manages what fits inside a single conversation turn — working-memory composition, context-window budget tracking, and multi-stage compaction when that budget runs out. The **tools** subsystem exposes a typed, registry-driven surface so the LLM can invoke memory reads, life-management writes, calendar operations, and more, all through a uniform calling contract.

---

## Filesystem-canonical + projection DB model

Kora has two copies of every memory, and they are not equal:

- **`_KoraMemory/` (canonical)** — plain Markdown files with YAML frontmatter. These are the ground truth. If the projection DB were deleted tomorrow, it could be rebuilt from these files entirely.
- **`data/projection.db` (derived)** — a SQLite database that indexes the same content for fast search. It adds FTS5 full-text indexes and, when `sqlite-vec` is installed, 768-dimensional float32 vector embeddings for semantic search. Triggers keep FTS5 in sync automatically; vector embeddings are written manually during the write pipeline.

This means writes always go to the filesystem first, then to the projection DB. Reads always come from the projection DB (fast path) or — for the Memory Worker's full-content retrieval — directly from the filesystem.

---

## Directory layout

```
_KoraMemory/
├── Long-Term/
│   └── {note_id}.md          # episodic, reflective, procedural memories
└── User Model/
    ├── identity/
    ├── preferences/
    ├── relationships/
    ├── routines/
    ├── health/
    ├── work/
    ├── education/
    ├── finances/
    ├── hobbies/
    ├── goals/
    ├── values/
    ├── communication_style/
    ├── emotional_patterns/
    ├── triggers/
    ├── strengths/
    ├── challenges/
    ├── medications/
    ├── pets/
    ├── living_situation/
    ├── diet/
    └── adhd_profile/
```

User Model data is not informal preference notes — it is a structured 21-domain profile of the user, each domain stored as its own directory of Markdown files. This is the data Kora uses to personalize responses and energy-aware planning.

---

## End-to-end flow: a memory write

```
User says something memorable ("I just started a new job at Stripe")
  │
  ▼
SignalScanner.scan()                 <10ms, no LLM
  Detects LIFE_EVENT (priority 2)
  │
  ▼
WritePipeline.store() or store_user_model_fact()
  │
  ├─ 1. Dedup check (FTS5 candidate search → LLM judgment)
  │      If DUPLICATE → increment evidence count, return early
  │      If MERGE     → merge content, update existing note
  │      If NEW       → continue
  │
  ├─ 2. Regex entity extraction
  │      (person patterns, location patterns, medication patterns)
  │
  ├─ 3. FilesystemMemoryStore.write_note()
  │      Writes {note_id}.md to _KoraMemory/Long-Term/ or User Model/{domain}/
  │      YAML frontmatter: id, memory_type, importance, entities, tags, timestamps
  │
  ├─ 4. LocalEmbeddingModel.embed(content, task_type="search_document")
  │      nomic-ai/nomic-embed-text-v1.5, 768-dim, L2 normalized
  │      Device: MPS > CUDA > CPU, thread-safe via threading.Lock
  │
  ├─ 5. ProjectionDB.index_memory() or index_user_model_fact()
  │      INSERT into memories (or user_model_facts)
  │      INSERT into memories_vec (or user_model_vec) — sqlite-vec embedding
  │      FTS5 triggers fire automatically on INSERT
  │
  └─ 6. Entity linking
         find_or_create_entity() → link_entity() in entity_links table
```

---

## End-to-end flow: a memory read / recall()

`recall()` is the **fast path** used by workers during turns. It never invokes an LLM.

```
Worker calls recall(query="new job", layer="all", max_results=10)
  │
  ▼
tools/recall.py
  │
  ├─ LocalEmbeddingModel.embed(query, task_type="search_query")
  │   "search_query: " prefix applied (asymmetric retrieval)
  │
  └─ memory/retrieval.hybrid_search(db, query, query_embedding, layer="all")
       │
       ├─ For each table in [memories, user_model_facts]:
       │   ├─ vector_search()   — sqlite-vec KNN, cosine distance → similarity 1−d
       │   └─ fts5_search()     — BM25, negated + min-max normalized to 0-1
       │
       ├─ merge_and_rank()      — weighted sum: 0.7 × vec_score + 0.3 × fts_score
       │   (if one source is empty, effective weight becomes 1.0)
       │
       └─ apply_time_weighting()  — exp(−0.1 × days_old), date from source_path
            │
            └─ top max_results returned as JSON list
```

For complex recall requiring cross-referencing or synthesis, the supervisor dispatches the **Memory Worker** instead. The Memory Worker uses the same hybrid_search but reasons over results with an LLM before returning.

---

## Working memory vs. long-term memory

| Concept | Where it lives | Lifespan | Purpose |
|---|---|---|---|
| Long-term memory | `_KoraMemory/Long-Term/*.md` + `memories` table | Persistent | Episodic, reflective, procedural memories from conversations |
| User Model facts | `_KoraMemory/User Model/{domain}/*.md` + `user_model_facts` table | Persistent | Structured 21-domain profile with Bayesian confidence |
| Working memory items | In-memory `WorkingMemoryLoader` result, max 5 items | Per turn | Open threads from `SessionBridge`, items due within 48h |
| Conversation history | In-memory message list | Per session | Raw turns managed by compaction pipeline |

"Working memory" in this codebase refers to the small set of high-priority items the supervisor injects into each turn's system prompt suffix — not the full conversation history. The conversation history is a separate concern managed by `ContextBudgetMonitor` and the compaction pipeline.

---

## Where user model data lives

User Model data has two representations:

1. **Filesystem**: `_KoraMemory/User Model/{domain}/{note_id}.md` — each note is a Markdown file with YAML frontmatter. The `domain` subdirectory matches one of 21 canonical domain names (e.g. `identity`, `preferences`, `adhd_profile`).

2. **Projection DB**: the `user_model_facts` table, with fields for `domain`, `confidence`, `evidence_count`, and `contradiction_count`. Confidence is Bayesian: `evidence_count / (evidence_count + contradiction_count + 2)`, starting at ~0.33 and converging toward 1.0 as confirmations accumulate.

---

## Integration map

```
kora_v2/graph/supervisor.py
  └─ calls recall() (fast path)
  └─ dispatches Memory Worker (complex operations)

kora_v2/agents/workers/memory.py (Memory Worker)
  └─ uses WritePipeline, hybrid_search, FilesystemMemoryStore

kora_v2/context/engine.py (ContextEngine)
  └─ reads operational.db for DayContext / LifeContext
  └─ does NOT read projection.db or _KoraMemory/

kora_v2/runtime/turn_runner.py
  └─ calls ContextBudgetMonitor.get_tier()
  └─ calls run_compaction() when above PRUNE threshold

kora_v2/tools/__init__.py
  └─ registers all tools into ToolRegistry at import time
```

---

## Further reading

- [`memory.md`](memory.md) — full documentation of `kora_v2/memory/`
- [`context.md`](context.md) — full documentation of `kora_v2/context/`
- [`tools.md`](tools.md) — full documentation of `kora_v2/tools/`
