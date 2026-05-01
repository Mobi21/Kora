# Kora Architecture Overview

This page is now a short orientation layer. The full current architecture file is [`current-architecture.md`](current-architecture.md).

Code snapshot last verified: 2026-04-28 against the dirty `main` worktree at `d056894`.
GUI/client refresh: 2026-04-29.
Acceptance/demo evidence refresh: 2026-04-30.

## Mental Model

```text
Desktop GUI or Rich CLI
  -> local FastAPI daemon
  -> per-session turn queue
  -> GraphTurnRunner
  -> supervisor graph
  -> tools, memory, workers, capabilities, LLM
  -> streamed response

Desktop GUI REST views
  -> /api/v1/desktop/*
  -> kora_v2/desktop/service.py

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
- Kora now has two working clients: the Electron/React desktop GUI in `apps/desktop/` and the Rich CLI in `kora_v2/cli/`.
- Desktop screens read daemon-owned view models from `/api/v1/desktop/*`; desktop chat uses the same `/api/v1/ws` channel as the CLI.
- Life OS acceptance is now the product-center gate; older coding/research/writing checks are capability-pack health checks, not the main Kora acceptance surface.
- Core pipelines are no longer mostly stubs. Phase 8 memory, vault, ADHD profile, proactive, wake, continuity, research/draft, triage, and connection-making handlers are wired.
- `TriggerEvaluator` owns trigger firing; `Dispatcher` steps ready tasks.
- Reminders are no longer stored-only; `ReminderStore` plus `continuity_check_step` can deliver due reminders through `NotificationGate`.
- The default MiniMax model is `MiniMax-M2.7-highspeed`.
- Capability packs are visible, but recent acceptance health reports workspace/browser/vault unconfigured and doctor unimplemented.

## Acceptance Caveat

The latest exported public demo acceptance artifact is `apps/desktop/public/demo/acceptance_report.md`, generated on 2026-04-30 with `70/70` active items satisfied, `0` partial, and `12/12` Life OS scenarios satisfied for the Maya Rivera lived-week run.

Older `/tmp/claude/kora_acceptance/acceptance_output/acceptance_report.md` and remembered pre-demo runs are historical evidence. Do not cite them as the latest public Life OS acceptance state.

## Read Next

- [`current-architecture.md`](current-architecture.md) for the current source-backed architecture.
- [`01-runtime-core/orchestration.md`](01-runtime-core/orchestration.md) for orchestration details.
- [`02-memory-context/memory.md`](02-memory-context/memory.md) for memory and projection details.
- [`03-agents-autonomous/autonomous.md`](03-agents-autonomous/autonomous.md) for autonomous execution.
- [`../apps/desktop/README.md`](../apps/desktop/README.md) for the desktop GUI.
- [`05-life-adhd/life.md`](05-life-adhd/life.md) for the Life OS loop, support profiles, routines, reminders, and trusted support surfaces.
