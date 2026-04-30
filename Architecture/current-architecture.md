# Kora Current Architecture

Last verified: 2026-04-28 against the dirty `main` worktree at `d056894`.
GUI/client refresh: 2026-04-29 against the working desktop/browser dev surface.

This is the public, source-backed architecture snapshot for Kora V2. It was checked against live `kora_v2/` code, recent acceptance artifacts under `/tmp/claude/kora_acceptance`, the Life OS manual probes run during the pivot implementation, and parallel subagent audits. The older deep-dive pages in this folder remain useful, but this file is the current entry point when a page disagrees with live code.

## Verification Boundary

- Active runtime package: `kora_v2/`.
- Console entrypoint: `kora = "kora_v2.daemon.launcher:main"`.
- Python requirement: `>=3.12`.
- Public repo docs should not depend on gitignored `Documentation/`, `docs/`, `_KoraMemory/`, or `data/` paths as if they ship with the repo.
- Current worktree is dirty. This file treats the checked-out code and generated acceptance traces as implementation truth without reverting or overwriting unrelated changes.

## Product Center

Kora's current product center is Life OS: local-first day-to-day life management for users whose days are affected by ADHD, anxiety, autism/sensory needs, low energy, burnout, chronic overwhelm, avoidance, or executive dysfunction.

The core Life OS loop is:

```text
Plan Today -> Confirm Reality -> Repair The Day -> Bridge Tomorrow
```

Coding, research, writing, browser, workspace, and vault behavior remain useful capability packs, but they are no longer the main acceptance target. Public docs should describe them as optional capability surfaces unless they directly support Life OS behavior.

## Acceptance Evidence

Three acceptance surfaces matter:

- Life OS acceptance proof collector: `tests/acceptance/life_os.py`, rendered into `tests/acceptance/_report.py`. It is DB/event backed: tool calls alone do not make a scenario green.
- Manual Life OS probe from the 2026-04-28 implementation pass proved fresh DB init, service resolution, tool registration, support profile bootstrap, day plan creation, reality correction, load assessment, repair actions (`shrink_task`, `add_transition_buffer`), stabilization, nudge suppression, context-pack artifact, future-bridge artifact, crisis preemption, and domain-event restart persistence.
- Focused tests from the same pass reported `63 passed` across Life OS schema, service, tool, support/safety, and acceptance proof tests.
- Desktop GUI smoke from 2026-04-29 proved the Vite/browser renderer can discover the local daemon, load the main screens without stuck loading or fetch/CORS failures, and send/receive chat through the actual global chat textarea and daemon WebSocket. The desktop API contract is covered by `tests/unit/test_desktop_api.py`.

Historical/general acceptance surfaces still matter, but they should be read as pre-pivot capability evidence:

- Latest local artifact: `/tmp/claude/kora_acceptance/acceptance_output/acceptance_report.md`, generated `2026-04-28T17:35:27Z`, is a short Day 1 run with `4` user turns, `44/69` active items satisfied, and `6` partial. It proves the current tool/pipeline shape but is not a full 3-day pass.
- Latest remembered full clean run from 2026-04-26 reported `67/69` active items satisfied, deferred item `1`, and still-red items `48` and `55`. Treat that as prior full-run evidence, not proof that the current dirty worktree is fully green.

The public architecture should therefore say what is implemented and wired, but it should not claim current full Life OS week acceptance is green until the harness is remade around the new Life OS product focus and run end-to-end.

## System Shape

Kora is a local-first daemon with two working clients: the Electron/React desktop GUI and the Rich CLI.

```text
Desktop GUI (apps/desktop) or Rich CLI
  -> FastAPI daemon on 127.0.0.1
  -> per-session turn queue
  -> GraphTurnRunner
  -> LangGraph supervisor
  -> tools, memory, workers, capabilities, LLM provider
  -> WebSocket stream back to client

Desktop GUI REST view-models:
  apps/desktop -> /api/v1/desktop/* -> kora_v2/desktop/service.py

OrchestrationEngine runs beside the turn path:
  TriggerEvaluator -> PipelineRegistry -> Dispatcher -> WorkerTask FSM
  WorkLedger / RequestLimiter / NotificationGate / WorkingDocStore
```

The active supervisor graph topology is:

```text
receive -> build_suffix -> think -> tool_loop -> think ... -> synthesize -> END
```

There are no current graph nodes named `plan`, `act`, `review`, or `emit`; those names are stale narrative aliases from older docs.

## Runtime Core

`kora_v2/core/` owns settings, DI, DB setup, events, logging, common models, and retry/error types.

`Settings` currently includes LLM, memory, agents, quality, daemon, notifications, autonomous, orchestration, MCP, security, vault, browser, and planning sections. The default daemon host is local-only and the configured default port is `8765`; `port=0` remains a supported configurable dynamic-port mode, not the normal default.

