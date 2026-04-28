# Kora Architecture Atlas

A public, implementation-grounded map of Kora. The current source-backed snapshot is [`current-architecture.md`](current-architecture.md); older cluster deep dives remain useful for subsystem detail, but `current-architecture.md` wins when a page still contains Phase 7.5-era wording.

If you are new to this codebase, read [`current-architecture.md`](current-architecture.md) first. It gives you the current mental model, acceptance caveats, and verified counts before you branch into the older cluster pages.

## How this atlas is organized

Kora is a long-running local daemon (FastAPI + LangGraph + SQLite) with a Rich CLI front-end. The codebase factors into five clusters. Each cluster has its own folder with a `README.md` overview plus one deep-dive per module.

| # | Cluster | What lives here | Read when you want to understand… |
|---|---------|-----------------|-----------------------------------|
| 00 | [Current architecture](current-architecture.md) | whole system | Current source-backed architecture, recent acceptance caveats, and corrected counts. |
| 01 | [Runtime core](01-runtime-core/README.md) | `core/`, `daemon/`, `runtime/`, `runtime/orchestration/`, `graph/` | How a turn runs and how every non-conversation job is scheduled. The DI container, FastAPI server, WebSocket protocol, LangGraph supervisor, turn tracing, `TriggerEvaluator`, and the current `OrchestrationEngine`. |
| 02 | [Memory & context](02-memory-context/README.md) | `memory/`, `context/`, `tools/` | How Kora remembers. Configurable filesystem-canonical memory, projection DB, hybrid retrieval, compaction, the `recall()` fast path, and the current 47 registered Python tools. |
| 03 | [Agents & autonomous](03-agents-autonomous/README.md) | `agents/workers/`, `autonomous/`, `capabilities/`, `skills/`, `routing/` | How Kora *does* things. Worker harnesses (planner/executor/reviewer), multi-step autonomous plans, capability packs, YAML skills. |
| 04 | [Conversation & LLM](04-conversation-llm/README.md) | `llm/`, `emotion/`, `cli/`, `mcp/`, `quality/` | How Kora talks. LLM providers (MiniMax via Anthropic SDK, Claude Code delegate), two-tier PAD emotion, Rich CLI, MCP manager, quality sampling. |
| 05 | [Life OS, support & ADHD](05-life-adhd/README.md) | `life/`, `support/`, `safety/`, `adhd/` | Kora's current product center: day plans, reality ledger, repair loop, load meter, proactivity policy, stabilization, context packs, future bridges, support profiles, crisis boundary, routines, reminders, and ADHD-aware behavior. |

## Navigation index

### 01 — Runtime core
- [Current architecture](current-architecture.md) — source-backed whole-system snapshot and acceptance caveats
- [Cluster README](01-runtime-core/README.md) — request→turn→response flow, lifecycle, cross-subsystem map
- [core.md](01-runtime-core/core.md) — DI container, settings, `operational.db` schema, event bus, retry, logging
- [daemon.md](01-runtime-core/daemon.md) — FastAPI routes, WebSocket turn queue, launcher, lockfile, auth relay, session bridge, orchestration engine lifecycle
- [runtime.md](01-runtime-core/runtime.md) — `GraphTurnRunner`, compaction circuit breaker, inspector, artifact store, checkpointer lifecycle
- [orchestration.md](01-runtime-core/orchestration.md) — `OrchestrationEngine`, `TriggerEvaluator`, `WorkerTask` FSM, pipelines, triggers, dispatcher, `SystemState`, `RequestLimiter`, `NotificationGate`, working docs
- [graph.md](01-runtime-core/graph.md) — LangGraph supervisor, tool-footprint tracker, CJK filter, and the 11 supervisor tools

### 02 — Memory & context
- [Cluster README](02-memory-context/README.md) — filesystem vs projection DB, write/read flows, working vs long-term memory
- [memory.md](02-memory-context/memory.md) — `FilesystemMemoryStore`, `ProjectionDB` (FTS5 + vec0), hybrid retrieval algorithm, `WritePipeline`, `SignalScanner`
- [context.md](02-memory-context/context.md) — `WorkingMemoryLoader`, `ContextBudgetMonitor` (5-tier), 4-stage compaction pipeline, `ContextEngine`
- [tools.md](02-memory-context/tools.md) — registry, `@tool` decorator, auth levels, `recall()` fast path, current 47 Python tools, `DomainVerbResolver`

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

### 05 — Life OS, support & ADHD
- [Cluster README](05-life-adhd/README.md) — Life OS product loop plus support/safety/ADHD integration
- [life.md](05-life-adhd/life.md) — day plans, ledger, load, repair, proactivity policy, stabilization, context packs, future bridge, trusted support, routines, reminders, and `ContextEngine`
- [adhd.md](05-life-adhd/adhd.md) — morning/crash/day bounds, trend detection, shame-free output rules, timezone fixes

## How this was produced

This atlas started as a source-derived cluster map. It has since drifted in places as the worktree moved through Phase 8 memory, vault, proactive, reminder, trigger-evaluator, and capability work. Use [`current-architecture.md`](current-architecture.md) for the current checked facts, and treat older cluster pages as deep dives that may still contain stale local claims.

The latest refresh pass was performed on 2026-04-28 against the dirty `main` worktree at `d056894`, using live code, `/tmp/claude/kora_acceptance` artifacts, Life OS manual probes, and parallel subagent audits. The current product center is now Life OS. The latest local `/tmp` general acceptance report is a short Day 1 run, not a full green proof; the latest remembered clean full pre-pivot run from 2026-04-26 reported `67/69` active items satisfied with deferred item `1` and still-red items `48` and `55`.

## Ground rules for reading

- **Every `file.py:42` reference should be rechecked when editing docs.** The worktree is active.
- **"Stub" means only what current code proves.** Several older pages still call Phase 8 handlers stubs even though real handlers are wired.
- **Timezone-aware date logic matters.** Phase 5 fixed three timezone collapse bugs — see [adhd.md](05-life-adhd/adhd.md).
- **The `routing/` directory is empty.** Routing logic lives in the supervisor graph and the autonomous graph, not in `routing/`.
- **MiniMax speaks the Anthropic API.** The LLM provider uses `anthropic.AsyncAnthropic` pointed at `https://api.minimax.io/anthropic`.

## Top-level repo context

- Primary package: `kora_v2/` — everything else (`kora/`, `src/`, `Documentation/archive/`) is legacy or docs.
- Canonical memory: `settings.memory.kora_memory_path`, default `~/.kora/memory` (acceptance can override it under `/tmp/claude/kora_acceptance/memory`).
- Runtime state: `data/operational.db`, `data/projection.db`, and per-session SQLite checkpointers.
- Entry point: `kora_v2.daemon.launcher:main` → `kora` console script.
- Stack: Python 3.12+, FastAPI, LangGraph, `anthropic` SDK, `sqlite-vec`, Rich.
