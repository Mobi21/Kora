# Life OS Module (`kora_v2/life/`)

The `life/` package is now Kora's Life OS runtime. It owns the durable loop:

```text
Plan Today -> Confirm Reality -> Repair The Day -> Bridge Tomorrow
```

Guided routines and reminders remain active, but they are no longer the whole life layer. Medication logging, meal tracking, focus blocks, expenses, quick notes, items, calendar entries, support profiles, context packs, nudge policy, stabilization, and future bridges now feed the same Life Event Ledger and operational database. `ContextEngine` still aggregates life state into `DayContext` and `LifeContext`; Life OS services add durable product behavior behind that context.

## Files in this module

| File | Lines | Role |
|---|---|---|
| [`kora_v2/life/__init__.py`](../../kora_v2/life/__init__.py) | 1 | Empty package marker |
| [`kora_v2/life/models.py`](../../kora_v2/life/models.py) | 400+ | Life OS Pydantic contracts |
| [`kora_v2/life/domain_events.py`](../../kora_v2/life/domain_events.py) | 100+ | Append-only domain-event writer |
| [`kora_v2/life/ledger.py`](../../kora_v2/life/ledger.py) | 200+ | Life Event Ledger |
| [`kora_v2/life/day_plan.py`](../../kora_v2/life/day_plan.py) | 250+ | Versioned day-plan service |
| [`kora_v2/life/load.py`](../../kora_v2/life/load.py) | 250+ | Life Load Meter |
| [`kora_v2/life/repair.py`](../../kora_v2/life/repair.py) | 300+ | Repair The Day and energy-aware reshaping |
| [`kora_v2/life/proactivity_policy.py`](../../kora_v2/life/proactivity_policy.py) | 200+ | Nudge policy and feedback |
| [`kora_v2/life/stabilization.py`](../../kora_v2/life/stabilization.py) | 200+ | Stabilization Mode |
| [`kora_v2/life/context_packs.py`](../../kora_v2/life/context_packs.py) | 200+ | Context packs and memory artifacts |
| [`kora_v2/life/future_bridge.py`](../../kora_v2/life/future_bridge.py) | 200+ | Future Self Bridge |
| [`kora_v2/life/trusted_support.py`](../../kora_v2/life/trusted_support.py) | 200+ | Social/sensory helpers and trusted-support exports |
| [`kora_v2/life/routines.py`](../../kora_v2/life/routines.py) | 440 | Models, RoutineManager, progress tracking |
| [`kora_v2/life/reminders.py`](../../kora_v2/life/reminders.py) | 250+ | ReminderStore, due polling, delivery/reschedule state |
| [`kora_v2/tools/life_management.py`](../../kora_v2/tools/life_management.py) | 1020 | 11 life-domain write/query tools |
| [`kora_v2/tools/life_os.py`](../../kora_v2/tools/life_os.py) | 300+ | 11 Life OS loop tools |
| [`kora_v2/support/`](../../kora_v2/support/) | package | Support registry, bootstrap, and runtime support modules |
| [`kora_v2/safety/crisis.py`](../../kora_v2/safety/crisis.py) | 150+ | Crisis safety boundary |
| [`kora_v2/tools/routines.py`](../../kora_v2/tools/routines.py) | 244+ | 5 routine-lifecycle tools |
| [`kora_v2/context/engine.py`](../../kora_v2/context/engine.py) | 886 | ContextEngine (aggregation hub) |
| [`kora_v2/tools/planning.py`](../../kora_v2/tools/planning.py) | ~740 | `day_briefing`, `life_summary`, ADHD time correction |

---

## Part 1: Life OS Services

Life OS services are container-resolved and all write durable proof to `data/operational.db`.

| Service | Main durable proof |
|---|---|
| `DomainEventStore` | `domain_events` rows for product-significant events |
| `LifeEventLedger` | `life_events` rows for confirmed, inferred, corrected, rejected, tool-generated reality |
| `DayPlanService` | `day_plans` and `day_plan_entries`, including active/superseded revisions |
| `LifeLoadEngine` | `load_assessments` with band, score, factors, and assumptions |
| `DayRepairEngine` | `plan_repair_actions` plus a new day-plan revision when repair is applied |
| `ProactivityPolicyEngine` | `nudge_decisions` and `nudge_feedback` |
| `StabilizationModeService` | `support_mode_state` and reduced active plan behavior |
| `ContextPackService` | `context_packs` rows and markdown artifacts under the memory root |
| `FutureSelfBridgeService` | `future_self_bridges` rows and markdown artifacts |
| `TrustedSupportExportService` / `SocialSensorySupportService` | user-reviewed export drafts and social/sensory planning helpers |
| `SupportProfileBootstrapService` / `SupportRegistry` | `support_profiles` and `support_profile_signals` |
| `CrisisSafetyRouter` | `safety_boundary_records`, before normal planning/repair/proactivity |

