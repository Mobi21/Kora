# Capabilities System

The capabilities system is the policy-aware integration boundary between Kora's internal reasoning and real-world external services. Each "capability pack" owns one integration domain (Google Workspace, browser automation, vault filesystem, system diagnostics), exposes typed `Action` objects through a shared `ActionRegistry`, and enforces a `PolicyMatrix` that determines whether each action requires user approval.

The system has 24 Python files across the `kora_v2/capabilities/` tree. It was introduced in Phase 9 to give each external action a stable name, a machine-readable approval policy, and a structured failure type (`StructuredFailure`) that the LLM can reason about rather than parsing error strings.

---

## Files in this module

### Top-level

| File | Purpose |
|---|---|
| [`kora_v2/capabilities/__init__.py`](../../kora_v2/capabilities/__init__.py) | Re-exports, module-level singleton registration |
| [`kora_v2/capabilities/base.py`](../../kora_v2/capabilities/base.py) | Core abstractions: `CapabilityPack`, `Action`, `CapabilityHealth`, `StructuredFailure`, `HealthStatus` |
| [`kora_v2/capabilities/policy.py`](../../kora_v2/capabilities/policy.py) | Policy matrix: `PolicyMatrix`, `PolicyKey`, `PolicyRule`, `ApprovalMode`, `Decision`, `SessionState`, `TaskState` |
| [`kora_v2/capabilities/registry.py`](../../kora_v2/capabilities/registry.py) | `ActionRegistry`, `CapabilityRegistry`, module-level singleton |

### `browser/` (6 files)

| File | Purpose |
|---|---|
| [`browser/__init__.py`](../../kora_v2/capabilities/browser/__init__.py) | `BrowserCapability` pack |
| [`browser/actions.py`](../../kora_v2/capabilities/browser/actions.py) | 9 action coroutines + `BrowserActionContext` |
| [`browser/binary.py`](../../kora_v2/capabilities/browser/binary.py) | `BrowserBinary` async subprocess wrapper |
| [`browser/config.py`](../../kora_v2/capabilities/browser/config.py) | `BrowserCapabilityConfig` dataclass |
| [`browser/health.py`](../../kora_v2/capabilities/browser/health.py) | `check_browser_health()` |
| [`browser/policy.py`](../../kora_v2/capabilities/browser/policy.py) | `build_browser_policy()` |

### `vault/` (6 files)

| File | Purpose |
|---|---|
| [`vault/__init__.py`](../../kora_v2/capabilities/vault/__init__.py) | `VaultCapability` pack |
| [`vault/actions.py`](../../kora_v2/capabilities/vault/actions.py) | 3 action coroutines + `VaultActionContext` |
| [`vault/mirror.py`](../../kora_v2/capabilities/vault/mirror.py) | `FilesystemMirror`, `NullMirror`, `MirrorTarget` ABC, `WriteResult` |
| [`vault/config.py`](../../kora_v2/capabilities/vault/config.py) | `VaultCapabilityConfig` dataclass |
| [`vault/health.py`](../../kora_v2/capabilities/vault/health.py) | `check_vault_health()` |
| [`vault/policy.py`](../../kora_v2/capabilities/vault/policy.py) | `build_vault_policy()` |

### `workspace/` (6 files)

| File | Purpose |
|---|---|
| [`workspace/__init__.py`](../../kora_v2/capabilities/workspace/__init__.py) | `WorkspaceCapability` pack |
| [`workspace/actions.py`](../../kora_v2/capabilities/workspace/actions.py) | 17 action coroutines + `WorkspaceActionContext` + `_call_action()` dispatcher |
| [`workspace/config.py`](../../kora_v2/capabilities/workspace/config.py) | `WorkspaceConfig` Pydantic model with `tool_map` |
| [`workspace/health.py`](../../kora_v2/capabilities/workspace/health.py) | `check_workspace_health()` via MCP tool discovery |
| [`workspace/policy.py`](../../kora_v2/capabilities/workspace/policy.py) | `build_default_policy()` |
| [`workspace/provenance.py`](../../kora_v2/capabilities/workspace/provenance.py) | `inject_calendar_create_provenance()` |

### `doctor/` (2 files)

| File | Purpose |
|---|---|
| [`doctor/__init__.py`](../../kora_v2/capabilities/doctor/__init__.py) | `DoctorCapability` scaffolding |
| [`doctor/checks.py`](../../kora_v2/capabilities/doctor/checks.py) | Stub only — one-line docstring, no implementation |

---

## Core abstractions (`base.py`)

### `CapabilityPack`

Protocol/base class. Three methods that concrete packs must implement:

```python
async def health_check(self) -> CapabilityHealth: ...
def register_actions(self, registry: ActionRegistry) -> None: ...
def get_policy(self) -> Policy: ...
```

