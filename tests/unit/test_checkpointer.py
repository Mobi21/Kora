"""Unit tests for kora_v2.runtime.checkpointer -- make_checkpointer factory."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kora_v2.runtime.checkpointer import close_checkpointer, make_checkpointer


@pytest.mark.asyncio
async def test_make_checkpointer_returns_memory_saver_when_sqlite_unavailable(
    tmp_path: Path,
) -> None:
    """When langgraph-checkpoint-sqlite is not installed, falls back to MemorySaver."""
    db_path = tmp_path / "test_checkpoint.db"

    import builtins

    real_import = builtins.__import__

    def _mock_import(name, *args, **kwargs):
        if "sqlite" in name:
            raise ImportError("no sqlite")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_mock_import):
        saver = await make_checkpointer(db_path)

    from langgraph.checkpoint.memory import MemorySaver

    assert isinstance(saver, MemorySaver)


@pytest.mark.asyncio
async def test_make_checkpointer_uses_real_sqlite(tmp_path: Path) -> None:
    """With langgraph-checkpoint-sqlite installed, returns AsyncSqliteSaver."""
    db_path = tmp_path / "test_checkpoint.db"

    saver = await make_checkpointer(db_path)

    assert "Sqlite" in type(saver).__name__
    assert db_path.exists()

    await close_checkpointer(saver)


@pytest.mark.asyncio
async def test_make_checkpointer_creates_parent_dirs(tmp_path: Path) -> None:
    """Parent directories are created if they don't exist."""
    nested_path = tmp_path / "deep" / "nested" / "checkpoint.db"

    saver = await make_checkpointer(nested_path)

    assert nested_path.parent.exists()

    await close_checkpointer(saver)


@pytest.mark.asyncio
async def test_close_checkpointer_noop_on_memory_saver() -> None:
    """close_checkpointer should not fail on a MemorySaver (no _cm attr)."""
    from langgraph.checkpoint.memory import MemorySaver

    saver = MemorySaver()
    await close_checkpointer(saver)  # should not raise
