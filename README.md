# Kora

An ADHD-aware, local-first AI companion.

> ⚠️ **Pre-alpha — active development.**
> APIs, file layouts, database schemas, and behavior change from week to week. There are no releases, no stability guarantees, and no support. Credentials, conversations, and memory all live on your machine, but the runtime itself is not considered safe for daily use. **Install at your own risk.**

## What Kora is

Kora's core identity is **ADHD-awareness as a design premise**, not a feature set. The memory system, the planner, the pacing, the proactive surfacing of things you forgot — they're shaped from the ground up around how an ADHD brain actually works. Four capability pillars serve that core:

1. **Subagent-first architecture.** A LangGraph supervisor delegates to specialized workers (memory, planner, executor, reviewer). The supervisor decides what to do; workers execute. No classifier, no intent router — the LLM self-regulates based on prompt guidance.
2. **Quality-first design.** Every agent output flows through typed Pydantic schemas and quality gates. A dedicated Reviewer worker catches problems before the user sees them. Structural enforcement, not aspirational.
3. **Autonomous execution.** Kora can take multi-step tasks (research → plan → implement → review → ship) and run them end-to-end over hours, with 30-minute reflection checkpoints. You can chat concurrently while autonomous work continues in the background.
4. **Life-management infrastructure.** The concrete layer that makes ADHD-awareness real: routines, medication reminders, finance, diet, time awareness, and proactive surfacing of pending commitments. Implemented as an on-demand life worker plus a proactive background agent that watches for things you forgot.

Those four pillars read as ADHD-aware once they're put together: Kora passively infers energy and focus from conversation signals (no self-reporting — that's itself an executive-function task), compensates for working-memory drift by surfacing due-soon items, breaks down tasks into 2-minute micro-steps when you're stuck, learns a personal time-estimation correction factor, and tracks a dedicated ADHD profile in the user model. None of that is bolted on; it's how the agents are built.

## Architecture at a glance

```
┌──────────────────────────────────────────────────────────┐
│  CLIENT                                                  │
│  Rich CLI (python -m kora_v2.cli) — WebSocket + REST     │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────┴───────────────────────────────────┐
│  DAEMON (FastAPI, 127.0.0.1)                             │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  SUPERVISOR (LangGraph)                            │  │
│  │  Understand → Decide → Delegate → Synthesize       │  │
│  │                                                    │  │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌─────────┐      │  │
│  │  │ MEMORY │ │PLANNER │ │EXECUTOR│ │REVIEWER │ ...  │  │
│  │  └────────┘ └────────┘ └────────┘ └─────────┘      │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  Infrastructure: LLM provider, tool registry, MCP        │
│  manager, quality gates, DI container, event bus         │
│                                                          │
│  Storage: _KoraMemory/ (canonical markdown + YAML),      │
│  projection.db (derived), items.db, operational.db       │
└──────────────────────────────────────────────────────────┘
```

- **Supervisor graph** (`kora_v2/graph/`) — the main conversation loop, built on LangGraph.
- **Workers** (`kora_v2/agents/workers/`) — memory, planner, executor, reviewer. Typed Pydantic I/O, shared harness, quality gates.
- **Filesystem-canonical memory** (`kora_v2/memory/`, `_KoraMemory/`) — markdown notes with YAML frontmatter are the source of truth. SQLite (`projection.db`) is a derived index for fast recall via FTS5 + local embeddings.
- **Daemon + CLI** (`kora_v2/daemon/`, `kora_v2/cli/`) — FastAPI daemon bound to `127.0.0.1`, Rich terminal client over WebSocket.
- **MiniMax M2.7** is the primary LLM (205K context). Claude Code is available as a user-toggled delegate for deep planning and research.

## Quickstart

Requires Python 3.12+.

```bash
git clone <this-repo> kora && cd kora
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

cp .env.example .env
# Edit .env and set MINIMAX_API_KEY
```

Run the tests to confirm the install works:

```bash
.venv/bin/python -m pytest tests/ -v
```

## Running Kora

Kora runs as a local daemon. You start the daemon once, then connect a client to it.

**Terminal 1 — start the daemon:**

```bash
.venv/bin/kora
# → "Kora V2 2.0.0a1 — daemon ready at 127.0.0.1:<port>"
```

The daemon auto-detaches, writes a lockfile at `data/kora.lock`, and logs to `data/logs/daemon.log`.

**Terminal 2 — chat with her:**

```bash
.venv/bin/python -m kora_v2.cli
```

The CLI auto-discovers the daemon port from the lockfile and the auth token from `data/.api_token`, then drops you into an interactive Rich prompt. Type `/help` for available slash commands.

**Lifecycle:**

```bash
.venv/bin/kora status   # check if the daemon is running
.venv/bin/kora stop     # request shutdown
```

## How a turn flows

1. You send a message over the WebSocket.
2. The supervisor assembles the system prompt (frozen prefix + dynamic suffix) and decides what to do — direct reply, fast recall, complex recall, plan+execute, or autonomous.
3. If memory is needed, the fast-path `recall()` tool runs (~0.3s, embedding + FTS5). More ambiguous needs go to the Memory Worker.
4. Complex requests hand off to the Planner → Executor → Reviewer workers, with the supervisor synthesizing the final response.
5. The response passes through a quality gate before streaming back to you. Post-turn, background extraction updates memory and emotion state.

## Repo layout

```text
kora_v2/
├── agents/        # Worker harness, middleware, quality gates
│   └── workers/   # Memory, planner, executor, reviewer
├── autonomous/    # Multi-step autonomous execution, checkpoints, budgets
├── cli/           # Rich WebSocket chat client (python -m kora_v2.cli)
├── context/       # Working-memory loader, compaction, budget
├── core/          # DI container, settings, logging, events, DB helpers
├── daemon/        # FastAPI server, launcher, lockfile, session mgmt
├── emotion/       # Two-tier PAD emotion assessment
├── graph/         # LangGraph supervisor graph
├── life/          # Life-management (routines, etc.)
├── llm/           # LLM provider abstractions (MiniMax, Claude Code)
├── mcp/           # MCP (Model Context Protocol) client integration
├── memory/        # Filesystem store, projection DB, retrieval, embeddings
├── quality/       # Quality measurement and sampling
├── routing/       # Intent and verb routing helpers
├── runtime/       # Turn runner, checkpointer, inspector, stores
├── skills/        # YAML skill definitions (code, emotional, autonomous...)
└── tools/         # recall(), filesystem, life-management tools, registry

tests/             # unit / integration / acceptance / fixtures
_KoraMemory/       # canonical memory (markdown + YAML) — gitignored, created at runtime
data/              # lockfile, token, logs, databases — gitignored, created at runtime
```

## Development commands

```bash
# All tests
.venv/bin/python -m pytest tests/ -v

# Focused tests
.venv/bin/python -m pytest tests/unit/test_db.py -q

# Lint
.venv/bin/ruff check kora_v2/ tests/

# Import sanity
.venv/bin/python -c "import kora_v2"
```

## Security & constraints

- **Local-first.** The daemon binds to `127.0.0.1` only. No remote exposure without an explicit tunnel you set up yourself.
- **Secrets via `.env`.** Never commit credentials. `.env` is gitignored; use `.env.example` as the template.
- **Auth token** at `data/.api_token` is generated per-session and required for all daemon API calls.
- **No deception.** The system is designed to be reliable and non-manipulative; contribution that violates this is out of scope.

## License

MIT. See `pyproject.toml` for package metadata.
