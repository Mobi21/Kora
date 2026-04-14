# LLM Provider Subsystem (`kora_v2/llm/`)

The LLM subsystem defines a provider-agnostic interface (`LLMProviderBase`) and
provides two concrete implementations: `MiniMaxProvider` for production
conversational use and `ClaudeCodeDelegate` for delegating code-heavy autonomous
work to a Claude Code subprocess. All callers in the graph, workers, and
extraction pipeline depend on the base interface, not on any concrete class.

## Files in this module

| File | Purpose |
|---|---|
| [`llm/types.py`](../../kora_v2/llm/types.py) | Provider-agnostic data models: `ModelTier`, `GenerationResult`, `StreamChunk`, `StreamEvent`, content block types, `ToolCall` |
| [`llm/base.py`](../../kora_v2/llm/base.py) | `LLMProviderBase` — abstract interface all providers must implement |
| [`llm/minimax.py`](../../kora_v2/llm/minimax.py) | `MiniMaxProvider` — production provider over MiniMax's Anthropic-compatible API |
| [`llm/claude_code.py`](../../kora_v2/llm/claude_code.py) | `ClaudeCodeDelegate` — subprocess shim for delegating to the `claude` CLI binary |
| [`llm/__init__.py`](../../kora_v2/llm/__init__.py) | Empty (no public re-exports) |

---

## `types.py` — Shared Data Models

### `LLMMode`

`StrEnum` with values `CONVERSATION`, `REFLECTION`, `BACKGROUND`. Used to
signal operating mode to providers that support `set_mode()`.

### `ModelTier`

`StrEnum` used in every generation call signature:

| Value | Meaning |
|---|---|
| `CONVERSATION` | Primary model (default) — user-facing responses |
| `BACKGROUND` | Cheaper model variant for background/extraction tasks |

`MiniMaxProvider._select_model()` maps `BACKGROUND` to
`LLMSettings.background_model` (defaults to the primary model when empty).

### Content block types

Four Pydantic models wrap the typed content blocks that appear in Anthropic
API responses and are preserved in conversation history:

| Model | `type` field | Key fields |
|---|---|---|
| `ThinkingBlock` | `"thinking"` | `thinking: str`, `signature: str \| None` |
| `ToolUseBlock` | `"tool_use"` | `id`, `name`, `input: dict` |
| `ToolResultBlock` | `"tool_result"` | `tool_use_id`, `content`, `is_error` |
| `TextBlock` | `"text"` | `text: str` |

### `ToolCall`

Represents a single tool invocation requested by the LLM:

```python
class ToolCall(BaseModel):
    id: str          # 8-char uuid prefix, auto-generated
    name: str        # Tool function name
    arguments: dict  # Key-value argument pairs
```

### `GenerationResult`

The unified return type for all non-streaming generation calls:

```python
class GenerationResult(BaseModel):
    content: str                      # Text reply
    tool_calls: list[ToolCall]        # May be non-empty when finish_reason="tool_use"
    finish_reason: str                # "stop", "tool_use", "max_tokens"
    thought_text: str                 # Thinking block text (if enabled)
    thought_tokens: int               # Always 0 for MiniMax (lumped into output_tokens)
    prompt_tokens: int
    completion_tokens: int
    content_blocks: list[dict]        # Full block list for history preservation
    cache_creation_input_tokens: int  # Anthropic prompt caching stats
    cache_read_input_tokens: int
    input_sensitive: bool             # MiniMax content moderation flag
    output_sensitive: bool

    @property
    def has_tool_calls(self) -> bool: ...
```

### `StreamChunk`

Simple dataclass yielded from streaming calls:

```python
@dataclass
class StreamChunk:
    type: str   # "thinking" | "text"
    text: str
```

### `StreamEvent`

Richer event emitted by graph nodes via `get_stream_writer()`:

```python
@dataclass
class StreamEvent:
    type: str            # "status" | "token" | "thinking" | "tool_status"
    text: str
    phase: str = ""      # Node name that emitted this
    metadata: dict | None = None
```

---

## `base.py` — `LLMProviderBase`

Abstract base class every provider must subclass. All methods are `@abstractmethod`
except `analyze_screenshot` (a file-reading convenience wrapper over `analyze_image`)
and `probe_availability` / `set_mode` (which have default no-op implementations).

### Method contract

| Method | Signature | Returns | Notes |
|---|---|---|---|
| `generate` | `(messages, system_prompt, temperature, max_tokens, tier)` | `str` | Complete text response |
| `generate_stream` | same args | `AsyncIterator[str]` | Token stream, text only |
| `generate_with_thinking` | + `thinking_enabled` | `GenerationResult` | Includes thought blocks |
| `generate_stream_with_thinking` | same | `AsyncIterator[StreamChunk]` | Mixed thinking/text chunks |
| `generate_with_tools` | + `tools, tool_choice` | `GenerationResult` | Tool call support |
| `health_check` | `()` | `bool` | Full liveness check (may call model) |
| `probe_availability` | `()` | `bool` | Transport-level check (no tokens) |
| `create_cache` | `(system_prompt, ttl_seconds)` | `str \| None` | Prompt caching setup |
| `invalidate_cache` | `()` | `None` | Clear cache state |
| `analyze_image` | `(image_data, prompt, media_type, …)` | `str` | Vision analysis |
| `analyze_screenshot` | `(screenshot_path, prompt, …)` | `str` | Reads file then calls `analyze_image` |
| `set_mode` | `(mode: str)` | `None` | No-op default; providers may override |

