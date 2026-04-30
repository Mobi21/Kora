"""Kora V2 Acceptance Test CLI.

Usage:
    python3 -m tests.acceptance.automated start [--fast]
    python3 -m tests.acceptance.automated stop
    python3 -m tests.acceptance.automated send "message"
    python3 -m tests.acceptance.automated status
    python3 -m tests.acceptance.automated snapshot <name>
    python3 -m tests.acceptance.automated diff <snap1> <snap2>
    python3 -m tests.acceptance.automated idle-wait [--min-soak N] [--timeout N]
    python3 -m tests.acceptance.automated advance <hours>
    python3 -m tests.acceptance.automated restart
    python3 -m tests.acceptance.automated test-auth
    python3 -m tests.acceptance.automated test-auth-reset
    python3 -m tests.acceptance.automated test-error
    python3 -m tests.acceptance.automated compaction-status
    python3 -m tests.acceptance.automated skill-gating-check
    python3 -m tests.acceptance.automated life-management-check
    python3 -m tests.acceptance.automated tool-usage-summary
    python3 -m tests.acceptance.automated monitor
    python3 -m tests.acceptance.automated report

    # Phase 7.5 / Phase 8 state surface (AT2):
    python3 -m tests.acceptance.automated orchestration-status
    python3 -m tests.acceptance.automated pipeline-history [--limit N]
    python3 -m tests.acceptance.automated working-docs
    python3 -m tests.acceptance.automated edit-working-doc "new plan item"
    python3 -m tests.acceptance.automated notifications [--limit N]
    python3 -m tests.acceptance.automated insights [--limit N]
    python3 -m tests.acceptance.automated phase-history [--hours N]
    python3 -m tests.acceptance.automated vault-snapshot

    # AT3 commands: idle-soak manifests, phase gates, benchmarks, event tail:
    python3 -m tests.acceptance.automated soak-manifest <phase>
    python3 -m tests.acceptance.automated phase-gate <phase_name>
    python3 -m tests.acceptance.automated benchmarks
    python3 -m tests.acceptance.automated event-tail [--seconds N]
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── Paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parents[2].resolve()
ACCEPT_DIR = Path(os.environ.get("KORA_ACCEPTANCE_DIR", "/tmp/claude/kora_acceptance"))
OUTPUT_DIR = ACCEPT_DIR / "acceptance_output"
SNAPSHOTS_DIR = OUTPUT_DIR / "snapshots"
SESSION_FILE = ACCEPT_DIR / "acceptance_session.json"
MONITOR_FILE = OUTPUT_DIR / "acceptance_monitor.md"
HARNESS_SOCK = ACCEPT_DIR / "harness.sock"
HARNESS_PID_FILE = ACCEPT_DIR / "harness.pid"

LOCKFILE = PROJECT_ROOT / "data" / "kora.lock"
TOKEN_FILE = PROJECT_ROOT / "data" / ".api_token"

# Prefer the repo venv so both the harness CLI and the spawned daemon
# use the same interpreter. Running under system `python3` breaks the
# daemon subprocess with ``ModuleNotFoundError: structlog`` because
# sys.executable gets propagated to subprocess.Popen.
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


def _resolve_python() -> str:
    """Return the Python interpreter to use for child processes.

    Picks the repo venv interpreter if it exists. Otherwise, checks that
    the current interpreter has the daemon's import-time requirement
    (``structlog``) and fails loudly with an actionable message if not.
    """
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)

    try:
        import structlog  # noqa: F401
    except ImportError:
        print(
            f"ERROR: running under {sys.executable} which lacks `structlog`.\n"
            f"       Re-run with the repo venv:\n"
            f"           .venv/bin/python -m tests.acceptance.automated ...",
            file=sys.stderr,
        )
        sys.exit(2)
    return sys.executable


def _ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def _reset_acceptance_artifacts() -> None:
    """Make `start` a fresh acceptance run by default."""
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    MONITOR_FILE.unlink(missing_ok=True)
    _ensure_dirs()


def _seed_run_start() -> None:
    """Persist the acceptance start boundary before daemon boot."""
    state = {
        "started_at": datetime.now(UTC).isoformat(),
        "current_day": 1,
        "simulated_hours_offset": 0,
        "messages": [],
        "phases_completed": [],
        "coverage": {},
        "errors": [],
        "thread_id": None,
        "kora_session_id": None,
    }
    SESSION_FILE.write_text(json.dumps(state, indent=2))


def _clean_before_daemon_start() -> None:
    """Clear acceptance-owned runtime state before daemon boot.

    The daemon rehydrates orchestration rows during startup. If cleanup
    waits until the harness server starts, stale worker tasks can already
    be live in memory even if their DB rows are later deleted. Keep this
    preflight here so every fresh `start` begins from the same DB state
    the daemon will observe.
    """
    from tests.acceptance._harness_server import (
        _clean_stale_projection_data,
        _clean_stale_test_data,
        _reset_daemon_runtime_files,
    )

    _reset_daemon_runtime_files(PROJECT_ROOT / "data")
    asyncio.run(_clean_stale_test_data(PROJECT_ROOT / "data" / "operational.db"))
    _clean_stale_projection_data(PROJECT_ROOT / "data" / "projection.db")
    _seed_projection_acceptance_fixtures()


def _seed_projection_acceptance_fixtures() -> None:
    """Seed minimal projection-only fixtures for lifecycle merge coverage."""
    db_path = PROJECT_ROOT / "data" / "projection.db"
    if not db_path.exists():
        try:
            from kora_v2.memory.projection import ProjectionDB

            projection_db = asyncio.run(ProjectionDB.initialize(db_path))
            asyncio.run(projection_db.close())
        except Exception:
            return
    try:
        import sqlite3

        with sqlite3.connect(str(db_path)) as db:
            has_memories = db.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='memories'"
            ).fetchone()
            if not has_memories:
                return
            has_entities = db.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='entities'"
            ).fetchone()
            if has_entities:
                existing = db.execute(
                    "SELECT id FROM entities "
                    "WHERE canonical_name='alex' AND entity_type='person' "
                    "LIMIT 1"
                ).fetchone()
                merged_metadata = json.dumps({
                    "source": "acceptance_fixture",
                    "merged_from": [
                        {
                            "id": "acceptance-alex-alias",
                            "name": "my partner",
                            "canonical_name": "partner",
                            "entity_type": "person",
                        }
                    ],
                })
                if existing:
                    db.execute(
                        "UPDATE entities SET metadata = ? WHERE id = ?",
                        (merged_metadata, existing[0]),
                    )
                else:
                    db.execute(
                        "INSERT OR IGNORE INTO entities "
                        "(id, name, canonical_name, entity_type, metadata) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            "acceptance-alex",
                            "Alex",
                            "alex",
                            "person",
                            merged_metadata,
                        ),
                    )
            memory_dir = ACCEPT_DIR / "memory" / "Long-Term"
            memory_dir.mkdir(parents=True, exist_ok=True)
            now = datetime.now(UTC).isoformat(timespec="seconds")
            duplicate_body = (
                "Jordan prefers local-first tools for the dashboard because "
                "privacy and low maintenance matter more than cloud polish."
            )
            fixture_notes = (
                (
                    "acceptance-dedup-local-first-a",
                    0.95,
                    ["Jordan", "Alex", "Mochi"],
                ),
                (
                    "acceptance-dedup-local-first-b",
                    0.2,
                    ["Jordan"],
                ),
            )
            for note_id, importance, entities in fixture_notes:
                note_path = memory_dir / f"{note_id}.md"
                entity_yaml = "".join(f"- {entity}\n" for entity in entities)
                note_path.write_text(
                    "---\n"
                    f"id: {note_id}\n"
                    "memory_type: reflective\n"
                    f"importance: {importance}\n"
                    "entities:\n"
                    f"{entity_yaml}"
                    "tags:\n"
                    "- acceptance-fixture\n"
                    "- dedup\n"
                    f"created_at: {now}\n"
                    f"updated_at: {now}\n"
                    "---\n\n"
                    f"{duplicate_body}\n",
                    encoding="utf-8",
                )
                db.execute(
                    "INSERT OR IGNORE INTO memories "
                    "(id, content, summary, importance, memory_type, "
                    "created_at, updated_at, entities, tags, source_path, "
                    "status, consolidated_into, merged_from, deleted_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, NULL, NULL)",
                    (
                        note_id,
                        duplicate_body,
                        None,
                        importance,
                        "reflective",
                        now,
                        now,
                        json.dumps(entities),
                        '["acceptance-fixture","dedup"]',
                        str(note_path),
                    ),
                )
            db.commit()
    except Exception:
        return


# ── Harness socket client ─────────────────────────────────────────────────────

async def _harness_send(request: dict[str, Any], timeout: float = 150.0) -> dict[str, Any]:
    """Send a JSON command to the harness server via Unix socket."""
    if not HARNESS_SOCK.exists():
        return {"error": "Harness server not running. Run: python3 -m tests.acceptance.automated start"}

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(
                str(HARNESS_SOCK),
                limit=16 * 1024 * 1024,
            ),
            timeout=5.0,
        )
    except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
        return {"error": f"Cannot connect to harness server: {e}"}
    except TimeoutError:
        return {"error": "Timeout connecting to harness server"}

    try:
        message = json.dumps(request) + "\n"
        writer.write(message.encode())
        await writer.drain()

        raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
        return json.loads(raw.decode())
    except TimeoutError:
        return {"error": f"Command timed out after {timeout}s"}
    except json.JSONDecodeError as e:
        return {"error": f"Bad response from harness: {e}"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def harness_cmd(request: dict[str, Any], timeout: float = 150.0) -> dict[str, Any]:
    """Synchronous wrapper for harness commands."""
    return asyncio.run(_harness_send(request, timeout=timeout))


# ── Kora daemon helpers ───────────────────────────────────────────────────────

def _read_lockfile() -> dict[str, Any] | None:
    if not LOCKFILE.exists():
        return None
    try:
        return json.loads(LOCKFILE.read_text())
    except Exception:
        return None


def _read_token() -> str | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return TOKEN_FILE.read_text().strip() or None
    except Exception:
        return None


def _health_probe(host: str, port: int, timeout: float = 2.0) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/v1/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _wait_for_daemon(timeout: float = 90.0) -> tuple[str, int]:
    """Wait for Kora daemon to become ready. Returns (host, port)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = _read_lockfile()
        if data:
            state = data.get("state")
            port = data.get("api_port")
            host = data.get("api_host", "127.0.0.1")
            if state in ("ready", "degraded") and port:
                if _health_probe(host, port):
                    return host, port
        time.sleep(0.5)
    raise TimeoutError(f"Kora daemon not ready after {timeout}s")


