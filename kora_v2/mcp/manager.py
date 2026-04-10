"""MCP server lifecycle manager for Kora V2.

Manages MCP server startup (lazy), shutdown, tool discovery, and
crash recovery with exponential backoff.

Real subprocess-backed stdio MCP:

* ``asyncio.create_subprocess_exec`` spawns the configured command.
* A background reader task dispatches JSON-RPC responses to waiting
  callers via ``asyncio.Future`` keyed by request id.
* ``ensure_server_running`` performs the JSON-RPC handshake and caches
  the server's ``tools/list``.
* ``call_tool`` sends ``tools/call`` and returns the joined text of
  the ``content[*].text`` items in the result.

Failures (missing binary, timeout, crash) set the server to FAILED
and surface as :class:`MCPServerUnavailableError` on the next call;
they never crash the daemon.
"""

from __future__ import annotations

import asyncio
import json
from enum import StrEnum
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict

from kora_v2.core.exceptions import KoraError
from kora_v2.core.settings import MCPSettings

logger = structlog.get_logger()

# ── Backoff schedule (seconds) ────────────────────────────────────────────
_BACKOFF_DELAYS: list[float] = [0, 2.0, 4.0]
_MAX_RESTART_ATTEMPTS = len(_BACKOFF_DELAYS)

# ── JSON-RPC handshake constants ──────────────────────────────────────────
_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "kora", "version": "2.0"}
_DEFAULT_CALL_TIMEOUT = 60.0  # seconds for a single tool call


# ── Exceptions ────────────────────────────────────────────────────────────

class MCPError(KoraError):
    """Base exception for MCP-related errors."""


class MCPServerNotFoundError(MCPError):
    """Requested MCP server is not configured."""


class MCPServerUnavailableError(MCPError):
    """MCP server is in FAILED state and cannot be started."""


class MCPToolNotFoundError(MCPError):
    """Requested tool does not exist on the target MCP server."""


# ── Models ────────────────────────────────────────────────────────────────

class MCPServerState(StrEnum):
    """Lifecycle state of an MCP server."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"


class MCPServerInfo(BaseModel):
    """Snapshot of a single MCP server's status.

    Runtime-only fields (the subprocess handle, reader task, and
    pending response futures) are attached as plain attributes after
    construction — they are not part of the Pydantic schema.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    state: MCPServerState = MCPServerState.STOPPED
    pid: int | None = None
    tools: list[str] = []
    tool_schemas: dict[str, dict[str, Any]] = {}
    start_count: int = 0
    last_error: str | None = None

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        # Non-model runtime state (set as instance attributes — not schema fields)
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()


# ── Manager ───────────────────────────────────────────────────────────────

