# Daemon Subsystem

`kora_v2/daemon/` — FastAPI server, launcher, lockfile, auth relay, and session manager.

The daemon is the long-running process that owns the conversation loop and session continuity. It exposes REST and WebSocket APIs over localhost, enforces single-instance semantics via an OS-level lockfile, and — as of Phase 7.5b — constructs and runs the `OrchestrationEngine` that owns every piece of background and autonomous work. The legacy `BackgroundWorker` / `work_items.py` pair was deleted in that same slice.

---

## Files

| File | Lines | Role |
|------|-------|------|
| `launcher.py` | ~688 | `kora` CLI entrypoint; daemon spawn, port discovery, client attachment |
| `server.py` | ~1113 | FastAPI app; REST + WebSocket routes; orchestration engine lifecycle |
| `lockfile.py` | ~341 | OS-level exclusive lock; `DaemonState` machine; PID tracking |
| `auth_relay.py` | ~383 | Three-layer MCP auth; WebSocket-based permission prompting |
| `session.py` | ~734 | Session lifecycle; bridge notes; emotion decay; turn history |
| `__init__.py` | ~0 | Package marker |

Background work that used to live in `daemon/worker.py` + `daemon/work_items.py` is now owned entirely by `kora_v2/runtime/orchestration/` — see [orchestration.md](orchestration.md).

---

## launcher.py — `kora_v2/daemon/launcher.py`

The `kora` console entrypoint. Handles daemon spawn-or-attach, port discovery, and the interactive CLI loop.

### Entry Points

```python
def main() -> None:
    """kora console_script entrypoint (daemon.launcher:main)."""

async def ensure_daemon_running(settings: Settings) -> tuple[int, str]:
    """Start daemon if absent; return (port, token). Primary startup path."""

async def spawn_daemon(settings: Settings) -> None:
    """Fork a detached daemon process. Sets KORA_DAEMON=1, start_new_session=True on Unix."""

async def wait_for_ready(port: int, token: str, timeout: float = 90.0) -> bool:
    """Poll /health until READY or DEGRADED. 90s timeout covers embedding model load (15–40s)."""

async def _run_daemon(settings: Settings) -> None:
    """Full initialization sequence when running as the daemon process."""
```

### Startup Sequence

```
main()
  └─ asyncio.run(_run_daemon())
       ├─ ensure_daemon_running()
       │    ├─ lockfile.read() → if port/pid alive → return existing port
       │    ├─ spawn_daemon() → writes KORA_DAEMON=1, strips proxy env vars
       │    └─ wait_for_ready(90s) → polls GET /health
       └─ attach CLI (kora_v2/cli/app.py) with (port, token)
```

### Environment Handling

`spawn_daemon()` constructs a clean child environment:
- Strips `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` and lowercase variants to prevent proxy interference
- Sets `KORA_DAEMON=1` so `settings_customise_sources()` in `core/settings.py` can detect daemon context
- Uses `start_new_session=True` on Unix (POSIX session leader) so the child survives terminal close

### Key Behavior

- `wait_for_ready()` polls every 1s; accepts both `READY` and `DEGRADED` states as "online"
- If the lockfile shows a stale PID (process dead), `ensure_daemon_running()` cleans up and respawns
- Port `0` is used by uvicorn for OS-assigned port; the actual port is read from the lockfile after `startup()` completes

---

## server.py — `kora_v2/daemon/server.py`

The FastAPI application. Owns all HTTP and WebSocket routes, the per-connection turn queue, and session event subscriptions.

### Top-level Functions

```python
def create_app(container: Container, token: str) -> FastAPI:
    """Wire container, auth middleware, CORS, routes, background worker, event subscriptions."""

async def run_server(container: Container, token: str) -> None:
    """Split uvicorn into startup()/main_loop()/shutdown() for port-0 discovery."""
```

