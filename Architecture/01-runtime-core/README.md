# Runtime Core — Architecture Cluster

This cluster documents the five subsystems that form Kora's runtime core: the dependency injection and shared utilities (`core/`), the FastAPI daemon process (`daemon/`), the turn execution and inspection layer (`runtime/`), the orchestration engine that owns every background and autonomous job (`runtime/orchestration/`), and the LangGraph conversation graph (`graph/`).

All documentation is derived from live source. File and line references are accurate as of commit `9a0d0d6` on branch `feature/phase-7-5-orchestration` — the slice that replaced `BackgroundWorker` with the `OrchestrationEngine`.

---

## Subsystem Map

```
kora_v2/
├── core/                      → Shared infrastructure: DI, settings, DB schema, events, models, logging
├── daemon/                    → Long-running process: HTTP/WS server, launcher, lockfile, auth, sessions
├── runtime/                   → Turn execution contract: GraphTurnRunner, inspector, protocol, stores
├── runtime/orchestration/     → OrchestrationEngine, WorkerTasks, Pipelines, Dispatcher, SystemState,
│                                RequestLimiter, NotificationGate, WorkingDocStore (Phase 7.5)
└── graph/                     → LangGraph graph: 5 nodes, tool dispatch, state, reducers, prompts
```

### Dependency Direction

```
daemon/                  ──uses──► graph/
daemon/                  ──uses──► runtime/
daemon/                  ──uses──► runtime/orchestration/  (constructs + starts + stops the engine)
graph/                   ──uses──► runtime/                (stores, protocol)
graph/                   ──uses──► runtime/orchestration/  (supervisor tools dispatch pipelines,
                                                            list_tasks reads the task registry)
runtime/orchestration/   ──uses──► core/                   (events, db, settings)
daemon/                  ──uses──► core/
graph/                   ──uses──► core/                   (errors, models, exceptions)
runtime/                 ──uses──► core/                   (settings, db)
```

All subsystems depend on `core/`. `daemon/` is the only subsystem that sees the others directly — it constructs the `OrchestrationEngine` in `run_server()` before `create_app()` and is responsible for starting and stopping it. `graph/` talks to the engine through `OrchestrationEngine`'s public methods only; there is no shared mutable state.

---

## Files in This Cluster

| File | Covers |
|------|--------|
| `core.md` | `kora_v2/core/` — DI container, settings, DB schema, events, models, logging, errors |
| `daemon.md` | `kora_v2/daemon/` — launcher, FastAPI server, lockfile, auth relay, session manager, orchestration engine lifecycle |
| `runtime.md` | `kora_v2/runtime/` — turn runner, checkpointer, inspector, protocol, stores, CLI |
| `orchestration.md` | `kora_v2/runtime/orchestration/` — engine, worker tasks, pipelines, dispatcher, system state, request limiter, notification gate, working docs (Phase 7.5) |
| `graph.md` | `kora_v2/graph/` — supervisor graph, dispatch, state, reducers, prompts, capability bridge |

---

## Request → Turn → Response

End-to-end path for a single conversation turn:

```
User types in kora_v2/cli/app.py
  │
  │  WebSocket {"type": "chat", "content": "..."}
  ▼
kora_v2/daemon/server.py  _websocket_handler()
  ├─ busy=True
  ├─ send {"type": "thinking"}
  └─ _handle_chat()
       ├─ session_mgr.start_session() → (session_id, thread_id)
       ├─ _check_autonomous_overlap() → (_overlap_score, _overlap_action)
       ├─ GraphTurnRunner.run_turn(graph, graph_input, config)
       │    ├─ _write_trace_start() → INSERT turn_traces
       │    └─ graph.ainvoke(graph_input, config={"configurable": {"thread_id": ...}})
       │         │
       │         ▼ LangGraph routes through nodes:
       │
       │    [receive]           increment turn_count, reset per-turn state,
       │                        seed emotion/energy on turn 1
       │         │
       │    [build_suffix]      cache frozen_prefix, build dynamic suffix,
       │                        rebuild DayContext, fetch unread updates,
       │                        run compaction if budget tier != NORMAL
       │         │
       │    [think]             ensure_tool_pair_integrity, call LLM,
       │                        set _pending_tool_calls if tool calls present
       │         │
       │    [should_continue]   tool calls? → tool_loop  else → synthesize
       │         │
       │    [tool_loop]         execute each tool (auth check first),
       │                        emit on_tool_event callbacks,
       │                        update topic tracker, clear _pending_tool_calls
       │         │
       │         └──► [think] again (up to 12 iterations)
       │         │
       │    [synthesize]        extract response_content,
       │                        strip CJK leaks,
       │                        record quality metrics
       │
       │    ◄── graph.ainvoke() returns SupervisorState
       ├─ _write_trace_complete() → UPDATE turn_traces
       │
       ├─ send {"type": "response", "content": response_content}
       ├─ send {"type": "turn_complete"}
       └─ busy=False → drain queued_messages
```

