# Tools Subsystem (`kora_v2/tools/`)

The tools subsystem provides the LLM's ability to take action. Every tool is an async Python function registered in a global singleton `ToolRegistry`. Tools are exported to the Anthropic API as JSON Schema definitions, invoked by the turn runner when the LLM emits a `tool_use` block, and their results are returned as JSON strings. The subsystem covers memory recall, filesystem I/O, life management (medication, meals, focus blocks, expenses, notes, reminders), calendar operations, planning and task management, and routines.

---

## Files in this module

| File | Role |
|---|---|
| [`types.py`](../../kora_v2/tools/types.py) | Core data models: `ToolDefinition`, `ToolCall`, `ToolResult`, `AuthLevel`, `ToolCategory` |
| [`registry.py`](../../kora_v2/tools/registry.py) | `ToolRegistry` singleton, `@tool` decorator, `ScopedToolRegistry`, schema cleaning |
| [`recall.py`](../../kora_v2/tools/recall.py) | `recall()` — fast hybrid memory search, no LLM |
| [`filesystem.py`](../../kora_v2/tools/filesystem.py) | `write_file`, `read_file`, `create_directory`, `list_directory`, `file_exists` |
| [`life_management.py`](../../kora_v2/tools/life_management.py) | Medication, meal, reminder, quick note, focus block, expense tools |
| [`planning.py`](../../kora_v2/tools/planning.py) | `draft_plan`, `update_plan`, `day_briefing`, `create_item`, `complete_item`, `defer_item`, `query_items`, `life_summary` |
| [`calendar.py`](../../kora_v2/tools/calendar.py) | Calendar entry CRUD, RRULE expansion, Google Calendar sync |
| [`routines.py`](../../kora_v2/tools/routines.py) | `list_routines`, `start_routine`, `advance_routine`, `routine_progress` |
| [`truncation.py`](../../kora_v2/tools/truncation.py) | `truncate_tool_result()` — structure-aware result truncation |
| [`verb_resolver.py`](../../kora_v2/tools/verb_resolver.py) | `DomainVerbResolver` — verb-to-tool-name mapping |

---

## `types.py` — Core Data Models

### `AuthLevel` (StrEnum)

Controls whether tool execution requires user confirmation:

| Value | Meaning |
|---|---|
| `ALWAYS_ALLOWED` | Executes without any check (read-only queries) |
| `ASK_FIRST` | Pause and confirm with user before executing |
| `NEVER` | Reject, suggest manual action |

### `ToolCategory` (StrEnum)

Logical groupings: `MEMORY`, `TASKS`, `USER_MODEL`, `ENTITIES`, `SELF`, `WORKFLOWS`, `FILESYSTEM`, `WEB`, `CALENDAR`, `MESSAGING`, `AGENTS`, `SHELL`, `LIFE_MANAGEMENT`, `SCREEN`.

Categories are used by `ToolRegistry.get_by_category()` and `get_anthropic_tools(categories=...)` to expose subsets of tools to different agents.

### `ToolDefinition` (Pydantic model)

The complete description of a registered tool:

```python
ToolDefinition(
    name: str,                     # unique snake_case name
    description: str,              # shown to the LLM
    category: ToolCategory,
    auth_level: AuthLevel,
    parameters_schema: dict,       # cleaned JSON Schema from Pydantic model
    internal: bool,                # True = Python function, False = MCP
    is_read_only: bool,            # auto-detected from name prefix if not set
    timeout_seconds: float | None, # per-tool override
)
```

**`to_anthropic_tool() -> dict`**: Converts to `{name, description, input_schema}` format for the Anthropic API.

### `ToolCall` (Pydantic model)

A tool invocation extracted from the LLM response: `id` (8-char UUID prefix), `name`, `arguments: dict`.

### `ToolResult` (Pydantic model)

The result of executing a tool: `tool_call_id`, `tool_name`, `content: str | None`, `error: str | None`, `error_category: ErrorCategory | None`, `success: bool`, `details: dict | None` (raw structured data, never truncated), `truncated: bool`, `total_count: int | None`.

