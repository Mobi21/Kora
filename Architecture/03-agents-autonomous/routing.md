# Routing

The `kora_v2/routing/` directory is an empty placeholder. It was created as part of the repo scaffold but contains no Python files, no `__init__.py`, and no implementation.

```
kora_v2/routing/
├── (empty — no files)
```

The CLAUDE.md README describes routing as "routing helpers (thin)". In practice, routing logic lives elsewhere:

- **Supervisor graph routing:** handled by `kora_v2/graph/supervisor.py` (out of scope for this cluster).
- **Autonomous execution routing:** `route_next_node()` in `kora_v2/autonomous/graph.py` — see [autonomous.md](autonomous.md).
- **Worker dispatch routing:** `container.resolve_worker(name)` in the DI container (`kora_v2/core/di.py`).
- **Verb-based tool routing:** `kora_v2/tools/verb_resolver.py` (in the tools layer, not this cluster).

There is nothing to document in `kora_v2/routing/` itself. If routing helpers are added in a future phase, they should be documented here.
