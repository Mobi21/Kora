"""Unit tests for the stdio-subprocess MCPManager.

These tests exercise the public surface without spawning real MCP
servers. A FileNotFoundError path is covered by pointing the manager
at a command that cannot exist on the filesystem.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kora_v2.core.settings import MCPServerConfig, MCPSettings
from kora_v2.mcp.manager import (
    MCPManager,
    MCPServerNotFoundError,
    MCPServerState,
    MCPServerUnavailableError,
)


def _settings(servers: dict[str, MCPServerConfig] | None = None) -> MCPSettings:
    return MCPSettings(servers=servers or {}, startup_timeout=5)


class TestMCPManagerInitialization:
    """Construction and configuration."""

    def test_empty_servers_dict_no_crash(self) -> None:
        manager = MCPManager(_settings({}))
        assert manager.list_servers() == []

    def test_prepopulates_enabled_servers(self) -> None:
        settings = _settings({
            "enabled_one": MCPServerConfig(command="echo", args=["hi"], enabled=True),
            "disabled_one": MCPServerConfig(command="echo", args=["bye"], enabled=False),
        })
        manager = MCPManager(settings)
        names = {s.name for s in manager.list_servers()}
        assert names == {"enabled_one"}

    def test_list_servers_returns_configured(self) -> None:
        settings = _settings({
            "brave_search": MCPServerConfig(command="brave-mcp", enabled=True),
            "fetch": MCPServerConfig(command="fetch-mcp", enabled=True),
        })
        manager = MCPManager(settings)
        servers = manager.list_servers()
        assert {s.name for s in servers} == {"brave_search", "fetch"}
        # All start in STOPPED state.
        for info in servers:
            assert info.state == MCPServerState.STOPPED


class TestEnsureServerRunning:
    """ensure_server_running dispatch + state checks."""

    @pytest.mark.asyncio
    async def test_unknown_server_raises(self) -> None:
        manager = MCPManager(_settings({}))
        with pytest.raises(MCPServerNotFoundError):
            await manager.ensure_server_running("nope")

    @pytest.mark.asyncio
    async def test_failed_server_raises_unavailable(self) -> None:
        settings = _settings({
            "bad": MCPServerConfig(command="no-such-binary-kora-test"),
        })
        manager = MCPManager(settings)
        info = manager.get_server_info("bad")
        assert info is not None
        # Simulate a prior failed start.
        info.state = MCPServerState.FAILED
        info.last_error = "previously failed"
        with pytest.raises(MCPServerUnavailableError):
            await manager.ensure_server_running("bad")

    @pytest.mark.asyncio
    async def test_running_server_is_noop(self) -> None:
        settings = _settings({
            "live": MCPServerConfig(command="echo"),
        })
        manager = MCPManager(settings)
        info = manager.get_server_info("live")
        assert info is not None
        info.state = MCPServerState.RUNNING
        # Should return without calling start_server.
        with patch.object(manager, "start_server", new=AsyncMock()) as mock_start:
            await manager.ensure_server_running("live")
            mock_start.assert_not_called()


class TestStartServerFailures:
    """start_server should mark FAILED (not raise) on spawn errors."""

    @pytest.mark.asyncio
    async def test_file_not_found_marks_failed(self) -> None:
        """A missing binary path should set state=FAILED without crashing."""
        settings = _settings({
            "ghost": MCPServerConfig(
                command="/definitely/not/a/real/path/ghost-mcp",
            ),
        })
        manager = MCPManager(settings)

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("no such file"),
        ):
            await manager.start_server("ghost")

        info = manager.get_server_info("ghost")
        assert info is not None
        assert info.state == MCPServerState.FAILED
        assert info.last_error is not None
        assert "command not found" in info.last_error

    @pytest.mark.asyncio
    async def test_spawn_exception_marks_failed(self) -> None:
        settings = _settings({
            "busted": MCPServerConfig(command="broken-mcp"),
        })
        manager = MCPManager(settings)

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("permission denied"),
        ):
            await manager.start_server("busted")

        info = manager.get_server_info("busted")
        assert info is not None
        assert info.state == MCPServerState.FAILED
        assert info.last_error is not None
        assert "spawn failed" in info.last_error


class TestCallToolOnFailedServer:
    """call_tool must refuse to run against a FAILED server."""

    @pytest.mark.asyncio
    async def test_call_tool_on_failed_raises(self) -> None:
        settings = _settings({
            "gone": MCPServerConfig(command="gone-mcp"),
        })
        manager = MCPManager(settings)
        info = manager.get_server_info("gone")
        assert info is not None
        info.state = MCPServerState.FAILED
        info.last_error = "boom"
        with pytest.raises(MCPServerUnavailableError):
            await manager.call_tool("gone", "whatever", {})

    @pytest.mark.asyncio
    async def test_call_tool_unknown_server(self) -> None:
        manager = MCPManager(_settings({}))
        with pytest.raises(MCPServerNotFoundError):
            await manager.call_tool("missing", "any", {})


class TestHealthCheck:
    """health_check reports RUNNING state truthfully."""

    @pytest.mark.asyncio
    async def test_unknown_returns_false(self) -> None:
        manager = MCPManager(_settings({}))
        assert await manager.health_check("nope") is False

    @pytest.mark.asyncio
    async def test_stopped_returns_false(self) -> None:
        settings = _settings({
            "svc": MCPServerConfig(command="svc-mcp"),
        })
        manager = MCPManager(settings)
        assert await manager.health_check("svc") is False