`kora_v2/daemon/` owns:

- launcher and lockfile/token handling,
- FastAPI REST/WebSocket server,
- authenticated desktop view-model router mounting `/api/v1/desktop/*`,
- auth relay,
- session manager/bridge,
- daemon lifecycle wiring for memory, workers, phase 4 services, checkpointer, and orchestration.

`kora_v2/desktop/` owns daemon-side desktop view-model assembly. It hides direct SQLite, memory, lockfile, and process details behind stable Pydantic contracts for Today, Calendar, Medication, Routines, Repair, Memory, Autonomous, Integrations, Settings, and Runtime-facing desktop screens.

`kora_v2/runtime/` owns:

- `GraphTurnRunner`,
- SQLite checkpointer lifecycle,
- runtime inspector,
- artifact/store helpers,
- protocol models.

LangGraph checkpoints state after graph node execution. Turn tracing and inspection are diagnostic surfaces, not the source of truth for memory or orchestration state.

## Supervisor And Tools

`kora_v2/graph/dispatch.py` currently declares 11 supervisor tools:

- `dispatch_worker`
- `recall`
- `search_web`
- `fetch_url`
- `decompose_and_dispatch`
- `get_running_tasks`
- `get_task_progress`
- `get_working_doc`
- `cancel_task`
- `modify_task`
- `record_decision`

Skill-gated registry tools are added from `ToolRegistry`, and dotted capability actions are added from capability packs through `capability_bridge.py`.

The current registered Python tool surface is 47 tools after worker initialization:

- Calendar: `create_calendar_entry`, `query_calendar`, `update_calendar_entry`, `delete_calendar_entry`, `sync_google_calendar`
- Filesystem: `write_file`, `read_file`, `create_directory`, `list_directory`, `file_exists`
- Life management: `log_medication`, `log_meal`, `create_reminder`, `query_reminders`, `quick_note`, `start_focus_block`, `end_focus_block`, `query_medications`, `query_meals`, `query_focus_blocks`, `log_expense`, `query_expenses`, `query_quick_notes`
- Life OS: `create_day_plan`, `confirm_reality`, `correct_reality`, `assess_life_load`, `repair_day_plan`, `decide_life_nudge`, `record_nudge_feedback`, `create_context_pack`, `bridge_tomorrow`, `set_support_profile_status`, `check_crisis_boundary`
- Planning/tasks: `draft_plan`, `update_plan`, `day_briefing`, `create_item`, `complete_item`, `defer_item`, `query_items`, `life_summary`
- Routines: `create_routine`, `list_routines`, `start_routine`, `advance_routine`, `routine_progress`

`DomainVerbResolver` is only a hint layer. It still contains stale suggestions such as `store_memory`, `update_item`, and `create_quick_note`; those names should be treated as routing debt, not public tool availability.

## Memory And Context

Canonical memory root is `settings.memory.kora_memory_path`, defaulting to `~/.kora/memory`. The acceptance harness can override it, and the latest local run used `/tmp/claude/kora_acceptance/memory`.

Do not describe repo-local `_KoraMemory/` as the only canonical runtime root in public docs. It can exist locally, but the runtime root is configurable.

Memory stack:

- `FilesystemMemoryStore`: markdown notes with YAML frontmatter.
- `ProjectionDB`: `data/projection.db`, FTS5, vector rows, entity links, and user-model fact indexes.
- `WritePipeline`: dedup check, entity extraction, filesystem write, embedding, projection indexing, entity linking.
- Retrieval: hybrid vector/BM25 over active records.

Projection memory now has soft-delete/consolidation state. Active reads filter for active rows; consolidation and dedup preserve tombstone metadata such as `status`, `consolidated_into`, `merged_from`, and `deleted_at` instead of treating every merge as a hard delete.

`ContextEngine` reads operational state and ADHD profile data to produce `DayContext` and `LifeContext` for the supervisor prompt.

## Orchestration

`kora_v2/runtime/orchestration/` replaced the deleted `BackgroundWorker`, but the current implementation is no longer a mostly-stub Phase 7.5 shell.

Core services:

- `OrchestrationEngine`: lifecycle, registries, worker task creation, pipeline start, notification gate, working docs.
- `TriggerEvaluator`: separate loop for interval, event, condition, time-of-day, user-action, sequence, and composite triggers. It records `TRIGGER_FIRED` and updates `trigger_state`.
- `Dispatcher`: steps ready `WorkerTask` rows through the FSM.
- `RequestLimiter`: 5-hour window, background/notification/conversation classes, persisted replay.
- `NotificationGate`: single outbound notification chokepoint with templated and LLM paths, DND/hyperfocus handling, delivery logging.
- `WorkingDocStore`: atomic markdown working docs under `<memory_root>/Inbox`.
- `WorkLedger`: append-only audit rows for pipeline/task transitions.
- `OpenDecisionsTracker`: durable decision table for conversation/orchestration decisions, though autonomous decision-pauses still need careful verification.