def _start_kora_daemon() -> int:
    """Spawn the Kora daemon process. Returns PID."""
    # Check if already running
    data = _read_lockfile()
    if data:
        pid = data.get("pid")
        state = data.get("state")
        if pid and state not in ("stopping", "error"):
            try:
                os.kill(pid, 0)
                print(f"  Kora daemon already running (PID {pid}, state={state})")
                return pid
            except ProcessLookupError:
                LOCKFILE.unlink(missing_ok=True)

    log_dir = PROJECT_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "daemon.log"

    proxy_vars = {
        "ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy",
        "HTTP_PROXY", "http_proxy",
    }
    env = {k: v for k, v in os.environ.items() if k not in proxy_vars}
    # Ensure tiktoken cache is available to the daemon subprocess
    if "TIKTOKEN_CACHE_DIR" not in env:
        import tempfile
        env["TIKTOKEN_CACHE_DIR"] = os.path.join(tempfile.gettempdir(), "data-gym-cache")
    # Default to trust_all auth for acceptance testing (auth relay tested explicitly on Day 3)
    env.setdefault("KORA_SECURITY__AUTH_MODE", "trust_all")
    # Production idle thresholds are 5 minutes / 1 hour. Acceptance runs
    # need to observe ACTIVE_IDLE -> LIGHT_IDLE -> DEEP_IDLE without an
    # hour-long wall-clock soak, while keeping production defaults intact.
    env.setdefault("KORA_ORCHESTRATION_ACTIVE_IDLE_SECONDS", "15")
    env.setdefault("KORA_ORCHESTRATION_LIGHT_IDLE_SECONDS", "45")
    # Keep forced wake-up late enough that acceptance can observe
    # ACTIVE_IDLE -> LIGHT_IDLE -> DEEP_IDLE before wake-up preparation
    # takes over.
    env.setdefault("KORA_ORCHESTRATION_WAKE_WINDOW_AFTER_IDLE_SECONDS", "60")
    env.setdefault("KORA_OPEN_DECISION_AGING_DAYS", "0")
    env.setdefault("KORA_CONTINUITY_REMINDER_WINDOW_HOURS", "36")
    env.setdefault("KORA_CONTINUITY_REMINDER_LOOKBACK_HOURS", "120")
    env.setdefault("KORA_ACCEPTANCE_ROUTINE_TRIGGER_SECONDS", "10")
    env.setdefault("KORA_ACCEPTANCE_SKILL_REFINEMENT_TRIGGER_SECONDS", "20")
    env.setdefault("KORA_ACCEPTANCE_WEEKLY_ADHD_TRIGGER_SECONDS", "20")
    env.setdefault("KORA_ACCEPTANCE_WAKE_PREP_TRIGGER_SECONDS", "10")
    env.setdefault("KORA_ACCEPTANCE_CONNECTION_TRIGGER_SECONDS", "20")
    env.setdefault("KORA_ORCHESTRATION_LIMITER_CAPACITY", "12")
    env.setdefault("KORA_ORCHESTRATION_LIMITER_CONVERSATION_RESERVE", "5")
    env.setdefault("KORA_ORCHESTRATION_LIMITER_NOTIFICATION_RESERVE", "3")
    env.setdefault("KORA_ORCHESTRATION_LIMITER_WINDOW_SECONDS", "20")
    env["KORA_MEMORY__KORA_MEMORY_PATH"] = str(ACCEPT_DIR / "memory")

    cmd = [_resolve_python(), "-m", "kora_v2", "--_daemon_internal"]
    with open(log_path, "a") as fh:
        proc = subprocess.Popen(
            cmd,
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(PROJECT_ROOT),
            start_new_session=True,
        )
    print(f"  Kora daemon spawned (PID {proc.pid}, python={cmd[0]})")
    return proc.pid


