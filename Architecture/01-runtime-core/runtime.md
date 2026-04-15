# Runtime Subsystem

`kora_v2/runtime/` — Turn execution, LangGraph checkpointing, operator inspection, protocol metadata, and thin data stores.

The runtime layer sits between the daemon (which owns the HTTP surface and session lifecycle) and the supervisor graph (which owns LLM reasoning). It provides the execution contract for every conversation turn, durable conversation state persistence, and a structured control-plane interface for operator visibility — all without requiring LLM access.

Phase 7.5b added `kora_v2/runtime/orchestration/` alongside the existing runtime modules. That subpackage owns the `OrchestrationEngine` and the 20 core pipelines that replace the deleted `BackgroundWorker`; it is documented separately in [orchestration.md](orchestration.md) and is intentionally isolated from `turn_runner.py` / `inspector.py` so the turn-execution contract stays LLM-free.

---

## Files

| File | Lines | Role |
|------|-------|------|
| `turn_runner.py` | ~318 | `GraphTurnRunner` with tracing; `CompactionCircuitBreaker` |
| `inspector.py` | ~990 | `RuntimeInspector` with 8 topics; `doctor_report_lines()` |
| `stores.py` | ~217 | `ArtifactStore`, `AutonomousUpdateStore` over `operational.db` |
| `protocol.py` | ~160 | Shared identity constants; health/status payload builders |
| `checkpointer.py` | ~79 | `make_checkpointer()` / `close_checkpointer()` |
| `__main__.py` | ~131 | Offline inspector CLI (`python -m kora_v2.runtime <topic>`) |
| `__init__.py` | ~0 | Package marker |

---

## turn_runner.py — `kora_v2/runtime/turn_runner.py`

Wraps `graph.ainvoke()` / `graph.astream()` with structured turn tracing. Owns the `CompactionCircuitBreaker`.

### `CompactionCircuitBreaker`

```python
class CompactionCircuitBreaker:
    def __init__(self, max_compactions: int = 3) -> None: ...

    def check(self) -> bool:
        """Return True if compaction is still allowed."""

    def record_compaction(self) -> None:
        """Record one event; trip the breaker when count >= max_compactions."""

    def reset(self) -> None:
        """Reset count and tripped flag for a new session."""

    @property
    def count(self) -> int: ...

    @property
    def tripped(self) -> bool: ...
```

Default maximum is 3 compactions per session. `tripped` becomes `True` when `count >= max`. Once tripped, `check()` returns `False` — callers in `graph/supervisor.py` skip compaction when the breaker is tripped. `reset()` is called at session start, not at daemon start, so accumulated counts from one session do not carry into the next.

### `GraphTurnRunner`

```python
class GraphTurnRunner:
    def __init__(self, container: Any) -> None:
        """Stores container ref; opens db_path = settings.data_dir / 'operational.db'."""

    @property
    def circuit_breaker(self) -> CompactionCircuitBreaker: ...

    async def run_turn(self, graph: Any, graph_input: dict, config: dict) -> dict:
        """Execute one turn via graph.ainvoke(). Writes turn_traces on start and completion."""

    async def stream_turn(self, graph: Any, graph_input: dict, config: dict) -> AsyncIterator:
        """Execute one turn via graph.astream(). Yields events; writes trace in finally block."""

    async def record_trace_event(self, trace_id: str, event_type: str, payload: str | None) -> None:
        """Record a within-turn event to turn_trace_events table."""
```

### Tracing Contract

Every `run_turn()` or `stream_turn()` call:
1. Generates a 16-char hex `trace_id` (`uuid4().hex[:16]`)
2. Writes a `turn_traces` row at start (user_input truncated to 2000 chars)
3. On completion or exception, updates the same row with:
   - `completed_at`, `latency_ms`, `succeeded`
   - `tool_call_count`, `response_length`
   - `final_output` (first 2000 chars of response)
   - `tools_invoked` (JSON array of tool names)
   - `error_text` (first 1000 chars of exception if any)

All DB writes are best-effort: exceptions are logged as `WARNING` and never bubble to the caller.

