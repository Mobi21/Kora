# CLI Subsystem (`kora_v2/cli/`)

The CLI subsystem is a Rich-based terminal client that connects to a running
Kora daemon over WebSocket, streams responses token-by-token, and handles slash
commands, tool permission prompts, and first-run onboarding. The daemon must be
started separately before the CLI is launched.

## Files in this module

| File | Purpose |
|---|---|
| [`cli/__main__.py`](../../kora_v2/cli/__main__.py) | Entry point for `python -m kora_v2.cli`; creates and runs `KoraCLI()` |
| [`cli/app.py`](../../kora_v2/cli/app.py) | `KoraCLI` class — main event loop, WebSocket client, slash commands, REST helpers |
| [`cli/first_run.py`](../../kora_v2/cli/first_run.py) | 5-section onboarding wizard; persists ADHD profile, settings, and API keys |
| [`cli/__init__.py`](../../kora_v2/cli/__init__.py) | Empty |

---

## `app.py` — `KoraCLI`

### Overview

```
KoraCLI
  ├── Discovery: lockfile + token file → port + auth
  ├── WebSocket connection (websockets library)
  ├── REPL: Prompt.ask → parse_command → _send_message / _handle_command
  ├── REST helpers: _rest_get / _rest_post for status and control endpoints
  └── Cleanup: ws.close() on exit
```

### Constructor

```python
KoraCLI(
    host: str = "127.0.0.1",
    port: int | None = None,   # Auto-discovered from lockfile if None
    token: str | None = None,  # Auto-discovered from data/.api_token if None
)
```

Internal state:

| Attribute | Type | Purpose |
|---|---|---|
| `_ws` | websockets connection | Active WebSocket handle |
| `_session_id` | `str \| None` | Not set by current code (placeholder) |
| `_console` | `rich.console.Console` | All terminal output |
| `_running` | `bool` | Main loop sentinel |
| `_response_buffer` | `str` | Accumulates streamed tokens |
| `_lockfile_path` | `Path` | `data/.lockfile` — patchable in tests |
| `_token_path` | `Path` | `data/.api_token` — patchable in tests |

### Port and token discovery

`_discover_port()` reads `data/.lockfile` as JSON and prefers `api_port` over
the legacy `port` key.

`_read_token()` reads `data/.api_token` (plain text, stripped).

Both methods return `None` on any error; `connect()` will print an error and
return `False` without raising.

### WebSocket connection

```
URI: ws://127.0.0.1:<port>/api/v1/ws?token=<token>
```

Token is passed as a query parameter (not a header). Uses the `websockets`
library (`import websockets` at call time — lazy import).

On success, `self._resolved_port` and `self._resolved_token` are set for later
REST call use.

### Reconnection

`reconnect()` tries up to `MAX_RECONNECT_ATTEMPTS = 5` times with exponential
backoff:

```
attempt 0 → 1 s delay
attempt 1 → 2 s delay
attempt 2 → 4 s delay
attempt 3 → 8 s delay
attempt 4 → 16 s delay
```

### Main loop

```python
while self._running:
    user_input = await run_in_executor(None, lambda: Prompt.ask("[bold cyan]You[/bold cyan]"))
    if not user_input.strip():
        continue
    command, args = parse_command(user_input)
    if command:
        should_continue = await _handle_command(command, args)
    else:
        await _send_message(user_input)
```

`Prompt.ask` is always run in the executor (thread) to avoid blocking the event
loop. `EOFError` and `KeyboardInterrupt` from the executor are caught and cause
clean exit.

### `parse_command(text) -> (str | None, str)`

Returns `(command_name, args)` for strings starting with `/`, or
`(None, original_text)` for regular messages. Command name is lowercased.

### `format_streaming_token(token: str) -> str`

Currently a passthrough. Reserved for future Rich rendering enhancements.

---

## WebSocket Message Protocol

All messages are JSON objects with a `"type"` discriminator.

### Client → Server

| Message | Shape | Purpose |
|---|---|---|
| `chat` | `{"type": "chat", "content": "..."}` | Send user message |
| `pong` | `{"type": "pong"}` | Heartbeat response |
| `auth_response` | `{"type": "auth_response", "request_id": "...", "approved": bool, "scope": "allow_once"\|"allow_always"}` | Grant or deny tool permission |

### Server → Client

| Message | Shape | Meaning |
|---|---|---|
| `token` | `{"type": "token", "content": "..."}` | Streaming response token |
| `tool_start` | `{"type": "tool_start", "content": "<tool_name>"}` | Tool invocation started |
| `tool_result` | `{"type": "tool_result"}` | Tool call completed |
| `response_complete` | `{"type": "response_complete"}` | End of response stream |
| `error` | `{"type": "error", "content": "..."}` | Error message |
| `ping` | `{"type": "ping"}` | Server heartbeat |
| `auth_request` | `{"type": "auth_request", "tool": "...", "args": {...}, "request_id": "..."}` | Request user permission for a tool |

### Recv timeout

`asyncio.wait_for(self._ws.recv(), timeout=120)` — 120-second per-message
timeout. On timeout: prints yellow warning and breaks the recv loop.

### Connection failure recovery

If `_send_message` raises an exception, `reconnect()` is attempted. On success,
a message is printed asking the user to resend manually (the message is not
automatically retried).

---

## Slash commands