### `ErrorCategory` (StrEnum)

Classifies failures to guide LLM retry behavior: `TRANSIENT` (retry), `NOT_FOUND` (inform LLM), `PERMISSION` (escalate to user), `VALIDATION` (fix arguments), `FATAL` (stop).

---

## `registry.py` — ToolRegistry and @tool Decorator

### `ToolRegistry` (singleton)

All `@tool`-decorated functions register into a single global instance. The registry stores `_RegisteredTool` wrappers (definition + function + input Pydantic model).

```python
# Look up a tool by name
registered = ToolRegistry.get("recall")
func = ToolRegistry.get_callable("recall")
model = ToolRegistry.get_input_model("recall")

# Export to Anthropic format (all tools, or filtered by category)
tools_json = ToolRegistry.get_anthropic_tools()
tools_json = ToolRegistry.get_anthropic_tools(categories={ToolCategory.MEMORY})

# Build tool_choice for Anthropic API
choice = ToolRegistry.get_tool_choice("AUTO")          # {"type": "auto"}
choice = ToolRegistry.get_tool_choice("TOOL:recall")   # {"type": "tool", "name": "recall"}
choice = ToolRegistry.get_tool_choice("ANY")           # {"type": "any"}
choice = ToolRegistry.get_tool_choice("NONE")          # {"type": "none"}
```

**Read-only auto-detection**: If `is_read_only` is not explicitly set, it is inferred from the tool name prefix:
- Prefixes `search_`, `get_`, `query_`, `read_`, `check_` → `is_read_only=True`
- Prefixes `create_`, `update_`, `add_`, `delete_`, `remove_`, `plan_` → `is_read_only=False`
- Anything else → `False` (safe default)

**`create_scoped_registry(tool_names) -> ScopedToolRegistry`**: Creates a non-singleton instance containing only the specified tools. Used by sub-agents that need limited tool access.

### `ScopedToolRegistry`

A non-singleton variant of `ToolRegistry`. Has the same read methods (`get`, `get_callable`, `get_anthropic_tools`, etc.) but is an independent instance. Created via `ToolRegistry.create_scoped_registry()`.

### `@tool` decorator

```python
@tool(
    name="recall",
    description="Search memory via hybrid vector + FTS5 search.",
    category=ToolCategory.MEMORY,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
    timeout_seconds=None,
)
async def recall(input: RecallInput, container: Any) -> str:
    ...
```

**Contract**:
1. The function must be `async`
2. First parameter must be a Pydantic `BaseModel` subclass (inspected at decoration time via `inspect.signature()`)
3. Second parameter receives the service container (typed `Any` for flexibility)
4. Must return `str`

**Why no `from __future__ import annotations`**: The `@tool` decorator uses `inspect.signature()` to extract the input model's type at decoration time. PEP 563 (stringified annotations, enabled by `from __future__ import annotations`) breaks `issubclass(input_type, BaseModel)`. All tool files therefore intentionally omit this import.

### `_clean_schema(schema) -> dict`

Resolves Pydantic-generated JSON Schema for API compatibility:
- Resolves `$ref` references against `$defs`
- Flattens `anyOf` patterns from `Optional[X]` → single type (drops `null` variant)
- Handles nested `properties` and `items` recursively
- Preserves all standard JSON Schema keys (`type`, `description`, `enum`, `default`, `minimum`, `maximum`, `pattern`, `format`, etc.)

### `get_schema_tool(name, description, schema) -> dict`

Creates a "Tool-as-Schema" definition used with `tool_choice={"type": "tool", "name": name}` to force the LLM to produce a specific JSON structure (structured output pattern).

---

## `recall.py` — Fast Memory Recall

```python
result = await recall(query, layer="all", max_results=10, container=container)
```

`recall()` is the fast, deterministic memory path. No LLM call is made.

### Flow

1. Validate `query` is non-empty and `container` is not None
2. Retrieve `container.embedding_model` and `container.projection_db`
3. Call `embedding_model.embed(query, task_type="search_query")` — applies `"search_query: "` prefix for asymmetric retrieval
4. Call `memory.retrieval.hybrid_search(db, query, query_embedding, layer, max_results)`
5. Format results as JSON list with fields: `id`, `content`, `summary`, `type`, `importance`, `score`, `source`