### `stream_turn()` Aggregation

`stream_turn()` yields each event from `graph.astream()` as it arrives. It simultaneously collects `response_content` and `tool_call_records` from node outputs as they flow through. The aggregated trace is written in a `finally` block, so it is always written even if the generator is cancelled mid-stream.

### DB Path

Both methods open a fresh `aiosqlite.connect()` per write rather than holding a long-lived connection. This avoids WAL reader conflicts with the main `operational.db` connection held by `core/db.py`.

---

## inspector.py — `kora_v2/runtime/inspector.py`

Operator visibility into the running system. All inspection methods are async, JSON-safe, and read-only.

### `RuntimeInspector`

```python
class RuntimeInspector:
    def __init__(self, container: Container) -> None: ...

    async def inspect(self, topic: str, **kwargs: Any) -> dict[str, Any]:
        """Dispatch by topic name (normalized: strip, replace _ with -)."""

    async def inspect_setup(self) -> dict[str, Any]: ...
    async def inspect_tools(self) -> dict[str, Any]: ...
    async def inspect_workers(self) -> dict[str, Any]: ...
    async def inspect_permissions(self, limit: int = 20) -> dict[str, Any]: ...
    async def inspect_session(self) -> dict[str, Any]: ...
    async def inspect_trace(self, trace_id: str | None = None, limit: int = 10) -> dict[str, Any]: ...
    async def doctor(self) -> dict[str, Any]: ...
    async def phase_audit(self) -> dict[str, Any]: ...
```

### Topic Reference

| Topic | Returns |
|-------|---------|
| `setup` | Runtime version, LLM settings, memory settings, security settings, data paths |
| `tools` | Skill loader status, per-skill tool lists, MCP server states |
| `workers` | Planner/executor/reviewer init status, checkpointer class, auth relay status |
| `permissions` | Last N `permission_grants` rows with risk classification |
| `session` | Active session state + last 5 sessions from `operational.db` |
| `trace` | Last N `turn_traces` rows; or full trace + events for a specific `trace_id` |
| `doctor` | 11+ health checks; returns `{"healthy": bool, "checks": [...]}` |
| `phase-audit` | 10 Phase 4.67 acceptance criteria checked against live code |

Topic normalization: `topic.strip().replace("_", "-")` — so `phase_audit` and `phase-audit` both route to `phase_audit()`.

### `doctor()` Check Categories

The doctor method runs checks in sequence and accumulates a `checks` list:

| # | Category | Checks |
|---|----------|--------|
| 1 | Operational DB schema | Required tables present: `sessions`, `telemetry`, `turn_traces`, `turn_trace_events`, `permission_grants` |
| 2 | Token file | `data/.api_token` exists |
| 3 | Security | Daemon binds to `127.0.0.1`/`localhost`/`::1`; CORS not wildcard |
| 4 | Workers | Planner, executor, reviewer all initialized |
| 5 | Checkpointer | Checkpointer not None (reports class name; notes MemorySaver fallback) |
| 6 | Module imports | 5 critical modules importable: `runtime.kernel.RuntimeKernel`, `turn_runner.GraphTurnRunner`, `stores.SessionStore`, `agents.harness.SchemaRepairExhaustedError`, `emotion.fast_assessor.FrustrationSignal` |
| 7 | Dependencies | Python >= 3.12; pysqlite3 swap status; sentence_transformers (soft); sqlite_vec loadable |
| 8 | MCP servers | Per-configured-server state; tool discovery counts |
| 9 | Capability packs | `get_all_capabilities()` returns >= 4 packs; each passes `health_check()` |
| 10 | Agent browser | `agent-browser` binary on PATH or at `settings.browser.binary_path` |
| 11 | Vault path | If `settings.vault.enabled`, path exists and is writable |

### `phase_audit()` Criteria

10 Phase 4.67 acceptance criteria verified by inspecting live source and DB:

| Key | What is checked |
|-----|-----------------|
| `no_stubs` | `harness`, `server`, `session` modules free of "not yet wired"/"placeholder"/"simulated lifecycle" strings |
| `session_persist` | `SessionManager` source references `SessionStore` and `BridgeStore` |
| `turn_traces` | `turn_traces` table present in `operational.db` |
| `permission_persist` | `permission_grants` table present |
| `no_start_autonomous` | `start_autonomous` attribute absent from `server` module (retired in Slice 7.5a; replaced by `decompose_and_dispatch` — see [orchestration.md](orchestration.md) and [graph.md](graph.md)) |
| `typed_workers` | `ExecutorWorkerHarness` and `ReviewerWorkerHarness` lack plain-text fallback patterns |
| `ws_turn_runner` | `_handle_chat()` source references `GraphTurnRunner` or `stream_turn` |
| `sqlite_checkpointer` | Doctor's `sqlite_checkpointer` check passes |
| `idempotency_rules` | `kora_v2.agents.models` has `ActionRecord` and `SideEffectLevel` |
| `compaction_breaker` | `turn_runner` module has `CompactionCircuitBreaker` |

### Risk Classification

`_risk_level(tool_name)` classifies by substring match:
- **high**: `filesystem_write`, `shell_exec`, `process_kill`, `db_write`
- **medium**: `filesystem_read`, `web_fetch`, `code_run`
- **low**: everything else

Used to enrich permission grant records when `risk_level` is NULL in the DB.

### `doctor_report_lines(report)` — Free Function

```python
def doctor_report_lines(report: dict[str, Any]) -> list[str]:
    """Render doctor report as human-readable lines. ✓/✗ per check."""
```

Example output:
```
Doctor: 18/22 checks passed  [DEGRADED]
  ✓ operational_db_schema (tables=26)
  ✓ python_version_ok (3.12.3)
  ✗ capability_workspace (unimplemented — not yet implemented)
```

The status label is `OK` if `healthy=True`, else `DEGRADED`.

---

## stores.py — `kora_v2/runtime/stores.py`

Stateless repository wrappers over `operational.db`. Each store receives a live `aiosqlite.Connection` and owns one domain.

### `ArtifactStore`

```python
class ArtifactStore:
    def __init__(self, db: aiosqlite.Connection) -> None: ...

    async def record(
        self, *, item_id, artifact_id, artifact_type, uri,
        label=None, size_bytes=None, recorded_at=None
    ) -> None:
        """Insert into item_artifact_links. Silently skips if table missing."""

    async def list_for_plan(self, plan_id: str, *, limit: int = 50) -> list[dict]:
        """Return artifacts for a plan via JOIN through items table."""
```

`list_for_plan()` joins `item_artifact_links` through `items` because artifact links have no `plan_id` column — only `items` carries `autonomous_plan_id`:

```sql
SELECT ial.*
FROM item_artifact_links ial
JOIN items i ON i.id = ial.item_id
WHERE i.autonomous_plan_id = ?
ORDER BY ial.created_at DESC LIMIT ?
```

### `AutonomousUpdateStore`

```python
class AutonomousUpdateStore:
    def __init__(self, db: aiosqlite.Connection) -> None: ...

    async def record(
        self, *, session_id, plan_id, update_type, summary, payload=None
    ) -> None:
        """Insert into autonomous_updates with delivered=0."""

    async def get_undelivered(self, session_id: str) -> list[dict]:
        """Return undelivered updates for session, oldest first."""

    async def mark_delivered(self, session_id: str) -> None:
        """SET delivered=1 for all undelivered rows matching session_id."""
```

`update_type` values: `'checkpoint'` (mid-plan progress) or `'completion'` (plan finished). The `_unread_autonomous_updates` field in `SupervisorState` is populated from undelivered rows and injected into the dynamic suffix by `build_suffix` so the supervisor can proactively surface background progress to the user.

