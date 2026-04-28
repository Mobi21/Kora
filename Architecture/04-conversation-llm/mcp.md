# MCP Subsystem (`kora_v2/mcp/`)

The MCP (Model Context Protocol) subsystem manages long-lived subprocesses that
expose external tools to the Kora runtime. Each configured MCP server is a
child process communicating over stdin/stdout using JSON-RPC 2.0. The manager
handles lazy startup, protocol handshake, tool discovery, and call routing.
Failures surface explicitly to callers; older docs overstated transparent crash
recovery because the restart helper is not the normal `call_tool()` path. Callers see
`call_tool(server, tool, args)`.

## Files in this module

| File | Purpose |
|---|---|
| [`mcp/manager.py`](../../kora_v2/mcp/manager.py) | `MCPManager` — server lifecycle, JSON-RPC transport, tool registry |
| [`mcp/results.py`](../../kora_v2/mcp/results.py) | `MCPToolResult`, `MCPContentBlock` — structured result types |
| [`mcp/__init__.py`](../../kora_v2/mcp/__init__.py) | Re-exports all public names from both modules |

---

## Config format (`MCPSettings` and `MCPServerConfig`)

Defined in `kora_v2/core/settings.py`:

```python
class MCPServerConfig(BaseModel):
    command: str           # Executable (e.g. "npx", "python")
    args: list[str] = []   # CLI arguments
    env: dict[str, str] = {}  # Extra environment variables (merged with os.environ)
    enabled: bool = True

class MCPSettings(BaseModel):
    servers: dict[str, MCPServerConfig] = {}  # name → config
    startup_timeout: int = 30                 # Handshake timeout in seconds
```

Example (written by `first_run._write_brave_mcp_config` when Brave key is
provided during onboarding):

```json
{
    "brave_search": {
        "command": "npx",
        "args": ["-y", "@anthropic/brave-search-mcp"],
        "env": {"BRAVE_API_KEY": "<key>"},
        "enabled": true
    }
}
```

---

## Server lifecycle

### States (`MCPServerState`)

```
STOPPED ──start_server──► STARTING ──handshake ok──► RUNNING
                              │                         │
                              └──error/timeout──► FAILED
                                                     ▲
                                               max restarts
```

The state is stored in `MCPServerInfo.state`. Once `FAILED`, the server will
not restart automatically until `_restart_with_backoff()` succeeds (called by
the operator; not triggered automatically on `call_tool`).

### `MCPServerInfo`

```python
class MCPServerInfo(BaseModel):
    name: str
    state: MCPServerState         # STOPPED | STARTING | RUNNING | FAILED
    pid: int | None
    tools: list[str]              # Tool names after handshake
    tool_schemas: dict[str, dict] # Full schema dicts keyed by name
    start_count: int
    last_error: str | None

    # Runtime-only (not schema fields):
    _process: asyncio.subprocess.Process | None
    _reader_task: asyncio.Task | None
    _pending: dict[int, asyncio.Future]  # request_id → future
    _next_id: int
    _lock: asyncio.Lock
```

---

## Startup sequence (`start_server`)

```
1. asyncio.create_subprocess_exec(command, *args,
       stdin=PIPE, stdout=PIPE, stderr=PIPE, env={**os.environ, **env_overrides})

2. Spawn _reader_loop as asyncio.Task  ← background dispatcher

3. JSON-RPC: initialize
   → {"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {
          "protocolVersion": "2024-11-05",
          "capabilities": {},
          "clientInfo": {"name": "kora", "version": "2.0"}
      }}
   ← waits for response (startup_timeout seconds)

4. JSON-RPC notification: notifications/initialized
   → {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
   (no response expected — notification, not request)

5. JSON-RPC: tools/list
   → {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
   ← parses tools array; stores names in info.tools and schemas in info.tool_schemas

6. Set state = RUNNING
```

On any timeout or exception during steps 3–5: `_terminate_process(info)`,
`state = FAILED`.

On `FileNotFoundError` (binary missing): `state = FAILED` immediately without
starting the reader task.

---

## JSON-RPC transport

### `_send_message(info, message)`

Serializes `message` to JSON + newline, then `info._process.stdin.write()` +
`drain()`. Raises `MCPServerUnavailableError` if stdin is gone.

### `_send_request(info, method, params, timeout=60.0)`

1. Allocates a `request_id` (monotonically incrementing `_next_id`).
2. Creates an `asyncio.Future` and stores it in `info._pending[request_id]`.
3. Sends the request via `_send_message`.
4. `asyncio.wait_for(future, timeout)`.
5. Removes the future from `_pending` in a `finally` block.

### `_send_notification(info, method, params)`

Sends a JSON-RPC message without an `"id"` field (no response expected).

### `_reader_loop(info)`

