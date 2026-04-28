# Orchestration Engine — `kora_v2/runtime/orchestration/`

Phase 7.5 shipped the unified orchestration substrate for every background, idle, and autonomous job in Kora. The current code has moved beyond the original mostly-stub slice: `OrchestrationEngine` owns registries and lifecycle, `TriggerEvaluator` fires triggers, `Dispatcher` steps ready `WorkerTask` rows, and Phase 8 memory/vault/proactive/reminder handlers are wired into the 20 core pipelines.

This document maps the subsystem. It is the reference when you need to understand how a scheduled pipeline decides to run, how a step function is called, or why a task is sitting in `paused_for_state`.

---

## Files

| File | Role |
|------|------|
| `engine.py` | `OrchestrationEngine` — top-level service, the object the DI container hands out |
| `worker_task.py` | `WorkerTask` dataclass, 11-state FSM, 3 presets, `StepContext`, `StepResult` |
| `pipeline.py` | `Pipeline`, `PipelineStage`, `PipelineInstance`, Tarjan cycle detection |
| `dispatcher.py` | Single-loop scheduler; ready set, phase filter, priority sort, crash recovery |
| `system_state.py` | `SystemStatePhase` enum (7 phases), `UserScheduleProfile`, `SystemStateMachine` |
| `limiter.py` | `RequestLimiter` — 5h sliding window, conversation + notification reserves |
| `notifications.py` | `NotificationGate` — two-tier delivery, hyperfocus, DND, template routing |
| `triggers.py` | 8 trigger kinds: `INTERVAL`, `EVENT`, `CONDITION`, `TIME_OF_DAY`, `SEQUENCE_COMPLETE`, `USER_ACTION`, `ANY_OF`, `ALL_OF` |
| `registry.py` | `PipelineRegistry`, `WorkerTaskRegistry`, `PipelineInstanceRegistry`, `TriggerStateStore`, schema init |
| `ledger.py` | `WorkLedger` — append-only audit trail (`work_ledger` rows) |
| `working_doc.py` | `WorkingDocStore` — per-instance YAML-frontmatter markdown under `<memory_root>/Inbox/` |
| `templates.py` | `TemplateRegistry` — YAML notification templates, hot reload |
| `decisions.py` | `OpenDecisionsTracker` — SQL-backed `open_decisions` table |
| `checkpointing.py` | `CheckpointStore` — writes/reads the `checkpoint_blob` JSON column |
| `core_pipelines.py` | `build_core_pipelines()` — 20 core declarations; most Phase 8 handlers are now wired, with `in_turn_subagent` still stubbed and two housekeeping no-ops |
| `autonomous_migration.py` | Idempotent one-shot: legacy `autonomous_checkpoints` → `worker_tasks` + `pipeline_instances` |
| `autonomous_budget.py` | 5-axis `BudgetEnforcer` used by the `user_autonomous_task` step function |
| `overlap.py` | `check_topic_overlap()` — foreground conversation vs running task topic score |
| `profile_bootstrap.py` | Fills orchestration anchors into `<memory_root>/User Model/` without clobbering user-set values |
| `migrations/001_orchestration.sql` | Eight orchestration tables; idempotent |
| `migrations/002_notifications_templates.sql` | Two-tier columns on the existing `notifications` table |

---

## The unit of work: `WorkerTask`

A `WorkerTask` is the single primitive the dispatcher schedules. In-turn sub-agents, bounded-background maintenance pipelines, and long-running autonomous jobs all share the same lifecycle, budget, and checkpoint plumbing.

### 11-state lifecycle

| State | Meaning |
|-------|---------|
| `PENDING` | Submitted to the dispatcher, not yet stepped |
| `PLANNING` | Optional pre-run phase for tasks that need a planning pass before `running` |
| `RUNNING` | Actively stepping; step function called each tick until an outcome changes state |
| `CHECKPOINTING` | Persisting a durable checkpoint; dispatcher transitions to/from this around `checkpoint_every_seconds` |
| `PAUSED_FOR_STATE` | System phase forbids running right now (e.g. conversation started on a `pause_on_conversation=True` task) |
| `PAUSED_FOR_RATE_LIMIT` | `RequestLimiter` refused the step's request-class allocation |
| `PAUSED_FOR_DECISION` | Waiting on an `open_decisions` row to be resolved |
| `PAUSED_FOR_DEPENDENCY` | Blocked on another task's completion |
| `COMPLETED` | Terminal — step returned `outcome="complete"` |
| `FAILED` | Terminal — budget exceeded, step raised, or step returned `outcome="failed"` |
| `CANCELLED` | Terminal — `request_cancellation()` observed before next step |

