# Kora V2 — Agent Reference

> Repo-local working guide for AI agents. Use this file for fast orientation. Use `CLAUDE.md` for fuller workflow and architecture notes.

## Active Codepath

- The only live runtime package is `kora_v2/`.
- The CLI entrypoint is `kora = "kora_v2.daemon.launcher:main"` from `pyproject.toml`.
- Historical V1 material belongs in archive locations only. Do not treat archive docs as the current runtime.

## Current Repo Shape

```text
kora_v2/
  adhd/             # ADHD profile helpers
  agents/           # Harness, worker models, worker implementations
  cli/              # Rich CLI client
  context/          # Budgeting and working-memory helpers
  core/             # Settings, DI, DB, events, shared models
  daemon/           # Launcher, server, session manager, background worker
  emotion/          # Fast and LLM emotion assessment
  graph/            # Supervisor graph, prompts, dispatch
  llm/              # Provider abstraction and MiniMax implementation
  mcp/              # MCP manager
  memory/           # Filesystem store, projection DB, retrieval, write pipeline
  quality/          # Tier 1 quality collection
  routing/          # Routing helpers
  runtime/          # Turn runner, kernel, checkpointer, inspector, stores
  security/         # Injection and auth-related code
  skills/           # YAML skill definitions and loader
  tools/            # Tool registry, truncation, recall, verb resolver
tests/
  acceptance/       # Scenario-style tests
  fixtures/         # Shared test helpers/data
  integration/      # Integration and spike tests
  unit/             # Unit tests
Documentation/plans/rearchitecture/
  PRD/              # Current source-of-truth PRD sections
  specs/            # Focused implementation specs
  reviews/          # Phase reviews and sign-offs
  research-outputs/ # Source research used by the rearchitecture
  AGENTS.md         # Review and sign-off protocol
docs/superpowers/plans/
  # Implementation working plans used during active development
_KoraMemory/
  # Filesystem-canonical memory data
data/
  # Runtime databases, token, lockfile, logs
```

## Working Assumptions

- Python: 3.12+
- Package manager/build: `setuptools` via `pyproject.toml`
- LLM: MiniMax via Anthropic-compatible endpoint
- Memory stack: filesystem canonical store + SQLite projection DB + sqlite-vec
- Logging: `structlog`
- Async by default
- Pydantic models are the main contract boundary

## Current Development Reality

- The repo is mid-rearchitecture, but the active implementation base is already the `kora_v2` stack.
- The PRD lives at `Documentation/plans/rearchitecture/PRD/`.
- Phase-specific implementation specs now live under `Documentation/plans/rearchitecture/specs/`.
- If a code path or doc still references `kora/` as the active runtime, treat that as stale unless it is explicitly archived.

## Testing

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/ruff check kora_v2/ tests/
.venv/bin/python -c "import kora_v2"
```

Use focused test runs when changing a small surface. Do not assume the whole repo is clean unless you ran it.

## Rules For Agents

- Develop against `kora_v2` only.
- Prefer the current PRD and spec docs over older planning material.
- Read code before making assumptions about phase completion.
- Do not reintroduce legacy `kora` imports, paths, lockfiles, or runtime assumptions.
- Keep archive/history docs in `Documentation/archive/` as reference only; do not treat them as implementation targets.
- All servers bind to `127.0.0.1` only.
- Never hardcode secrets.

## Reference Docs

- Repo guide: `CLAUDE.md`
- Review protocol: `Documentation/plans/rearchitecture/AGENTS.md`
- Current PRD: `Documentation/plans/rearchitecture/PRD/`
- Current specs: `Documentation/plans/rearchitecture/specs/`
