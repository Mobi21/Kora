# Agents, Autonomous Execution, Capabilities, and Skills — Cluster Overview

This cluster documents the worker harnesses that execute individual turns, the autonomous multi-step execution engine that runs long tasks in the background, the capability-pack system that wraps external integrations with policy enforcement, and the skill/tool-gating layer that tells the supervisor LLM which tools are available in a given context.

These subsystems are distinct from the LangGraph supervisor graph (documented separately) and from the runtime kernel, memory, and conversation layers.

---

## Subsystems in this cluster

| Subsystem | Path | Files |
|---|---|---|
| Worker harnesses | `kora_v2/agents/workers/` | 3 py + `__init__` |
| Autonomous execution | `kora_v2/autonomous/` | 7 py + `__init__` |
| Capabilities | `kora_v2/capabilities/` | 24 py files across 4 packs |
| Skills | `kora_v2/skills/` | 1 py loader + 14 YAML definitions |
| Routing | `kora_v2/routing/` | Empty directory (no code) |

---

## Conceptual distinctions

### Worker vs. Capability vs. Skill

These three terms appear throughout the codebase and mean distinct things:

**Worker** (`agents/workers/`): An `AgentHarness` subclass that receives a typed Pydantic input model, calls the LLM with structured-output tool-forcing, validates the result, and returns a typed Pydantic output model. Workers are stateless per call. There are three: `PlannerWorkerHarness`, `ExecutorWorkerHarness`, `ReviewerWorkerHarness`.

**Capability** (`capabilities/`): A `CapabilityPack` subclass representing one integration domain (workspace/Google, browser, vault, doctor). Each pack owns: a health check, a set of `Action` objects registered into an `ActionRegistry`, and a `PolicyMatrix` describing what approval level each action requires. Capabilities gate real-world side effects (writing email, clicking browser elements, saving vault files). They are invoked by the executor worker or directly by the supervisor, not by the planner.

**Skill** (`skills/`): A YAML file that describes which tool names are available when a skill is "active" and optionally points at an agent that the skill activates. Skills are a tool-gating mechanism for the supervisor LLM — not a code execution unit. The `SkillLoader` reads YAML at startup and returns the union of tool names for whatever skills are currently active. A skill references capability actions by name (e.g., `browser.open`) or raw tool names (e.g., `write_file`). Skills and capabilities are intentionally separate: a skill is configuration-layer gating; a capability is the implementation-layer enforcement.

### Single-turn vs. autonomous

A **single-turn worker dispatch** happens when the supervisor graph routes to a worker (planner/executor/reviewer) during the main conversation turn. The worker runs, returns its output, and control returns to the supervisor within the same turn. This uses `container.resolve_worker(name)`.

**Autonomous multi-step execution** is a completely separate runtime: an `AutonomousExecutionLoop` spawned as a background asyncio task. It runs a 12-node graph (`classify → plan → persist_plan → execute_step → review_step → checkpoint → reflect → [continue | replan | decision_request | paused_for_overlap] → complete | failed`) independently of the main conversation thread. It has its own checkpoint format written to `operational.db`, its own budget enforcer, and its own decision manager. The foreground conversation can interrupt it via `request_interruption()` or communicate an updated overlap score via `set_overlap_score()`.

---

## Turn lifecycle: where workers fit

```
Supervisor LLM (graph/supervisor.py)
    │
    ├── routes to memory worker → recall() → memory context for the turn
    │
    ├── routes to planner worker → PlanInput → PlanOutput (if planning needed)
    │
    ├── routes to executor worker → ExecutionInput → ExecutionOutput (task execution)
    │
    └── routes to reviewer worker → ReviewInput → ReviewOutput (quality check)

Capabilities are invoked *within* executor work, not at the graph routing level.
Skills are consulted by the supervisor when building the tool list for the LLM.
```

---

## Files in this cluster

### `kora_v2/agents/workers/`
- [`workers.md`](workers.md) — full per-worker documentation

### `kora_v2/autonomous/`
- [`autonomous.md`](autonomous.md) — plan lifecycle, checkpoint format, budget enforcement, routing

### `kora_v2/capabilities/`
- [`capabilities.md`](capabilities.md) — capability abstraction, registry, all four packs

### `kora_v2/skills/`
- [`skills.md`](skills.md) — YAML schema, loader, runtime invocation, relationship to capabilities

### `kora_v2/routing/`
- [`routing.md`](routing.md) — empty directory; documented for completeness
