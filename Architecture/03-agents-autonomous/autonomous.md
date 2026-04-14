# Autonomous Multi-Step Execution

The autonomous execution subsystem runs long, multi-step tasks as a background asyncio process, separate from the main conversation thread. It manages a 12-node graph, periodic checkpointing to SQLite, five-axis budget enforcement, topic overlap detection with the foreground conversation, and a human-decision management layer for branch points requiring user judgment.

This is distinct from the runtime checkpointer in `kora_v2/runtime/` — the autonomous subsystem has its own checkpoint model and its own persistence table (`autonomous_checkpoints`).

---

## Files in this module

| File | Purpose |
|---|---|
| [`kora_v2/autonomous/__init__.py`](../../kora_v2/autonomous/__init__.py) | Package docstring only |
| [`kora_v2/autonomous/state.py`](../../kora_v2/autonomous/state.py) | Pydantic state models |
| [`kora_v2/autonomous/graph.py`](../../kora_v2/autonomous/graph.py) | 12-node graph + routing function |
| [`kora_v2/autonomous/loop.py`](../../kora_v2/autonomous/loop.py) | Execution loop + resume helper |
| [`kora_v2/autonomous/budget.py`](../../kora_v2/autonomous/budget.py) | Budget enforcer |
| [`kora_v2/autonomous/checkpoint.py`](../../kora_v2/autonomous/checkpoint.py) | Checkpoint persistence |
| [`kora_v2/autonomous/decisions.py`](../../kora_v2/autonomous/decisions.py) | Decision lifecycle manager |
| [`kora_v2/autonomous/overlap.py`](../../kora_v2/autonomous/overlap.py) | Topic overlap detection |

---

## State models (`state.py`)

### `AutonomousStepState`

Per-step snapshot: `id`, `title`, `description`, `status: StepStatus`, `worker`, `started_at`, `completed_at`, `artifacts`, `error`.

`StepStatus` literal: `planned | dispatched | waiting_on_user | blocked | accepted | dropped`.

### `AutonomousState`

Full session state passed between every graph node. Immutable by convention — every node calls `state.model_copy(deep=True)` before mutation. Key fields:

| Field | Type | Purpose |
|---|---|---|
| `session_id` | str | Conversation session owning this plan |
| `plan_id` | str | UUID for the autonomous plan row |
| `mode` | `task\|routine` | Detected from goal keywords |
| `status` | 14-value literal | Drives routing in `route_next_node()` |
| `current_step_id` | str\|None | Currently executing step |
| `pending_step_ids` | list[str] | Steps not yet dispatched |
| `completed_step_ids` | list[str] | Finished steps |
| `decision_queue` | list[str] | Pending decision IDs |
| `overlap_score` | float | Set by foreground conversation via `set_overlap_score()` |
| `request_count` | int | Total LLM/API calls made |
| `token_estimate` | int | Cumulative token count |
| `cost_estimate` | float | Cumulative $ cost |
| `request_window_1h` | int | Calls in the current 1-hour window |
| `interruption_pending` | bool | Set by `request_interruption()` |
| `safe_resume_token` | str\|None | UUID set at each checkpoint |
| `quality_summary` | dict | Per-step review results |
| `metadata` | dict | Goal, plan JSON, step data, item IDs, results |

### `AutonomousCheckpoint`

Serialisable snapshot written to SQLite. Contains the full `AutonomousState` plus redundant denormalised fields (`completed_step_ids`, `pending_step_ids`, etc.) for backwards-compatible SQL filtering. Also carries `resume_token`, `reason` (periodic/overlap/budget/replan/termination), and quality results.

---

## Graph nodes (`graph.py`)

The 12 nodes are plain async functions. `route_next_node()` maps `AutonomousState.status` to the next node name.

```
classify_request
     │
     ▼
   plan  ──(failure)──► failed
     │
     ▼
 persist_plan ──(failure)──► failed
     │
     ▼
 execute_step ──(failure)──► failed
     │
     ▼
 review_step
     │
     ▼
  checkpoint
     │
     ▼
   reflect
     │
     ├──► continue_execution → execute_step (next pending step)
     ├──► replan → persist_plan → execute_step
     ├──► decision_request → waiting_on_user (polls)
     ├──► paused_for_overlap → END (resumable)
     └──► complete → END
```

