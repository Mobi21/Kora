# Skills System

The skills system is a YAML-driven tool-gating layer that controls which tool names the supervisor LLM sees in a given context. It is purely configuration — no code execution logic lives in skills. A skill declares a set of tool names and optional guidance text; the `SkillLoader` provides them to the supervisor, which decides what to offer to the LLM on each turn.

Skills are intentionally separate from capabilities. A skill is a configuration-time declaration of intent and tool visibility; a capability is a runtime enforcement boundary with policy and structured failure handling. A skill can reference capability action names (`browser.open`) or raw tool names (`write_file`) interchangeably.

---

## Files in this module

| File | Purpose |
|---|---|
| [`kora_v2/skills/__init__.py`](../../kora_v2/skills/__init__.py) | Package marker (empty) |
| [`kora_v2/skills/loader.py`](../../kora_v2/skills/loader.py) | `SkillLoader`, `Skill` model |
| `kora_v2/skills/*.yaml` | 14 skill definition files |

---

## YAML skill schema

Each YAML file maps to the `Skill` Pydantic model:

```yaml
name: string          # unique identifier; defaults to filename stem
display_name: string  # human-readable label (optional)
tools: [string, ...]  # tool names available when skill is active
discovery_tools: [string, ...]  # subset used for "what can I do?" probing
agent: string | null  # on-demand agent name this skill activates (optional)
guidance: string      # freeform markdown injected into the system prompt
```

`tools` + `discovery_tools` are additive — `get_active_tools()` returns their union. `discovery_tools` is intended for the smaller set of tools that are relevant when the supervisor is determining what it can do, without exposing every write action.

