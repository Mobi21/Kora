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
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import structlog
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

log = structlog.get_logger(__name__)

# Reconnection constants
MAX_RECONNECT_ATTEMPTS = 5
HEARTBEAT_INTERVAL = 30  # seconds

# Default paths
_DEFAULT_LOCKFILE = Path("data/kora.lock")
_LEGACY_LOCKFILE = Path("data/.lockfile")
_DEFAULT_TOKEN_PATH = Path("data/.api_token")

KORA_BANNER = r"""
 _  __
| |/ /___  _ __ __ _
| ' // _ \| '__/ _` |
| . \ (_) | | | (_| |
|_|\_\___/|_|  \__,_|
"""


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


def format_memory_preview(content: Any, max_chars: int = 240) -> str:
    """Return a compact one-line memory preview for terminal display."""
    text = " ".join(str(content).split())
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3]}..."


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
        self._legacy_lockfile_path: Path = _LEGACY_LOCKFILE
        self._token_path: Path = _DEFAULT_TOKEN_PATH

    # ── Discovery ────────────────────────────────────────────────────────

    def _read_lockfile_data(self) -> dict[str, Any] | None:
        """Read daemon discovery data from the current or legacy lockfile."""
        for path in (self._lockfile_path, self._legacy_lockfile_path):
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
            except Exception:
                continue
            if isinstance(data, dict):
                return data
        return None

    def _discover_port(self) -> int | None:
        """Auto-discover daemon port from lockfile.

        Reads the JSON lockfile written by the daemon launcher. Falls back
        to the pre-rearchitecture ``data/.lockfile`` path for old dev state.
        Prefers ``api_port`` over ``port`` (legacy fallback).

        Returns:
            Port number, or None if lockfile is absent or unreadable.
        """
        data = self._read_lockfile_data()
        if not data:
            return None
        return data.get("api_port") or data.get("port") or None

    def _discover_host(self) -> str | None:
        """Auto-discover daemon host from lockfile data."""
        data = self._read_lockfile_data()
        if not data:
            return None
        return data.get("api_host") or data.get("host") or None

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
        host = self._discover_host() or self.host
        token = self.token or self._read_token()

        if not port:
            self._console.print(
                "[red]Could not find a running Kora daemon.[/red]\n"
                "[dim]Try `kora start` first, or use `kora chat` to auto-start it.[/dim]"
            )
            return False

        if not token:
            self._console.print(
                "[red]Could not find the local API token.[/red]\n"
                "[dim]Start the daemon once so Kora can create data/.api_token.[/dim]"
            )
            return False

        # Persist resolved values so REST helpers can use them
        self.host = host
        self._resolved_port: int = port
        self._resolved_token: str = token

        uri = f"ws://{host}:{port}/api/v1/ws?token={token}"

        try:
            self._ws = await websockets.connect(uri)
            self._console.print(f"[green]Connected to Kora[/green] [dim]{host}:{port}[/dim]")
            log.info("cli_connected", host=host, port=port)
            return True
        except Exception as e:
            self._console.print(f"[red]Connection failed:[/red] {e}")
            self._console.print("[dim]Run `kora status` or `kora doctor` for daemon details.[/dim]")
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

        self._render_welcome()

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

    def _render_welcome(self) -> None:
        """Render the terminal-first welcome panel."""
        self._console.print(
            Panel(
                f"[bold cyan]{KORA_BANNER}[/bold cyan]\n"
                "[bold]Local-first Life OS CLI[/bold]\n"
                "Type a message to chat. Use [bold]/help[/bold] for commands, "
                "[bold]/status[/bold] for runtime state, or [bold]/doctor[/bold] for checks.",
                title="Kora",
                subtitle="127.0.0.1 only",
                border_style="cyan",
            )
        )

    # ── First-run ────────────────────────────────────────────────────────

    async def _check_first_run(self) -> None:
        """Run first-run onboarding if no previous session exists.

        Phase 5: delegates all questions to the structured 5-section
        ``run_wizard`` flow. Section 5 owns API key prompts (MiniMax +
        optional Brave), so this method is just a gate + introduction.
        """
        from kora_v2.core.settings import get_settings

        memory_base = Path(
            get_settings().memory.kora_memory_path
        ).expanduser()
        bridges_dir = memory_base / ".kora" / "bridges"
        if bridges_dir.exists() and list(bridges_dir.glob("*.md")):
            return  # Not first run

        from kora_v2.cli.first_run import run_wizard
        result = await run_wizard(
            self._console, container=None, memory_base=memory_base
        )

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
            wait_notice_count = 0

            while True:
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
                    data = json.loads(raw)
                    msg_type = data.get("type", "")

                    if msg_type in {"session_ready", "session_greeting"}:
                        continue

                    if msg_type == "token":
                        token = data.get("content", "")
                        self._response_buffer += token
                        self._console.print(format_streaming_token(token), end="")

                    elif msg_type == "tool_start":
                        tool = data.get("content", "")
                        self._console.print(f"\n[cyan]tool[/cyan] {tool} [dim]started[/dim]")

                    elif msg_type == "tool_result":
                        tool = data.get("tool_name") or data.get("content") or "tool"
                        status = data.get("content", "completed")
                        style = "green" if status == "completed" else "yellow"
                        self._console.print(f"[{style}]tool[/{style}] {tool} [dim]{status}[/dim]")

                    elif msg_type == "response_complete":
                        metadata = data.get("metadata", {}) or {}
                        tool_count = metadata.get("tool_call_count", 0)
                        turn_count = metadata.get("turn_count")
                        details = []
                        if turn_count:
                            details.append(f"turn {turn_count}")
                        if tool_count:
                            details.append(f"{tool_count} tool call(s)")
                        if metadata.get("compaction_tier"):
                            details.append(f"memory {metadata['compaction_tier']}")
                        if details:
                            self._console.print(f"\n[dim]{' | '.join(details)}[/dim]")
                        else:
                            self._console.print()
                        break

                    elif msg_type == "error":
                        error = data.get("content", "Unknown error")
                        self._console.print(f"\n[red]Error: {error}[/red]")
                        self._console.print("[dim]Try /status or /doctor if this looks like a runtime issue.[/dim]")
                        break

                    elif msg_type == "info":
                        content_msg = data.get("content", "")
                        if content_msg:
                            self._console.print(f"\n[blue]info[/blue] {content_msg}")

                    elif msg_type == "notification":
                        title = data.get("title") or data.get("content") or "Notification"
                        self._console.print(f"\n[magenta]notice[/magenta] {title}")

                    elif msg_type == "autonomous_checkpoint":
                        steps = data.get("steps_completed", 0)
                        self._console.print(f"\n[blue]plan[/blue] checkpoint saved ({steps} step(s) done)")

                    elif msg_type == "autonomous_failed":
                        reason = data.get("reason", "unknown")
                        self._console.print(f"\n[yellow]plan paused[/yellow] {reason}")

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
                    wait_notice_count += 1
                    if wait_notice_count <= 6:
                        self._console.print("\n[dim]still working...[/dim]", end="")
                        continue
                    self._console.print("\n[yellow]Response timed out after 70s.[/yellow]")
                    self._console.print("[dim]The daemon may still be busy; /status can confirm it is alive.[/dim]")
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
            if not (getattr(self, "_resolved_port", None) or self.port or self._discover_port()):
                return None
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
            if not (getattr(self, "_resolved_port", None) or self.port or self._discover_port()):
                return None
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
            self._cmd_help()

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

        elif command == "doctor":
            await self._cmd_doctor()

        elif command == "setup":
            await self._cmd_setup()

        else:
            self._console.print(f"[red]Unknown command: /{command}[/red]")
            self._console.print("[dim]Type /help for available commands.[/dim]")

        return True

    # ── Command implementations ─────────────────────────────────────────

    def _cmd_help(self) -> None:
        """Display CLI commands grouped by use."""
        self._console.print(
            Panel(
                "[bold]Chat[/bold]\n"
                "  /status       Show daemon, session, tools, and orchestration state\n"
                "  /doctor       Run runtime checks and separate core from optional issues\n"
                "  /help         Show this help\n"
                "  /quit         Exit the CLI\n\n"
                "[bold]Life OS[/bold]\n"
                "  /memory QUERY Search local memory metadata with a harmless query\n"
                "  /plan         Show active autonomous plans or queued work\n"
                "  /compact      Request conversation compaction on the next turn\n\n"
                "[bold]Control[/bold]\n"
                "  /permissions  Show recent permission grants\n"
                "  /setup        Show local paths and setup hints\n"
                "  /stop         Stop the daemon and exit",
                title="Commands",
                border_style="cyan",
            )
        )

    async def _cmd_status(self) -> None:
        """Fetch and display system status from the daemon."""
        status = await self._rest_get("/api/v1/status")
        if not status:
            self._console.print("[red]Cannot reach daemon.[/red]")
            self._console.print("[dim]Run `kora start`, then retry /status.[/dim]")
            return

        tools = await self._rest_get("/api/v1/inspect/tools") or {}
        orchestration = await self._rest_get("/api/v1/orchestration/status") or {}
        lock = self._read_lockfile_data() or {}

        table = Table(title="Kora Status", show_header=True, header_style="bold cyan")
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        table.add_row("daemon", str(status.get("status", "unknown")))
        table.add_row("pid", str(lock.get("pid", "unknown")))
        table.add_row("endpoint", f"{self.host}:{getattr(self, '_resolved_port', None) or self.port or self._discover_port()}")
        table.add_row("version", str(status.get("version", "unknown")))
        table.add_row("session", "active" if status.get("session_active") else "not active")
        table.add_row("turn_count", str(status.get("turn_count", 0)))
        failed = status.get("failed_subsystems") or []
        table.add_row("degraded_subsystems", ", ".join(failed) if failed else "none")
        table.add_row("skill_loader", "ready" if tools.get("skill_loader_initialized") else "unavailable")
        table.add_row("skills", str(tools.get("skill_count", 0)))
        mcp = tools.get("mcp", {}) if isinstance(tools.get("mcp", {}), dict) else {}
        table.add_row("mcp", "initialized" if mcp.get("initialized") else "not initialized or lazy")
        table.add_row("pipelines", str(len(orchestration.get("pipelines", []))))
        table.add_row("live_tasks", str(len(orchestration.get("live_tasks", []))))
        table.add_row("open_decisions", str(orchestration.get("open_decisions_count", 0)))
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
        data = await self._rest_get(f"/api/v1/memory/recall?{urlencode({'q': q})}")
        if not data:
            self._console.print("[dim]No memory results[/dim]")
            return
        if data.get("error"):
            self._console.print(f"[yellow]Memory unavailable:[/yellow] {data['error']}")
            return
        results = data.get("results", [])
        if not results:
            self._console.print("[dim]No matching memories[/dim]")
            return
        for r in results:
            preview = format_memory_preview(r.get("content", r))
            note_id = str(r.get("id", ""))
            tags = r.get("tags", [])
            metadata = []
            if note_id:
                metadata.append(f"id={note_id[:12]}")
            if tags:
                metadata.append("tags=" + ", ".join(str(t) for t in tags[:4]))
            if metadata:
                preview = f"{preview}\n\n[dim]{' | '.join(metadata)}[/dim]"
            self._console.print(Panel(
                preview,
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
            self._console.print(
                f"[green]Compaction requested:[/green] {data.get('status', 'done')}\n"
                "[dim]Kora will apply it on the next turn; no external files are touched here.[/dim]"
            )
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

    async def _cmd_doctor(self) -> None:
        """Run and display runtime doctor checks."""
        data = await self._rest_get("/api/v1/inspect/doctor")
        if not data:
            self._console.print("[red]Doctor unavailable: cannot reach daemon.[/red]")
            return

        healthy = data.get("healthy", False)
        style = "green" if healthy else "yellow"
        self._console.print(
            Panel(
                data.get("summary", "no summary"),
                title=f"Doctor {'OK' if healthy else 'Needs Attention'}",
                border_style=style,
            )
        )

        optional_markers = (
            "agent_browser",
            "vault_",
            "mcp_",
            "sentence_transformers",
            "sqlite_vec",
            "capability_browser",
            "capability_vault",
            "capability_workspace",
        )
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Area")
        table.add_column("Check")
        table.add_column("Result")
        table.add_column("Detail")
        for check in data.get("checks", []):
            name = str(check.get("name", "unknown"))
            passed = bool(check.get("passed"))
            optional = any(marker in name for marker in optional_markers)
            area = "optional" if optional else "core"
            result = "[green]pass[/green]" if passed else "[red]fail[/red]"
            table.add_row(area, name, result, str(check.get("detail", "")))
        self._console.print(table)

    async def _cmd_setup(self) -> None:
        """Show local setup paths without exposing secrets."""
        data = await self._rest_get("/api/v1/inspect/setup")
        lock = self._read_lockfile_data() or {}

        table = Table(title="Local Setup", show_header=True, header_style="bold cyan")
        table.add_column("Item", style="cyan")
        table.add_column("Value")
        table.add_row("lockfile", str(self._lockfile_path))
        table.add_row("legacy_lockfile", str(self._legacy_lockfile_path))
        table.add_row("api_token_file", str(self._token_path))
        table.add_row("daemon_pid", str(lock.get("pid", "unknown")))
        table.add_row("daemon_state", str(lock.get("state", "unknown")))
        if data:
            table.add_row("data_dir", str(data.get("data_dir", "")))
            memory = data.get("memory", {}) if isinstance(data.get("memory"), dict) else {}
            table.add_row("memory_path", str(memory.get("path", "")))
            security = data.get("security", {}) if isinstance(data.get("security"), dict) else {}
            table.add_row("token_file_exists", str(security.get("token_file_exists", False)))
            table.add_row("auth_mode", str(security.get("auth_mode", "")))
        else:
            table.add_row("daemon_api", "unreachable")
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
