# Graph Subsystem

`kora_v2/graph/` — LangGraph supervisor graph, tool dispatch, state definition, reducers, prompts, and capability bridge.

The graph subsystem owns the core reasoning loop: a 5-node LangGraph `StateGraph` that receives a user turn, builds a system prompt, calls the LLM, executes tools, and synthesizes a final response. It is compiled once per daemon startup with a checkpointed state machine and reused across all conversation turns.

---

## Files

| File | Lines | Role |
|------|-------|------|
| `supervisor.py` | ~1120 | `build_supervisor_graph()`; 5 node functions; topic tracker; CJK filter |
| `dispatch.py` | ~473 | Tool definitions (`SUPERVISOR_TOOLS`); `execute_tool()`; auth check |
| `state.py` | ~94 | `SupervisorState` TypedDict with annotated reducers |
| `reducers.py` | ~648 | `add_messages_reducer`, `ensure_tool_pair_integrity`, `bounded_errors_reducer` |
| `prompts.py` | ~large | `build_frozen_prefix()`, `build_dynamic_suffix()` |
| `capability_bridge.py` | ~200+ | `collect_capability_tools()`, `execute_capability_action()` |
| `__init__.py` | ~0 | Package marker |

---

## Graph Topology

```
START
  │
  ▼
receive          (parse input, increment turn, reset per-turn state)
  │
  ▼
build_suffix     (assemble system prompt suffix, run compaction if needed)
  │
  ▼
think ◄──────────────────────────────┐
  │                                  │
  ├── tool_calls pending → tool_loop ┘  (loop back to think)
  │
  └── no tool_calls → synthesize
                          │
                         END
```

Maximum `think → tool_loop → think` iterations: `_MAX_TOOL_ITERATIONS = 12` (`supervisor.py:44`).

---

## supervisor.py — `kora_v2/graph/supervisor.py`

### `build_supervisor_graph(container)`

```python
def build_supervisor_graph(container: Any) -> CompiledGraph:
    """Build and compile the supervisor LangGraph graph with container closure."""
```

**Initialization:**
1. Calls `get_available_tools(container, active_skills=skill_names)` to resolve the full tool list
2. Falls back to `_CORE_SKILLS_FALLBACK = ["life_management", "web_research", "file_creation"]` if skill loader returns zero skills (`supervisor.py:63`)
3. Creates `iteration_count = {"value": 0}` (shared mutable dict, reset on each turn)
4. Wraps each node function as a closure over `container` and `iteration_count`
5. Adds 5 nodes, edges, and a conditional edge on `think`
6. Compiles with `container._checkpointer` if set; falls back to `MemorySaver()` with a warning

**Checkpointer selection** (`supervisor.py:1103`):
```python
checkpointer = getattr(container, "_checkpointer", None)
if checkpointer is None:
    checkpointer = MemorySaver()  # warn: state will NOT survive restart
```

### Node: `receive`

```python
async def receive(state: SupervisorState) -> dict[str, Any]:
    """Parse input, increment turn_count, reset per-turn state."""
```

Returns:
- `turn_count`: incremented by 1
- `session_id`: existing or new UUID
- `active_workers`, `tool_call_records`, `response_content`: reset to empty
- `_overlap_score`, `_overlap_action`: reset to defaults

The **`_receive` closure** (graph-internal wrapper, `supervisor.py:901`) extends `receive()` with:
- **Turn 1**: seeds `emotional_state`, `energy_estimate`, `pending_items`, and `session_bridge` from `session_manager.active_session`
- **Every turn**: runs `fast_emotion.assess()` on the latest user message; conditionally triggers `llm_emotion_assessor.assess()` via `should_trigger_llm_assessment()`
- **Turn 1 and every 10th turn**: calls `estimate_energy()` to refresh `energy_estimate`

### Node: `build_suffix`

```python
async def build_suffix(state: SupervisorState, container: Any = None) -> dict[str, Any]:
    """Build dynamic suffix; cache frozen prefix; run compaction if budget tier != NORMAL."""
```