Terminal states and paused states are frozen sets in `worker_task.py`. The dispatcher owns every transition; tasks themselves never mutate `state` directly.

### Step contract

A step function is `async (WorkerTask, StepContext) -> StepResult`. Each dispatcher tick builds a fresh `StepContext` (limiter ref, cancellation flag, `now()` callable, optional checkpoint callback) and hands it to the task. The returned `StepResult.outcome` is one of:

- `continue` — stay in `RUNNING`, will be stepped again next tick
- `complete` — transition to `COMPLETED`, record `result_summary`
- `paused_for_state` / `paused_for_rate_limit` / `paused_for_decision` / `paused_for_dependency` — pause until the blocker clears
- `failed` — transition to `FAILED` with `error_message`

`StepResult` also carries `request_count_delta`, `agent_turn_count_delta`, `artifacts`, and `progress_marker` for ledger accounting and working-doc updates.

### Three presets

`worker_task.py` exposes three canonical `WorkerTaskConfig` presets. Each preset pins a request class, allowed system phases, budget ceilings, and interruption posture. Callers use `get_preset("bounded_background")` and optionally override individual axes via `dataclasses.replace`.

| Preset | Max duration | Checkpoint cadence | Request class | Allowed phases | Pause on conversation | Pause on topic overlap | Report via |
|--------|--------------|--------------------|----|-|-|-|-|
| `IN_TURN` | 300 s | — | `CONVERSATION` | `CONVERSATION` | false (runs *during* conversation) | false | `return` |
| `BOUNDED_BACKGROUND` | 1800 s | 300 s | `BACKGROUND` | `LIGHT_IDLE`, `DEEP_IDLE`, `WAKE_UP_WINDOW` | false | false | `notification` |
| `LONG_BACKGROUND` | 0 s (unbounded) | 60 s | `BACKGROUND` | `LIGHT_IDLE`, `DEEP_IDLE`, `WAKE_UP_WINDOW` | **true** | **true** | `notification`, `working_doc` |

Budget ceilings (requests/hour, total requests, tokens, cost) and `blocks_parent` also vary — see `worker_task.py` for the authoritative values.

### Checkpoint blob

`Checkpoint` is a small dataclass serialised as JSON into the `worker_tasks.checkpoint_blob` column (not a separate table). It carries `current_step_index`, `plan`, `accumulated_artifacts`, `working_doc_mtime`, `scratch_state`, and `request_count` / `agent_turn_count` for durable resume. Autonomous tasks stash the full `AutonomousState` in `scratch_state` between ticks.

---

## Pipelines — declarative multi-stage workflows

A `Pipeline` is the *declaration* of a multi-stage workflow; a `PipelineInstance` is the run-time record (one per trigger firing or ad-hoc dispatch). A single pipeline can be instantiated many times.

### Validation

`Pipeline.validate()` runs at registration time and enforces:

1. At least one stage
2. Unique stage names
3. Every `depends_on` references a known stage
4. The stage DAG is acyclic (Tarjan-style SCC walk in `_assert_acyclic`)

Cyclic pipelines are rejected. The 12-node autonomous graph — which is genuinely cyclic — sidesteps this by declaring an acyclic stage list mirroring the forward flow and keeping the real cycles inside a single step function. See [`autonomous.md`](../03-agents-autonomous/autonomous.md).

### Policies

Each pipeline carries an `InterruptionPolicy` and a `FailurePolicy`:

| `InterruptionPolicy` | Behaviour |
|----------------------|-----------|
| `PAUSE_ON_CONVERSATION` | Long-running pipelines freeze when the user returns and resume when idle |
| `RUN_TO_COMPLETION` | Bounded-background pipelines keep going even if a conversation starts |
| `ABORT_IMMEDIATELY` | Lowest-priority jobs die the moment the system leaves their allowed phase |

