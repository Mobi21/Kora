"""Unit tests for KoraCLI slash command implementations.

Tests the command methods with mocked REST calls. No actual HTTP traffic —
``_rest_get`` and ``_rest_post`` are patched to return canned data.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kora_v2.cli.app import KoraCLI

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_cli(**kwargs) -> KoraCLI:
    """Create a KoraCLI with resolved port/token pre-set."""
    cli = KoraCLI(host="127.0.0.1", port=9999, token="test-tok")
    cli._resolved_port = 9999
    cli._resolved_token = "test-tok"
    return cli


# ── /status ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_displays_table():
    """``/status`` renders a Rich Table when daemon returns data."""
    cli = _make_cli()
    printed: list[object] = []
    cli._console.print = lambda *a, **kw: printed.append(a[0] if a else "")

    with patch.object(cli, "_rest_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "status": "running",
            "version": "0.2.0",
            "session_active": False,
            "turn_count": 3,
        }
        await cli._cmd_status()

    assert mock_get.await_args_list[0].args == ("/api/v1/status",)
    assert [call.args[0] for call in mock_get.await_args_list] == [
        "/api/v1/status",
        "/api/v1/inspect/tools",
        "/api/v1/orchestration/status",
    ]
    # Should have printed a Rich Table (not a plain string error)
    from rich.table import Table
    assert any(isinstance(p, Table) for p in printed)


@pytest.mark.asyncio
async def test_status_handles_unreachable():
    """``/status`` prints error when daemon is unreachable."""
    cli = _make_cli()
    printed: list[str] = []
    cli._console.print = lambda *a, **kw: printed.append(str(a[0]) if a else "")

    with patch.object(cli, "_rest_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        await cli._cmd_status()

    assert any("Cannot reach daemon" in p for p in printed)


# ── /memory ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_displays_results():
    """``/memory`` shows panels for each returned memory result."""
    cli = _make_cli()
    printed: list[object] = []
    cli._console.print = lambda *a, **kw: printed.append(a[0] if a else "")

    with patch.object(cli, "_rest_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "results": [
                {"content": "User likes coffee", "source": "user_model"},
                {"content": "Morning routine note", "source": "episodic"},
            ],
        }
        await cli._cmd_memory("coffee")

    mock_get.assert_awaited_once_with("/api/v1/memory/recall?q=coffee")
    from rich.panel import Panel
    panels = [p for p in printed if isinstance(p, Panel)]
    assert len(panels) == 2


@pytest.mark.asyncio
async def test_memory_handles_empty():
    """``/memory`` prints a dim message when no results come back."""
    cli = _make_cli()
    printed: list[str] = []
    cli._console.print = lambda *a, **kw: printed.append(str(a[0]) if a else "")

    with patch.object(cli, "_rest_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"results": []}
        await cli._cmd_memory()

    assert any("No matching memories" in p for p in printed)


@pytest.mark.asyncio
async def test_memory_default_query():
    """``/memory`` with no args uses 'recent' as the default query."""
    cli = _make_cli()
    cli._console.print = lambda *a, **kw: None

    with patch.object(cli, "_rest_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"results": []}
        await cli._cmd_memory("")

    mock_get.assert_awaited_once_with("/api/v1/memory/recall?q=recent")


# ── /plan ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_shows_active():
    """``/plan`` renders panels for active autonomous loops."""
    cli = _make_cli()
    printed: list[object] = []
    cli._console.print = lambda *a, **kw: printed.append(a[0] if a else "")

    with patch.object(cli, "_rest_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "loops": {
                "abc12345-dead-beef": {
                    "goal": "Research ADHD tools",
                    "status": "executing",
                    "steps_completed": 3,
                    "steps_pending": 2,
                },
            },
            "count": 1,
        }
        await cli._cmd_plan()

    mock_get.assert_awaited_once_with("/api/v1/inspect/autonomous")
    from rich.panel import Panel
    panels = [p for p in printed if isinstance(p, Panel)]
    assert len(panels) == 1


@pytest.mark.asyncio
async def test_plan_handles_no_plans():
    """``/plan`` prints a dim message when no loops are active."""
    cli = _make_cli()
    printed: list[str] = []
    cli._console.print = lambda *a, **kw: printed.append(str(a[0]) if a else "")

    with patch.object(cli, "_rest_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"loops": {}, "count": 0}
        await cli._cmd_plan()

    assert any("No active autonomous plans" in p for p in printed)


# ── /stop ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_sends_shutdown():
    """``/stop`` POSTs to shutdown endpoint and sets _running = False."""
    cli = _make_cli()
    cli._running = True
    printed: list[str] = []
    cli._console.print = lambda *a, **kw: printed.append(str(a[0]) if a else "")

    # Mock httpx at the module level where it's imported inside the method
    mock_response = AsyncMock()
    mock_response.status_code = 200

    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_instance):
        await cli._cmd_stop()

    assert cli._running is False
    assert any("shutting down" in p for p in printed)


@pytest.mark.asyncio
async def test_stop_handles_failure():
    """``/stop`` shows error when shutdown request fails."""
    cli = _make_cli()
    cli._running = True
    printed: list[str] = []
    cli._console.print = lambda *a, **kw: printed.append(str(a[0]) if a else "")

    mock_response = AsyncMock()
    mock_response.status_code = 500

    mock_client_instance = AsyncMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client_instance):
        await cli._cmd_stop()

    # _running should remain True on failure
    assert cli._running is True
    assert any("Shutdown failed" in p for p in printed)


# ── /compact ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compact_success():
    """``/compact`` shows success on 200 response."""
    cli = _make_cli()
    printed: list[str] = []
    cli._console.print = lambda *a, **kw: printed.append(str(a[0]) if a else "")

    with patch.object(cli, "_rest_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"status": "compaction_requested"}
        await cli._cmd_compact()

    mock_post.assert_awaited_once_with("/api/v1/compact")
    assert any("compaction_requested" in p for p in printed)


@pytest.mark.asyncio
async def test_compact_failure():
    """``/compact`` shows error when POST fails."""
    cli = _make_cli()
    printed: list[str] = []
    cli._console.print = lambda *a, **kw: printed.append(str(a[0]) if a else "")

    with patch.object(cli, "_rest_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = None
        await cli._cmd_compact()

    assert any("Compaction failed" in p for p in printed)


# ── /permissions ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_permissions_displays_table():
    """``/permissions`` renders a Rich Table when grants exist."""
    cli = _make_cli()
    printed: list[object] = []
    cli._console.print = lambda *a, **kw: printed.append(a[0] if a else "")

    with patch.object(cli, "_rest_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "grants": [
                {
                    "tool_name": "file_write",
                    "scope": "session",
                    "decision": "allow",
                    "granted_at": "2026-04-06T10:00:00Z",
                },
                {
                    "tool_name": "shell_exec",
                    "scope": "always",
                    "decision": "allow",
                    "granted_at": "2026-04-05T08:30:00Z",
                },
            ],
        }
        await cli._cmd_permissions()

    mock_get.assert_awaited_once_with("/api/v1/permissions")
    from rich.table import Table
    assert any(isinstance(p, Table) for p in printed)


@pytest.mark.asyncio
async def test_permissions_handles_empty():
    """``/permissions`` prints dim message when no grants exist."""
    cli = _make_cli()
    printed: list[str] = []
    cli._console.print = lambda *a, **kw: printed.append(str(a[0]) if a else "")

    with patch.object(cli, "_rest_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"grants": []}
        await cli._cmd_permissions()

    assert any("No permission grants" in p for p in printed)


# ── _handle_command dispatch ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_command_dispatches_status():
    """``_handle_command('status', '')`` calls ``_cmd_status``."""
    cli = _make_cli()
    cli._cmd_status = AsyncMock()
    result = await cli._handle_command("status", "")
    assert result is True
    cli._cmd_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_command_dispatches_stop():
    """``_handle_command('stop', '')`` calls ``_cmd_stop`` and returns False."""
    cli = _make_cli()
    cli._cmd_stop = AsyncMock()
    result = await cli._handle_command("stop", "")
    assert result is False
    cli._cmd_stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_command_dispatches_memory_with_args():
    """``_handle_command('memory', 'coffee')`` passes args through."""
    cli = _make_cli()
    cli._cmd_memory = AsyncMock()
    result = await cli._handle_command("memory", "coffee")
    assert result is True
    cli._cmd_memory.assert_awaited_once_with("coffee")


@pytest.mark.asyncio
async def test_handle_command_dispatches_plan():
    """``_handle_command('plan', '')`` calls ``_cmd_plan``."""
    cli = _make_cli()
    cli._cmd_plan = AsyncMock()
    result = await cli._handle_command("plan", "")
    assert result is True
    cli._cmd_plan.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_command_dispatches_compact():
    """``_handle_command('compact', '')`` calls ``_cmd_compact``."""
    cli = _make_cli()
    cli._cmd_compact = AsyncMock()
    result = await cli._handle_command("compact", "")
    assert result is True
    cli._cmd_compact.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_command_dispatches_permissions():
    """``_handle_command('permissions', '')`` calls ``_cmd_permissions``."""
    cli = _make_cli()
    cli._cmd_permissions = AsyncMock()
    result = await cli._handle_command("permissions", "")
    assert result is True
    cli._cmd_permissions.assert_awaited_once()
