# 05 — Life OS, Support & ADHD Cluster

Kora's current product center is Life OS: local-first day-to-day life management for people whose planning, initiation, transitions, sensory load, social load, low energy, burnout, anxiety, avoidance, or executive dysfunction make ordinary productivity tools unrealistic.

The core loop is:

```text
Plan Today -> Confirm Reality -> Repair The Day -> Bridge Tomorrow
```

The older ADHD-aware life engine is still active, but it is now one support surface inside a broader Life OS runtime. Kora tracks reality through durable ledger rows, keeps versioned day plans, explains load, repairs days without shame framing, can enter Stabilization Mode, creates context packs for hard events, bridges unfinished work into tomorrow, and routes crisis language away from normal productivity flows.

## What this cluster produces

| User-visible outcome | Mechanism |
|---|---|
| A versioned plan for today with fixed commitments and flexible entries | `DayPlanService` in `kora_v2/life/day_plan.py`, exposed by `create_day_plan` |
| "I took my meds" or "I skipped breakfast" becomes durable reality | `LifeEventLedger` in `kora_v2/life/ledger.py`, plus migrated life-management tools |
| Kora can accept "No, I didn't do that" and correct its inference | `correct_reality` tool writes corrected/rejected ledger and domain-event rows |
| Load band changes are explainable | `LifeLoadEngine` in `kora_v2/life/load.py` records factors in `load_assessments` |
| Being behind produces a repaired plan revision, not guilt | `DayRepairEngine` in `kora_v2/life/repair.py` writes repair actions and a new active revision |
| Nudges can be sent, deferred, suppressed, or queued | `ProactivityPolicyEngine` in `kora_v2/life/proactivity_policy.py` writes `nudge_decisions` and feedback |
| Overload can reduce the day to essentials | `StabilizationModeService` in `kora_v2/life/stabilization.py` writes `support_mode_state` |
| Admin/anxiety/sensory-heavy events can get scripts and first steps | `ContextPackService` in `kora_v2/life/context_packs.py` writes DB metadata and memory artifacts |
| Tomorrow starts from a shame-safe bridge | `FutureSelfBridgeService` in `kora_v2/life/future_bridge.py` writes bridge rows and artifacts |
| ADHD/anxiety/autism-sensory/low-energy/burnout profiles change behavior | `SupportRegistry`, `SupportProfileBootstrapService`, and support modules under `kora_v2/support/` |
| Crisis language preempts planning/repair/proactivity | `CrisisSafetyRouter` in `kora_v2/safety/crisis.py` writes `safety_boundary_records` |
| User-reviewed support exports exclude unselected sensitive data | `TrustedSupportExportService` in `kora_v2/life/trusted_support.py` |
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

ADHD is a **cross-cutting concern**. The `kora_v2/adhd/` package defines the protocol, profile, and module. The `kora_v2/support/` package generalizes support profiles across ADHD, anxiety, autism/sensory load, low-energy days, and burnout. The `kora_v2/life/` package owns Life OS services plus routines and reminder storage/delivery helpers. The `kora_v2/context/engine.py` remains the integration hub for prompt context.

The relationship is:

```
  adhd/profile.py       ←── _KoraMemory/User Model/adhd_profile/profile.yaml
       ↓
  adhd/module.py        ←── energy_signals(), focus_detection(), output_rules()
       ↓
  life/* + support/*    ←── day plans, ledger, load, repair, support modes
       ↓
  context/engine.py     ←── DayContext, LifeContext, supervisor context
       ↓
  graph/supervisor.py   ←── _render_today_block() in each turn's frozen prefix
       ↓
  graph/prompts.py      ←── rendered "## Today" block in dynamic suffix
```

`life/routines.py` is a peer: it writes to `routine_sessions` in `operational.db`, which `ContextEngine.build_day_context` reads to populate `RoutineStatus`. `life/reminders.py` provides `ReminderStore` for due reminder polling and delivery state. Tools in `kora_v2/tools/routines.py` and `kora_v2/tools/life_management.py` are the user-facing entry points.

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

These are rendered in the supervisor's dynamic suffix (see `graph/prompts.py:_render_today_block`) and the supervisor model decides whether to surface them in conversation. The autonomous delivery route is also wired: `OrchestrationEngine` pipelines can raise notifications through `NotificationGate`, and current code includes real proactive handlers for continuity checks, wake preparation, pattern scans, anticipatory prep, contextual engagement, commitment tracking, stuck detection, weekly triage, draft-on-observation, and connection making. Some acceptance surfaces remain unproven in the latest short run, so docs should distinguish implemented handlers from fully green acceptance.