| `FailurePolicy` | Behaviour |
|-----------------|-----------|
| `FAIL_PIPELINE` | One stage fails → whole pipeline fails |
| `CONTINUE_NEXT_STAGE` | One stage fails → next stage still runs |
| `RETRY_STAGE` | One stage fails → retry with `retry_count` budget |

### Registry: code-declared vs runtime

The in-memory `PipelineRegistry` holds both core pipelines (registered at boot by `register_core_pipelines()`) and user-created pipelines (registered via `decompose_and_dispatch`). Runtime pipelines are also persisted to the `runtime_pipelines` table by `engine.register_runtime_pipeline()` so they survive daemon restart. Core pipelines live only in code and do **not** write a row to `runtime_pipelines`.

---

## Triggers — 8 kinds

Triggers are declarative predicates over `TriggerContext` (current time, system phase, event stream, etc.). The dispatcher evaluates them every tick and fires the matching pipelines. Six base kinds plus two composition kinds:

| Kind | Fires when |
|------|------------|
| `INTERVAL` | Every N seconds, optionally gated on `allowed_phases` |
| `EVENT` | A named event fires on the `EventEmitter` (e.g. `SESSION_END`, `MEMORY_STORED`) |
| `CONDITION` | A callable predicate returns True, no more often than `min_interval` |
| `TIME_OF_DAY` | Local wall-clock time crosses a configured `datetime.time` |
| `SEQUENCE_COMPLETE` | Another named pipeline just completed |
| `USER_ACTION` | A supervisor tool named the trigger's `action_name` fired (kept as a diagnostic handle for `user_autonomous_task`) |
| `ANY_OF(t1, t2, ...)` | Composition — fires when any child trigger fires |
| `ALL_OF(t1, t2, ...)` | Composition — fires when every child trigger has fired at least once |

Last-fire timestamps live in the `trigger_state` table so cooldown windows survive daemon restart.

---

## Dispatcher — the single scheduling loop

`Dispatcher` owns the one asyncio loop that drives every task. Every tick (default `0.5s`, configurable) it:

1. Calls `SystemStateMachine.publish_if_changed()` — computes and publishes the current phase, writes a `system_state_log` row on any transition.
2. `_react_to_phase(tasks, phase, now)` — any running task whose `pause_on_conversation=True` is true *and* the phase is now `CONVERSATION` is transitioned to `PAUSED_FOR_STATE` *before* the ready set is computed. Same for tasks whose phase is no longer in their `allowed_states`.
3. Builds the ready set: non-terminal, non-paused, dependencies met, phase allowed.
4. Sorts by `_task_priority(task, now)` — hard request-class priority (conversation > notification > background) plus a **fairness boost**: a pending task waiting longer than its class threshold is bumped one rank up. Thresholds: `FAIRNESS_THRESHOLD_IN_TURN_SECONDS = 30`, `FAIRNESS_THRESHOLD_BACKGROUND_SECONDS = 300`.
5. For each ready task: checks duration budget, request-count budget, acquires a limiter slot for the task's `request_class`, calls the step function with a fresh `StepContext`, applies `StepResult`, records the transition to `work_ledger`, persists the updated task row.

### Crash recovery (spec §7.6 step 4)

On engine start, `dispatcher.start()` calls `task_registry.load_all_non_terminal()`, rehydrates every surviving task into `_live_tasks`, and transitions any task that was in an *active* state (`RUNNING` / `PLANNING` / `CHECKPOINTING`) to `PAUSED_FOR_STATE` with `reason="crash_recovery"`. The next tick then re-evaluates them like any other paused task. Already-paused tasks are left alone.

### Conversation collision

`pause_on_conversation=True` is the contract that makes `LONG_BACKGROUND` tasks polite. The dispatcher double-checks the phase at two points: once in `_react_to_phase` before building the ready set, and again inside `_step_one` just before calling the step function — so a phase transition between those two moments still parks the task cleanly and emits a `TASK_CHECKPOINTED` event with `reason="conversation_began"`.

---

## System state — 7 phases

`SystemStatePhase` is Kora's coarse operational mode, derived from session state + the user's `UserScheduleProfile`:

| Phase | Rule |
|-------|------|
| `CONVERSATION` | A session is currently active |
| `ACTIVE_IDLE` | Less than 5 minutes since the most recent session ended |
| `LIGHT_IDLE` | Less than 1 hour since the most recent session ended |
| `DEEP_IDLE` | More than 1 hour since the most recent session ended |
| `WAKE_UP_WINDOW` | Within 30 minutes before the user's `wake_time` (tz-local) |
| `DND` | Inside the user's `dnd_start`/`dnd_end` window (tz-local, wrap-around supported) |
| `SLEEPING` | Inside the user's `sleep_start`/`sleep_end` window (stricter than DND; checked first) |

All windows are evaluated in `UserScheduleProfile.timezone` via `zoneinfo.ZoneInfo`, so DST transitions are handled correctly — no manual offset math.

The machine is pull-plus-push: `current_phase(now)` is always valid, and `publish_if_changed(now, reason)` emits `SYSTEM_STATE_CHANGED` on the event bus plus writes a `system_state_log` audit row on every real transition. It tracks `_last_phase` (for the sync tick/note_session_start path) separately from `_last_published_phase` (for the async publish path) so a sync tick can never silently swallow a transition the dispatcher would otherwise emit.

The `UserScheduleProfile` also carries the `weekly_review_time` + `weekly_review_weekday` anchors for the `weekly_triage` pipeline and a `hyperfocus_suppression` flag consumed by the notification gate.

---

## RequestLimiter — the 5-hour budget

`RequestLimiter` is the single choke-point for the provider-API budget every non-conversation thing consumes.

- **Window**: 5 hours rolling (`WINDOW_SECONDS = 5 * 3600`)
- **Cap**: 4500 requests total (`WINDOW_CAPACITY`)
- **Reserves**: 300 for `CONVERSATION`, 100 for `NOTIFICATION` (`CONVERSATION_RESERVE`, `NOTIFICATION_RESERVE`)
- **Refusal rules**: `CONVERSATION` requests *never* fail — the reserve exists *for* them, and the limiter still records them so the window stays accurate. `NOTIFICATION` fails if `total + count > capacity - conversation_reserve`. `BACKGROUND` fails if `total + count > capacity - conversation_reserve - notification_reserve`.

Every acquisition is written to `request_limiter_log`. On engine start, `replay_from_log()` rehydrates the in-memory window from the last 5 hours of rows — crash recovery is "just read the table." `LimiterSnapshot` exposes `remaining_for(cls)` so callers can see the effective budget per class.

Concurrency is serialised through an `asyncio.Lock` because several background tasks can hit `acquire()` in the same tick.

---

## NotificationGate — two-tier delivery

`NotificationGate` is the single chokepoint for outbound messages. Anyone that wants to notify the user calls `gate.send_llm(...)` (for LLM-generated text) or `gate.send_templated(...)` (for zero-request deterministic messages). The gate:

1. Resolves the tier (`llm` vs `templated`).
2. Honours a manual `suppress_until(deadline, reason)` deadline. `bypass_dnd=True` does **not** override manual suppression.
3. **Hyperfocus suppression is unconditional.** If `hyperfocus_active_fn()` returns True and the user profile has `hyperfocus_suppression=True` (default), the notification is dropped regardless of `bypass_dnd`. Hyperfocus is a separate axis from DND and cannot be overridden by the caller.
4. **DND window** — if the profile has `dnd_start`/`dnd_end` and current local time falls inside, the notification is queued with `reason="dnd_queued"`. Templated entries with `bypass_dnd=True` skip this check.
5. RSD filter hook — currently identity, the placeholder for a future wording sanitiser. The real RSD softening still lives in the supervisor system prompt today.
6. Routes delivery: `WEBSOCKET` (live session), `TURN_RESPONSE` (caller appends to the current reply), `INBOX` (working-doc writer handles the file), `QUEUE` (drain later).
7. Records the delivery in the `notifications` table with `delivery_tier` / `template_id` / `template_vars` / `reason` columns (schema extended by `002_notifications_templates.sql`). The writer gracefully degrades when the columns are missing — full insert → minimal insert → silent skip — so orchestration-only unit tests still work.

Templated messages cost **zero** provider requests, so even when the limiter is exhausted Kora can still tell the user "I'm catching up on my window."

### Templates