### Return format

Success:
```json
{"results": [{"id": "...", "content": "...", "type": "episodic", "importance": 0.7, "score": 0.8234, "source": "long_term"}, ...]}
```

Empty results:
```json
{"results": [], "message": "No memories found matching this query. ..."}
```

### Empty recall dedup

A module-level list `_recent_empty_recalls` tracks `(timestamp, query)` pairs within a 120-second window. If the same search (or similar searches) returns empty 3+ times within 120 seconds, an additional message is appended: "This query has been attempted multiple times with no results. Consider proceeding without memory recall." This prevents agents from looping on fruitless recall attempts.

---

## `filesystem.py` — Filesystem Tools

Five tools for real file I/O. All return JSON strings (`{"success": true, ...}` or `{"success": false, "error": "..."}`).

### Path safety — `_resolve_safe(path_str) -> Path | None`

Resolves the path via `Path.expanduser().resolve()` and checks against a blocked-prefix list:
```
/etc, /sys, /proc, /dev, /boot, /sbin, /bin, /usr/bin, /usr/sbin,
/var/run, /var/log, /private/etc, /private/var/run
```
Returns `None` if blocked. This prevents the LLM from accidentally reading system files.

### Tools

| Tool | Auth | Read-only | Description |
|---|---|---|---|
| `write_file(path, content)` | `ASK_FIRST` | No | Create or overwrite file; returns bytes written |
| `read_file(path)` | `ALWAYS_ALLOWED` | Yes | Read text; refuses files > 1MB |
| `create_directory(path)` | `ASK_FIRST` | No | `mkdir -p` semantics |
| `list_directory(path)` | `ALWAYS_ALLOWED` | Yes | Lists children with type and size |
| `file_exists(path)` | `ALWAYS_ALLOWED` | Yes | Returns `exists` bool and `type` (file/directory/missing) |

Maximum file read size: 1MB (`_MAX_READ_BYTES = 1 * 1024 * 1024`). Files read with `encoding="utf-8", errors="replace"`.

---

## `life_management.py` — Life Management Tools

ADHD-oriented tools that write to and read from `data/operational.db`. All tools get the database path via `container.settings.data_dir / "operational.db"`.

### Medication tools

| Tool | Auth | Table | Description |
|---|---|---|---|
| `log_medication(medication_name, dose, notes)` | `ASK_FIRST` | `medication_log` | Record a dose; always log when user mentions taking meds |
| `query_medications(days_back, medication_name, limit)` | `ALWAYS_ALLOWED` | `medication_log` | Query log; most-recent first; substring filter on name |

`log_medication` description explicitly instructs: "ALWAYS call this tool when the user mentions taking medication, even casually." This is intentional behavioral guidance for the LLM.

### Meal tools

| Tool | Auth | Table | Description |
|---|---|---|---|
| `log_meal(description, meal_type, calories)` | `ASK_FIRST` | `meal_log` | Record a meal; calories optional (0 = not tracked) |
| `query_meals(days_back, meal_type, limit)` | `ALWAYS_ALLOWED` | `meal_log` | Query log; most-recent first; meal_type filter |

### Reminder tools

| Tool | Auth | Table | Description |
|---|---|---|---|
| `create_reminder(title, description, remind_at, recurring)` | `ASK_FIRST` | `reminders` | Insert with status `"pending"`; `remind_at` is optional ISO timestamp |
| `query_reminders(status, limit)` | `ALWAYS_ALLOWED` | `reminders` | Filter by status; ordered by `remind_at ASC` |

### Focus block tools

| Tool | Auth | Table | Description |
|---|---|---|---|
| `start_focus_block(label, notes)` | `ASK_FIRST` | `focus_blocks` | Insert open block (`ended_at = NULL`) |
| `end_focus_block(notes, completed)` | `ASK_FIRST` | `focus_blocks` | Close most-recent open block; merges notes; returns duration in minutes |
| `query_focus_blocks(days_back, open_only, limit)` | `ALWAYS_ALLOWED` | `focus_blocks` | Lists blocks with computed `duration_minutes`; `open_only=True` for active sessions |

