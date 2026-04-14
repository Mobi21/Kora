# ADHD Module (`kora_v2/adhd/`)

The `adhd/` package is the core of what makes Kora different from a general-purpose assistant. It implements a typed protocol for neurodivergent support, a persistent YAML user profile, and a concrete ADHD module that contributes energy signals, output filters, focus detection, planning constraints, and check-in triggers to every conversation turn. The module is consumed by the `ContextEngine` and injected into the supervisor's frozen prefix at session start, making ADHD awareness ambient — not something the user has to invoke.

## Files in this module

| File | Lines | Role |
|---|---|---|
| [`kora_v2/adhd/__init__.py`](../../kora_v2/adhd/__init__.py) | 44 | Package re-exports |
| [`kora_v2/adhd/protocol.py`](../../kora_v2/adhd/protocol.py) | 93 | `NeurodivergentModule` protocol + 6 shared data models |
| [`kora_v2/adhd/profile.py`](../../kora_v2/adhd/profile.py) | 101 | `ADHDProfile` Pydantic model + YAML loader/writer |
| [`kora_v2/adhd/module.py`](../../kora_v2/adhd/module.py) | 360 | `ADHDModule` — concrete implementation |
| [`kora_v2/core/rsd_filter.py`](../../kora_v2/core/rsd_filter.py) | 68 | `check_output()` — standalone RSD output filter |
| [`kora_v2/cli/first_run.py`](../../kora_v2/cli/first_run.py) | 547 | 5-section onboarding wizard |

---

## Part 1: Protocol (`kora_v2/adhd/protocol.py`)

This file defines the extensibility contract: any future neurodivergent module (anxiety, depression, etc.) must implement `NeurodivergentModule`. The `ContextEngine` accepts a list of modules and calls them uniformly.

### Data models

#### `EnergySignal`

The atomic unit of ADHD-aware energy estimation.

```python
class EnergySignal(BaseModel):
    source: str                              # e.g. "medication", "calendar_load", "time_of_day"
    level_adjustment: float                  # [-1.0, 1.0] — nudge to latent energy score
    confidence: float                        # [0.0, 1.0] — per-signal certainty
    description: str                         # human-readable explanation
    is_guess: bool = True
```

`level_adjustment` and `confidence` are combined via noisy-OR in `ContextEngine._estimate_energy`. A signal with `confidence=0.7` and `level_adjustment=+0.2` nudges `latent` by `+0.14` and raises the aggregate confidence via `1 - (1-existing)*(1-0.7)`.

#### `OutputRule`

A regex-based output filter entry. Programmatically checkable — semantic guidance that cannot be expressed as a regex goes to `output_guidance()` instead.

```python
class OutputRule(BaseModel):
    name: str
    pattern: str              # Python regex (compiled with re.IGNORECASE)
    description: str
    replacement_guidance: str # human-readable rewrite hint
```

#### `FocusState`

```python
class FocusState(BaseModel):
    level: Literal["scattered", "normal", "focused", "locked_in"]
    turns_at_level: int
    session_minutes: int
    hyperfocus_mode: bool = False
```

`hyperfocus_mode=True` maps to `level="locked_in"`. It is surfaced in `DayContext.hyperfocus_mode` and is available for the supervisor to handle differently (e.g., avoid interrupting with notifications).

#### `PlanningConfig`

```python
class PlanningConfig(BaseModel):
    time_correction_factor: float = 1.5     # multiply all time estimates by this
    max_steps_per_plan: int = 7
    first_step_max_minutes: int = 10
    require_micro_step_first: bool = True
    energy_matching: bool = True
```

`time_correction_factor=1.5` reflects the research finding that people with ADHD systematically underestimate task duration. The planner worker reads this via `adhd_module.planning_adjustments()`.

#### `CheckInTrigger`

```python
class CheckInTrigger(BaseModel):
    name: str
    interval_minutes: int
    condition: str | None = None           # e.g. "only_if_no_recent_self_report"
    message_template: str                  # {session_minutes} interpolated
```