Also has `name: str` and `description: str` class attributes.

### `Action`

Dataclass representing a single callable action:

```python
@dataclass
class Action:
    name: str          # e.g., "workspace.gmail.search"
    description: str
    capability: str    # e.g., "workspace"
    input_schema: dict # JSON schema for tool args
    requires_approval: bool = False
    read_only: bool = True
    handler: Callable | None = None  # async coroutine set by register_actions()
```

### `CapabilityHealth`

```python
@dataclass
class CapabilityHealth:
    status: HealthStatus
    summary: str
    details: dict = {}
    remediation: str | None = None
```

`HealthStatus` enum: `ok | degraded | unhealthy | unconfigured | unimplemented`.

### `StructuredFailure`

Returned instead of raising exceptions when an action fails:

```python
@dataclass
class StructuredFailure:
    capability: str      # "workspace"
    action: str          # "gmail.send"
    path: str            # "mcp.workspace.send_gmail_message"
    reason: str          # machine-readable: "auth_required", "policy_denied", ...
    user_message: str    # plain-language Kora can relay
    recoverable: bool
    machine_details: dict = {}
```

---

## Policy system (`policy.py`)

### `ApprovalMode`

```python
class ApprovalMode(StrEnum):
    NEVER_ASK         # always allowed, no prompt
    FIRST_PER_SESSION # prompt once per session
    FIRST_PER_TASK    # prompt once per autonomous task/turn group
    ALWAYS_ASK        # prompt every time
    DENY              # never allowed
```

### `PolicyKey`

Identifies a specific capability/account/action/resource combination. Uses `match_score()` to determine specificity (0–4). `None` fields are wildcards. Most-specific matching rule wins.

```
score 0 = no match (different capability)
score 1 = capability matches (action wildcard)
score 2 = capability + action match
score 3 = capability + action + account match
score 4 = capability + action + account + resource (most specific)
```

### `PolicyMatrix`

Ordered rule list. `evaluate(key, session, task)` finds the most-specific matching rule and applies it:

- `DENY` → `Decision(allowed=False)`
- `NEVER_ASK` → `Decision(allowed=True, requires_prompt=False)`
- `FIRST_PER_SESSION` → allowed; `requires_prompt=True` unless already in `session.granted_this_session`
- `FIRST_PER_TASK` → allowed; `requires_prompt=True` unless already in `task.granted_this_task`
- `ALWAYS_ASK` → `Decision(allowed=True, requires_prompt=True)`

When a write action is approved with `FIRST_PER_*`, the serialised `PolicyKey` is added to the session/task granted set so future calls don't re-prompt.

### `SessionState` / `TaskState`

Carry the sets of already-granted serialised `PolicyKey` strings for the current session and current autonomous task respectively.

---

## Registry (`registry.py`)

### `ActionRegistry`

Dict-based store keyed by `Action.name`. Methods: `register(action)`, `get(name)`, `get_all()`, `get_by_capability(capability)`.

### `CapabilityRegistry`

Owns one `ActionRegistry`. Methods: `register(pack)`, `get(name)`, `get_all()`, `actions` (property returning the `ActionRegistry`).

### Module-level singleton

```python
_default_registry = CapabilityRegistry()

def get_default_registry() -> CapabilityRegistry: ...
def register_capability(pack: CapabilityPack) -> None: ...
def get_all_capabilities() -> list[CapabilityPack]: ...
```

`__init__.py` calls `register_capability()` for all four packs at import time, making them available immediately.

---

## Browser capability (`browser/`)

### What it does

Headless browser automation via the `agent-browser` CLI binary. Provides navigation, DOM snapshots, screenshots, page clipping, and basic interaction (click, type, fill). Backs the `browser_capability` skill.

### `BrowserCapability` pack

- `bind(settings)` — late-binds settings and config. Accepts `**kwargs` so callers passing `mcp_manager=` don't get a `TypeError`.
- `health_check()` → delegates to `check_browser_health(config)`.
- `register_actions(registry)` — registers 9 actions (see table below).
- `get_policy()` → `build_browser_policy()`.
- `make_context(session, task) → BrowserActionContext` — builds the context object passed to each action.

### Actions

| Action name | Read/write | Approval |
|---|---|---|
| `browser.open` | Read | Never |
| `browser.snapshot` | Read | Never |
| `browser.screenshot` | Read | Never |
| `browser.clip_page` | Read | Never |
| `browser.clip_selection` | Read | Never |
| `browser.close` | Read | Never |
| `browser.click` | Write | Never (but Google domain check in action layer) |
| `browser.type` | Write | Never (but Google domain check) |
| `browser.fill` | Write | Never (but Google domain check) |

### Google domain enforcement

