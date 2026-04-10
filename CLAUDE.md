# Kora V2 Development Guide

This repo’s active implementation target is `kora_v2`. Use this guide as the repo-level development contract for code and docs work.

## Active Runtime

- Package: `kora_v2`
- Console entrypoint: `kora`
- Entry function: `kora_v2.daemon.launcher:main`
- Tests are rooted in `tests/`
- Historical V1 material is archive/reference only

Do not build new work against `kora/`, old lockfiles, or legacy PRD paths.

## Current Repo Status

The current repo is a live `kora_v2` rearchitecture worktree with implemented foundations across:

- Supervisor graph and LangGraph state orchestration
- Memory subsystem: filesystem store, projection DB, hybrid retrieval, signal scanner, write pipeline
- Worker harnesses: planner, executor, reviewer
- Runtime infrastructure: daemon launcher, server, runtime kernel, background worker, auth relay, SQLite checkpointer
- Conversation systems: prompts, session manager, emotion assessment, compaction, Rich CLI
- Supporting packages: `runtime/`, `security/`, `mcp/`, `adhd/`, `agents/workers/`, `tools/verb_resolver.py`

The planning/docs surface has also moved:

- Current PRD: `Documentation/plans/rearchitecture/PRD/`
- Current implementation specs: `Documentation/plans/rearchitecture/specs/`
- Current review outputs: `Documentation/plans/rearchitecture/reviews/`
- Active working plans: `docs/superpowers/plans/`

## Repo Layout

```text
Kora_Max/
├── kora_v2/
│   ├── adhd/
│   ├── agents/
│   │   └── workers/
│   ├── cli/
│   ├── context/
│   ├── core/
│   ├── daemon/
│   ├── emotion/
│   ├── graph/
│   ├── llm/
│   ├── mcp/
│   ├── memory/
│   ├── quality/
│   ├── routing/
│   ├── runtime/
│   ├── security/
│   ├── skills/
│   └── tools/
├── tests/
│   ├── acceptance/
│   ├── fixtures/
│   ├── integration/
│   └── unit/
├── Documentation/plans/rearchitecture/
│   ├── PRD/
│   ├── specs/
│   ├── reviews/
│   ├── research-outputs/
│   └── AGENTS.md
├── docs/superpowers/plans/
├── _KoraMemory/
└── data/
```

## Development Rules

1. `kora_v2` is the only active runtime surface.
2. Prefer code truth over stale phase labels in old docs.
3. Use the current PRD/spec directories, not the removed `.../PRD/` root.
4. When changing implementation, verify the corresponding tests and docs move with it.
5. Do not add new references to deleted legacy runtime paths.
6. Treat `Documentation/archive/` as history, not implementation guidance.

## Workflow Expectations

- Inspect the current code before proposing or making structural changes.
- For small or concrete requests, implement directly after inspection.
- For larger changes, align code, tests, and the nearest relevant spec/doc in the same pass.
- If repo docs disagree with code, update the docs to match the current repo unless the code is clearly wrong.

## Architecture Notes

### Core runtime
- `kora_v2/core/di.py` wires the container.
- `kora_v2/graph/supervisor.py` owns the main conversation graph.
- `kora_v2/runtime/turn_runner.py` is the turn execution contract.
- `kora_v2/runtime/kernel.py` owns runtime state and background services.

### Memory
- Filesystem memory is canonical.
- Projection DB is derived.
- `recall()` is the fast memory path.
- User-model data belongs under `_KoraMemory/User Model/`.

### Daemon and CLI
- `kora_v2/daemon/server.py` exposes REST/WebSocket runtime APIs.
- `kora_v2/cli/app.py` is the Rich CLI client.
- Lockfile/token/logs live under `data/`.

### Docs
- PRD: `Documentation/plans/rearchitecture/PRD/`
- Specs: `Documentation/plans/rearchitecture/specs/`
- Reviews/signoffs: `Documentation/plans/rearchitecture/reviews/`

## Commands

```bash
# tests
.venv/bin/python -m pytest tests/ -v

# focused tests
.venv/bin/python -m pytest tests/unit/test_db.py -q

# lint
.venv/bin/ruff check kora_v2/ tests/

# import sanity
.venv/bin/python -c "import kora_v2"
```

## Security and Constraints

- Bind services to `127.0.0.1` only
- Never hardcode API keys or tokens
- Use `.env` / `.env.local` for secrets
- Never implement deception, manipulation, contempt, malice, or unreliable behavior
- Keep the project local-first
