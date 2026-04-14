# Daemon Subsystem

`kora_v2/daemon/` — FastAPI server, launcher, lockfile, auth relay, session manager, and background worker.

The daemon is the long-running process that owns the conversation loop, background work, and session continuity. It exposes REST and WebSocket APIs over localhost, enforces single-instance semantics via an OS-level lockfile, and manages a two-tier background worker for memory consolidation and autonomous task delivery.

---

## Files

| File | Lines | Role |
|------|-------|------|
| `launcher.py` | ~689 | `kora` CLI entrypoint; daemon spawn, port discovery, client attachment |
| `server.py` | ~950 | FastAPI app; REST + WebSocket routes; per-connection turn queue |
| `lockfile.py` | ~342 | OS-level exclusive lock; `DaemonState` machine; PID tracking |
| `auth_relay.py` | ~384 | Three-layer MCP auth; WebSocket-based permission prompting |
| `session.py` | ~735 | Session lifecycle; bridge notes; emotion decay; turn history |
| `worker.py` | ~183 | Two-tier background worker; conversation-aware scheduling |
| `work_items.py` | ~234 | Five background task factories with priority and cooldown |
| `__init__.py` | ~0 | Package marker |

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
| GET | `/health` | None | Returns DaemonState + version + phase |
| GET | `/status` | Token | Runtime metrics, memory stats, phase info |
| POST | `/session/end` | Token | End current session, flush bridge |
| GET | `/session/info` | Token | Current session_id, turn_count, thread_id |
| POST | `/turns` | Token | Single-turn REST API (non-streaming) |
| GET | `/inspect/{topic}` | Token | RuntimeInspector dispatch |
| GET | `/autonomous/updates` | Token | Pending autonomous update summaries |
| POST | `/autonomous/updates/{id}/read` | Token | Mark update as read |
| DELETE | `/autonomous/stop` | Token | Cancel running autonomous task |

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

## worker.py — `kora_v2/daemon/worker.py`

Two-tier background worker. Runs low-priority maintenance tasks without blocking conversations.

### `BackgroundWorker` Class

```python
class BackgroundWorker:
    async def start(self) -> None:
        """Launch bg_safe_loop and bg_idle_loop asyncio tasks."""

    async def stop(self) -> None:
        """Cancel both tasks; await their completion."""

    async def _bg_safe_loop(self) -> None:
        """Safe tier: runs tasks during conversations (when _conversation_active=True)."""

    async def _bg_idle_loop(self) -> None:
        """Idle tier: runs tasks only between sessions."""
```

### Two-Tier Scheduling

| Tier | Condition | Use Case |
|------|-----------|----------|
| `safe` | Always (including mid-conversation) | Signal scanning, autonomous update delivery |
| `idle` | Only when `_conversation_active=False` | Memory consolidation, bridge pruning, skill refinement |

```python
async def _bg_safe_loop(self):
    while True:
        eligible = [t for t in self._tasks if t.tier == "safe" and t.is_due()]
        if eligible:
            task = min(eligible, key=lambda t: t.priority)
            await task.run()        # run exactly one per cycle
            break                   # break after first eligible
        await asyncio.sleep(5)

async def _bg_idle_loop(self):
    while True:
        if not self._conversation_active:
            # same pattern as safe loop but tier == "idle"
            ...
        await asyncio.sleep(30)
```

One item per cycle — the loops `break` after executing the first eligible task. This prevents a backlog of stale tasks from saturating the event loop.

### Conversation Tracking

```python
_conversation_active: bool = False

# Subscribed to EventEmitter:
on SESSION_START → _conversation_active = True
on SESSION_END   → _conversation_active = False
```

Subscriptions are set up in `create_app()` via `container.events.on(EventType.SESSION_START, ...)`.

---

## work_items.py — `kora_v2/daemon/work_items.py`

Factory functions that create `BackgroundTask` instances registered with `BackgroundWorker`.

### Task Registry

| Factory | Tier | Priority | Cooldown | Description |
|---------|------|----------|----------|-------------|
| `memory_consolidation()` | idle | 1 (highest) | 600s | Flush working memory to filesystem store |
| `signal_scanner()` | safe | 2 | 120s | Scan memory signals for pattern detection |
| `autonomous_update_delivery()` | safe | 3 | 30s | Deliver pending autonomous task summaries |
| `session_bridge_pruning()` | idle | 4 | 3600s | Delete bridge files older than retention window |
| `skill_refinement()` | idle | 5 (lowest) | 86400s | Nightly skill quality improvement pass |

### `BackgroundTask` Structure

```python
@dataclass
class BackgroundTask:
    name: str
    tier: Literal["safe", "idle"]
    priority: int               # Lower = higher priority
    cooldown_seconds: float
    run: Callable[[], Awaitable[None]]
    _last_run: datetime | None = None

    def is_due(self) -> bool:
        if self._last_run is None:
            return True
        return (datetime.now() - self._last_run).total_seconds() >= self.cooldown_seconds
```

### Registration Pattern

```python
# In create_app() (server.py):
worker = BackgroundWorker(container)
worker.register(memory_consolidation(container))
worker.register(signal_scanner(container))
worker.register(autonomous_update_delivery(container))
worker.register(session_bridge_pruning(container))
worker.register(skill_refinement(container))
await worker.start()
```

---

## Integration Points

### Inbound (daemon consumes)

| Source | What | How |
|--------|------|-----|
| `kora_v2/core/di.py` | `Container` with all services | Passed to `create_app()` and `_run_daemon()` |
| `kora_v2/graph/supervisor.py` | `build_supervisor_graph()` | Called once during `_run_daemon()`; result stored as `container.supervisor_graph` |
| `kora_v2/runtime/turn_runner.py` | `GraphTurnRunner` | Wraps `graph.ainvoke()` with tracing |
| `kora_v2/core/db.py` | `operational.db` | Auth grants, turn traces, autonomous updates |
| `kora_v2/core/settings.py` | `Settings` | Port, token, paths, trust_all flag |

### Outbound (daemon produces)

| Consumer | What | Transport |
|----------|------|-----------|
| `kora_v2/cli/app.py` | REST + WebSocket API | HTTP over 127.0.0.1:{port} |
| Lockfile readers | Port + state | `data/kora.lock` (JSON) |
| `_KoraMemory/System/bridges/` | Bridge notes | Filesystem (YAML + JSON sidecar) |
| `kora_v2/core/events.py` | `SESSION_START`, `SESSION_END` events | In-process `EventEmitter` |

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
- `skill_refinement` task factory (86400s cooldown) is registered but its `run()` implementation is a stub in Phase 5 (`work_items.py`)
