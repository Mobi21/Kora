# Emotion Assessment Subsystem (`kora_v2/emotion/`)

The emotion subsystem classifies the user's current affective state as a
Pleasure-Arousal-Dominance (PAD) vector after each turn. It operates in two
tiers: a synchronous, rule-based fast assessor that runs on every message in
under 1 ms, and an async LLM assessor that is invoked only when the fast tier
is uncertain or a large emotional shift is detected. The result is an
`EmotionalState` model stored on `SessionState` and used downstream for
response tone, notification throttling, and proactive surfacing decisions.

## Files in this module

| File | Purpose |
|---|---|
| [`emotion/fast_assessor.py`](../../kora_v2/emotion/fast_assessor.py) | Rule-based PAD assessor — lexicon, topic valence, arousal signals, dominance patterns |
| [`emotion/llm_assessor.py`](../../kora_v2/emotion/llm_assessor.py) | LLM-based PAD assessor — async, structured JSON prompt, LRU cache, fallback logic |
| [`emotion/__init__.py`](../../kora_v2/emotion/__init__.py) | Single-line module docstring; no public exports |

---

## The PAD Model

PAD stands for **Pleasure** (valence), **Arousal**, and **Dominance** — a
three-dimensional model of affective space. Kora stores it as:

```python
class EmotionalState(BaseModel):   # kora_v2/core/models.py:33
    valence:    float   # [-1.0, 1.0]  negative = unpleasant, positive = pleasant
    arousal:    float   # [0.0, 1.0]   0 = calm, 1 = excited/high energy
    dominance:  float   # [0.0, 1.0]   0 = helpless, 1 = in control
    mood_label: str     # human-readable label derived from PAD coordinates
    confidence: float   # [0.0, 1.0]   how certain the assessment is
    source:     Literal["fast", "llm", "loaded"]
    assessed_at: datetime
```

---

## Tier 1 — `FastEmotionAssessor`

### When it runs

Every turn, synchronously, before any LLM call. No I/O; target latency < 1 ms.

### Input

```python
def assess(
    message: str,              # The current user message
    recent_messages: list[str],# Last ≤ 3 prior messages (for trajectory)
    current_state: EmotionalState | None,  # Previous state (for momentum)
) -> EmotionalState
```

### Signal pipeline

The assessor computes six signal categories, then blends them into PAD values:

#### 1. Sentiment lexicon

- **Positive words** (79 entries): `happy`, `joy`, `amazing`, `confident`,
  `productive`, `relieved`, etc.
- **Negative words** (73 entries): `sad`, `anxious`, `overwhelmed`, `hopeless`,
  `burnt`, `panicked`, etc.
- Score formula: `(pos_count - neg_count) / max(total_signals, 1) * 3.0`, then
  clamped to `[-1, 1]`.
- Normalization is by matched-word count (not total word count), so even one
  sentiment word in a long sentence produces a strong signal.

#### 2. Topic valence

46 keyword entries with fixed valence weights, e.g.:

```
"grief" → -0.9    "cancer" → -0.7    "trauma" → -0.8
"celebration" → 0.8    "promotion" → 0.8    "graduation" → 0.8
"money" → -0.2    "debt" → -0.6    "vacation" → 0.7
```

The strongest signal (by absolute value) wins when multiple topics match.

#### 3. Arousal signals

Four structural features of the message text, averaged then applied to a
baseline-0.3 formula:

| Signal | Computation |
|---|---|
| Length | `min(len(message) / 200.0, 1.0)` — 200 chars maps to max |
| Caps ratio | `(uppercase_alpha / total_alpha) * 1.5` — amplified |
| Punctuation density | `(! + ?) per 100 chars / 5.0` — 5 marks per 100 = max |
| Emoji density | `emoji_count / (len / 20)` |

Final arousal: `0.3 + 0.7 * min(avg_components, 1.0)`.

#### 4. Emoji sentiment

