"""Unit tests for HarnessServer graceful shutdown behaviour.

Verifies that:
- cmd_stop_server() sets _running = False
- _handle_client with cmd="stop" sets the shutdown event rather than
  calling loop.stop()
- No asyncio.get_event_loop().stop() is ever called during shutdown
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Adjust sys.path so we can import from the acceptance package without a full
# install (worktree has no editable install of tests/).
_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.acceptance._harness_server import HarnessServer


class TestCmdStopServer:
    """cmd_stop_server() should flip _running to False."""

    def test_initial_running_state_is_false(self) -> None:
        server = HarnessServer()
        assert server._running is False

    @pytest.mark.asyncio
    async def test_cmd_stop_server_sets_running_false(self) -> None:
        server = HarnessServer()
        server._running = True  # Simulate a started server
        result = await server.cmd_stop_server()
        assert server._running is False
        assert result == {"stopped": True}

    @pytest.mark.asyncio
    async def test_cmd_stop_server_idempotent(self) -> None:
        """Calling stop twice should not raise."""
        server = HarnessServer()
        server._running = True
        await server.cmd_stop_server()
        result = await server.cmd_stop_server()
        assert result == {"stopped": True}
        assert server._running is False


class TestHandleClientShutdown:
    """_handle_client with cmd='stop' should set the shutdown event, not loop.stop()."""

    @pytest.mark.asyncio
    async def test_stop_cmd_sets_shutdown_event(self) -> None:
        """After handling 'stop', _shutdown_event.set() is called."""
        server = HarnessServer()
        server._running = True  # will be flipped to False by cmd_stop_server

        # Create a real asyncio.Event so we can observe it
        shutdown_event = asyncio.Event()
        server._shutdown_event = shutdown_event

        # Build fake reader/writer
        stop_request = json.dumps({"cmd": "stop"}).encode() + b"\n"
        reader = AsyncMock(spec=asyncio.StreamReader)
        reader.readline = AsyncMock(return_value=stop_request)

        writer = MagicMock(spec=asyncio.StreamWriter)
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        await server._handle_client(reader, writer)

        # The shutdown event must have been set
        assert shutdown_event.is_set(), (
            "_shutdown_event.set() was not called after 'stop' command"
        )
        # _running must have been set to False by cmd_stop_server
        assert server._running is False

    @pytest.mark.asyncio
    async def test_stop_cmd_does_not_call_loop_stop(self) -> None:
        """asyncio.get_event_loop().stop() must NOT be called."""
        server = HarnessServer()
        server._running = True

        shutdown_event = asyncio.Event()
        server._shutdown_event = shutdown_event

        stop_request = json.dumps({"cmd": "stop"}).encode() + b"\n"
        reader = AsyncMock(spec=asyncio.StreamReader)
        reader.readline = AsyncMock(return_value=stop_request)

        writer = MagicMock(spec=asyncio.StreamWriter)
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        # Patch loop.stop to detect if it ever gets called
        loop = asyncio.get_event_loop()
        original_stop = loop.stop
        stop_called = []

        def patched_stop() -> None:  # type: ignore[misc]
            stop_called.append(True)
            original_stop()

        loop.stop = patched_stop  # type: ignore[method-assign]
        try:
            await server._handle_client(reader, writer)
        finally:
            loop.stop = original_stop  # type: ignore[method-assign]

        assert not stop_called, (
            "asyncio.get_event_loop().stop() was called — this crashes asyncio.run()"
        )

    @pytest.mark.asyncio
    async def test_non_stop_cmd_does_not_set_shutdown_event(self) -> None:
        """A normal 'ping' command should not touch the shutdown event."""
        server = HarnessServer()
        server._running = True
        server._kora_session_id = "test-session-123"

        shutdown_event = asyncio.Event()
        server._shutdown_event = shutdown_event

        ping_request = json.dumps({"cmd": "ping"}).encode() + b"\n"
        reader = AsyncMock(spec=asyncio.StreamReader)
        reader.readline = AsyncMock(return_value=ping_request)

        writer = MagicMock(spec=asyncio.StreamWriter)
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        await server._handle_client(reader, writer)

        # Server still running, shutdown event NOT set
        assert server._running is True
        assert not shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_shutdown_event_none_does_not_raise(self) -> None:
        """If _shutdown_event is None (pre-run), stop cmd should not raise."""
        server = HarnessServer()
        server._running = True
        # _shutdown_event remains None (server.run() not yet called)

        stop_request = json.dumps({"cmd": "stop"}).encode() + b"\n"
        reader = AsyncMock(spec=asyncio.StreamReader)
        reader.readline = AsyncMock(return_value=stop_request)

        writer = MagicMock(spec=asyncio.StreamWriter)
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        # Should complete without raising AttributeError
        await server._handle_client(reader, writer)
        assert server._running is False

    @pytest.mark.asyncio
    async def test_broken_pipe_does_not_propagate(self) -> None:
        """BrokenPipeError on write should be silently absorbed."""
        server = HarnessServer()
        server._running = True
        server._kora_session_id = "sess"

        shutdown_event = asyncio.Event()
        server._shutdown_event = shutdown_event

        ping_request = json.dumps({"cmd": "ping"}).encode() + b"\n"
        reader = AsyncMock(spec=asyncio.StreamReader)
        reader.readline = AsyncMock(return_value=ping_request)

        writer = MagicMock(spec=asyncio.StreamWriter)
        writer.write = MagicMock(side_effect=BrokenPipeError("pipe broken"))
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        # Should not raise — BrokenPipeError is absorbed
        await server._handle_client(reader, writer)