| Command | Handler | REST call |
|---|---|---|
| `/help` | Prints `Panel` with command list | — |
| `/status` | `_cmd_status` | GET `/api/v1/status` |
| `/stop` | `_cmd_stop` | POST `/api/v1/daemon/shutdown` |
| `/memory [query]` | `_cmd_memory` | GET `/api/v1/memory/recall?q=<query>` |
| `/plan` | `_cmd_plan` | GET `/api/v1/inspect/autonomous` |
| `/compact` | `_cmd_compact` | POST `/api/v1/compact` |
| `/permissions` | `_cmd_permissions` | GET `/api/v1/permissions` |
| `/quit` or `/exit` | Sets `_running = False` | — |

### REST helpers

`_rest_get(path)` and `_rest_post(path)` build the full URL from
`_resolved_port`, attach `Authorization: Bearer <token>` headers, and return
the parsed JSON body or `None` on failure. Uses `httpx.AsyncClient` with
no timeout specified (relies on httpx default).

### Rich components used

| Component | Usage |
|---|---|
| `Console` | All terminal output — stdout and colour |
| `Panel` | Welcome banner, help command, tool permission prompt, memory results, plan details |
| `Prompt.ask` | User input and all wizard prompts |
| `Confirm.ask` | Yes/no wizard confirmations |
| `Table` | `/status` output and `/permissions` grant list |

---

## `first_run.py` — Onboarding Wizard

### Trigger

`_check_first_run()` runs immediately after the first successful connection. It
checks for the existence of `_KoraMemory/.kora/bridges/*.md`. If none exist,
the wizard runs.

### `WizardResult` dataclass

Captures everything the wizard collects:

```python
@dataclass
class WizardResult:
    name: str
    pronouns: str
    use_case: str
    conditions: list[str]          # ["adhd", "anxiety", ...]
    peak_window_label: str         # "late morning", "afternoon", etc.
    crash_window_label: str
    medications_text: str          # Freeform medication schedule
    coping_strategies: list[str]
    timezone: str                  # IANA tz name
    weekly_planning_day: str
    weekly_planning_time: time
    notifications_per_hour: int
    dnd_start: time | None
    dnd_end: time | None
    life_tracking_domains: list[str]
    minimax_api_key: str
    brave_api_key: str
```

### Five wizard sections

| Section | Title | Key prompts |
|---|---|---|
| 1 | Identity | Name, pronouns, use case |
| 2 | ADHD & Neurodivergent | Conditions, peak/crash windows, medication schedule, coping strategies |
| 3 | Planning | Timezone (auto-detected), weekly planning day/time, notifications/hour, DND window |
| 4 | Life Management | Domains to track (medications, meals, finances, routines, focus) |
| 5 | API Keys | MiniMax API key (if not already in env), optional Brave API key |

All prompts run in `run_in_executor` via `_aprompt` and `_aconfirm` wrappers.
`EOFError` / `KeyboardInterrupt` at any point cancels the wizard cleanly.

### Medication parsing (`_parse_medication_text`)

Regex pattern extracts lines of the form:
`<name> [<dose_with_unit>] <HH:MM>-<HH:MM>`

Dose must include a unit (`mg`, `mcg`, `g`, `mL`). Returns
`list[MedicationScheduleEntry]`; empty list on no match.

### Peak/crash window mapping

```python
_PEAK_RANGES = {
    "morning": (6, 9), "late morning": (9, 12),
    "afternoon": (12, 16), "evening": (16, 21), "varies": None,
}
_CRASH_RANGES = {
    "early afternoon": (13, 15), "late afternoon": (15, 17),
    "evening": (17, 21), "varies": None,
}
```

### Persistence (`_persist`)

Called after all sections complete (unless cancelled):

1. `ADHDProfileLoader(memory_base).save(profile)` → writes
   `_KoraMemory/User Model/adhd_profile/profile.yaml`.
2. `_write_wizard_summary()` → writes
   `_KoraMemory/User Model/adhd_profile/wizard_summary.md` (YAML frontmatter +
   prose `use_case` body).
3. `_append_env_keys()` → appends `MINIMAX_API_KEY` / `BRAVE_API_KEY` to
   `.env` and sets them in `os.environ`. Never overwrites an existing key.
4. `_write_brave_mcp_config()` → writes `data/mcp_servers.json` with a
   `brave_search` entry (only if Brave key was provided).
5. Settings mutation: updates `container.settings.user_tz`,
   `notifications.max_per_hour`, `dnd_start/end`, and `planning.cadence`.

After the wizard, `_check_first_run` sends an introduction message to the
daemon over WebSocket using the collected name and use case.

### `run_wizard` entry point

```python
async def run_wizard(
    console: Console,
    container: Any | None = None,
    memory_base: Path | None = None,
) -> WizardResult
```

When `memory_base` is `None` and `container` is provided, the memory path is
read from `container.settings.memory.kora_memory_path`.

---

## Integration points

- **Daemon** (`kora_v2/daemon/server.py`): exposes `ws://<host>:<port>/api/v1/ws`
  and REST endpoints consumed by the CLI.
- **Lockfile** (`kora_v2/daemon/launcher.py`): writes `data/.lockfile` JSON
  with `api_port`; read by `_discover_port()`.
- **Auth token** (`kora_v2/daemon/launcher.py`): writes `data/.api_token`;
  read by `_read_token()`.
- **ADHD profile** (`kora_v2/adhd/profile.py`): `ADHDProfileLoader` is
  instantiated by `first_run._persist()`.
- **Auth relay** (`kora_v2/runtime/`): `auth_request` / `auth_response` messages
  flow through the daemon to the auth relay and back.
