# Quality Subsystem (`kora_v2/quality/`)

The quality subsystem provides two distinct services: automatic per-turn metric
collection (Tier 1) and autonomous-work quality gates (used by the planner/executor
pipeline). Tier 1 runs silently on every turn with no caller opt-in; quality
gates wrap producer–reviewer cycles and retry failed outputs with injected
feedback. All data is held in memory with optional async persistence to SQLite.

## Files in this module

| File | Purpose |
|---|---|
| [`quality/tier1.py`](../../kora_v2/quality/tier1.py) | `QualityCollector` — per-turn metric recording and DB persistence |
| [`quality/confidence.py`](../../kora_v2/quality/confidence.py) | `compute_confidence` — composite 3-component confidence score |
| [`quality/gates.py`](../../kora_v2/quality/gates.py) | `execute_with_quality_gates` — producer–reviewer cycle with retry |
| [`quality/__init__.py`](../../kora_v2/quality/__init__.py) | Single-line module docstring; no public exports |

---

## Core data models (defined in `kora_v2/core/models.py`)

### `QualityGateResult`

```python
class QualityGateResult(BaseModel):
    gate_name: str
    passed: bool
    reason: str | None = None
    suggested_fix: str | None = None
```

One result per named gate check. Stored in `QualityTurnMetrics.gate_results`.

### `QualityTurnMetrics`

```python
class QualityTurnMetrics(BaseModel):
    session_id: str
    turn: int
    latency_ms: int
    tool_calls: int = 0
    worker_dispatches: int = 0
    gate_results: list[QualityGateResult] = []
    compaction_triggered: bool = False
    tokens_used: int = 0
    timestamp: datetime  # auto-set to now(UTC)
```

---

## `tier1.py` — `QualityCollector`

### Purpose

Automatic, low-overhead metric collection. Records latency, token usage, tool
call count, worker dispatch count, compaction events, and named gate results
for every turn. Persists optionally to `operational.db`.

### Constructor

```python
QualityCollector(db_path: Path | None = None)
```

Internal storage: `dict[session_id, list[QualityTurnMetrics]]`.

### `record_turn(...) -> QualityTurnMetrics`

Creates a `QualityTurnMetrics` object, appends it to the in-memory store, and
returns it. All arguments except `session_id`, `turn`, and `latency_ms` have
sensible defaults of 0 or empty.

```python
def record_turn(
    self,
    session_id: str,
    turn: int,
    latency_ms: int,
    tool_calls: int = 0,
    worker_dispatches: int = 0,
    tokens_used: int = 0,
    gate_results: list[QualityGateResult] | None = None,
    compaction_triggered: bool = False,
) -> QualityTurnMetrics
```

### `persist_turn(metrics) -> None` (async)

Writes to `operational.db` (aiosqlite). Schema: `quality_metrics` table with
columns `(session_id, turn_number, metric_name, metric_value, recorded_at)`.

Inserts one row per scalar metric:

- `"latency_ms"` → `metrics.latency_ms`
- `"tool_calls"` → `metrics.tool_calls`
- `"worker_dispatches"` → `metrics.worker_dispatches`
- `"tokens_used"` → `metrics.tokens_used`

Plus one row per gate result:

- `"gate_<gate_name>"` → `1.0` if passed, `0.0` if failed.

Silently skips (logs warning) on any DB error or when `db_path is None`.

### Query methods

| Method | Returns |
|---|---|
| `get_session_metrics(session_id)` | `list[QualityTurnMetrics]` for session |
| `average_latency(session_id)` | Mean `latency_ms` across all turns |
| `total_tool_calls(session_id)` | Sum of `tool_calls` across all turns |
| `total_tokens(session_id)` | Sum of `tokens_used` across all turns |
| `gate_pass_rate(session_id)` | Fraction of all gate results that passed; returns `1.0` when no gates recorded |
| `clear_session(session_id)` | Removes all metrics for a session from memory |

---

## `confidence.py` — Composite Confidence Scoring

### `ConfidenceComponents`

```python
class ConfidenceComponents(BaseModel):
    llm_confidence: float    # [0, 1]
    tool_success_rate: float # [0, 1]
    completeness: float      # [0, 1]
```

### `ConfidenceResult`

```python
class ConfidenceResult(BaseModel):
    score: float            # Final composite score [0, 1]
    components: ConfidenceComponents
    label: Literal["low", "medium", "high"]
    threshold_passed: bool
```

Label thresholds:

| Score | Label |
|---|---|
| ≥ 0.7 | `"high"` |
| ≥ 0.4 | `"medium"` |
| < 0.4 | `"low"` |

### `compute_confidence(...) -> ConfidenceResult`