There are 20 code-declared core pipelines:

| Pipeline | Current step status |
|---|---|
| `post_session_memory` | real 5-stage Memory Steward pipeline: extract, consolidate, dedup, entities, vault_handoff |
| `post_memory_vault` | real 4-stage Vault Organizer pipeline: reindex, structure, links, moc_sessions |
| `weekly_adhd_profile` | real ADHD profile refinement handler |
| `user_autonomous_task` | real autonomous step function |
| `in_turn_subagent` | still stubbed/no-op |
| `wake_up_preparation` | real proactive handler |
| `continuity_check` | real proactive/reminder handler |
| `proactive_pattern_scan` | real proactive handler |
| `anticipatory_prep` | real proactive handler |
| `proactive_research` | real proactive handler, but acceptance still leaves proactive research unproven in the latest short run |
| `article_digest` | real proactive handler |
| `follow_through_draft` | real proactive handler |
| `contextual_engagement` | real proactive handler |
| `commitment_tracking` | real proactive handler |
| `stuck_detection` | real proactive handler |
| `weekly_triage` | real proactive handler |
| `draft_on_observation` | real proactive handler |
| `connection_making` | real proactive handler |
| `session_bridge_pruning` | registered housekeeping no-op placeholder |
| `skill_refinement` | registered skill-review no-op placeholder |

The old claim that 17 of 20 pipelines are stubs is obsolete.

## Autonomous And Workers

`kora_v2/autonomous/` still contains the 12-node autonomous state machine, but `classify_request()` is an initializer, not one of the canonical node names. The current `AUTONOMOUS_NODES` sequence runs from `plan` through terminal states.

Autonomous execution is wrapped by the `user_autonomous_task` orchestration pipeline and persists progress in `worker_tasks` / `pipeline_instances` plus checkpoint blobs. Legacy `autonomous_checkpoints` exists for migration/back-compat, not as the live source of truth.

`AutonomousState.status` currently has 12 values, not 14.

Budget enforcement in the current step function runs before `plan`, `execute_step`, and `replan`, not before every node.

Worker harnesses:

- Planner and reviewer are typed worker harnesses.
- Executor has deterministic filesystem paths, LLM filesystem paths, structured output, and a research/tool path that can expose `search_web`, `fetch_url`, and browser capability tools.
- Direct `dispatch_worker` resolves and calls a worker synchronously in the supervisor tool loop. `IN_TURN` `WorkerTask` envelopes are used for orchestration pipelines such as `decompose_and_dispatch(..., in_turn=True)`, not for every direct worker dispatch.

## Skills And Capabilities

There are 14 skill YAML files under `kora_v2/skills/`.

There are 24 Python files under `kora_v2/capabilities/`, across four packs:

- `workspace`
- `browser`
- `vault`
- `doctor`

Browser, workspace, and vault each include `__init__.py` plus implementation modules; the total count remains 24. `doctor` is scaffolding and reports unimplemented health.

Capabilities are Python action packs with policy and structured failure surfaces. Skills are YAML guidance/tool-gating definitions. A skill can expose capability-style names, but capability execution happens through `graph/capability_bridge.py`.

Current acceptance health shows the packs are visible, but workspace/browser/vault are unconfigured in the latest local run and doctor is unimplemented. Public docs should say visible/configurable, not production-ready.

`kora_v2/routing/` has no Python files. Routing lives in the supervisor graph, autonomous graph/pipeline factory, worker resolution, tools, and capability bridge.

## LLM, MCP, Clients, Emotion, Quality

`kora_v2/llm/` has one real provider abstraction implementation for MiniMax via Anthropic-compatible API. The default model string is `MiniMax-M2.7-highspeed`. `ClaudeCodeDelegate` is a subprocess shim for the `claude` CLI, not a provider subclass.

`kora_v2/mcp/manager.py` supports server definitions, lazy startup, JSON-RPC handshakes, and tool calls. Automatic self-healing is overstated in older docs: `_restart_with_backoff()` exists, but `call_tool()` does not invoke it as the normal failure path.

The CLI has a first-run wizard code path, but current acceptance defers first-run onboarding. Public docs should distinguish implemented code from acceptance-proven behavior.

The desktop GUI lives in `apps/desktop/`. In Electron it discovers the daemon through preload-backed lockfile/token access; in browser dev mode Vite exposes local-only discovery endpoints so the renderer can run at `http://127.0.0.1:5173/`. Both GUI and CLI use the daemon WebSocket for chat.