### Life OS tools

`kora_v2/tools/life_os.py` exposes the runtime loop:

- `create_day_plan`
- `confirm_reality`
- `correct_reality`
- `assess_life_load`
- `repair_day_plan`
- `decide_life_nudge`
- `record_nudge_feedback`
- `create_context_pack`
- `bridge_tomorrow`
- `set_support_profile_status`
- `check_crisis_boundary`

These are not prompt-only instructions. They mutate operational DB state, memory-root artifacts, support profile status, or crisis-boundary records.

### Acceptance status

The Life OS acceptance collector exists in `tests/acceptance/life_os.py` and is rendered by `tests/acceptance/_report.py`. It checks DB rows and artifacts for the Life OS loop. The public docs should still avoid claiming a full week-long Life OS harness is green until the old product-centered acceptance scenario is fully replaced and rerun.

---

## Part 2: Routines (`kora_v2/life/routines.py`)

Routines are Phase 6B. They represent multi-step structured workflows (e.g., "Morning Routine", "Wind-down") that the user can execute step-by-step inside a conversation. They run through the same autonomous graph runtime as other plan types when invoked with `mode='routine'` (referenced in the module docstring), but their partial-completion state is tracked separately in SQLite so progress survives session boundaries.

### Data models

#### `RoutineStep`

```
index          int           — position in the routine (0-based)
title          str           — brief name
description    str           — what to do
estimated_minutes  int       — default 5
energy_required    "low"|"medium"|"high"   — default "medium"
skippable      bool          — default True
cue            str           — ADHD-friendly memory cue (optional)
```

The `cue` field is specifically for ADHD users: it provides a physical or sensory anchor for the step ("put on shoes before touching your bag") so the step can be recalled without reading the description.

#### `RoutineVariant`

A named variant (`"standard"` or `"low_energy"`) with its own step list and total time estimate. The standard variant is always present; `low_energy` is optional. When a user reports low energy, Kora should offer the `low_energy` variant (fewer steps, all `energy_required="low"`).

#### `Routine`

The template that is persisted in the `routines` table. Has both variants, `tags` (list of strings for filtering, e.g. `["morning", "health"]`), and timestamps.

#### `RoutineSessionState`

The live per-execution record in `routine_sessions`. Key fields:

```
session_id          str           — PK in routine_sessions
routine_id          str           — FK to routines.id
variant             "standard"|"low_energy"
current_step_index  int           — pointer to the next step to execute
completed_steps     list[int]     — indices of completed steps
skipped_steps       list[int]     — indices of explicitly skipped steps
status              "active"|"completed"|"abandoned"
completion_confidence  float     — fraction of non-skipped steps completed (0.0–1.0)
```

Note: `completion_confidence` is recomputed on every `advance_step()` call as `len(completed_steps) / total_steps`. It does not penalize skipped steps in the numerator, though skipped steps do reduce the denominator's weight implicitly because the user chose not to complete them.

#### `RoutineProgress`

User-facing summary returned by `get_progress()`. The `message` field uses explicitly shame-free language:

| `completion_pct` | message |
|---|---|
| 0% | "Ready when you are." |
| 1–29% (at least 1 done) | "You started. That's the hardest part." |
| 30–59% | "Got partway through — that counts." |
| 60–99% | "Good progress — X/Y steps done." |
| 100% | "You did it. Routine complete." |

### `RoutineManager` class

`RoutineManager(db_path: Path)` is the single service object for all routine persistence. It is wired by the DI container at `container.routine_manager` and initialized during `initialize_phase4()`.

All methods are async; they open and close their own `aiosqlite.connect()` connections with `PRAGMA journal_mode=WAL`.

#### CRUD — routine templates