#### `NeurodivergentModule` (Protocol)

```python
class NeurodivergentModule(Protocol):
    name: str

    def energy_signals(self, day_context: Any) -> list[EnergySignal]: ...
    def output_rules(self) -> list[OutputRule]: ...
    def output_guidance(self) -> list[str]: ...
    def supervisor_context(self) -> dict[str, Any]: ...
    def focus_detection(self, turns_in_topic: int, session_minutes: int) -> FocusState: ...
    def planning_adjustments(self) -> PlanningConfig: ...
    def check_in_triggers(self) -> list[CheckInTrigger]: ...
    def profile_schema(self) -> dict[str, Any]: ...
```

---

## Part 2: Profile (`kora_v2/adhd/profile.py`)

### `ADHDProfile`

The single source of truth for all runtime-deterministic ADHD settings.

```python
class ADHDProfile(BaseModel):
    version: int = 1
    time_correction_factor: float = 1.5
    check_in_interval_minutes: int = 120
    transition_buffer_minutes: int = 15
    peak_windows: list[tuple[int, int]] = []     # [(start_hour, end_hour), ...]
    crash_periods: list[tuple[int, int]] = []    # [(start_hour, end_hour), ...]
    medication_schedule: list[MedicationScheduleEntry] = []
    coping_strategies: list[str] = []
    overwhelm_triggers: list[str] = []
```

All fields default to safe no-ops: empty lists degrade gracefully (no signals, no medication tracking). A new user gets a working assistant without filling out a profile.

**Medication schedule models**:

```python
class MedicationWindow(BaseModel):
    start: time        # local time (Settings.user_tz)
    end: time          # local time
    label: str | None  # e.g. "morning", "afternoon"

class MedicationScheduleEntry(BaseModel):
    name: str
    dose: str | None
    windows: list[MedicationWindow]
```

`MedicationWindow.start` and `end` are `datetime.time` values in the **user's local timezone** (not UTC). The `ContextEngine._build_medication_status` converts them to UTC using `datetime.combine(local_today, window.start, tzinfo=user_tz).astimezone(UTC)` for comparison against log rows.

### `ADHDProfileLoader`

```python
class ADHDProfileLoader:
    def __init__(self, base: Path): ...  # base = _KoraMemory root

    @property
    def path(self) -> Path:
        # _KoraMemory/User Model/adhd_profile/profile.yaml

    def load(self) -> ADHDProfile: ...   # Returns ADHDProfile() defaults on any failure
    def save(self, profile: ADHDProfile) -> None: ...  # Creates parent dirs
```

`load()` is fault-tolerant: if the file does not exist, is unparseable YAML, or is not a dict, it silently returns `ADHDProfile()` defaults. This allows the container to always construct a valid `ADHDModule` regardless of the filesystem state.

**Filesystem location**: `_KoraMemory/User Model/adhd_profile/profile.yaml`

Example `profile.yaml`:
```yaml
version: 1
time_correction_factor: 1.5
check_in_interval_minutes: 120
transition_buffer_minutes: 15
peak_windows:
  - [9, 12]
crash_periods:
  - [13, 15]
medication_schedule:
  - name: Adderall XR
    dose: 20mg
    windows:
      - start: "08:00"
        end: "09:00"
        label: morning
coping_strategies:
  - timers
  - body doubling
overwhelm_triggers:
  - too many open tasks
  - surprise meetings
```

The wizard also writes `_KoraMemory/User Model/adhd_profile/wizard_summary.md` with a YAML frontmatter block (name, pronouns, life_tracking_domains) and a prose `use_case` section. These two files have different filename patterns and different consumers — they do not conflict.

---

## Part 3: Module (`kora_v2/adhd/module.py`)

`ADHDModule` is the concrete implementation of `NeurodivergentModule`. It is instantiated once per container lifetime, wired with the loaded `ADHDProfile`.