`TemplateRegistry` reads YAML templates from `<memory_root>/.kora/templates/`, writes default templates the first time, and supports `reload_if_changed()` for hot-reload. Templates carry `priority`, `bypass_dnd`, and a Jinja-style variable list. `RenderedTemplate` is what the gate gets back.

---

## Working documents — `<memory_root>/Inbox/`

Every pipeline instance writes a markdown working document while it runs. Content goes into `<memory_root>/Inbox/<pipeline-instance-id>.md`; this is the human-readable surface the user and the supervisor both read.

`WorkingDocStore`:

- Holds an `asyncio.Lock` **per instance path**, not one global lock — concurrent pipelines do not block each other.
- Writes are atomic: temp file + rename.
- YAML frontmatter encodes metadata; sections are `## Plan`, `## Progress`, `## Findings`, `## Decisions`, etc. Parsed by a light regex reader into a `WorkingDocHandle` so supervisor tools can return structured slices.
- A `status: done` sentinel in frontmatter means the task is finished and the doc is read-only.
- `ensure_inbox()` at engine start creates the directory if missing.

Working docs are the canonical "what is this background task actually doing" surface. The supervisor `get_working_doc` tool returns either the whole document or a single section.

---

## Open decisions

`OpenDecisionsTracker` is the SQL-backed version of "things the user said they'd decide later". It writes to the `open_decisions` table (`id`, `topic`, `posed_at`, `posed_in_session`, `context`, `status`, `resolved_at`, `resolution`) and emits `OPEN_DECISION_POSED` on the event bus when a new entry is recorded. `get_pending(limit)` returns the list the supervisor resurfaces in later turns. The `record_decision` supervisor tool is the public surface.

---

## Work ledger

`WorkLedger` writes every state transition, every rate-limit rejection, and every pipeline lifecycle event to the append-only `work_ledger` table with an `event_type`, optional `pipeline_instance_id` / `worker_task_id` / `trigger_name`, a free-form `reason`, and a JSON metadata blob. The ledger is the audit trail you grep when "why did this task pause?" is a real question.

---

## The 20 core pipelines

`core_pipelines.py` declares the 20 pipelines from spec §4.3. Current code wires real step handlers for:

- `post_session_memory` (`extract`, `consolidate`, `dedup`, `entities`, `vault_handoff`)
- `post_memory_vault` (`reindex`, `structure`, `links`, `moc_sessions`)
- `weekly_adhd_profile`
- `user_autonomous_task`
- `wake_up_preparation`
- `continuity_check`
- `proactive_pattern_scan`
- `anticipatory_prep`
- `proactive_research`
- `article_digest`
- `follow_through_draft`
- `contextual_engagement`
- `commitment_tracking`
- `stuck_detection`
- `weekly_triage`
- `draft_on_observation`
- `connection_making`

`in_turn_subagent` is still wired to the generic stub. `session_bridge_pruning` and `skill_refinement` are registered housekeeping placeholders that complete with no-op summaries. The old “17 stubs” statement is obsolete.

