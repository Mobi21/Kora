"""Process-level runtime context for the autonomous pipeline step fn.

The orchestration dispatcher builds a fresh :class:`StepContext` for
every step invocation, but that context deliberately does not carry
the DI container — step functions are meant to be self-contained so
the dispatcher can stay agnostic of Kora's wider object graph.

The 12-node autonomous graph, on the other hand, was built against a
full container: the planner/executor/reviewer workers, the operational
DB path, the event emitter, and the settings bundle all live there.
Rather than reshape the dispatcher surface mid-slice to plumb a
container through, we expose a tiny process-level registry here that
the :class:`OrchestrationEngine` populates at ``start()`` time and the
autonomous step function reads per-call.

This is the only place the autonomous migration leaks non-step-fn
state into a module global. It is intentionally not exposed on the
engine or the container — callers must import these helpers by path
so the dependency is visible in review.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AutonomousRuntimeContext:
    """Container handle + operational DB path for the autonomous step fn."""

    container: Any
    db_path: Path


_context: AutonomousRuntimeContext | None = None


def set_autonomous_context(
    *, container: Any, db_path: Path
) -> None:
    """Install the process-level autonomous runtime context.

    Called by :meth:`OrchestrationEngine.start` once the engine's
    container is wired. Safe to call repeatedly — later calls replace
    earlier ones (tests reset the context between cases).
    """
    global _context
    _context = AutonomousRuntimeContext(container=container, db_path=db_path)
    log.debug(
        "autonomous_runtime_context_set",
        db_path=str(db_path),
        has_container=container is not None,
    )


def get_autonomous_context() -> AutonomousRuntimeContext | None:
    """Return the currently-installed context, or ``None`` if unset."""
    return _context


def clear_autonomous_context() -> None:
    """Drop the installed context. Used by tests between cases."""
    global _context
    _context = None