`end_focus_block` finds the open block via `WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1`. Notes are merged: existing + new joined by newline.

### Quick note tools

| Tool | Auth | Table | Description |
|---|---|---|---|
| `quick_note(content, tags)` | `ASK_FIRST` | `quick_notes` | Immediate capture; does not go through memory pipeline |
| `query_quick_notes(days_back, tag, limit)` | `ALWAYS_ALLOWED` | `quick_notes` | Substring filter on tags |

### Expense tools

| Tool | Auth | Table | Description |
|---|---|---|---|
| `log_expense(amount, category, description)` | `ASK_FIRST` | `finance_log` | Record expense; detects impulse spend |
| `query_expenses(days_back, category, limit)` | `ALWAYS_ALLOWED` | `finance_log` | Returns entries + category totals + grand total |

**Impulse detection in `log_expense`**: Queries the past 30 days for existing entries in the same category. Requires at least `IMPULSE_MIN_SAMPLES = 5` prior entries to avoid noisy averages. If `amount > category_avg * 1.5`, `is_impulse=True` is written to the row and a `note` field is returned explaining the comparison. The tool description explicitly warns: "do NOT shame or lecture" — the note is surfaced gently.

Categories: `food`, `transport`, `tech`, `entertainment`, `health`, `other`.

---

## `planning.py` — Planning and Task Tools

Life planning tools that operate on `DayContext`/`LifeContext` and the `items` table.

### ADHD time correction — `apply_time_correction(minutes, profile)`

Applies `profile.time_correction_factor` (default 1.5x) to user-estimated durations. This is the single canonical location for this multiplication — the docstring explicitly forbids reimplementing it elsewhere. The factor can be below 1.0 if the user reliably overestimates; no floor is enforced. Planning workers (agent execution planning) deliberately do NOT use this multiplier since those plans are agent-executed.

### Scope parsing — `_parse_scope_window(scope) -> (since, until, label)`

Converts natural-language scope strings to date ranges:
- `"today"` → today, today
- `"tomorrow"` → tomorrow, tomorrow
- `"this week"` → Monday of current week to Sunday
- `"next week"` → Monday + 7 days to Sunday + 7 days
- `"last N days"` → today - N days to today
- `"until {weekday}"` → today to next occurrence of weekday
- Unknown → today, today, original string

### Planning tools

| Tool | Auth | Description |
|---|---|---|
| `draft_plan(scope, goal)` | `ALWAYS_ALLOWED` | Builds draft from live `DayContext` (today) or `LifeContext` (date range); includes ADHD adjustments |
| `update_plan(summary, affected_entry_ids, action, ...)` | `ASK_FIRST` | Structured plan change: `delete`, `reschedule`, `shrink` calendar entries; crash-window warnings |
| `day_briefing(date)` | `ALWAYS_ALLOWED` | Returns full `DayContext` for a date as JSON |
| `life_summary(since, until)` | `ALWAYS_ALLOWED` | Returns `LifeContext` for a time range |

**`update_plan` crash-window warning**: When rescheduling, the new start time is checked against `profile.crash_periods` (list of `[start_hour, end_hour]` pairs) in the user's local timezone. If the new time falls in a crash window, a warning string is added to the response. This comparison uses `.astimezone(user_tz).hour` so that e.g. PDT 3pm (UTC 22:00) correctly matches a `[14, 16]` window.

**`update_plan` conflict detection**: After rescheduling, a second database query detects other `active` entries that overlap the new time slot. Conflicts are reported in `new_conflicts`.

### Item tools