Terminal states: `completed`, `cancelled`, `failed`.

### Node details

**`classify_request(goal, session_id) → AutonomousState`**
Keyword scan of the goal string. Any word in `_ROUTINE_KEYWORDS` (morning, daily, habit, ritual, etc.) sets `mode="routine"`. Generates `plan_id` UUID. Sets `status="planned"`.

**`plan(state, container) → AutonomousState`**
Calls `container.resolve_worker("planner")` with a `PlanInput`. Stores the resulting steps in `state.metadata["steps"]` (dict keyed by step_id). Sets `state.pending_step_ids`. Guards against empty plan (would cause a tight `plan→plan` loop) by transitioning to `failed` immediately if zero steps.

**`persist_plan(state, db_path) → AutonomousState`**
Writes a parent item for the goal and child items for each step into the `items` table. On replan, only inserts new steps not already in `metadata["item_ids"]`. Also creates the `autonomous_plans` row on the initial call. Fatal on exception (transitions to `failed` rather than retrying, to avoid an infinite tight loop).

**`execute_step(state, container, db_path) → AutonomousState`**
Pops the first pending step. Routes to executor, reviewer, or (for `code` steps) the ClaudeCodeDelegate subprocess, based on `step_data["worker"]`:

- `executor / memory / research / life_mgmt / screen` → `_dispatch_to_executor()`
- `reviewer` → `_dispatch_to_reviewer()`
- `code` + `delegate_to_claude_code=True` → `_delegate_to_claude_code()`

Updates the item row to `in_progress` at start and `completed` on success. On exception, transitions to `failed`.

**`review_step(state, container) → AutonomousState`**
Calls `container.resolve_worker("reviewer")` on `last_step_result`. Computes confidence via `confidence_from_review()` (from `kora_v2/quality/confidence.py`). Stores result in `state.quality_summary[step_id]`. Review errors are non-fatal — they store a default confidence of 0.5 and continue.

**`checkpoint(state, checkpoint_manager, reason) → AutonomousState`**
Builds an `AutonomousCheckpoint` and calls `checkpoint_manager.save()`. Generates fresh `checkpoint_id` and `resume_token` UUIDs. Sets `state.status="checkpointing"`.

**`reflect(state) → tuple[AutonomousState, next_action_str]`**
Pure heuristic decision function (no LLM call). Answers five questions in order:

1. All steps done? → `"complete"`
2. Overlap score >= 0.70? → `"paused_for_overlap"`
3. Decision queue non-empty? → `"decision_request"`
4. Average step confidence < 0.35 and at least one step completed? → `"replan"`
5. Default → `"continue"`

**`decision_request(state, decision_manager, options, ...) → tuple[AutonomousState, PendingDecision]`**
Creates a `PendingDecision` via `DecisionManager.create_decision()`. Sets `status="waiting_on_user"` and adds the decision ID to `decision_queue`.

**`paused_for_overlap(state) → AutonomousState`**
Sets `status="paused_for_overlap"`. The loop then saves a checkpoint with `reason="overlap"` and exits. The session is resumable via `resume_from_checkpoint()`.

**`replan(state, container, failure_reason) → AutonomousState`**
Calls the planner again with a modified goal that includes context about how many steps are already done and why replanning was triggered. Replaces `pending_step_ids` with the new plan's steps. Sets `status="replanning"`, which routes to `persist_plan` next.

**`complete(state, db_path) → AutonomousState`**
Updates `autonomous_plans` and the root item to `completed`. Sets `state.status="completed"`. Calculates and stores `completion_summary` in metadata.

**`failed(state, error, db_path) → AutonomousState`**
Updates `autonomous_plans` to `failed`, root item to `cancelled`. Sets `state.status="failed"`.

### `route_next_node(state) → str`

Pure function. Status-to-node mapping:

| Status | Next node |
|---|---|
| `planned` + no steps | `plan` |
| `planned` + steps + no root_item_id | `persist_plan` |
| `planned` + steps + root_item_id | `execute_step` |
| `replanning` + steps | `persist_plan` |
| `replanning` + no steps | `complete` |
| `executing` | `review_step` |
| `reviewing` | `checkpoint` |
| `checkpointing` | `reflect` |
| `reflecting` | `reflect` (re-evaluate) |
| `waiting_on_user` | `waiting_on_user` (loop polls) |
| `paused_for_overlap` | `END` |
| terminal | `END` |