### Module-level constants

These are exported in `__all__` and used directly in tests to avoid magic numbers:

| Constant | Value | Meaning |
|---|---|---|
| `MEDS_TAKEN_ADJUSTMENT` | 0.2 | Energy boost when medication logged |
| `MEDS_TAKEN_CONFIDENCE` | 0.7 | Confidence in that boost |
| `MEDS_MISSED_ADJUSTMENT` | -0.3 | Energy penalty when dose missed |
| `MEDS_MISSED_CONFIDENCE` | 0.8 | Higher confidence — objective fact |
| `BUSY_MORNING_ADJUSTMENT` | -0.25 | 3+ morning events = negative load |
| `BUSY_MORNING_CONFIDENCE` | 0.6 | |
| `BUSY_MORNING_THRESHOLD` | 3 | Number of morning events to trigger |
| `OPEN_MORNING_ADJUSTMENT` | 0.15 | No morning events = small boost |
| `OPEN_MORNING_CONFIDENCE` | 0.5 | |
| `PEAK_WINDOW_ADJUSTMENT` | 0.15 | In user's focus window |
| `CRASH_WINDOW_ADJUSTMENT` | -0.2 | In user's crash window |
| `TIME_OF_DAY_CONFIDENCE` | 0.4 | All time-of-day signals share this confidence |
| `HYPERFOCUS_MIN_TURNS` | 3 | Turns in same topic to detect hyperfocus |
| `HYPERFOCUS_MIN_MINUTES` | 45 | Session length to detect hyperfocus |
| `FOCUSED_MIN_TURNS` | 2 | Turns for "focused" state |
| `FOCUSED_MIN_MINUTES` | 20 | Minutes for "focused" state |
| `SCATTERED_MAX_MINUTES` | 5 | Sessions < 5min are "scattered" |

### `energy_signals()` — timezone-aware signal production

```python
def energy_signals(
    self,
    day_context: Any,
    now: datetime | None = None,
    user_tz: tzinfo | None = None,
) -> list[EnergySignal]:
```

`now` should be the user's local datetime (the `ContextEngine` converts UTC to `user_tz` before calling). `user_tz` is required for the morning-events comparison. Defaults:
- `now=None` → `datetime.now(UTC)` (safe fallback for tests)
- `user_tz=None` → falls back to `now.tzinfo` if tz-aware

**Signal production logic**:

1. **Medication signals**: reads `day_context.medication_status.taken` and `.missed`. For each taken dose: `+0.2 / 0.7`. For each missed dose: `-0.3 / 0.8`.

2. **Calendar load signal (timezone-aware)**: reads `day_context.schedule` (list of `CalendarEntry`). Counts entries with `kind="event"` where `_starts_before(entry, time(12, 0), user_tz)` is True.
   - If `len(morning_events) >= BUSY_MORNING_THRESHOLD`: `-0.25 / 0.6`
   - If `len(morning_events) == 0` and `now.time() < time(12, 0)`: `+0.15 / 0.5`

   `_starts_before()` is the timezone-safe helper (see below).

3. **Time-of-day signals**: reads `now.hour` against `profile.peak_windows` and `profile.crash_periods`. First matching window wins (both loops break after first match). `+0.15 / 0.4` for peak, `-0.2 / 0.4` for crash.

### `_starts_before()` — the timezone fix (commit `dac6612`)

```python
def _starts_before(entry: Any, cutoff: time, user_tz: tzinfo | None = None) -> bool:
```

Before the fix: the comparison happened directly on `starts_at.time()`, which for UTC-aware timestamps returned UTC hours. A 9:00 AM PST meeting stored as `2026-04-12T17:00:00+00:00` would return `time(17, 0)` → classified as afternoon, not morning.

After the fix: if `user_tz is not None and starts_at.tzinfo is not None`, the entry is first converted via `starts_at.astimezone(user_tz)`. Now the same entry returns `time(9, 0)` → correctly classified as morning.

