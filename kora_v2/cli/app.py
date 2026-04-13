"""Rich CLI client for Kora V2.

Connects to daemon via WebSocket. Streams responses with Rich formatting.
Handles reconnection with exponential backoff.

Usage:
    from kora_v2.cli.app import KoraCLI
    cli = KoraCLI()
    asyncio.run(cli.run())
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import structlog
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

log = structlog.get_logger(__name__)

# Reconnection constants
MAX_RECONNECT_ATTEMPTS = 5
HEARTBEAT_INTERVAL = 30  # seconds

# Default paths
_DEFAULT_LOCKFILE = Path("data/.lockfile")
_DEFAULT_TOKEN_PATH = Path("data/.api_token")


def calculate_backoff(attempt: int) -> int:
    """Calculate exponential backoff delay in seconds.

    attempt 0 → 1s, 1 → 2s, 2 → 4s, 3 → 8s, 4 → 16s
    """
    return 2**attempt


def parse_command(text: str) -> tuple[str | None, str]:
    """Parse slash commands from input.

    Returns (command_name, args) if slash command.
    Returns (None, original_text) if regular message.
    """
    text = text.strip()
    if not text.startswith("/"):
        return None, text

    parts = text[1:].split(None, 1)
    command = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    return command, args


def format_streaming_token(token: str) -> str:
    """Format a streaming token for display.

    Handles partial markdown gracefully — currently a passthrough,
    reserved for future rendering enhancements.
    """
    return token


class KoraCLI:
    """Rich CLI client for Kora V2.

    Auto-discovers daemon port from lockfile and API token from
    ``data/.api_token``. Connects via WebSocket with streaming display
    and exponential-backoff reconnection.

    Commands:
        /status  — system status
        /stop    — stop daemon
        /memory  — browse/search memories (placeholder)
        /plan    — show active plan (placeholder)
        /compact — force compaction (placeholder)
        /quit    — exit CLI
        /help    — show available commands
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int | None = None,
        token: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.token = token
        self._ws: Any = None
        self._session_id: str | None = None
        self._console = Console()
        self._running = False
        self._response_buffer = ""

        # Paths are attributes so tests can patch them easily
        self._lockfile_path: Path = _DEFAULT_LOCKFILE
        self._token_path: Path = _DEFAULT_TOKEN_PATH

    # ── Discovery ────────────────────────────────────────────────────────

    def _discover_port(self) -> int | None:
        """Auto-discover daemon port from lockfile.

        Reads the JSON lockfile written by the daemon launcher.
        Prefers ``api_port`` over ``port`` (legacy fallback).

        Returns:
            Port number, or None if lockfile is absent or unreadable.
        """
        if not self._lockfile_path.exists():
            return None
        try:
            data = json.loads(self._lockfile_path.read_text())
            return data.get("api_port") or data.get("port") or None
        except Exception:
            return None

    def _read_token(self) -> str | None:
        """Read API token from standard location.

        Returns:
            Token string (stripped), or None if file absent/unreadable.
        """
        if not self._token_path.exists():
            return None
        try:
            return self._token_path.read_text().strip() or None
        except Exception:
            return None

    # ── Connection ───────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Connect to daemon WebSocket.

        Auto-discovers port and token if not provided via constructor.
        Stores the resolved port/token on the instance for use by REST
        helpers.

        Returns:
            True if connected successfully, False otherwise.
        """
        import websockets  # type: ignore[import-untyped]

        # Auto-discover if not explicitly set
        port = self.port or self._discover_port()
        token = self.token or self._read_token()

        if not port:
            self._console.print("[red]Could not find daemon. Is it running?[/red]")
            return False

        if not token:
            self._console.print("[red]Could not find API token.[/red]")
            return False

        # Persist resolved values so REST helpers can use them
        self._resolved_port: int = port
        self._resolved_token: str = token

        uri = f"ws://{self.host}:{port}/api/v1/ws?token={token}"

        try:
            self._ws = await websockets.connect(uri)
            self._console.print("[green]Connected to Kora[/green]")
            log.info("cli_connected", host=self.host, port=port)
            return True
        except Exception as e:
            self._console.print(f"[red]Connection failed: {e}[/red]")
            log.warning("cli_connect_failed", error=str(e))
            return False

    async def reconnect(self) -> bool:
        """Reconnect with exponential backoff.

        Tries up to MAX_RECONNECT_ATTEMPTS times, waiting 1→2→4→8→16s
        between attempts.

        Returns:
            True if reconnected, False if all attempts exhausted.
        """
        for attempt in range(MAX_RECONNECT_ATTEMPTS):
            delay = calculate_backoff(attempt)
            self._console.print(
                f"[yellow]Reconnecting in {delay}s "
                f"(attempt {attempt + 1}/{MAX_RECONNECT_ATTEMPTS})...[/yellow]"
            )
            await asyncio.sleep(delay)

            if await self.connect():
                return True

        self._console.print("[red]Failed to reconnect after 5 attempts.[/red]")
        return False

    # ── Main Loop ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main interactive input loop.

        Displays welcome banner, connects to daemon, then enters the
        read-eval-print loop until the user quits or an unrecoverable
        error occurs.
        """
        self._running = True

        self._console.print(
            Panel(
                "[bold]Kora V2[/bold] — ADHD-Aware AI Companion\n"
                "Type a message or /help for commands.",
                title="Welcome",
                border_style="blue",
            )
        )

        if not await self.connect():
            if not await self.reconnect():
                return

        await self._check_first_run()

        try:
            while self._running:
                try:
                    user_input = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: Prompt.ask("[bold cyan]You[/bold cyan]")
                    )
                except (EOFError, KeyboardInterrupt):
                    self._running = False
                    break

                if not user_input.strip():
                    continue

                command, args = parse_command(user_input)

                if command is not None:
                    should_continue = await self._handle_command(command, args)
                    if not should_continue:
                        break
                    continue

                await self._send_message(user_input)

        finally:
            await self._cleanup()

    # ── First-run ────────────────────────────────────────────────────────

    async def _check_first_run(self) -> None:
        """Run first-run onboarding if no previous session exists.

        Phase 5: delegates most of the questions to the structured
        ``run_wizard`` flow (identity / ADHD / planning / life tracking).
        The MINIMAX_API_KEY check and Brave Search setup run first and
        then feed into Section 1 of the wizard.
        """
        bridges_dir = Path("_KoraMemory/.kora/bridges")
        if bridges_dir.exists() and list(bridges_dir.glob("*.md")):
            return  # Not first run

        # ── Step 1: API key (legacy — must run before wizard) ──────
        if not os.environ.get("MINIMAX_API_KEY"):
            self._console.print("[yellow]No API key found.[/yellow]")
            self._console.print("Kora needs a MiniMax API key to work.")
            try:
                key = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: Prompt.ask("Enter your MINIMAX_API_KEY")
                )
            except (EOFError, KeyboardInterrupt):
                return
            if key.strip():
                env_path = Path(".env")
                with env_path.open("a") as f:
                    f.write(f"\nMINIMAX_API_KEY={key.strip()}\n")
                os.environ["MINIMAX_API_KEY"] = key.strip()
                self._console.print("[green]API key saved to .env[/green]")

        # ── Step 2: Structured 4-section wizard ─────────────────────
        from kora_v2.cli.first_run import run_wizard

        memory_base = Path("_KoraMemory")
        result = await run_wizard(
            self._console, container=None, memory_base=memory_base
        )

        # ── Step 3: Optional Brave web search (folded in as Section 5)
        try:
            enable_search = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: Confirm.ask(
                    "Would you like to enable web search? (requires a Brave API key)",
                    default=False,
                ),
            )
        except (EOFError, KeyboardInterrupt):
            enable_search = False

        if enable_search:
            try:
                brave_key = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: Prompt.ask("Enter your BRAVE_API_KEY")
                )
            except (EOFError, KeyboardInterrupt):
                brave_key = ""
            if brave_key.strip():
                settings_path = Path("data/mcp_servers.json")
                settings_path.parent.mkdir(parents=True, exist_ok=True)
                config = {
                    "brave_search": {
                        "command": "npx",
                        "args": ["-y", "@anthropic/brave-search-mcp"],
                        "env": {"BRAVE_API_KEY": brave_key.strip()},
                        "enabled": True,
                    }
                }
                settings_path.write_text(json.dumps(config, indent=2))
                self._console.print("[green]Web search configured![/green]")

        # ── Send introduction ────────────────────────────────────────
        if result.name or result.use_case:
            intro = (
                f"Hi! I'm {result.name or 'the user'}. "
                f"I mainly want help with: {result.use_case or 'general tasks'}."
            )
            self._console.print("\n[dim]Sending introduction...[/dim]")
            await self._send_message(intro)
            self._console.print()

    # ── Messaging ────────────────────────────────────────────────────────

    async def _send_message(self, content: str) -> None:
        """Send a chat message and stream the response tokens.

        Handles token streaming, tool status updates, heartbeat pings,
        and error envelopes.  On connection failure, attempts reconnect
        and prompts user to resend.

        Args:
            content: The user's message text.
        """
        if self._ws is None:
            self._console.print("[red]Not connected.[/red]")
            return

        try:
            await self._ws.send(json.dumps({"type": "chat", "content": content}))

            self._response_buffer = ""
            self._console.print("[bold green]Kora[/bold green]: ", end="")

            while True:
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=120)
                    data = json.loads(raw)
                    msg_type = data.get("type", "")

                    if msg_type == "token":
                        token = data.get("content", "")
                        self._response_buffer += token
                        self._console.print(format_streaming_token(token), end="")

                    elif msg_type == "tool_start":
                        tool = data.get("content", "")
                        self._console.print(f"\n  [dim]→ using {tool}...[/dim]", end="")

                    elif msg_type == "tool_result":
                        self._console.print(" [dim]done[/dim]", end="")

                    elif msg_type == "response_complete":
                        self._console.print()  # Newline after streaming
                        break

                    elif msg_type == "error":
                        error = data.get("content", "Unknown error")
                        self._console.print(f"\n[red]Error: {error}[/red]")
                        break

                    elif msg_type == "ping":
                        # Respond to server heartbeat
                        await self._ws.send(json.dumps({"type": "pong"}))

                    elif msg_type == "auth_request":
                        tool = data.get("tool", "unknown")
                        args_data = data.get("args", {})
                        req_id = data.get("request_id", "")

                        self._console.print()  # newline after any streaming
                        args_preview = json.dumps(args_data, indent=2)
                        if len(args_preview) > 200:
                            args_preview = args_preview[:197] + "..."
                        self._console.print(
                            Panel(
                                f"Tool: [bold]{tool}[/bold]\nArgs: {args_preview}",
                                title="Permission Required",
                                border_style="yellow",
                            )
                        )

                        try:
                            choice = await asyncio.get_event_loop().run_in_executor(
                                None,
                                lambda: Prompt.ask(
                                    "Allow this action?",
                                    choices=["y", "n", "always"],
                                    default="y",
                                ),
                            )
                        except (EOFError, KeyboardInterrupt):
                            choice = "n"

                        approved = choice in ("y", "always")
                        scope = "allow_always" if choice == "always" else "allow_once"

                        await self._ws.send(json.dumps({
                            "type": "auth_response",
                            "request_id": req_id,
                            "approved": approved,
                            "scope": scope,
                        }))

                        if approved:
                            self._console.print(f"  [green]Approved ({scope})[/green]")
                        else:
                            self._console.print("  [red]Denied[/red]")

                except TimeoutError:
                    self._console.print("\n[yellow]Response timed out.[/yellow]")
                    break

        except Exception as e:
            self._console.print(f"\n[red]Send failed: {e}[/red]")
            log.warning("cli_send_failed", error=str(e))
            if await self.reconnect():
                self._console.print("[green]Reconnected. Please resend your message.[/green]")

    # ── REST helpers ───────────────────────────────────────────────────────

    def _rest_url(self, path: str) -> str:
        """Build a full URL for a REST API path.

        Uses the resolved port/token from the last successful ``connect()``.
        Falls back to constructor values and re-discovery if connect hasn't
        been called yet.
        """
        port = getattr(self, "_resolved_port", None) or self.port or self._discover_port()
        return f"http://{self.host}:{port}{path}"

    def _rest_headers(self) -> dict[str, str]:
        """Return Authorization headers for REST requests."""
        token = getattr(self, "_resolved_token", None) or self.token or self._read_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _rest_get(self, path: str) -> dict[str, Any] | None:
        """Issue an authenticated GET to the daemon REST API.

        Args:
            path: API path (e.g. ``/api/v1/status``).

        Returns:
            Parsed JSON dict on success, None on any failure.
        """
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    self._rest_url(path),
                    headers=self._rest_headers(),
                )
                return resp.json() if resp.status_code == 200 else None
        except Exception:
            return None

    async def _rest_post(self, path: str) -> dict[str, Any] | None:
        """Issue an authenticated POST to the daemon REST API.

        Args:
            path: API path (e.g. ``/api/v1/compact``).

        Returns:
            Parsed JSON dict on success, None on any failure.
        """
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._rest_url(path),
                    headers=self._rest_headers(),
                )
                return resp.json() if resp.status_code == 200 else None
        except Exception:
            return None

    # ── Commands ─────────────────────────────────────────────────────────

    async def _handle_command(self, command: str, args: str) -> bool:
        """Dispatch a parsed slash command.

        Args:
            command: Lowercased command name (without the leading ``/``).
            args: Remainder of the input after the command token.

        Returns:
            False to signal the main loop should exit, True to continue.
        """
        if command in ("quit", "exit"):
            self._console.print("[dim]Goodbye![/dim]")
            return False

        elif command == "help":
            self._console.print(
                Panel(
                    "/status      — system status\n"
                    "/stop        — stop daemon\n"
                    "/memory      — search memories\n"
                    "/plan        — show active plan\n"
                    "/compact     — force compaction\n"
                    "/permissions — show granted permissions\n"
                    "/quit        — exit CLI\n"
                    "/help        — show this help",
                    title="Commands",
                    border_style="blue",
                )
            )

        elif command == "status":
            await self._cmd_status()

        elif command == "stop":
            await self._cmd_stop()
            return False

        elif command == "memory":
            await self._cmd_memory(args)

        elif command == "plan":
            await self._cmd_plan()

        elif command == "compact":
            await self._cmd_compact()

        elif command == "permissions":
            await self._cmd_permissions()

        else:
            self._console.print(f"[red]Unknown command: /{command}[/red]")
            self._console.print("[dim]Type /help for available commands.[/dim]")

        return True

    # ── Command implementations ─────────────────────────────────────────

    async def _cmd_status(self) -> None:
        """Fetch and display system status from the daemon."""
        from rich.table import Table

        data = await self._rest_get("/api/v1/status")
        if not data:
            self._console.print("[red]Cannot reach daemon[/red]")
            return

        table = Table(title="Kora Status", show_header=True)
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        for key, val in data.items():
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val) if val else "none"
            table.add_row(str(key), str(val))
        self._console.print(table)

    async def _cmd_stop(self) -> None:
        """Request graceful daemon shutdown via REST POST."""
        import httpx

        url = self._rest_url("/api/v1/daemon/shutdown")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=self._rest_headers())
                if resp.status_code == 200:
                    self._console.print("[yellow]Daemon shutting down...[/yellow]")
                    self._running = False
                else:
                    self._console.print(f"[red]Shutdown failed: {resp.status_code}[/red]")
        except Exception as e:
            self._console.print(f"[red]Cannot reach daemon: {e}[/red]")

    async def _cmd_memory(self, query: str = "") -> None:
        """Search and display memory notes."""
        q = query.strip() or "recent"
        data = await self._rest_get(f"/api/v1/memory/recall?q={q}")
        if not data:
            self._console.print("[dim]No memory results[/dim]")
            return
        results = data.get("results", [])
        if not results:
            self._console.print("[dim]No matching memories[/dim]")
            return
        for r in results:
            self._console.print(Panel(
                str(r.get("content", r)),
                title=str(r.get("source", "Memory")),
                border_style="blue",
            ))

    async def _cmd_plan(self) -> None:
        """Display active autonomous plans."""
        data = await self._rest_get("/api/v1/inspect/autonomous")
        if not data or not data.get("loops"):
            self._console.print("[dim]No active autonomous plans[/dim]")
            return
        for sid, info in data.get("loops", {}).items():
            status = info.get("status", "unknown")
            goal = info.get("goal", "No goal")
            steps = info.get("steps_completed", 0)
            pending = info.get("steps_pending", 0)
            border = "green" if status == "executing" else "yellow"
            self._console.print(Panel(
                f"Goal: {goal}\nStatus: {status}\nCompleted: {steps}, Pending: {pending}",
                title=f"Autonomous [{sid[:8]}]",
                border_style=border,
            ))

    async def _cmd_compact(self) -> None:
        """Trigger manual conversation compaction."""
        data = await self._rest_post("/api/v1/compact")
        if data:
            self._console.print(f"[green]Compaction: {data.get('status', 'done')}[/green]")
        else:
            self._console.print("[red]Compaction failed[/red]")

    async def _cmd_permissions(self) -> None:
        """Display permission grants from the daemon."""
        from rich.table import Table

        data = await self._rest_get("/api/v1/permissions")
        if not data or not data.get("grants"):
            self._console.print("[dim]No permission grants[/dim]")
            return

        table = Table(title="Permission Grants")
        table.add_column("Tool")
        table.add_column("Scope")
        table.add_column("Decision")
        table.add_column("Granted At")
        for g in data.get("grants", []):
            table.add_row(
                g.get("tool_name", ""),
                g.get("scope", ""),
                g.get("decision", ""),
                g.get("granted_at", ""),
            )
        self._console.print(table)

    # ── Cleanup ──────────────────────────────────────────────────────────

    async def _cleanup(self) -> None:
        """Gracefully close the WebSocket connection on exit."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._console.print("[dim]Disconnected.[/dim]")