### REST Routes

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | None | Returns DaemonState + version |
| GET | `/status` | Token | Runtime metrics, memory stats, orchestration pipeline count |
| POST | `/daemon/shutdown` | Token | Graceful shutdown (flips `_server.should_exit`) |
| GET | `/orchestration/status` | Token | Pipelines registered, non-terminal task count, current `SystemStatePhase` |
| GET | `/inspect/autonomous` | Token | Live state of the `user_autonomous_task` instance owned by this session |
| GET | `/inspect/{topic}` | Token | Generic `RuntimeInspector` dispatch for non-autonomous topics |
| GET | `/memory/recall` | Token | Direct `recall()` passthrough for debugging |
| POST | `/compact` | Token | Force working-memory compaction |
| GET | `/permissions` | Token | List MCP permission grants |
| POST | `/auth-mode` | Token | Flip `trust_all` / per-scope auth policy |

### WebSocket Route

`GET /ws` — Primary conversation channel.

```
Client connects
  └─ _websocket_handler(ws, container, token)
       ├─ Auth: bearer token in query param or first message
       ├─ Per-connection state: busy_flag (bool), queued_messages (list)
       └─ Message loop:
            ├─ Type "chat" → _handle_chat() or queue if busy
            ├─ Type "auth_response" → auth_relay.receive_response()
            ├─ Type "ping" → send "pong"
            └─ Turn completion → drain queued_messages
```

### Turn Queue Protocol

`_websocket_handler()` maintains per-connection turn state:

```python
busy: bool = False          # True while a turn is running
queued_messages: list[dict] # Incoming messages buffered during busy
```

When a turn completes, the handler drains `queued_messages` one at a time, processing each through `_handle_chat()`. This prevents concurrent turns on a single connection without rejecting messages.

### `_handle_chat()` Internals

```python
async def _handle_chat(ws, message, container, session_mgr, token):
    """Run one conversation turn end-to-end."""
```

- Uses `graph.ainvoke()` (not `astream_events`) — MiniMax M2.7 does not support streaming event protocol
- Checks `_overlap_score`/`_overlap_action` from `_check_autonomous_overlap()` before invoking graph
- Sends `{"type": "thinking"}` immediately so CLI shows spinner
- On completion, sends `{"type": "response", "content": ...}` then `{"type": "turn_complete"}`
- Greeting is sent on a separate `thread_id` (format: `{session_id}__greeting`) to keep the checkpoint clean

### Session Greeting

```python
async def _send_greeting(ws, container, session_mgr):
    """Send session-open greeting on isolated thread_id."""
```

The greeting uses `graph.ainvoke()` with `thread_id=f"{session_id}__greeting"` so no greeting messages appear in the main conversation checkpoint that `session.py` reads for bridge construction.

### Port Discovery Pattern

```python
# run_server() splits uvicorn lifecycle:
config = uvicorn.Config(app, host="127.0.0.1", port=0, ...)
server = uvicorn.Server(config)
await server.startup()                     # binds socket, port is now known
actual_port = server.servers[0].sockets[0].getsockname()[1]
lockfile.update(port=actual_port)          # write port into lockfile
await server.main_loop()                   # serve requests
await server.shutdown()
```

This pattern allows `port=0` (OS-assigned) while still publishing the actual port to the lockfile before `wait_for_ready()` succeeds.

---

## lockfile.py — `kora_v2/daemon/lockfile.py`

OS-level exclusive file lock enforcing single-daemon semantics. Doubles as the port/state registry.

### `DaemonState` StrEnum

```python
class DaemonState(StrEnum):
    STARTING  = "STARTING"   # Lock acquired, initialization in progress
    READY     = "READY"      # All phases initialized; accepting requests
    DEGRADED  = "DEGRADED"   # Partial init (e.g. MCP failed); limited functionality
    ERROR     = "ERROR"      # Fatal initialization failure
    STOPPING  = "STOPPING"   # Shutdown in progress
```

### `Lockfile` Class