### Abstract properties

- `model_name -> str` — current model name string
- `context_window -> int` — context window size in tokens

### `tool_choice` semantics

The `generate_with_tools` signature accepts `tool_choice: str | dict | None`:

| Value | Behaviour |
|---|---|
| `"auto"` | LLM may choose any tool or return prose |
| `"any"` | Force a tool call; LLM picks which one |
| `{"type": "tool", "name": "x"}` | Force a specific tool |
| `None` | Provider default: force single tool if only one; `auto` otherwise |

---

## `minimax.py` — `MiniMaxProvider`

The active production provider. Uses the Anthropic Python SDK pointed at
MiniMax's endpoint.

### API endpoint

- Default base URL: `https://api.minimax.io/anthropic`
- URL normalization: `_build_base_url()` strips any trailing `/anthropic` or `/v1`,
  then appends `/anthropic` when the host matches `api.minimax.io` or
  `api.minimaxi.com`.
- Auth: Bearer token via both `api_key=` parameter and
  `Authorization: Bearer <key>` default header (MiniMax requires both).

### Initialization

```python
MiniMaxProvider(settings: LLMSettings)
```

Constructs an `anthropic.AsyncAnthropic` client with:

- `trust_env=False` on the underlying `httpx.AsyncClient` — prevents SOCKS/system
  proxies from interfering.
- `max_retries=settings.retry_attempts` (default 3) — Anthropic SDK-level retry.
- `timeout=settings.timeout` (default 120 s).

Tracks cumulative `_call_count`, `_total_prompt_tokens`,
`_total_completion_tokens`, `_total_thinking_tokens` for observability.

### Model selection

`_select_model(tier: ModelTier) -> str`:

- `BACKGROUND` → `settings.background_model` if set, otherwise `settings.model`.
- `CONVERSATION` → `settings.model` (default `"MiniMax-M2.7"`).

### Pre-call safety check

Both `_call_api` and `_stream_api` estimate input tokens before sending:

1. `count_messages_tokens(api_messages)` — estimates from message list.
2. Adds system block tokens and tool definition tokens.
3. If estimate exceeds 95% of `context_window` (205 000 tokens):
   - Thinking is disabled for this call.
   - `max_tokens` is trimmed to `min(max_tokens, max(1024, remaining - 1000))`.
4. If fewer than 2 000 tokens remain: raises `LLMGenerationError` — compaction
   required.

### Thinking configuration

When `thinking_enabled=True`, `_build_params()` sets:

```python
params["thinking"] = {"type": "enabled", "budget_tokens": max_tokens}
params["max_tokens"] = max_tokens * 2   # Allocate room for both thinking and answer
```

### Empty-response retry

`_call_api` detects the case where `finish_reason == "max_tokens"` with no
content and no tool calls (model burned all tokens on thinking). It retries once
with `thinking["type"] = "disabled"` and the original `max_tokens`.

### Prompt caching

When `settings.enable_caching` is `True`:

- System prompt blocks get `"cache_control": {"type": "ephemeral"}`.
- The last tool definition in the tools list also gets `cache_control` applied.
- `create_cache()` tracks a content hash to avoid re-marking unchanged prompts.
- `invalidate_cache()` clears `_cache_hash`.

### Streaming (`_stream_api`)

Uses `self._client.messages.stream(**params)` (async context manager). On each
`content_block_delta` event:

- `delta.type == "thinking_delta"` → yields `StreamChunk(type="thinking", ...)`
- `delta.type == "text_delta"` → yields `StreamChunk(type="text", ...)`

### Message formatting (`_format_messages`)

Converts Kora's internal message list to Anthropic API format:

| Input role | Output handling |
|---|---|
| `"system"` | Extracted to separate system blocks list |
| `"assistant"` with `content_blocks` | Sanitized block-list (normalizes `tool_call` → `tool_use`) |
| `"assistant"` with `tool_calls` | Builds list of `thinking`, `text`, and `tool_use` blocks |
| `"tool"` | Batches consecutive tool messages into one `user` message with `tool_result` blocks |
| `"user"` / `"assistant"` | Plain `{"role": ..., "content": ...}` |

### Orphan cleanup (`cleanup_incomplete_messages`)

Static method called before every API request. Two-pass algorithm:

1. **Pass 1**: Strip trailing assistant messages that contain `tool_use` but have
   no following `tool_result` (prevents MiniMax API error 2013).