| Method | Signature | Notes |
|---|---|---|
| `create_routine` | `(routine: Routine) -> str` | Inserts into `routines`; returns `routine.id` |
| `get_routine` | `(routine_id: str) -> Routine \| None` | Loads by PK; reconstructs steps from JSON |
| `list_routines` | `(tags: list[str] \| None) -> list[Routine]` | ORDER BY name; optional tag intersection filter |

Steps are serialized as a JSON array of `RoutineStep.model_dump()` dicts. `low_energy_variant_json` is NULL when no low-energy variant exists.

#### Session management

| Method | Signature | Effect |
|---|---|---|
| `start_session` | `(routine_id, session_id, variant, parent_session_id) -> RoutineSessionState` | INSERT into `routine_sessions`; status=`active` |
| `get_session` | `(session_id) -> RoutineSessionState \| None` | SELECT by PK |
| `advance_step` | `(session_id, step_index, skipped) -> RoutineSessionState` | Appends to `completed_steps` or `skipped_steps`, advances `current_step_index`, recomputes `completion_confidence` |
| `complete_session` | `(session_id) -> RoutineSessionState` | Sets `status='completed'`, `completed_at=now` |
| `abandon_session` | `(session_id) -> RoutineSessionState` | Sets `status='abandoned'`, `completed_at=now`; note the docstring says "no judgment" |

`advance_step` never raises if a step index is re-submitted — it guards against duplicates in the list (`if step_index not in state.completed_steps`). The `current_step_index` pointer advances only if `step_index >= current_step_index`.

#### Progress computation

`get_progress(session, routine)` is synchronous. It selects the correct variant's step count based on `session.variant`, then produces `RoutineProgress`.

### SQL schema — routines

```sql
CREATE TABLE IF NOT EXISTS routines (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT,
    steps_json    TEXT NOT NULL,          -- JSON array of RoutineStep dicts
    low_energy_variant_json TEXT,         -- JSON array, nullable
    tags          TEXT,                   -- JSON array of strings
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS routine_sessions (
    id                   TEXT PRIMARY KEY,
    routine_id           TEXT NOT NULL REFERENCES routines(id),
    session_id           TEXT REFERENCES sessions(id),  -- conversation session FK
    variant              TEXT NOT NULL DEFAULT 'standard',
    current_step_index   INTEGER DEFAULT 0,
    completed_steps      TEXT NOT NULL DEFAULT '[]',    -- JSON int array
    skipped_steps        TEXT NOT NULL DEFAULT '[]',    -- JSON int array
    checkpoint_state     TEXT,                           -- reserved for future autonomous state
    last_nudge_at        TEXT,
    completion_confidence REAL DEFAULT 0.0,
    status               TEXT NOT NULL DEFAULT 'active',
    started_at           TEXT NOT NULL,
    completed_at         TEXT
);
```

Indexes: `idx_routine_sessions_routine (routine_id)`, `idx_routine_sessions_session (session_id)`.

### Routine tools (`kora_v2/tools/routines.py`)

Five tools bridge the `RoutineManager` to the supervisor's tool registry:

| Tool | Auth | Notes |
|---|---|---|
| `create_routine(name, steps, tags, ...)` | `ASK_FIRST` | Persists a reusable routine template and can register runtime scheduling metadata |
| `list_routines(tags)` | `ALWAYS_ALLOWED` (read-only) | Comma-separated tags string → tag list filter |
| `start_routine(routine_id, session_id, variant)` | `ASK_FIRST` | Creates `routine_sessions` row |
| `advance_routine(session_id, step_index, skipped)` | `ASK_FIRST` | Returns `RoutineProgress` fields + shame-free message |
| `routine_progress(session_id)` | `ALWAYS_ALLOWED` (read-only) | Loads session + routine, returns progress |

All tools return JSON strings with a `success` boolean.

---

## Part 3: Life-Management Tools (`kora_v2/tools/life_management.py`)

These are the direct write and query surface for the older life-domain tables. Each is registered with the `@tool` decorator and appears in the supervisor's tool registry. They all accept a `container` argument to resolve `settings.data_dir / "operational.db"`.

After the Life OS pivot, successful write tools also emit Life Event Ledger rows so medication, meals, focus blocks, reminders, expenses, and quick notes do not bypass Life OS reality tracking.

### Medication tracking

| Tool | Auth | Table | Description |
|---|---|---|---|
| `log_medication(medication_name, dose, notes)` | `ASK_FIRST` | `medication_log` | INSERT with current UTC timestamp |
| `query_medications(days_back, medication_name, limit)` | `ALWAYS_ALLOWED` | `medication_log` | SELECT, most-recent first |

