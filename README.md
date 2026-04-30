# Kora

A local-first Life OS agent for keeping a messy day believable.

> **Beta-stage local-first release — active development.**
> Kora now has a working Electron/React desktop GUI, a working Rich CLI, and a local daemon that powers both clients through the same REST/WebSocket runtime. It is generally ready for local use by developers and early users who are comfortable with setup, logs, and fast-moving code. APIs, file layouts, database schemas, and behavior can still change; packaged distribution and long-term stability hardening are ongoing.

## What Kora is

Kora's product center is now **Life OS**: a local-first agent for day-to-day life management when planning, initiation, transitions, sensory load, social load, low energy, anxiety, burnout, or executive dysfunction make ordinary productivity tools unrealistic.

The core loop is:

```text
Plan Today -> Confirm Reality -> Repair The Day -> Bridge Tomorrow
```

Kora is designed to maintain a believable day plan, notice when reality diverges, make it easy for the user to correct Kora, and repair the rest of the day without shame. Coding, research, writing, browser, workspace, and vault work still exist as optional capability packs, but they are no longer the main product acceptance surface.

Current Life OS pillars:

1. **Believable day planning.** Kora creates versioned day plans from calendar entries, items, reminders, routines, and current load. Only one plan is active for a local day; older revisions stay queryable for repair proof.
2. **Reality ledger.** Medication, meals, focus blocks, reminders, quick notes, corrections, inferred events, and tool-generated life events are recorded in a durable Life Event Ledger.
3. **Repair engine.** Kora detects stale plans, partial/skipped/blocked reality, overload, and "I'm behind" reports, then proposes/apply safe private repairs such as shrinking tasks, adding buffers, deferring nonessential work, or creating a new plan revision.
4. **Life Load and support modes.** The Life Load Meter produces explainable load factors and can drive quiet, high-support, recovery, or Stabilization Mode behavior.
5. **Support profiles.** A baseline `general_life_management` profile is active by default. ADHD, anxiety, autism/sensory, low-energy, and burnout profiles are suggested or user-activated; active profiles change runtime decisions, not just wording.
6. **Context packs and bridges.** Kora can create context packs for admin/anxiety/sensory-heavy situations and Future Self Bridges that carry partial, skipped, blocked, or dropped items into tomorrow with first moves.
7. **Proactivity with restraint.** Every nudge candidate gets a durable send/defer/suppress/queue decision, including quiet/stabilization suppression and feedback such as "too much" or "wrong."
8. **Safety boundary.** Crisis language preempts normal planning, repair, and productivity flows and writes a durable safety-boundary record.

## Architecture at a glance

```
┌──────────────────────────────────────────────────────────┐
│  CLIENTS                                                 │
│  Electron/React desktop GUI - REST view-models + WS chat │
│  Rich CLI (python -m kora_v2.cli) - WebSocket + REST     │
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
│  Storage: memory root (canonical markdown + YAML),       │
│  projection.db (derived), operational.db                 │
└──────────────────────────────────────────────────────────┘
```

- **Supervisor graph** (`kora_v2/graph/`) — the main conversation loop, built on LangGraph.
- **Workers** (`kora_v2/agents/workers/`) — planner, executor, reviewer. Typed Pydantic I/O, shared harness, quality gates.
- **Filesystem-canonical memory** (`kora_v2/memory/`, `_KoraMemory/`) — markdown notes with YAML frontmatter are the source of truth. SQLite (`projection.db`) is a derived index for fast recall via FTS5 + local embeddings.
- **Life OS services** (`kora_v2/life/`, `kora_v2/support/`, `kora_v2/safety/`) — day plans, ledger, load meter, repair, proactivity policy, stabilization, context packs, future bridges, support profiles, trusted support exports, and crisis safety.
- **Desktop GUI** (`apps/desktop/`, `kora_v2/desktop/`) - Electron/React client with typed REST view-models for Life OS screens and global WebSocket chat.
- **Daemon + CLI** (`kora_v2/daemon/`, `kora_v2/cli/`) - FastAPI daemon bound to `127.0.0.1`, Rich terminal client over WebSocket.
- **MiniMax M2.7** is the primary LLM (205K context). Claude Code is available as a user-toggled delegate for deep planning and research.

