# Kora Architecture Atlas

A full, implementation-grounded map of Kora — every subsystem, every module, every non-obvious behavior — derived directly from `kora_v2/` source code (not from specs or legacy docs).

If you are new to this codebase, read [`overview.md`](overview.md) first. It gives you the mental model you need to read any of the cluster docs without context-switching.

## How this atlas is organized

Kora is a long-running local daemon (FastAPI + LangGraph + SQLite) with a Rich CLI front-end. The codebase factors into five clusters. Each cluster has its own folder with a `README.md` overview plus one deep-dive per module.

| # | Cluster | What lives here | Read when you want to understand… |
|---|---------|-----------------|-----------------------------------|
| 01 | [Runtime core](01-runtime-core/README.md) | `core/`, `daemon/`, `runtime/`, `runtime/orchestration/`, `graph/` | How a turn runs and how every non-conversation job is scheduled. The DI container, FastAPI server, WebSocket protocol, LangGraph supervisor, turn tracing, and the Phase 7.5 `OrchestrationEngine` that replaced `BackgroundWorker`. |
| 02 | [Memory & context](02-memory-context/README.md) | `memory/`, `context/`, `tools/` | How Kora remembers. Filesystem-canonical memory, projection DB, hybrid retrieval, compaction, the `recall()` fast path, all 23 registered tools. |
| 03 | [Agents & autonomous](03-agents-autonomous/README.md) | `agents/workers/`, `autonomous/`, `capabilities/`, `skills/`, `routing/` | How Kora *does* things. Worker harnesses (planner/executor/reviewer), multi-step autonomous plans, capability packs, YAML skills. |
| 04 | [Conversation & LLM](04-conversation-llm/README.md) | `llm/`, `emotion/`, `cli/`, `mcp/`, `quality/` | How Kora talks. LLM providers (MiniMax via Anthropic SDK, Claude Code delegate), two-tier PAD emotion, Rich CLI, MCP manager, quality sampling. |
| 05 | [Life engine & ADHD](05-life-adhd/README.md) | `life/`, `adhd/` | Why Kora is different. Routines, reminders, proactive surfacing, morning/crash/day bounds, trend detection, shame-free output rules. Phase 5 work. |

## Navigation index

### 01 — Runtime core
- [Cluster README](01-runtime-core/README.md) — request→turn→response flow, lifecycle, cross-subsystem map
- [core.md](01-runtime-core/core.md) — DI container, settings, `operational.db` schema, event bus, retry, logging
- [daemon.md](01-runtime-core/daemon.md) — FastAPI routes, WebSocket turn queue, launcher, lockfile, auth relay, session bridge, orchestration engine lifecycle
- [runtime.md](01-runtime-core/runtime.md) — `GraphTurnRunner`, compaction circuit breaker, inspector, artifact store, checkpointer lifecycle
- [orchestration.md](01-runtime-core/orchestration.md) — `OrchestrationEngine`, `WorkerTask` FSM, pipelines, triggers, dispatcher, `SystemState`, `RequestLimiter`, `NotificationGate`, working docs (Phase 7.5)
- [graph.md](01-runtime-core/graph.md) — LangGraph supervisor: full topology, every node, every edge, tool-footprint tracker, CJK filter, the 7 new orchestration supervisor tools

### 02 — Memory & context
- [Cluster README](02-memory-context/README.md) — filesystem vs projection DB, write/read flows, working vs long-term memory
- [memory.md](02-memory-context/memory.md) — `FilesystemMemoryStore`, `ProjectionDB` (FTS5 + vec0), hybrid retrieval algorithm, `WritePipeline`, `SignalScanner`
- [context.md](02-memory-context/context.md) — `WorkingMemoryLoader`, `ContextBudgetMonitor` (5-tier), 4-stage compaction pipeline, `ContextEngine`
- [tools.md](02-memory-context/tools.md) — registry, `@tool` decorator, auth levels, `recall()` fast path, all 23 tools, `DomainVerbResolver`

