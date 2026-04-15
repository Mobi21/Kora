# 05 — Life Engine & ADHD Cluster

Kora is a local-first, ADHD-aware AI assistant. The life engine and ADHD subsystems are its most distinctive features: they transform a general-purpose LLM assistant into a daily operating system that adapts in real time to the user's neurobiology. Every Kora session is aware of the user's medications, energy level, focus state, scheduled routines, and cognitive patterns — and the model's language output is filtered to remove phrasing that triggers rejection-sensitive dysphoria (RSD). This is not a bolt-on accommodation; it is baked into the supervisor graph and the context prefix that prefaces every turn.

## What this cluster produces

| User-visible outcome | Mechanism |
|---|---|
| "How's your energy?" check-in after a long session | `check_in_suggestion` field in `DayContext`, populated by `ContextEngine._compute_check_in_suggestion` |
| Medication reminder in the "Today" block | `MedicationStatus.pending/missed` surfaced via `_render_today_block` in the supervisor prompt |
| Energy level shown as "medium/normal" (with honest "guess" tag) | `ContextEngine._estimate_energy` blending ADHD signals |
| Step-by-step morning routine with "You started. That's the hardest part." | `RoutineManager.get_progress` + `RoutineSessionState` tracking |
| Tasks scheduled to avoid the afternoon crash window | `update_plan` warning when rescheduling into `ADHDProfile.crash_periods` |
| No "you forgot" in any Kora response | `ADHDModule.output_rules()` + `RSDFilter` in `kora_v2/core/rsd_filter.py`; every notification also passes through the `NotificationGate` RSD hook |
| Notifications respect hyperfocus and DND | `NotificationGate` in `runtime/orchestration/notifications.py` — see [`adhd.md`](adhd.md) § NotificationGate |
| Open decisions persist across turns instead of only living in transcript | `OpenDecisionsTracker` in `runtime/orchestration/decisions.py` — see [`life.md`](life.md) § Open decisions |
| Focus hours trend in weekly review | `ContextEngine._aggregate_focus_summary` trend detection |

## Is ADHD a feature of `life/`, or a cross-cutting concern?

ADHD is a **cross-cutting concern**. The `kora_v2/adhd/` package defines the protocol, profile, and module. The `kora_v2/life/` package owns routines (the only file there today, `routines.py`). The `kora_v2/context/engine.py` is the integration hub: it reads from `adhd/` and writes to `DayContext` / `LifeContext`, which the supervisor graph injects into every prompt turn.

The relationship is:

```
  adhd/profile.py       ←── _KoraMemory/User Model/adhd_profile/profile.yaml
       ↓
  adhd/module.py        ←── energy_signals(), focus_detection(), output_rules()
       ↓
  context/engine.py     ←── DayContext, LifeContext (reads operational.db)
       ↓
  graph/supervisor.py   ←── _render_today_block() in each turn's frozen prefix
       ↓
  graph/prompts.py      ←── rendered "## Today" block in dynamic suffix
```

`life/routines.py` is a peer: it writes to `routine_sessions` in `operational.db`, which `ContextEngine.build_day_context` reads to populate `RoutineStatus`. Tools in `kora_v2/tools/routines.py` are the user-facing entry point.

## Phase 5 narrative

Phase 5 ("ADHD-aware life engine") shipped on the main branch in commit `a2df4a1` and was post-launch fixed across four commits:

| Commit | Fix |
|---|---|
| `0636f6d` | Blocker: `items_due` SQL used range comparison against bare `YYYY-MM-DD` date strings (items vanished from the "Today" block) |
| `dac6612` | High: timezone-aware morning/crash/day bounds — a 9am PST calendar entry stored as 17:00 UTC was wrongly classified as an afternoon event |
| `187513e` | High: `_aggregate_focus_summary` trend detection was dead code (second-half comparison was never reachable); also fixed silent SQL error swallowing |
| `53665d6` | Medium: wizard Section 5 (API keys) + profile persistence |