def _start_harness_server() -> int:
    """Spawn the harness background server. Returns PID."""
    # Check if already running
    if HARNESS_PID_FILE.exists():
        try:
            pid = int(HARNESS_PID_FILE.read_text().strip())
            os.kill(pid, 0)
            print(f"  Harness server already running (PID {pid})")
            return pid
        except (ProcessLookupError, ValueError):
            HARNESS_PID_FILE.unlink(missing_ok=True)

    env = dict(os.environ)
    env["KORA_ACCEPTANCE_DIR"] = str(ACCEPT_DIR)
    env["KORA_ACCEPTANCE_PRE_CLEANED"] = "1"
    env["KORA_MEMORY__KORA_MEMORY_PATH"] = str(ACCEPT_DIR / "memory")
    if "PYTHONPATH" not in env:
        env["PYTHONPATH"] = str(PROJECT_ROOT)

    log_path = OUTPUT_DIR / "harness.log"
    with open(log_path, "a") as fh:
        proc = subprocess.Popen(
            [_resolve_python(), "-m", "tests.acceptance._harness_server"],
            stdout=fh,
            stderr=fh,
            env=env,
            cwd=str(PROJECT_ROOT),
            start_new_session=True,
        )
    print(f"  Harness server spawned (PID {proc.pid})")

    # Wait for socket to appear
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if HARNESS_SOCK.exists():
            break
        time.sleep(0.2)
    else:
        print("  Warning: Harness socket did not appear. Check harness.log")

    return proc.pid


def _stop_kora_daemon() -> bool:
    """Send graceful shutdown to Kora."""
    data = _read_lockfile()
    if not data:
        return False
    port = data.get("api_port")
    host = data.get("api_host", "127.0.0.1")
    token = _read_token()
    if not port or not token:
        return False
    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://{host}:{port}/api/v1/daemon/shutdown",
            method="POST",
            data=b"",
        )
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def _set_daemon_auth_mode(mode: str) -> bool:
    """POST /api/v1/auth-mode to flip the daemon's auth_mode at runtime.

    The harness normally runs with ``auth_mode=trust_all`` so every tool
    auto-approves. For the auth relay test we need ``auth_mode=prompt``
    so the daemon actually emits ``auth_request`` over the WebSocket.
    This endpoint sets ``container.settings.security.auth_mode``
    directly — no daemon restart needed.
    """
    data = _read_lockfile()
    if not data:
        return False
    port = data.get("api_port")
    host = data.get("api_host", "127.0.0.1")
    token = _read_token()
    if not port or not token:
        return False
    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://{host}:{port}/api/v1/auth-mode",
            method="POST",
            data=json.dumps({"mode": mode}).encode(),
        )
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def _stop_harness_server() -> bool:
    """Stop the harness background server."""
    result = harness_cmd({"cmd": "stop"}, timeout=5.0)
    if "error" not in result:
        return True
    # Fallback: kill by PID
    if HARNESS_PID_FILE.exists():
        try:
            pid = int(HARNESS_PID_FILE.read_text().strip())
            os.kill(pid, 15)
            return True
        except Exception:
            pass
    return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True


# ── Coverage file management ───────────────────────────────────────────────────

def _init_coverage_file(fast: bool = False) -> None:
    """Create the coverage tracking file."""
    _ensure_dirs()
    from tests.acceptance.scenario.week_plan import COVERAGE_ITEMS, CoverageStatus

    lines = ["# Coverage Tracker", ""]

    # Active items first
    lines.append("## Active (testable in V2)")
    for item_id, item in sorted(COVERAGE_ITEMS.items()):
        if item.status == CoverageStatus.ACTIVE:
            lines.append(f"- [ ] {item_id}. {item.description}")

    # Deferred items
    lines.append("")
    lines.append("## Deferred (requires unimplemented V2 features)")
    for item_id, item in sorted(COVERAGE_ITEMS.items()):
        if item.status == CoverageStatus.DEFERRED:
            lines.append(f"- [~] {item_id}. {item.description} -- DEFERRED: {item.deferred_reason}")

    if fast:
        lines.extend(["", "## Mode: --fast (single-day smoke test, no idle phases)"])

    coverage_path = OUTPUT_DIR / "coverage.md"
    coverage_path.write_text("\n".join(lines) + "\n")
    print(f"  Coverage tracker: {coverage_path}")


# ── Command implementations ───────────────────────────────────────────────────

def cmd_start(fast: bool = False) -> None:
    """Start Kora daemon + harness server."""
    _reset_acceptance_artifacts()
    _seed_run_start()
    mode = " (--fast mode)" if fast else ""
    print(f"Starting Kora V2 acceptance test environment{mode}...")

    # 1. Clean acceptance-owned state before the daemon can rehydrate it.
    print("\n[1/4] Cleaning stale acceptance runtime state...")
    _clean_before_daemon_start()
    print("  Acceptance DB/runtime state cleared.")

    # 2. Start Kora daemon
    print("\n[2/4] Starting Kora daemon...")
    _start_kora_daemon()

    # 3. Wait for daemon ready
    print("[3/4] Waiting for daemon to be ready...")
    try:
        host, port = _wait_for_daemon(timeout=90.0)
        token = _read_token()
        print(f"  Daemon ready at {host}:{port}")
        print(f"  API token: {token}")
        _seed_projection_acceptance_fixtures()
        print("  Projection acceptance fixtures seeded.")
    except TimeoutError as e:
        print(f"  ERROR: {e}")
        print("  Check data/logs/daemon.log for details")
        sys.exit(1)

    # 4. Start harness server
    print("[4/4] Starting harness server...")
    _start_harness_server()

    # 5. Verify harness connection
    time.sleep(1.0)
    result = harness_cmd({"cmd": "ping"}, timeout=10.0)
    if "error" in result:
        print(f"  Warning: Harness ping failed: {result['error']}")
    else:
        print(f"  Harness server ready. Kora session: {result.get('session_id', '?')}")

    # 6. Init coverage file
    _init_coverage_file(fast=fast)

    # 7. Print plan info
    if fast:
        from tests.acceptance.scenario.week_plan import ACTIVE_ITEMS, FAST_PLAN
        phase_count = sum(len(d["phases"]) for d in FAST_PLAN.values())
        print(f"\n  FAST MODE: {phase_count} phases, {len(ACTIVE_ITEMS)} active coverage items")
        print("  No idle phases. Estimated run time: ~10 minutes.")
    else:
        from tests.acceptance.scenario.week_plan import ACTIVE_ITEMS, DEFERRED_ITEMS, WEEK_PLAN
        phase_count = sum(len(d["phases"]) for d in WEEK_PLAN.values())
        print(f"\n  FULL MODE: {phase_count} phases across {len(WEEK_PLAN)} lived-week days")
        print(f"  Active coverage items: {len(ACTIVE_ITEMS)}")
        print(f"  Deferred coverage items: {len(DEFERRED_ITEMS)}")

    print("\nAcceptance test environment ready.")
    print(f"  Output dir: {OUTPUT_DIR}")
    print(f"  Session:    {SESSION_FILE}")
    print(f"  Monitor:    {MONITOR_FILE}")