36-entry mapping from emoji characters to float sentiment values:

```
😊 → 0.8    😭 → -0.9    ❤️ → 0.9    🤔 → 0.0    😡 → -0.9
```

Mean of all matched emoji scores, clamped to `[-1, 1]`.

#### 5. Dominance (agency vs. helplessness)

17 agency regex patterns vs. 17 helplessness patterns:

```
Agency:      r"\bi will\b"  r"\bi solved\b"  r"\bi got this\b"
Helplessness: r"\bi can't\b"  r"\bnothing works\b"  r"\bi'm powerless\b"
```

Formula: `0.7 * (agency_hits / total_hits) + 0.15`
Range is therefore `[0.15, 0.85]`; baseline 0.5 when no signals found.

#### 6. Trajectory

Compares current message sentiment to the mean sentiment of the last 3 messages.
Used only for confidence scoring (high delta = inconsistent = lower confidence).

### Valence blending

**With prior state (momentum):**
```
valence = 0.5 * sentiment + 0.15 * topic_val + 0.15 * emoji_sentiment
        + 0.2 * current_state.valence
```

**First message (no prior state):**
```
valence = 0.6 * sentiment + 0.2 * topic_val + 0.2 * emoji_sentiment
```

### Confidence scoring

```
confidence = (abs(sentiment) + signal_ratio + trajectory_consistency) / 3.0
```

Where:
- `signal_ratio = signals_found / 6` (6 possible signal categories)
- `trajectory_consistency = max(0.0, 1.0 - abs(trajectory_delta))`

### Mood label mapping (`_pad_to_mood`)

| Condition | Label |
|---|---|
| Near-neutral valence `(-0.2, 0.2)` and low arousal `≤ 0.3` | `"neutral"` or `"relaxed"` (by dominance) |
| High V + High A + High D | `"excited"` |
| High V + High A | `"happy"` |
| High V + Low A + High D | `"content"` |
| High V + Low A | `"calm"` |
| Low V + High A + High D | `"angry"` |
| Low V + High A + Low D | `"anxious"` |
| Low V + Low A + Low D | `"sad"` |
| Low V + Low A + High D | `"tired"` |

Thresholds: high valence > 0.2, low < -0.2; high arousal > 0.4; high dominance > 0.5.

---

## Tier 2 — `LLMEmotionAssessor`

### When it runs

`should_trigger_llm_assessment()` in `llm_assessor.py` decides:

```python
def should_trigger_llm_assessment(
    current: EmotionalState,    # Fast tier result
    previous: EmotionalState | None,
    turns_since_last_llm: int,  # 0 = never run before
) -> bool:
```

| Trigger | Condition |
|---|---|
| Very low confidence | `current.confidence < 0.3` — always triggers (ignores cooldown) |
| Low confidence | `current.confidence < 0.5` |
| Large shift | Any PAD axis delta > 0.4 vs previous state |
| Cooldown suppressor | If `1 ≤ turns_since_last_llm ≤ 2`, skip (3-turn cooldown) |

### LRU cache

`LLMEmotionAssessor` maintains an `OrderedDict` LRU cache keyed on a SHA-256
hash of the last 5 messages joined with `\u0001`. Capacity: 32 entries.
This prevents re-paying a 30-second LLM call when the same message window
recurs (common in acceptance test harness scenarios).

### LLM prompt

The system prompt instructs the LLM to return a JSON object with no preamble:

```
Required fields:
- "valence": float in [-1.0, 1.0]
- "arousal": float in [0.0, 1.0]
- "dominance": float in [0.0, 1.0]
- "mood_label": one of: excited, elated, happy, surprised, calm, content,
  neutral, relaxed, anxious, distressed, angry, frustrated, sad, helpless, bored, tired
- "reasoning": string (one sentence)
```

Call parameters: `temperature=0.1`, `timeout=30.0` seconds (raised from 15s
after the 2026-04-11 acceptance run saw ~40% of calls time out).