Background task that reads lines from `info._process.stdout`:

1. For each non-empty line: `json.loads(line)`.
2. Extracts `"id"` from the message.
3. Looks up `info._pending[id]`; if found and not done:
   - `"error"` in message → `future.set_exception(MCPError(...))`.
   - Otherwise → `future.set_result(message.get("result") or {})`.
4. Messages without `"id"` (server notifications) are silently discarded.
5. On EOF or crash: fails all remaining pending futures with
   `MCPServerUnavailableError`.

---

## Tool invocation (`call_tool`)

```python
async def call_tool(
    server: str,
    tool: str,
    args: dict | None = None,
) -> MCPToolResult
```

1. `ensure_server_running(server)` — lazy start if `STOPPED`; raises if `FAILED`.
2. Validates `tool` is in `info.tools`; raises `MCPToolNotFoundError` otherwise.
3. Sends `{"method": "tools/call", "params": {"name": tool, "arguments": args or {}}}`.
4. Timeout: `_DEFAULT_CALL_TIMEOUT = 60.0` seconds.
5. Returns `MCPToolResult.from_mcp(server, tool, raw_result)`.

`call_tool_text(server, tool, args)` is a backwards-compatible shim:
returns `(await call_tool(...)).text`.

---

## Crash recovery (`_restart_with_backoff`)

Backoff schedule (3 attempts max):

```
attempt 1: 0 s delay
attempt 2: 2 s delay
attempt 3: 4 s delay
```

On exhaustion: `state = FAILED`, `last_error` set. Returns `False`.

Note: `_restart_with_backoff` is defined but not called automatically by
`call_tool` or `ensure_server_running` — it is available for the operator
(runtime kernel or health check loop) to call explicitly.

---

## `results.py` — Structured Tool Results

### `MCPContentBlock`

Parsed representation of one block in the MCP `content` array:

```python
@dataclass
class MCPContentBlock:
    type: str           # "text", "image", "resource", "json", or other
    text: str | None    # present for text and json-as-text blocks
    data: dict | None   # present for json, image, resource blocks
    mime_type: str | None
```

`from_mcp(block: dict)` handles:

| Block type | Handling |
|---|---|
| `"text"` | `text = block["text"]` |
| `"json"` | `data = block["data"]` (dict or list wrapped in `{"items": ...}`) |
| `"image"` | `data = {"data": ..., "mimeType": ...}`, `mime_type` set |
| `"resource"` | `data = resource dict`, `text = resource.get("text")` |
| Unknown | Falls back to `text = repr(block)` — never raises |

### `MCPToolResult`

```python
@dataclass
class MCPToolResult:
    server: str
    tool: str
    is_error: bool            # From MCP isError flag
    content: list[MCPContentBlock]
    raw: dict                 # Full JSON-RPC result for advanced use

    @property
    def text(self) -> str: ...          # Joined text from all text blocks
    @property
    def structured_data(self) -> dict | None: ...  # First JSON block's data
    @property
    def first_json(self) -> dict | None: ...       # Alias for structured_data
```

`text` joins text-type block texts with `"\n"`.

`structured_data` searches blocks in order:
1. Any block with `.data` dict → return it.
2. Any text block where `json.loads(text)` succeeds as dict → return it.
3. List → return `{"items": list}`.
4. Returns `None` if nothing is parseable.

`from_mcp(server, tool, raw)` is robust to unexpected shapes — any non-list
`content` is wrapped in a single text block.

---

## Exceptions

All defined in `mcp/manager.py`, inheriting from `kora_v2.core.exceptions.KoraError`:

| Exception | When raised |
|---|---|
| `MCPError` | Base class for all MCP errors |
| `MCPServerNotFoundError` | `_require_server` when name not in `_servers` |
| `MCPServerUnavailableError` | Server is `FAILED`; or stdin/stdout unavailable |
| `MCPToolNotFoundError` | `call_tool` when `tool` not in `info.tools` |

---

## Integration points

- **DI container** (`kora_v2/core/di.py`): instantiates `MCPManager` with
  `KoraSettings.mcp`.
- **Runtime kernel** (`kora_v2/runtime/kernel.py`): starts/stops `MCPManager`
  as part of daemon lifecycle; may call `_restart_with_backoff` in health checks.
- **Tool verb resolver** (`kora_v2/tools/verb_resolver.py`): looks up MCP
  tools by name and routes `call_tool` invocations.
- **Settings** (`kora_v2/core/settings.py`): `MCPSettings.servers` comes from
  the settings system. The first-run wizard can write `data/mcp_servers.json`,
  but current `Settings` loading does not treat that file as an automatic
  public source of truth.
- **First-run wizard** (`kora_v2/cli/first_run.py`): writes `data/mcp_servers.json`
  when the user provides a Brave API key.
