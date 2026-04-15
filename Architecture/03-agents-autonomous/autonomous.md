# Autonomous Multi-Step Execution

Autonomous work is the long-running, multi-step execution surface for goals like "research 3 project management tools", "plan my morning routine", or "draft a reply and outline the follow-up". Through Slice 7.5b it lived in its own `BackgroundWorker`-launched `AutonomousExecutionLoop`; as of Slice 7.5c that standalone loop is gone. The 12-node state machine still exists node-for-node, but it now runs **inside** a single `LONG_BACKGROUND` `WorkerTask` dispatched by the `OrchestrationEngine`. The step function walks the state machine per dispatcher tick and parks the task in `PAUSED_FOR_STATE` / `PAUSED_FOR_DECISION` / `PAUSED_FOR_RATE_LIMIT` at every natural boundary.

This document covers the 12 graph nodes, the pipeline that wraps them, the step function that drives them, the 5-axis budget enforcer, topic-overlap detection, in-memory decision management, the legacy `autonomous_checkpoints` migration, and the spec §17.7 preservation contract that pins the whole thing in place. For the orchestration primitives themselves (`WorkerTask`, `Pipeline`, `Dispatcher`, `SystemStatePhase`, `RequestLimiter`, etc.) see [../01-runtime-core/orchestration.md](../01-runtime-core/orchestration.md).

---

## What changed in Phase 7.5

| Before (Slice 7.5a and earlier) | After (Slice 7.5b/c) |
|---|---|
| `AutonomousExecutionLoop` in `kora_v2/autonomous/loop.py` — its own asyncio task, its own watchdog, its own checkpoint cadence | Deleted. The 12 node functions now live in `kora_v2/autonomous/graph.py` as a library. |
| `CheckpointManager` in `kora_v2/autonomous/checkpoint.py` writing to the `autonomous_checkpoints` table | Deleted. State is stored as JSON in `worker_tasks.checkpoint_blob.scratch_state` via the orchestration engine's `Checkpoint` column. Legacy rows are migrated idempotently on first boot by `runtime/orchestration/autonomous_migration.py`. |
| `BudgetEnforcer` standalone in `kora_v2/autonomous/budget.py` | Moved to `runtime/orchestration/autonomous_budget.py`. Same 5-axis API, called from inside the step function. |
| `DecisionManager` and `check_topic_overlap` in `kora_v2/autonomous/` | Moved to `runtime/orchestration/decisions.py` and `runtime/orchestration/overlap.py`. The old `kora_v2.autonomous.decisions` / `kora_v2.autonomous.overlap` module paths now exist only as compatibility shims (one line re-exports). |
| Supervisor tool `start_autonomous` spawned the loop | Retired. The supervisor now calls `decompose_and_dispatch(pipeline_name="user_autonomous_task")`, which routes through `OrchestrationEngine.start_pipeline_instance()`. |
| Overlap score pushed into a live loop via `loop.set_overlap_score()` | Cached on the container by `_check_autonomous_overlap()` in `daemon/server.py`; the dispatcher picks it up at the next safe tick and the step function's `reflect` branch transitions the task to `paused_for_state` if it crosses 0.70. |
| `daemon/worker.py` + `daemon/work_items.py` (two-tier BackgroundWorker) | Deleted. See [../01-runtime-core/orchestration.md](../01-runtime-core/orchestration.md) for the single `OrchestrationEngine` that replaced them. |

---

## Files in this module