```python
class Lockfile:
    def acquire(self) -> bool:
        """Acquire exclusive OS lock. Writes STARTING state immediately."""

    def release(self) -> None:
        """Release lock and delete lockfile."""

    def update(self, *, state: DaemonState | None = None, port: int | None = None, ...) -> None:
        """Update fields in place. Uses os.lseek/ftruncate/write/fsync while holding fd."""

    def read(self) -> LockfilePayload | None:
        """Read lockfile without acquiring lock. Returns None if absent."""

    def is_alive(self) -> bool:
        """True if PID in lockfile is a running, non-zombie process."""
```

### Lock Mechanics

Platform-specific locking:
- **Unix**: `fcntl.flock(fd, LOCK_EX | LOCK_NB)` — fails immediately if another process holds it
- **Windows**: `msvcrt.locking(fd, LK_NBLCK, ...)` — equivalent non-blocking exclusive lock

`_write_payload()` updates the JSON payload atomically while holding the fd:
```python
os.lseek(fd, 0, 0)         # seek to start
os.ftruncate(fd, 0)        # clear existing content
os.write(fd, payload_bytes) # write new content
os.fsync(fd)               # flush to disk
```

### Zombie Detection

`pid_is_running()` uses `ps -o stat= -p {pid}` to check process status. A process is considered dead if:
- `ps` returns non-zero exit code (process absent)
- Status string starts with `Z` (zombie)

This prevents false positives where a PID exists but the process is defunct.

### Lockfile Payload

```python
@dataclass
class LockfilePayload:
    pid: int
    port: int | None         # None until server binds
    state: DaemonState
    started_at: str          # ISO timestamp
    version: str             # kora_v2 version string
```

---

## auth_relay.py — `kora_v2/daemon/auth_relay.py`

Three-layer MCP permission system. Translates MCP tool authorization requests into user-visible WebSocket prompts.

### Auth Policy Tiers

| Policy | Behavior |
|--------|----------|
| `ALWAYS_ALLOWED` | Approve silently, no user prompt |
| `ASK_FIRST` | Prompt user via WebSocket; 30s timeout → auto-deny |
| `ALWAYS_DENIED` | Reject immediately |

The active policy for any scope is resolved by `_resolve_policy()`:
1. Check explicit grants in `permission_grants` DB table
2. Check `settings.mcp.trust_all` flag → if True, ALWAYS_ALLOWED
3. Fall back to `CAPABILITY_POLICIES` dict (per-capability defaults)
4. Default: `ASK_FIRST`

### `AuthRelay` Class

```python
class AuthRelay:
    async def request_permission(
        self, capability: str, description: str, ws
    ) -> bool:
        """Legacy path (capability='legacy'). Deprecated but retained for compatibility."""

    async def request_permission_with_policy(
        self, scope: str, description: str, ws, tool_name: str = ""
    ) -> bool:
        """New path. Resolves policy before prompting. Used by execute_tool() in dispatch.py."""

    async def receive_response(self, approved: bool, scope: str) -> None:
        """Called by WebSocket handler when client sends auth_response message."""
```

### Permission Flow

```
execute_tool() calls request_permission_with_policy(scope, ...)
  ├─ Policy = ALWAYS_ALLOWED → return True immediately
  ├─ Policy = ALWAYS_DENIED  → return False immediately
  └─ Policy = ASK_FIRST:
       ├─ ws.send_json({"type": "auth_request", "scope": scope, "description": ...})
       ├─ Store scope in _last_scope[ws_id]
       ├─ asyncio.wait_for(response_event, timeout=30.0)
       │    └─ receive_response() sets event + result
       └─ Timeout → auto-deny, log warning
```

### DB Integration

Approved grants are persisted to `permission_grants` table in `operational.db`:
```sql
INSERT INTO permission_grants (scope, granted_at, expires_at)
VALUES (?, ?, ?)
```
Future calls with the same scope skip the WebSocket prompt entirely.

### Special Case: `dispatch_worker`

