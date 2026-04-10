"""LangGraph conversation checkpointer backed by SQLite.

Uses MemorySaver as the default (in-process) checkpointer.  When the
``langgraph-checkpoint-sqlite`` package is installed, ``make_checkpointer``
upgrades to ``AsyncSqliteSaver`` for crash-safe persistence.

Install the optional package for durable checkpoints::

    pip install langgraph-checkpoint-sqlite
"""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


async def make_checkpointer(db_path: Path):
    """Create a LangGraph checkpoint saver for conversation persistence.

    Attempts to use ``AsyncSqliteSaver`` (from langgraph-checkpoint-sqlite)
    for durable, restart-safe conversation state.  Falls back to the
    built-in ``MemorySaver`` when the optional package is not installed.

    The returned saver's connection is opened and its schema is set up.
    The caller must call ``await close_checkpointer(saver)`` on shutdown.

    Args:
        db_path: Path to the SQLite database file.  The file (and parent
            directory) are created automatically by the saver.

    Returns:
        A checkpoint saver compatible with LangGraph's ``compile()``
        ``checkpointer=`` parameter.
    """
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        db_path.parent.mkdir(parents=True, exist_ok=True)
        # from_conn_string returns an async context manager (AsyncIterator).
        # We enter it manually and keep the saver alive for the daemon's
        # lifetime.  The caller must call close_checkpointer() on shutdown.
        cm = AsyncSqliteSaver.from_conn_string(str(db_path))
        saver = await cm.__aenter__()
        # Stash the context manager so we can exit it later.
        saver._cm = cm  # type: ignore[attr-defined]
        await saver.setup()
        log.info("checkpointer_initialized", backend="sqlite", db_path=str(db_path))
        return saver
    except ImportError:
        log.warning(
            "checkpointer_fallback_to_memory",
            reason="langgraph-checkpoint-sqlite not installed",
            hint="pip install langgraph-checkpoint-sqlite for durable checkpoints",
        )
        from langgraph.checkpoint.memory import MemorySaver

        saver = MemorySaver()
        log.info("checkpointer_initialized", backend="memory")
        return saver
    except Exception:
        log.warning("checkpointer_sqlite_failed_fallback_to_memory", exc_info=True)
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()


async def close_checkpointer(saver) -> None:
    """Cleanly close the checkpointer's underlying connection."""
    cm = getattr(saver, "_cm", None)
    if cm is not None:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:
            log.debug("checkpointer_close_failed", exc_info=True)