The `log_medication` description contains an aggressive instruction: "ALWAYS call this tool when the user mentions taking medication, even casually ('took my Adderall', 'had my Vyvanse', 'just took my meds'). Never acknowledge medication without logging it." This ensures the `ContextEngine` has data for its medication status computation.

### Meal tracking

| Tool | Auth | Table | Description |
|---|---|---|---|
| `log_meal(description, meal_type, calories)` | `ASK_FIRST` | `meal_log` | INSERT; `calories=0` stored as NULL |
| `query_meals(days_back, meal_type, limit)` | `ALWAYS_ALLOWED` | `meal_log` | SELECT, most-recent first |

`meal_type` can be `breakfast`, `lunch`, `dinner`, `snack`, or the generic `meal`.

### Focus block tracking

| Tool | Auth | Table | Description |
|---|---|---|---|
| `start_focus_block(label, notes)` | `ASK_FIRST` | `focus_blocks` | INSERT with `ended_at=NULL` |
| `end_focus_block(notes, completed)` | `ASK_FIRST` | `focus_blocks` | UPDATE most-recent open block; computes duration |
| `query_focus_blocks(days_back, open_only, limit)` | `ALWAYS_ALLOWED` | `focus_blocks` | Returns computed `duration_minutes` |

`end_focus_block` finds the most recent row where `ended_at IS NULL`, computes duration in minutes, and merges existing + new notes. Returns error if no open block exists.

### Reminder tracking

| Tool | Auth | Table | Description |
|---|---|---|---|
| `create_reminder(title, description, remind_at, recurring)` | `ASK_FIRST` | `reminders` | INSERT; `remind_at` is an ISO timestamp string or empty |
| `query_reminders(status, limit)` | `ALWAYS_ALLOWED` | `reminders` | SELECT WHERE status=? ORDER BY remind_at ASC |

Reminders are no longer stored-only. There is still no dedicated reminder daemon; delivery is owned by orchestration. `create_reminder` writes the legacy columns plus Phase 8e scheduling columns and can kick the `continuity_check` pipeline when the due time is inside its scan window. `continuity_check_step` polls `ReminderStore.get_due_reminders()`, sends through `NotificationGate`, then marks or reschedules via `deliver_and_reschedule()`.

### Finance / expense tracking

| Tool | Auth | Table | Description |
|---|---|---|---|
| `log_expense(amount, category, description)` | `ASK_FIRST` | `finance_log` | INSERT; computes `is_impulse` flag |
| `query_expenses(days_back, category, limit)` | `ALWAYS_ALLOWED` | `finance_log` | Returns entries + category totals |

**Impulse spend detection**: `log_expense` queries the last 30 days of the same category. If there are at least `IMPULSE_MIN_SAMPLES = 5` prior entries and the new amount exceeds 1.5× the category average, `is_impulse=1` is stored and a `note` string is returned. The note is designed to be surfaced "gently per RSD rules — do NOT shame or lecture" (per the tool description).

### Quick notes

| Tool | Auth | Table | Description |
|---|---|---|---|
| `quick_note(content, tags)` | `ASK_FIRST` | `quick_notes` | INSERT immediately, bypasses memory pipeline |
| `query_quick_notes(days_back, tag, limit)` | `ALWAYS_ALLOWED` | `quick_notes` | Substring tag filter |

Quick notes are for immediate capture: "note: buy dog food", "remember: call dentist". They do not go through the memory write pipeline.

### SQL schema — life management tables

