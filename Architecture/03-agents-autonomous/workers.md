# Worker Harnesses

The three worker harnesses — Planner, Executor, and Reviewer — are the structured-output LLM agents that the supervisor graph dispatches to when a turn requires planning, task execution, or quality review. Each worker wraps the LLM in a tool-forcing contract: the LLM must call a specific structured-output tool rather than returning free text. This guarantees a validated Pydantic output model on every successful call.

All three workers inherit from `AgentHarness` (`kora_v2/agents/harness.py`), which provides the `execute(input_data)` public entry point, middleware hooks, and quality gate wiring. The workers hold a reference to the DI container for LLM access.

### Dispatch contexts (Phase 7.5)

Workers are stateless per call and are dispatched from two different contexts, both of which are now budget-accounted through the Phase 7.5 orchestration layer:

| Context | Caller | `WorkerTask` preset | Budget class |
|---|---|---|---|
| Conversation turn | Supervisor graph via `container.resolve_worker(name)` | `IN_TURN` (300s hard cap) | `CONVERSATION` (always-reserved 300 slots) |
| Autonomous plan | `_autonomous_step_fn()` in `autonomous/pipeline_factory.py`, called by the `OrchestrationEngine` dispatcher for one `LONG_BACKGROUND` `WorkerTask` per plan | `LONG_BACKGROUND` (unbounded wall, pauses on conversation, pauses on topic overlap) | `BACKGROUND` (counts against the 4500-slot 5h window) |

The worker code itself is identical in both paths. What changes is the envelope: the supervisor-tool dispatch wraps the call in an `IN_TURN` preset for the current turn, while the autonomous path wraps every execute/review tick inside a long-running `WorkerTask` that the dispatcher can pause, checkpoint, and resume. See [`../01-runtime-core/orchestration.md`](../01-runtime-core/orchestration.md) for the preset definitions and [`autonomous.md`](autonomous.md) for the autonomous step function that ticks workers inside the pipeline wrapper.

---

## Files in this module

| File | Purpose |
|---|---|
| [`kora_v2/agents/workers/__init__.py`](../../kora_v2/agents/workers/__init__.py) | Empty package marker |
| [`kora_v2/agents/workers/planner.py`](../../kora_v2/agents/workers/planner.py) | Planner harness |
| [`kora_v2/agents/workers/executor.py`](../../kora_v2/agents/workers/executor.py) | Executor harness |
| [`kora_v2/agents/workers/reviewer.py`](../../kora_v2/agents/workers/reviewer.py) | Reviewer harness |

---

## Worker I/O contracts

```
PlannerWorkerHarness
  Input:  PlanInput  (goal, context, constraints, optional existing_plan)
  Output: PlanOutput (plan, steps[], estimated_effort, confidence, adhd_notes)
  Tool:   submit_plan

ExecutorWorkerHarness
  Input:  ExecutionInput  (task, tools_available, context, energy_level, estimated_minutes)
  Output: ExecutionOutput (result, tool_calls_made, artifacts, success, confidence, error)
  Tool:   structured_execution_output  (or a real filesystem tool)

ReviewerWorkerHarness
  Input:  ReviewInput  (work_product, criteria[], original_goal, context)
  Output: ReviewOutput (passed, findings[], confidence, recommendation, revision_guidance)
  Tool:   submit_review
```

---

## PlannerWorkerHarness

**File:** [`kora_v2/agents/workers/planner.py`](../../kora_v2/agents/workers/planner.py)

### Purpose

Decomposes a high-level goal into an ADHD-friendly execution plan: time-bounded steps (5–30 minutes each), dependency-aware, energy-level-tagged, with a mandatory sub-10-minute first "initiation micro-step" to overcome startup friction.

### System prompt

The system prompt is a module-level constant (`PLANNER_SYSTEM_PROMPT`) that encodes the ADHD planning rules inline. Key rules baked in:

- Each step must be 5–30 minutes.
- The **first step must be ≤10 minutes** (ideally ≤5) — the "initiation micro-step".
- Maximum 7 steps per plan.
- Each step must declare: `worker`, `estimated_minutes`, `depends_on`, `energy_level`, `needs_review`, `review_criteria`, `adhd_notes`.
- `estimated_effort` is one of `quick / moderate / complex`.
- `confidence` is 0.0–1.0.

If an `existing_plan` is passed in `PlanInput`, a `REVISION_ADDENDUM` template is appended to the system prompt with the existing plan JSON, telling the LLM to improve rather than replace.