| # | Pipeline | Preset | Trigger(s) | Step function |
|---|----------|--------|------------|---------------|
| 1 | `post_session_memory` | `bounded_background` | `EVENT(SESSION_END)` | real 5-stage Memory Steward |
| 2 | `post_memory_vault` | `bounded_background` | `ANY_OF(SEQUENCE_COMPLETE(post_session_memory), INTERVAL(1800s, deep_idle))` | real 4-stage Vault Organizer |
| 3 | `weekly_adhd_profile` | `bounded_background` | `TIME_OF_DAY(02:00)` | real ADHD profile refinement |
| 4 | `user_autonomous_task` | `long_background` | `USER_ACTION(decompose_and_dispatch)` | **real** — `autonomous.pipeline_factory.get_autonomous_step_fn()` |
| 5 | `in_turn_subagent` | `in_turn` | `USER_ACTION(decompose_and_dispatch_in_turn)` | stub |
| 6 | `wake_up_preparation` | `bounded_background` | `TIME_OF_DAY(06:15)` | real proactive handler |
| 7 | `continuity_check` | `bounded_background` | `INTERVAL(300s)` / medication event | real reminder/proactive handler |
| 8 | `proactive_pattern_scan` | `bounded_background` | `ANY_OF(INSIGHT_AVAILABLE, EMOTION_SHIFT_DETECTED, MEMORY_STORED, INTERVAL(1800s, idle))` | real proactive handler |
| 9 | `anticipatory_prep` | `long_background` | `ANY_OF(INTERVAL(1200s, deep_idle), TIME_OF_DAY(06:15))` | real proactive handler |
| 10 | `proactive_research` | `long_background` | `USER_ACTION(dispatch_research)` | real proactive handler; latest short acceptance leaves full proof red |
| 11 | `article_digest` | `long_background` | `CONDITION(min_interval=3600s)` | real proactive handler |
| 12 | `follow_through_draft` | `bounded_background` | `EVENT(USER_STATED_INTENT)` | real proactive handler |
| 13 | `contextual_engagement` | `bounded_background` | `ANY_OF(EMOTION_SHIFT_DETECTED, TASK_LINGERING, OPEN_DECISION_POSED, LONG_FOCUS_BLOCK_ENDED)` | real proactive handler |
| 14 | `commitment_tracking` | `bounded_background` | `TIME_OF_DAY(01:00)` | real proactive handler |
| 15 | `stuck_detection` | `bounded_background` | `INTERVAL(21600s, idle)` | real proactive handler |
| 16 | `weekly_triage` | `bounded_background` | `TIME_OF_DAY(09:00)` | real proactive handler |
| 17 | `draft_on_observation` | `bounded_background` | `EVENT(USER_STATED_NEED)` | real proactive handler |
| 18 | `connection_making` | `bounded_background` | `TIME_OF_DAY(03:00)` | real proactive handler |
| 19 | `session_bridge_pruning` | `bounded_background` | `INTERVAL(3600s, deep_idle)` | housekeeping no-op placeholder |
| 20 | `skill_refinement` | `bounded_background` | `TIME_OF_DAY(03:00)` | skill-review no-op placeholder |

The stub path remains for `in_turn_subagent` and for future runtime-created work, but the core catalogue is no longer primarily stubbed.

---

## OrchestrationEngine surface

`OrchestrationEngine` is the object the DI container hands to callers. The engine bundles every sub-component into one cohesive surface; the rest of Kora talks to one object. Key methods:

| Method | Purpose |
|--------|---------|
| `start()` | Init schema, replay limiter log, rehydrate tasks (crash-recover `RUNNING`/`PLANNING`/`CHECKPOINTING`), run autonomous migration, install autonomous runtime context, start dispatcher |
| `stop(graceful=False)` | Stop the dispatcher; graceful waits for in-flight steps |
| `register_pipeline(p)` | Register a code-declared pipeline (no persistence) |
| `register_runtime_pipeline(p, created_by_session=...)` | Register + persist to `runtime_pipelines` — used by the `decompose_and_dispatch` tool |
| `start_pipeline_instance(name, goal=..., working_doc_path=..., parent_session_id=...)` | Instantiate a pipeline; writes a `PIPELINE_STARTED` ledger row |
| `dispatch_task(goal=..., system_prompt=..., step_fn=..., preset=..., ...)` | Ad-hoc standalone task (not part of a pipeline); supervisor uses this for in-turn sub-agents |
| `list_tasks(relevant_to_session=..., user_message=...)` | Supervisor turn-start surfacing. Four-condition OR from spec §13.1: session-owned, running system pipeline (or completed within 10 min), unacknowledged terminal, or topic overlap in the 0.45–0.70 ambiguous band |
| `get_task(task_id)` / `get_task_progress(task_id)` | Read-only accessors for the supervisor tools |
| `get_working_doc(task_id)` | Return a `WorkingDocHandle` for the task's pipeline instance |
| `cancel_task(task_id, reason=...)` | Request cancellation, write ledger row |
| `modify_task(task_id, goal=..., system_prompt=...)` | Patch a running task — the step function reads fields each tick so the change is visible next step |
| `acknowledge_task(task_id)` | Turn-end cleanup: set `result_acknowledged_at` so the task stops surfacing on future turns |
| `notify(template_id=...|text=..., priority=..., via=..., template_vars=..., metadata=...)` | Pass-through to `NotificationGate` — exactly one of `template_id` / `text` |
| `record_open_decision(topic, context, posed_in_session=...)` | SQL-backed decision tracking |
| `subscribe_event(event_type, handler)` | Thin wrapper over the event emitter |
| `limiter_snapshot()` | Diagnostics: total / remaining / per-class breakdown |
| `current_phase()` | Current `SystemStatePhase` as string |
| `tick_once()` / `run_task_to_completion(task)` | Test helpers that drive the dispatcher one step at a time |