```python
def compute_confidence(
    llm_confidence: float,        # From ReviewOutput.confidence
    tool_call_records: list[dict],# Each must have a "success" bool key
    criteria_met: int,
    criteria_total: int,
    threshold: float = 0.6,
) -> ConfidenceResult
```

**Formula (weighted sum of three components):**

```
score = 0.4 * llm_confidence
      + 0.3 * tool_success_rate
      + 0.3 * completeness
```

Where:

- `tool_success_rate = successes / len(tool_call_records)` — defaults to `1.0`
  when the list is empty (absence of failures is not penalized).
- `completeness = max(0, min(1, criteria_met / criteria_total))` — defaults to
  `1.0` when `criteria_total == 0`.
- All inputs are clamped to `[0, 1]` before use.

`threshold_passed = score >= threshold` (default threshold: 0.6).

### `confidence_from_review(...) -> ConfidenceResult`

Convenience wrapper that extracts `llm_confidence` from any object with a
`.confidence` attribute (typed as `Any` to avoid circular imports with
`kora_v2.core.models.ReviewOutput`):

```python
def confidence_from_review(
    review_output: Any,            # kora_v2.core.models.ReviewOutput
    tool_call_records: list[dict],
    criteria_met: int = 0,
    criteria_total: int = 0,
    threshold: float = 0.6,
) -> ConfidenceResult
```

---

## `gates.py` — Quality Gate Execution

### Supporting models

```python
class GateAttempt(BaseModel):
    attempt: int
    confidence: float
    passed: bool
    findings: list[dict]       # From ReviewOutput.findings, serialized to dicts
    suggested_fix: str | None  # From ReviewOutput.revision_guidance or findings

class GateResult(BaseModel):
    passed: bool
    attempts: list[GateAttempt]
    final_confidence: float
    output: Any                 # Last attempt's producer output
    partial_result: bool        # True when returning after max retries without passing
```

### `execute_with_quality_gates(...) -> GateResult`

The main entry point for autonomous work quality loops:

```python
async def execute_with_quality_gates(
    producer: Callable[[], Awaitable[Any]],
    reviewer: Callable[[Any], Awaitable[Any]],
    threshold: float = 0.6,
    max_attempts: int = 2,
    feedback_injector: Callable[[Any, list[str]], Any] | None = None,
) -> GateResult
```

**Loop per attempt:**

```
1. Call producer()
   - If producer accepts "feedback" kwarg AND feedback_injector AND
     current_feedback is set → call producer(feedback=current_feedback)
   - On exception: record failed GateAttempt(confidence=0.0), continue

2. Call reviewer(output)
   - On exception: record failed GateAttempt(confidence=0.0), continue

3. Compute confidence:
   confidence_from_review(review, tool_call_records=[], threshold=threshold)

4. gate_passed = review.passed AND confidence.threshold_passed

5. If gate_passed → return GateResult(passed=True, ...)

6. If not last attempt AND feedback_injector:
   current_feedback = feedback_injector(output, [finding descriptions])
```

**After max_attempts exhausted without passing:**

Returns `GateResult(passed=False, partial_result=True, output=<last output>)`.

### Helper functions

`_extract_findings(review) -> list[dict]`: converts `ReviewOutput.findings`
to plain dicts via `.model_dump()` or identity for dicts.

`_extract_fix_suggestion(review) -> str | None`: checks
`review.revision_guidance` first, then `finding.suggested_fix` on each finding.

---

## QualitySettings (from `kora_v2/core/settings.py`)

```python
class QualitySettings(BaseModel):
    confidence_threshold: float = 0.6    # Gate pass threshold
    regression_window_days: int = 7      # Window for regression detection
    regression_threshold: float = 0.15   # Min drop before flagging regression
    llm_judge_sampling: float = 0.1      # Fraction of turns sampled for LLM judge
```

`llm_judge_sampling` and `regression_window_days`/`regression_threshold` are
configuration stubs — the LLM judge sampling and regression detection logic
are referenced by settings but not yet wired up in the current codebase
(no implementation found in `quality/`).

---

## Integration points

- **Turn runner** (`kora_v2/runtime/turn_runner.py`): calls
  `QualityCollector.record_turn()` and `persist_turn()` after each turn.
- **Autonomous executor** (`kora_v2/autonomous/`): calls
  `execute_with_quality_gates()` to wrap plan-step execution.
- **Reviewer worker** (`kora_v2/agents/workers/reviewer.py`): produces the
  `ReviewOutput` objects consumed by `confidence_from_review`.
- **DI container** (`kora_v2/core/di.py`): instantiates `QualityCollector`
  with the `operational.db` path from `KoraSettings`.
- **`AgentSettings`** (`kora_v2/core/settings.py`): `reviewer_sampling_rate = 0.1`
  controls how often the reviewer worker is invoked on non-autonomous turns —
  distinct from `QualitySettings.llm_judge_sampling`.