Write actions (`click`, `type`, `fill`) check `_is_google_domain(url)` against `ctx.open_sessions[session_id].current_url`. If on a Google domain and `approved=False`, they return a `StructuredFailure(reason="google_write_requires_approval")` without touching the browser. This is a second enforcement layer on top of the policy matrix's resource-scoped `ALWAYS_ASK` rule.

### `BrowserBinary`

Async subprocess wrapper. Builds argv from `CommandTemplate` dataclass (overridable). Validates placeholder values against `_UNSAFE_CHARS = {"\x00"}`. Uses `asyncio.create_subprocess_exec` with `asyncio.wait_for(proc.communicate(), timeout=command_timeout_seconds)`. On timeout, kills the process.

Output is parsed as JSON. If `--version` is called and the output is not JSON, it wraps the plain text in `{"version": stripped}`.

`BrowserCommandError` carries `argv`, `exit_code`, `stdout`, `stderr`, `reason`. The action layer converts this to `StructuredFailure`.

### `BrowserCapabilityConfig`

`binary_path`, `profile`, `clip_target`, `max_session_duration_seconds`, `command_timeout_seconds`, `enabled`. Built from `settings.browser` by `from_settings()`.

### Browser policy

Default: reads are `NEVER_ASK`. Writes (`click`, `type`, `fill`) are `NEVER_ASK` on non-Google URLs, `ALWAYS_ASK` on `https://*.google.com/*` (resource-scoped rule, higher specificity wins). Default for unmatched: `ALWAYS_ASK`.

---

## Vault capability (`vault/`)

### What it does

Local filesystem mirror for notes and web clips. Writes content to a configured root directory (typically an Obsidian vault). No external network calls — pure local I/O. Backs the `vault_capability` skill.

### `VaultCapability` pack

- `bind(settings)` — constructs `FilesystemMirror` if `vault.enabled` and `vault.path` are set; otherwise installs `NullMirror`.
- `health_check()` → `check_vault_health(config)`.
- `register_actions(registry)` — 3 actions.
- `get_policy()` → `build_vault_policy()` (all `NEVER_ASK`).
- `make_context(session, task) → VaultActionContext`.

### Actions

| Action name | Purpose |
|---|---|
| `vault.write_note` | Write markdown to `{root}/{notes_subdir}/{relative_path}` |
| `vault.write_clip` | Write clip to `{root}/{clips_subdir}/{YYYY}/{MM}/{slug}.md` |
| `vault.read_note` | Read from `{root}/{notes_subdir}/{relative_path}` |

### `FilesystemMirror`

**`write_note(relative_path, content, metadata)`:** Path safety via `_safe_relative_path()` (rejects `../` traversal). Creates parent dirs. If metadata is provided, renders YAML frontmatter via `yaml.safe_dump()`. Returns `WriteResult(success, path, failure, content=None)`.

**`write_clip(source_url, title, content, metadata)`:** Derives timestamp from `metadata["clipped_at"]` or current UTC time. Slugifies title for filename (`_slugify()` — Unicode normalise → ASCII → lowercase → collapse non-alnum to `-` → truncate to 80 chars). Writes to `{root}/{clips_subdir}/{YYYY}/{MM}/{slug}.md` with YAML frontmatter including `source_url`, `title`, `clipped_at`. No Kora attribution marker by spec.

**`read_note(relative_path)`:** Path safety check. Returns `WriteResult(content=text)` on success.

### `NullMirror`

All methods return `WriteResult(success=False, failure=StructuredFailure(reason="vault_disabled"))`. Used when vault is disabled or unconfigured.

### Vault policy

All three vault actions: `NEVER_ASK`. Default: `NEVER_ASK`. The vault is a local mirror — no user prompts for writes.

---

## Workspace capability (`workspace/`)

### What it does

Google Workspace (Gmail, Calendar, Drive, Docs, Tasks) via a configurable MCP server (defaults target `taylorwilsdon/google_workspace_mcp`). Provides 17 stable action names mapped to MCP tool names via `WorkspaceConfig.tool_map`. Backs the `workspace_capability` skill.

### `WorkspaceCapability` pack

- `bind(settings, mcp_manager)` — late-binds both dependencies.
- `health_check()` → `check_workspace_health(config, settings, mcp_manager)`.
- `register_actions(registry)` — 17 actions.
- `get_policy()` → `build_default_policy(account, read_only)`.
- `make_context(session, task) → WorkspaceActionContext`.

### Actions (17 total)

**Gmail (read — NEVER_ASK):** `gmail.search`, `gmail.get_message`

**Gmail (write — DENY by default for personal account):** `gmail.draft`, `gmail.send`

**Calendar (read — NEVER_ASK):** `calendar.list`, `calendar.get_event`

