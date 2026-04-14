# `kora_v2/core/` — DI Container, Settings, DB, Events, Shared Models

The `core` package is Kora's shared infrastructure layer. It provides the typed dependency-injection container that wires every subsystem together, a Pydantic settings model loaded from TOML and environment variables, two SQLite database abstractions (the schema-initialisation layer and a migration runner), an async event bus, and all the Pydantic models that form the data contracts between the supervisor, workers, and tools.

Nothing in `core` imports from agents, graph, or life. Everything else imports from `core`.

---

## Files in this module

| Path | Purpose | Approx. lines |
|---|---|---|
| `core/__init__.py` | Empty package marker | ~1 |
| `core/di.py` | Typed DI container — wires LLM, event bus, graph, memory, workers | ~554 |
| `core/settings.py` | Pydantic settings — 13 nested sections, TOML + env loading | ~368 |
| `core/db.py` | `operational.db` schema DDL + additive migration helpers | ~557 |
| `core/migrations.py` | File-based SQL migration runner for `projection.db` | ~250 |
| `core/events.py` | Async event bus (`EventEmitter`) with typed `EventType` enum | ~141 |
| `core/models.py` | All shared Pydantic models: emotion, planning, workers, session | ~446 |
| `core/calendar_models.py` | Calendar-specific models (`CalendarEntry`, `CalendarTimelineSlot`) | ~73 |
| `core/exceptions.py` | Exception hierarchy (`KoraError` → `LLMError`, `MemoryError`, …) | ~51 |
| `core/errors.py` | `retry_with_backoff()` utility — exponential backoff for LLM calls | ~90 |
| `core/logging.py` | structlog configuration, secret scrubbing, correlation IDs | ~183 |
| `core/rsd_filter.py` | Rejection-sensitive dysphoria (RSD) output filter | ~68 |

---

## Per-file breakdown

### `core/di.py` — `Container`

[`core/di.py`](../../kora_v2/core/di.py)

The single central registry of all runtime services. Constructed by the launcher at daemon startup; passed by reference into every subsystem.