| File | Purpose |
|------|---------|
| [`kora_v2/autonomous/__init__.py`](../../kora_v2/autonomous/__init__.py) | Package docstring only |
| [`kora_v2/autonomous/state.py`](../../kora_v2/autonomous/state.py) | `AutonomousState`, `AutonomousStepState`, `AutonomousCheckpoint` — the Pydantic models that survive unchanged |
| [`kora_v2/autonomous/graph.py`](../../kora_v2/autonomous/graph.py) | The 12 node functions as plain async functions, plus `route_next_node()` |
| [`kora_v2/autonomous/pipeline_factory.py`](../../kora_v2/autonomous/pipeline_factory.py) | **New in 7.5c.** `build_user_autonomous_task_pipeline()` / `build_user_routine_task_pipeline()`, `AUTONOMOUS_NODES` tuple, and the real `_autonomous_step_fn()` that walks the state machine |
| [`kora_v2/autonomous/runtime_context.py`](../../kora_v2/autonomous/runtime_context.py) | **New in 7.5c.** Process-level `AutonomousRuntimeContext` — the single place the autonomous migration leaks the DI container into the otherwise-agnostic step function contract |
| [`kora_v2/autonomous/decisions.py`](../../kora_v2/autonomous/decisions.py) | Compatibility shim — re-exports `DecisionManager` / `DecisionResult` / `PendingDecision` from `runtime/orchestration/decisions.py` |
| [`kora_v2/autonomous/overlap.py`](../../kora_v2/autonomous/overlap.py) | Compatibility shim — re-exports `OverlapResult` / `check_topic_overlap` from `runtime/orchestration/overlap.py` |
| [`kora_v2/runtime/orchestration/autonomous_budget.py`](../../kora_v2/runtime/orchestration/autonomous_budget.py) | `BudgetEnforcer` (moved out of `kora_v2/autonomous/`) |
| [`kora_v2/runtime/orchestration/autonomous_migration.py`](../../kora_v2/runtime/orchestration/autonomous_migration.py) | Idempotent one-shot migration: legacy `autonomous_checkpoints` rows → `worker_tasks` + `pipeline_instances`, guarded by a `work_ledger` marker |

The three deleted files are `kora_v2/autonomous/loop.py`, `kora_v2/autonomous/checkpoint.py`, and `kora_v2/autonomous/budget.py`.

---

## The pipeline wrapper

`pipeline_factory.py` declares two pipelines that both wrap the same 12-node graph. They differ only in the trigger source:

| Pipeline | Trigger | How it fires |
|----------|---------|--------------|
| `user_autonomous_task` | `user_action(action_name="decompose_and_dispatch")` | The supervisor's `decompose_and_dispatch` tool calls `engine.start_pipeline_instance()` directly. The declared trigger is a diagnostic placeholder — it is never the actual dispatch path. |
| `user_routine_task` | `time_of_day(at=<routine local time>)` | `TriggerScheduler` fires the scheduled trigger. Same stage list, same step function, same budget, same preservation contract. |

Both pipelines use the `long_background` preset, `InterruptionPolicy.PAUSE_ON_CONVERSATION`, `FailurePolicy.FAIL_PIPELINE`, and `intent_duration="long"`.

### Why one task for twelve nodes?

Orchestration pipelines must be **acyclic** — `Pipeline._assert_acyclic()` runs a Tarjan SCC walk at registration time and rejects any stage-level cycle. The autonomous graph is **cyclic**: `execute_step` loops back on itself while pending steps exist; `reflect` may route to `execute_step` via `replan` or to `paused_for_overlap` / `complete` / `failed`.

The resolution, pinned by spec §17.6 and §17.7:

1. The 12 stages are declared (one `PipelineStage` per node) so that `tests/unit/orchestration/test_pipeline_parity.py` can diff the stage list against the live `AUTONOMOUS_NODES` tuple.
2. Only the first stage (`plan`) is ever dispatched as a `WorkerTask`.
3. The step function — `_autonomous_step_fn()` in `pipeline_factory.py` — walks the entire state machine internally, calling `route_next_node(state)` and the individual graph-node functions from `autonomous/graph.py` directly.
4. `AutonomousState` round-trips through `Checkpoint.scratch_state` as JSON. The dispatcher never sees it.
5. The acyclic stage list encodes only the *primary forward flow*; the step function decides whether to actually advance along any given edge.

### Stage DAG

```
plan
 └─ persist_plan
      └─ execute_step
           └─ review_step
                └─ checkpoint
                     └─ reflect
                          ├─ replan
                          ├─ decision_request
                          │    └─ waiting_on_user
                          ├─ paused_for_overlap
                          ├─ complete
                          └─ failed
```

The `execute_step → review_step → checkpoint → reflect → execute_step` cycle from the runtime graph does not appear here — the step function, not the stage DAG, drives that loop.

---

## `_autonomous_step_fn()` — the real step function