`_resolve_auth_context()` in `graph/dispatch.py` special-cases the `dispatch_worker` tool:
- Executor role → `ASK_FIRST` (has side effects)
- Planner/Reviewer roles → `ALWAYS_ALLOWED` (read-only planning)

---

## session.py — `kora_v2/daemon/session.py`

Session lifecycle management, bridge note construction, emotion state decay, and conversation history extraction.

### `SessionManager` Class

```python
class SessionManager:
    async def initialize(self) -> None:
        """Load persisted thread_id, session_id from data/. Apply emotion decay."""

    async def start_session(self) -> tuple[str, str]:
        """Return (session_id, thread_id). Create new session if none persisted."""

    async def end_session(self, graph_state: SupervisorState) -> SessionBridge:
        """Build bridge note from final state. Save to disk. Emit SESSION_END event."""

    async def get_turn_history(self, thread_id: str) -> list[dict]:
        """Extract last N messages from LangGraph checkpoint state."""

    def apply_emotion_decay(self, emotion: dict, hours_elapsed: float) -> dict:
        """Apply 20%/hr exponential decay of PAD dimensions toward neutral."""
```

### Persistence

Thread and session IDs survive daemon restart:
- `data/thread_id` — current LangGraph `thread_id`
- `data/session_id` — current session UUID

On `initialize()`, these files are read. If the saved PID matches a dead process, a new session is created.

### Bridge Note Format

Session bridges are YAML with optional JSON sidecar:

**Primary bridge** (`_KoraMemory/System/bridges/{session_id}.md`):
```yaml
---
session_id: uuid
ended_at: ISO timestamp
turn_count: N
energy_at_end: float
working_on:
  task: str
  progress: str
  next_step: str
---

[Markdown narrative of session]
```

**Sidecar JSON** (same path, `.json` extension):
```json
{
  "session_id": "...",
  "emotional_state": {...},
  "pending_items": [...],
  "errors": [...]
}
```

The sidecar stores structured data that doesn't belong in YAML frontmatter. `session_bridge.py` in `kora_v2/core/models.py` defines `SessionBridge` with the full schema.

### `_build_working_on()` — LLM-Free

```python
def _build_working_on(self, messages: list[dict], items: list[dict]) -> WorkingOnSnapshot:
    """Scan last 20 messages + item_state_history. No LLM call."""
```

Extracts "working on" context from:
1. Last 20 conversation messages (pattern-matched for task mentions)
2. `item_state_history` table rows for items transitioned in this session

Returns a `WorkingOnSnapshot` with `task`, `progress`, `next_step` fields.

### Emotion Decay

```python
def apply_emotion_decay(self, emotion: dict, hours_elapsed: float) -> dict:
    """PAD exponential decay: value *= 0.8 ** hours_elapsed."""
```

Applied at `initialize()` when loading persisted emotional state from the previous bridge. Each dimension (pleasure, arousal, dominance) decays 20% per hour toward 0.0 (neutral).

### Session ID Collision Guard

If `data/session_id` contains an ID already present in the bridge archive, `start_session()` generates a fresh UUID rather than continuing the old session. This prevents bridge overwrites when the daemon restarts quickly.

---

## OrchestrationEngine lifecycle — `run_server()` in `server.py`

Phase 7.5b deleted `BackgroundWorker` / `work_items.py` outright. The daemon no longer schedules anything on its own; instead, `run_server()` builds an `OrchestrationEngine` before the FastAPI app is created, registers the 20 core pipelines, and starts/stops the engine around the uvicorn loop.