**Frozen prefix** is built once (turn 1 only) and cached in `state["frozen_prefix"]`. Built by `build_frozen_prefix()` with:
- `skill_index`: list of skill names from skill loader (or `_CORE_SKILLS_FALLBACK`)
- `adhd_output_guidance`: from `container.adhd_module.output_guidance()` if wired
- `user_triggers`: overwhelm triggers from `adhd_module.supervisor_context()`

**Dynamic suffix** is rebuilt every turn by `build_dynamic_suffix(state)`. Includes:
- Emotional state, energy estimate, pending items, session bridge
- `## Today` block from `DayContext` (built by `container.context_engine.build_day_context()`)
- Unread autonomous updates (fetched from `autonomous_updates` table, marked delivered)

**Compaction** is triggered when `ContextBudgetMonitor.get_tier()` returns any tier other than `NORMAL`. Calls `run_compaction(messages, tier, llm, existing_summary)`. The result replaces `state["messages"]` with a compacted list and updates `compaction_summary`. Token estimate is stored in `compaction_tokens` for downstream observers.

### Node: `think`

```python
async def think(
    state: SupervisorState,
    container: Any,
    tools: list[dict] | None = None,
    *,
    extra_system_suffix: str | None = None,
) -> dict[str, Any]:
    """Single LLM call: frozen_prefix + dynamic_suffix + tools."""
```

**System prompt assembly** (`supervisor.py:300`):
```
system_prompt = frozen_prefix
if suffix: system_prompt = f"{frozen_prefix}\n\n{suffix}"
if extra_system_suffix: system_prompt += f"\n\n{extra_system_suffix}"
```

**Tool pair integrity** is enforced here, not in the reducer (`supervisor.py:314`):
```python
from kora_v2.graph.reducers import ensure_tool_pair_integrity
messages = ensure_tool_pair_integrity(state.get("messages", []))
```
This strips dangling `tool_use`/`tool_result` orphans before the LLM sees them — critical because MiniMax rejects imbalanced pairs.

**LLM call** (`supervisor.py:397`):
```python
result = await retry_with_backoff(
    llm.generate_with_tools,
    messages=formatted_messages,
    tools=active_tools,
    system_prompt=system_prompt,
    temperature=0.7,
    thinking_enabled=False,
)
```
Uses `retry_with_backoff()` which retries only `LLMConnectionError` and `LLMTimeoutError` (not `LLMGenerationError`).

**Iteration cap fallback** (inside `_think` closure, `supervisor.py:1004`):
```python
if iteration_count["value"] > _MAX_TOOL_ITERATIONS:
    clarify_update = await think(state, container, tools=[], extra_system_suffix=_ITERATION_CAP_CLARIFY_SUFFIX)
    clarify_update["_pending_tool_calls"] = []
```
Forces a text-only response asking one focused clarifying question instead of emitting a "giving up" bail string. If the LLM produces nothing anyway, a hardcoded fallback text is used (`supervisor.py:1032`).

### Node: `tool_loop`

```python
async def tool_loop(state: SupervisorState, container: Any, on_tool_event=None) -> dict[str, Any]:
    """Execute all pending tool calls; emit real-time events; update topic tracker."""
```

For each call in `_pending_tool_calls`:
1. Calls `execute_tool(tool_name, tool_args, container, auth_relay=auth_relay)`
2. On error, catches exception and returns `{"error": str(e)}` as the result (no re-raise)
3. Emits `on_tool_event({"event": "tool_executed", ...})` if the callback is wired
4. Appends `{"role": "tool", "tool_call_id": ..., "content": result_str}` to `tool_results`
5. Appends a `tool_call_records` entry (name, args, first 200 chars of result, success flag)

After all calls, runs `_update_topic_tracker()` and clears `_pending_tool_calls`.

### Topic Tracker: `_update_topic_tracker(state, tool_records)`

Implements the tool-footprint heuristic for hyperfocus detection (`supervisor.py:583`).