class MCPManager:
    """Manages MCP server lifecycle and tool discovery.

    Lazy startup: servers start on first use.  Crash recovery uses a
    3-attempt exponential backoff (immediate, 2 s, 4 s) before marking
    the server FAILED.
    """

    def __init__(self, settings: MCPSettings) -> None:
        self._settings = settings
        self._servers: dict[str, MCPServerInfo] = {}

        # Pre-populate from config
        for name, cfg in settings.servers.items():
            if cfg.enabled:
                self._servers[name] = MCPServerInfo(name=name)

    # ── Helpers ───────────────────────────────────────────────────────

    def _require_server(self, name: str) -> MCPServerInfo:
        """Return server info or raise MCPServerNotFoundError."""
        info = self._servers.get(name)
        if info is None:
            raise MCPServerNotFoundError(
                f"MCP server '{name}' is not configured",
                details={"server": name},
            )
        return info

    def _next_request_id(self, info: MCPServerInfo) -> int:
        info._next_id += 1
        return info._next_id

    async def _send_message(
        self,
        info: MCPServerInfo,
        message: dict[str, Any],
    ) -> None:
        """Serialize and write a JSON-RPC message to the server stdin."""
        if info._process is None or info._process.stdin is None:
            raise MCPServerUnavailableError(
                f"MCP server '{info.name}' has no writable stdin",
                details={"server": info.name},
            )
        payload = (json.dumps(message) + "\n").encode("utf-8")
        info._process.stdin.write(payload)
        await info._process.stdin.drain()

    async def _send_request(
        self,
        info: MCPServerInfo,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = _DEFAULT_CALL_TIMEOUT,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and await its matching response."""
        request_id = self._next_request_id(info)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        info._pending[request_id] = future

        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        try:
            await self._send_message(info, message)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            info._pending.pop(request_id, None)

    async def _send_notification(
        self,
        info: MCPServerInfo,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        await self._send_message(info, message)

    async def _reader_loop(self, info: MCPServerInfo) -> None:
        """Continuously read JSON-RPC messages and route to pending futures."""
        if info._process is None or info._process.stdout is None:
            return

        stdout = info._process.stdout
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    # EOF — process closed its stdout.
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "mcp.reader.bad_json",
                        server=info.name,
                        error=str(exc),
                        raw=line[:200],
                    )
                    continue

                msg_id = message.get("id")
                if msg_id is None:
                    # Notification from server — nothing to route.
                    continue
                future = info._pending.get(msg_id)
                if future is None or future.done():
                    continue
                if "error" in message:
                    err = message["error"]
                    err_msg = (
                        err.get("message", "unknown MCP error")
                        if isinstance(err, dict)
                        else str(err)
                    )
                    future.set_exception(
                        MCPError(
                            f"MCP server '{info.name}' returned error: {err_msg}",
                            details={"server": info.name, "error": err},
                        )
                    )
                else:
                    future.set_result(message.get("result") or {})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "mcp.reader.crash",
                server=info.name,
                error=str(exc),
            )
        finally:
            # Fail any still-pending requests.
            for fut in list(info._pending.values()):
                if not fut.done():
                    fut.set_exception(
                        MCPServerUnavailableError(
                            f"MCP server '{info.name}' closed its stdout",
                            details={"server": info.name},
                        )
                    )
            info._pending.clear()

    def _server_command(self, name: str) -> tuple[str, list[str], dict[str, str]]:
        cfg = self._settings.servers[name]
        return cfg.command, list(cfg.args), dict(cfg.env)

    # ── Public API ────────────────────────────────────────────────────

    async def ensure_server_running(self, name: str) -> None:
        """Ensure a server is RUNNING, starting it lazily if needed."""
        info = self._require_server(name)

        if info.state == MCPServerState.RUNNING:
            return

        if info.state == MCPServerState.FAILED:
            raise MCPServerUnavailableError(
                f"MCP server '{name}' is in FAILED state after max restart attempts",
                details={"server": name, "last_error": info.last_error},
            )

        async with info._lock:
            # Re-check under lock.
            if info.state == MCPServerState.RUNNING:
                return
            if info.state == MCPServerState.FAILED:
                raise MCPServerUnavailableError(
                    f"MCP server '{name}' is in FAILED state",
                    details={"server": name, "last_error": info.last_error},
                )
            await self.start_server(name)

    async def start_server(self, name: str) -> None:
        """Spawn the MCP subprocess and perform the JSON-RPC handshake."""
        info = self._require_server(name)
        info.state = MCPServerState.STARTING
        info.start_count += 1
        info.last_error = None
        info.tools = []
        info.tool_schemas = {}

        logger.info("mcp.server.starting", server=name, attempt=info.start_count)

        command, args, env_overrides = self._server_command(name)

        # Build the env: inherit + overrides.
        import os
        env = {**os.environ, **env_overrides} if env_overrides else None

        try:
            process = await asyncio.create_subprocess_exec(
                command,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            info.state = MCPServerState.FAILED
            info.last_error = f"command not found: {command}"
            logger.warning(
                "mcp.server.command_not_found",
                server=name,
                command=command,
                error=str(exc),
            )
            return
        except Exception as exc:  # noqa: BLE001
            info.state = MCPServerState.FAILED
            info.last_error = f"spawn failed: {exc}"
            logger.warning(
                "mcp.server.spawn_failed",
                server=name,
                error=str(exc),
            )
            return

        info._process = process
        info.pid = process.pid
        info._reader_task = asyncio.create_task(
            self._reader_loop(info), name=f"mcp_reader_{name}"
        )

        try:
            startup_timeout = float(self._settings.startup_timeout)
            # 1. initialize handshake
            await self._send_request(
                info,
                method="initialize",
                params={
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": _CLIENT_INFO,
                },
                timeout=startup_timeout,
            )
            # 2. initialized notification
            await self._send_notification(info, "notifications/initialized")

            # 3. tools/list
            list_result = await self._send_request(
                info,
                method="tools/list",
                params={},
                timeout=startup_timeout,
            )
            tools = list_result.get("tools") or []
            info.tools = [t.get("name", "") for t in tools if isinstance(t, dict)]
            info.tool_schemas = {
                t.get("name", ""): t
                for t in tools
                if isinstance(t, dict) and t.get("name")
            }
        except TimeoutError:
            info.state = MCPServerState.FAILED
            info.last_error = "startup timeout"
            logger.warning("mcp.server.startup_timeout", server=name)
            await self._terminate_process(info)
            return
        except Exception as exc:  # noqa: BLE001
            info.state = MCPServerState.FAILED
            info.last_error = f"handshake failed: {exc}"
            logger.warning(
                "mcp.server.handshake_failed",
                server=name,
                error=str(exc),
            )
            await self._terminate_process(info)
            return

        info.state = MCPServerState.RUNNING
        logger.info(
            "mcp.server.running",
            server=name,
            pid=info.pid,
            tool_count=len(info.tools),
        )

    async def _terminate_process(self, info: MCPServerInfo) -> None:
        """Terminate the subprocess and cancel the reader task."""
        if info._reader_task is not None and not info._reader_task.done():
            info._reader_task.cancel()
            try:
                await info._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        info._reader_task = None

        if info._process is not None:
            try:
                if info._process.returncode is None:
                    info._process.terminate()
                    try:
                        await asyncio.wait_for(info._process.wait(), timeout=3.0)
                    except TimeoutError:
                        info._process.kill()
                        try:
                            await asyncio.wait_for(info._process.wait(), timeout=2.0)
                        except TimeoutError:
                            pass
            except ProcessLookupError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mcp.server.terminate_error",
                    server=info.name,
                    error=str(exc),
                )
        info._process = None
        info.pid = None

    async def stop_server(self, name: str) -> None:
        """Stop an MCP server."""
        info = self._require_server(name)

        if info.state in (MCPServerState.STOPPED, MCPServerState.FAILED):
            await self._terminate_process(info)
            info.state = MCPServerState.STOPPED
            info.tools = []
            info.tool_schemas = {}
            return

        logger.info("mcp.server.stopping", server=name)
        await self._terminate_process(info)
        info.state = MCPServerState.STOPPED
        info.tools = []
        info.tool_schemas = {}

    async def stop_all(self) -> None:
        """Stop every managed MCP server."""
        for name in list(self._servers):
            try:
                await self.stop_server(name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mcp.server.stop_error",
                    server=name,
                    error=str(exc),
                )

    async def discover_tools(self, name: str) -> list[str]:
        """Return tool names advertised by a server."""
        info = self._require_server(name)
        if info.state != MCPServerState.RUNNING:
            raise MCPServerUnavailableError(
                f"MCP server '{name}' is not running; cannot discover tools.",
                details={"server": name, "state": info.state},
            )
        return list(info.tools)

    async def call_tool(
        self,
        server: str,
        tool: str,
        args: dict[str, Any] | None = None,
    ) -> Any:
        """Invoke a tool on an MCP server and return the text result.

        Ensures the server is running first (lazy start). Returns the
        concatenated text from ``result.content[*]``. If the server is
        FAILED this raises :class:`MCPServerUnavailableError`.
        """
        await self.ensure_server_running(server)

        info = self._require_server(server)
        if info.state != MCPServerState.RUNNING:
            raise MCPServerUnavailableError(
                f"MCP server '{server}' is not running",
                details={"server": server, "state": info.state},
            )

        if tool not in info.tools:
            raise MCPToolNotFoundError(
                f"Tool '{tool}' not found on server '{server}'",
                details={
                    "server": server,
                    "tool": tool,
                    "available": info.tools,
                },
            )

        logger.info("mcp.tool.call", server=server, tool=tool)
        result = await self._send_request(
            info,
            method="tools/call",
            params={"name": tool, "arguments": args or {}},
            timeout=_DEFAULT_CALL_TIMEOUT,
        )

        # Extract text content from result.
        content = result.get("content") or []
        if not isinstance(content, list):
            return result
        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
        if text_parts:
            return "\n".join(text_parts)
        return result

    async def health_check(self, name: str) -> bool:
        """Return True if the server is RUNNING."""
        info = self._servers.get(name)
        if info is None:
            return False
        if info.state != MCPServerState.RUNNING:
            return False
        # Also check the process is still alive.
        if info._process is not None and info._process.returncode is not None:
            info.state = MCPServerState.STOPPED
            return False
        return True

    def get_server_info(self, name: str) -> MCPServerInfo | None:
        """Return a snapshot of server state, or None if unknown."""
        return self._servers.get(name)

    def list_servers(self) -> list[MCPServerInfo]:
        """Return info for every configured server."""
        return list(self._servers.values())

    async def _restart_with_backoff(self, name: str) -> bool:
        """Attempt to restart a server with exponential backoff."""
        info = self._require_server(name)

        for attempt, delay in enumerate(_BACKOFF_DELAYS):
            if delay > 0:
                logger.info(
                    "mcp.server.backoff",
                    server=name,
                    attempt=attempt + 1,
                    delay=delay,
                )
                await asyncio.sleep(delay)

            info.state = MCPServerState.STOPPED
            info.last_error = None
            try:
                await self.start_server(name)
                if info.state == MCPServerState.RUNNING:
                    logger.info(
                        "mcp.server.restart.success",
                        server=name,
                        attempt=attempt + 1,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                info.last_error = str(exc)
                logger.warning(
                    "mcp.server.restart.failed",
                    server=name,
                    attempt=attempt + 1,
                    error=str(exc),
                )

        # All attempts exhausted
        info.state = MCPServerState.FAILED
        logger.error(
            "mcp.server.restart.exhausted",
            server=name,
            last_error=info.last_error,
        )
        return False