There is no `activation` field in the `Skill` model even though some YAML files include `activation: always` — the loader ignores unknown fields (Pydantic's default for the model as configured).

---

## `SkillLoader`

### Instantiation

```python
loader = SkillLoader(skills_dir=Path("kora_v2/skills/"))
# or use the default (same directory as loader.py):
loader = SkillLoader()
loader.load_all()
```

### Loading

`load_all()` globs `*.yaml` in the skills directory, sorted alphabetically. Each file is loaded via `load_skill(path)`:

```python
def load_skill(self, path: Path) -> Skill:
    data = yaml.safe_load(open(path)) or {}
    skill = Skill(
        name=data.get("name", path.stem),
        display_name=data.get("display_name", ""),
        tools=data.get("tools", []),
        discovery_tools=data.get("discovery_tools", []),
        guidance=data.get("guidance", ""),
        agent=data.get("agent"),
    )
    self._skills[skill.name] = skill
    return skill
```

Failures during `load_all()` are caught and logged (non-fatal). Failures during `load_skill()` raise.

### Tool gating: `get_active_tools(active_skills)`

Returns the union of `tools + discovery_tools` for all named active skills. Preserves order, deduplicates:

```python
tools = loader.get_active_tools(["web_research", "life_management", "file_creation"])
# returns: ["search_web", "fetch_url", "log_medication", ..., "write_file", ...]
```

Unknown skill names are warned but do not raise.

### Other lookups

| Method | Returns |
|---|---|
| `get_skill(name)` | `Skill | None` |
| `get_all_skills()` | `list[Skill]` |
| `get_skill_for_agent(agent_name)` | Finds skill where `skill.agent == agent_name` |
| `get_guidance(skill_name)` | `str` (guidance text or empty string) |

---

## Skill definitions

### `autonomous_review`

**Tools:** `read_file`, `list_directory`  
**Purpose:** Activates filesystem inspection tools for the reviewer agent when verifying files created during autonomous execution. Guidance emphasizes: read → verify → report, never claim success without checking.

### `browser_capability`

**Tools:** `browser.open`, `browser.snapshot`, `browser.screenshot`, `browser.clip_page`, `browser.clip_selection`, `browser.close`, `browser.click`, `browser.type`, `browser.fill`  
**Discovery tools:** `browser.open`, `browser.clip_page`  
**Purpose:** Exposes the full browser capability action set. Guidance covers read-vs-write distinction, Google-write restrictions, and the "fallback after MCP failure" pattern (acknowledge failure, then use `browser.open`).

### `calendar`

**Tools:** `create_calendar_entry`, `query_calendar`, `update_calendar_entry`, `delete_calendar_entry`, `sync_google_calendar`  
**Purpose:** Unified timeline tools for Kora's internal calendar store. Guidance covers ADHD-aware scheduling (buffer auto-insertion), ISO 8601 datetime handling, recurring entry management, and the update_plan workflow for adjustments.

### `code_work`

**Tools:** `read_file`, `list_directory`, `write_file`  
**Purpose:** Multi-file development context. Guidance instructs reading before writing and using `dispatch_worker` for complex tasks.

### `emotional_support`

**Tools:** (none)  
**Purpose:** Tool-less mode. Activating this skill injects guidance on presence-first, solution-second emotional support. Kora's tone rules are encoded here: warmth without performance, honesty without cruelty.

### `file_creation`

**Tools:** `write_file`, `create_directory`, `read_file`, `list_directory`, `file_exists`  
**Purpose:** Single-file and simple directory creation. Guidance covers the "enumerate before summarizing" rule — always call `list_directory` before producing any status summary.

### `life_management`

**Tools:** `log_medication`, `log_meal`, `create_reminder`, `query_reminders`, `query_medications`, `query_meals`, `query_focus_blocks`, `quick_note`, `query_quick_notes`, `start_focus_block`, `end_focus_block`, `log_expense`, `query_expenses`, `create_routine`, `list_routines`, `start_routine`, `advance_routine`, `routine_progress`
**Purpose:** ADHD life tracking (meds, meals, focus, quick capture, reminders, finance). The guidance section is the most detailed of any skill — it encodes exact rules for when to log vs. not log (the distinction between a past-tense event and a routine statement), the "never tell the user you logged something unless you actually called the tool" rule, and the "one rule above all" trust principle.

### `obsidian_vault`

**Tools:** (none)  
**Purpose:** Stub/redirect. Directs users to the `vault_capability` skill. Contains guidance explaining that Obsidian integration is now handled by `vault.*` actions.

### `planning`

**Tools:** `draft_plan`, `update_plan`, `day_briefing`, `create_item`, `complete_item`, `defer_item`, `query_items`, `life_summary`, `create_reminder`, `query_reminders`  
**Purpose:** Life planning and task management. Guidance covers the Kora-leads-planning philosophy, how `update_plan` is deterministic (requires identified IDs, not vague descriptions), how `draft_plan` output is for the supervisor not the user, and `goal_scope` values.

### `screen_control`

**Tools:** (none)  
**Purpose:** Placeholder for future screen-interaction tools. Guidance says to inform users this feature is not yet available.

### `self_improvement`

**Tools:** (none)  
**Purpose:** Placeholder for future self-observation tools. Guidance instructs using `recall` to check for existing observations before storing new ones.

### `vault_capability`

**Tools:** `vault.write_note`, `vault.write_clip`, `vault.read_note`  
**Discovery tools:** `vault.read_note`  
**Purpose:** Exposes vault capability actions. Guidance covers the note/clip/read use cases and the note that `vault_capability` replaces the older `obsidian_vault` stub.

### `web_research`

**Tools:** `search_web`, `fetch_url`  
**Purpose:** Web access. Guidance covers the `search → fetch` pattern and privacy rules (no passwords, payment info, ToS acceptance, or public posting on behalf of the user).

### `workspace_capability`

**Tools:** All 17 workspace actions (reads + writes)  
**Discovery tools:** `workspace.gmail.search`, `workspace.calendar.list`, `workspace.drive.search`, `workspace.tasks.list`  
**Purpose:** Exposes the full Google Workspace capability action set. Guidance covers read-vs-write approval split and the "fallback to browser.open after MCP failure" pattern.

---

## Skills vs. capabilities: the distinction

| Dimension | Skills | Capabilities |
|---|---|---|
| Layer | Configuration / gating | Runtime enforcement |
| Code | YAML + Pydantic loader | Python packs with handlers |
| Policy | None (declarative only) | `PolicyMatrix` with `ApprovalMode` |
| Failure | N/A | `StructuredFailure` returned |
| Runtime | Supervisor consults at turn start | Invoked when action is called |
| Approval | Not enforced by skills | Enforced by `_call_action()` |

Skills reference capability action names as strings (e.g., `browser.open`) — this is purely an identifier. The capability pack is what actually enforces whether the call is allowed and executes it. A skill being "active" makes the tool name visible to the LLM; a capability being configured makes the action executable.

---

## Runtime invocation

At the start of each turn, the supervisor queries the `SkillLoader` with the current active skill list to build the tool list for the LLM. Guidance text from active skills is injected into the system prompt. The LLM then names tools from that list in its response; the tool executor resolves the actual callable.

The `agent` field on a skill can be read by `get_skill_for_agent()` to find which skill would activate a given on-demand agent. Current code search shows the method exists, but no active caller outside the loader itself; do not treat it as proven routing behavior without rechecking call sites.

---

## Integration points

- `SkillLoader` is instantiated by the DI container at startup and shared across turns.
- The supervisor graph consults `loader.get_active_tools(active_skills)` when building the LLM's tool list.
- Guidance text is injected into the supervisor system prompt via `loader.get_guidance(skill_name)`.
- Skill names that reference capability action names (e.g., `browser.open`) are resolved by the tool registry at execution time.
- The `kora_v2/skills/` directory is the default — the loader uses `Path(__file__).parent` so it self-locates regardless of working directory.
