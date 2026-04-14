# Context Subsystem (`kora_v2/context/`)

The context subsystem manages everything that lives inside a single conversation's active window. It has three responsibilities: (1) assembling a small set of high-priority "working memory" items for injection into each turn's system prompt; (2) tracking the token budget of the conversation history and triggering compression when thresholds are crossed; and (3) aggregating operational data from the ADHD module and life-management tables into `DayContext` and `LifeContext` objects consumed by the supervisor and planning tools.

---

## Files in this module

| File | Role |
|---|---|
| [`working_memory.py`](../../kora_v2/context/working_memory.py) | `WorkingMemoryLoader` + deprecated `estimate_energy()` |
| [`budget.py`](../../kora_v2/context/budget.py) | `ContextBudgetMonitor`, token counting, `BudgetTier` enum |
| [`compaction.py`](../../kora_v2/context/compaction.py) | 4-stage compaction pipeline: prune → summarize → aggressive → hard stop |
| [`engine.py`](../../kora_v2/context/engine.py) | `ContextEngine` — aggregates operational DB into `DayContext` / `LifeContext` |

---

## `working_memory.py` — Working Memory

### `WorkingMemoryLoader`

Loads up to 5 prioritized items that are injected into the supervisor's turn-level system prompt suffix. This is Kora's short-term "what matters right now" layer — distinct from the full conversation history.

```python
loader = WorkingMemoryLoader(projection_db, items_db, last_bridge)
items = await loader.load()   # returns list[WorkingMemoryItem], max 5
```

**Sources (priority order):**

| Priority | Source | Content |
|---|---|---|
| 1 (highest) | `SessionBridge.open_threads` | Unresolved topics from the previous session |
| 2 | `items` table | Items not done/cancelled, due within 48h |
| 3 | `projection_db` (placeholder) | Commitments — not yet implemented |

The 48h query for items uses `due_date <= cutoff` where `cutoff = now + 48h`. Column name is `type` (not `item_type` — the previous bug used `item_type` which silently returned nothing; fixed in Phase 5).

Items are sorted by `priority` ascending (lower number = higher priority), then capped at 5.

### `estimate_energy()` — DEPRECATED

Kept as a thin time-of-day fallback but marked deprecated. New code should use `ContextEngine._estimate_energy()` which incorporates medication status, calendar signals, self-reports, and ADHD module signals.

The fallback uses a hardcoded time-of-day curve:

| Hours | Energy level | Focus |
|---|---|---|
| 06–09 | medium | moderate |
| 09–12 | high | locked_in |
| 12–14 | medium | moderate |
| 14–16 | low | scattered |
| 16–21 | medium | moderate |
| 21–06 (default) | low | scattered |

When an `adhd_profile` dict is provided with `peak_windows` or `crash_periods`, those override the defaults for matching hours.

Returns `EnergyEstimate` with `confidence=0.4` (low — time-of-day only, no behavioral signals).

---

## `budget.py` — Context Budget Monitor

The budget monitor prevents context overflow in the MiniMax M2.7 205K-token window. MiniMax's documentation warns that the model terminates prematurely near capacity, so proactive management is critical.

### `BudgetTier` (StrEnum)

| Tier | Threshold | Action |
|---|---|---|
| `NORMAL` | 0–100K tokens | No action |
| `PRUNE` | 100K–150K | Mask old tool results + strip thinking blocks |
| `SUMMARIZE` | 150K–175K | LLM summary of middle turns |
| `AGGRESSIVE` | 175K–195K | Merge new turns into existing summary |
| `HARD_STOP` | 195K+ | Refuse generation |

### Budget allocation

The token window is divided into named buckets for planning purposes (not enforced at the API level, but used to understand headroom):

```
tools:                3,000
system_prompt:        6,000
conversation:       120,000
tool_context:        15,000
output_and_thinking: 50,000
safety_margin:       11,000
─────────────────────────────
total:              205,000
```

### Token counting

**Primary**: `tiktoken` with `cl100k_base` encoding. If `tiktoken` is not installed, falls back to a character-based estimate (`max(1, len(text) // 4)`), which intentionally overestimates to trigger compaction early rather than risk overflow.

**`count_tokens(text) -> int`**: Single-text counter. Returns 0 for empty strings.

**`count_message_tokens(message) -> int`**: Counts one message dict including a 4-token per-message overhead. Handles:
- String `content` (direct text)
- List `content` (content blocks: `text`, `thinking`, `tool_use`, `tool_result`)
- Tool call arguments via `json.dumps(input_data)`

**`count_messages_tokens(messages) -> int`**: Sums `count_message_tokens` across all messages.

### `ContextBudgetMonitor`

```python
monitor = ContextBudgetMonitor(context_window=200_000)
tier = monitor.get_tier(messages, system_prompt, tools)
remaining = monitor.remaining_budget(messages, system_prompt, tools)
refused = monitor.should_refuse_generation(messages, system_prompt, tools)
```