### 03 — Agents & autonomous
- [Cluster README](03-agents-autonomous/README.md) — worker vs capability vs skill distinction, turn-lifecycle fit
- [workers.md](03-agents-autonomous/workers.md) — planner, executor (with fast deterministic path), reviewer harnesses; dispatch contexts for in-turn and long-background `WorkerTask` presets
- [autonomous.md](03-agents-autonomous/autonomous.md) — the 12-node state machine now running inside a single `LONG_BACKGROUND` `WorkerTask`, the pipeline wrapper, step function, budget, open decisions, topic-overlap flow, and the legacy `autonomous_checkpoints` migration (Phase 7.5c)
- [capabilities.md](03-agents-autonomous/capabilities.md) — all 4 packs, base abstractions, policy system, registry
- [skills.md](03-agents-autonomous/skills.md) — YAML schema, loader, all 14 skill definitions, skills-vs-capabilities
- [routing.md](03-agents-autonomous/routing.md) — empty directory (aspirational); actual routing lives in `autonomous/graph.py` and the supervisor

### 04 — Conversation & LLM
- [Cluster README](04-conversation-llm/README.md) — provider abstraction → model calls → emotion → quality
- [llm.md](04-conversation-llm/llm.md) — `LLMProviderBase`, MiniMax (via `anthropic.AsyncAnthropic`), `ClaudeCodeDelegate` subprocess shim
- [emotion.md](04-conversation-llm/emotion.md) — two-tier PAD model, LLM assessor with LRU cache, 40%-timeout history
- [cli.md](04-conversation-llm/cli.md) — Rich CLI, WebSocket client, streaming display
- [mcp.md](04-conversation-llm/mcp.md) — MCP manager lifecycle, tool exposure, server config
- [quality.md](04-conversation-llm/quality.md) — quality measurement, sampling, stub areas

### 05 — Life engine & ADHD
- [Cluster README](05-life-adhd/README.md) — how life and adhd fit together, the Phase 5 narrative
- [life.md](05-life-adhd/life.md) — routines (stateful sessions), reminders, proactive surfacing, `ContextEngine` integration hub
- [adhd.md](05-life-adhd/adhd.md) — morning/crash/day bounds, trend detection, shame-free output rules, timezone fixes

## How this was produced

This atlas was built by reading every `.py` file in `kora_v2/`. Five Sonnet agents worked in parallel — one per cluster — each instructed to base every claim on real code with `file:line` references. No content was sourced from `Documentation/`, `README.md`, or legacy spec folders. When source disagrees with any legacy doc, **the atlas follows source**.

The initial pass was generated against the commit at the tip of `main` on 2026-04-14. Phase 7.5 (orchestration layer) updates were folded in on 2026-04-15 against branch `feature/phase-7-5-orchestration` at commits `d35e3a5` / `9a0d0d6`. Regenerate any cluster by re-running that cluster's agent prompt against the current HEAD.

## Ground rules for reading

- **Every `file.py:42` reference is a real line.** Click through.
- **"Stub" means stub.** When a doc says a feature is stubbed, the code really is empty or placeholder. Don't trust CLAUDE.md, trust the atlas.
- **Timezone-aware date logic matters.** Phase 5 fixed three timezone collapse bugs — see [adhd.md](05-life-adhd/adhd.md).
- **The `routing/` directory is empty.** Routing logic lives in the supervisor graph and the autonomous graph, not in `routing/`.
- **MiniMax speaks the Anthropic API.** The LLM provider uses `anthropic.AsyncAnthropic` pointed at `https://api.minimax.io/anthropic`.

## Top-level repo context

- Primary package: `kora_v2/` — everything else (`kora/`, `src/`, `Documentation/archive/`) is legacy or docs.
- Canonical memory: `_KoraMemory/` on the filesystem (gitignored).
- Runtime state: `data/operational.db` (27 tables) + per-session SQLite checkpointer.
- Entry point: `kora_v2.daemon.launcher:main` → `kora` console script.
- Stack: Python 3.11+, FastAPI, LangGraph, `anthropic` SDK, `sqlite-vec`, Rich.