**Class: `Container`** [`di.py:34`](../../kora_v2/core/di.py#L34)

Initialised with a `Settings` instance. Fields are grouped by the phase they were added in (documented in the class docstring). Key fields:

| Field | Type | Set by |
|---|---|---|
| `settings` | `Settings` | `__init__` |
| `llm` | `MiniMaxProvider` | `__init__` (eager) |
| `event_emitter` | `EventEmitter` | `__init__` (eager) |
| `_supervisor_graph` | any or None | `supervisor_graph` property (lazy) |
| `embedding_model` | any or None | `initialize_memory()` |
| `projection_db` | any or None | `initialize_memory()` |
| `memory_store` | any or None | `initialize_memory()` |
| `write_pipeline` | any or None | `initialize_memory()` |
| `signal_scanner` | any or None | `initialize_memory()` |
| `_planner/executor/reviewer` | harnesses or None | `initialize_workers()` |
| `_mcp_manager` | `MCPManager` or None | `initialize_workers()` |
| `_skill_loader` | `SkillLoader` or None | `initialize_workers()` |
| `_verb_resolver` | `DomainVerbResolver` or None | `initialize_workers()` |
| `fast_emotion`, `llm_emotion` | assessors or None | `initialize_phase4()` |
| `quality_collector` | any or None | `initialize_phase4()` |
| `session_manager` | `SessionManager` or None | `initialize_phase4()` |
| `_checkpointer` | saver or None | `initialize_checkpointer()` (async) |
| `_routine_manager` | `RoutineManager` or None | `initialize_phase4()` |
| `_auth_relay` | `AuthRelay` or None | `create_app()` in server.py |
| `_autonomous_loops` | `dict[str, Any]` | runtime (autonomous tasks) |

**Initialization sequence** (called from `launcher._run_daemon()`):

```
Container.__init__(settings)           # eager: LLM, event bus
initialize_checkpointer()              # async, SQLite saver
initialize_memory()                    # async, embedding model, projection DB, store
initialize_workers()                   # sync, skill loader, workers, MCP, verb resolver
initialize_mcp()                       # async, starts MCP server subprocesses
initialize_phase4()                    # sync, emotion assessors, quality, session manager
```

**Key method: `initialize_checkpointer()`** [`di.py:110`](../../kora_v2/core/di.py#L110)
- Calls `make_checkpointer(db_path)` from `runtime/checkpointer.py`
- If the supervisor graph was already built (shouldn't happen), resets `_supervisor_graph = None` so the next access rebuilds with the SQLite backend
- Database: `data/operational.db`

**Key method: `initialize_workers()`** [`di.py:208`](../../kora_v2/core/di.py#L208)
- Side-effect: imports all tool modules so `@tool` decorators register themselves
- Emits `log.error("skill_loader_empty")` loudly when zero skills load — this prevents silent hallucination of tool calls
- Calls `_bind_capabilities()` to late-bind `settings` and `mcp_manager` into capability packs

**Key property: `supervisor_graph`** [`di.py:192`](../../kora_v2/core/di.py#L192)
- Lazy-built on first access via `build_supervisor_graph(self)`
- Subsequent accesses return the cached instance
- The ADHD life-engine properties (`adhd_profile`, `adhd_module`, `context_engine`, `calendar_sync`) are also lazy-built on first property access

**Key method: `close()`** [`di.py:492`](../../kora_v2/core/di.py#L492)
- Shutdown order matters: cancel autonomous tasks → flush checkpointer → close projection DB → unload embedding model
- Without `task.cancel()` + `asyncio.wait(..., timeout=2.0)` the process would hang on uvicorn exit

**`_bind_capabilities()`** [`di.py:277`](../../kora_v2/core/di.py#L277)
- Uses `inspect.signature` to determine whether a capability pack's `bind()` accepts keyword or positional args — supports both legacy and new pack styles
- All failures are swallowed at `debug` level; never crashes the daemon

---

### `core/settings.py` — `Settings`

[`core/settings.py`](../../kora_v2/core/settings.py)

All configuration is in one `BaseSettings` class with 13 nested `BaseModel` sections.

**Load priority (highest first):**
1. Environment variables (`KORA_*`, nested via `__` e.g. `KORA_LLM__MODEL`)
2. `~/.kora/settings.toml`
3. In-code defaults

**Nested sections:**

| Class | Env prefix | Key fields |
|---|---|---|
| `LLMSettings` | `KORA_LLM__` | `provider`, `model`, `api_base`, `api_key`, `max_tokens=16384`, `context_window=205000`, `temperature=0.7`, `timeout=120`, `retry_attempts=3` |
| `MemorySettings` | `KORA_MEMORY__` | `kora_memory_path`, `embedding_model`, `embedding_dims=768`, `hybrid_vector_weight=0.7`, `dedup_threshold=0.50` |
| `AgentSettings` | `KORA_AGENTS__` | `iteration_budget=150`, `default_timeout=300`, `loop_detection_threshold=3`, `reviewer_sampling_rate=0.1` |
| `QualitySettings` | `KORA_QUALITY__` | `confidence_threshold=0.6`, `regression_window_days=7` |
| `DaemonSettings` | `KORA_DAEMON__` | `host="127.0.0.1"`, `port=0` (auto-assign), `idle_check_interval=300`, `background_safe_interval=60` |
| `NotificationSettings` | `KORA_NOTIFICATIONS__` | `cooldown_minutes=15`, `max_per_hour=4`, `dnd_start/end` |
| `PlanningSettings` | `KORA_PLANNING__` | `cadence` (daily/weekly/monthly triggers) |
| `AutonomousSettings` | `KORA_AUTONOMOUS__` | `daily_cost_limit=5.0`, `per_session_cost_limit=1.0`, `max_session_hours=4.0`, `checkpoint_interval_minutes=30` |
| `MCPSettings` | `KORA_MCP__` | `servers: dict[str, MCPServerConfig]`, `startup_timeout=30` |
| `SecuritySettings` | `KORA_SECURITY__` | `api_token_path`, `cors_origins`, `injection_scan_enabled`, `auth_mode` (`"prompt"` or `"trust_all"`) |
| `VaultSettings` | `KORA_VAULT__` | `enabled`, `path` |
| `BrowserSettings` | `KORA_BROWSER__` | `enabled=False`, `binary_path`, `command_timeout_seconds=30` |
| `WorkspaceConfig` | via `_workspace_config_default()` | Loaded lazily from capabilities package |

**`LLMSettings._resolve_api_key()`** [`settings.py:67`](../../kora_v2/core/settings.py#L67)
- Tries `MINIMAX_API_KEY` env var, then `.env.local` / `.env` files if `api_key` is empty

**`Settings.expand_home_paths()`** [`settings.py:334`](../../kora_v2/core/settings.py#L334)
- Model validator that expands `~` in `kora_memory_path`, `api_token_path`, `vault.path`, `browser.binary_path`

**`Settings.data_dir`** [`settings.py:351`](../../kora_v2/core/settings.py#L351)
- Derived property: `Path("data")` — created on first access. All runtime data goes here.

**`get_settings()`** [`settings.py:361`](../../kora_v2/core/settings.py#L361)
- `@lru_cache(maxsize=1)` singleton; call `get_settings.cache_clear()` in tests

**Note on `KORA_DAEMON` env var collision:** The `settings_customise_sources` override [`settings.py:307`](../../kora_v2/core/settings.py#L307) filters out non-dict `daemon` values to prevent the OS environment variable `KORA_DAEMON=1` (set by the launcher) from corrupting the nested `DaemonSettings` object.

---

### `core/db.py` — Operational database schema

[`core/db.py`](../../kora_v2/core/db.py)

Manages `data/operational.db` — the runtime database for everything that is not long-term memory.

**`init_operational_db(db_path)`** [`db.py:517`](../../kora_v2/core/db.py#L517)
- Idempotent. Creates the directory, runs `CREATE TABLE IF NOT EXISTS` for all tables, then runs additive column migrations.
- Enables WAL mode on every connection.

**Tables created (complete list):**

| Table | Purpose |
|---|---|
| `sessions` | One row per session: start/end time, turn count, emotional states |
| `quality_metrics` | Per-turn numeric quality measurements |
| `quality_evaluations` | LLM-graded quality evaluations (sampled at 10%) |
| `autonomous_checkpoints` | Serialised checkpoint state for autonomous execution loops |
| `notifications` | Delivered notifications with acknowledgment tracking |
| `notification_engagement` | Per-category engagement stats for DND optimisation |
| `telemetry` | Per-turn: tokens, latency, tool calls, quality gate result |
| `audit_log` | Immutable action log |
| `turn_traces` | One row per turn: timing, tool calls, response, error |
| `turn_trace_events` | Streaming within-turn events linked to a turn_trace |
| `permission_grants` | Tool auth decisions: tool, scope, decision, risk level |
| `autonomous_plans` | Goal-level plans with cost/token budgets |
| `items` | Task items with hierarchy, status, energy, priority |
| `item_state_history` | Status-change audit trail for items |
| `item_artifact_links` | Links items to file/URL/data artifacts |
| `item_deps` | `blocks` / `depends_on` / `contains` edges between items |
| `routines` | Routine templates with steps JSON |
| `routine_sessions` | Partial completion state for routine runs |
| `autonomous_updates` | Unread background-loop summaries for foreground delivery |
| `reminders` | Scheduled reminders |
| `medication_log` | Medication taken events |
| `meal_log` | Meal logging |
| `focus_blocks` | Focus block start/end records |
| `quick_notes` | Unstructured notes |
| `calendar_entries` | Unified timeline (events, meds, focus blocks, reminders, deadlines) |
| `finance_log` | Financial entries with impulse flag |
| `energy_log` | Energy/focus check-in records |

**Additive column migration pattern** [`db.py:497`](../../kora_v2/core/db.py#L497)
- `_ensure_columns(db, table, migrations)` reads `PRAGMA table_info(table)`, skips already-existing columns, runs `ALTER TABLE ADD COLUMN` for new ones
- Migration tuples are defined as module-level constants (`_TURN_TRACE_MIGRATIONS`, `_PERMISSION_GRANT_MIGRATIONS`, etc.) — they never run destructive DDL

**`get_db(db_path)`** [`db.py:540`](../../kora_v2/core/db.py#L540)
- Opens a WAL-mode `aiosqlite.Connection` with `Row` factory; caller manages lifecycle

---

### `core/migrations.py` — `MigrationRunner`

[`core/migrations.py`](../../kora_v2/core/migrations.py)

File-based SQL migration runner used by the memory projection DB (not operational.db — that uses its own inline DDL + `_ensure_columns`).

**`MigrationRunner.run_migrations(db, migrations_dir)`** [`migrations.py:128`](../../kora_v2/core/migrations.py#L128)
- Discovers `NNN_*.sql` files in `migrations_dir`, compares against `schema_version` table
- Runs each statement individually (handles triggers with `BEGIN…END` blocks via `_split_sql_statements`)
- Statements that touch `USING vec0` (sqlite-vec virtual tables) are allowed to fail gracefully — the migration is still marked applied so it doesn't re-run. This handles the case where `sqlite-vec` is not installed.
- Each migration is committed atomically after all its statements succeed

---

### `core/events.py` — `EventEmitter`

[`core/events.py`](../../kora_v2/core/events.py)

A lightweight in-process pub/sub bus. One instance lives on the container (`container.event_emitter`).

**`EventType` enum** [`events.py:25`](../../kora_v2/core/events.py#L25)

| Value | Meaning |
|---|---|
| `SESSION_START / SESSION_END` | Session lifecycle |
| `TURN_START / TURN_END` | Turn lifecycle |
| `WORKER_DISPATCHED / WORKER_COMPLETED / WORKER_FAILED` | Worker harness lifecycle |
| `TOOL_CALLED / TOOL_RESULT` | Tool execution |
| `MEMORY_STORED` | Memory write pipeline |
| `QUALITY_GATE_RESULT` | Quality collector output |
| `NOTIFICATION_SENT` | Notification delivery (triggers WS broadcast) |
| `AUTONOMOUS_CHECKPOINT / AUTONOMOUS_COMPLETE / AUTONOMOUS_FAILED` | Autonomous loop milestones |
| `ERROR_OCCURRED` | General error reporting |

**`EventEmitter.emit(event_type, **data)`** [`events.py:100`](../../kora_v2/core/events.py#L100)
- Adds `event_type`, `timestamp` (ISO UTC), and `correlation_id` to every payload
- Iterates handlers sequentially (no parallel fan-out)
- Swallows handler exceptions (logs at `exception` level); re-raises `asyncio.CancelledError`

**`EventEmitter.on/off/clear()`** — registration and test teardown

---

### `core/logging.py` — Structured logging

[`core/logging.py`](../../kora_v2/core/logging.py)

**`setup_logging(log_dir, level, console)`** [`logging.py:88`](../../kora_v2/core/logging.py#L88)
- Configures both structlog and stdlib logging in one call
- Log file: `~/.kora/logs/kora.log`, daily rotation, 7-day retention, UTF-8
- Pipeline: `merge_contextvars → add_logger_name → add_log_level → TimeStamper → _add_correlation_id → _scrub_secrets → PositionalArgumentsFormatter → StackInfoRenderer → UnicodeDecoder → JSONRenderer`

**`_scrub_secrets()`** [`logging.py:62`](../../kora_v2/core/logging.py#L62)
- Recursively scrubs dicts, lists, strings
- Patterns: `sk-ant-*` (Anthropic), `sk-cp-*` (MiniMax CP), `sk-*` (generic), `Bearer …`, `api_key=…`, `authorization=…`

**Correlation ID** [`logging.py:21`](../../kora_v2/core/logging.py#L21)
- `contextvars.ContextVar` bound per-request via `new_correlation_id()`; read by the event emitter and injected into every log entry

---

### `core/models.py` — Shared Pydantic models

[`core/models.py`](../../kora_v2/core/models.py)

All data contracts between graph, workers, and tools. Key models:

**Emotion / energy:**
- `EmotionalState` — PAD (valence/arousal/dominance) + mood label + confidence + source (`fast`, `llm`, `loaded`)
- `EnergyEstimate` — level (`low/medium/high`), focus (`scattered/normal/moderate/locked_in`), signals list or dict, `is_guess` flag

**Planning:**
- `PlanConstraints`, `ExecutionConstraints` — budget and auth level limits
- `PlanStep` — one step: id, worker, tools_needed, energy_level, depends_on
- `Plan` — ordered steps + ADHD notes

**Worker I/O (strictly typed contracts):**
- `PlanInput/PlanOutput` — planner's wire format
- `ExecutionInput/ExecutionOutput` — executor's wire format; `params: dict` preserves side-effect args from the supervisor
- `ReviewInput/ReviewOutput` — reviewer's wire format
- `_coerce_to_list()` [`models.py:16`](../../kora_v2/core/models.py#L16) — validator that coerces JSON-encoded list strings to real lists (for LLM output repair)

**Session:**
- `SessionState` — in-memory session snapshot: session_id, turn_count, emotional_state, energy_estimate, pending_items
- `SessionBridge` — cross-session continuity note: summary, open_threads, `working_on: WorkingOnSnapshot`, `energy_at_end`
- `WorkingOnSnapshot` — deterministic extraction: last tools used, items touched, last user message snippet
- `DayPlanSnapshot` / `DayPlanItemSnapshot` — sidecar JSON for day plan at session end

**Context engine:**
- `DayContext` — aggregated view of the current day: schedule, medication status, meals, focus blocks, routines, energy, items due, hyperfocus mode
- `LifeContext` — multi-day aggregate for planning tools
- `MedicationStatus`, `FocusBlockStatus`, `RoutineStatus` — component views within DayContext

**Quality:**
- `QualityTurnMetrics`, `QualityGateResult`, `CompactionResult`
- `ADHDScanResult`, `ADHDViolation` — output of the RSD filter

---

### `core/calendar_models.py`

[`core/calendar_models.py`](../../kora_v2/core/calendar_models.py)

Lightweight calendar models separate from the main models file to avoid circular imports:
- `CalendarEntry` — unified timeline row with kind (`event`, `medication_window`, `focus_block`, `routine_window`, `buffer`, `reminder`, `deadline`), source (`google`, `kora`, `user`), energy_match, recurring_rule, override fields for exception handling
- `CalendarTimelineSlot` — rendered slot with conflict and buffer metadata

---

### `core/exceptions.py`

[`core/exceptions.py`](../../kora_v2/core/exceptions.py)

```
KoraError
├── LLMError
│   ├── LLMConnectionError   ← retried by retry_with_backoff
│   ├── LLMGenerationError   ← not retried (bad request / rate limit)
│   └── LLMTimeoutError      ← retried by retry_with_backoff
├── MemoryError
└── PlanningFailedError      ← raised after planner exhausts retries
```

---

### `core/errors.py` — `retry_with_backoff()`

[`core/errors.py`](../../kora_v2/core/errors.py)

**`retry_with_backoff(fn, *args, max_retries=3, base_delay=1.0, retryable_exceptions=(LLMConnectionError, LLMTimeoutError))`** [`errors.py:23`](../../kora_v2/core/errors.py#L23)
- Delay formula: `base_delay * 2^attempt` → 1s, 2s, 4s
- Only `LLMConnectionError` and `LLMTimeoutError` are retried by default
- `LLMGenerationError` propagates immediately (bad request or rate-limit — retrying won't help)
- Used in `graph/supervisor.py:think()` to wrap `llm.generate_with_tools()`

---

### `core/rsd_filter.py`

[`core/rsd_filter.py`](../../kora_v2/core/rsd_filter.py)

**`check_output(text, rules)`** — async function, returns `RSDFilterResult`
- Compiles each `OutputRule.pattern` as a case-insensitive regex
- Returns `passed=False` with `violations` list if any rule matches
- `rewritten` is always `None` in Phase 5; automatic rewrite is deferred to Phase 8

---

## Integration points

| Called by | Calls into |
|---|---|
| `daemon/launcher.py` | `settings.get_settings()`, `Container.__init__()`, all `initialize_*()` methods |
| `daemon/server.py` | `Container` (via module-level `_container`), `EventType` |
| `graph/supervisor.py` | `Container.llm`, `Container.event_emitter`, `retry_with_backoff` |
| `graph/dispatch.py` | `Container.session_manager`, `Container._auth_relay` |
| `runtime/checkpointer.py` | Called by `Container.initialize_checkpointer()` |
| `daemon/session.py` | `SessionState`, `SessionBridge`, `EmotionalState`, `EventType.SESSION_START/END` |
| All workers | `PlanInput/Output`, `ExecutionInput/Output`, `ReviewInput/Output` |
| All tool modules | `db.get_db()`, `Settings.data_dir` |