### Wiring

The engine is constructed in `kora_v2/core/di.py` (`Container.initialize_orchestration` → sets `container._orchestration_engine`, exposed as the `container.orchestration_engine` property). The daemon constructs the engine **before** `create_app()` so the engine is available by the time event subscriptions are set up; `register_core_pipelines(engine)` runs in the same block. `engine.start()` runs inside the FastAPI startup event, after the WebSocket broadcaster is wired; `engine.stop(graceful=True)` runs in shutdown. See `daemon/server.py` for the wiring block.

---

## list_tasks — the turn-start surfacing rule

Every turn the supervisor calls `get_running_tasks` (which calls `engine.list_tasks(...)`) and uses the result to decide whether the user is asking about something Kora is already working on. The four-condition OR from spec §13.1:

1. **Session-owned**: `parent_session_id == relevant_to_session`.
2. **System pipeline currently active**: `parent_session_id IS NULL` and the pipeline instance is `pending`/`running`/`paused`, or `completed` within the last 10 minutes. System pipelines are relevant to whatever session is active when they land.
3. **Unacknowledged terminal**: `state ∈ {COMPLETED, FAILED}` and `result_acknowledged_at IS NULL`. The supervisor marks these via `acknowledge_task` at turn-end so they stop resurfacing.
4. **Topic overlap**: if `user_message` is non-empty, include tasks whose `goal`/`stage_name` scores in the `0.45 ≤ score ≤ 0.70` ambiguous band against the user message. Scores ≥ 0.70 already pause at the dispatcher level and are in the list by construction.

Callers that just want "everything live" pass both filter args as `None`.

---

## Preservation contract §17.7

When the orchestration migration replaces legacy subsystems, spec §17.7 pins a 10-row table of behaviours that **must** survive. These are enforced by `tests/integration/orchestration/test_preservation_contract.py`:

1. 12-value `AutonomousState.status` enum — unchanged, still the node transition driver
2. Topic-overlap pause threshold at 0.70 — now surfaced as a `paused_for_state` step outcome
3. 5-axis budget enforcer — currently checked before `plan`, `execute_step`, and `replan`
4. Reflect heuristic: avg step confidence <0.35 → `replan`
5. Same-node watchdog: 5 repeats of a non-cyclic node → `failed`
6. In-memory `DecisionManager` with `auto_select` / `never_auto` policies for pause/resume
7. `safe_resume_token` + `elapsed_seconds` preserved in `scratch_state` across dispatcher checkpoints
8. Idempotent legacy `autonomous_checkpoints` migration (guarded by a marker row in `work_ledger`)
9. Keyword-based routine classification in `classify_request`
10. The 12-node sequence — audit surface, parity test — even though only the first stage is ever dispatched

See [`autonomous.md`](../03-agents-autonomous/autonomous.md) for how the step function actually implements these.

---

## New event types

Phase 7.5 added 13 new `EventType` values to `kora_v2/core/events.py`:

| Event | Fired from |
|-------|------------|
| `TASK_CHECKPOINTED` | Dispatcher — on conversation pause, on explicit checkpoint |
| `TASK_COMPLETED` | Dispatcher `_emit_transition` on `COMPLETED` |
| `TASK_FAILED` | Dispatcher `_emit_transition` on `FAILED` |
| `PIPELINE_COMPLETE` | Engine when the last stage terminates |
| `INSIGHT_AVAILABLE` | Memory subsystem — consumed by `proactive_pattern_scan` |
| `SYSTEM_STATE_CHANGED` | `SystemStateMachine.publish_if_changed` on every transition |
| `RATE_LIMIT_APPROACHING` | `RequestLimiter` when remaining budget crosses a threshold |
| `OPEN_DECISION_POSED` | `OpenDecisionsTracker.record` |
| `TASK_LINGERING` | Contextual engagement detector |
| `LONG_FOCUS_BLOCK_ENDED` | Life/ADHD focus-block tracker |
| `USER_STATED_INTENT` | Signal scanner — consumed by `follow_through_draft` |
| `USER_STATED_NEED` | Signal scanner — consumed by `draft_on_observation` |
| `MEMORY_SOFT_DELETED` / `ENTITY_MERGED` | Memory subsystem write pipeline |