def cmd_stop() -> None:
    """Stop harness server + Kora daemon."""
    print("Stopping acceptance test environment...")

    print("  Stopping harness server...")
    if _stop_harness_server():
        print("  Harness server stopped.")
    else:
        print("  Harness server was not running.")

    print("  Stopping Kora daemon...")
    if _stop_kora_daemon():
        print("  Kora daemon shutdown requested.")
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            data = _read_lockfile()
            if not data:
                break
            state = data.get("state", "")
            pid = data.get("pid")
            if state == "stopping" or (pid and not _pid_alive(pid)):
                break
            time.sleep(0.5)
        print("  Kora daemon stopped.")
    else:
        print("  Kora daemon was not running.")


def cmd_send(message: str) -> None:
    """Send a message to Kora and print the response."""
    print(f"\n[Jordan] {message}")
    print("[Kora] thinking...", end="\r")

    result = harness_cmd(
        {"cmd": "send", "message": message, "timeout": 600.0},
        timeout=660.0,
    )

    if "error" in result and result["error"]:
        print(f"[ERROR] {result['error']}")
        sys.exit(1)

    response = result.get("response", "(empty response)")
    trace_id = result.get("trace_id", "")
    latency = result.get("latency_ms", 0)
    tool_count = result.get("tool_call_count", 0)
    tool_calls = result.get("tool_calls", [])
    compaction = result.get("compaction_tier")
    tokens = result.get("token_count")

    print(f"[Kora] {response}")
    meta_parts = [
        f"trace:{trace_id[:8] if trace_id else '?'}",
        f"{latency}ms",
        f"tools:{tool_count}",
    ]
    if compaction and compaction != "none":
        meta_parts.append(f"compaction:{compaction}")
    if tokens:
        meta_parts.append(f"tokens:{tokens}")
    print(f"  [{'] ['.join(meta_parts)}]")
    if tool_calls:
        print(f"  Tools used: {', '.join(tool_calls[:5])}")

    print(f"\n__JSON__:{json.dumps(result, default=str)}")


def cmd_status() -> None:
    """Print daemon status."""
    result = harness_cmd({"cmd": "status"})
    if "error" in result:
        print(f"Status error: {result['error']}")
        sys.exit(1)
    print(json.dumps(result, indent=2, default=str))


def cmd_snapshot(name: str) -> None:
    """Capture state snapshot."""
    result = harness_cmd({"cmd": "snapshot", "name": name})
    if "error" in result:
        print(f"Snapshot error: {result['error']}")
        sys.exit(1)
    print(f"Snapshot saved: {result['path']}")


def cmd_diff(snap1: str, snap2: str) -> None:
    """Show diff between two snapshots."""
    result = harness_cmd({"cmd": "diff", "snap1": snap1, "snap2": snap2})
    if "error" in result:
        print(f"Diff error: {result['error']}")
        sys.exit(1)
    print(result.get("diff", "(empty diff)"))


def cmd_idle_wait(
    min_soak: int, timeout: int, manifest: str | None = None,
) -> None:
    """Wait for idle quiescence while monitoring autonomous work.

    If ``manifest`` is supplied, the harness evaluates the named
    idle-soak manifest against a before/after full-state snapshot and
    reports pass/fail per check.
    """
    print(f"Idle wait: min_soak={min_soak}s timeout={timeout}s")
    if manifest:
        print(f"  (manifest: {manifest})")
    print("  (V2: monitoring health + autonomous runtime state)")

    payload: dict[str, Any] = {
        "cmd": "idle-wait", "min_soak": min_soak, "timeout": timeout,
    }
    if manifest:
        payload["manifest"] = manifest

    result = harness_cmd(payload, timeout=float(timeout + 30))
    if "error" in result:
        print(f"  Idle-wait error: {result['error']}")
        sys.exit(1)

    elapsed = result.get("elapsed", 0)
    polls = result.get("polls", 0)
    errors = result.get("errors", 0)
    items_delta = result.get("items_delta", 0)
    checkpoints_delta = result.get("checkpoints_delta", 0)
    print(f"  Idle-wait complete: elapsed={elapsed:.0f}s polls={polls} health_errors={errors}")
    if items_delta or checkpoints_delta:
        print(f"  Autonomous activity: items_delta={items_delta:+d} checkpoints_delta={checkpoints_delta:+d}")
    if result.get("timeout"):
        print("  Note: hit timeout limit")
    elif result.get("quiescent"):
        print("  Daemon quiescent and healthy")

    mres = result.get("manifest")
    if mres:
        if mres.get("error"):
            print(f"  Manifest error: {mres['error']}")
        else:
            status = "PASS" if mres.get("passed") else "FAIL"
            print(f"  Manifest [{mres.get('manifest_name')}] {status}")
            print(f"    {mres.get('summary', '?')}")
            if mres.get("missing"):
                print("    missing: " + ", ".join(mres["missing"]))


def cmd_advance(hours: float) -> None:
    """Advance simulated time."""
    result = harness_cmd({"cmd": "advance", "hours": hours})
    if "error" in result:
        print(f"Advance error: {result['error']}")
        sys.exit(1)
    total = result.get("total_offset", 0)
    print(f"  Time advanced +{hours}h. Total simulated offset: +{total:.1f}h")


def cmd_restart() -> None:
    """Restart daemon + reconnect harness."""
    print("Restarting Kora daemon...")

    r = harness_cmd({"cmd": "snapshot", "name": "pre_restart"})
    print(f"  Pre-restart snapshot: {r.get('path', '?')}")

    print("  Stopping harness server...")
    _stop_harness_server()
    time.sleep(1)

    print("  Stopping Kora daemon...")
    _stop_kora_daemon()
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if not _read_lockfile():
            break
        data = _read_lockfile()
        if data:
            pid = data.get("pid")
            if not pid or not _pid_alive(pid):
                break
        time.sleep(0.5)
    LOCKFILE.unlink(missing_ok=True)
    print("  Daemon stopped.")

    print("  Starting Kora daemon...")
    _start_kora_daemon()
    try:
        host, port = _wait_for_daemon(timeout=90.0)
        print(f"  Daemon ready at {host}:{port}")
    except TimeoutError as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    print("  Starting harness server...")
    _start_harness_server()
    time.sleep(2.0)

    result = harness_cmd({"cmd": "ping"}, timeout=10.0)
    if "error" in result:
        print(f"  Warning: Harness ping failed: {result['error']}")
    else:
        print(f"  Harness ready. New Kora session: {result.get('session_id', '?')}")

    r = harness_cmd({"cmd": "snapshot", "name": "post_restart"})
    print(f"  Post-restart snapshot: {r.get('path', '?')}")