**Continuity rules** (§4.4 of life engine spec):
1. Empty tool set → pure conversation → same topic
2. Current tool names overlap any of last 3 turn's tool sets → same topic
3. Current entity IDs overlap any of last 3 turn's entity sets → same topic
4. Pronoun continuity: last user message contains "it", "that", "this", "them", "those", "these" → same topic
5. Otherwise → topic changed, reset counter

**Hyperfocus gate** (`supervisor.py:665`):
```python
hyperfocus = turns_in_topic >= 3 and session_minutes >= 45
```
Requires both topic continuity (≥ 3 turns) and session length (≥ 45 minutes).

**Entity extraction**: `_extract_entity_ids()` reads from `_ENTITY_ARG_KEYS` (item_id, entry_id, calendar_entry_id, parent_id, etc.) and parses `{"id": ...}` from create-tool result JSON.

**State storage**: `topic_tracker` dict stores `recent_tool_sets` and `recent_entity_ids` as deques capped at 3 entries each.

### Node: `synthesize`

```python
async def synthesize(state: SupervisorState) -> dict[str, Any]:
    """Pass-through if think already produced response; otherwise extract from last assistant message."""
```

Also applies `_strip_unintended_cjk()` to the response.

The **`_synthesize` closure** additionally records quality metrics via `container.quality_collector.record_turn()`.

### CJK Filter: `_strip_unintended_cjk(response, user_messages)`

Removes CJK token leaks from MiniMax M2.7 English responses (`supervisor.py:680`).