## Files in this cluster

| Path | Role |
|---|---|
| `kora_v2/adhd/__init__.py` | Public re-exports for the `adhd` package |
| `kora_v2/adhd/protocol.py` | `NeurodivergentModule` protocol + shared data models |
| `kora_v2/adhd/profile.py` | `ADHDProfile` Pydantic model + YAML loader/writer |
| `kora_v2/adhd/module.py` | `ADHDModule` — the concrete ADHD implementation |
| `kora_v2/life/models.py` | Life OS Pydantic contracts |
| `kora_v2/life/domain_events.py` | Append-only Life OS domain events |
| `kora_v2/life/ledger.py` | Life Event Ledger |
| `kora_v2/life/day_plan.py` | Versioned day-plan engine |
| `kora_v2/life/load.py` | Life Load Meter and explainable factors |
| `kora_v2/life/repair.py` | Repair The Day and energy-aware reshaping |
| `kora_v2/life/proactivity_policy.py` | Nudge send/defer/suppress/queue policy and feedback |
| `kora_v2/life/stabilization.py` | Stabilization Mode state and reduced-day planning |
| `kora_v2/life/context_packs.py` | Context packs and memory-root artifacts |
| `kora_v2/life/future_bridge.py` | Future Self Bridges |
| `kora_v2/life/trusted_support.py` | Social/sensory helpers and user-reviewed support exports |
| `kora_v2/life/routines.py` | `RoutineManager`, `Routine`, `RoutineSessionState`, progress tracking |
| `kora_v2/life/reminders.py` | `ReminderStore`, due reminder polling, delivery marking, dismissal, recurrence rescheduling |
| `kora_v2/support/` | Support profile bootstrap, registry, and ADHD/anxiety/autism-sensory/low-energy/burnout runtime modules |
| `kora_v2/safety/crisis.py` | Crisis safety boundary router |
| `kora_v2/context/engine.py` | `ContextEngine` — integration hub, produces `DayContext`/`LifeContext` |
| `kora_v2/core/rsd_filter.py` | `check_output()` — regex RSD filter (standalone utility) |
| `kora_v2/cli/first_run.py` | 5-section onboarding wizard that writes `profile.yaml` |
| `kora_v2/tools/life_management.py` | 11 life-domain tools (medication, meal, focus, reminder, expense, quick notes) |
| `kora_v2/tools/life_os.py` | 11 Life OS tools for day plans, reality confirmation, load, repair, nudges, context packs, bridges, support profiles, and crisis checks |
| `kora_v2/tools/routines.py` | 5 routine-lifecycle tools (create, list, start, advance, progress) |
| `kora_v2/tools/planning.py` | `day_briefing`, `life_summary`, `draft_plan`, ADHD time correction |
| `kora_v2/core/di.py` | DI wiring for ADHD, context, routines, Life OS services, support registry, and crisis safety |

## Integration with the rest of Kora

- **`graph/supervisor.py`**: calls `engine.build_day_context()` every turn, injects `adhd_module.output_guidance()` and `supervisor_context()` into the frozen prefix.
- **`agents/workers/planner.py`**: reads `adhd_module.planning_adjustments()` to cap step count at 7, apply 1.5x time correction, and require a micro-step first.
- **`agents/workers/reviewer.py`**: has `adhd_friendliness` as an explicit review category.
- **`core/models.py`**: `DayContext`, `LifeContext`, `MedicationStatus`, `FocusBlockStatus`, `RoutineStatus`, `EnergyEstimate` are all Phase 5 additions.
- **`core/db.py`**: `operational.db` houses the life-domain tables plus Life OS tables (`day_plans`, `day_plan_entries`, `life_events`, `domain_events`, `load_assessments`, `plan_repair_actions`, `nudge_decisions`, `nudge_feedback`, `support_mode_state`, `context_packs`, `future_self_bridges`, `support_profiles`, `support_profile_signals`, `safety_boundary_records`).
- **`runtime/orchestration/`**: `NotificationGate` delivers every proactive message raised by this cluster; `OpenDecisionsTracker` persists decisions raised in conversation or by background pipelines; `SleepSchedule` / `UserScheduleProfile` own the DND and hyperfocus window that the gate enforces. See [`../01-runtime-core/orchestration.md`](../01-runtime-core/orchestration.md).
