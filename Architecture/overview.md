# Kora — Top-Level Architecture

Kora is a **local-first, ADHD-aware AI companion** that runs as a long-lived daemon on your machine. It remembers everything in a filesystem canonical store, orchestrates LLM-backed workers through a LangGraph supervisor, and proactively supports the user through a dedicated life/ADHD engine. Everything local: the memory, the projection DB, the emotion state, the tools. The only network calls are to the configured LLM provider (MiniMax by default, via an Anthropic-compatible endpoint) and optionally Claude Code for code delegation.

This document gives you the full mental model in one read. The [cluster docs](README.md) are where you go when you need depth on any single subsystem.

---

## The 30-second mental model

```
┌──────────────────────────────────────────────────────────────────────┐
│                          USER (Rich CLI)                             │
│                     kora_v2/cli/  (cluster 04)                       │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  WebSocket (localhost)
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    DAEMON  (FastAPI, per-user local)                 │
│                  kora_v2/daemon/  (cluster 01)                       │
│   launcher → lockfile → auth relay → session bridge → turn queue     │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  one turn at a time (per session)
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    RUNTIME  (turn runner + kernel)                   │
│                  kora_v2/runtime/  (cluster 01)                      │
│   GraphTurnRunner → checkpointer → RuntimeInspector → ArtifactStore  │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  invokes supervisor graph
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│              SUPERVISOR GRAPH  (LangGraph state machine)             │
│                   kora_v2/graph/  (cluster 01)                       │
│  receive → plan → act (tools) → synthesize → review → emit          │
└──────┬──────────────┬──────────────┬──────────────┬──────────────────┘
       │              │              │              │
       ▼              ▼              ▼              ▼
  ┌─────────┐   ┌──────────┐   ┌─────────┐   ┌─────────────┐
  │ workers │   │  memory  │   │  tools  │   │  life/adhd  │
  │  (03)   │   │   (02)   │   │  (02)   │   │    (05)     │
  │ planner │   │ fs store │   │ recall  │   │ ContextEng  │
  │executor │   │ proj DB  │   │ filesys │   │ morning/    │
  │reviewer │   │ hybrid   │   │ calendar│   │ crash/trend │
  └─────────┘   │ retrieval│   │ routines│   │ shame-free  │
                └──────────┘   └─────────┘   └─────────────┘
                      ▲              ▲              ▲
                      │              │              │
                      └──────────────┴──────────────┘
                                     │
                                     ▼
                            ┌──────────────────┐
                            │    LLM layer     │
                            │  llm/ (04)       │
                            │ MiniMax (anthropic SDK) │
                            │ Claude Code subprocess  │
                            └──────────────────┘
```

Every box above maps to a real folder. Every folder is documented in depth in its cluster.

---

## Lifecycle: from `kora` to a rendered reply

A pass through the whole system on one user message:

1. **User presses Enter** in the Rich CLI. See [cli.md](04-conversation-llm/cli.md).
2. CLI sends a WebSocket message to the local daemon, which is already running on a dynamic port that the launcher wrote into a per-user lockfile. See [daemon.md](01-runtime-core/daemon.md).
3. The daemon's **turn queue** serializes the request — only one turn per session at a time. The auth relay resolves the caller's `AuthContext` (three-tier policy system). The session bridge loads the session's YAML + sidecar state.
4. `GraphTurnRunner` claims the turn, attaches a 16-char trace ID, opens the SQLite checkpointer, and invokes the LangGraph supervisor. See [runtime.md](01-runtime-core/runtime.md).
5. The **supervisor graph** (`kora_v2/graph/supervisor.py`) runs:
   - **`_receive`** — seeds the state with user message, energy estimate, ambient emotion (PAD). Freezes the system-prefix for this turn, including ADHD output rules and the context prefix from `ContextEngine`. See [graph.md](01-runtime-core/graph.md).
   - **`_plan`** — LLM call that decides which tools to call (if any) or jumps straight to synthesis. Tool choices are gated by the enabled `skills/` YAML files and the `AuthContext`.
   - **`_act`** — executes one tool call. The tool registry dispatches to the concrete tool function. For `recall()`, this hits the memory subsystem's fast path. For capability-backed tools, this routes into a `Capability` pack with its policy layer. See [tools.md](02-memory-context/tools.md) and [capabilities.md](03-agents-autonomous/capabilities.md).
   - **`_synthesize`** — LLM call that turns tool results + working memory + context prefix into the final reply. Records a quality sample.
   - **`_review`** — reviewer worker post-processes for ADHD output-rule violations and tone. See [workers.md](03-agents-autonomous/workers.md).
   - **`_emit`** — CJK-safe streaming over the WebSocket back to the CLI.