Emotion remains two-tier PAD assessment: fast rules plus LLM fallback/cache. Notification-related emotion handling should reference `runtime/orchestration/notifications.py`, not a nonexistent `notification_gate.py`.

Quality collection currently records samples in memory on the supervisor path. Persistence helpers exist, but older docs overstate per-turn persistence as a guaranteed runtime behavior.

## Life OS, Support, Safety, ADHD, Routines, Reminders

`kora_v2/adhd/` owns profile, protocol, and ADHD module behavior.

The Life OS pivot adds first-class services under `kora_v2/life/`, `kora_v2/support/`, and `kora_v2/safety/`:

- `life/domain_events.py`: append-only product-domain proof events.
- `life/ledger.py`: Life Event Ledger for confirmed, inferred, corrected, rejected, and tool-generated reality.
- `life/day_plan.py`: versioned day plans and day-plan entries.
- `life/load.py`: Life Load Meter with explainable factors and user correction.
- `life/repair.py`: Repair The Day engine, repair actions, and day-plan revision application.
- `life/proactivity_policy.py`: durable nudge send/defer/suppress/queue decisions and feedback.
- `life/stabilization.py`: Stabilization Mode state and optional-work suppression.
- `life/context_packs.py`: admin/anxiety/sensory context packs plus memory-root markdown artifacts.
- `life/future_bridge.py`: Future Self Bridge rows and artifacts.
- `life/trusted_support.py`: trusted-support export drafts and social/sensory load helper.
- `support/`: support profile registry, bootstrap service, and ADHD/anxiety/autism-sensory/low-energy/burnout runtime modules.
- `safety/crisis.py`: CrisisSafetyRouter and durable safety-boundary records.

Older life infrastructure remains active:

- `routines.py`: `RoutineManager`, routine templates, routine sessions, progress.
- `reminders.py`: `ReminderStore`, due reminder polling, delivery marking, dismissal, recurrence rescheduling.

Life tools include medication, meals, reminders, focus blocks, expenses, quick notes, Life OS loop tools, planning/task helpers, and five routine tools:

- `create_routine`
- `list_routines`
- `start_routine`
- `advance_routine`
- `routine_progress`

Reminders are no longer stored-only. `create_reminder` writes legacy and Phase 8e columns and can kick `continuity_check` when the due time is inside the scan window. `continuity_check_step` polls due reminders, sends through `NotificationGate`, then marks or reschedules them.

The reminder schema has legacy columns plus Phase 8e additions: `due_at`, `repeat_rule`, `source`, `delivered_at`, `dismissed_at`, and `metadata`, with a due-reminder index.

The Life OS operational schema now includes `day_plans`, `day_plan_entries`, `life_events`, `domain_events`, `load_assessments`, `plan_repair_actions`, `nudge_decisions`, `nudge_feedback`, `support_mode_state`, `context_packs`, `future_self_bridges`, `support_profiles`, `support_profile_signals`, and `safety_boundary_records`.

Latest local general acceptance proves life tool calls and several proactive pipelines, but it does not prove routine-created reminders: the short report has reminders count `0` and item `66` red. The new Life OS acceptance collector proves implementation surfaces independently, but the full week-long Life OS harness still needs to replace the old product-centered scenario.

## Data Stores

| Store | Path | Role |
|---|---|---|
| Runtime memory root | `settings.memory.kora_memory_path`, default `~/.kora/memory` | Canonical markdown memory and working docs |
| Working docs | `<memory_root>/Inbox/*.md` | Pipeline working documents with YAML frontmatter |
| Projection DB | `data/projection.db` | FTS/vector/entity projection for memory retrieval |
| Operational DB | `data/operational.db` | Life OS tables, sessions, pipelines, worker tasks, ledger, decisions, limiter, notifications |
| Checkpointer DB | session/runtime SQLite | LangGraph checkpoints |
| Lock/token/logs | `data/` | Daemon process state and local diagnostics |

## Public Documentation Rules

- Prefer this file and live code over older Phase 7.5 language.
- Do not claim all acceptance is green from the current `/tmp` report.
- Do not treat coding/research/writing checks as the Life OS product gate.
- Do not claim the week-long Life OS harness is complete; the DB-backed collector exists, but the main scenario still needs to be remade around a real user's messy week.
- Do not call Phase 8 memory/vault/proactive/reminder handlers stubs when real handler functions are wired.
- Do not claim `_KoraMemory/` is the only runtime memory root.
- Do not claim 23 or 36 tools, 10 supervisor tools, Python 3.11 support, or the `MiniMax-M2.7` default model.
- Do not describe reminders as stored-only.
- Do not describe MCP as automatically self-healing.