### `focus_detection()` — hyperfocus detection

```python
def focus_detection(self, turns_in_topic: int, session_minutes: int) -> FocusState:
```

Decision tree:

```
turns_in_topic >= 3 AND session_minutes >= 45  →  "locked_in", hyperfocus_mode=True
turns_in_topic >= 2 AND session_minutes >= 20  →  "focused", hyperfocus_mode=False
session_minutes < 5                            →  "scattered"
otherwise                                      →  "normal"
```

`turns_in_topic` is taken from `state["turns_in_current_topic"]` in the supervisor's session state. `session_minutes` is computed in `graph/supervisor.py:_compute_session_duration`.

### `output_rules()` — RSD filter patterns

Two regex-based rules:

**`banned_phrases`**:
```
\b(you forgot|you should have|you didn't|why didn't you|you missed)\b
```
Matches direct blame phrases. The replacement guidance: reframe as observation ("Looks like X hasn't been logged yet" not "You forgot X").

**`failure_context_again`**:
```
\b(fail(?:ed)?|miss(?:ed)?|forgot|wrong|broke(?:n)?)[\w\s,\.]{0,30}\bagain\b
|\bagain[\w\s,\.]{0,30}(fail(?:ed)?|miss(?:ed)?|forgot|wrong|broke(?:n)?)\b
```
Matches "again" within 30 characters of a failure word, in either order. "Tell me again" (neutral) does NOT match — the word must appear near a failure word. Tests in `test_adhd_module.py` verify both the positive and negative cases explicitly.

### `output_guidance()` — prose guidance for the LLM

Two non-regex behavioral rules injected into the frozen prefix as prose:

1. "Lead with effort acknowledgment before corrective feedback. 'Nice work on X. For Y, maybe try...' not 'Y needs fixing'."
2. "Frame misses as normal, not failures. 'Happens!' not 'You failed to...'."

These are NOT checked programmatically — they are read and applied by the LLM. Regex-checkable rules go to `output_rules()`.

### `supervisor_context()` — overwhelm triggers

```python
def supervisor_context(self) -> dict[str, Any]:
    return {"overwhelm_triggers": list(self._profile.overwhelm_triggers)}
```

The supervisor injects these into the frozen prefix so the model knows which situations to handle with extra care (e.g., "too many open tasks", "surprise meetings"). The profile's `overwhelm_triggers` list is populated via the wizard or hand-editing.

### `planning_adjustments()` — ADHD planning constraints

```python
def planning_adjustments(self) -> PlanningConfig:
    return PlanningConfig(
        time_correction_factor=self._profile.time_correction_factor,  # default 1.5
        max_steps_per_plan=7,
        first_step_max_minutes=10,
        require_micro_step_first=True,
        energy_matching=True,
    )
```

The planner worker calls this to apply ADHD-appropriate plan constraints:
- **1.5× time correction**: all estimated minutes are multiplied.
- **Max 7 steps**: prevents overwhelming task lists.
- **First step max 10 minutes**: the first step must be achievable quickly to break inertia.
- **Micro-step first**: the very first step should be trivially small (e.g., "open the document").
- **Energy matching**: planner should select step variants matching the user's current energy level.

### `check_in_triggers()` — periodic energy check-ins

```python
def check_in_triggers(self) -> list[CheckInTrigger]:
    return [
        CheckInTrigger(
            name="energy_check",
            interval_minutes=self._profile.check_in_interval_minutes,  # default 120
            condition="only_if_no_recent_self_report",
            message_template="You've been going for {session_minutes}min — how's your energy?",
        ),
    ]
```

The `condition="only_if_no_recent_self_report"` is a string tag interpreted by `ContextEngine._compute_check_in_suggestion`. The message template has a single `{session_minutes}` interpolation slot.

---

## Part 4: RSD Filter (`kora_v2/core/rsd_filter.py`)