---

## Execution loop (`loop.py`)

### `AutonomousExecutionLoop`

Drives the graph as a background asyncio task.

```python
loop = AutonomousExecutionLoop(
    goal="Research 3 project management tools",
    session_id="abc123",
    container=container,
    db_path=Path("data/operational.db"),
    checkpoint_interval_minutes=30,  # default
    auto_continue_seconds=30,        # pause window after checkpoint
)
task = asyncio.create_task(loop.run())
```

**Public API:**

| Method | Purpose |
|---|---|
| `request_interruption()` | Signal stop at next safe boundary; saves checkpoint with `reason="termination"` |
| `submit_decision(decision_id, chosen)` | Forward a user answer to `DecisionManager` |
| `set_overlap_score(score)` | Write a new overlap score so reflect() can act on it |
| `state` (property) | Read-only current `AutonomousState` |
| `is_terminal` (property) | True if status is completed/cancelled/failed |

**Main loop:** `run()` calls `classify_request()` once to initialise state, then loops:

1. Update elapsed time.
2. Check interruption signal.
3. Call `route_next_node()`.
4. Same-node watchdog: if the same non-cyclic node repeats `_MAX_SAME_NODE_REPEATS = 5` times, transition to `failed`.
5. Check budget on `plan / execute_step / replan` nodes.
6. Dispatch to `_run_node()`.
7. If a work node just ran (`plan / execute_step / review_step / replan`) and the checkpoint interval has elapsed, save a periodic checkpoint.

**Stuck-loop protection:** legitimately cyclic nodes (`waiting_on_user`, `checkpointing`) reset the consecutive-same-node counter. Non-cyclic repeats are counted and fail the session at 5.

**Foreground hedge (documented in module docstring):** the foreground conversation may write a draft file synchronously while this loop is running. Workers are expected to use `merge_strategy="keep_existing"` semantics (read before write) to avoid clobbering the draft.

**Decision polling:** `_handle_decision_wait()` checks the first pending decision ID, calls `DecisionManager.check_timeout()`, and either resolves it (auto-select on timeout) or sleeps `_POLL_INTERVAL = 2.0` seconds.

### `resume_from_checkpoint(session_id, container, db_path)`

Module-level coroutine. Loads the latest checkpoint for a session via `CheckpointManager.load_latest()`. If the status is not terminal, builds a new `AutonomousExecutionLoop`, injects the restored `AutonomousState`, and sets `_wall_start` to account for already-elapsed time. Returns `None` if no checkpoint exists or the session is terminal.

---

## Budget enforcement (`budget.py`)

### `BudgetEnforcer`

Stateless checker instantiated once per session. Checks five axes in priority order before each step and before each external call.

```python
enforcer = BudgetEnforcer(
    autonomous=auto_settings,
    llm=llm_settings,
    request_warning_threshold=0.85,
    request_hard_stop_threshold=1.0,
)
```

**Axes checked by `check_before_step(state)`:**

| Priority | Axis | Source | Hard stop |
|---|---|---|---|
| 1 | 1-hour request window | `state.request_window_1h` vs `auto.request_limit_per_hour` | Yes |
| 2 | Total request count | `state.request_count` vs derived/configured limit | Yes |
| 3 | Wall-clock time | `state.elapsed_seconds` vs `auto.max_session_hours * 3600` | Yes |
| 4 | Cost estimate | `state.cost_estimate` vs `auto.per_session_cost_limit` | Yes |
| 5 | Token estimate | `state.token_estimate` vs `llm.context_window` | Yes |

`check_before_external_call(state)` checks only axis 1 (quota window) — used before any LLM call.

### `BudgetCheckResult`

`ok: bool`, `hard_stop: bool`, `soft_warning: bool`, `reason: str`, `dimension: str`.

- `ok=False` → caller should stop immediately.
- `soft_warning=True` → caller sets `metadata["budget_soft_warning"]=True` and continues.

`update_counters(state, tokens_used, cost, requests)` returns a new state with counters incremented. Does not mutate.

---

## Checkpoint persistence (`checkpoint.py`)