| Tool | Auth | Description |
|---|---|---|
| `create_item(title, description, due_date, priority, goal_scope, energy_level, estimated_minutes)` | `ASK_FIRST` | Inserts into `items`; applies 1.5x time correction; records state history entry |
| `complete_item(item_id, notes)` | `ASK_FIRST` | Updates status to `"done"`; records state history |
| `defer_item(item_id, to_when)` | `ASK_FIRST` | Updates status to `"deferred"`, sets new due date via `_parse_scope_window`; records state history |
| `query_items(status, due_before, goal_scope)` | `ALWAYS_ALLOWED` | Reads `items` table; `due_before` truncated to `YYYY-MM-DD` before comparison; `NULL`-safe sort |

**`goal_scope` values**: `task`, `daily_goal`, `weekly_goal`, `monthly_goal`, `someday`.

**`query_items` NULL-safe sort**: Uses `ORDER BY (due_date IS NULL), due_date ASC, priority ASC` so items with real due dates appear before those without.

**`due_date` storage**: Stored as bare `YYYY-MM-DD` (not ISO datetime). The `create_item` tool writes `input.due_date` directly. The `query_items` tool slices `input.due_before[:10]` before comparison to handle callers who pass full datetime strings.

---

## `calendar.py` — Calendar Tools

Manages calendar entries in `calendar_entries` table. Supports recurring entries via `dateutil.rrule` expansion.

### `expand_recurring(rule_str, dtstart, until) -> list[datetime]`

RFC 5545 RRULE expansion via `dateutil.rrulestr`. Returns occurrence datetimes between `dtstart` and `until`.

### `CalendarSync`

A thin bidirectional sync helper against the Google Calendar MCP server. Phase 5 wires the interface; full sync scheduling is deferred to Phase 8.

### Calendar tools

| Tool | Auth | Description |
|---|---|---|
| `create_calendar_entry(title, starts_at, ends_at, kind, recurring_rule, notes)` | `ASK_FIRST` | INSERT into `calendar_entries`; validates `kind` against `CalendarKind` |
| `query_calendar(date, lookahead_days)` | `ALWAYS_ALLOWED` | Reads entries in date range; expands recurring entries at query time |
| `update_calendar_entry(entry_id, ...)` | `ASK_FIRST` | Partial update; preserves unchanged fields |
| `delete_calendar_entry(entry_id)` | `ASK_FIRST` | Soft-delete (sets `status="cancelled"`) |
| `sync_google_calendar(...)` | `ASK_FIRST` | Calls Google Calendar MCP; stub in Phase 5 |

Recurring entries store `recurring_rule` (RRULE string) in the template row. Exception rows (individual overrides) prevent those occurrences from being re-expanded.

Synthetic IDs for expanded recurring occurrences use `::` as a separator (`base_id::occurrence_iso_date`), accessible via `SYNTHETIC_ID_SEP`.

---

## `routines.py` — Routine Tools

Four tools for guided routine lifecycle. All delegate to `container.routine_manager` (a `RoutineManager` instance from `kora_v2/life/`).

| Tool | Auth | Description |
|---|---|---|
| `list_routines(tags)` | `ALWAYS_ALLOWED` | List available routine templates; optional tag filter |
| `start_routine(routine_id, session_id, variant)` | `ASK_FIRST` | Begin a new session; `variant` is `"standard"` or `"low_energy"` |
| `advance_routine(session_id, step_index, skipped)` | `ASK_FIRST` | Complete or skip a step; returns progress percentage |
| `routine_progress(session_id)` | `ALWAYS_ALLOWED` | Check progress; returns counts, percentage, shame-free message |

`routine_progress` description says "shame-free progress message" — the message wording comes from `RoutineManager.get_progress()` and is explicitly designed to avoid language that could trigger Rejection Sensitive Dysphoria (RSD) in ADHD users.

---

## `truncation.py` — Structure-Aware Truncation

`truncate_tool_result(result, budget_tier) -> TruncationResult` replaces naive character cutoff with intelligent strategies:

### Character limits by budget tier

| Tier | Limit |
|---|---|
| `NORMAL` | 4,000 chars |
| `PRUNE` | 3,000 chars |
| `SUMMARIZE` | 2,000 chars |
| `AGGRESSIVE` | 1,000 chars |
| `HARD_STOP` | 500 chars |

### Strategy selection (in priority order)