### LLM call

```python
result = await self._container.llm.generate_with_tools(
    messages=messages,
    tools=[submit_plan_tool],
    system_prompt=system_prompt,
    thinking_enabled=False,
)
```

`thinking_enabled=False` — structured output does not benefit from extended thinking. The tool definition is built from `PlanOutput.model_json_schema()` via `get_schema_tool()`.

### Retry logic

`MAX_RETRIES = 2`. Two failure conditions cause a retry:

1. `confidence < 0.3` on the returned plan. A repair hint ("your confidence was too low") is appended to the conversation and the LLM is called again.
2. Any `ValueError / KeyError / TypeError` during parsing.

After retries are exhausted, the best result by confidence is returned if any attempt partially succeeded. If no result exists at all, `PlanningFailedError` is raised.

### Output parsing: `_parse_plan_output()`

The LLM sometimes returns a nested `plan` dict and sometimes returns flat fields. `_parse_plan_output()` handles both shapes:

1. If no `plan` key is present, it assembles one from top-level fields.
2. Ensures `plan.id` and each `step.id` are set (auto-generates UUID fragments if missing).
3. Calculates `estimated_total_minutes` from step sum if the LLM omitted it.
4. Validates using `Plan.model_validate()` and `PlanStep.model_validate()`.

### Key models

- `PlanInput`: `goal: str`, `context: str`, `constraints: PlanConstraints`, `existing_plan: Plan | None`
- `PlanOutput`: `plan: Plan`, `steps: list[PlanStep]`, `estimated_effort: str`, `confidence: float`, `adhd_notes: str`
- `PlanConstraints`: `max_steps: int`, `max_minutes: int`, `autonomy_level: str`, `available_tools: list[str]`

---

## ExecutorWorkerHarness

**File:** [`kora_v2/agents/workers/executor.py`](../../kora_v2/agents/workers/executor.py)

### Purpose

Executes concrete tasks with real callable filesystem tools. The design principle is: never fabricate success — only report `success=True` if a tool confirmed it on disk.

### Execution paths

The executor has four paths in priority order:

**Path 1 — Deterministic fast path** (`_execute_local_filesystem`): If the task name is exactly `"write_file"` or `"create_directory"` and params are well-formed, the executor calls the filesystem directly using Python's `pathlib` without an LLM call. On `write_file`, it verifies the file exists on disk before returning `success=True`. Path safety is enforced via `_resolve_safe()` from `kora_v2/tools/filesystem.py`.

**Path 2 — LLM with real filesystem tools**: If the task description matches file-operation patterns (`_FILE_WRITE_PATTERNS` — e.g., "save to", "write_file", "create a file"), the LLM is called with the real registered filesystem tools plus `structured_execution_output`. The LLM picks which tool to call. `tool_choice="any"` forces a tool call — no prose responses accepted.

**Path 3 — LLM with research/capability tools**: Research-like tasks can expose `search_web`, `fetch_url`, and browser capability tools before falling back to structured output.

**Path 4 — LLM with structured output only**: If the task is not a file operation or research/capability task, the LLM is offered only `structured_execution_output` and must fill in the result object directly.

### System prompt

`EXECUTOR_SYSTEM_PROMPT` instructs the LLM to:
- Call a filesystem tool if disk operations are needed.
- Always call `structured_execution_output` to report the result.
- Never fabricate success.
- Keep result strings concise and factual.

### LLM call

```python
result = await self._container.llm.generate_with_tools(
    messages=messages,
    tools=tool_defs,
    system_prompt=EXECUTOR_SYSTEM_PROMPT,
    thinking_enabled=False,
    tool_choice="any",
)
```

`tool_choice="any"` is critical — it prevents the LLM from responding with prose.

### Real tool dispatch: `_dispatch_filesystem_tool()`

When the LLM calls a real filesystem tool (e.g., `write_file`), `_dispatch_filesystem_tool()`:

1. Resolves the callable from `ToolRegistry.get_callable(tool_name)`.
2. Instantiates the tool's input model with the LLM-provided args.
3. Calls the function and parses the JSON result.
4. For `write_file`: verifies the file actually exists on disk after the call.
5. Returns a fully populated `ExecutionOutput` with `ToolCallRecord` and `Artifact` objects.

### Helper: `plan_step_to_execution_input()`