**Calendar (write — FIRST_PER_TASK):** `calendar.create_event`, `calendar.update_event`

**Calendar (delete — ALWAYS_ASK):** `calendar.delete_event`

**Drive (read — NEVER_ASK):** `drive.search`, `drive.get_file`

**Drive (write — FIRST_PER_TASK):** `drive.upload`

**Docs (read — NEVER_ASK):** `docs.read`

**Docs (write — FIRST_PER_TASK):** `docs.create`, `docs.update`

**Tasks (read — NEVER_ASK):** `tasks.list`

**Tasks (write — FIRST_PER_TASK):** `tasks.create`

### `_call_action()` dispatcher

Central coroutine shared by all 17 action functions:

1. Build `PolicyKey(capability, action, account, resource)`.
2. `policy.evaluate(key, session, task)` → `Decision`.
3. If `DENY` → return `StructuredFailure(reason="policy_denied")`.
4. If `requires_prompt` and `not approved` → return `StructuredFailure(reason="approval_required")`.
5. Resolve MCP tool name from `config.tool_map`.
6. Call `mcp_manager.call_tool(server_name, tool_name, args)`.
7. If `result.is_error` → `StructuredFailure(reason="mcp_error")`.
8. Return `result.structured_data` or `{"text": result.text}`.

On approval with `FIRST_PER_*` modes, the serialised `PolicyKey` is added to session/task grants.

### `WorkspaceConfig`

Pydantic model with:
- `mcp_server_name = "workspace"` — key in `settings.mcp.servers`
- `account = "personal"` — used for account-scoped policy rules
- `read_only = False` — if True, all write rules become `DENY`
- `default_calendar_id = "primary"`
- `provenance_marker = "[Created by Kora]"`
- `tool_map` — maps stable Kora action names to MCP tool names (17 entries)

### Provenance injection (`provenance.py`)

`inject_calendar_create_provenance(event_args, config)`: Deep-copies the args dict and:
1. Appends `config.provenance_marker` to `description` (or creates it).
2. Sets `extendedProperties.private[provenance_metadata_key] = provenance_metadata_value`.

Called only for `calendar.create_event`. Calendar edits and other writes have no provenance marker by spec.

### Workspace health check

`check_workspace_health()` returns degraded/unhealthy status at each failure tier:
1. `mcp_manager` is None → `UNCONFIGURED`
2. Server not in `settings.mcp.servers` → `UNCONFIGURED`
3. Server not in manager's state → `UNCONFIGURED`
4. Server not running → `DEGRADED` (lazy-start acceptable)
5. Tool discovery fails → `UNHEALTHY`
6. None of the expected tools found → `UNHEALTHY`
7. Some tools missing → `DEGRADED` with list of missing
8. All tools present → `OK`

### Workspace policy

Personal account defaults:
- Gmail send/draft: `DENY`
- Calendar writes: `FIRST_PER_TASK`
- Calendar delete: `ALWAYS_ASK`
- Drive/Docs write: `FIRST_PER_TASK`
- All reads: `NEVER_ASK`
- Default fallback: `ALWAYS_ASK`

If `read_only=True`, all write modes become `DENY`.

---

## Doctor capability (`doctor/`)

### Status: stub / work-in-progress

`DoctorCapability` is scaffolding only. `health_check()` returns `UNIMPLEMENTED`. `register_actions()` returns `None`. `checks.py` contains only a one-line docstring with no implementation. The module docstring references "Task 5" for future implementation.

---

## Module initialisation (`__init__.py`)

At import time, the four packs are registered into the module-level singleton:

```python
register_capability(WorkspaceCapability())
register_capability(BrowserCapability())
register_capability(VaultCapability())
register_capability(DoctorCapability())
```

This means importing `kora_v2.capabilities` is sufficient for the default registry to contain all four packs. The DI container later calls `pack.bind(settings, ...)` on each to inject runtime dependencies.

---

## Integration points

- The DI container (`kora_v2/core/di.py`) calls `pack.bind()` on all registered capabilities during startup.
- Capability health checks are exposed via the daemon's doctor endpoint.
- The supervisor dispatch path can execute dotted capability actions through `graph/capability_bridge.py`. The executor currently pulls browser capability tools into its research path; do not describe it as a general workspace/vault capability dispatcher.
- Skills (`kora_v2/skills/`) reference capability action names in their `tools` lists (e.g., `browser.open`, `vault.write_note`) so the supervisor LLM sees them as available tools when the relevant skill is active.
- The `WorkspaceActionContext` holds an `MCPManager` reference from `kora_v2/mcp/manager.py`.
- The `PolicyMatrix` uses `SessionState` and `TaskState` to track per-session/per-task grants — these are passed in by the caller, not stored globally.