```sql
CREATE TABLE IF NOT EXISTS reminders (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT,
    remind_at    TEXT,        -- ISO timestamp, nullable
    recurring    TEXT,        -- e.g. 'daily', 'weekly', nullable
    status       TEXT NOT NULL DEFAULT 'pending',
    session_id   TEXT,
    created_at   TEXT NOT NULL
    -- Startup migrations add Phase 8e columns:
    -- due_at, repeat_rule, source, delivered_at, dismissed_at, metadata
);

CREATE TABLE IF NOT EXISTS medication_log (
    id               TEXT PRIMARY KEY,
    medication_name  TEXT NOT NULL,
    dose             TEXT,
    taken_at         TEXT NOT NULL,   -- UTC ISO timestamp
    notes            TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meal_log (
    id           TEXT PRIMARY KEY,
    meal_type    TEXT NOT NULL DEFAULT 'meal',
    description  TEXT NOT NULL,
    calories     INTEGER,         -- nullable
    tags         TEXT,
    logged_at    TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS focus_blocks (
    id          TEXT PRIMARY KEY,
    label       TEXT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,            -- NULL = open/active
    notes       TEXT,
    completed   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
    -- calendar_entry_id TEXT added via migration (Phase 5)
);

CREATE TABLE IF NOT EXISTS quick_notes (
    id         TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    tags       TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finance_log (
    id          TEXT PRIMARY KEY,
    amount      REAL NOT NULL,
    category    TEXT NOT NULL,
    description TEXT,
    is_impulse  INTEGER DEFAULT 0,
    logged_at   TEXT NOT NULL DEFAULT (datetime('now')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS energy_log (
    id          TEXT PRIMARY KEY,
    level       TEXT NOT NULL,      -- 'low'|'medium'|'high'
    focus       TEXT,               -- 'scattered'|'normal'|'locked_in'
    source      TEXT NOT NULL,      -- 'self_report' or other
    notes       TEXT,
    logged_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### SQL schema — Life OS tables

`core/db.py` also creates the Life OS product tables:

| Table | Purpose |
|---|---|
| `domain_events` | Append-only product event stream |
| `day_plans` | One active day-plan revision per local day, with superseded revisions preserved |
| `day_plan_entries` | Entries inside a day-plan revision |
| `life_events` | Ledger of confirmed, inferred, corrected, rejected, and tool-generated reality |
| `load_assessments` | Life Load Meter outputs and factors |
| `plan_repair_actions` | Repair proposals/applied actions |
| `nudge_decisions` | Proactivity policy decisions |
| `nudge_feedback` | User feedback on nudges |
| `support_mode_state` | Quiet/high-support/recovery/stabilization state |
| `context_packs` | Context pack metadata linked to artifacts |
| `future_self_bridges` | End-of-day bridge metadata linked to artifacts |
| `support_profiles` | Baseline and condition-specific profile status/configuration |
| `support_profile_signals` | User corrections and profile-learning signals |
| `safety_boundary_records` | Crisis-boundary checks and routing records |

---

## Part 3.5: Open decisions (Phase 7.5)

Before Phase 7.5 the autonomous graph had its own ad-hoc `DecisionManager` that only tracked decisions a `WorkerTask` was blocked on. There was no general surface for "the user and Kora are in the middle of deciding something" — life-domain decisions like "where do I reroute my dentist" lived only in conversation transcripts.

Phase 7.5 replaces that model with a single `OpenDecisionsTracker` at `kora_v2/runtime/orchestration/decisions.py`. The table is owned by the orchestration layer but the semantics are life-module semantics: an open decision is a pending choice with a `prompt`, an optional `task_id` (set when a `WorkerTask` is the one waiting), a `deadline`, and a `resolution`. Decisions raised by autonomous work trigger `OPEN_DECISION_POSED` events and can pause the owning task into `PAUSED_FOR_DECISION`; decisions raised by the conversation flow are recorded without blocking anything and surface later via recall.

A legacy shim at `kora_v2/autonomous/decisions.py` re-exports the new module so any code or test that still imports the old name continues to work without edits.

### `record_decision` supervisor tool

The supervisor exposes a new tool at `kora_v2/graph/dispatch.py` — `record_decision(prompt, options, context, task_id=None, deadline=None)`. Auth: `ALWAYS_ALLOWED` (read-mostly — it only inserts into `open_decisions`). The tool is how Kora captures in-conversation choices without having to re-derive them later:

| Argument | Meaning |
|---|---|
| `prompt` | One-line framing of the choice ("Should I reroute the dentist?") |
| `options` | List of strings; may be empty if the choice is open-ended |
| `context` | Free-form string preserved with the decision so recall has something to show |
| `task_id` | Optional — link to an in-flight `WorkerTask` if the decision is blocking it |
| `deadline` | Optional ISO timestamp after which the decision should be surfaced as stale |

The tool appends a row to the `open_decisions` table (defined in `runtime/orchestration/migrations/001_orchestration.sql` — see [`../01-runtime-core/core.md`](../01-runtime-core/core.md) § Orchestration tables). Resolution is a separate action: calling `get_task_progress` or `get_working_doc` on a task whose decision has been resolved reads the resolved row, and the dispatcher auto-transitions the owning task out of `PAUSED_FOR_DECISION`.

### How this changes the life domain

- **Routing through pipelines.** Decisions raised by background pipelines (e.g., a proactive scan that finds a scheduling conflict) go through the same tracker as conversation-raised decisions — no parallel path.
- **Event stream.** `OPEN_DECISION_POSED` fires on insert, `TASK_LINGERING` fires when a decision passes its `deadline` without resolution; both are consumable by the signal scanner for surfacing.
- **Recall.** Because every decision is a real row rather than a transient message attribute, the recall path can return open decisions as first-class results.

See [`../01-runtime-core/orchestration.md`](../01-runtime-core/orchestration.md) § OpenDecisionsTracker for the full method surface and the FSM interaction with `WorkerTask.PAUSED_FOR_DECISION`.

---

## Part 4: Context Engine (`kora_v2/context/engine.py`)

`ContextEngine` is the aggregation hub. It does not own any data; it queries every life-domain table and synthesizes two output types.

### Class interface

```python
class ContextEngine:
    def __init__(
        self,
        db_path: Path,
        adhd_module: ADHDModule,
        user_tz_name: str = "UTC",
    ) -> None: ...

    async def build_day_context(
        self,
        target_date: date | None = None,
        session_state: dict[str, Any] | None = None,
    ) -> DayContext: ...

    async def build_life_context(
        self, since: date, until: date, label: str = ""
    ) -> LifeContext: ...

    async def generate_insights(self, lc: LifeContext) -> list[str]: ...