The RSD (Rejection-Sensitive Dysphoria) filter is a standalone async utility. It is not tied to `ADHDModule` — it consumes `list[OutputRule]` from any neurodivergent module.

```python
async def check_output(text: str, rules: list[OutputRule]) -> RSDFilterResult:
```

**Algorithm**:
1. If `text` is empty or `rules` is empty: return `RSDFilterResult(passed=True)`.
2. For each rule: compile `rule.pattern` with `re.IGNORECASE`. Use `pattern.finditer(text)` to find all matches.
3. For each match: append `{rule, match, position, suggestion}` to violations.
4. Return `RSDFilterResult(passed=len(violations)==0, violations=violations, rewritten=None)`.

`rewritten` is always `None` in the current phase. Automatic rewriting is planned for Phase 8 via a cheap LLM pass.

```python
class RSDFilterResult(BaseModel):
    passed: bool
    violations: list[dict[str, Any]]  # keys: rule, match, position, suggestion
    rewritten: str | None             # always None in Phase 5
```

**Current usage**: the filter is called in tests and is available for callers to invoke on any text. In the current supervisor graph, the filter is not wired into the main response path — the `output_rules()` patterns are injected into the frozen prefix as instructions to the LLM, not as a post-processing gate. Hooking the filter as a mandatory post-processing step before delivery is a known gap.

---

## Part 5: First-Run Wizard (`kora_v2/cli/first_run.py`)

The wizard is the ADHD onboarding surface. It runs on first launch when `_KoraMemory/User Model/adhd_profile/profile.yaml` does not exist, replacing the previous 3-question stub. It collects all data needed to populate `ADHDProfile` and the wizard summary.

### `WizardResult` dataclass

Collects all wizard outputs before persistence:

```python
@dataclass
class WizardResult:
    name: str
    pronouns: str
    use_case: str
    conditions: list[str]           # ["adhd", "anxiety", ...]
    peak_window_label: str          # "morning" | "late morning" | "afternoon" | "evening" | "varies"
    crash_window_label: str         # "early afternoon" | "late afternoon" | "evening" | "varies"
    medications_text: str           # freeform multi-line medication text
    coping_strategies: list[str]
    timezone: str                   # IANA name
    weekly_planning_day: str
    weekly_planning_time: time
    notifications_per_hour: int
    dnd_start: time | None
    dnd_end: time | None
    life_tracking_domains: list[str]
    minimax_api_key: str
    brave_api_key: str
```

### 5 wizard sections

| Section | Panel title | Key outputs |
|---|---|---|
| 1 | "Identity" | `name`, `pronouns`, `use_case` |
| 2 | "ADHD & Neurodivergent Support" | `conditions`, `peak_window_label`, `crash_window_label`, `medications_text`, `coping_strategies` — skipped if "adhd" not in conditions |
| 3 | "Planning" | `timezone` (auto-detected as default), `weekly_planning_day`, `weekly_planning_time`, `notifications_per_hour`, `dnd_start/end` |
| 4 | "Life Management" | `life_tracking_domains` (medications, meals, finances, routines, focus, all, none) |
| 5 | "API Keys" | `minimax_api_key`, `brave_api_key` — MiniMax key required; Brave optional for web search |

### Peak/crash window mappings

```python
_PEAK_RANGES = {
    "morning":      (6, 9),
    "late morning": (9, 12),
    "afternoon":    (12, 16),
    "evening":      (16, 21),
    "varies":       None,       # no entry added to profile
}

_CRASH_RANGES = {
    "early afternoon": (13, 15),
    "late afternoon":  (15, 17),
    "evening":         (17, 21),
    "varies":          None,
}
```

### Medication text parser (`_parse_medication_text`)

Accepts freeform multi-line input. Each line is matched against:
```
([A-Za-z][\w\s.-]*?)(?:\s+([\d.]+\s*(?:mg|mcg|g|mL)))?\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})
```

Example input:
```
Adderall XR 20mg 08:00-09:00
Adderall IR 10mg 13:00-15:00
```