def cmd_test_auth() -> None:
    """Exercise auth relay deny + approve with disposable probes.

    Two things need to happen for this to actually exercise the auth relay:
    1. The daemon must be in ``auth_mode=prompt`` so it emits ``auth_request``
       events. The harness start command defaults to ``trust_all``, so we
       flip it here via the /auth-mode endpoint.
    2. The harness must queue a deny-first decision for the next auth_request
       callback.
    """
    if not _set_daemon_auth_mode("prompt"):
        print("Error: could not set daemon auth_mode to 'prompt' "
              "(is the daemon running?)")
        sys.exit(1)

    result = harness_cmd({"cmd": "test-auth"})
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    print("Auth test mode ENABLED; running deny/approve probes.")
    print("  Daemon auth_mode = prompt (asks for each tool).")
    probe = (
        "quick auth probe: please write the word ok to "
        "/tmp/claude/kora_acceptance/auth_probe.txt"
    )
    try:
        first = harness_cmd(
            {"cmd": "send", "message": probe, "timeout": 600.0},
            timeout=660.0,
        )
        if first.get("error"):
            print(f"  First auth probe error: {first['error']}")
            sys.exit(1)
        print(
            "  First probe tools: "
            + ", ".join(first.get("tool_calls", [])[:5])
        )
        second = harness_cmd(
            {"cmd": "send", "message": "try the same auth probe again: " + probe,
             "timeout": 600.0},
            timeout=660.0,
        )
        if second.get("error"):
            print(f"  Second auth probe error: {second['error']}")
            sys.exit(1)
        print(
            "  Second probe tools: "
            + ", ".join(second.get("tool_calls", [])[:5])
        )
    finally:
        if not _set_daemon_auth_mode("trust_all"):
            print("Warning: could not restore daemon auth_mode to 'trust_all'")
        reset = harness_cmd({"cmd": "test-auth-reset"})
        if "error" in reset:
            print(f"Warning: auth harness reset failed: {reset['error']}")
    print("Auth relay probe complete. Daemon auth_mode = trust_all.")


def cmd_test_auth_reset() -> None:
    """Reset to auto-approve mode.

    Flips the daemon back to ``auth_mode=trust_all`` (so the rest of the
    test runs without prompts) and clears the harness's deny-first state.
    """
    if not _set_daemon_auth_mode("trust_all"):
        print("Warning: could not restore daemon auth_mode to 'trust_all'")

    result = harness_cmd({"cmd": "test-auth-reset"})
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    print("Auth test mode DISABLED. Daemon auth_mode = trust_all, auto-approve restored.")


def cmd_test_error() -> None:
    """Run error recovery tests."""
    print("Running error recovery tests...")
    result = harness_cmd({"cmd": "test-error"}, timeout=600.0)
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)

    results = result.get("results", [])
    all_survived = result.get("all_survived", False)

    for r in results:
        status = "PASS" if r.get("survived") else "FAIL"
        test_name = r.get("test", "?")
        detail = r.get("error") or r.get("response", "")[:80]
        print(f"  [{status}] {test_name}: {detail}")

    if all_survived:
        print("\nAll error recovery tests passed. Session survived.")
    else:
        print("\nSome error recovery tests FAILED.")


def cmd_compaction_status() -> None:
    """Show compaction events detected during the test."""
    result = harness_cmd({"cmd": "compaction-status"})
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)

    detected = result.get("compaction_detected", False)
    count = result.get("event_count", 0)
    events = result.get("events", [])

    if detected:
        print(f"Compaction detected: {count} event(s)")
        for ev in events:
            print(f"  tier={ev.get('tier')} tokens={ev.get('token_count')} at {ev.get('ts', '?')}")
    else:
        print("No compaction events detected yet.")


def cmd_skill_gating_check() -> None:
    """Verify skill activation exposes distinct tool sets."""
    result = harness_cmd({"cmd": "skill-gating-check"})
    if result.get("error"):
        print(f"Error: {result['error']}")
        sys.exit(1)
    print("Skill gating:", "PASS" if result.get("passed") else "FAIL")
    for name, case in sorted((result.get("cases") or {}).items()):
        print(f"  {name}: skills={case.get('skills', [])}")


def cmd_life_management_check() -> None:
    """Query life management DB tables and display results."""
    print("Querying life management records...")
    result = harness_cmd({"cmd": "life-management-check"})
    if not result.get("available"):
        print(f"  Not available: {result.get('error', 'unknown')}")
        return

    print(f"\n  Medications logged: {result.get('medication_count', 0)}")
    for med in result.get("medication_log", []):
        print(f"    - {med.get('medication_name')} {med.get('dose', '')} at {med.get('taken_at', '?')}")

    print(f"\n  Meals logged: {result.get('meal_count', 0)}")
    for meal in result.get("meal_log", []):
        print(f"    - [{meal.get('meal_type', '?')}] {meal.get('description', '?')[:80]}")

    print(f"\n  Reminders: {result.get('reminder_count', 0)}")
    for rem in result.get("reminders", []):
        print(f"    - [{rem.get('status', '?')}] {rem.get('title', '?')}")

    print(f"\n  Quick notes: {result.get('quick_note_count', 0)}")
    for note in result.get("quick_notes", []):
        print(f"    - {note.get('content', '?')[:80]}")

    print(f"\n  Focus blocks: {result.get('focus_block_count', 0)}")
    for fb in result.get("focus_blocks", []):
        ended = fb.get("ended_at") or "still open"
        print(f"    - [{fb.get('label', '?')}] started={fb.get('started_at', '?')} ended={ended}")

    # Phase 8 enrichment — "did things actually happen?"
    mem = result.get("memory_lifecycle") or {}
    if mem and not mem.get("error"):
        print("\n  Memory lifecycle:")
        for k in ("memories", "user_model_facts", "entities"):
            entry = mem.get(k) or {}
            total = entry.get("total", 0)
            by_status = entry.get("by_status", {}) or {}
            status_str = " ".join(
                f"{s}={c}" for s, c in sorted(by_status.items())
            ) or "(no status breakdown)"
            print(f"    {k}: total={total} [{status_str}]")
        sessions = mem.get("sessions") or {}
        if sessions and not sessions.get("error"):
            print(
                f"    session_transcripts: total={sessions.get('transcripts_total', 0)} "
                f"processed={sessions.get('processed', 0)} "
                f"unprocessed={sessions.get('unprocessed', 0)}"
            )
        sq = mem.get("signal_queue") or {}
        if sq and not sq.get("error"):
            sq_status = " ".join(
                f"{s}={c}" for s, c in sorted((sq.get("by_status") or {}).items())
            )
            print(f"    signal_queue: total={sq.get('total', 0)} [{sq_status}]")

    vault = result.get("vault_snapshot") or {}
    if vault and vault.get("exists"):
        counts = vault.get("counts", {})
        print(
            f"\n  Vault: notes={counts.get('total_notes', 0)} "
            f"working_docs={vault.get('working_docs_count', 0)} "
            f"hierarchy_present={vault.get('folder_hierarchy_present', False)}"
        )

    rem = result.get("reminder_delivery") or {}
    if rem and not rem.get("error"):
        by_status = rem.get("by_status") or {}
        status_str = " ".join(f"{s}={c}" for s, c in sorted(by_status.items()))
        slip = rem.get("mean_delivery_slip_seconds")
        slip_str = f"{slip:.1f}s" if isinstance(slip, (int, float)) else "n/a"
        print(
            f"\n  Reminder delivery: total={rem.get('total', 0)} "
            f"[{status_str}] mean_slip={slip_str}"
        )

    notif = result.get("notifications_summary") or {}
    if notif and not notif.get("error"):
        by_tier = notif.get("by_tier") or {}
        tier_str = " ".join(f"{t}={c}" for t, c in sorted(by_tier.items()))
        print(f"\n  Notifications: total={notif.get('total', 0)} [{tier_str}]")