**`get_tier()`** calls `estimate_current_usage()` (sums messages + system_prompt + tools JSON) and compares against scaled thresholds.

**Threshold scaling**: Thresholds are scaled proportionally when `context_window != 200_000`. This ensures test cases with smaller windows still exercise all tiers. Tiers are kept strictly increasing even on very small windows via `max(scaled[tier], last_value + 1)`.

**Stateless design**: The monitor does not accumulate state between calls. `remaining_budget()` is based on actual message content, not cumulative tracking, because each API call re-submits the full history. The `reset()` method exists for symmetry but is a no-op.

---

## `compaction.py` — Compaction Pipeline

When `ContextBudgetMonitor.get_tier()` returns anything above `NORMAL`, the compaction pipeline runs to reduce the conversation footprint. It has four stages of increasing aggression.

### Trigger flow

```
turn_runner.py calls budget_monitor.get_tier(messages)
  ├─ NORMAL → no action
  ├─ PRUNE, SUMMARIZE, AGGRESSIVE → run_compaction(messages, tier, llm)
  └─ HARD_STOP → session manager calls build_hard_stop_bridge()
```

### Stage 1: PRUNE — `mask_observations(messages, preserve_last_n=10)`

**What it does**: In messages outside the last N turns, replaces large tool results and strips thinking blocks.

- **Tool results** (`role="tool"`, content > 200 chars): replaced with `"[result from {tool_name}: {first_line}...]"`
- **Thinking blocks** in assistant messages: removed entirely (only text blocks kept)

A "turn" is defined as one user message plus all subsequent assistant/tool messages until the next user message. The `_find_turns()` helper groups message indices into turns. The last N turns are left untouched.

Tool name resolution (`_get_tool_name_for_call_id`): scans backward through preceding assistant messages to find the `tool_use` block with a matching `id`. Falls back to `"unknown_tool"` if not found.

**Output**: Deep copy of messages with modified entries. Original is untouched.

### Stage 2: SUMMARIZE — `apply_structured_compaction(messages, llm, preserve_first_n=2, preserve_last_n=10)`

**What it does**: Mask first, then produce an LLM summary of middle turns.

Structure:
```
[first 2 messages unchanged]
[system message: 6-section structured summary]
[last 10 messages unchanged]
```

Tool pair boundary check (`_tool_pair_safe_boundary`): adjusts the slice boundary so a `tool_call` is never separated from its `tool_result`. If the boundary falls on an assistant message with pending tool calls, the boundary moves back by 1.

The 6-section summary template (used in `_SUMMARY_SYSTEM_PROMPT`):
```
## Goal
## Progress (Done / In Progress / Blocked)
## Key Decisions
## Emotional Context
## Open Threads
## Critical Context
```

LLM call: `temperature=0.1`, `max_tokens=2000`. Every section must be populated or marked `[none]`. Numeric values, file paths, names, and dates must be preserved verbatim.

### Stage 3: AGGRESSIVE — `aggressive_recompress(messages, existing_summary, llm)`

**What it does**: UPDATE mode — merges new turns into an existing summary rather than rebuilding from scratch.

Structure after aggressive recompression:
```
[first 2 messages (anchor)]
[system message: updated summary]
[last 3 turns]
```

LLM call: `temperature=0.05`, `max_tokens=1500`. System prompt instructs "Do NOT rebuild from scratch — incorporate new information into the existing structure."

If the LLM call fails, the existing summary is preserved as the fallback.

### Stage 4: HARD_STOP — `build_hard_stop_bridge(messages, session_id) -> SessionBridge`

**What it does**: Heuristic-only bridge note. No LLM call (context is too full to afford one).

- **Summary**: last 3 user messages, joined by ` | `, truncated to 200 chars each
- **Open threads**: any message in the last 10 messages whose text ends with `?`, up to 300 chars each
- **Emotional trajectory**: hardcoded `"ongoing conversation"` (no LLM to assess)

Returns a `SessionBridge` model, which is handed to `WorkingMemoryLoader` for the next session's priority-1 items.

### `run_compaction(messages, tier, llm, existing_summary) -> CompactionResult | None`

Main entry point. Routes to the correct stage. Returns `None` for NORMAL and HARD_STOP (the latter is handled separately by the session manager).

For SUMMARIZE, it masks first then summarizes, but reports `tokens_before` from before masking for accurate reporting.

### `CompactionResult` (from `kora_v2.core.models`)

Fields: `stage`, `messages`, `tokens_before`, `tokens_after`, `messages_removed`, `messages_masked`, `summary_text`.

---

## `engine.py` — ContextEngine

The `ContextEngine` is a read-only aggregation layer that queries operational database tables and produces structured context objects for the supervisor and planning tools. It does not own or modify any data.

### Constructor

```python
engine = ContextEngine(db_path, adhd_module, user_tz_name="UTC")
```

`db_path` points to `data/operational.db`. `adhd_module` provides the user's ADHD profile and signal-generation logic.

### `build_day_context(target_date, session_state) -> DayContext`