The step function is called once per dispatcher tick with `(task, ctx)` where `task` is a `WorkerTask` and `ctx` is a fresh `StepContext`. Each invocation:

1. **Rehydrates** `AutonomousState` from `task.checkpoint_blob.scratch_state[_SCRATCH_STATE_KEY]`, or classifies the request on the first tick via `graph_nodes.classify_request(goal, session_id)`. Wall-clock start is stored as an epoch under `_SCRATCH_WALL_START_KEY` so elapsed time survives daemon restarts (`time.monotonic()` would reset).
2. **Pulls the process-level runtime context** via `get_autonomous_context()` so it can reach `container.resolve_worker("planner" | "executor" | "reviewer")` and the operational DB path. The engine installs this at `engine.start()` via `set_autonomous_context(container=..., db_path=...)` — the one and only place the autonomous layer leaks the DI container into the step-function contract.
3. **Checks the interruption and watchdog signals.** A same-node watchdog counter (`_SCRATCH_SAME_NODE_REPEATS_KEY`) increments every time `route_next_node()` returns the same non-cyclic node twice in a row. `_MAX_SAME_NODE_REPEATS = 5` trips the state machine to `failed`. Only `waiting_on_user` is in `_LEGITIMATELY_CYCLIC_NODES` and resets the counter.
4. **Runs the 5-axis budget enforcer** before any work node (`plan`, `execute_step`, `review_step`, `replan`). `BudgetEnforcer.check_before_step(state)` returns a `BudgetCheckResult`; a hard stop transitions the state machine directly to `failed`, a soft warning sets `metadata["budget_soft_warning"]=True` and continues.
5. **Dispatches to one node** — `graph_nodes.<name>(state, ...)` or the `code`-worker subprocess path via `_delegate_to_claude_code()`. The state returned by the node is the new `AutonomousState` for the next tick.
6. **Stashes periodic checkpoints.** After any work node runs, if `_DEFAULT_CHECKPOINT_INTERVAL_SECONDS` (30 min) has elapsed since the last checkpoint, the step function marks a new `Checkpoint` on the task. `reason` is `"periodic"`, `"overlap"`, `"budget"`, `"replan"`, or `"termination"`.
7. **Returns a `StepResult`** — one of:
   - `complete` for terminal `completed` status (end of pipeline)
   - `continue` for any non-terminal status (dispatcher reschedules on the next tick)
   - `paused_for_state` with `reason="overlap"` when `state.overlap_score >= 0.70` (dispatcher resumes when `SystemStatePhase` allows)
   - `paused_for_decision` with the `decision_id` when `state.status == "waiting_on_user"` (dispatcher resumes when `record_decision` fires on the matching row in `open_decisions`)
   - `paused_for_rate_limit` when the `RequestLimiter` refuses a BACKGROUND-class request (dispatcher resumes when the 5h window has capacity)
   - `failed` for any terminal failure path

The full scratch key surface is declared as module constants in `pipeline_factory.py`:

| Constant | Stores |
|----------|--------|
| `_SCRATCH_STATE_KEY` | JSON dump of `AutonomousState` |
| `_SCRATCH_PREV_NODE_KEY` | Last node dispatched (for watchdog) |
| `_SCRATCH_SAME_NODE_REPEATS_KEY` | Consecutive-same-node count |
| `_SCRATCH_WALL_START_KEY` | Epoch start time (survives restarts) |
| `_SCRATCH_LAST_CHECKPOINT_KEY` | Last periodic checkpoint epoch |
| `_SCRATCH_INITIALISED_KEY` | `True` after first successful `classify_request` |

Any change to this surface requires a migration — the constants are deliberately hand-written in one place so the JSON shape is reviewable.

---

## The 12 graph nodes

`kora_v2/autonomous/graph.py` still defines the twelve node functions as plain async coroutines. The canonical order is pinned by `AUTONOMOUS_NODES` in `pipeline_factory.py`; adding a new node or status requires updating both.