6. `ContextBudgetMonitor` tracks the running token count. If the conversation breaches the compaction threshold, the 4-stage compaction pipeline runs (mask observations → structured LLM summary → UPDATE merge → heuristic hard-stop bridge). A **circuit breaker** caps this at 3 compactions per session. See [context.md](02-memory-context/context.md).
7. Any new facts the user stated are routed by the memory `WritePipeline` through a dedup check into the filesystem canonical store. The projection DB is updated incrementally (FTS5 + vec0 rows). See [memory.md](02-memory-context/memory.md).
8. The ADHD + life engine observes the turn ambiently — `ContextEngine` will read the updated DB on the next turn to compute the next `DayContext`. No explicit hook fires; the state propagates through shared SQLite tables. See [05-life-adhd](05-life-adhd/README.md).
9. Background workers run in their own tiered cooldowns (memory consolidation, emotion decay, proactive surfacing). See `BackgroundWorker` in [daemon.md](01-runtime-core/daemon.md).

---

## What makes Kora *different* from a generic LLM chat app

### 1. Filesystem-canonical memory + derived projection DB
Every memory Kora holds is a markdown file under `_KoraMemory/`. The projection database in SQLite exists solely as a fast index: `FTS5` for BM25 keyword search and `sqlite-vec` (`vec0`) for dense embeddings. Retrieval is a weighted sum (0.7 vector + 0.3 BM25) with min-max normalization — **no reranker**. The projection DB is maintained incrementally: if you edit a file by hand, there's no automatic reconciliation. Details in [memory.md](02-memory-context/memory.md).

### 2. ADHD as a cross-cutting concern, not a feature
`kora_v2/adhd/` is consumed by the supervisor every turn. It contributes:
- **Morning / crash / day-bounds detection** (timezone-aware after Phase 5 fixes — commit `dac6612` plumbed `user_tz` through `energy_signals`, `_starts_before`, `update_plan`, and `query_calendar`).
- **Shame-free output rules** — regex-level pattern detection for RSD triggers ("you forgot", "again" near failure words) injected into the supervisor's frozen prefix.
- **Trend detection** over multi-day aggregates.
- **Context shaping** — `ContextEngine` in `kora_v2/context/` is the integration hub that reads life-domain SQLite tables (`medication_log`, `focus_blocks`, `energy_log`, `routine_sessions`, `finance_log`, `calendar_entries`, `items`) and produces `DayContext` / `LifeContext`.

Routines are **stateful sessions**, not checklists. `RoutineManager` tracks partial completion across session boundaries with ADHD-aware progress messaging: *"You started. That's the hardest part."* at 10%. Details in [life.md](05-life-adhd/life.md) and [adhd.md](05-life-adhd/adhd.md).

### 3. Skills vs capabilities (a loaded distinction)
- **Skills** (`kora_v2/skills/`, 14 definitions) are **YAML files** that gate which tool names the LLM sees in its available-tools list. They're configuration.
- **Capabilities** (`kora_v2/capabilities/`, 4 packs, 24 files) are **Python packs** with policy enforcement and structured failure returns. They're code.
- A skill names a capability action as a string (e.g. `browser.open`); the capability pack actually runs it.
- Some skills (`obsidian_vault`, `screen_control`) are empty guidance containers with zero tools.
- `DoctorCapability` is a registered stub — shows up in health checks as `UNIMPLEMENTED`. Details in [capabilities.md](03-agents-autonomous/capabilities.md) and [skills.md](03-agents-autonomous/skills.md).

### 4. Autonomous execution has its own checkpoint format
Multi-step autonomous plans run through a **separate 12-node graph** in `kora_v2/autonomous/graph.py`, parallel to (not reusing) the runtime LangGraph checkpointer. Checkpoints serialize as JSON into the `plan_json` column of `autonomous_checkpoints` in `operational.db`. There's **no `session_id` column** in that table — session filtering is done in Python at query time, a known schema limitation. Details in [autonomous.md](03-agents-autonomous/autonomous.md).

### 5. Executor has a deterministic fast path
For exact task names `write_file` / `create_directory` with valid params, the executor **bypasses the LLM entirely** and does the filesystem operation directly in Python, then verifies the file exists on disk before claiming success. Details in [workers.md](03-agents-autonomous/workers.md).

### 6. LLM provider layer is thinner than it looks
- **MiniMax** is accessed via `anthropic.AsyncAnthropic` pointed at `https://api.minimax.io/anthropic`. The Anthropic SDK is doing all the transport work — MiniMax is Anthropic-API-compatible.
- **`ClaudeCodeDelegate` is not an LLM provider.** It does not subclass `LLMProviderBase`. It's a subprocess shim that shells out to the `claude` CLI binary for code delegation.
- So "LLM providers" is really one real provider (MiniMax) plus one subprocess shim. Details in [llm.md](04-conversation-llm/llm.md).

