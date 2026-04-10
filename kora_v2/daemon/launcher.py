"""Daemon launcher -- spawn, probe, and stop the Kora daemon process.

Used by the unified entry point to auto-start the daemon on ``kora_v2``,
wait for readiness, and manage lifecycle via ``kora_v2 stop`` / ``kora_v2 status``.

The launcher is read-only with respect to the lockfile. It never acquires
the lock -- only reads existing lockfile state via try_read_existing()
and read_state(). The spawned daemon owns lockfile.acquire().
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import structlog

from kora_v2.daemon.lockfile import DaemonState, Lockfile, pid_is_running

logger = structlog.get_logger()


def _pid_alive(pid: int | None) -> bool:
    """Check if a process with the given PID is alive."""
    return pid_is_running(pid)


def is_daemon_running(lockfile_path: Path) -> bool:
    """Check if a daemon process is alive via lockfile PID validation.

    Args:
        lockfile_path: Path to the daemon lockfile.

    Returns:
        True if daemon is running and lockfile is valid.
    """
    lock = Lockfile(lockfile_path)
    return lock.is_running()


def get_daemon_info(lockfile_path: Path) -> dict[str, Any] | None:
    """Read daemon connection info from lockfile.

    Args:
        lockfile_path: Path to the daemon lockfile.

    Returns:
        Dict with pid, port, started, state, or None if not running.
    """
    lock = Lockfile(lockfile_path)
    if not lock.is_running():
        return None
    return lock.read()


def spawn_daemon(log_dir: Path | None = None) -> int:
    """Fork the daemon as a detached background process.

    Uses --_daemon_internal flag (hidden from --help) to start the
    actual DaemonController in a new process.

    Args:
        log_dir: Directory for daemon log output. Defaults to data/logs/.

    Returns:
        PID of the spawned process.
    """
    if log_dir is None:
        log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "daemon.log"

    cmd = [sys.executable, "-m", "kora_v2", "--_daemon_internal"]

    # Strip proxy env vars -- SOCKS proxies break httpx without socksio,
    # and the daemon should connect directly to APIs.
    _PROXY_VARS = {
        "ALL_PROXY", "all_proxy",
        "HTTPS_PROXY", "https_proxy",
        "HTTP_PROXY", "http_proxy",
        "FTP_PROXY", "ftp_proxy",
        "GRPC_PROXY", "grpc_proxy",
        "RSYNC_PROXY", "rsync_proxy",
    }
    daemon_env = {k: v for k, v in os.environ.items() if k not in _PROXY_VARS}
    daemon_env["KORA_DAEMON"] = "1"

    # Platform-specific detachment
    log_handle = open(log_path, "a")
    kwargs: dict[str, Any] = {
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "env": daemon_env,
    }

    if sys.platform == "win32":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    # Close fd in parent -- child process has its own copy
    log_handle.close()
    logger.info("daemon_spawned", pid=proc.pid, log=str(log_path))
    return proc.pid


def wait_for_ready(
    lockfile_path: Path,
    timeout: float = 90.0,
    poll_interval: float = 0.5,
) -> tuple[str, int]:
    """Poll lockfile until daemon is ready, then health-probe the API.

    State-aware: waits for lockfile state to transition to READY or
    DEGRADED before attempting health probe. Falls back to legacy
    health-probe behavior if state field is absent.

    Args:
        lockfile_path: Path to the daemon lockfile.
        timeout: Max seconds to wait (default 90s -- embedding model load takes 15-40s).
        poll_interval: Seconds between polls.

    Returns:
        Tuple of (host, port) once daemon is confirmed ready.

    Raises:
        TimeoutError: If daemon doesn't become ready within timeout.
    """
    start = time.monotonic()

    while time.monotonic() - start < timeout:
        lock = Lockfile(lockfile_path)
        data = lock.read()

        if data:
            pid = data.get("pid")
            state_str = data.get("state")

            if pid and _pid_alive(pid):
                # State-aware path
                if state_str:
                    try:
                        state = DaemonState(state_str)
                    except ValueError:
                        state = None

                    if state in (DaemonState.READY, DaemonState.DEGRADED):
                        host = data.get("api_host", "127.0.0.1")
                        port = data.get("api_port")
                        if port and _health_probe(host, port):
                            if state == DaemonState.DEGRADED:
                                logger.warning(
                                    "daemon_degraded",
                                    host=host,
                                    port=port,
                                )
                            logger.info(
                                "daemon_ready",
                                host=host,
                                port=port,
                                state=state.value,
                            )
                            return host, port

                    elif state == DaemonState.STARTING:
                        elapsed = time.monotonic() - start
                        logger.debug(
                            "daemon_waiting",
                            elapsed_s=round(elapsed),
                            state="starting",
                        )

                    elif state in (DaemonState.STOPPING, DaemonState.ERROR):
                        logger.debug("daemon_terminal_state", state=state.value)

                else:
                    # Legacy path (no state field) -- fall back to health probe
                    host = data.get("api_host", "127.0.0.1")
                    port = data.get("api_port")
                    if port and _health_probe(host, port):
                        logger.info("daemon_ready_legacy", host=host, port=port)
                        return host, port

        time.sleep(poll_interval)

    raise TimeoutError(
        f"Daemon did not become ready within {timeout}s. Check data/logs/daemon.log for errors."
    )


def _wait_for_ready_state(
    lockfile_path: Path,
    timeout_seconds: int = 30,
) -> tuple[str, int]:
    """Poll lockfile every 1s until state transitions to READY or DEGRADED.

    Args:
        lockfile_path: Path to the daemon lockfile.
        timeout_seconds: Max seconds to wait for READY state.

    Returns:
        Tuple of (host, port) once daemon is confirmed ready.

    Raises:
        RuntimeError: If daemon dies, enters ERROR, or timeout is exceeded.
    """
    deadline = time.monotonic() + timeout_seconds
    poll_interval = 1.0

    while time.monotonic() < deadline:
        time.sleep(poll_interval)

        lock = Lockfile(lockfile_path)
        try:
            pid, state = lock.read_state()
        except Exception:
            continue

        if not _pid_alive(pid):
            raise RuntimeError("Daemon process died during startup")

        if state in (DaemonState.READY, DaemonState.DEGRADED):
            data = lock.read() or {}
            host = data.get("api_host", "127.0.0.1")
            port = data.get("api_port")
            if port:
                return host, port
            continue

        if state in (DaemonState.STOPPING, DaemonState.ERROR):
            raise RuntimeError(f"Daemon entered terminal state '{state}' during startup")

        elapsed = time.monotonic() - (deadline - timeout_seconds)
        logger.debug("daemon_waiting", elapsed_s=round(elapsed))

    # Timeout -- attempt connection anyway (slow hardware edge case)
    logger.warning(
        "daemon_ready_timeout",
        timeout_s=timeout_seconds,
        msg="Attempting connection anyway",
    )
    lock = Lockfile(lockfile_path)
    data = lock.read() or {}
    host = data.get("api_host", "127.0.0.1")
    port = data.get("api_port")
    if port:
        return host, port
    raise RuntimeError(
        f"Daemon did not reach READY state within {timeout_seconds}s and no port available."
    )


def _health_probe(host: str, port: int, timeout: float = 2.0) -> bool:
    """Quick HTTP health check against the daemon's API.

    Args:
        host: API server host.
        port: API server port.
        timeout: Request timeout.

    Returns:
        True if health endpoint responds with 200.
    """
    import http.client
    import urllib.error
    import urllib.request

    url = f"http://{host}:{port}/api/v1/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException):
        return False


def stop_daemon(
    lockfile_path: Path,
    token_path: Path | None = None,
) -> bool:
    """Send graceful shutdown to the daemon via REST API.

    Args:
        lockfile_path: Path to the daemon lockfile.
        token_path: Path to the API token file. Defaults to data/.api_token.

    Returns:
        True if shutdown was requested successfully.
    """
    lock = Lockfile(lockfile_path)
    data = lock.read()
    if not data:
        logger.info("no_lockfile_found")
        return False

    if not lock.is_running():
        logger.info("daemon_not_running_stale_lockfile")
        return False

    host = data.get("api_host", data.get("host", "127.0.0.1"))
    port = data.get("api_port", data.get("port"))
    if not port:
        logger.error("lockfile_missing_port")
        return False

    token = _load_api_token(token_path)

    import urllib.error
    import urllib.request

    url = f"http://{host}:{port}/api/v1/daemon/shutdown"
    try:
        req = urllib.request.Request(url, method="POST", data=b"")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=5.0) as resp:
            if resp.status == 200:
                logger.info("daemon_shutdown_requested")
                return True
            else:
                logger.warning("daemon_shutdown_bad_status", status=resp.status)
                return False
    except urllib.error.HTTPError as e:
        logger.error("daemon_shutdown_http_error", code=e.code, reason=e.reason)
        return False
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        logger.error("daemon_shutdown_unreachable", error=str(e))
        return False


def _load_api_token(token_path: Path | None = None) -> str:
    """Load API token from file.

    Args:
        token_path: Explicit path. Falls back to data/.api_token.

    Returns:
        Token string, or empty if not found.
    """
    if token_path is None:
        token_path = Path("data/.api_token")

    if token_path.exists():
        try:
            return token_path.read_text().strip()
        except OSError:
            pass
    return ""


def get_daemon_status(
    lockfile_path: Path,
    token_path: Path | None = None,
) -> dict[str, Any]:
    """Get detailed daemon status by combining lockfile + health endpoint.

    Args:
        lockfile_path: Path to the daemon lockfile.
        token_path: Path to the API token file.

    Returns:
        Status dict with running, pid, port, uptime, version, state fields.
    """
    result: dict[str, Any] = {"running": False}

    lock = Lockfile(lockfile_path)
    if not lock.is_running():
        return result

    data = lock.read()
    if not data:
        return result

    result["running"] = True
    result["pid"] = data.get("pid")
    result["port"] = data.get("api_port", data.get("port"))
    result["started"] = data.get("started_at", data.get("started"))
    result["state"] = data.get("state")

    # Try health endpoint for extended info
    host = data.get("api_host", data.get("host", "127.0.0.1"))
    port = data.get("api_port", data.get("port"))
    if port:
        health = _fetch_health(host, port, token_path)
        if health:
            result.update(health)

    return result


def _fetch_health(
    host: str,
    port: int,
    token_path: Path | None = None,
) -> dict[str, Any] | None:
    """Fetch /api/v1/health for extended daemon info.

    Returns:
        Parsed health response, or None on failure.
    """
    import urllib.error
    import urllib.request

    token = _load_api_token(token_path)
    url = f"http://{host}:{port}/api/v1/health"

    try:
        req = urllib.request.Request(url, method="GET")
        if token:
            req.add_header("Authorization", f"Bearer {token}")

        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        pass
    return None


def ensure_daemon_running(
    lockfile_path: Path,
    log_dir: Path | None = None,
) -> tuple[str, int]:
    """Start daemon if not running, return connection info.

    The launcher is read-only with respect to the lockfile. It uses
    try_read_existing() + read_state() to inspect the lockfile. If no
    valid running daemon exists, it spawns a daemon process. The spawned
    daemon owns lockfile.acquire().

    This is the primary function called by ``kora_v2`` (no args).

    Args:
        lockfile_path: Path to the daemon lockfile.
        log_dir: Directory for daemon logs.

    Returns:
        Tuple of (host, port) for WebSocket connection.

    Raises:
        TimeoutError: If daemon fails to start.
    """
    lock = Lockfile(lockfile_path)

    # 1. Check if lockfile exists and is readable
    if not lock.try_read_existing():
        spawn_daemon(log_dir=log_dir)
        return wait_for_ready(lockfile_path)

    # 2. Read PID and state
    pid, state = lock.read_state()

    # 3. Verify PID is alive
    if not _pid_alive(pid):
        logger.info("stale_lockfile", pid=pid)
        lock.unlink()
        spawn_daemon(log_dir=log_dir)
        return wait_for_ready(lockfile_path)

    # 4. State-aware wait
    if state == DaemonState.STARTING:
        logger.info("daemon_starting", pid=pid)
        try:
            return _wait_for_ready_state(lockfile_path, timeout_seconds=30)
        except RuntimeError as e:
            logger.warning("daemon_ready_wait_failed", error=str(e))
            return wait_for_ready(lockfile_path, timeout=60.0)

    elif state in (DaemonState.READY, DaemonState.DEGRADED):
        data = lock.read() or {}
        host = data.get("api_host", "127.0.0.1")
        port = data.get("api_port")

        if port:
            if _health_probe(host, port):
                if state == DaemonState.DEGRADED:
                    logger.warning("daemon_degraded", host=host, port=port)
                else:
                    logger.info("daemon_already_running", host=host, port=port)
                return host, port
            else:
                logger.warning("daemon_alive_but_unresponsive")
                grace_deadline = time.monotonic() + 15.0
                while time.monotonic() < grace_deadline:
                    time.sleep(0.5)
                    if not lock.is_running():
                        break
                    refreshed = lock.read() or {}
                    host = refreshed.get("api_host", "127.0.0.1")
                    port = refreshed.get("api_port")
                    if port and _health_probe(host, port):
                        logger.info("daemon_recovered", host=host, port=port)
                        return host, port

                if lock.is_running():
                    raise TimeoutError(
                        "Daemon process is alive but API is not responsive. "
                        "Wait a few seconds and retry, or run 'kora_v2 stop' if it is stuck."
                    )
        else:
            logger.info("daemon_running_no_port")
            return wait_for_ready(lockfile_path)

    elif state in (DaemonState.STOPPING, DaemonState.ERROR):
        logger.info("daemon_terminal_state_waiting", state=state.value if state else "unknown")
        time.sleep(2)
        return ensure_daemon_running(lockfile_path, log_dir=log_dir)

    elif state is None:
        # Legacy lockfile without state field
        data = lock.read() or {}
        host = data.get("api_host", "127.0.0.1")
        port = data.get("api_port")
        if port and _health_probe(host, port):
            logger.info("daemon_already_running_legacy", host=host, port=port)
            return host, port
        return wait_for_ready(lockfile_path)

    # Not running -- spawn it
    spawn_daemon(log_dir=log_dir)
    return wait_for_ready(lockfile_path)


async def _run_daemon(settings: Any) -> None:
    """Initialize the DI container and start the API server.

    Each subsystem is initialized inside its own try/except so that a
    failure in one non-critical subsystem (e.g. MCP servers, emotion
    assessors) degrades the daemon rather than crashing it outright.

    The lockfile transitions to READY when all subsystems succeed, or
    DEGRADED when one or more non-critical subsystems failed.

    Args:
        settings: Kora Settings instance.
    """
    from kora_v2.core.di import Container
    from kora_v2.daemon.server import run_server

    lock = Lockfile(settings.data_dir / "kora.lock")
    lock.acquire()
    lock.set_state(DaemonState.STARTING)

    container = Container(settings)
    _failed_subsystems: list[str] = []

    # -- Subsystem 0: Ensure operational DB schema exists --
    try:
        from kora_v2.core.db import init_operational_db
        await init_operational_db(settings.data_dir / "operational.db")
    except Exception:
        logger.error("operational_db_init_failed", exc_info=True)
        _failed_subsystems.append("operational_db")

    # -- Subsystem 1: Checkpointer (non-critical — falls back to MemorySaver) --
    try:
        await container.initialize_checkpointer()
    except Exception:
        logger.error("checkpointer_init_failed_continuing", exc_info=True)
        _failed_subsystems.append("checkpointer")

    # -- Subsystem 2: Memory (non-critical — conversation still works) --
    try:
        await container.initialize_memory()
    except Exception:
        logger.error("memory_init_failed_continuing", exc_info=True)
        _failed_subsystems.append("memory")

    # -- Subsystem 3: Workers + tools (non-critical) --
    try:
        container.initialize_workers()
    except Exception:
        logger.error("workers_init_failed_continuing", exc_info=True)
        _failed_subsystems.append("workers")

    # -- Subsystem 4: MCP servers (non-critical) --
    try:
        await container.initialize_mcp()
    except Exception:
        logger.error("mcp_init_failed_continuing", exc_info=True)
        _failed_subsystems.append("mcp")

    # -- Subsystem 5: Phase 4 — emotion, quality, session manager (non-critical) --
    try:
        container.initialize_phase4()
    except Exception:
        logger.error("phase4_init_failed_continuing", exc_info=True)
        _failed_subsystems.append("phase4")

    # Store failure list on container for inspection by /status endpoint
    container._failed_subsystems = _failed_subsystems  # type: ignore[attr-defined]

    # Choose port
    port = settings.daemon.api_port if hasattr(settings, "daemon") and hasattr(settings.daemon, "api_port") else 0

    if _failed_subsystems:
        logger.warning(
            "daemon_starting_degraded",
            failed_subsystems=_failed_subsystems,
        )
    else:
        logger.info("daemon_all_subsystems_initialized")

    # Callback to update lockfile with actual port after uvicorn binds.
    # This is critical when port=0 (OS-assigned).
    def _on_bind(actual_host: str, actual_port: int) -> None:
        state = DaemonState.READY if not _failed_subsystems else DaemonState.DEGRADED
        lock.set_state(state, api_host=actual_host, api_port=actual_port)
        logger.info("lockfile_port_updated", host=actual_host, port=actual_port)

    try:
        await run_server(container, host="127.0.0.1", port=port, on_bind=_on_bind)
    except Exception:
        logger.error("server_crashed", exc_info=True)
        lock.set_state(DaemonState.ERROR)
        raise
    finally:
        await container.close()
        lock.set_state(DaemonState.STOPPING)
        lock.release()


def main() -> None:
    """CLI entry point for ``kora`` command.

    Handles subcommands (stop, status) and the hidden ``--_daemon_internal``
    flag used by ``spawn_daemon()`` to start the actual daemon process.
    """
    import argparse
    import asyncio

    from kora_v2 import __version__

    parser = argparse.ArgumentParser(prog="kora", description="Kora V2 AI Companion")
    parser.add_argument("--version", action="version", version=f"kora {__version__}")
    parser.add_argument("--_daemon_internal", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("command", nargs="?", choices=["stop", "status"], default=None)
    args = parser.parse_args()

    if args._daemon_internal:
        from kora_v2.core.settings import get_settings

        settings = get_settings()
        asyncio.run(_run_daemon(settings))
        return

    if args.command == "stop":
        from kora_v2.core.settings import get_settings

        settings = get_settings()
        lockfile_path = settings.data_dir / "kora.lock"
        if stop_daemon(lockfile_path):
            print("Shutdown requested.")
        else:
            print("Daemon is not running.")
        return

    if args.command == "status":
        from kora_v2.core.settings import get_settings

        settings = get_settings()
        lockfile_path = settings.data_dir / "kora.lock"
        info = get_daemon_status(lockfile_path)
        if info.get("running"):
            print(f"Kora daemon running (pid={info.get('pid')}, port={info.get('port')}, state={info.get('state', 'unknown')})")
        else:
            print("Kora daemon is not running.")
        return

    # Default: ensure daemon is running and print connection info
    from kora_v2.core.settings import get_settings

    settings = get_settings()
    lockfile_path = settings.data_dir / "kora.lock"
    try:
        host, port = ensure_daemon_running(lockfile_path)
        print(f"Kora V2 {__version__} — daemon ready at {host}:{port}")
    except TimeoutError as e:
        print(f"Error: {e}")
        sys.exit(1)