---

## Key Data Types

### `SupervisorState` (`graph/state.py`)

The LangGraph state TypedDict. All fields are optional (`total=False`). Key fields:

| Field | Type | Persistence | Description |
|-------|------|-------------|-------------|
| `messages` | `list[dict]` | Checkpoint | Full conversation history |
| `frozen_prefix` | `str` | Checkpoint | Cached system prompt prefix (built once) |
| `emotional_state` | `dict \| None` | Checkpoint | PAD-model emotion vector |
| `energy_estimate` | `dict \| None` | Checkpoint | Current energy level estimate |
| `day_context` | `dict \| None` | Per-turn | ADHD life engine ambient context |
| `turns_in_current_topic` | `int` | Checkpoint | Topic continuity counter |
| `hyperfocus_mode` | `bool` | Checkpoint | True when topic ≥ 3 turns and session ≥ 45 min |
| `_pending_tool_calls` | `list[dict]` | Per-turn | Tool calls from think; cleared by tool_loop |
| `compaction_tier` | `str` | Per-turn | "NORMAL", "PRUNE", etc. |

### `Container` (`core/di.py`)

The central dependency injection object. Holds all service instances. Phased initialization:

| Phase | What is initialized |
|-------|---------------------|
| Phase 1 | LLM provider, basic settings |
| Phase 2 | Memory subsystem (filesystem store, embedding model, projection DB) |
| Phase 3 | Worker harnesses (planner, executor, reviewer) |
| Phase 4 | MCP manager |
| Phase 4.67 | LangGraph checkpointer (SQLite-backed) |
| Phase 5 | ADHD life engine (ContextEngine, ADHDModule, routines, reminders) |

### `LockfilePayload` (`daemon/lockfile.py`)

Written to `data/kora.lock` by the daemon. Consumed by the launcher (`ensure_daemon_running()`) to attach to an already-running daemon or detect stale processes.

```json
{"pid": 12345, "port": 54321, "state": "READY", "started_at": "...", "version": "..."}
```

### `SessionBridge` (`core/models.py`)

Persisted at session end. Loaded at next session start. Contains:
- `working_on`: LLM-free snapshot of what was in progress (task, progress, next_step)
- `emotional_state`: final PAD vector (decays 20%/hr in `session.apply_emotion_decay()`)
- `energy_at_end`: energy level at session end

---

## Daemon Lifecycle

```
kora (CLI)
  └─ ensure_daemon_running()
       ├─ lockfile.read() → if alive, return existing port
       └─ spawn_daemon() → child process with KORA_DAEMON=1
            └─ _run_daemon()
                 ├─ Container.initialize_phase1() ... initialize_phase5()
                 ├─ build_supervisor_graph(container) → compiled graph
                 └─ run_server(container)
                      ├─ container.initialize_orchestration() → engine built
                      ├─ register_core_pipelines(engine) → 20 pipelines declared
                      ├─ create_app(container)
                      │    └─ EventEmitter subscriptions (SESSION_START/END, NOTIFICATION_SENT)
                      ├─ engine.start() → dispatcher + trigger evaluator running
                      ├─ server.startup() → binds port=0
                      ├─ lockfile.update(state=READY, port=actual_port)
                      ├─ server.main_loop() ← serving requests
                      ├─ server.shutdown()
                      └─ engine.stop(graceful=True) → paused RUNNING tasks, pool closed

launcher polls wait_for_ready(90s)
  └─ GET /health → DaemonState.READY
       └─ attach CLI WebSocket
```

---

## Conversation State Persistence

LangGraph checkpoints conversation state to `data/checkpoint.db` (SQLite, WAL mode) after every node execution. This means:

- Daemon crash mid-turn → the checkpoint holds state up to the last completed node
- Daemon restart → `thread_id` is read from `data/thread_id`; LangGraph resumes from the saved checkpoint
- MemorySaver fallback → state is lost on daemon restart (used when `langgraph-checkpoint-sqlite` is not installed)

The `frozen_prefix` field is cached in the checkpoint — it is only rebuilt on turn 1 of a new session thread.

---

## Background Work

Phase 7.5 replaced the two-tier `BackgroundWorker` with the `OrchestrationEngine` (`runtime/orchestration/engine.py`). A single dispatcher loop owns every background and autonomous job: memory consolidation, signal scanning, session-bridge pruning, skill refinement, proactive area scans, morning briefings, and user autonomous tasks.

Instead of tier-based cooldowns, each job is a declarative `Pipeline` with explicit triggers (8 kinds: `INTERVAL`, `EVENT`, `CONDITION`, `TIME_OF_DAY`, `SEQUENCE_COMPLETE`, `USER_ACTION`, `ANY_OF`, `ALL_OF`). The dispatcher:

1. Classifies the current `SystemStatePhase` (7 values: `CONVERSATION`, `ACTIVE_IDLE`, `LIGHT_IDLE`, `DEEP_IDLE`, `WAKE_UP_WINDOW`, `DND`, `SLEEPING`).
2. Evaluates every trigger; any pipeline whose predicate fires in the current phase is eligible.
3. Picks the highest-priority eligible pipeline that fits within the `RequestLimiter`'s 5-hour window (4500-request cap, 300 reserved for `CONVERSATION`, 100 reserved for `NOTIFICATION`).
4. Dispatches one `WorkerTask` per tick using one of three presets: `IN_TURN` (300s hard cap, conversation-class budget), `BOUNDED_BACKGROUND` (1800s, idle-only, background class), `LONG_BACKGROUND` (unbounded, pauses on conversation, pauses on topic overlap).

The 20 core pipelines are declared in `runtime/orchestration/core_pipelines.py`. Two have real step functions in Slice 7.5b — `session_bridge_pruning` (replaces the old idle task, `interval(3600s, deep_idle)`) and `skill_refinement` (replaces the old idle task, `time_of_day(3:00)`). The `user_autonomous_task` pipeline wraps the 12-node autonomous graph through a factory-built step function. The remaining 17 pipelines have stub steps that Phase 8 will flesh out.

See [orchestration.md](orchestration.md) for the full treatment (WorkerTask FSM, pipeline validation, dispatcher fairness, request limiter rules, notification gate, working docs).

---

## Auth Model

Three-layer permission system for tool execution:

```
execute_tool()
  └─ _resolve_auth_context() → (AuthLevel, risk)
       ├─ ALWAYS_ALLOWED → approve immediately (planner, reviewer, recall, web tools)
       ├─ NEVER → reject immediately
       └─ ASK_FIRST:
            ├─ trust_all=True → approve without prompting
            ├─ trust_all=False + relay available →
            │    ws.send_json({"type": "auth_request", ...})
            │    wait 30s for {"type": "auth_response", "approved": ...}
            │    timeout → auto-deny
            └─ no relay → deny (returns False)
```

Approved grants are stored in `permission_grants` table. Future calls with the same scope skip the prompt.

---

## Operator Inspection

The `RuntimeInspector` (`runtime/inspector.py`) provides structured health visibility without log reads.

**Via HTTP (daemon running):**
```bash
curl -H "Authorization: Bearer $(cat data/.api_token)" \
     http://127.0.0.1:<port>/inspect/doctor
```

**Via CLI (offline):**
```bash
python -m kora_v2.runtime doctor        # human-readable
python -m kora_v2.runtime doctor --json # raw JSON
python -m kora_v2.runtime phase-audit   # Phase 4.67 acceptance criteria
```

Exit codes: 0 = pass, 2 = doctor unhealthy.

---

## Critical Non-Obvious Behaviors

1. **Tool pair integrity is not a reducer.** `ensure_tool_pair_integrity()` is called in `think()` at LLM-send time. The reducer only does append; orphan cleanup happens just before the LLM call. (`graph/supervisor.py:314`)

2. **Iteration cap produces a question, not a bail string.** When `_MAX_TOOL_ITERATIONS=12` is hit, the supervisor calls `think()` again with `tools=[]` and `_ITERATION_CAP_CLARIFY_SUFFIX` instructing it to ask one focused question. (`graph/supervisor.py:1004`)

3. **Greeting uses an isolated thread_id.** `{session_id}__greeting` is used for the greeting turn so greeting messages never appear in the main conversation checkpoint that `session.py` reads for bridge construction. (`daemon/server.py`)

4. **Port 0 + lockfile.** The daemon binds to `port=0` (OS-assigned) in uvicorn's `startup()` phase, then writes the actual port to the lockfile before `main_loop()` begins. The launcher reads the port after `wait_for_ready()` succeeds. (`daemon/server.py`)

5. **Frozen prefix is cached by LangGraph checkpoint.** The first-turn cost of `build_frozen_prefix()` is paid once per `thread_id`. Subsequent turns skip it because `state["frozen_prefix"]` is non-empty in the checkpoint. (`graph/supervisor.py:129`)

6. **Compaction circuit breaker is per-session, not per-daemon.** `CompactionCircuitBreaker.reset()` is called at session start; `record_compaction()` trips after 3 compactions. A new session starts with a fresh counter. (`runtime/turn_runner.py:55`)

7. **KORA_DAEMON env collision fix.** `settings_customise_sources()` in `core/settings.py` detects the `KORA_DAEMON=1` environment variable and filters out non-dict daemon config values, preventing a type coercion error when the daemon child process reads settings. (`core/settings.py`)

8. **`_receive` runs fast emotion every turn; LLM emotion is gated.** `fast_emotion.assess()` runs synchronously on every user message. `llm_emotion_assessor.assess()` only fires when `should_trigger_llm_assessment()` returns True (based on signal strength and cooldown). (`graph/supervisor.py:931`)