### 7. Emotion assessment has a documented timeout history
The two-tier PAD assessor has a 30-second timeout on LLM calls — raised from 15 seconds after a 2026-04-11 acceptance run observed ~40% of LLM emotion calls timing out on MiniMax cold starts. An LRU cache (32 entries, keyed on SHA-256 of the last 5 messages) bounds that cost. Details in [emotion.md](04-conversation-llm/emotion.md).

### 8. Context compaction has a circuit breaker
The 4-stage compaction pipeline is capped at 3 runs per session. If the runtime keeps hitting compaction, it stops trying rather than looping. The breaker is reset at session start. See `CompactionCircuitBreaker` in [runtime.md](01-runtime-core/runtime.md).

### 9. Turns are checkpointed per-node, not per-turn
The SQLite checkpointer stores state after every supervisor graph node, not once per turn. This makes inspector-driven debugging (resume-from-node, observe intermediate state) cheap, and supports the `_cm` stash pattern for checkpointer lifecycle. See [runtime.md](01-runtime-core/runtime.md).

### 10. MCP is wired but not self-healing
`MCPManager._restart_with_backoff` exists but `call_tool` does **not** call it on failure — the server simply raises `MCPServerUnavailableError` and recovery must be initiated externally by the runtime kernel. Details in [mcp.md](04-conversation-llm/mcp.md).

---

## Data stores at a glance

| Store | Path | Role | Schema lives in |
|-------|------|------|-----------------|
| Canonical memory | `_KoraMemory/**/*.md` | Source of truth for user memory | Filesystem (markdown) |
| Projection DB | `data/operational.db` tables `memory_*` | Fast retrieval index | `memory.projection_db` |
| Operational DB | `data/operational.db` (27 tables) | Life domain, routines, calendars, logs, budgets, autonomous checkpoints | `core/db.py` |
| Checkpointer DB | per-session SQLite | LangGraph state after every node | `runtime/checkpointer.py` |
| Session bridge | `data/sessions/<id>.yaml + .sidecar.json` | Session config + live state | `daemon/session_bridge.py` |
| Lockfile | per-user OS lock path | Daemon port discovery, zombie detection | `daemon/lockfile.py` |
| Logs | `data/logs/kora-YYYY-MM-DD.log` | Daily rotation, secret scrubbing, correlation IDs | `core/logging.py` |

---

## Key control-plane invariants

1. **One turn at a time per session.** The daemon's turn queue enforces this. See [daemon.md](01-runtime-core/daemon.md).
2. **Supervisor graph edges are static.** No runtime edge mutation; routing is expressed via node return values. See [graph.md](01-runtime-core/graph.md).
3. **All tools carry an `AuthLevel`.** The supervisor refuses tools whose level exceeds the caller's `AuthContext`. See [tools.md](02-memory-context/tools.md).
4. **The system-prefix is frozen at turn start.** ADHD output rules, context prefix, and skill list are snapshotted in `_receive` and cannot change mid-turn. See [graph.md](01-runtime-core/graph.md).
5. **All filesystem tool calls go through a path-safety check.** No tool can escape the project and memory roots. See [tools.md](02-memory-context/tools.md).
6. **Observations are masked before LLM compaction.** Compaction stage 1 strips raw tool observations to prevent the summary step from faithfully parroting sensitive content. See [context.md](02-memory-context/context.md).
7. **Turn tracing is best-effort.** Trace writes never block a turn. If the tracer fails, the turn still completes. See [runtime.md](01-runtime-core/runtime.md).

---

## Where to read next

- If you want to **understand a turn end-to-end**: [`01-runtime-core/README.md`](01-runtime-core/README.md) then [`graph.md`](01-runtime-core/graph.md).
- If you want to **understand how Kora remembers**: [`02-memory-context/README.md`](02-memory-context/README.md) then [`memory.md`](02-memory-context/memory.md).
- If you want to **understand what Kora can *do***: [`03-agents-autonomous/README.md`](03-agents-autonomous/README.md) then [`capabilities.md`](03-agents-autonomous/capabilities.md).
- If you want to **understand why Kora is ADHD-aware**: [`05-life-adhd/README.md`](05-life-adhd/README.md) then [`adhd.md`](05-life-adhd/adhd.md).
- If you want to **plug in a new LLM**: [`04-conversation-llm/llm.md`](04-conversation-llm/llm.md).
- If you want to **debug a turn**: [`01-runtime-core/runtime.md`](01-runtime-core/runtime.md) → `RuntimeInspector` section.

---

*Generated from live `kora_v2/` source on 2026-04-14. When the atlas disagrees with CLAUDE.md or `Documentation/`, the atlas is right — it was read from code.*