As of Slice 7.5b the `autonomous_updates` table is a legacy surface: writes still happen for back-compat so older CLI clients keep receiving checkpoint summaries, but the canonical source of truth for in-flight work is now the orchestration engine's `worker_tasks` + `pipeline_instances` pair. The dispatcher emits `TASK_CHECKPOINTED` / `TASK_COMPLETED` / `PIPELINE_COMPLETE` events, and the supervisor's `list_tasks()` tool (documented in [graph.md](graph.md)) reads from those tables directly. See [orchestration.md](orchestration.md#the-20-core-pipelines) for the `user_autonomous_task` pipeline that replaced the legacy loop.

Both stores are defensive against schema lag: `"no such table"` and `"no such column"` errors are caught and logged as `WARNING`, not raised. Any other exception propagates.

---

## protocol.py — `kora_v2/runtime/protocol.py`

Shared identity constants and payload builders for the runtime control plane. No external dependencies.

### Constants

```python
RUNTIME_NAME = "kora_v2"
API_VERSION  = __version__          # from kora_v2/__init__.py
PROTOCOL_VERSION = "1.0"
PROTOCOL_MAJOR = 1
STATUS_SCHEMA_VERSION = 1
```

### Capability Declarations

```python
SUPPORTED_CAPABILITIES: tuple[str, ...] = (
    "health", "status", "shutdown", "inspect", "ws_chat",
    "turn_streaming", "auth_relay", "session_tracking",
    "trace_persistence", "permission_persistence", "doctor", "phase_audit",
)

WS_MESSAGE_TYPES: tuple[str, ...] = (
    "token", "tool_start", "tool_result", "status",
    "auth_request", "question_request", "response_complete",
    "interrupt_ack", "error", "ping",
)
```

### Builder Functions

```python
def runtime_metadata() -> dict[str, Any]:
    """Return immutable server identity and capability metadata."""

def build_health_payload() -> dict[str, Any]:
    """Auth-free payload for GET /health."""

def build_status_payload(
    *, session_id, turn_count, session_active,
    active_sessions=0, extra=None
) -> dict[str, Any]:
    """Authenticated payload for GET /status."""

def extract_runtime_metadata(payload: Mapping) -> dict[str, Any]:
    """Pull runtime identity from status/health payload. Checks server_info sub-dict first."""

def is_compatible_runtime(payload: Mapping) -> tuple[bool, str]:
    """Validate protocol_major match. Returns (True, '') or (False, reason_str)."""
```

`is_compatible_runtime()` extracts `protocol_major` from either direct field or by splitting `protocol_version` on `"."`. Compatibility is determined solely by major version equality (`PROTOCOL_MAJOR == 1`).

`extract_runtime_metadata()` checks `payload["server_info"]` first, then falls through to `payload` directly, preferring already-found keys. This handles both nested and flat payload shapes.

---

## checkpointer.py — `kora_v2/runtime/checkpointer.py`

Two-function module for LangGraph conversation state persistence.

```python
async def make_checkpointer(db_path: Path) -> Any:
    """Create a checkpoint saver. Prefer AsyncSqliteSaver; fall back to MemorySaver."""

async def close_checkpointer(saver: Any) -> None:
    """Close checkpointer by calling saver._cm.__aexit__() if present."""
```

### Initialization Sequence

```
make_checkpointer(db_path)
  ├─ Import AsyncSqliteSaver (from langgraph.checkpoint.sqlite.aio)
  │    ├─ db_path.parent.mkdir(parents=True, exist_ok=True)
  │    ├─ cm = AsyncSqliteSaver.from_conn_string(str(db_path))
  │    ├─ saver = await cm.__aenter__()      # enter async context manager
  │    ├─ saver._cm = cm                     # stash for later cleanup
  │    ├─ await saver.setup()                # create checkpointer tables
  │    └─ return saver
  │
  ├─ ImportError → MemorySaver() fallback (warn + hint to install package)
  └─ Any other Exception → MemorySaver() fallback (warn with exc_info)
```

### Lifecycle Management

`AsyncSqliteSaver` is an async context manager. `make_checkpointer()` enters it manually (`__aenter__`) and stashes the `_cm` reference on the saver so `close_checkpointer()` can later call `__aexit__` — releasing the underlying `aiosqlite` connection.

```python
async def close_checkpointer(saver) -> None:
    cm = getattr(saver, "_cm", None)
    if cm is not None:
        await cm.__aexit__(None, None, None)
```

`MemorySaver` has no `_cm` attribute, so `close_checkpointer()` is a no-op for it.

### Thread Isolation

LangGraph uses `thread_id` as the checkpoint namespace. Each session gets a stable `thread_id` (persisted to `data/thread_id`). Greeting turns use a separate `{session_id}__greeting` thread to avoid polluting the main conversation checkpoint.

---

## `__main__.py` — Offline Inspector CLI

```
python -m kora_v2.runtime <topic> [--json]
```

Available topics: `setup`, `tools`, `workers`, `permissions`, `session`, `trace`, `doctor`, `phase-audit`.

### Offline Container

```python
def _build_offline_container():
    from kora_v2.core.di import Container
    from kora_v2.core.settings import get_settings
    return Container(get_settings())
```

A settings-only `Container` — no workers, MCP, memory, or LLM initialized. Worker/MCP fields report `initialized: false`. All read-only checks (DB files, settings sanity, module imports) run normally.

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Settings construction failed or inspector raised exception |
| 2 | `doctor` topic returned `healthy=False` |

The `--json` flag bypasses `doctor_report_lines()` and prints raw JSON for all topics.

### HTTP Alternative

When the daemon is running, the inspect endpoint is preferred:
```bash
curl -H "Authorization: Bearer $(cat data/.api_token)" \
     http://127.0.0.1:<port>/inspect/<topic>
```

---

## Integration Points

### Inbound (runtime consumes)

| Source | What | How |
|--------|------|-----|
| `kora_v2/core/di.py` | `Container` | Passed to `GraphTurnRunner`, `RuntimeInspector` |
| `kora_v2/graph/supervisor.py` | LangGraph `CompiledGraph` | `graph.ainvoke()` / `graph.astream()` called in turn runner |
| `kora_v2/core/db.py` | `operational.db` schema | Stores write to same DB; tables created by `db.py` |
| `kora_v2/core/settings.py` | `settings.data_dir` | DB path for turn traces and stores |

### Outbound (runtime produces)

| Consumer | What | Transport |
|----------|------|-----------|
| `kora_v2/daemon/server.py` | `GraphTurnRunner.run_turn()` | Direct call in `_handle_chat()` |
| `kora_v2/daemon/server.py` | `RuntimeInspector.inspect()` | HTTP `GET /inspect/{topic}` |
| `kora_v2/daemon/server.py` | `build_health_payload()`, `build_status_payload()` | HTTP `GET /health`, `GET /status` |
| `kora_v2/cli/app.py` | Protocol constants | Runtime compatibility check on connect |
| `operational.db` | `turn_traces`, `turn_trace_events`, `item_artifact_links`, `autonomous_updates` | aiosqlite writes |

---

## Data Flow: Turn Tracing

```
daemon/_handle_chat()
  └─ runner.run_turn(graph, input, config)
       ├─ _write_trace_start()  →  INSERT turn_traces (id, session_id, turn_number, ...)
       ├─ graph.ainvoke(input, config)
       │    └─ supervisor graph runs: receive → build_suffix → think → tool_loop → synthesize
       └─ _write_trace_complete()  →  UPDATE turn_traces SET completed_at, latency_ms, ...
```

Individual tool calls within a turn can also be written to `turn_trace_events` via `record_trace_event()`, though this is optional and callers are sparse in Phase 5.

---

## Known Constraints

- `runtime/kernel.py` (referenced in `inspector.py` module checks) is referenced as `kora_v2.runtime.kernel.RuntimeKernel` but no `kernel.py` file exists in the runtime package — the doctor module check for this import will fail gracefully (import error caught and logged as a failed check, not a crash)
- `stores.py` receives an `aiosqlite.Connection` at construction but the connection lifecycle is managed by the caller — there is no context manager on the stores themselves
- `stream_turn()` is defined but `server.py` uses `run_turn()` (not streaming) because MiniMax M2.7 does not support the LangGraph streaming event protocol
- The Phase 4.67 `phase_audit()` criteria set is static (hardcoded in `inspector.py`); adding new acceptance criteria requires editing this file