Returns `list[MedicationScheduleEntry]`. Lines that do not match are silently ignored. The wizard tells the user they can edit `profile.yaml` by hand afterward.

### `_persist()` — what gets written

Called after all sections complete:

| Artifact | Path |
|---|---|
| `ADHDProfile` | `_KoraMemory/User Model/adhd_profile/profile.yaml` |
| Wizard summary | `_KoraMemory/User Model/adhd_profile/wizard_summary.md` |
| API keys | `.env` (appended; never overwrites existing keys) |
| Brave MCP config | `data/mcp_servers.json` |
| Settings mutations | `container.settings.user_tz`, `notifications.max_per_hour`, `notifications.dnd_start/end`, `planning.cadence.*` — in-place if container is provided |

### Timezone detection

`_detect_user_tz()` delegates to `kora_v2.core.settings._detect_user_tz()`. The detected IANA timezone name is presented as the default in Section 3. If the user accepts, it is stored in both `profile.yaml` (indirectly via settings) and `settings.user_tz`.

### Entry point

```python
async def run_wizard(
    console: Console,
    container: Any | None = None,
    memory_base: Path | None = None,
) -> WizardResult:
```

`EOFError` and `KeyboardInterrupt` are caught in the main `try/except` — cancelling mid-wizard returns a partial `WizardResult` without persisting. The caller in `cli/app.py:_check_first_run` determines whether to act on an incomplete result.

---

## Energy signal flow — complete diagram

```
User opens Kora
       │
       ▼
graph/supervisor.py: supervisor_node()
       │
       ├─► [once per session] container.adhd_module.output_guidance()
       │                      container.adhd_module.supervisor_context()
       │                      → injected into frozen_prefix
       │
       └─► [every turn] engine.build_day_context(session_state)
                               │
                               ├─► _build_medication_status(profile, med_log_rows, now_utc, user_tz)
                               │       → MedicationStatus {taken, pending, missed}
                               │
                               ├─► adhd_module.energy_signals(proxy_ctx, now=now_local, user_tz=user_tz)
                               │       → list[EnergySignal]
                               │
                               ├─► _estimate_energy(signals, last_check_in, now_utc)
                               │       → EnergyEstimate {level, focus, confidence, is_guess}
                               │
                               ├─► adhd_module.focus_detection(turns_in_topic, session_duration_min)
                               │       → FocusState → hyperfocus_mode
                               │
                               └─► DayContext {energy, hyperfocus_mode, medication_status,
                                               check_in_suggestion, upcoming_nudges, ...}
                                       │
                                       ▼
                               graph/prompts.py: _render_today_block()
                                       │
                                       ▼
                               dynamic suffix: "## Today (Monday, April 14)"
                               "Energy: medium/normal (guess — no check-in yet today)"
                               "Meds: Adderall pending (08:00-09:00)"
                               "[Check-in idea: You've been going for 150min...]"
```

---

## End-to-end: "A day in the ADHD life"

**7:50 AM** — First session of the day. Wizard ran at setup; `profile.yaml` has `peak_windows=[(9,12)]`, `crash_periods=[(13,15)]`, `medication_schedule=[{name:"Adderall XR", windows:[{start:"08:00",end:"09:00"}]}]`. `build_day_context` runs. No medication log entry yet → `pending`. No `energy_log` self-report → energy is a guess. No morning events → `OPEN_MORNING_ADJUSTMENT=+0.15`. `latent=0.65`, `level="medium"`, `is_guess=True`.

**8:10 AM** — "took my Adderall". `log_medication` called. Next turn: `MEDS_TAKEN_ADJUSTMENT=+0.2`. `latent=0.85`, `level="high"`. The medication entry disappears from `pending`, appears in `taken`.