Convenience adapter that converts a `PlanStep` model into an `ExecutionInput`. Used by the autonomous execution graph when dispatching steps to workers.

### Key models

- `ExecutionInput`: `task: str`, `tools_available: list[str]`, `context: str`, `energy_level: str | None`, `estimated_minutes: int | None`
- `ExecutionOutput`: `result: str`, `tool_calls_made: list[ToolCallRecord]`, `artifacts: list[Artifact]`, `success: bool`, `confidence: float`, `error: str | None`
- `ToolCallRecord`: `tool_name`, `args`, `result_summary`, `success`, `duration_ms`, `timestamp`
- `Artifact`: `type`, `uri`, `label`, `size_bytes`

---

## ReviewerWorkerHarness

**File:** [`kora_v2/agents/workers/reviewer.py`](../../kora_v2/agents/workers/reviewer.py)

### Purpose

Evaluates a work product against a list of criteria and produces structured findings with severity, category, and suggested fixes. The reviewer also applies an RSD (Rejection Sensitive Dysphoria) filter to its own output to catch harsh phrasing in findings and guidance.

### System prompt

`REVIEWER_SYSTEM_PROMPT` instructs the LLM to:
- Evaluate against each criterion.
- Create findings with `severity` (critical/warning/info), `category` (correctness/completeness/security/quality/adhd_friendliness), `description`, and optional `suggested_fix`.
- Set `passed=True` only if there are no critical findings.
- Set `recommendation` to `accept / revise / reject`.
- Set `revision_guidance` if recommending revision.

### LLM call

```python
result = await self._container.llm.generate_with_tools(
    messages=messages,
    tools=[submit_review_tool],
    system_prompt=REVIEWER_SYSTEM_PROMPT,
    thinking_enabled=False,
)
```

Tool is `submit_review`, built from `ReviewOutput.model_json_schema()`.

### Retry logic

`MAX_RETRIES = 2`. A retry is triggered if `passed=True` and `confidence < 0.5` — this catches "rubber stamp" passes where the LLM accepted without scrutiny. The conversation is extended with a "re-examine more carefully" prompt.

After retries, if all attempts failed (ValueError/KeyError/TypeError on every attempt), a conservative `ReviewOutput(passed=False, recommendation="reject")` is returned with a critical finding explaining the review failure.

### Post-validation logic

After parsing, three fixups are applied:

1. If `passed=True` with zero findings: an `info / completeness` finding is added.
2. If `recommendation="revise"` with no `revision_guidance`: a default guidance string is set.
3. **RSD filter** (`_apply_rsd_filter()`): scans finding descriptions and revision_guidance against ADHD output rules from `container.adhd_module`. Any rule violations append `adhd_friendliness / info` findings without downgrading the recommendation. Non-blocking — if `adhd_module` is absent, it is skipped silently.

### Key models

- `ReviewInput`: `work_product: str`, `criteria: list[str]`, `original_goal: str | None`, `context: str | None`
- `ReviewOutput`: `passed: bool`, `findings: list[ReviewFinding]`, `confidence: float`, `recommendation: str`, `revision_guidance: str | None`
- `ReviewFinding`: `severity`, `category`, `description`, `suggested_fix`

---

## Integration points

- Workers are resolved via `container.resolve_worker(name)` — the DI container owns instantiation.
- The supervisor graph dispatches to workers during conversation turns inside an `IN_TURN` `WorkerTask` envelope.
- The 12-node autonomous state machine in `autonomous/graph.py` still calls `_dispatch_to_executor()`, `_dispatch_to_reviewer()`, and `container.resolve_worker("planner")` inside the `plan()` node, but those nodes are now ticked one at a time by the dispatcher through `_autonomous_step_fn()` in `autonomous/pipeline_factory.py` — the loop lives in the `OrchestrationEngine`, not in `autonomous/` anymore.
- `PlannerWorkerHarness` calls `get_schema_tool()` from `kora_v2/tools/registry.py` to build the structured-output tool definition.
- `ExecutorWorkerHarness` imports `ToolRegistry` from `kora_v2/tools/registry.py` and filesystem tools from `kora_v2/tools/filesystem.py`.
- `ReviewerWorkerHarness` calls `check_output()` from `kora_v2/core/rsd_filter.py` for the ADHD safety pass.
- Model definitions live in `kora_v2/core/models.py`.
- `PlanningFailedError` is defined in `kora_v2/core/exceptions.py`.