| # | Node | What it does | Failure behaviour |
|---|------|--------------|-------------------|
| 1 | `classify_request` | Keyword scan of the goal string. Any word in `_ROUTINE_KEYWORDS` sets `mode="routine"`. Generates `plan_id`. Sets `status="planned"`. | Pure function — cannot fail |
| 2 | `plan` | Calls `container.resolve_worker("planner")` with a `PlanInput`. Stores steps in `state.metadata["steps"]`. **Empty plan is a hard fail** to prevent a tight `plan→plan` loop. | Transitions to `failed` on empty plan or exception |
| 3 | `persist_plan` | Writes a parent `items` row for the goal and child items for each step. On replan, only inserts new steps not already in `metadata["item_ids"]`. Creates the `autonomous_plans` row on first call. | Transitions to `failed` on any exception (rather than retrying, to avoid an infinite tight loop) |
| 4 | `execute_step` | Pops the first pending step. Routes to executor / reviewer / memory / research / life_mgmt / screen / ClaudeCodeDelegate based on `step_data["worker"]`. Updates the `items` row to `in_progress` / `completed` / `failed`. | Transitions to `failed` on exception |
| 5 | `review_step` | Calls the reviewer worker. Computes confidence via `confidence_from_review()`. Stores the result under `state.quality_summary[step_id]`. | Review errors are **non-fatal** — they store a default confidence of 0.5 and continue |
| 6 | `checkpoint` | Marks a durable boundary by asking the step function to checkpoint the scratch state. Generates fresh `checkpoint_id` + `resume_token` UUIDs. | Cannot fail — it is a no-op on the state machine level |
| 7 | `reflect` | Pure heuristic decision function (no LLM). Answers five questions in order: (1) all steps done → `complete`; (2) `overlap_score >= 0.70` → `paused_for_overlap`; (3) decision queue non-empty → `decision_request`; (4) average step confidence `< 0.35` and at least one step completed → `replan`; (5) default → `continue` | Pure — cannot fail |
| 8 | `replan` | Calls the planner again with context about how many steps are done and why replanning fired. Replaces `pending_step_ids`. | Transitions to `failed` on exception |
| 9 | `decision_request` | Registers a `PendingDecision` with the shared `DecisionManager`. Sets `status="waiting_on_user"` and adds the id to `decision_queue`. | Cannot fail |
| 10 | `waiting_on_user` | Returns immediately — the step function converts this node into a `paused_for_decision` outcome and the dispatcher keeps the task parked until `record_decision` writes to `open_decisions` | — |
| 11 | `paused_for_overlap` | Returns immediately — the step function converts this node into a `paused_for_state` outcome with `reason="overlap"`. The dispatcher resumes when `SystemStatePhase` allows | — |
| 12 | `complete` / `failed` | Updates `autonomous_plans` and the root item to the terminal state. Sets `state.status` accordingly. The step function returns `StepResult(outcome="complete" | "failed")` which the dispatcher interprets as the end of the pipeline instance | — |

### `route_next_node(state) → str`

Pure function. Status → node mapping:

| Status | Next node |
|--------|-----------|
| `planned` + no steps | `plan` |
| `planned` + steps + no root_item_id | `persist_plan` |
| `planned` + steps + root_item_id | `execute_step` |
| `replanning` + steps | `persist_plan` |
| `replanning` + no steps | `complete` |
| `executing` | `review_step` |
| `reviewing` | `checkpoint` |
| `checkpointing` | `reflect` |
| `reflecting` | `reflect` (re-evaluate) |
| `waiting_on_user` | `waiting_on_user` (step fn converts to `paused_for_decision`) |
| `paused_for_overlap` | step fn converts to `paused_for_state` |
| `completed` / `cancelled` / `failed` | terminal |

The router is unchanged from the pre-7.5c implementation. Only the driver changed.

---

## State models (`state.py`)

The Pydantic models survived the 7.5c rewrite unchanged.

### `AutonomousStepState`

Per-step snapshot: `id`, `title`, `description`, `status: StepStatus`, `worker`, `started_at`, `completed_at`, `artifacts`, `error`.

`StepStatus` literal: `planned | dispatched | waiting_on_user | blocked | accepted | dropped`.

### `AutonomousState`

Full session state passed between node functions. Immutable by convention — every node calls `state.model_copy(deep=True)` before mutation. Key fields:

| Field | Type | Purpose |
|-------|------|---------|
| `session_id` | str | Conversation session owning this plan |
| `plan_id` | str | UUID for the autonomous plan row |
| `mode` | `task|routine` | Detected from goal keywords |
| `status` | 12-value literal | Drives routing in `route_next_node()` |
| `current_step_id` | str\|None | Currently executing step |
| `pending_step_ids` | list[str] | Steps not yet dispatched |
| `completed_step_ids` | list[str] | Finished steps |
| `decision_queue` | list[str] | Pending decision IDs |
| `overlap_score` | float | Set from the container-cached score at the start of each tick |
| `request_count` | int | Total LLM/API calls made |
| `token_estimate` | int | Cumulative token count |
| `cost_estimate` | float | Cumulative $ cost |
| `request_window_1h` | int | Calls in the current 1-hour window |
| `request_window_5h` | int | Calls in the current 5-hour window (for `RequestLimiter` parity) |
| `interruption_pending` | bool | Honoured at the next tick |
| `safe_resume_token` | str\|None | UUID set at each checkpoint |
| `quality_summary` | dict | Per-step review results |
| `metadata` | dict | Goal, plan JSON, step data, item IDs, results |

### `AutonomousCheckpoint`

Still defined in `state.py` — carries the full `AutonomousState` plus denormalised fields (`completed_step_ids`, `pending_step_ids`, `quality_results`, etc.). In Phase 7.5 it is no longer the unit of persistence; the step function serialises `AutonomousState` directly into `scratch_state`. The model survives so the legacy migration can parse old `autonomous_checkpoints.plan_json` rows, and so diagnostic UIs can still build a checkpoint view from the current scratch state.

---

## Budget enforcement (`runtime/orchestration/autonomous_budget.py`)

`BudgetEnforcer` is stateless — one instance per step-function invocation. It checks five axes before each work node and before any external call.

```python
enforcer = BudgetEnforcer(
    autonomous=container.settings.autonomous,
    llm=container.settings.llm,
    request_warning_threshold=0.85,
    request_hard_stop_threshold=1.0,
)
```

**Axes checked by `check_before_step(state)`:**

| Priority | Axis | Source | Hard stop |
|----------|------|--------|-----------|
| 1 | 1-hour request window | `state.request_window_1h` vs `auto.request_limit_per_hour` | Yes |
| 2 | Total request count | `state.request_count` vs derived/configured limit | Yes |
| 3 | Wall-clock time | `state.elapsed_seconds` vs `auto.max_session_hours * 3600` | Yes |
| 4 | Cost estimate | `state.cost_estimate` vs `auto.per_session_cost_limit` | Yes |
| 5 | Token estimate | `state.token_estimate` vs `llm.context_window` | Yes |

`check_before_external_call(state)` checks only axis 1 — used before any LLM call.

A hard stop transitions the state machine to `failed` with a reason string in `metadata["budget_failure"]`. A soft warning (>= 85% of the request limit) sets `metadata["budget_soft_warning"]=True` and continues.