## Quickstart

Requires Python 3.12+. On macOS, ensure your system sqlite3 is 3.38 or newer
(`sqlite3 --version`); macOS Ventura+ ships 3.39+ which is sufficient.

```bash
git clone <this-repo> kora && cd kora
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -c "import kora_v2; print('ok')"

cp .env.example .env
# Edit .env and set MINIMAX_API_KEY
```

Or use the automated bootstrap script (also prints non-Python prerequisites):

```bash
bash scripts/bootstrap_tooling.sh
```

Run the unit tests to confirm the install works:

```bash
.venv/bin/python -m pytest tests/unit -q
```

## Running Kora

Kora runs as a local daemon. You start the daemon once, then connect a client to it.

**Terminal 1 — start the daemon:**

```bash
.venv/bin/kora
# → "Kora V2 2.0.0a1 — daemon ready at 127.0.0.1:<port>"
```

The daemon auto-detaches, writes a lockfile at `data/kora.lock`, and logs to `data/logs/daemon.log`.

**Terminal 2 - open the desktop GUI:**

```bash
cd apps/desktop
npm install
npm run dev
```

Open `http://127.0.0.1:5173/` in a browser for renderer development, or run `npm run dev:all` from `apps/desktop/` to launch Vite and Electron together. The desktop GUI uses `/api/v1/desktop/*` REST view-model routes for Today, Calendar, Medication, Routines, Memory, Repair, Autonomous, Integrations, Settings, and Runtime, and keeps chat available globally through the same daemon WebSocket used by the CLI.

**Or use the terminal CLI:**

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
├── desktop/       # Desktop API service and view-model assembly
├── emotion/       # Two-tier PAD emotion assessment
├── graph/         # LangGraph supervisor graph
├── life/          # Life OS services: day plans, ledger, load, repair, bridge
├── llm/           # LLM provider abstractions (MiniMax, Claude Code)
├── mcp/           # MCP (Model Context Protocol) client integration
├── memory/        # Filesystem store, projection DB, retrieval, embeddings
├── quality/       # Quality measurement and sampling
├── routing/       # Intent and verb routing helpers
├── runtime/       # Turn runner, checkpointer, inspector, stores
├── safety/        # Crisis safety boundary
├── skills/        # YAML skill definitions (code, emotional, autonomous...)
├── support/       # Life OS support profiles and runtime modules
└── tools/         # recall(), filesystem, life-management tools, registry

tests/             # unit / integration / acceptance / fixtures
apps/desktop/      # Electron + React desktop GUI
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

# Desktop API contract
.venv/bin/python -m pytest tests/unit/test_desktop_api.py -q

# Desktop GUI checks
cd apps/desktop
npm run typecheck
npm run lint
npm run test
npm run build:renderer
```

## Acceptance focus

Life OS acceptance is the product-center gate. A convincing pass requires durable proof for the Plan Today -> Confirm Reality -> Repair The Day -> Bridge Tomorrow loop: DB rows, domain events, tool calls, day-plan revisions, repair actions, nudge decisions, support mode state, context packs, future bridges, support profiles/signals, and safety-boundary records.

The old coding/research/writing acceptance checks are treated as optional capability-pack health. They should not make the Life OS core look red unless they break core life-management behavior.

## Security & constraints

- **Local-first.** The daemon binds to `127.0.0.1` only. No remote exposure without an explicit tunnel you set up yourself.
- **Secrets via `.env`.** Never commit credentials. `.env` is gitignored; use `.env.example` as the template.
- **Auth token** at `data/.api_token` is generated per-session and required for all daemon API calls.
- **Not clinical care.** Kora can support planning, routines, grounding, and user-authored support workflows. It is not a clinician, therapist, emergency responder, diagnostic system, or replacement for professional care.
- **No deception.** The system is designed to be reliable and non-manipulative; contribution that violates this is out of scope.

## License

MIT. See `pyproject.toml` for package metadata.