Phase 6B added guided routines (`life/routines.py`) in a separate pass.

## Proactive surfacing model (current state)

`DayContext` carries two proactive fields that are populated every turn:

- `check_in_suggestion` — a string like "You've been going for 135min — how's your energy?" surfaced when the session exceeds 120 minutes with no self-report, or when a medication dose window has been missed.
- `upcoming_nudges` — a list like `["standup in 12min", "Adderall window in 45min"]` built from the next 5 calendar entries within 2 hours.

These are rendered in the supervisor's dynamic suffix (see `graph/prompts.py:_render_today_block`) and the supervisor model decides whether to surface them in conversation. Phase 7.5b added the *delivery* infrastructure for the autonomous route: `OrchestrationEngine` pipelines can raise notifications that route through the `NotificationGate` (see [`adhd.md`](adhd.md)), and the pipeline wrapper for proactive surfacing is one of the 17 stubs in `core_pipelines.py` waiting for Phase 8 to ship real step functions. The `DayContext` fields stay the same; what changes with Phase 8 is that a dispatched `WorkerTask` — not a prompt hint — will decide when to emit.

## Files in this cluster

| Path | Role |
|---|---|
| `kora_v2/adhd/__init__.py` | Public re-exports for the `adhd` package |
| `kora_v2/adhd/protocol.py` | `NeurodivergentModule` protocol + shared data models |
| `kora_v2/adhd/profile.py` | `ADHDProfile` Pydantic model + YAML loader/writer |
| `kora_v2/adhd/module.py` | `ADHDModule` — the concrete ADHD implementation |
| `kora_v2/life/__init__.py` | Empty package marker |
| `kora_v2/life/routines.py` | `RoutineManager`, `Routine`, `RoutineSessionState`, progress tracking |
| `kora_v2/context/engine.py` | `ContextEngine` — integration hub, produces `DayContext`/`LifeContext` |
| `kora_v2/core/rsd_filter.py` | `check_output()` — regex RSD filter (standalone utility) |
| `kora_v2/cli/first_run.py` | 5-section onboarding wizard that writes `profile.yaml` |
| `kora_v2/tools/life_management.py` | 11 life-domain tools (medication, meal, focus, reminder, expense, quick notes) |
| `kora_v2/tools/routines.py` | 4 routine-lifecycle tools (list, start, advance, progress) |
| `kora_v2/tools/planning.py` | `day_briefing`, `life_summary`, `draft_plan`, ADHD time correction |
| `kora_v2/core/di.py` | DI wiring: lazy properties `adhd_profile`, `adhd_module`, `context_engine`, `routine_manager` |

## Integration with the rest of Kora

- **`graph/supervisor.py`**: calls `engine.build_day_context()` every turn, injects `adhd_module.output_guidance()` and `supervisor_context()` into the frozen prefix.
- **`agents/workers/planner.py`**: reads `adhd_module.planning_adjustments()` to cap step count at 7, apply 1.5x time correction, and require a micro-step first.
- **`agents/workers/reviewer.py`**: has `adhd_friendliness` as an explicit review category.
- **`core/models.py`**: `DayContext`, `LifeContext`, `MedicationStatus`, `FocusBlockStatus`, `RoutineStatus`, `EnergyEstimate` are all Phase 5 additions.
- **`core/db.py`**: `operational.db` houses the life-domain tables (`medication_log`, `meal_log`, `focus_blocks`, `finance_log`, `energy_log`, `reminders`, `routines`, `routine_sessions`, `quick_notes`, `calendar_entries`).
- **`runtime/orchestration/`**: `NotificationGate` delivers every proactive message raised by this cluster; `OpenDecisionsTracker` persists decisions raised in conversation or by background pipelines; `SleepSchedule` / `UserScheduleProfile` own the DND and hyperfocus window that the gate enforces. See [`../01-runtime-core/orchestration.md`](../01-runtime-core/orchestration.md).