Note: the 5-axis budget is *per-autonomous-session*. It is separate from and independent of the orchestration `RequestLimiter`, which tracks the global 5-hour / 4500-request window across every non-`CONVERSATION` request Kora makes — see [../01-runtime-core/orchestration.md § RequestLimiter](../01-runtime-core/orchestration.md#requestlimiter--the-5-hour-budget). The step function runs both checks: `BudgetEnforcer` for the per-session axes, and the dispatcher consults `RequestLimiter` for the global window.

---

## Decision management (`runtime/orchestration/decisions.py`)

`DecisionManager` is the same class that used to live in `kora_v2/autonomous/decisions.py`; Slice 7.5a moved it to the orchestration package so non-autonomous pipelines can reuse it, and `kora_v2/autonomous/decisions.py` is now a one-line compatibility shim.

### `PendingDecision`

`decision_id`, `options: list[str]`, `recommendation: str | None`, `policy: "auto_select" | "never_auto"`, `expires_at`, `created_at`.

### `DecisionResult`

`decision_id`, `chosen: str`, `method: "user" | "auto_select" | "timeout"`, `decided_at`.

### `DecisionManager`

In-memory dict of `decision_id → PendingDecision`. `OpenDecisionsTracker` (see [orchestration.md § Open decisions](../01-runtime-core/orchestration.md#open-decisions)) persists the durable copy in the `open_decisions` table; the in-memory map is the fast-path cache the step function reads.

| Method | Behavior |
|--------|----------|
| `create_decision(options, recommendation, policy, timeout_minutes)` | Creates and registers a decision. Validates `recommendation ∈ options` for `auto_select`. |
| `submit_answer(decision_id, chosen)` | Records user answer, removes from pending. Raises on unknown id or invalid choice. |
| `check_timeout(decision)` | For `auto_select`: if expired, auto-resolves to `recommendation` (or first option). For `never_auto`: always `None` regardless of expiry. |
| `is_expired(decision)` | `datetime.now(UTC) >= decision.expires_at` |
| `get_pending(decision_id)` | Returns `PendingDecision | None` |

When the step function hits `decision_request`, it creates a `PendingDecision`, records an open-decision row via `OpenDecisionsTracker.record()`, and returns `StepResult(outcome="paused_for_decision", decision_id=...)`. The supervisor's `record_decision` tool (see [../01-runtime-core/graph.md](../01-runtime-core/graph.md)) later writes to the same row, which causes the dispatcher to un-pause the task at the next tick.

---

## Topic overlap detection (`runtime/orchestration/overlap.py`)

`check_topic_overlap(user_message, autonomous_goal, active_step_description, container)` — async function. Computes a weighted similarity score:

```
score = 0.6 * cosine(user_message, active_step_description)
      + 0.2 * cosine(user_message, autonomous_goal)
      + 0.2 * lexical_jaccard(user_message, active_step_description)
```

Cosine similarity uses the container's embedding service. If the service is unavailable, cosine returns `0.0` for that axis (not `0.5`, to avoid biasing toward `pause`). Lexical Jaccard uses content words (length > 3, stop-word filtered).

**Classification thresholds:**

| Score | Action | Message |
|-------|--------|---------|
| >= 0.70 | `pause` | "That sounds related to the work I am doing..." |
| 0.45–0.70 | `ambiguous` | None |
| < 0.45 | `continue` | None |

Exceptions in the scoring function return `OverlapResult(score=0.0, action="continue")` — fail-safe to "don't interrupt".

**How the score reaches the step function:**

1. The user sends a chat message.
2. `_check_autonomous_overlap()` in `daemon/server.py` looks up the in-flight `user_autonomous_task` / `user_routine_task` instance via `engine.task_registry.load_all_non_terminal()` + `engine.instance_registry.load()`, calls `check_topic_overlap()`, and caches the score on the container.
3. The supervisor's turn-start `list_tasks()` surfacing rule (see [../01-runtime-core/graph.md](../01-runtime-core/graph.md)) uses the 0.45–0.70 mid-band to decide whether to show the task to the user in-turn.
4. At the next dispatcher tick, the step function reads the cached score into `state.overlap_score`.
5. If `reflect` sees `overlap_score >= 0.70`, the state machine routes to `paused_for_overlap` and the step function returns `paused_for_state` with `reason="overlap"`.
6. The dispatcher resumes the task when `SystemStatePhase` is no longer `CONVERSATION`.

This is strictly one-directional — the step function never pushes back into the main conversation.

---

## Legacy `autonomous_checkpoints` migration

`runtime/orchestration/autonomous_migration.py` runs once at engine `start()` time and is guarded by a `work_ledger` marker row so it never runs twice.

1. **Read** every row from `autonomous_checkpoints` ordered by `created_at DESC`.
2. **Group** by `session_id` (extracted from `plan_json`). The legacy table had no `session_id` column.
3. **Keep only the most recent checkpoint per session** — older ones are legacy redundancy that the new scratch-state model doesn't need.
4. **Parse** `plan_json` → `AutonomousCheckpoint` → `AutonomousState`.
5. **Write** a new `pipeline_instances` row (pipeline `user_autonomous_task`, `status=paused_for_state`, `reason=crash_recovery`) and a new `worker_tasks` row with the rehydrated state serialised into `checkpoint_blob.scratch_state`.
6. **Mark** the session id as migrated in `work_ledger` via `mark_migrated(session_id)`.

If the engine crashes mid-migration, the partial transaction rolls back and the next boot re-attempts from the un-marked rows.

---

## Preservation contract §17.7

The 10-row parity contract is pinned by `tests/integration/orchestration/test_preservation_contract.py`. Each row asserts that one piece of 7.5-era behaviour has been preserved verbatim by the 7.5c step function:

| # | Preserved behaviour | Asserted by |
|---|---------------------|-------------|
| 1 | 12-node sequence and names | `live_graph_node_names() == AUTONOMOUS_NODES` |
| 2 | 14-value `AutonomousState.status` enum | Field literal on the Pydantic model |
| 3 | `classify_request` routine keyword detection | Golden-case test with routine vs task goals |
| 4 | `reflect` five-question heuristic (order matters) | Golden-case state transitions |
| 5 | Same-node watchdog at 5 repeats | Inject a stuck state, assert terminal `failed` |
| 6 | 5-axis budget enforcer ordering and hard-stop semantics | Mock limits; assert first-hit axis wins |
| 7 | Topic-overlap pause at 0.70 | Inject `overlap_score=0.75`; assert `paused_for_state reason="overlap"` |
| 8 | Periodic checkpoint at 30 min | Advance wall-clock past the interval; assert checkpoint boundary |
| 9 | `DecisionManager` auto-select vs never-auto timeout policy | Pre-expire a decision; assert the right outcome |
| 10 | Legacy `autonomous_checkpoints` round-trip | Seed a legacy row; run migration; assert the resulting `worker_tasks` row rehydrates to the same `AutonomousState` |

If you change `_autonomous_step_fn()` or any of the twelve graph nodes, this test tells you which guarantee you broke. Do not edit the test to make a regression pass without a spec update.

---

## Integration points

- **Dispatched by** `OrchestrationEngine.start_pipeline_instance()` via the supervisor's `decompose_and_dispatch` tool, or by `TriggerScheduler` firing a routine's `time_of_day` trigger.
- **Worker dispatch** inside the step function goes through `container.resolve_worker("planner" | "executor" | "reviewer")`, reached via the `AutonomousRuntimeContext` installed at `engine.start()`.
- **`code` worker delegation** still uses `ClaudeCodeDelegate` from `kora_v2/llm/claude_code.py` for `delegate_to_claude_code=True` steps.
- **Persistence** lives in `worker_tasks.checkpoint_blob` (column, not a separate table) and `pipeline_instances`. The legacy `autonomous_checkpoints` table is kept for back-compat only.
- **Budget settings** come from `kora_v2/core/settings.py` (`AutonomousSettings`, `LLMSettings`) — same as before.
- **Events** `TASK_CHECKPOINTED` / `TASK_COMPLETED` / `TASK_FAILED` / `PIPELINE_COMPLETE` are emitted by the dispatcher automatically on the relevant state transitions. The legacy `AUTONOMOUS_CHECKPOINT` / `AUTONOMOUS_COMPLETE` / `AUTONOMOUS_FAILED` events are still emitted inside the step function for back-compat so older CLI clients keep receiving checkpoint summaries via `autonomous_updates`.
- **Overlap scoring** is invoked per-turn by `daemon/server.py:_check_autonomous_overlap()`, which reads directly from the engine's task and instance registries.

---

## Cross-references

- [../01-runtime-core/orchestration.md](../01-runtime-core/orchestration.md) — the `OrchestrationEngine`, `WorkerTask` FSM, `Pipeline` validation, `Dispatcher`, `SystemStatePhase`, `RequestLimiter`, `NotificationGate`, `WorkingDocStore`, and the full 20 core pipelines catalogue
- [../01-runtime-core/graph.md](../01-runtime-core/graph.md) — the 7 supervisor tools that steer the engine (`decompose_and_dispatch`, `get_running_tasks`, `get_task_progress`, `get_working_doc`, `cancel_task`, `modify_task`, `record_decision`)
- [../01-runtime-core/daemon.md](../01-runtime-core/daemon.md) — the daemon's engine-lifecycle wiring in `run_server()`
- [../05-life-adhd/life.md](../05-life-adhd/life.md) — the `OpenDecisionsTracker` and how life-management decisions flow through the same `open_decisions` table