Produces an ambient "today" awareness object by querying 9 tables in a single `aiosqlite.connect()` block:

| Table | Purpose |
|---|---|
| `calendar_entries` | Today's schedule (via `_load_entries_between`) |
| `medication_log` | Doses taken today |
| `meal_log` | Meals eaten today |
| `focus_blocks` | Active and completed focus sessions |
| `routine_sessions` | Routine completion status |
| `finance_log` | Today's spending |
| `items` | Items due today (status NOT done/cancelled, `due_date = target_date`) |
| `energy_log` | Most recent self-report (source=`"self_report"`) |

**Items due today query note**: `due_date` is stored as a bare `YYYY-MM-DD` string (not a datetime). The query uses `due_date = target_date.isoformat()` — not a range comparison. Range comparisons against ISO datetime strings would fail lexically.

**DayContext fields** (partial):
- `schedule: list[CalendarEntry]` — full day schedule
- `next_event` and `minutes_until_next`
- `medication_status: MedicationStatus` — taken/pending/missed per window
- `meals_logged`, `focus_blocks: FocusBlockStatus`, `routine_status: RoutineStatus`
- `finance_today`, `energy: EnergyEstimate`
- `hyperfocus_mode`, `session_duration_min`, `items_due`, `plan_status`
- `check_in_suggestion: str | None` — suggested prompt for user check-in
- `upcoming_nudges: list[str]` — events within 2h formatted as "X in Nmin"

### `build_life_context(since, until, label) -> LifeContext`

Produces a multi-day aggregate across 7 tables: `medication_log`, `focus_blocks`, `meal_log`, `routine_sessions`, `finance_log`, `energy_log`, `items`.

After aggregation, `generate_insights()` applies 4 rule-based insight triggers:
1. If medication adherence rate < 0.7 AND daily focus avg < 2.0h → "Focus has been lower on days you skip doses"
2. If lunch is in `skipped_patterns` AND focus trend is "declining" → correlation note
3. Best weekday by focus hours → "X has been your most productive day"
4. Routine streaks ≥ 5 completed sessions → streak acknowledgment

**Finance impulse detection** for `LifeContext`: the `finance_summary` tracks `impulse_count` and `impulse_total`. Impulse detection itself happens at write time in `tools/life_management.py::log_expense()`.

### Energy estimation — `_estimate_energy(signals, last_check_in, now)`

Used internally by `build_day_context()`:

1. **Self-report short-circuit**: if the last self-report was within 2 hours, return it with `confidence=1.0` and `is_guess=False`
2. **Signal blending**: start at `latent=0.5`, add each signal's `level_adjustment × confidence`, accumulate confidence via noisy-OR: `confidence = 1 - (1-c)(1-s_confidence)`
3. **Bucket snap**: `latent < 0.33 → low`, `> 0.66 → high`, else `medium`; `latent < 0.25 → scattered`, `> 0.75 → locked_in`, else `normal`
4. `is_guess=True` for signal-blended estimates

### Medication status — `_build_medication_status(profile, med_log_rows, now, user_tz)`

Cross-references `ADHDProfile.medication_schedule` against today's log rows. For each medication × window combination:
- A log entry within `[window_start - 30min, window_end + 30min]` → `taken`
- Current time past window end with no match → `missed` (with `hours_overdue`)
- Current time before window end with no match → `pending`

### Check-in suggestion — `_compute_check_in_suggestion()`

Returns a nudge string when:
- Session has been running ≥ 120 minutes (without a recent check-in), OR
- A medication dose is missed

The `check_in_interval_minutes` from the ADHD profile prevents nagging — if the last check-in was within that interval, returns `None`.

### Upcoming nudges — `_collect_upcoming_nudges(schedule, now_utc)`

Scans the first 5 schedule entries for events within the next 2 hours (excluding `buffer` kind). Returns strings like `"standup in 45min"` or `"Adderall window in 12min"`.

---

## Integration points

**Called by:**
- `kora_v2/runtime/turn_runner.py` — calls `ContextBudgetMonitor.get_tier()` and `run_compaction()`
- `kora_v2/graph/supervisor.py` — uses `WorkingMemoryLoader` results in system prompt suffix; calls `ContextEngine.build_day_context()` for ambient awareness
- `kora_v2/tools/planning.py` — calls `engine.build_day_context()` and `engine.build_life_context()` via `draft_plan`, `day_briefing`, `life_summary` tools

**Calls:**
- `kora_v2/core/models.py` — `EnergyEstimate`, `DayContext`, `LifeContext`, `CompactionResult`, `SessionBridge`, `WorkingMemoryItem`, `MedicationStatus`, `FocusBlockStatus`, `RoutineStatus`
- `kora_v2/adhd/module.py` — `ADHDModule.energy_signals()`, `focus_detection()`
- `kora_v2/adhd/profile.py` — `ADHDProfile` with medication schedule, peak windows, crash periods
- `kora_v2/tools/calendar.py` — `_load_entries_between()` for schedule queries
- `aiosqlite` — operational.db queries