```python
async def run_server(container, *, host, port, on_bind) -> None:
    # 1. Build the engine BEFORE create_app so /status can report counts.
    engine = await container.initialize_orchestration(
        websocket_broadcast=_broadcast_to_clients,
        session_active_fn=lambda: bool(_connected_clients),
    )

    # 2. Declare the 20 core pipelines (spec §4.3) on the engine.
    from kora_v2.runtime.orchestration.core_pipelines import (
        register_core_pipelines,
    )
    register_core_pipelines(engine)

    # 3. Create the FastAPI app — it reaches back into
    #    ``container.orchestration_engine`` through the module-level
    #    ``_orchestration_engine`` reference for /status and
    #    /orchestration/status.
    app = create_app(container)

    # 4. Start the engine (dispatcher loop + trigger evaluator).
    await engine.start()

    try:
        # ...uvicorn startup / main_loop / shutdown...
    finally:
        # 5. Stop the engine gracefully: cancel in-flight tasks, checkpoint
        #    RUNNING tasks to PAUSED_FOR_STATE, close the DB pool.
        await engine.stop(graceful=True)
```

The engine is constructed pre-`create_app()` so the `/status` and `/orchestration/status` REST endpoints can report `len(engine.pipelines.all())` without a null check. `engine.start()` is deferred until after uvicorn has everything it needs so tests that import `create_app` without running a server don't pay the dispatcher startup cost.

### WebSocket hooks passed to the engine

`initialize_orchestration()` receives two callbacks that let the dispatcher make conversation-aware decisions without importing `server.py`:

| Callback | Purpose |
|----------|---------|
| `websocket_broadcast` | `NotificationGate` calls this to push `WEBSOCKET` channel notifications to every connected CLI. It wraps `_broadcast_to_clients`, which iterates `_connected_clients` and tolerates disconnected sockets silently. |
| `session_active_fn` | Dispatcher checks this every tick to decide whether `CONVERSATION` phase is active. `bool(_connected_clients)` is a cheap proxy: any WebSocket connection means the user is (or might be) present. |

### Graceful shutdown

`engine.stop(graceful=True)` does three things in order:

1. Cancels the dispatcher's async task so no new work is picked up.
2. For each `RUNNING` / `PLANNING` / `CHECKPOINTING` WorkerTask: request a cooperative stop and, if the step function yields, transition the task to `PAUSED_FOR_STATE` with `reason="shutdown"`. On next boot, crash recovery treats those rows identically to a real crash.
3. Closes the orchestration DB pool.

The same graceful path runs on a crash if uvicorn itself throws — `run_server()` wraps the whole serve block in `try / finally`.

### The legacy work items, mapped to pipelines

The five `work_items.py` factories that existed before 7.5b have all moved into `kora_v2/runtime/orchestration/core_pipelines.py`:

| Old background task | New pipeline | Trigger |
|---------------------|--------------|---------|
| `memory_consolidation` | `post_session_memory` | `event(SESSION_END)` |
| `signal_scanner` | `proactive_pattern_scan` | `any_of(INSIGHT_AVAILABLE, EMOTION_SHIFT_DETECTED, MEMORY_STORED, interval(1800s, idle))` |
| `autonomous_update_delivery` | folded into `user_autonomous_task` step function | dispatched via `decompose_and_dispatch` |
| `session_bridge_pruning` | `session_bridge_pruning` (real step fn) | `interval(3600s, deep_idle)` |
| `skill_refinement` | `skill_refinement` (real step fn) | `time_of_day(3:00)` |

