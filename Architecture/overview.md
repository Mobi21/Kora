# Kora Architecture Overview

This page is now a short orientation layer. The full current architecture file is [`current-architecture.md`](current-architecture.md).

Last verified: 2026-04-28 against the dirty `main` worktree at `d056894`.

## Mental Model

```text
Rich CLI
  -> local FastAPI daemon
  -> per-session turn queue
  -> GraphTurnRunner
  -> supervisor graph
  -> tools, memory, workers, capabilities, LLM
  -> streamed response

OrchestrationEngine runs beside the turn path:
  TriggerEvaluator -> PipelineRegistry -> Dispatcher -> WorkerTask
  WorkLedger / RequestLimiter / NotificationGate / WorkingDocStore
```

The live supervisor graph is:

```text
receive -> build_suffix -> think -> tool_loop -> think ... -> synthesize -> END
```

Older descriptions that use `plan`, `act`, `review`, or `emit` as graph node names are stale.

## Current Corrections

- Runtime package is `kora_v2/`; the public console script is `kora`.
- Python requirement is `>=3.12`.
- Memory root is `settings.memory.kora_memory_path`, default `~/.kora/memory`; acceptance can override it to `/tmp/claude/kora_acceptance/memory`.
- Projection DB is `data/projection.db`; operational/orchestration/life state is `data/operational.db`.
- The supervisor has 11 base tools, not 10.
- Initialized Python registry tools currently total 47, not 23.
- Kora's current product center is Life OS: Plan Today -> Confirm Reality -> Repair The Day -> Bridge Tomorrow.
- Life OS acceptance is now the product-center gate; older coding/research/writing checks are capability-pack health checks, not the main Kora acceptance surface.
- Core pipelines are no longer mostly stubs. Phase 8 memory, vault, ADHD profile, proactive, wake, continuity, research/draft, triage, and connection-making handlers are wired.
- `TriggerEvaluator` owns trigger firing; `Dispatcher` steps ready tasks.
- Reminders are no longer stored-only; `ReminderStore` plus `continuity_check_step` can deliver due reminders through `NotificationGate`.
- The default MiniMax model is `MiniMax-M2.7-highspeed`.
- Capability packs are visible, but recent acceptance health reports workspace/browser/vault unconfigured and doctor unimplemented.

## Acceptance Caveat

The latest local `/tmp/claude/kora_acceptance/acceptance_output/acceptance_report.md` is a short Day 1 run generated on 2026-04-28 with `4` user turns and `44/69` active items satisfied plus `6` partial. It proves current wiring but is not a full green acceptance proof.

The latest remembered clean full run from 2026-04-26 reported `67/69` active items satisfied, deferred item `1`, and still-red items `48` and `55`. Do not claim current full acceptance is green without running a fresh full harness.

## Read Next

- [`current-architecture.md`](current-architecture.md) for the current source-backed architecture.
- [`01-runtime-core/orchestration.md`](01-runtime-core/orchestration.md) for orchestration details.
- [`02-memory-context/memory.md`](02-memory-context/memory.md) for memory and projection details.
- [`03-agents-autonomous/autonomous.md`](03-agents-autonomous/autonomous.md) for autonomous execution.
- [`05-life-adhd/life.md`](05-life-adhd/life.md) for the Life OS loop, support profiles, routines, reminders, and trusted support surfaces.