def cmd_tool_usage_summary() -> None:
    """Display tool usage summary from conversation history."""
    print("Analyzing tool usage from conversation...")
    result = harness_cmd({"cmd": "tool-usage-summary"})
    if "error" in result:
        print(f"  Error: {result['error']}")
        return

    print(f"\n  Total tool calls: {result.get('total_tool_calls', 0)}")
    print(f"  Unique tools: {result.get('unique_tools', 0)}")

    # ``Orchestration`` replaces the retired ``Autonomous`` (start_autonomous
    # was removed in Phase 7.5). ``Pipelines`` is an AT3 placeholder — pipelines
    # fire from triggers, not tool calls, so the answer comes from the
    # pipeline_instances table. AT3 will populate it.
    cats = [
        ("Life management", "life_management_tools_used"),
        ("Filesystem", "filesystem_tools_used"),
        ("MCP (web)", "mcp_tools_used"),
        ("Orchestration", "orchestration_tools_used"),
        ("Memory", "memory_tools_used"),
        ("Pipelines", "pipelines_fired"),
    ]
    for label, key in cats:
        tools = result.get(key, [])
        if tools:
            print(f"\n  {label}: {', '.join(tools)}")
        elif label == "Pipelines":
            print(f"\n  {label}: (AT3 will populate this from pipeline_instances)")
        else:
            print(f"\n  {label}: (none used)")

    tool_counts = result.get("tool_counts", {})
    if tool_counts:
        print("\n  Call counts:")
        for name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
            print(f"    {name}: {count}")


def cmd_orchestration_status() -> None:
    """Print a formatted orchestration-status summary."""
    result = harness_cmd({"cmd": "orchestration-status"})
    if "error" in result and not result.get("available"):
        print(f"Orchestration status error: {result['error']}")
        sys.exit(1)
    if not result.get("available"):
        print("Orchestration tables not available.")
        return

    print("Orchestration status:")

    pi = result.get("pipeline_instances") or {}
    if pi.get("error"):
        print(f"  pipeline_instances: {pi['error']}")
    else:
        print(f"  Pipeline instances: total={pi.get('total', 0)}")
        for state, cnt in sorted((pi.get("by_state") or {}).items()):
            print(f"    [state={state}] {cnt}")
        for name, cnt in sorted((pi.get("by_name") or {}).items()):
            print(f"    [name={name}] {cnt}")

    wt = result.get("worker_tasks") or {}
    if wt.get("error"):
        print(f"  worker_tasks: {wt['error']}")
    else:
        print(
            f"  Worker tasks: total={wt.get('total', 0)} "
            f"active={wt.get('active_count', 0)}"
        )

    wl = result.get("work_ledger") or {}
    if wl.get("error"):
        print(f"  work_ledger: {wl['error']}")
    else:
        print(f"  Work ledger events: total={wl.get('total', 0)}")
        for et, cnt in sorted((wl.get("by_event_type") or {}).items()):
            print(f"    {et}: {cnt}")

    ss = result.get("system_state_log") or {}
    if not ss.get("error"):
        print(
            f"  Current phase: {ss.get('current_phase', '?')} "
            f"(transitions: {ss.get('transitions_total', 0)})"
        )

    rl = result.get("request_limiter") or {}
    if not rl.get("error"):
        print(
            f"  Request limiter: total={rl.get('total_requests_logged', 0)} "
            f"in_window={rl.get('in_window', 0)} "
            f"window_seconds={rl.get('window_seconds', 0)}"
        )


def cmd_pipeline_history(limit: int = 20) -> None:
    """Print recent pipeline_instances with durations."""
    result = harness_cmd({"cmd": "pipeline-history", "limit": limit})
    if not result.get("available"):
        print(f"Pipeline history unavailable: {result.get('error', 'unknown')}")
        return
    count = result.get("count", 0)
    print(f"Pipeline history ({count} recent):")
    for p in result.get("pipelines", []):
        dur = p.get("duration_s")
        dur_s = f"{dur:.1f}s" if isinstance(dur, (int, float)) else "–"
        completion = p.get("completion_reason") or ""
        pid = (p.get("id") or "")[:8]
        print(
            f"  [{p.get('state', '?')}] {p.get('pipeline_name', '?')}"
            f" ({pid}) started={p.get('started_at', '?')} dur={dur_s} {completion}"
        )


def cmd_working_docs() -> None:
    """List working docs under _KoraMemory/Inbox/."""
    result = harness_cmd({"cmd": "working-docs"})
    if not result.get("available"):
        print(f"Vault not available at {result.get('root', '?')}")
        return
    docs = result.get("working_docs", [])
    print(f"Working docs in {result.get('root')}: {len(docs)}")
    for d in docs:
        size = d.get("size_bytes", 0)
        print(
            f"  [{d.get('status', '?')}] {d.get('pipeline_name', '?')} "
            f"{size}B mtime={d.get('mtime', '?')}"
        )
        print(f"    {d.get('path')}")


def cmd_edit_working_doc(text: str) -> None:
    """Append a Current Plan item to the newest active working doc."""
    result = harness_cmd({"cmd": "edit-working-doc", "text": text})
    if not result.get("ok"):
        print(f"Error: {result.get('error', 'edit failed')}")
        sys.exit(1)
    status = "added" if result.get("added") else "already present"
    print(f"Working doc item {status}: {result.get('path')}")