Only the two housekeeping items at the bottom have real step functions in Slice 7.5b; the rest carry stub steps that the Phase 8 slices will flesh out. See [orchestration.md § 20 core pipelines](orchestration.md#the-20-core-pipelines) for the full catalogue.

### `_check_autonomous_overlap()` — rerouted onto the engine

The one piece of background-aware logic still in `server.py` is `_check_autonomous_overlap()`, which runs on every incoming chat message and computes a topic-overlap score against any running `user_autonomous_task` / `user_routine_task` pipeline. Slice 7.5c rewrote it to look up the in-flight instance via `engine.task_registry.load_all_non_terminal()` + `engine.instance_registry.load()` instead of reaching into the deleted loop. It only caches the score on the container; the actual pause happens at the next dispatcher tick via `_autonomous_step_fn`'s `paused_for_overlap` branch.

---

## Integration Points

### Inbound (daemon consumes)

| Source | What | How |
|--------|------|-----|
| `kora_v2/core/di.py` | `Container` with all services | Passed to `create_app()` and `run_server()` |
| `kora_v2/graph/supervisor.py` | `build_supervisor_graph()` | Called once during `_run_daemon()`; result stored as `container.supervisor_graph` |
| `kora_v2/runtime/turn_runner.py` | `GraphTurnRunner` | Wraps `graph.ainvoke()` with tracing |
| `kora_v2/runtime/orchestration/engine.py` | `OrchestrationEngine` | Constructed by `container.initialize_orchestration()` before `create_app`; started in `run_server`, stopped in the finally block. Core pipelines registered via `register_core_pipelines(engine)`. |
| `kora_v2/core/db.py` | `operational.db` | Auth grants, turn traces, orchestration tables |
| `kora_v2/core/settings.py` | `Settings` | Port, token, paths, trust_all flag |

### Outbound (daemon produces)

| Consumer | What | Transport |
|----------|------|-----------|
| `kora_v2/cli/app.py` | REST + WebSocket API | HTTP over 127.0.0.1:{port} |
| Lockfile readers | Port + state | `data/kora.lock` (JSON) |
| `_KoraMemory/System/bridges/` | Bridge notes | Filesystem (YAML + JSON sidecar) |
| `kora_v2/core/events.py` | `SESSION_START`, `SESSION_END` events | In-process `EventEmitter` |
| `OrchestrationEngine` | `websocket_broadcast` + `session_active_fn` callbacks | Injected at `initialize_orchestration()` time so the `NotificationGate` can reach WebSocket clients and the dispatcher can tell whether a conversation is active. |

### WebSocket Message Types

**Client → Server:**
```json
{"type": "chat", "content": "user message", "session_id": "..."}
{"type": "auth_response", "approved": true, "scope": "mcp.tool_name"}
{"type": "ping"}
```

**Server → Client:**
```json
{"type": "thinking"}
{"type": "response", "content": "assistant message", "session_id": "..."}
{"type": "turn_complete", "session_id": "...", "turn_count": N}
{"type": "auth_request", "scope": "mcp.tool_name", "description": "..."}
{"type": "pong"}
{"type": "error", "message": "..."}
```

---

## Data Flow: Conversation Turn

```
CLI sends {"type": "chat", "content": "..."}
  │
  ▼
_websocket_handler()
  ├─ busy=True
  ├─ ws.send_json({"type": "thinking"})
  └─ _handle_chat()
       ├─ session_mgr.start_session() → (session_id, thread_id)
       ├─ _check_autonomous_overlap() → (_overlap_score, _overlap_action)
       ├─ graph.ainvoke(input, config={"thread_id": thread_id})
       │    └─ supervisor graph: receive → build_suffix → think → tool_loop → synthesize
       ├─ ws.send_json({"type": "response", "content": ...})
       ├─ ws.send_json({"type": "turn_complete", ...})
       └─ busy=False → drain queued_messages
```

---

## Known Constraints and TODOs

- `astream_events` is not used because MiniMax M2.7 does not support the LangGraph streaming event protocol; streaming is approximated by the `thinking` message sent before `graph.ainvoke()` returns (`server.py`)
- `request_permission()` in `auth_relay.py` is marked deprecated (legacy path); only `request_permission_with_policy()` is used by new callers
- `_build_working_on()` uses pattern matching rather than LLM extraction to keep session-end latency low — accuracy depends on consistent message phrasing
- Bridge sidecar JSON is written separately from the YAML; a crash between writes can leave an inconsistent pair — no reconciliation logic currently exists
- 17 of the 20 core pipelines registered by `register_core_pipelines()` still point at stub step functions; only `session_bridge_pruning`, `skill_refinement`, and `user_autonomous_task` have real bodies in Slice 7.5b (see [orchestration.md](orchestration.md))