```

`user_tz_name` is an IANA timezone name (e.g. `"America/Los_Angeles"`). The engine stores a `ZoneInfo` object and uses it for all local-time comparisons. Fallback is `"UTC"` if the name is invalid.

### `build_day_context()` — what it queries

All queries are scoped to the local calendar day (midnight-to-midnight) converted to UTC:

```
day_start_local = datetime.combine(target_date, time.min, tzinfo=user_tz)
day_end_local   = day_start_local + timedelta(days=1)
day_start       = day_start_local.astimezone(UTC)
day_end         = day_end_local.astimezone(UTC)
```

This was fixed in commit `dac6612` — previously the day bounds were computed in UTC directly, causing midnight-rollover mismatches for non-UTC users.

| DB query | Purpose |
|---|---|
| `calendar_entries` between day_start/day_end | Schedule, next event, transition buffer events |
| `medication_log` between day_start/day_end | Cross-referenced against `ADHDProfile.medication_schedule` |
| `meal_log` between day_start/day_end | `meals_logged` list |
| `focus_blocks` started between day_start/day_end | Active + completed focus blocks |
| `routine_sessions JOIN routines` started between day_start/day_end | `RoutineStatus.by_routine` |
| `finance_log` between day_start/day_end | `finance_today` summary |
| `items` WHERE `due_date = target_date.isoformat()` | `items_due` list |
| `energy_log` WHERE `source='self_report'` ORDER BY DESC LIMIT 1 | Last self-reported energy |

Note on `items_due`: the query uses an exact string match `due_date = ?` against a bare ISO date string (e.g., `"2026-04-14"`). This was a Phase 5 blocker (`0636f6d`): `create_item` stores bare date strings, not full ISO datetimes, and a range comparison against UTC timestamps is lexically false.

### `_build_medication_status()` — timezone-aware cross-reference

`MedicationStatus` is built by `_build_medication_status(profile, med_log_rows, now_utc, user_tz)`. For each medication in `ADHDProfile.medication_schedule` and each `MedicationWindow`:

1. Convert `(window.start, window.end)` from local time to UTC using `datetime.combine(local_today, window.start, tzinfo=user_tz).astimezone(UTC)`.
2. Search `med_log_rows` for a matching `medication_name` with `taken_at` within the window ± 30 minutes grace.
3. Classify as `taken`, `missed` (window has passed), or `pending` (window is future).

The `±30 minute grace` is hardcoded as `grace = timedelta(minutes=30)`.

### `_estimate_energy()` — energy blending algorithm

```
inputs: signals (list[EnergySignal]), last_check_in (dict | None), now (datetime)
```

1. **Self-report short-circuit**: if `last_check_in` was within 2 hours of `now`, return it verbatim with `is_guess=False` and `confidence=1.0`.
2. **Signal blending**: start at `latent=0.5`, `confidence=0.4` (baseline floor). For each signal: `latent += adjustment * signal.confidence`, `confidence = 1 - (1-confidence)*(1-signal.confidence)` (noisy-OR). Clamp latent to [0.0, 1.0].
3. **Bucketing**: `level` = `"low"` (<0.33), `"medium"` (0.33–0.66), `"high"` (>0.66). `focus` = `"scattered"` (<0.25), `"locked_in"` (>0.75), else `"normal"`.
4. Return `EnergyEstimate(is_guess=True)` with signal descriptions as the `signals` list.

### `_build_focus_status()` — active vs planned

Completed blocks are rows with `ended_at IS NOT NULL`; active block is the most recent row with `ended_at IS NULL`. Planned blocks come from `calendar_entries` with `kind='focus_block'` starting after `now_utc`. All times are formatted in the user's local timezone.

### `_collect_upcoming_nudges()` — proactive surfacing

Scans the first 5 calendar entries. For each entry starting within 120 minutes of now (and not yet started), appends a string like `"standup in 14min"`. If the entry kind is `"medication_window"`, the label becomes `"{title} window"`. Returns a list of up to 5 strings.

### `_compute_check_in_suggestion()` — proactive energy check-in

Returns a string suggestion (or None) based on:
1. If `last_check_in` is within `check_in_interval_minutes` (default 120, from `ADHDProfile`): return None.
2. If `session_minutes >= 120`: return "You've been going for Nmin — how's your energy?"
3. If any medication is missed: return "Looks like {name} hasn't been logged for the {window} window. How are you feeling?"
4. Otherwise: return None.

### `build_life_context()` — multi-day aggregation

Accepts `since: date`, `until: date`. All queries use `datetime.combine(..., tzinfo=user_tz).astimezone(UTC)` for bounds. Aggregators:

| Aggregator | Output |
|---|---|
| `_aggregate_medication_adherence` | `{taken_count, expected_count, rate}` — rate = taken / (windows_per_day * total_days) |
| `_aggregate_focus_summary` | `{total_hours, daily_avg, trend, best_day, by_weekday}` — trend from first-half vs second-half daily averages |
| `_aggregate_meals_summary` | `{avg_per_day, by_type, skipped_patterns}` — `skipped_patterns` flags meal types seen < half the days |
| `_aggregate_routine_completion` | `{routine_name: {completed, total}}` |
| `_aggregate_finance_summary` | `{total_spend, by_category, impulse_count, impulse_total}` or None |
| `_aggregate_energy_trend` | `{overall, morning_avg, afternoon_avg}` based on `energy_log` hour |
| `_aggregate_items_summary` | `{completed, created, carried_over, overdue}` |

**Trend detection** (commit `187513e`): `_aggregate_focus_summary` splits daily focus hours into first half and second half of the date range. If second-half average < 0.7× first-half: `"declining"`. If > 1.3×: `"improving"`. Otherwise: `"stable"`. This was previously dead code — the comparison existed but was never reachable because the `by_day` aggregation was missing.

### `generate_insights()` — 4 rule-based insights

Called at the end of `build_life_context`. Pure rule-based, no LLM:

| Rule | Condition | Insight text |
|---|---|---|
| 1 | med adherence rate < 0.7 AND daily focus avg < 2.0 hours | "Focus has been lower on days you skip doses — worth noting, no judgment." |
| 2 | "lunch" in skipped_patterns AND focus trend == "declining" | "You tend to skip lunch on busy days — focus dips those afternoons." |
| 3 | best weekday by focus hours | "{Day} has been your most productive day." |
| 4 | Any routine with completed >= 5 sessions | "Nice streak on {name} — {completed}/{total} days." |

Note: All insight strings are written to avoid blame framing ("no judgment" is explicit in Rule 1).

### Tools that invoke the ContextEngine

| Tool | How it uses ContextEngine |
|---|---|
| `day_briefing(date)` | Calls `engine.build_day_context(target_date)`, serializes to JSON |
| `life_summary(since, until)` | Calls `engine.build_life_context(since, until, label)` |
| `draft_plan(...)` | Optionally calls `engine.build_life_context` to embed a `LifeContext` in the plan prompt |

`draft_plan` also reads `adhd_module.planning_adjustments()` to apply ADHD-specific constraints.

---

## End-to-end: "A day in the life"

**6:45 AM** — User opens Kora. The supervisor graph calls `engine.build_day_context()`. `_build_medication_status` finds the Adderall 8:00-9:00 window is future → `pending`. No self-report in `energy_log` within 2 hours → `_estimate_energy` uses signals: no morning meetings → `open_morning` signal (+0.15). `latent=0.65` → `level="medium"`, `focus="normal"`. The "## Today (Monday, April 14)" block renders: "Energy: medium/normal (guess — no check-in yet today)".

**8:22 AM** — User says "just took my Adderall 20mg". Supervisor calls `log_medication(medication_name="Adderall", dose="20mg")`. Next turn: `build_day_context` re-runs. `_build_medication_status` now finds a match within the 8:00-9:00 window (+30 min grace). `taken` list populated. `ADHDModule.energy_signals` returns `MEDS_TAKEN_ADJUSTMENT=+0.2` signal. `latent` → 0.7 → `level="high"`.

**10:15 AM** — User says "let's start my morning routine". Supervisor calls `list_routines()`, then `start_routine(routine_id="...", session_id="...", variant="standard")`. Returns `RoutineSessionState` with `status="active"`, `current_step_index=0`. User works through steps; each `advance_routine(session_id, step_index)` call updates `completed_steps` and computes `completion_confidence`.

**2:30 PM** — User hasn't logged energy since morning. Session has been running for 90 minutes. `_compute_check_in_suggestion` checks: 90 min < 120 threshold → None. Still no suggestion yet.

**3:45 PM** — Session now 150 minutes. `_compute_check_in_suggestion`: 150 min >= 120 → returns "You've been going for 150min — how's your energy?". The supervisor renders `[Check-in idea: You've been going for 150min — how's your energy?]` in the dynamic suffix.

**4:00 PM** — User says "I reschedule the dentist to 3pm tomorrow". `update_plan` fires with `action="reschedule"`, `reschedule_to="2026-04-15T15:00:00"`. `local_hour = 15`. Profile has `crash_periods=[(14, 16)]`. Match → `warnings` list gets "Dentist rescheduled into your usual crash window (14-16h)". Supervisor surfaces this gently.

---

## Integration points

- **`graph/supervisor.py`**: calls `engine.build_day_context()` every turn; injects result into `state["day_context"]`.
- **`graph/prompts.py`**: `_render_today_block(day_context, state)` renders the `## Today` block in the dynamic suffix.
- **`core/di.py`**: `context_engine` property lazy-builds `ContextEngine(db_path, adhd_module, user_tz_name=settings.user_tz)`. `routine_manager` initialized in `initialize_phase4()`.
- **`core/db.py`**: `init_operational_db()` creates all life-domain tables in `data/operational.db`.
- **`adhd/module.py`**: `ADHDModule.energy_signals()` is the signal source for `_estimate_energy`.
- **`runtime/orchestration/decisions.py`**: `OpenDecisionsTracker` replaces the old autonomous-only decision path; `record_decision` supervisor tool writes to it, autonomous `WorkerTask`s transition to `PAUSED_FOR_DECISION` via it. See [`../01-runtime-core/orchestration.md`](../01-runtime-core/orchestration.md).
- **`runtime/orchestration/notifications.py`**: `NotificationGate` is the delivery chokepoint for every proactive surfacing produced by this module; see [`adhd.md`](adhd.md) § NotificationGate.

## Known limitations and stubs

- **Reminder acceptance is not fully proven in the latest short run**: code has `ReminderStore` and `continuity_check` delivery, but the 2026-04-28 short acceptance report had `Reminders: 0` and left routine-created reminder delivery red.
- **`energy_log` write tool is absent**: `energy_log` is read by `ContextEngine` and the `_aggregate_energy_trend` aggregator, but there is no `log_energy` tool in `life_management.py`. Self-reported energy must currently come from the supervisor or a direct DB insert. This is likely intended for Phase 8.
- **`checkpoint_state` and `last_nudge_at` in `routine_sessions`**: both columns exist in the schema but are never written by `RoutineManager`. They are reserved for future autonomous routine execution and proactive nudging.
- **`finance_summary` insight**: the 4-rule insights generator does not include a finance rule despite the `finance_summary` field being present in `LifeContext`.
- **`routine` mode in autonomous graph**: the `routines.py` module docstring references running routines "through the same Phase 6A runtime graph with mode='routine'", but the tool layer does not currently construct an autonomous plan for routine sessions.