def cmd_notifications(limit: int = 20) -> None:
    """Print recent notifications with tier and reason."""
    result = harness_cmd({"cmd": "notifications", "limit": limit})
    if not result.get("available"):
        print(f"Notifications unavailable: {result.get('error', 'unknown')}")
        return
    if result.get("error"):
        print(f"  Notifications table: {result['error']}")
        return
    total = result.get("total", 0)
    print(f"Notifications (total={total}):")
    by_tier = result.get("by_tier") or {}
    if by_tier:
        tier_line = " ".join(f"{k}={v}" for k, v in sorted(by_tier.items()))
        print(f"  Tiers: {tier_line}")
    by_reason = result.get("by_reason") or {}
    if by_reason:
        reason_line = " ".join(f"{k}={v}" for k, v in sorted(by_reason.items()))
        print(f"  Reasons: {reason_line}")
    for n in result.get("recent", []):
        tier = n.get("delivery_tier") or "?"
        reason = n.get("reason") or "–"
        print(
            f"  [{tier}/{n.get('priority', '?')}] {reason} "
            f"at {n.get('delivered_at', '?')}: "
            f"{(n.get('content') or '')[:80]}"
        )


def cmd_insights(limit: int = 20) -> None:
    """Print recent INSIGHT_AVAILABLE events (placeholder)."""
    result = harness_cmd({"cmd": "insights", "limit": limit})
    if not result.get("available"):
        print(f"Insights unavailable: {result.get('error', 'unknown')}")
        return
    print("Insights:")
    print(f"  Persisted: {result.get('persisted', False)}")
    if result.get("note"):
        print(f"  Note: {result['note']}")
    events = result.get("events") or []
    if events:
        print(f"  Recent events ({len(events)}):")
        for e in events:
            print(f"    {e.get('event_type', '?')} at {e.get('timestamp', '?')}")
    else:
        print("  (no persisted insight events found)")


def cmd_phase_history(hours: int = 24) -> None:
    """Print SystemStatePhase transitions over the last N hours."""
    result = harness_cmd({"cmd": "phase-history", "hours": hours})
    if not result.get("available"):
        print(f"Phase history unavailable: {result.get('error', 'unknown')}")
        return
    count = result.get("count", 0)
    print(f"Phase transitions (last {result.get('hours', hours)}h, {count} rows):")
    for t in result.get("transitions", []):
        reason = t.get("reason") or ""
        print(
            f"  {t.get('transitioned_at', '?')}  "
            f"{t.get('previous_phase', '?')} → {t.get('new_phase', '?')}"
            f" {reason}"
        )


def cmd_vault_snapshot() -> None:
    """Print vault file counts, folder hierarchy, working-doc count."""
    result = harness_cmd({"cmd": "vault-snapshot"})
    if not result.get("exists"):
        print(f"Vault not found at {result.get('root', '?')}")
        return
    print(f"Vault snapshot at {result.get('root')}:")
    counts = result.get("counts") or {}
    for k in sorted(counts):
        print(f"  {k}: {counts[k]}")
    wd = result.get("working_docs") or []
    print(f"  working docs: {len(wd)}")
    density = result.get("wikilink_density") or {}
    print(
        f"  wikilinks: {density.get('notes_with_wikilinks', 0)} notes, "
        f"{density.get('total_wikilinks', 0)} total"
    )
    print(f"  folder hierarchy present: {result.get('folder_hierarchy_present', False)}")
    if result.get("truncated"):
        print(f"  (walk truncated at {result.get('files_walked')} files)")


def cmd_soak_manifest(phase: str) -> None:
    """Run the named soak manifest against the current snapshot.

    Prints PASS/FAIL plus the per-check breakdown. For a meaningful
    before/after comparison, prefer ``idle-wait --manifest <phase>`` —
    this standalone command only captures the *current* state and
    compares against an empty before-snapshot.
    """
    result = harness_cmd(
        {"cmd": "soak-manifest", "phase": phase},
        timeout=60.0,
    )
    if "error" in result and "result" not in result:
        print(f"Soak manifest error: {result['error']}")
        if result.get("available"):
            print("  Available manifests: " + ", ".join(result["available"]))
        sys.exit(1)

    manifest = result.get("manifest") or {}
    res = result.get("result") or {}
    passed = res.get("passed", False)
    status = "PASS" if passed else "FAIL"
    print(f"Soak manifest [{phase}] {status}")
    print(f"  {res.get('summary', '?')}")
    print(
        f"  min_soak={manifest.get('min_soak_seconds', '?')}s "
        f"timeout={manifest.get('timeout_seconds', '?')}s"
    )
    checks = res.get("checks") or {}
    if checks:
        print("  Checks:")
        for key, ok in sorted(checks.items()):
            marker = "✓" if ok else "✗"
            print(f"    [{marker}] {key}")
    if res.get("missing"):
        print("  Missing: " + ", ".join(res["missing"]))
    if res.get("unexpected"):
        print("  Unexpected: " + ", ".join(res["unexpected"]))


def cmd_phase_gate(phase_name: str) -> None:
    """Run the phase-gate check for a named phase."""
    result = harness_cmd(
        {"cmd": "phase-gate", "phase_name": phase_name},
        timeout=60.0,
    )
    if "error" in result:
        print(f"Phase gate error: {result['error']}")
        known = result.get("known_phases") or []
        if known:
            print(f"  Known phases: {', '.join(known)}")
        sys.exit(1)
    res = result.get("result") or {}
    checked = res.get("items_checked") or []
    satisfied = res.get("items_satisfied") or []
    missing = res.get("items_missing") or []
    details = res.get("details") or {}
    print(f"Phase gate [{phase_name}]:")
    print(
        f"  items_checked={len(checked)} "
        f"satisfied={len(satisfied)} missing={len(missing)}"
    )
    if satisfied:
        print("  Satisfied:")
        for i in satisfied:
            key = str(i)
            print(f"    [x] {i}: {details.get(key) or details.get(i) or ''}")
    if missing:
        print("  Missing:")
        for i in missing:
            key = str(i)
            print(f"    [ ] {i}: {details.get(key) or details.get(i) or ''}")