2. **Pass 2**: Walk the full list; for each assistant message with `tool_use`,
   collect all consecutive user messages carrying `tool_result` blocks and verify
   every `tool_use` ID has a matching result. Remove orphaned pairs.

### Error handling

| SDK exception | Kora exception |
|---|---|
| `anthropic.AuthenticationError` | `LLMConnectionError` |
| `anthropic.APITimeoutError` | `LLMTimeoutError` |
| `anthropic.APIConnectionError` | `LLMConnectionError` |
| `anthropic.RateLimitError` | `LLMGenerationError` |
| `anthropic.APIStatusError` | `LLMGenerationError` (includes HTTP status code) |
| `asyncio.TimeoutError` | `LLMTimeoutError` |

### Availability probe

`probe_availability()` sends an HTTP GET to the base URL with a 5-second
timeout via the underlying `httpx.AsyncClient` — no tokens consumed.

### Image analysis

`analyze_image()` encodes bytes as base64 and sends a standard Anthropic
vision request with the image `source.type = "base64"`. Supports
`image/png`, `image/jpeg`, `image/gif`, `image/webp`.

### JSON resilience (`_safe_parse_json`)

Static utility for callers that expect JSON from the LLM. Four fallbacks in
order: direct `json.loads`, extract from markdown code block, find first
balanced `{...}`, find first balanced `[...]`. Logs a warning and returns
`default` if all fail.

### Observability (`get_status`)

Returns a dict with: `provider`, `model`, `base_url`, `call_count`,
`total_prompt_tokens`, `total_completion_tokens`, `caching_enabled`.

---

## `claude_code.py` — `ClaudeCodeDelegate`

Not a `LLMProviderBase` subclass. A subprocess-based delegate for handing off
code-heavy autonomous work to an installed `claude` CLI binary.

### Models

| Model | Purpose |
|---|---|
| `DelegationBrief` | Structured task brief: goal, target_files, target_dirs, allowed_tools, forbidden_actions, expected_deliverables, validation_steps, budget_limits, stop_conditions, context |
| `DelegateOutput` | Parsed result: summary, files_touched, tests_run, validation_result (`"passed"/"failed"/"skipped"`), remaining_risks, patch_references, exit_code, raw_output |
| `DelegateFailure` | Classified failure: category (missing_binary, timeout, nonzero_exit, malformed_output, validation_failure, policy_violation, budget_exhaustion), message, exit_code, raw_output |
| `DelegateResult` | Final result wrapper: success, output, failure, attempts (1 or 2), fell_back_to_local |

### `ClaudeCodeDelegate` class

Constructor:

```python
ClaudeCodeDelegate(
    claude_binary: str = "claude",
    default_timeout: int = 300,
    max_output_bytes: int = 1_000_000,
)
```

### `delegate(brief, working_dir)` — main entry point

1. Builds a plain-text prompt from the brief via `_build_prompt()`.
2. Runs the `claude --print [--allowedTools ...]` subprocess with `asyncio.create_subprocess_exec`.
3. Stdin receives the prompt bytes; stdout is captured; stderr truncated to 4 096 bytes.
4. On success: attempts to parse the output as JSON (`_parse_output`).
5. On failure: classifies via `_classify_failure`, then retries once with a
   narrowed brief (`_narrow_brief` — trims goal to 120 chars, keeps first 3
   target files, truncates context to 500 chars).
6. Missing binary: no retry; returns `fell_back_to_local=True` immediately.

### Output parsing (`_parse_output`)

Three-stage fallback:

1. JSON inside ` ```json ... ``` ` block.
2. Any bare JSON object containing `"summary"`.
3. Full raw text truncated to 500 chars as `summary`.

### Failure classification (`_classify_failure`)

Scans `stdout + stderr` (lowercased) for keywords:

- `"budget"` / `"rate limit"` / `"quota"` → `budget_exhaustion`
- `"policy"` / `"violation"` / `"not allowed"` → `policy_violation`
- Otherwise → `nonzero_exit`
- Separate path for `timed_out` → `timeout`

### Availability check

`is_available()` uses `shutil.which(self._binary)` — no subprocess spawn.

---

## Integration points

- **Graph / supervisor** (`kora_v2/graph/supervisor.py`): consumes
  `LLMProviderBase` injected via the DI container.
- **Workers** (`kora_v2/agents/workers/`): planner, executor, and reviewer each
  call `generate_with_thinking` or `generate_with_tools`.
- **Emotion assessor** (`kora_v2/emotion/llm_assessor.py`): calls
  `generate(messages, system_prompt, temperature=0.1)` — no thinking, no tools.
- **DI container** (`kora_v2/core/di.py`): instantiates `MiniMaxProvider` with
  `LLMSettings` from `KoraSettings`.
- **Settings** (`kora_v2/core/settings.py`): `LLMSettings` — api_key resolved
  from `MINIMAX_API_KEY` env var or `.env`/`.env.local` files.
- **Context budget** (`kora_v2/context/budget.py`): `count_tokens` and
  `count_messages_tokens` are imported by `minimax.py` for pre-call safety.