- Skips filter entirely if any user message contains CJK characters (user chose CJK language)
- Preserves code fence blocks verbatim (odd-indexed chunks after `split("```")`)
- Removes `[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]+` from prose
- Collapses double-spaces left by stripped tokens
- Logs `WARNING: synthesize_stripped_cjk_leak` when triggered

### Routing: `should_continue(state)`

```python
def should_continue(state: SupervisorState) -> str:
    pending = state.get("_pending_tool_calls", [])
    return "tool_loop" if pending else "synthesize"
```

---

## dispatch.py — `kora_v2/graph/dispatch.py`

Tool schema definitions and execution routing.

### `SUPERVISOR_TOOLS`

Eleven base tools in Anthropic schema format. Phase 7.5 retired `start_autonomous` and added orchestration-aware tools that let the supervisor see and steer work running inside the `OrchestrationEngine`.

**Core + retrieval tools:**

| Tool | Auth | Description |
|------|------|-------------|
| `dispatch_worker` | ALWAYS_ALLOWED (planner/reviewer), ASK_FIRST (executor) | Delegate to typed worker harness |
| `recall` | ALWAYS_ALLOWED | Hybrid memory search (~0.3s, no LLM) |
| `search_web` | ALWAYS_ALLOWED | Brave Search API; returns titles/URLs/snippets |
| `fetch_url` | ALWAYS_ALLOWED | Fetch URL text content; max 8000 chars default |

**Orchestration tools:**

| Tool | Auth | Description |
|------|------|-------------|
| `decompose_and_dispatch` | ALWAYS_ALLOWED | Break a user request into pipeline stages and dispatch through `OrchestrationEngine.start_pipeline_instance()`. Replaces the retired `start_autonomous` tool; for autonomous goals pass `pipeline_name="user_autonomous_task"`. Returns the pipeline instance id. |
| `get_running_tasks` | ALWAYS_ALLOWED | List `WorkerTask` rows with state + progress + topic-overlap score. Default is the four-condition turn-start surfacing rule (session-owned, recent system tasks, unacknowledged terminal states, mid-band topic overlap 0.45–0.70). |
| `get_task_progress` | ALWAYS_ALLOWED | Fetch live progress for a single task id: state, stage, steps completed, last `result_summary`. |
| `get_working_doc` | ALWAYS_ALLOWED | Read the per-instance working document from `<memory_root>/Inbox/<task_id>.md`. Returns the full markdown (YAML frontmatter + sections) so the supervisor can quote pending decisions back to the user. |
| `cancel_task` | ASK_FIRST | Transition a task to `CANCELLED` with a reason. Dispatcher honours it at the next safe checkpoint. |
| `modify_task` | ASK_FIRST | Apply in-flight edits to a task (add/remove stages, retarget goal). The engine validates the mutation against the pipeline's `_assert_acyclic` rule. |
| `record_decision` | ALWAYS_ALLOWED | Append a resolution to an open decision row in the `open_decisions` table. Unblocks any `PAUSED_FOR_DECISION` task waiting on that decision id and emits `OPEN_DECISION_POSED`'s sibling event. |

The legacy `start_autonomous` path was retired by Slice 7.5c. The comment block at the top of `dispatch.py` makes this explicit:

> Phase 7.5c retired that tool — autonomous goals now flow through `decompose_and_dispatch(pipeline_name="user_autonomous_task")` and the orchestration engine.

### `get_available_tools(container, active_skills)`

```python
def get_available_tools(container=None, active_skills=None) -> list[dict]:
    """Return SUPERVISOR_TOOLS + skill-gated ToolRegistry tools + capability pack actions."""
```

Assembly order:
1. `SUPERVISOR_TOOLS` (minus `dispatch_worker` and the orchestration tools if workers/engine not initialized — `_WORKER_DEPENDENT_TOOLS` gates both)
2. `ToolRegistry.get_all()` filtered by `skill_loader.get_active_tools(active_skills)`
3. `collect_capability_tools(container)` from `capability_bridge.py`

Duplicate names are skipped via `seen_names` set.

### `execute_tool(tool_name, tool_args, container, auth_relay)`

```python
async def execute_tool(tool_name, tool_args, container=None, auth_relay=None) -> str:
    """Auth check → named dispatch → ToolRegistry fallback → capability_bridge fallback."""
```

Execution order:
1. `_resolve_auth_context()` → `(AuthLevel, risk_level)`
2. `check_tool_auth()` → if denied, return `{"error": "not authorized"}`
3. Named dispatch for `dispatch_worker`, `recall`, `search_web`, `fetch_url`, plus the seven orchestration tools (`decompose_and_dispatch`, `get_running_tasks`, `get_task_progress`, `get_working_doc`, `cancel_task`, `modify_task`, `record_decision`) — each routes to a private `_orch_*` helper that resolves `container._orchestration_engine` and calls the appropriate engine method
4. `_execute_registry_tool()` for `ToolRegistry`-registered tools
5. `execute_capability_action()` for dot-namespaced capability tools (e.g. `workspace.gmail.send`)

### `_resolve_auth_context(tool_name, tool_args)`

Special cases:
- `dispatch_worker` with `worker_name="executor"` → `ASK_FIRST`, risk `high`
- `dispatch_worker` with `worker_name="planner"` or `"reviewer"` → `ALWAYS_ALLOWED`, risk `low`
- `search_web`, `fetch_url` → `ALWAYS_ALLOWED`, risk `low`
- ToolRegistry tools → `definition.auth_level`, risk from `definition.is_read_only`
- Default → `ALWAYS_ALLOWED`

---

## state.py — `kora_v2/graph/state.py`

`SupervisorState` TypedDict defining all supervisor graph state fields.

```python
class SupervisorState(TypedDict, total=False):
    # Conversation
    messages: Annotated[list[dict], add_messages_reducer]
    session_id: str
    turn_count: int

    # Context (Phase 2+)
    emotional_state: dict | None
    energy_estimate: dict | None
    pending_items: Annotated[list[dict], last_value_list_reducer]

    # Per-turn (reset by receive)
    active_workers: Annotated[list[dict], last_value_list_reducer]
    tool_call_records: Annotated[list[dict], last_value_list_reducer]

    # Cached per-session
    frozen_prefix: str

    # Response
    response_content: str

    # Errors
    errors: Annotated[list[str], bounded_errors_reducer]

    # Compaction (Phase 4)
    compaction_tier: str       # "NORMAL", "PRUNE", "SUMMARIZE", "AGGRESSIVE", "HARD_STOP"
    compaction_tokens: int     # estimated token count
    compaction_summary: str    # structured summary from compaction
    session_bridge: dict | None
    greeting_sent: bool

    # Phase 5: ADHD life engine
    day_context: dict | None
    turns_in_current_topic: int
    hyperfocus_mode: bool
    topic_tracker: dict | None

    # Internal (graph-private)
    _dynamic_suffix: str
    _pending_tool_calls: Annotated[list[dict], last_value_list_reducer]
    _unread_autonomous_updates: list[dict]
    _overlap_score: float
    _overlap_action: str
```

`total=False` makes all fields optional — nodes only write keys they update.

### Reducer Assignment

| Field | Reducer | Behavior |
|-------|---------|----------|
| `messages` | `add_messages_reducer` | Delegates to LangGraph `add_messages` |
| `pending_items`, `active_workers`, `tool_call_records`, `_pending_tool_calls` | `last_value_list_reducer` | New value replaces old (not appended) |
| `errors` | `bounded_errors_reducer` | Bounded append; oldest evicted when > max |

---

## reducers.py — `kora_v2/graph/reducers.py`

Custom LangGraph reducer functions.

### `add_messages_reducer(existing, new)`

Delegates to LangGraph's built-in `add_messages`. This handles the standard append-with-dedup behavior for conversation history.

### `ensure_tool_pair_integrity(messages)`

```python
def ensure_tool_pair_integrity(messages: list[Any]) -> list[Any]:
    """Full 2-pass orphan detection and removal."""
```

Two-pass algorithm:
1. **Pass 1**: Strip trailing dangling assistant messages with `tool_use` blocks that have no following result messages
2. **Pass 2**: Scan full list — for each assistant with `tool_use`, check that all its IDs are covered by consecutive following `tool_result` messages. Drop the pair if any ID is uncovered. Drop standalone `tool_result` messages with no matching preceding `tool_use`.

This is called in `think()` at LLM-send time (not as a reducer) because the reducer runs on every state write — applying it there would mutate history mid-turn and cause false positives.

### `last_value_list_reducer(existing, new)`

If `new` is not `None` (even `[]`), returns `list(new)`. If `new` is `None` (node skipped writing the key), keeps `existing`. This enables "replace" semantics for list fields that tools write per-turn.

### `bounded_errors_reducer(existing, new, max_errors=20)`

Appends `new` to `existing`, then trims to `max_errors` by removing the oldest entries (FIFO eviction). Used for `errors` field.

### `workspace_reducer(existing, new)`

Implements Baddeley's working memory model: maximum 7 items, FIFO eviction when full, salience-weighted decay. Used for the `workspace` field (if wired).

### Additional Reducers

| Reducer | Behavior |
|---------|----------|
| `or_bool_reducer` | `existing or new` — True if either branch set True |
| `last_value_bool_reducer` | Last writer wins |
| `last_value_string_reducer` | Last non-None value wins |
| `bounded_append_list_reducer(max=50)` | Append with FIFO cap at 50 entries |

---

## prompts.py — `kora_v2/graph/prompts.py`

System prompt construction. Two exported functions:

```python
def build_frozen_prefix(
    user_model_snapshot=None,
    skill_index=None,
    skill_loader=None,
    active_skills=None,
    adhd_output_guidance=None,
    user_triggers=None,
) -> str:
    """Build the cached per-session system prompt prefix."""

def build_dynamic_suffix(state: SupervisorState) -> str:
    """Build the per-turn dynamic suffix from state."""
```

**Frozen prefix** contains: Kora identity and role, skill index, ADHD output guidance, overwhelm triggers, user model snapshot.

**Dynamic suffix** contains: emotional state, energy estimate, pending items, session bridge context, `## Today` block (rendered from `day_context`), unread autonomous updates.

---

## capability_bridge.py — `kora_v2/graph/capability_bridge.py`

Bridges capability packs (workspace, browser, vault, etc.) into the supervisor tool list.

```python
def collect_capability_tools(container: Any) -> list[dict[str, Any]]:
    """Enumerate all capability packs' actions and return as Anthropic-format tool dicts."""

async def execute_capability_action(
    tool_name: str,
    tool_args: dict[str, Any],
    container: Any,
) -> str:
    """Route dot-namespaced tool names (e.g. workspace.gmail.send) to the correct pack."""
```

Capability tools are always included in `get_available_tools()` output (not skill-gated). The model's skill guidance controls when to invoke them. Dot-notation (`namespace.action`) distinguishes capability tools from registry tools.

---

## Integration Points

### Inbound (graph consumes)

| Source | What | How |
|--------|------|-----|
| `kora_v2/core/di.py` | `Container` | Passed to `build_supervisor_graph()` as closure |
| `kora_v2/daemon/server.py` | `graph.ainvoke(graph_input, config)` | Called from `_handle_chat()` |
| `kora_v2/runtime/turn_runner.py` | Same `ainvoke` wrapped with tracing | `GraphTurnRunner.run_turn()` |
| `kora_v2/context/budget.py` | `ContextBudgetMonitor` | Imported inside `build_suffix()` |
| `kora_v2/context/compaction.py` | `run_compaction()` | Called when tier != NORMAL |
| `kora_v2/tools/registry.py` | `ToolRegistry.get_all()` | Tool resolution in `get_available_tools()` |

### Outbound (graph produces)

| Consumer | What | Transport |
|----------|------|-----------|
| `kora_v2/daemon/server.py` | `state["response_content"]` | Returned from `graph.ainvoke()` |
| `kora_v2/runtime/stores.py` | Autonomous updates marked delivered | Direct DB write in `_fetch_unread_autonomous_updates()` |
| `kora_v2/quality/` | Turn quality metrics | `container.quality_collector.record_turn()` in `_synthesize` |
| `kora_v2/emotion/` | Emotional state updates | Written to `state["emotional_state"]` in `_receive` |

---

## Data Flow: Tool-Using Turn

```
think() detects tool calls → sets _pending_tool_calls
  │
  ▼ (should_continue → "tool_loop")
tool_loop()
  ├─ _resolve_auth_context() → (AuthLevel, risk)
  ├─ check_tool_auth() → True or False
  ├─ execute_tool() → result_str
  ├─ on_tool_event() → real-time WebSocket push
  ├─ appends {"role": "tool", "tool_call_id": ..., "content": result_str}
  └─ _update_topic_tracker() → turns_in_current_topic, hyperfocus_mode
  │
  ▼ (edge back to "think")
think()
  ├─ ensure_tool_pair_integrity(messages)  ← strips orphans before LLM sees them
  ├─ llm.generate_with_tools(...)
  └─ if no more tool calls → response_content set, _pending_tool_calls empty
  │
  ▼ (should_continue → "synthesize")
synthesize()
  └─ _strip_unintended_cjk()
```

---

## Key Invariants

- **Tool pair integrity** is enforced in `think()` before each LLM call, not in the reducer. This means the conversation state may briefly hold orphaned pairs (between tool_loop and the next think call) but the LLM never sees them.
- **`_pending_tool_calls`** uses `last_value_list_reducer` — each `tool_loop` return clears it to `[]`. This prevents stale tool calls from re-executing on the next turn.
- **`frozen_prefix`** is written once (turn 1) and cached in the LangGraph checkpoint. Subsequent turns skip `build_frozen_prefix()`.
- **`iteration_count`** is a plain Python dict shared by closure — not in LangGraph state. This makes it invisible to the checkpointer; it resets to 0 on every `_receive` call.
- **Hyperfocus detection** requires both `turns_in_current_topic >= 3` AND `session_duration_min >= 45`. Session duration comes from `day_context.session_duration_min`, which is populated by `ContextEngine.build_day_context()` in `build_suffix()`.