### Response parsing (`_parse_and_validate`)

Four-step JSON recovery before validation:

1. Direct `json.loads`.
2. Extract from ` ```json ... ``` ` markdown block.
3. Find first balanced `{...}`.
4. (On failure) Return `None`.

After parsing, applies one coercion: if the response is `{"messages": [{...}]}`,
takes the first entry (schema drift observed in production).

Then validates against `PADResponse` (Pydantic model with field bounds):

```python
class PADResponse(BaseModel):
    valence:    float = Field(ge=-1.0, le=1.0)
    arousal:    float = Field(ge=0.0, le=1.0)
    dominance:  float = Field(ge=0.0, le=1.0)
    mood_label: str = "neutral"
```

### Repair retry

If the first parse/validate fails, a single repair attempt is made with an
explicit schema correction message:

> "Your previous response did not match the required schema. Return ONLY a JSON
> object with keys valence, arousal, dominance, mood_label."

### Fallback

All failure paths (timeout, exception, parse failure, both retries) call
`_fallback(current_state)`:

```python
return EmotionalState(
    valence=current_state.valence,
    arousal=current_state.arousal,
    dominance=current_state.dominance,
    mood_label=current_state.mood_label,
    confidence=current_state.confidence / 2.0,  # halved
    source="llm",
)
```

LLM-assessed results receive `confidence=0.85` and `source="llm"`.

---

## State storage

`EmotionalState` is stored on `SessionState.emotional_state`
(`kora_v2/core/models.py:146`). The session manager updates it after each turn.

---

## Events

Both tiers publish events into the `EventEmitter` (`kora_v2/core/events.py`)
so that downstream subsystems — the orchestration engine, the life module,
and the notification gate — can react to affective changes without polling
the session state:

| Event | Payload | Fired by |
|---|---|---|
| `EMOTION_STATE_ASSESSED` | `{state: EmotionalState, source: "fast" \| "llm", turn_id}` | After every fast or LLM assessment completes and the result is written to `SessionState.emotional_state`. The `ContextEngine` listens for this to stamp the current PAD vector into the ambient `DayContext`; the `NotificationGate` listens so that hyperfocus detection can react to an arousal jump within the same turn. |
| `EMOTION_SHIFT_DETECTED` | `{previous: EmotionalState, current: EmotionalState, delta: dict[str, float]}` | Fired only when any PAD axis changes by more than 0.4 between consecutive assessments. The life module uses this to trigger a proactive check-in pipeline via the orchestration engine's `EVENT` trigger kind; the signal scanner uses it to elevate the shift into a `Signal` candidate. |

Subscribing to these events is how the rest of the system stays in sync with
the two-tier assessor without importing emotion-module internals. See
[`../01-runtime-core/core.md`](../01-runtime-core/core.md) for the full
`EventType` enum.

---

## Integration points

- **Session manager** (`kora_v2/daemon/sessions.py`): calls `FastEmotionAssessor.assess()`
  each turn; calls `LLMEmotionAssessor.assess()` when triggered.
- **System prompt construction** (graph / supervisor): reads `emotional_state`
  from session context to adjust response tone.
- **Life engine** (`kora_v2/life/`): reads `emotional_state.mood_label` and
  `valence` for proactive notification decisions; subscribes to
  `EMOTION_SHIFT_DETECTED` via the event emitter.
- **Notification gate** (`kora_v2/runtime/orchestration/notifications.py`):
  subscribes to `EMOTION_STATE_ASSESSED` so that hyperfocus detection — which
  relies on sustained high arousal — reacts inside the same turn as the
  assessment that triggered it. See
  [`../01-runtime-core/orchestration.md`](../01-runtime-core/orchestration.md).
- **DI container** (`kora_v2/core/di.py`): instantiates both assessors; injects
  the LLM provider into `LLMEmotionAssessor`.