def cmd_benchmarks() -> None:
    """Collect and print the current benchmark summary."""
    result = harness_cmd({"cmd": "benchmarks"}, timeout=60.0)
    if "error" in result:
        print(f"Benchmarks error: {result['error']}")
        sys.exit(1)
    bench = result.get("json") or {}
    print("Benchmarks:")
    print(f"  response_count: {bench.get('response_count', 0)}")
    print(f"  latency_p50_ms: {bench.get('response_latency_p50_ms', 0)}")
    print(f"  latency_p95_ms: {bench.get('response_latency_p95_ms', 0)}")
    print(
        f"  tokens: prompt={bench.get('total_prompt_tokens', 0)} "
        f"completion={bench.get('total_completion_tokens', 0)} "
        f"mean_per_response={bench.get('tokens_per_response_mean', 0)}"
    )
    print(
        f"  remaining_budget_fraction: "
        f"{bench.get('remaining_budget_fraction', 1.0)}"
    )
    pipes = bench.get("pipeline_fires_by_name") or {}
    if pipes:
        print("  pipelines:")
        for name, count in sorted(pipes.items(), key=lambda x: -x[1]):
            print(f"    {name}: {count}")
    print(
        f"  pipeline_success={bench.get('pipeline_success_count', 0)} "
        f"fail={bench.get('pipeline_fail_count', 0)}"
    )
    notifs = bench.get("notifications_by_tier") or {}
    if notifs:
        tier_str = " ".join(f"{k}={v}" for k, v in sorted(notifs.items()))
        print(f"  notifications_by_tier: {tier_str}")
    print(
        f"  memory deltas: created={bench.get('memories_created', 0)} "
        f"consolidated={bench.get('memories_consolidated', 0)} "
        f"dedup_merged={bench.get('memories_dedup_merged', 0)}"
    )
    print(
        f"  vault: notes={bench.get('vault_notes_total', 0)} "
        f"wikilinks={bench.get('vault_wikilinks_total', 0)} "
        f"entities={bench.get('vault_entity_pages', 0)} "
        f"mocs={bench.get('vault_moc_pages', 0)} "
        f"working={bench.get('vault_working_docs_active', 0)}"
    )
    dwell = bench.get("phase_dwell_seconds") or {}
    if dwell:
        print("  phase dwell (s):")
        for k, v in sorted(dwell.items()):
            print(f"    {k}: {v}")


def cmd_event_tail(seconds: int = 10) -> None:
    """Subscribe to the daemon's event stream for N seconds and print events."""
    # Harness tail lasts `seconds`, so give the command at least that much
    # plus a small buffer on the socket read.
    result = harness_cmd(
        {"cmd": "event-tail", "seconds": seconds},
        timeout=float(seconds + 30),
    )
    if "error" in result:
        print(f"Event tail error: {result['error']}")
        sys.exit(1)
    events = result.get("events") or []
    print(
        f"Event tail: {result.get('seconds', seconds)}s — "
        f"{result.get('event_count', len(events))} event(s) captured"
    )
    for ev in events:
        ev_type = ev.get("type", "?")
        content = (ev.get("content") or "")
        if isinstance(content, str) and len(content) > 120:
            content = content[:117] + "..."
        print(f"  [{ev.get('ts', '?')}] {ev_type}: {content}")


def cmd_monitor() -> None:
    """Print current monitor summary."""
    if MONITOR_FILE.exists():
        print(MONITOR_FILE.read_text())
    else:
        result = harness_cmd({"cmd": "monitor"})
        print(result.get("content", "(no monitor data)"))


def cmd_report() -> None:
    """Generate and print final report."""
    result = harness_cmd({"cmd": "report"}, timeout=240.0)
    if "error" in result:
        print(f"Report error: {result['error']}")
        sys.exit(1)
    path = result.get("path", "")
    print(f"Report generated: {path}")
    if path and Path(path).exists():
        print("\n" + Path(path).read_text())


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0]

    if cmd == "start":
        fast = "--fast" in args
        cmd_start(fast=fast)

    elif cmd == "stop":
        cmd_stop()

    elif cmd == "send":
        if len(args) < 2:
            print("Usage: automated.py send 'message'")
            sys.exit(1)
        cmd_send(args[1])

    elif cmd == "status":
        cmd_status()

    elif cmd == "snapshot":
        if len(args) < 2:
            print("Usage: automated.py snapshot <name>")
            sys.exit(1)
        cmd_snapshot(args[1])

    elif cmd == "diff":
        if len(args) < 3:
            print("Usage: automated.py diff <snap1> <snap2>")
            sys.exit(1)
        cmd_diff(args[1], args[2])

    elif cmd == "idle-wait":
        import argparse as _ap
        parser = _ap.ArgumentParser()
        parser.add_argument("--min-soak", type=int, default=15)
        parser.add_argument("--timeout", type=int, default=30)
        parser.add_argument("--manifest", type=str, default=None)
        parsed = parser.parse_args(args[1:])
        cmd_idle_wait(parsed.min_soak, parsed.timeout, manifest=parsed.manifest)

    elif cmd == "advance":
        if len(args) < 2:
            print("Usage: automated.py advance <hours>")
            sys.exit(1)
        cmd_advance(float(args[1]))

    elif cmd == "restart":
        cmd_restart()

    elif cmd == "test-auth":
        cmd_test_auth()

    elif cmd == "test-auth-reset":
        cmd_test_auth_reset()

    elif cmd == "test-error":
        cmd_test_error()

    elif cmd == "compaction-status":
        cmd_compaction_status()

    elif cmd == "skill-gating-check":
        cmd_skill_gating_check()

    elif cmd == "life-management-check":
        cmd_life_management_check()

    elif cmd == "tool-usage-summary":
        cmd_tool_usage_summary()

    elif cmd == "monitor":
        cmd_monitor()

    elif cmd == "report":
        cmd_report()

    elif cmd == "orchestration-status":
        cmd_orchestration_status()

    elif cmd == "pipeline-history":
        import argparse as _ap
        parser = _ap.ArgumentParser()
        parser.add_argument("--limit", type=int, default=20)
        parsed = parser.parse_args(args[1:])
        cmd_pipeline_history(parsed.limit)

    elif cmd == "working-docs":
        cmd_working_docs()

    elif cmd == "edit-working-doc":
        text = " ".join(args[1:]) or "user-added acceptance plan item"
        cmd_edit_working_doc(text)

    elif cmd == "notifications":
        import argparse as _ap
        parser = _ap.ArgumentParser()
        parser.add_argument("--limit", type=int, default=20)
        parsed = parser.parse_args(args[1:])
        cmd_notifications(parsed.limit)

    elif cmd == "insights":
        import argparse as _ap
        parser = _ap.ArgumentParser()
        parser.add_argument("--limit", type=int, default=20)
        parsed = parser.parse_args(args[1:])
        cmd_insights(parsed.limit)

    elif cmd == "phase-history":
        import argparse as _ap
        parser = _ap.ArgumentParser()
        parser.add_argument("--hours", type=int, default=24)
        parsed = parser.parse_args(args[1:])
        cmd_phase_history(parsed.hours)

    elif cmd == "vault-snapshot":
        cmd_vault_snapshot()

    elif cmd == "soak-manifest":
        if len(args) < 2:
            print("Usage: automated.py soak-manifest <phase>")
            sys.exit(1)
        cmd_soak_manifest(args[1])

    elif cmd == "phase-gate":
        if len(args) < 2:
            print("Usage: automated.py phase-gate <phase_name>")
            sys.exit(1)
        cmd_phase_gate(args[1])

    elif cmd == "benchmarks":
        cmd_benchmarks()

    elif cmd == "event-tail":
        import argparse as _ap
        parser = _ap.ArgumentParser()
        parser.add_argument("--seconds", type=int, default=10)
        parsed = parser.parse_args(args[1:])
        cmd_event_tail(parsed.seconds)

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