**9:30 AM** — Session `turns_in_current_topic=4`, `session_duration_min=100`. `focus_detection(4, 100)` → `turns_in_topic >= 3 AND session_minutes >= 45` → `level="locked_in"`, `hyperfocus_mode=True`. `DayContext.hyperfocus_mode=True` is available in the supervisor state.

**11:00 AM** — User asks to plan a task. Planner reads `planning_adjustments()`: `time_correction_factor=1.5`, `max_steps_per_plan=7`, `first_step_max_minutes=10`. A 2-hour task becomes 3 hours in the plan. The first step is 5 minutes. The plan has 5 steps.

**2:00 PM** — User says "I reschedule my dentist to 3pm". `update_plan` checks: `reschedule_to.astimezone(user_tz).hour = 15`. `crash_periods=[(13,15)]`: 13 ≤ 15 < 15 → no match. `crash_periods=[(13,16)]` would match → `warnings` list gets "Dentist rescheduled into your usual crash window (13-16h)".

**3:30 PM** — Kora output contains "you missed your afternoon dose again". RSD filter: `check_output(text, rules)` → `banned_phrases` matches "you missed" → `passed=False`, `violations=[{rule:"banned_phrases", match:"you missed", suggestion:"Reframe as observation..."}]`. (In the current phase, the supervisor does not hook this filter automatically — this is a known gap.)

---

## Integration points

- **`core/di.py`**: `adhd_profile` property: lazy-loads `ADHDProfile` from `_KoraMemory/User Model/adhd_profile/profile.yaml` via `ADHDProfileLoader`. Falls back to `ADHDProfile()` defaults on any failure. `adhd_module` property: lazy-builds `ADHDModule(adhd_profile)`. Both are singletons per container lifetime.
- **`context/engine.py`**: `ContextEngine.__init__` accepts `adhd_module: ADHDModule`. Used in `build_day_context` for `energy_signals()`, `focus_detection()`, `check_in_triggers()`, and `_build_medication_status(adhd_module.profile, ...)`.
- **`graph/supervisor.py`**: calls `adhd_module.output_guidance()` and `adhd_module.supervisor_context()` to build the frozen prefix. Calls `engine.build_day_context()` every turn.
- **`agents/workers/planner.py`**: reads `adhd_module.planning_adjustments()` for `time_correction_factor`, `max_steps_per_plan`, `first_step_max_minutes`, `require_micro_step_first`.
- **`agents/workers/reviewer.py`**: `adhd_friendliness` is an explicit severity category in `ReviewFinding`.
- **`core/models.py`**: `ADHDViolation` and `ADHDScanResult` models exist for structured violation reporting (currently used in tests and available for callers).
- **`tools/planning.py`**: `apply_time_correction(minutes, profile)` multiplies by `profile.time_correction_factor` and clamps to a minimum of `minutes + 5`.

## Known limitations and stubs

- **RSD filter is not wired into the response pipeline**: `check_output()` exists and is tested, but the supervisor does not call it on the final response before delivery. Phase 8 is referenced as the target for automatic rewriting.
- **`rewritten` is always None**: the `RSDFilterResult.rewritten` field is reserved for Phase 8 LLM-based rewriting.
- **No multi-module support in production**: `NeurodivergentModule` is a Protocol designed for multiple modules, but `ContextEngine` and `di.py` hardwire a single `ADHDModule`. The `_adhd` attribute in `ContextEngine` is not a list.
- **`turns_in_current_topic` is not always maintained**: the supervisor state field `turns_in_current_topic` must be incremented by the turn runner when the topic stays the same. If it is not maintained, `focus_detection` always returns `"scattered"` for fresh sessions.
- **`profile.yaml` is not hot-reloaded**: if the user edits `profile.yaml` after startup, the change is not visible until daemon restart. The `adhd_profile` property is cached on first access (`self._adhd_profile is None` guard).
- **Overwhelm triggers are prose-only**: `overwhelm_triggers` from the profile are injected as text into the frozen prefix, but there is no programmatic detection — the LLM reads the list and decides whether a situation matches.