1. **Short-circuit**: if content ≤ limit, return unchanged
2. **Error preservation**: if content contains error markers (traceback, error:, exception:, failed:), extract from earliest marker and keep in full (up to limit)
3. **JSON array** (starts with `[`): binary search to keep first N complete items; appends `"\n... and X more items"`. Returns total array count.
4. **JSON object** (starts with `{`): tries to preserve named list keys (`runs`, `events`, `templates`, `items`, `results`) first, then falls back to keeping first N key-value pairs with `"\n... and X more keys"` suffix
5. **Line-aware** (contains `\n`): keeps header + first N lines with `"\n... (X more lines)"` suffix
6. **Head+tail** (plain text fallback): 70% head + 30% tail split with `"\n\n... [truncated] ...\n\n"` separator

`TruncationResult` fields: `content`, `truncated: bool`, `original_length: int`, `total_count: int | None` (set for JSON arrays).

---

## `verb_resolver.py` — DomainVerbResolver

A thin routing hint layer that maps natural language verbs to tool names.

```python
resolver = DomainVerbResolver()
resolver.resolve("remind")         # → ["create_reminder"]
resolver.resolve("remember")       # → ["recall", "store_memory"]
resolver.suggest_tools("remind me to call Sarah")  # → ["create_reminder"]
```

**Verb map** (selected entries):

| Verb | Tools |
|---|---|
| `remind` | `create_reminder` |
| `remember` | `recall`, `store_memory` |
| `research` | `search_web`, `fetch_url` |
| `plan` | `dispatch_worker` |
| `track` | `create_item`, `update_item` |
| `log` | `log_medication`, `log_meal` |
| `focus` | `start_focus_block` |
| `note` | `create_quick_note` |
| `routine` | `list_routines`, `start_routine` |
| `search` / `find` | `search_web`, `recall` |
| `schedule` | `create_reminder` |
| `check` | `routine_progress`, `recall` |

`suggest_tools(text)` splits text by whitespace and checks each word against the verb map. Does not handle multi-word verbs or stemming.

---

## Tool-calling contract

### Invocation (from turn runner)

1. LLM emits a `tool_use` content block with `id`, `name`, `input`
2. Turn runner looks up `ToolRegistry.get(name)`
3. Input model is validated: `input_model.model_validate(arguments)`
4. `auth_level` is checked: `ALWAYS_ALLOWED` proceeds immediately; `ASK_FIRST` may pause for user confirmation
5. The async function is called: `await func(validated_input, container)`
6. Result string is wrapped in `ToolResult`
7. `truncate_tool_result(result, budget_tier)` is applied if result is large
8. Returned to the LLM as a `tool_result` content block

### Container injection

Tools receive the service container as their second argument (typed `Any`). They use `getattr` to access:
- `container.embedding_model` — for `recall()`
- `container.projection_db` — for `recall()`
- `container.settings.data_dir` — for life management and planning tools
- `container.context_engine` — for planning tools
- `container.adhd_profile` — for planning tools
- `container.routine_manager` — for routine tools

### Error handling

All tools return JSON strings. Errors are returned as `{"success": false, "error": "..."}` — never raised as exceptions. This allows the LLM to read the error and decide how to proceed without a conversation-breaking exception.

---

## Integration points

**Called by:**
- `kora_v2/runtime/turn_runner.py` — dispatches tool calls, wraps results, applies truncation
- `kora_v2/graph/supervisor.py` — assembles Anthropic tools list via `ToolRegistry.get_anthropic_tools()`
- Sub-agents via `ScopedToolRegistry` for limited tool access

**Calls:**
- `kora_v2/memory/retrieval.py` — `hybrid_search()` from `recall()`
- `kora_v2/context/budget.py` — `BudgetTier` for truncation limits
- `kora_v2/context/engine.py` — `ContextEngine` for planning and day briefing tools
- `kora_v2/adhd/profile.py` — `ADHDProfile` for time correction and planning adjustments
- `kora_v2/life/` — `RoutineManager` for routine tools
- `aiosqlite` — all database-writing tools
