"""PID + port lockfile management for preventing duplicate daemon instances.

Provides:
- DaemonState enum (5-state readiness machine)
- Lockfile class (OS-level exclusive lock, JSON payload, state transitions)
- pid_is_running() helper

The Lockfile uses OS-level exclusive locks (fcntl on Unix, msvcrt on Windows)
to prevent TOCTOU race conditions when multiple processes try to spawn the daemon.
"""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

logger = structlog.get_logger()


def pid_is_running(pid: int | None) -> bool:
    """Return True when PID refers to a live, non-zombie process."""
    if pid is None or pid <= 0:
        return False

    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False

    if sys.platform != "win32":
        try:
            proc = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
        except Exception:
            return True

        status = proc.stdout.strip().splitlines()
        if status:
            first_state = status[0].strip().upper()
            if first_state.startswith("Z"):
                logger.info("pid_is_zombie", pid=pid)
                return False

    return True


class DaemonState(StrEnum):
    """Five-state readiness machine for daemon lifecycle.

    State transitions:
        STARTING -> READY (all services initialized)
        STARTING -> DEGRADED (services up but LLM unavailable)
        STARTING -> ERROR (fatal initialization failure)
        READY -> DEGRADED (LLM becomes unavailable)
        READY -> STOPPING (shutdown requested)
        DEGRADED -> READY (LLM restored)
        DEGRADED -> STOPPING (shutdown requested)
        STOPPING -> (process exits)
        ERROR -> (process exits)
    """

    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    ERROR = "error"


class Lockfile:
    """Manages a lockfile containing PID, port, state, and start time.

    Used to prevent duplicate daemon instances and to allow CLI clients
    to discover the daemon's API port and readiness state.

    Uses OS-level exclusive file locks (fcntl/msvcrt) to prevent TOCTOU
    race conditions when multiple processes try to spawn the daemon.

    The lockfile JSON payload:
        {"pid", "state", "api_host", "api_port", "started_at", "ready_at"}
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_fd: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------
    # OS-level exclusive lock
    # ------------------------------------------------------------------

    def acquire(self, port: int | None = None) -> bool:
        """Acquire an OS-level exclusive lock on the lockfile.

        Returns True if acquired, False if held by another process.
        After acquiring, writes initial payload with PID and STARTING state.

        Args:
            port: DEPRECATED. Ignored. Use update(api_port=...) after the
                  API server binds.
        """
        if port is not None:
            logger.debug("lockfile_acquire_port_deprecated", port=port)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._lock_fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR)

            if sys.platform == "win32":
                msvcrt.locking(self._lock_fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            payload = {
                "pid": os.getpid(),
                "state": DaemonState.STARTING.value,
                "started_at": datetime.now(UTC).isoformat(),
                "ready_at": None,
                "api_host": None,
                "api_port": None,
            }
            self._write_payload(payload)

            logger.info(
                "lockfile_acquired",
                path=str(self._path),
                pid=os.getpid(),
            )
            return True

        except (BlockingIOError, OSError) as e:
            logger.debug("lockfile_already_held", error=str(e))
            if self._lock_fd is not None:
                try:
                    os.close(self._lock_fd)
                except OSError:
                    pass
                self._lock_fd = None
            return False

    def release(self) -> None:
        """Release the OS-level lock and close the file descriptor."""
        if self._lock_fd is not None:
            try:
                if sys.platform == "win32":
                    try:
                        msvcrt.locking(self._lock_fd, msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    try:
                        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                os.close(self._lock_fd)
            except OSError as e:
                logger.warning("lockfile_release_error", error=str(e))
            finally:
                self._lock_fd = None
            logger.info("lockfile_released", path=str(self._path))

        try:
            self._path.unlink(missing_ok=True)
        except OSError as e:
            logger.debug("lockfile_remove_failed", error=str(e))

    # ------------------------------------------------------------------
    # Readiness state machine
    # ------------------------------------------------------------------

    def set_state(self, state: DaemonState, **kwargs: Any) -> bool:
        """Update the daemon state in the lockfile payload.

        Args:
            state: The new DaemonState.
            **kwargs: Additional fields to merge (e.g. ready_at).

        Returns:
            True if updated successfully.
        """
        data = self._read_payload()
        if data is None:
            data = {"pid": os.getpid()}

        data["state"] = state.value
        data.update(kwargs)

        return self._write_payload(data)

    def read_state(self) -> tuple[int | None, DaemonState | None]:
        """Read PID and state from the lockfile (read-only).

        Returns:
            Tuple of (pid, DaemonState), or (None, None) if unreadable.
        """
        data = self.read()
        if data is None:
            return None, None

        pid = data.get("pid")
        state_str = data.get("state")

        daemon_state = None
        if state_str:
            try:
                daemon_state = DaemonState(state_str)
            except ValueError:
                logger.warning("lockfile_unknown_state", state=state_str)

        return pid, daemon_state

    def try_read_existing(self) -> bool:
        """Check if a lockfile exists and is readable.

        Returns True if the lockfile exists and contains valid JSON.
        """
        return self.read() is not None

    # ------------------------------------------------------------------
    # Legacy-compatible API
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """Check if another daemon is running by validating lockfile PID."""
        data = self.read()
        if data is None:
            return False

        pid = data.get("pid")
        if pid is None:
            return False

        if pid_is_running(pid):
            return True

        logger.info("lockfile_stale", pid=pid)
        return False

    def update(self, **kwargs: Any) -> bool:
        """Update lockfile data with additional fields.

        Merges kwargs into existing lockfile data.

        Returns:
            True if updated successfully.
        """
        data = self._read_payload()
        if data is None:
            data = self.read()
            if data is None:
                return False

        data.update(kwargs)
        return self._write_payload(data)

    def read(self) -> dict[str, Any] | None:
        """Read and parse lockfile contents.

        Returns:
            Parsed lockfile data, or None if not found/invalid.
        """
        if not self._path.exists():
            return None

        try:
            text = self._path.read_text()
            if not text.strip():
                return None
            return json.loads(text)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("lockfile_read_failed", error=str(e))
            return None

    def unlink(self) -> None:
        """Remove the lockfile from disk."""
        try:
            self._path.unlink(missing_ok=True)
            logger.info("lockfile_removed", path=str(self._path))
        except OSError as e:
            logger.warning("lockfile_unlink_failed", error=str(e))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_payload(self, data: dict[str, Any]) -> bool:
        """Write payload using rewrite-safety pattern.

        If holding the OS lock, writes through the locked fd.
        Otherwise falls back to Path.write_text().
        """
        encoded = json.dumps(data, indent=2).encode()

        if self._lock_fd is not None:
            try:
                os.lseek(self._lock_fd, 0, os.SEEK_SET)
                os.ftruncate(self._lock_fd, 0)
                os.write(self._lock_fd, encoded)
                os.fsync(self._lock_fd)
                return True
            except OSError as e:
                logger.warning("lockfile_write_fd_failed", error=str(e))
                return False
        else:
            try:
                self._path.write_text(json.dumps(data, indent=2))
                return True
            except OSError as e:
                logger.warning("lockfile_write_path_failed", error=str(e))
                return False

    def _read_payload(self) -> dict[str, Any] | None:
        """Read payload through the locked fd if available."""
        if self._lock_fd is not None:
            try:
                os.lseek(self._lock_fd, 0, os.SEEK_SET)
                raw = os.read(self._lock_fd, 4096)
                if raw:
                    return json.loads(raw.decode())
                return None
            except (OSError, json.JSONDecodeError):
                pass

        return self.read()