Memory and emotion events (`MEMORY_STORED`, `EMOTION_STATE_ASSESSED`, `EMOTION_SHIFT_DETECTED`) were already present but are now consumed by pipeline triggers — see the 20-pipeline table above.

---

## Database tables (created by `migrations/001_orchestration.sql`)

All eight tables live in `data/operational.db` alongside the existing core + life-domain schema:

| Table | Purpose |
|-------|---------|
| `pipeline_instances` | Pipeline runs (active + historical) |
| `worker_tasks` | 11-state FSM rows; `checkpoint_blob` JSON column for durable resume |
| `work_ledger` | Append-only audit trail |
| `trigger_state` | Per-trigger last-fire / next-eligible timestamps |
| `request_limiter_log` | Rows for the 5-hour sliding window |
| `system_state_log` | Phase-transition audit |
| `open_decisions` | User-posed decisions awaiting resolution |
| `runtime_pipelines` | User-created pipeline declarations |

`init_orchestration_schema(db_path)` runs on every engine start. All `CREATE` statements are idempotent.

Note: `spec §16.1`'s SQL block has a stale `NOT NULL` on `worker_tasks.pipeline_instance_id` that must be ignored — the dataclass and the unit tests rely on it being optional (standalone tasks have `pipeline_instance_id = None`).

---

## Non-obvious behaviours

1. **`pause_on_conversation` is checked twice per tick.** Once in `_react_to_phase` (before the ready set is built) and once inside `_step_one` (just before calling the step function). A phase transition between those two points still parks the task cleanly.
2. **Conversation requests never fail the limiter.** The reserve exists *for* them. Even over-cap conversation requests are recorded so the sliding window stays accurate.
3. **Hyperfocus trumps `bypass_dnd`.** `bypass_dnd=True` only overrides the DND window. Hyperfocus suppression is a separate, unoverridable axis on the notification gate.
4. **Core pipelines never write to `runtime_pipelines`.** Code-declared pipelines live only in the in-memory `PipelineRegistry`. Only user-created pipelines from `decompose_and_dispatch` are persisted.
5. **`register_runtime_pipeline` updates the in-memory registry before writing the SQL row.** A crash mid-write leaves an in-memory pipeline that is lost at restart but no stale DB row.
6. **Stage names are acyclic but steps can cycle.** `Pipeline.validate()` rejects cycles in the stage DAG; the 12-node autonomous graph's cycles live inside the step function, not in the stage list.
7. **`checkpoint_blob` is a column, not a table.** Serialised `Checkpoint` JSON on `worker_tasks`, small (<10KB typical) so writes stay cheap.
8. **`list_tasks(None, None)` returns everything live.** The four-condition filter is only applied when at least one of the filter args is non-None — this is the "give me everything" convenience path.
9. **System state log records the sync tick and the async publish separately.** `_last_phase` (sync) and `_last_published_phase` (async) are distinct so a sync `note_session_start` cannot swallow a transition the dispatcher would otherwise emit.
10. **The autonomous migration is guarded by a marker row in `work_ledger`.** Reruns are no-ops; the engine starts idempotently every boot.

---

## Cross-references

- Daemon wiring: [`daemon.md`](daemon.md) → "Orchestration engine lifecycle"
- Supervisor tools that talk to the engine: [`graph.md`](graph.md) → `SUPERVISOR_TOOLS` table
- Autonomous step function internals: [`../03-agents-autonomous/autonomous.md`](../03-agents-autonomous/autonomous.md)
- ADHD consumption of `NotificationGate`: [`../05-life-adhd/adhd.md`](../05-life-adhd/adhd.md)
- `OpenDecisionsTracker` used by the life surface: [`../05-life-adhd/life.md`](../05-life-adhd/life.md)
- `RequestLimiter` consumer side: [`../04-conversation-llm/llm.md`](../04-conversation-llm/llm.md)