### `CheckpointManager`

Async persistence layer over the `autonomous_checkpoints` table in `operational.db`.

```python
manager = CheckpointManager(db_path=Path("data/operational.db"))
checkpoint_id = await manager.save(checkpoint)
cp = await manager.load_latest(session_id)
cp = await manager.load_by_id(checkpoint_id)
ids = await manager.list_session_checkpoints(session_id)
```

**Storage format:** the entire `AutonomousCheckpoint` is serialised as `model_dump_json()` into the `plan_json` column. Other columns (`id`, `plan_id`, `completed_steps`, `current_step`, `artifacts`, `elapsed_minutes`, `reflection`) are also populated for backwards-compatible SQL filtering.

**Session filtering limitation:** the `autonomous_checkpoints` table has no `session_id` column. `_load_all_for_session()` fetches all rows ordered by `created_at DESC` and parses `plan_json` to filter by session. This is acceptable because typical checkpoint counts are small.

`save()` uses `INSERT OR REPLACE` — calling save again with the same `checkpoint_id` overwrites.

---

## Decision management (`decisions.py`)

### `PendingDecision`

`decision_id`, `options: list[str]`, `recommendation: str | None`, `policy: "auto_select" | "never_auto"`, `expires_at`, `created_at`.

### `DecisionResult`

`decision_id`, `chosen: str`, `method: "user" | "auto_select" | "timeout"`, `decided_at`.

### `DecisionManager`

In-memory dict of `decision_id → PendingDecision`.

| Method | Behavior |
|---|---|
| `create_decision(options, recommendation, policy, timeout_minutes)` | Creates and registers a decision. Validates recommendation is in options for `auto_select`. |
| `submit_answer(decision_id, chosen)` | Records user answer, removes from pending. Raises `KeyError` if not found, `ValueError` if chosen not in options. |
| `check_timeout(decision)` | For `auto_select`: if expired, auto-resolves to recommendation (or first option). For `never_auto`: always `None` regardless of expiry. |
| `is_expired(decision)` | `datetime.now(UTC) >= decision.expires_at` |
| `get_pending(decision_id)` | Returns `PendingDecision | None` |

---

## Topic overlap detection (`overlap.py`)

### `check_topic_overlap(user_message, autonomous_goal, active_step_description, container)`

Async function. Computes a weighted similarity score:

```
score = 0.6 * cosine(user_message, active_step_description)
      + 0.2 * cosine(user_message, autonomous_goal)
      + 0.2 * lexical_jaccard(user_message, active_step_description)
```

Cosine similarity uses the container's `embedding_service` (LocalEmbeddingService). If the service is unavailable, cosine returns `0.0` for that axis (not `0.5`, to avoid biasing toward `pause`). Lexical Jaccard uses content words (length > 3, stop-word filtered).

**Classification thresholds:**

| Score | Action | Message |
|---|---|---|
| >= 0.70 | `pause` | "That sounds related to the work I am doing..." |
| 0.45–0.70 | `ambiguous` | None |
| < 0.45 | `continue` | None |

Exceptions in the scoring function return `OverlapResult(score=0.0, action="continue")` — fail-safe to "don't interrupt".

---

## Integration points

- `AutonomousExecutionLoop` is launched by the daemon's background worker or by the supervisor graph when a user request triggers autonomous mode.
- The loop calls `container.resolve_worker("planner")`, `container.resolve_worker("executor")`, `container.resolve_worker("reviewer")` for each step.
- For `code` worker steps with `delegate_to_claude_code=True`, it invokes `ClaudeCodeDelegate` from `kora_v2/llm/claude_code.py`.
- Checkpoints are stored in the `autonomous_checkpoints` table created by `init_operational_db` in `kora_v2/core/db.py`.
- Budget settings come from `kora_v2/core/settings.py` (`AutonomousSettings`, `LLMSettings`).
- Events (`AUTONOMOUS_CHECKPOINT`, `AUTONOMOUS_FAILED`, `AUTONOMOUS_COMPLETE`) are emitted via `container.event_emitter`.
- Overlap scores are typically set by the main conversation turn after calling `check_topic_overlap()` from `overlap.py`.
- The autonomous checkpoint format is separate from the LangGraph checkpointer in `kora_v2/runtime/checkpointer.py`.
