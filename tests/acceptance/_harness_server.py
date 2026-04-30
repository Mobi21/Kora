"""Kora V2 Acceptance Test — Background Harness Server.

Runs as a background process (spawned by `automated.py start`).
Maintains a persistent WebSocket connection to the Kora daemon so that
multi-turn conversation continuity is preserved across CLI invocations.

Communication: Unix domain socket at $ACCEPT_DIR/harness.sock
Protocol: newline-delimited JSON (request then response)

Start: python3 -m tests.acceptance._harness_server
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# ── Paths ────────────────────────────────────────────────────────────────────

_DEFAULT_PROJECT_ROOT = Path(__file__).parents[2].resolve()
PROJECT_ROOT = _DEFAULT_PROJECT_ROOT
ACCEPT_DIR = Path(os.environ.get("KORA_ACCEPTANCE_DIR", "/tmp/claude/kora_acceptance"))
OUTPUT_DIR = ACCEPT_DIR / "acceptance_output"
SNAPSHOTS_DIR = OUTPUT_DIR / "snapshots"
SESSION_FILE = ACCEPT_DIR / "acceptance_session.json"
MONITOR_FILE = OUTPUT_DIR / "acceptance_monitor.md"
HARNESS_SOCK = ACCEPT_DIR / "harness.sock"
HARNESS_PID_FILE = ACCEPT_DIR / "harness.pid"
LOG_FILE = OUTPUT_DIR / "test_log.jsonl"

LOCKFILE = PROJECT_ROOT / "data" / "kora.lock"
TOKEN_FILE = PROJECT_ROOT / "data" / ".api_token"

# Allowlist of table names that ``_q_lifecycle_table`` may interpolate
# into its SQL. This exists as a defence-in-depth guard so future
# maintainers cannot accidentally extend the f-string interpolation in
# that helper to an attacker-controlled name.
_ALLOWED_LIFECYCLE_TABLES = frozenset({"memories", "user_model_facts"})

_ACCEPTANCE_PERSONA_EXPLICIT_MARKERS: tuple[str, ...] = (
    "acceptance-fixture",
    "kora acceptance",
    "acceptance test",
    "acceptance harness",
)

_ACCEPTANCE_PERSONA_FINGERPRINTS: tuple[str, ...] = (
    "maya",
    "maya rivera",
    "talia",
    "talia chen",
    "three rivers university",
    "accessibility resource center",
    "cognitive science",
    "adderall xr",
    "jordan",
    "alex",
    "mochi",
    "adderall",
    "trusted support",
    "doctor portal",
    "portal form",
    "local-first",
    "local first",
)


def _ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


# ── Session state ────────────────────────────────────────────────────────────

def _load_session() -> dict[str, Any]:
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text())
        except Exception:
            pass
    return {
        "started_at": None,
        "current_day": 1,
        "simulated_hours_offset": 0,
        "messages": [],
        "phases_completed": [],
        "coverage": {},
        "errors": [],
        "thread_id": None,
        "kora_session_id": None,
    }


def _save_session(state: dict[str, Any]) -> None:
    SESSION_FILE.write_text(json.dumps(state, indent=2, default=str))


def _log_event(event: dict[str, Any]) -> None:
    _ensure_dirs()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps({**event, "ts": datetime.now(UTC).isoformat()}) + "\n")


def _update_monitor(state: dict[str, Any], extra: str = "") -> None:
    """Write current status to the monitor file."""
    lines = [
        "# Kora Acceptance Monitor",
        "",
        f"Updated: {datetime.now(UTC).isoformat()}",
        f"Day: {state.get('current_day', '?')}",
        f"Simulated offset: +{state.get('simulated_hours_offset', 0):.1f}h",
        f"Messages sent: {len(state.get('messages', []))}",
        "",
        "## Recent Messages",
    ]
    messages = state.get("messages", [])[-5:]
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content", "")[:120]
        lines.append(f"- [{role}] {content}")
    if extra:
        lines.extend(["", "## Notes", extra])
    MONITOR_FILE.write_text("\n".join(lines) + "\n")


# ── Lockfile / token ─────────────────────────────────────────────────────────

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


def _rest_get(path: str, port: int, host: str, token: str) -> dict[str, Any] | None:
    import urllib.error
    import urllib.request
    try:
        url = f"http://{host}:{port}{path}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def _turn_trace_tool_counts(db_path: Path) -> dict[str, int]:
    """Return persisted GUI/daemon tool counts from ``turn_traces``."""
    if not db_path.exists():
        return {}
    try:
        import sqlite3
    except ImportError:
        return {}
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT tools_invoked FROM turn_traces WHERE tools_invoked IS NOT NULL"
            ).fetchall()
    except Exception:
        return {}
    counts: dict[str, int] = {}
    for (raw_tools,) in rows:
        try:
            tools = json.loads(raw_tools or "[]")
        except Exception:
            continue
        if not isinstance(tools, list):
            continue
        for raw_name in tools:
            name = str(raw_name or "").strip()
            if not name:
                continue
            if name.startswith("[auth:"):
                name = name.split(":")[1].rstrip("]").split(":")[0]
            counts[name] = counts.get(name, 0) + 1
    return counts


# ── Test data cleanup ────────────────────────────────────────────────────────

# Tables that must be cleared between acceptance test runs.
#
# Two layers:
#   1. Legacy autonomous tables (Phase ≤ 7): autonomous_plans,
#      autonomous_checkpoints, items, autonomous_updates,
#      item_state_history, item_artifact_links, item_deps.
#   2. Phase 7.5 + Phase 8 tables: orchestration rows, LangGraph
#      checkpoints, turn traces, lifecycle/session artifacts, and the
#      user-facing operational rows acceptance explicitly verifies.
#
# Order matters where foreign keys are involved (children before
# parents). worker_tasks → pipeline_instances; work_ledger references
# both but with no FK enforcement so order is loose. We use TRUNCATE-
# style ``DELETE FROM`` (SQLite has no TRUNCATE) and tolerate missing
# tables so the harness boots cleanly against an older DB.
_LEGACY_AUTONOMOUS_TABLES: tuple[str, ...] = (
    "item_state_history",
    "item_artifact_links",
    "item_deps",
    "items",
    "autonomous_updates",
    "autonomous_checkpoints",
    "autonomous_plans",
)

_ORCHESTRATION_TABLES: tuple[str, ...] = (
    "work_ledger",
    "worker_tasks",
    "pipeline_instances",
    "permission_grants",
    "trigger_state",
    "system_state_log",
    "request_limiter_log",
    "open_decisions",
    "runtime_pipelines",
)

_RUNTIME_STATE_TABLES: tuple[str, ...] = (
    "checkpoints",
    "turn_trace_events",
    "turn_traces",
    "sessions",
)

_LIFE_MANAGEMENT_TABLES: tuple[str, ...] = (
    "medication_log",
    "meal_log",
    "focus_blocks",
    "quick_notes",
    "reminders",
    "routine_sessions",
    "routines",
)

_LIFE_OS_TABLES: tuple[str, ...] = (
    "day_plan_entries",
    "day_plans",
    "life_events",
    "load_assessments",
    "plan_repair_actions",
    "support_profile_signals",
    "support_profiles",
    "future_self_bridges",
    "trusted_support_exports",
    "safety_boundary_records",
    "nudge_feedback",
    "nudge_decisions",
    "context_pack_feedback",
    "context_packs",
    "energy_log",
    "notification_engagement",
    "calendar_entries",
)

_LIFECYCLE_TABLES: tuple[str, ...] = (
    "notifications",
    "session_transcripts",
    "signal_queue",
    "dedup_rejected_pairs",
)

# Backward-compatible table set imported by unit tests and older harness
# helpers. The expanded cleanup path also includes runtime state and life
# management tables, but this alias preserves the previous public subset.
_PROACTIVE_TABLES: tuple[str, ...] = (
    "notifications",
    "reminders",
    "session_transcripts",
    "signal_queue",
    "dedup_rejected_pairs",
)


async def _clean_stale_test_data(db_path: Path) -> None:
    """Wipe stale data from previous test runs (Phase ≤ 7 + Phase 8).

    Phase ≤ 7 tables (autonomous_*, items, etc.) plus Phase 7.5
    orchestration rows, LangGraph checkpoints, session / trace
    artifacts, Phase 8 lifecycle adjuncts, and the user-facing life
    management rows acceptance validates.

    The previous ``_clean_stale_autonomous_data`` only wiped the legacy
    autonomous tables, which left a post-Phase 8 system in mixed state
    between runs. Always clean the full set atomically.

    Each DELETE is wrapped so a missing table doesn't abort the rest —
    the cleanup runs at startup against whatever DB shape exists.
    """
    if not db_path.exists():
        return
    try:
        import aiosqlite
    except ImportError:
        return
    try:
        async with aiosqlite.connect(str(db_path)) as db:
            # Children first, then parents, then runtime/lifecycle rows.
            for table in (
                *_LEGACY_AUTONOMOUS_TABLES,
                *_ORCHESTRATION_TABLES,
                *_RUNTIME_STATE_TABLES,
                *_LIFE_MANAGEMENT_TABLES,
                *_LIFE_OS_TABLES,
                *_LIFECYCLE_TABLES,
            ):
                try:
                    await db.execute(f"DELETE FROM {table}")
                except Exception:
                    pass  # Table may not exist yet
            await db.commit()
    except Exception:
        pass  # DB may be locked or corrupted — not fatal for test startup


def _clean_stale_projection_data(db_path: Path) -> None:
    """Wipe acceptance-owned projection rows while keeping the schema.

    Acceptance uses a scratch vault root, so retaining old projection rows
    creates split-brain evidence: entity links point at a previous memory
    tree while the report counts files in the fresh acceptance tree.
    """
    if not db_path.exists():
        return

    def _remove_projection_file() -> None:
        for path in (
            db_path,
            db_path.with_name(f"{db_path.name}-wal"),
            db_path.with_name(f"{db_path.name}-shm"),
        ):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    try:
        try:
            import pysqlite3 as sqlite3  # type: ignore[import-untyped]
        except ImportError:
            import sqlite3  # type: ignore[no-redef]

        with sqlite3.connect(str(db_path)) as db:
            db.execute("PRAGMA trusted_schema=ON")
            try:
                import sqlite_vec

                db.enable_load_extension(True)
                sqlite_vec.load(db)
                db.enable_load_extension(False)
            except Exception:
                pass

            uncleared_tables: list[str] = []
            for table in (
                "entity_links",
                "memories_vec",
                "user_model_vec",
                "memories",
                "user_model_facts",
                "entities",
            ):
                try:
                    db.execute(f"DELETE FROM {table}")
                except sqlite3.OperationalError:
                    uncleared_tables.append(table)
            db.commit()

            if uncleared_tables:
                raise RuntimeError(
                    "projection cleanup left uncleared tables: "
                    + ", ".join(uncleared_tables)
                )
    except Exception:
        # projection.db is a derived cache. If sqlite-vec cannot be loaded
        # during acceptance cleanup, recreating the file is safer than leaving
        # stale vec rowids that make the next run half-index memories.
        _remove_projection_file()


def _reset_daemon_runtime_files(data_dir: Path) -> dict[str, str]:
    """Drop persisted conversation identity files between acceptance runs.

    The daemon persists ``thread_id`` / ``session_id`` on disk so the
    LangGraph checkpoint state survives restart. That is correct for real
    usage, but the acceptance harness needs a fresh conversation identity
    every run; otherwise a stale checkpoint can be replayed into the first
    turn and break the clean-room test session before any new message lands.
    """
    result: dict[str, str] = {}
    for name in ("thread_id", "session_id"):
        path = data_dir / name
        try:
            existed = path.exists()
            path.unlink(missing_ok=True)
            result[name] = "removed" if existed else "absent"
        except Exception:
            result[name] = "error"
    return result


def _looks_like_acceptance_persona_residue(text: str) -> bool:
    """Return true only for old acceptance persona notes.

    This intentionally requires either an explicit acceptance marker or a
    dense cluster of the old Jordan persona fingerprints. A random real
    note mentioning one name should not be touched.
    """
    lowered = text.lower()
    if any(marker in lowered for marker in _ACCEPTANCE_PERSONA_EXPLICIT_MARKERS):
        return True
    hits = sum(1 for term in _ACCEPTANCE_PERSONA_FINGERPRINTS if term in lowered)
    return ("jordan" in lowered or "maya" in lowered) and hits >= 4


def _clean_acceptance_persona_residue(
    memory_root: Path,
    quarantine_root: Path,
) -> dict[str, Any]:
    """Move stale acceptance-persona notes out of persistent memory.

    Acceptance normally runs with ``KORA_MEMORY__KORA_MEMORY_PATH`` pointed at
    ``/tmp/claude/kora_acceptance/memory``. Prior runs can still leave old
    persona notes in the default ``~/.kora/memory`` tree. Those files can leak
    into a "fresh" run through fallback settings or projection rebuilding, so
    quarantine only files that are clearly acceptance-owned.
    """
    memory_root = memory_root.expanduser()
    quarantine_root = quarantine_root.expanduser()
    summary: dict[str, Any] = {
        "memory_root": str(memory_root),
        "quarantine_root": str(quarantine_root),
        "scanned_files": 0,
        "quarantined_files": [],
        "errors": [],
    }
    if not memory_root.exists():
        summary["status"] = "memory_root_missing"
        return summary
    if not memory_root.is_dir():
        summary["status"] = "memory_root_not_directory"
        return summary

    suffixes = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml"}
    for path in memory_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        summary["scanned_files"] += 1
        try:
            if path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if not _looks_like_acceptance_persona_residue(text):
                continue
            relative = path.relative_to(memory_root)
            target = quarantine_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
                target = target.with_name(f"{target.stem}.{stamp}{target.suffix}")
            shutil.move(str(path), str(target))
            summary["quarantined_files"].append({
                "from": str(path),
                "to": str(target),
            })
        except Exception as exc:
            summary["errors"].append({"path": str(path), "error": str(exc)})

    summary["status"] = "ok"
    summary["quarantined_count"] = len(summary["quarantined_files"])
    return summary


# Backward-compat alias — one slice of grace before removal.
_clean_stale_autonomous_data = _clean_stale_test_data


# ── Harness server ───────────────────────────────────────────────────────────

class HarnessServer:
    """Background server that manages Kora WebSocket and serves harness commands."""

    def __init__(self) -> None:
        self._state: dict[str, Any] = _load_session()
        self._ws: Any = None  # websockets connection
        self._kora_port: int | None = None
        self._kora_host: str = "127.0.0.1"
        self._token: str | None = None
        self._kora_session_id: str | None = None
        self._response_buffer: list[str] = []
        self._response_ready: asyncio.Event | None = None
        self._response_data: dict[str, Any] | None = None
        self._recv_task: asyncio.Task | None = None
        self._running = False
        self._busy = False  # True while waiting for a Kora response
        self._shutdown_event: asyncio.Event | None = None
        # Auth test mode: "auto" (approve all), "deny_once" (deny first, approve rest)
        self._auth_mode: str = "auto"
        self._auth_deny_count: int = 0
        # Compaction tracking — restored from session state so a daemon
        # or harness restart does not wipe the accumulated count. The
        # 2026-04-11 audit saw compaction-status return 25 events while
        # the final report only captured 2 because this list was an
        # in-memory field that got reset on restart.
        self._compaction_events: list[dict[str, Any]] = list(
            self._state.get("compaction_events") or []
        )

    # ── Connection management ─────────────────────────────────────────────

    async def connect_to_kora(self) -> bool:
        """Open WebSocket connection to Kora daemon."""
        try:
            import websockets
        except ImportError:
            print("websockets not installed", file=sys.stderr)
            return False

        data = _read_lockfile()
        if not data:
            return False

        port = data.get("api_port")
        host = data.get("api_host", "127.0.0.1")
        token = _read_token()

        if not port or not token:
            return False

        self._kora_port = port
        self._kora_host = host
        self._token = token

        uri = f"ws://{host}:{port}/api/v1/ws?token={token}"
        try:
            self._ws = await websockets.connect(uri, ping_interval=30, ping_timeout=10)
            # Drain bootstrap events
            await self._drain_bootstrap()
            return True
        except Exception as e:
            print(f"WebSocket connect failed: {e}", file=sys.stderr)
            return False

    async def _drain_bootstrap(self) -> None:
        """Consume session_greeting and session_ready after connect."""
        deadline = asyncio.get_event_loop().time() + 10.0
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
                data = json.loads(raw)
                if data.get("type") == "session_ready":
                    meta = data.get("metadata", {})
                    self._kora_session_id = meta.get("session_id")
                    self._state["kora_session_id"] = self._kora_session_id
                    _save_session(self._state)
                    break
                elif data.get("type") == "ping":
                    await self._ws.send(json.dumps({"type": "pong"}))
                # session_greeting: ignore
            except TimeoutError:
                break

    async def _recv_loop(self) -> None:
        """Background task: read WebSocket events and route to response handlers."""
        try:
            while self._ws is not None:
                raw = await self._ws.recv()
                data = json.loads(raw)
                await self._handle_ws_event(data)
        except Exception as e:
            if self._running:
                _log_event({"event": "ws_recv_loop_error", "error": str(e)})

    async def _ensure_ws_connected(self) -> bool:
        """Ensure a WebSocket and receive loop are both running."""
        if self._ws is None:
            if not await self.connect_to_kora():
                return False
        if self._recv_task is None or self._recv_task.done():
            self._recv_task = asyncio.create_task(self._recv_loop())
        return True

    async def _disconnect_for_idle(self) -> bool:
        """Close the conversation WebSocket so runtime can enter idle phases."""
        if self._ws is None:
            return False
        if self._recv_task is not None:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._recv_task
            self._recv_task = None
        with contextlib.suppress(Exception):
            await self._ws.close()
        self._ws = None
        _log_event({"event": "idle_ws_closed"})
        return True

    async def _reconnect_after_idle(self) -> None:
        """Restore the harness WebSocket after an idle soak."""
        if self._ws is not None:
            return
        if await self._ensure_ws_connected():
            _log_event(
                {
                    "event": "idle_ws_reconnected",
                    "session_id": self._kora_session_id,
                }
            )
        else:
            _log_event({"event": "idle_ws_reconnect_failed"})

    async def _handle_ws_event(self, data: dict[str, Any]) -> None:
        """Route a single WebSocket event."""
        msg_type = data.get("type", "")

        if msg_type == "ping":
            if self._ws:
                await self._ws.send(json.dumps({"type": "pong"}))
            return

        if msg_type in {"session_ready", "session_greeting"}:
            if msg_type == "session_ready":
                meta = data.get("metadata", {})
                self._kora_session_id = meta.get("session_id")
                self._state["kora_session_id"] = self._kora_session_id
                _save_session(self._state)
            return

        if msg_type == "auth_request":
            req_id = data.get("request_id", "")
            tool = data.get("tool", "?")
            risk = data.get("risk_level", "low")
            _log_event({"event": "auth_request", "tool": tool, "risk": risk, "req_id": req_id,
                        "auth_mode": self._auth_mode})

            # Decide approval based on auth mode
            if self._auth_mode == "deny_once" and self._auth_deny_count == 0:
                approved = False
                self._auth_deny_count += 1
                _log_event({"event": "auth_denied_test", "tool": tool})
            else:
                approved = True

            if self._ws:
                await self._ws.send(json.dumps({
                    "type": "auth_response",
                    "request_id": req_id,
                    "approved": approved,
                    "scope": "allow_once" if approved else "deny",
                }))
            # Append to current response data if building one
            if self._response_data is not None:
                tools = self._response_data.setdefault("tool_calls", [])
                label = f"[auth:{tool}:{'approved' if approved else 'denied'}]"
                tools.append(label)
            return

        if self._response_data is None:
            return  # Not awaiting response

        if msg_type == "token":
            self._response_data.setdefault("tokens", []).append(data.get("content", ""))
        elif msg_type == "tool_start":
            self._response_data.setdefault("tool_calls", []).append(data.get("content", ""))
        elif msg_type == "tool_result":
            pass
        elif msg_type == "response_complete":
            meta = data.get("metadata", {})
            self._response_data.update({
                "trace_id": meta.get("trace_id"),
                "latency_ms": meta.get("latency_ms", 0),
                "tool_call_count": meta.get("tool_call_count", 0),
                # Capture token accounting when the daemon emits it. The
                # current daemon only sends ``token_count`` (aggregate
                # post-compaction budget); prompt_tokens / completion_tokens
                # are forwarded if future envelopes add them.
                "token_count": meta.get("token_count"),
                "prompt_tokens": meta.get("prompt_tokens"),
                "completion_tokens": meta.get("completion_tokens"),
                "complete": True,
            })
            # Track compaction events
            compaction_tier = meta.get("compaction_tier")
            if compaction_tier and compaction_tier != "none":
                event = {
                    "tier": compaction_tier,
                    "token_count": meta.get("token_count"),
                    "ts": datetime.now(UTC).isoformat(),
                }
                self._compaction_events.append(event)
                # Mirror to session state so restarts do not drop events.
                self._state["compaction_events"] = list(self._compaction_events)
                try:
                    _save_session(self._state)
                except Exception:
                    # Best-effort persistence — fall through rather than
                    # blocking the response pipeline on disk failure.
                    pass
                self._response_data["compaction_tier"] = compaction_tier
            if self._response_ready:
                self._response_ready.set()
        elif msg_type == "error":
            self._response_data["error"] = data.get("content", "Unknown error")
            self._response_data["complete"] = True
            if self._response_ready:
                self._response_ready.set()

    # ── Command handlers ──────────────────────────────────────────────────

    async def cmd_send(self, message: str, timeout: float = 600.0) -> dict[str, Any]:
        """Send a message to Kora and return the response."""
        if not await self._ensure_ws_connected():
            return {"error": "Cannot connect to Kora daemon"}

        if self._busy:
            return {"error": "Harness is busy with another request"}

        self._busy = True
        self._response_data = {"tokens": [], "tool_calls": []}
        self._response_ready = asyncio.Event()

        try:
            # Harness-measured latency (the daemon's response_complete
            # envelope does not include latency_ms today, so time the round
            # trip locally to get a real measurement).
            send_monotonic = time.monotonic()
            await self._ws.send(json.dumps({"type": "chat", "content": message}))
            try:
                await asyncio.wait_for(self._response_ready.wait(), timeout=timeout)
            except TimeoutError:
                return {"error": f"Response timed out after {timeout}s"}
            measured_latency_ms = int(
                max(0.0, (time.monotonic() - send_monotonic) * 1000.0)
            )

            tokens = self._response_data.get("tokens", [])
            response_text = "".join(tokens)

            # Prefer daemon-reported latency_ms if it's present and
            # positive; fall back to the locally-measured value.
            daemon_latency = self._response_data.get("latency_ms", 0) or 0
            try:
                latency_ms = (
                    int(daemon_latency) if int(daemon_latency) > 0
                    else measured_latency_ms
                )
            except (TypeError, ValueError):
                latency_ms = measured_latency_ms

            # ``token_count`` is the total token budget for the turn as
            # reported by the daemon in ``response_complete.metadata``.
            # ``prompt_tokens`` / ``completion_tokens`` are not currently
            # emitted by the daemon — persist whichever is available and
            # leave the rest ``None``.
            token_count = self._response_data.get("token_count")
            prompt_tokens = self._response_data.get("prompt_tokens")
            completion_tokens = self._response_data.get("completion_tokens")
            compaction_tier = (
                self._response_data.get("compaction_tier") or "none"
            )

            result = {
                "response": response_text,
                "trace_id": self._response_data.get("trace_id"),
                "latency_ms": latency_ms,
                "tool_call_count": self._response_data.get("tool_call_count", 0),
                "tool_calls": self._response_data.get("tool_calls", []),
                "session_id": self._kora_session_id,
                "error": self._response_data.get("error"),
            }

            # Record in conversation state. Persist latency, token
            # accounting, and compaction_tier here so the benchmarks
            # collector can read them back from message state without
            # having to re-derive them from the response pipeline.
            self._state["messages"].append({
                "role": "user",
                "content": message,
                "ts": datetime.now(UTC).isoformat(),
            })
            self._state["messages"].append({
                "role": "assistant",
                "content": response_text,
                "ts": datetime.now(UTC).isoformat(),
                "trace_id": result["trace_id"],
                "tool_calls": result["tool_calls"],
                "latency_ms": latency_ms,
                # token_count is the total-token budget for the turn
                # (completion + prompt when both are known; daemon
                # currently reports a single aggregated number).
                "token_count": token_count,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "compaction_tier": compaction_tier,
                # Explicit flag so the benchmarks collector can
                # distinguish real assistant turns from synthetic
                # compaction-event entries.
                "is_response": True,
            })
            _save_session(self._state)
            _update_monitor(self._state)
            _log_event({"event": "turn_complete", **result})
            return result

        except Exception as e:
            return {"error": str(e)}
        finally:
            self._busy = False
            self._response_data = None
            self._response_ready = None

    async def cmd_status(self) -> dict[str, Any]:
        """Return current daemon status + key inspect topics."""
        if self._kora_port is None:
            data = _read_lockfile()
            if data:
                self._kora_port = data.get("api_port")
                self._kora_host = data.get("api_host", "127.0.0.1")
            self._token = _read_token()

        if not self._kora_port or not self._token:
            return {"error": "Daemon not reachable"}

        result: dict[str, Any] = {
            "daemon_port": self._kora_port,
            "daemon_host": self._kora_host,
            "ws_connected": self._ws is not None,
            "session_id": self._kora_session_id,
            "simulated_hours": self._state.get("simulated_hours_offset", 0),
            "messages_count": len(self._state.get("messages", [])),
            "first_run": self._state.get("first_run", {}),
            "clean_start": self._state.get("clean_start", {}),
        }

        status = _rest_get("/api/v1/status", self._kora_port, self._kora_host, self._token)
        if status:
            result["status"] = status

        for topic in ["doctor", "session", "workers"]:
            result[f"inspect_{topic}"] = _rest_get(
                f"/api/v1/inspect/{topic}",
                self._kora_port, self._kora_host, self._token,
            )

        return result

    async def cmd_snapshot(self, name: str) -> dict[str, Any]:
        """Capture full system state to a named snapshot file."""
        if self._kora_port is None:
            data = _read_lockfile()
            if data:
                self._kora_port = data.get("api_port")
                self._kora_host = data.get("api_host", "127.0.0.1")
            self._token = _read_token()

        snapshot: dict[str, Any] = {
            "name": name,
            "captured_at": datetime.now(UTC).isoformat(),
            "simulated_hours": self._state.get("simulated_hours_offset", 0),
            "first_run": self._state.get("first_run", {}),
            "clean_start": self._state.get("clean_start", {}),
            "conversation": {
                "message_count": len(self._state.get("messages", [])),
                "last_3": self._state.get("messages", [])[-3:],
            },
        }

        if self._kora_port and self._token:
            snapshot["status"] = _rest_get(
                "/api/v1/status", self._kora_port, self._kora_host, self._token
            )
            for topic in [
                "setup", "tools", "workers", "permissions",
                "session", "trace", "doctor", "phase-audit",
            ]:
                snapshot[f"inspect_{topic}"] = _rest_get(
                    f"/api/v1/inspect/{topic}",
                    self._kora_port, self._kora_host, self._token,
                )
        else:
            snapshot["error"] = "Daemon not reachable for state capture"

        # Capture full state across all post-Phase 8 dimensions.
        # ``autonomous_state`` stays at the top level for backward compat
        # with the existing report builder; the new dimensions live next
        # to it as documented in AT2.
        full = await self._snapshot_full_state()
        snapshot.update(full)

        path = SNAPSHOTS_DIR / f"{name}.json"
        path.write_text(json.dumps(snapshot, indent=2, default=str))

        # AT3: write a benchmarks JSON sidecar alongside the snapshot.
        benchmarks_path: str | None = None
        try:
            bench_result = await self.cmd_benchmarks()
            sidecar = SNAPSHOTS_DIR / f"{name}.benchmarks.json"
            sidecar.write_text(
                json.dumps(bench_result.get("json", {}), indent=2, default=str)
            )
            benchmarks_path = str(sidecar)
            # Append to the central CSV for trending.
            try:
                self._append_benchmarks_csv(name, bench_result.get("csv_row", {}))
            except Exception:
                # CSV append is best-effort — never fails a snapshot.
                pass
        except Exception:
            # Benchmarks are best-effort — never fail a snapshot on them.
            pass

        _log_event({
            "event": "snapshot", "name": name, "path": str(path),
            "benchmarks_path": benchmarks_path,
        })
        return {"path": str(path), "name": name, "benchmarks_path": benchmarks_path}

    @staticmethod
    def _append_benchmarks_csv(
        snapshot_name: str, row: dict[str, Any],
    ) -> None:
        """Append one benchmark row to the acceptance-local trend CSV.

        Creates the file + header if it doesn't yet exist. Rows are
        keyed by the snapshot name and an ISO timestamp so trending
        tools can sort / dedupe.

        Uses an ``fcntl.LOCK_EX`` advisory file lock around the write
        so concurrent snapshot callers cannot interleave their header
        + row writes (which would produce doubled headers or partial
        rows on the second writer).
        """
        import csv
        import fcntl

        from tests.acceptance.scenario.benchmarks import CSV_COLUMNS

        if os.environ.get("KORA_ACCEPTANCE_DIR"):
            out_dir = OUTPUT_DIR
        else:
            out_dir = PROJECT_ROOT / "data" / "acceptance"
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "benchmarks.csv"

        header = ("snapshot", "captured_at", *CSV_COLUMNS)
        with open(csv_path, "a", newline="") as fh:
            # Exclusive lock held for the whole header+row write so
            # another process cannot observe an empty file, decide to
            # also write a header, and produce a doubled one.
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                # Re-check for header *under the lock* — the file could
                # have been created by a racing writer between the path
                # existence check and our acquisition.
                fh.seek(0, 2)  # seek to end
                write_header = fh.tell() == 0
                writer = csv.writer(fh)
                if write_header:
                    writer.writerow(header)
                writer.writerow(
                    [
                        snapshot_name,
                        datetime.now(UTC).isoformat(),
                        *(row.get(col, "") for col in CSV_COLUMNS),
                    ]
                )
                fh.flush()
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    async def cmd_diff(self, snap1: str, snap2: str) -> dict[str, Any]:
        """Human-readable diff between two snapshots."""
        p1 = SNAPSHOTS_DIR / f"{snap1}.json"
        p2 = SNAPSHOTS_DIR / f"{snap2}.json"

        if not p1.exists():
            return {"error": f"Snapshot '{snap1}' not found at {p1}"}
        if not p2.exists():
            return {"error": f"Snapshot '{snap2}' not found at {p2}"}

        s1 = json.loads(p1.read_text())
        s2 = json.loads(p2.read_text())

        lines = [
            f"# Diff: {snap1} → {snap2}",
            f"Time: {s1.get('captured_at')} → {s2.get('captured_at')}",
            "",
        ]

        # Message count
        m1 = s1.get("conversation", {}).get("message_count", 0)
        m2 = s2.get("conversation", {}).get("message_count", 0)
        delta = m2 - m1
        lines.append(f"Messages: {m1} → {m2} (Δ{delta:+d})")

        # Simulated hours
        h1 = s1.get("simulated_hours", 0)
        h2 = s2.get("simulated_hours", 0)
        if h1 != h2:
            lines.append(f"Simulated hours: {h1} → {h2} (Δ{h2 - h1:+.1f}h)")

        # Session state
        sess1 = (s1.get("inspect_session") or {}).get("active")
        sess2 = (s2.get("inspect_session") or {}).get("active")
        if sess1 != sess2:
            lines.append(f"Session active: {bool(sess1)} → {bool(sess2)}")

        # Doctor health
        doc1 = s1.get("inspect_doctor", {})
        doc2 = s2.get("inspect_doctor", {})
        lines.append(f"Doctor: {doc1.get('summary', '?')} → {doc2.get('summary', '?')}")

        # Phase audit
        pa1 = s1.get("inspect_phase-audit", {})
        pa2 = s2.get("inspect_phase-audit", {})
        lines.append(f"Phase audit: {pa1.get('summary', '?')} → {pa2.get('summary', '?')}")

        # Trace count
        tr1 = (s1.get("inspect_trace") or {}).get("trace_count", 0)
        tr2 = (s2.get("inspect_trace") or {}).get("trace_count", 0)
        if tr1 != tr2:
            lines.append(f"Turn traces: {tr1} → {tr2} (Δ{tr2 - tr1:+d})")

        # Permission grants
        pg1 = (s1.get("inspect_permissions") or {}).get("grant_count", 0)
        pg2 = (s2.get("inspect_permissions") or {}).get("grant_count", 0)
        if pg1 != pg2:
            lines.append(f"Permission grants: {pg1} → {pg2} (Δ{pg2 - pg1:+d})")

        # ── Phase 7.5 / Phase 8 state diffs ────────────────────────────
        self._diff_orchestration(s1, s2, lines)
        self._diff_memory_lifecycle(s1, s2, lines)
        self._diff_vault(s1, s2, lines)
        self._diff_proactive(s1, s2, lines)

        # ── Legacy autonomous tables (deprecated) ──────────────────────
        a1 = s1.get("autonomous_state") or {}
        a2 = s2.get("autonomous_state") or {}
        if a1.get("available") or a2.get("available"):
            legacy_lines: list[str] = []
            items1 = a1.get("total_items", 0)
            items2 = a2.get("total_items", 0)
            if items1 != items2:
                legacy_lines.append(
                    f"Items (tasks): {items1} → {items2} (Δ{items2 - items1:+d})"
                )

            chk1 = a1.get("checkpoint_count", 0)
            chk2 = a2.get("checkpoint_count", 0)
            if chk1 != chk2:
                legacy_lines.append(
                    f"Autonomous checkpoints: {chk1} → {chk2} (Δ{chk2 - chk1:+d})"
                )

            plans1 = len(a1.get("autonomous_plans", []))
            plans2 = len(a2.get("autonomous_plans", []))
            if plans1 != plans2:
                legacy_lines.append(f"Autonomous plans total: {plans1} → {plans2}")

            active1 = a1.get("active_plan_count", 0)
            active2 = a2.get("active_plan_count", 0)
            if active1 != active2:
                legacy_lines.append(
                    f"Active autonomous plans: {active1} → {active2}"
                )

            # Status breakdown change
            st1 = a1.get("items_by_status", {})
            st2 = a2.get("items_by_status", {})
            all_statuses = set(st1) | set(st2)
            for status in sorted(all_statuses):
                c1, c2 = st1.get(status, 0), st2.get(status, 0)
                if c1 != c2:
                    legacy_lines.append(
                        f"  items[{status}]: {c1} → {c2} (Δ{c2 - c1:+d})"
                    )

            # New items created since snap1
            new_items = [
                it for it in a2.get("recent_items", [])
                if it.get("created_at", "") > (
                    s1.get("captured_at") or ""
                )
            ]
            if new_items:
                legacy_lines.append("")
                legacy_lines.append(f"  New autonomous items ({len(new_items)}):")
                for it in new_items[:5]:
                    content = (it.get("content") or "")[:120]
                    status = it.get("status", "?")
                    owner = it.get("owner", "?")
                    legacy_lines.append(f"    [{status}/{owner}] {content}")

            if legacy_lines:
                lines.append("")
                lines.append("## Legacy autonomous tables (deprecated)")
                lines.extend(legacy_lines)

        # New messages since snap1
        if delta > 0:
            lines.append("")
            lines.append(f"## New conversation ({delta} messages):")
            new_msgs = s2.get("conversation", {}).get("last_3", [])
            for m in new_msgs:
                role = m.get("role", "?")
                content = (m.get("content") or "")[:200]
                lines.append(f"  [{role}] {content}")

        diff_text = "\n".join(lines)
        diff_path = OUTPUT_DIR / f"diff_{snap1}_to_{snap2}.md"
        diff_path.write_text(diff_text)

        return {"diff": diff_text, "path": str(diff_path)}

    async def _query_autonomous_state(self) -> dict[str, Any]:
        """Query live autonomous runtime state directly from operational.db.

        DEPRECATED — kept for backward compat with the legacy snapshot
        format and existing report code. Phase 7.5+ callers should use
        :meth:`_query_orchestration_state` for the orchestration tables
        and :meth:`_snapshot_full_state` for everything else.
        """
        op_db = PROJECT_ROOT / "data" / "operational.db"
        if not op_db.exists():
            return {"available": False}

        try:
            import aiosqlite

            async with aiosqlite.connect(str(op_db)) as db:
                db.row_factory = aiosqlite.Row

                # Per-table try/except matches the new methods' contract —
                # a missing deprecated table records ``{"error":
                # "table_missing"}`` rather than aborting the snapshot.

                # autonomous_plans
                plans: list[dict[str, Any]] = []
                plans_error: str | None = None
                try:
                    cursor = await db.execute(
                        """SELECT id, goal, status,
                                  COALESCE(request_count, 0) AS request_count,
                                  COALESCE(token_estimate, 0) AS token_estimate,
                                  COALESCE(cost_estimate, 0.0) AS cost_estimate,
                                  created_at,
                                  COALESCE(updated_at, completed_at) AS updated_at
                           FROM autonomous_plans
                           ORDER BY created_at DESC LIMIT 10"""
                    )
                    plans = [dict(r) for r in await cursor.fetchall()]
                except Exception:
                    plans_error = "table_missing"

                # items
                items_by_status: dict[str, int] = {}
                total_items = 0
                recent_items: list[dict[str, Any]] = []
                items_error: str | None = None
                try:
                    cursor = await db.execute(
                        "SELECT status, COUNT(*) as cnt FROM items GROUP BY status"
                    )
                    items_by_status = {
                        r["status"]: r["cnt"] for r in await cursor.fetchall()
                    }

                    cursor = await db.execute("SELECT COUNT(*) FROM items")
                    row = await cursor.fetchone()
                    total_items = row[0] if row else 0

                    cursor = await db.execute(
                        """SELECT id, title AS content, status, owner, spawned_from, created_at
                           FROM items ORDER BY created_at DESC LIMIT 10"""
                    )
                    recent_items = [dict(r) for r in await cursor.fetchall()]
                except Exception:
                    items_error = "table_missing"

                # autonomous_checkpoints
                checkpoint_count = 0
                checkpoints_error: str | None = None
                try:
                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM autonomous_checkpoints"
                    )
                    row = await cursor.fetchone()
                    checkpoint_count = row[0] if row else 0
                except Exception:
                    checkpoints_error = "table_missing"

                # Active plans (not in terminal state)
                active_plans = [
                    p for p in plans
                    if p.get("status") not in ("completed", "cancelled", "failed")
                ]

                result: dict[str, Any] = {
                    "available": True,
                    "total_items": total_items,
                    "items_by_status": items_by_status,
                    "recent_items": recent_items,
                    "autonomous_plans": plans,
                    "active_plan_count": len(active_plans),
                    "checkpoint_count": checkpoint_count,
                }
                if plans_error or items_error or checkpoints_error:
                    result["table_errors"] = {
                        "autonomous_plans": plans_error,
                        "items": items_error,
                        "autonomous_checkpoints": checkpoints_error,
                    }
                return result
        except Exception as e:
            return {"available": False, "error": f"connect_failed: {e}"}

    # ── Phase 7.5 / Phase 8 state queries ────────────────────────────────

    async def _query_orchestration_state(
        self,
        db_path: Path | None = None,
    ) -> dict[str, Any]:
        """Query the 8 orchestration tables (Phase 7.5).

        Returns a dict shaped per the AT2 contract. Each table query is
        wrapped so a missing table records ``{"error": "table_missing"}``
        in its slot rather than aborting the whole snapshot — older DB
        states (pre-orchestration migrations) must still snapshot OK.
        """
        op_db = db_path or (PROJECT_ROOT / "data" / "operational.db")
        if not op_db.exists():
            return {"available": False, "error": "db_missing", "path": str(op_db)}

        try:
            import aiosqlite
        except ImportError:
            return {"available": False, "error": "aiosqlite not installed"}

        result: dict[str, Any] = {"available": True}

        try:
            async with aiosqlite.connect(str(op_db)) as db:
                db.row_factory = aiosqlite.Row

                result["pipeline_instances"] = await self._q_pipeline_instances(db)
                result["worker_tasks"] = await self._q_worker_tasks(db)
                result["work_ledger"] = await self._q_work_ledger(db)
                result["trigger_state"] = await self._q_trigger_state(db)
                result["system_state_log"] = await self._q_system_state_log(db)
                result["request_limiter"] = await self._q_request_limiter(db)
                result["open_decisions"] = await self._q_open_decisions(db)
                result["runtime_pipelines"] = await self._q_runtime_pipelines(db)
        except Exception as e:
            result["available"] = False
            result["error"] = f"connect_failed: {e}"

        return result

    @staticmethod
    async def _q_pipeline_instances(db: Any) -> dict[str, Any]:
        try:
            cur = await db.execute("SELECT COUNT(*) FROM pipeline_instances")
            row = await cur.fetchone()
            total = row[0] if row else 0

            cur = await db.execute(
                "SELECT state, COUNT(*) AS cnt FROM pipeline_instances GROUP BY state"
            )
            by_state = {r["state"]: r["cnt"] for r in await cur.fetchall()}

            cur = await db.execute(
                "SELECT pipeline_name, COUNT(*) AS cnt FROM pipeline_instances "
                "GROUP BY pipeline_name"
            )
            by_name = {r["pipeline_name"]: r["cnt"] for r in await cur.fetchall()}

            cur = await db.execute(
                """SELECT id, pipeline_name, state, started_at, completed_at,
                          updated_at, completion_reason
                   FROM pipeline_instances
                   ORDER BY started_at DESC LIMIT 20"""
            )
            recent: list[dict[str, Any]] = []
            for r in await cur.fetchall():
                row_dict = dict(r)
                # Best-effort duration_s computation (ISO timestamps).
                duration_s: float | None = None
                started = row_dict.get("started_at")
                completed = row_dict.get("completed_at")
                if started and completed:
                    try:
                        s = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                        e = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
                        duration_s = (e - s).total_seconds()
                    except Exception:
                        pass
                row_dict["duration_s"] = duration_s
                recent.append(row_dict)

            return {
                "total": total,
                "by_state": by_state,
                "by_name": by_name,
                "recent": recent,
            }
        except Exception:
            return {"error": "table_missing", "table": "pipeline_instances"}

    @staticmethod
    async def _q_worker_tasks(db: Any) -> dict[str, Any]:
        try:
            cur = await db.execute("SELECT COUNT(*) FROM worker_tasks")
            row = await cur.fetchone()
            total = row[0] if row else 0

            cur = await db.execute(
                "SELECT state, COUNT(*) AS cnt FROM worker_tasks GROUP BY state"
            )
            by_lifecycle = {r["state"]: r["cnt"] for r in await cur.fetchall()}

            active_states = {"pending", "running", "checkpointing", "paused_for_conversation"}
            active_count = sum(by_lifecycle.get(s, 0) for s in active_states)

            return {
                "total": total,
                "by_lifecycle": by_lifecycle,
                "active_count": active_count,
            }
        except Exception:
            return {"error": "table_missing", "table": "worker_tasks"}

    @staticmethod
    async def _q_work_ledger(db: Any) -> dict[str, Any]:
        try:
            cur = await db.execute("SELECT COUNT(*) FROM work_ledger")
            row = await cur.fetchone()
            total = row[0] if row else 0

            cur = await db.execute(
                "SELECT event_type, COUNT(*) AS cnt FROM work_ledger GROUP BY event_type"
            )
            by_event_type = {r["event_type"]: r["cnt"] for r in await cur.fetchall()}

            cur = await db.execute(
                "SELECT reason, COUNT(*) AS cnt FROM work_ledger "
                "WHERE reason IS NOT NULL AND reason != '' GROUP BY reason"
            )
            by_reason = {r["reason"]: r["cnt"] for r in await cur.fetchall()}

            cur = await db.execute(
                """SELECT timestamp, event_type, pipeline_instance_id,
                          worker_task_id, trigger_name, reason, metadata_json
                   FROM work_ledger
                   ORDER BY id DESC LIMIT 30"""
            )
            recent = [dict(r) for r in await cur.fetchall()]

            return {
                "total": total,
                "by_event_type": by_event_type,
                "by_reason": by_reason,
                "recent": recent,
            }
        except Exception:
            return {"error": "table_missing", "table": "work_ledger"}

    @staticmethod
    async def _q_trigger_state(db: Any) -> dict[str, Any]:
        try:
            cur = await db.execute("SELECT COUNT(*) FROM trigger_state")
            row = await cur.fetchone()
            total = row[0] if row else 0

            cur = await db.execute(
                """SELECT trigger_id, pipeline_name, last_fired_at, last_fire_reason,
                          next_eligible_at
                   FROM trigger_state
                   ORDER BY last_fired_at DESC LIMIT 20"""
            )
            last_fires = []
            for r in await cur.fetchall():
                d = dict(r)
                last_fires.append({
                    "trigger_name": d.get("trigger_id"),
                    "pipeline_name": d.get("pipeline_name"),
                    "last_fired_at": d.get("last_fired_at"),
                    "last_fire_reason": d.get("last_fire_reason"),
                    "next_eligible_at": d.get("next_eligible_at"),
                })

            return {
                "total_triggers_tracked": total,
                "last_fires": last_fires,
            }
        except Exception:
            return {"error": "table_missing", "table": "trigger_state"}

    @staticmethod
    async def _q_system_state_log(db: Any) -> dict[str, Any]:
        try:
            cur = await db.execute("SELECT COUNT(*) FROM system_state_log")
            row = await cur.fetchone()
            total = row[0] if row else 0

            cur = await db.execute(
                "SELECT new_phase, COUNT(*) AS cnt FROM system_state_log "
                "GROUP BY new_phase"
            )
            by_phase = {r["new_phase"]: r["cnt"] for r in await cur.fetchall()}

            cur = await db.execute(
                """SELECT previous_phase, new_phase, transitioned_at, reason
                   FROM system_state_log
                   ORDER BY id DESC LIMIT 20"""
            )
            recent_transitions = []
            current_phase: str | None = None
            rows = await cur.fetchall()
            for r in rows:
                d = dict(r)
                recent_transitions.append({
                    "from_phase": d.get("previous_phase"),
                    "to_phase": d.get("new_phase"),
                    "at": d.get("transitioned_at"),
                    "reason": d.get("reason"),
                })
            if rows:
                current_phase = dict(rows[0]).get("new_phase")

            return {
                "transitions_total": total,
                "by_phase": by_phase,
                "current_phase": current_phase,
                "recent_transitions": recent_transitions,
            }
        except Exception:
            return {"error": "table_missing", "table": "system_state_log"}

    @staticmethod
    async def _q_request_limiter(db: Any) -> dict[str, Any]:
        # Window: 5 hours = 18000 seconds (per AT2 spec).
        window_seconds = 18000
        try:
            cur = await db.execute("SELECT COUNT(*) FROM request_limiter_log")
            row = await cur.fetchone()
            total = row[0] if row else 0

            cur = await db.execute(
                "SELECT class, COUNT(*) AS cnt FROM request_limiter_log GROUP BY class"
            )
            by_class = {r["class"]: r["cnt"] for r in await cur.fetchall()}

            # Approximate budget remaining over the trailing window.
            # The "capacity" is unknowable without the limiter config; we
            # report total-in-window so callers can compute their own.
            in_window = 0
            try:
                cur = await db.execute(
                    "SELECT COUNT(*) FROM request_limiter_log "
                    "WHERE timestamp >= datetime('now', ?)",
                    (f"-{window_seconds} seconds",),
                )
                row = await cur.fetchone()
                in_window = row[0] if row else 0
            except Exception:
                pass

            return {
                "total_requests_logged": total,
                "by_class": by_class,
                "window_seconds": window_seconds,
                "in_window": in_window,
            }
        except Exception:
            return {"error": "table_missing", "table": "request_limiter_log"}

    @staticmethod
    async def _q_open_decisions(db: Any) -> dict[str, Any]:
        try:
            cur = await db.execute("SELECT COUNT(*) FROM open_decisions")
            row = await cur.fetchone()
            total = row[0] if row else 0

            cur = await db.execute(
                "SELECT status, COUNT(*) AS cnt FROM open_decisions GROUP BY status"
            )
            by_status = {r["status"]: r["cnt"] for r in await cur.fetchall()}

            cur = await db.execute(
                """SELECT id, topic, posed_at, posed_in_session, status,
                          resolved_at, resolution
                   FROM open_decisions
                   ORDER BY posed_at DESC LIMIT 20"""
            )
            recent = [dict(r) for r in await cur.fetchall()]

            return {
                "total": total,
                "by_status": by_status,
                "recent": recent,
            }
        except Exception:
            return {"error": "table_missing", "table": "open_decisions"}

    @staticmethod
    async def _q_runtime_pipelines(db: Any) -> dict[str, Any]:
        try:
            cur = await db.execute("SELECT COUNT(*) FROM runtime_pipelines")
            row = await cur.fetchone()
            total = row[0] if row else 0

            cur = await db.execute(
                "SELECT name, enabled FROM runtime_pipelines LIMIT 50"
            )
            rows = [dict(r) for r in await cur.fetchall()]
            names = [r["name"] for r in rows]
            by_enabled = {"enabled": 0, "disabled": 0}
            for r in rows:
                if r.get("enabled"):
                    by_enabled["enabled"] += 1
                else:
                    by_enabled["disabled"] += 1

            return {
                "total": total,
                "by_type": by_enabled,
                "names": names,
            }
        except Exception:
            return {"error": "table_missing", "table": "runtime_pipelines"}

    async def _query_memory_lifecycle_state(
        self,
        projection_db_path: Path | None = None,
        operational_db_path: Path | None = None,
    ) -> dict[str, Any]:
        """Query memory lifecycle (Phase 8): soft-delete, dedup, entities.

        Pulls from ``projection.db`` (memories, user_model_facts,
        entities, entity_links) and ``operational.db`` (signal_queue,
        session_transcripts, dedup_rejected_pairs).
        """
        proj_db = projection_db_path or (PROJECT_ROOT / "data" / "projection.db")
        op_db = operational_db_path or (PROJECT_ROOT / "data" / "operational.db")

        result: dict[str, Any] = {
            "available": proj_db.exists() or op_db.exists(),
            "projection_db_path": str(proj_db),
            "operational_db_path": str(op_db),
        }

        try:
            import aiosqlite
        except ImportError:
            return {"available": False, "error": "aiosqlite not installed"}

        projection_ok = False
        operational_ok = False

        # --- projection.db queries ---
        if proj_db.exists():
            try:
                async with aiosqlite.connect(str(proj_db)) as db:
                    db.row_factory = aiosqlite.Row
                    result["memories"] = await self._q_lifecycle_table(
                        db, "memories", with_status=True,
                    )
                    result["user_model_facts"] = await self._q_lifecycle_table(
                        db, "user_model_facts", with_status=True,
                    )
                    result["entities"] = await self._q_entities(db)
                    result["entity_links"] = await self._q_entity_links(db)
                projection_ok = True
            except Exception as e:
                result["projection_error"] = f"connect_failed: {e}"
        else:
            for k in ("memories", "user_model_facts", "entities", "entity_links"):
                result[k] = {"error": "db_missing", "db": "projection.db"}

        # --- operational.db queries ---
        if op_db.exists():
            try:
                async with aiosqlite.connect(str(op_db)) as db:
                    db.row_factory = aiosqlite.Row
                    result["sessions"] = await self._q_session_transcripts(db)
                    result["signal_queue"] = await self._q_signal_queue(db)
                    result["dedup_rejected_pairs"] = await self._q_dedup_pairs(db)
                operational_ok = True
            except Exception as e:
                result["operational_error"] = f"connect_failed: {e}"
        else:
            for k in ("sessions", "signal_queue", "dedup_rejected_pairs"):
                result[k] = {"error": "db_missing", "db": "operational.db"}

        # ``available`` reflects whether *any* query actually succeeded —
        # a present-but-unreadable DB file no longer counts as available.
        result["available"] = projection_ok or operational_ok

        return result

    @staticmethod
    async def _q_lifecycle_table(
        db: Any,
        table: str,
        with_status: bool = True,
    ) -> dict[str, Any]:
        """Generic query for memories / user_model_facts soft-delete state.

        ``table`` is interpolated into SQL via f-string, so it MUST be
        checked against :data:`_ALLOWED_LIFECYCLE_TABLES`. Callers pass
        literals today, but this guard keeps a future maintainer from
        extending the helper with an attacker-controlled name.
        """
        if table not in _ALLOWED_LIFECYCLE_TABLES:
            raise ValueError(
                f"unsafe table name for lifecycle query: {table!r}"
            )
        try:
            cur = await db.execute(f"SELECT COUNT(*) FROM {table}")
            row = await cur.fetchone()
            total = row[0] if row else 0

            by_status: dict[str, int] = {}
            with_consolidated_into = 0
            with_merged_from = 0
            recent_active: list[dict[str, Any]] = []

            if with_status:
                try:
                    cur = await db.execute(
                        f"SELECT status, COUNT(*) AS cnt FROM {table} GROUP BY status"
                    )
                    by_status = {r["status"]: r["cnt"] for r in await cur.fetchall()}
                except Exception:
                    by_status = {"_error": "status_column_missing"}  # type: ignore[dict-item]

                try:
                    cur = await db.execute(
                        f"SELECT COUNT(*) FROM {table} "
                        "WHERE consolidated_into IS NOT NULL"
                    )
                    row = await cur.fetchone()
                    with_consolidated_into = row[0] if row else 0
                except Exception:
                    pass

                try:
                    cur = await db.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE merged_from IS NOT NULL"
                    )
                    row = await cur.fetchone()
                    with_merged_from = row[0] if row else 0
                except Exception:
                    pass

                try:
                    cur = await db.execute(
                        f"SELECT id, created_at FROM {table} "
                        "WHERE status = 'active' "
                        "ORDER BY created_at DESC LIMIT 10"
                    )
                    recent_active = [dict(r) for r in await cur.fetchall()]
                except Exception:
                    pass

            return {
                "total": total,
                "by_status": by_status,
                "with_consolidated_into": with_consolidated_into,
                "with_merged_from": with_merged_from,
                "recent_active": recent_active,
            }
        except Exception:
            return {"error": "table_missing", "table": table}

    @staticmethod
    async def _q_entities(db: Any) -> dict[str, Any]:
        try:
            cur = await db.execute("SELECT COUNT(*) FROM entities")
            row = await cur.fetchone()
            total = row[0] if row else 0
            cur = await db.execute(
                "SELECT entity_type, COUNT(*) AS cnt FROM entities GROUP BY entity_type"
            )
            by_type = {r["entity_type"]: r["cnt"] for r in await cur.fetchall()}
            cur = await db.execute(
                "SELECT COUNT(*) FROM entities "
                "WHERE metadata LIKE '%\"merged_from\"%'"
            )
            row = await cur.fetchone()
            with_merged_from = row[0] if row else 0
            return {
                "total": total,
                "by_type": by_type,
                "with_merged_from": with_merged_from,
            }
        except Exception:
            return {"error": "table_missing", "table": "entities"}

    @staticmethod
    async def _q_entity_links(db: Any) -> dict[str, Any]:
        try:
            cur = await db.execute("SELECT COUNT(*) FROM entity_links")
            row = await cur.fetchone()
            return {"total": row[0] if row else 0}
        except Exception:
            return {"error": "table_missing", "table": "entity_links"}

    @staticmethod
    async def _q_session_transcripts(db: Any) -> dict[str, Any]:
        try:
            cur = await db.execute("SELECT COUNT(*) FROM session_transcripts")
            row = await cur.fetchone()
            total = row[0] if row else 0
            cur = await db.execute(
                "SELECT COUNT(*) FROM session_transcripts WHERE processed_at IS NOT NULL"
            )
            row = await cur.fetchone()
            processed = row[0] if row else 0
            return {
                "transcripts_total": total,
                "processed": processed,
                "unprocessed": max(total - processed, 0),
            }
        except Exception:
            return {"error": "table_missing", "table": "session_transcripts"}

    @staticmethod
    async def _q_signal_queue(db: Any) -> dict[str, Any]:
        try:
            cur = await db.execute("SELECT COUNT(*) FROM signal_queue")
            row = await cur.fetchone()
            total = row[0] if row else 0
            cur = await db.execute(
                "SELECT status, COUNT(*) AS cnt FROM signal_queue GROUP BY status"
            )
            by_status = {r["status"]: r["cnt"] for r in await cur.fetchall()}
            return {"total": total, "by_status": by_status}
        except Exception:
            return {"error": "table_missing", "table": "signal_queue"}

    @staticmethod
    async def _q_dedup_pairs(db: Any) -> dict[str, Any]:
        try:
            cur = await db.execute("SELECT COUNT(*) FROM dedup_rejected_pairs")
            row = await cur.fetchone()
            return {"total": row[0] if row else 0}
        except Exception:
            return {"error": "table_missing", "table": "dedup_rejected_pairs"}

    async def _query_vault_state(
        self,
        vault_root: Path | None = None,
    ) -> dict[str, Any]:
        """Inspect the _KoraMemory/ vault filesystem.

        Counts notes per folder, detects working docs in Inbox via
        ``pipeline:`` frontmatter, and computes rough wikilink density.

        Defaults to ``data/_KoraMemory/`` relative to PROJECT_ROOT, but
        accepts an override for tests. The walk is bounded to 500 files
        to avoid stalling on huge vaults — additional files are noted
        in the response.
        """
        import re as _re

        # Default vault root: settings.memory.kora_memory_path (which
        # resolves to ~/.kora/memory out of the box). Previously this
        # hard-coded ``data/_KoraMemory`` so the acceptance report said
        # "Vault not available" while working docs and bridges were
        # being written under the real settings root.
        if vault_root is None:
            if PROJECT_ROOT != _DEFAULT_PROJECT_ROOT:
                vault_root = PROJECT_ROOT / "data" / "_KoraMemory"
            else:
                try:
                    from kora_v2.core.settings import get_settings as _get_settings

                    vault_root = Path(
                        _get_settings().memory.kora_memory_path
                    ).expanduser()
                except Exception:  # noqa: BLE001
                    vault_root = PROJECT_ROOT / "data" / "_KoraMemory"

        result: dict[str, Any] = {
            "root": str(vault_root),
            "exists": vault_root.exists(),
            "counts": {
                "total_notes": 0,
                "long_term_episodic": 0,
                "long_term_reflective": 0,
                "long_term_procedural": 0,
                "user_model": 0,
                "entities_people": 0,
                "entities_places": 0,
                "entities_projects": 0,
                "inbox": 0,
                "references": 0,
                "ideas": 0,
                "sessions": 0,
                "moc_pages": 0,
            },
            "working_docs": [],
            "wikilink_density": {
                "notes_with_wikilinks": 0,
                "total_wikilinks": 0,
            },
            "folder_hierarchy_present": False,
        }

        if not vault_root.exists():
            return result

        # Folder-counter mapping (relative path prefix → counts key).
        folder_map: tuple[tuple[str, str], ...] = (
            ("Long-Term/Episodic", "long_term_episodic"),
            ("Long-Term/Reflective", "long_term_reflective"),
            ("Long-Term/Procedural", "long_term_procedural"),
            ("User Model", "user_model"),
            ("Entities/People", "entities_people"),
            ("Entities/Places", "entities_places"),
            ("Entities/Projects", "entities_projects"),
            ("Inbox", "inbox"),
            ("References", "references"),
            ("Ideas", "ideas"),
            ("Sessions", "sessions"),
            ("Maps of Content", "moc_pages"),
        )
        wikilink_re = _re.compile(r"\[\[[^\]]+\]\]")
        # Match the leading ``pipeline:`` key inside a YAML frontmatter
        # block. Frontmatter starts at line 1 with ``---``, ends with the
        # next ``---``, and we only need to detect presence.
        frontmatter_pipeline_re = _re.compile(
            r"^---\s*\n(.*?)^---\s*$", _re.DOTALL | _re.MULTILINE,
        )
        pipeline_key_re = _re.compile(r"^pipeline\s*:\s*(.+)$", _re.MULTILINE)
        status_key_re = _re.compile(r"^status\s*:\s*(.+)$", _re.MULTILINE)

        def _priority(path: Path) -> tuple[int, str]:
            try:
                rel = path.relative_to(vault_root)
                parts = rel.parts
            except ValueError:
                return (99, str(path))
            if parts[:1] == ("Entities",):
                return (0, str(rel))
            if parts[:1] == ("Sessions",):
                return (1, str(rel))
            if parts[:1] == ("Maps of Content",):
                return (2, str(rel))
            if parts[:1] == ("Inbox",):
                return (3, str(rel))
            return (10, str(rel))

        paths = sorted(vault_root.rglob("*.md"), key=_priority)
        files_walked = 0
        max_files = 500
        truncated = False

        for path in paths:
            if files_walked >= max_files:
                truncated = True
                break
            files_walked += 1

            try:
                rel = path.relative_to(vault_root)
                rel_str = str(rel)
            except ValueError:
                continue

            result["counts"]["total_notes"] += 1

            for prefix, key in folder_map:
                if rel_str.startswith(prefix):
                    result["counts"][key] += 1
                    break

            # Read content (best-effort) for frontmatter + wikilink scan.
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            # Working doc detection: inside Inbox and frontmatter has
            # ``pipeline:`` key.
            if rel_str.startswith("Inbox"):
                fm_match = frontmatter_pipeline_re.search(text)
                if fm_match:
                    fm = fm_match.group(1)
                    p_match = pipeline_key_re.search(fm)
                    s_match = status_key_re.search(fm)
                    if p_match:
                        try:
                            stat = path.stat()
                            mtime = datetime.fromtimestamp(
                                stat.st_mtime, tz=UTC
                            ).isoformat()
                            size = stat.st_size
                        except Exception:
                            mtime = None
                            size = 0
                        result["working_docs"].append({
                            "path": str(path),
                            "pipeline_name": p_match.group(1).strip(),
                            "status": s_match.group(1).strip() if s_match else None,
                            "mtime": mtime,
                            "size_bytes": size,
                        })

            # Wikilink density.
            matches = wikilink_re.findall(text)
            if matches:
                result["wikilink_density"]["notes_with_wikilinks"] += 1
                result["wikilink_density"]["total_wikilinks"] += len(matches)

        # The bounded content scan above protects huge user vaults, but
        # coverage gates for Entities/Sessions/MOCs need folder counts
        # that are not biased by traversal order or the 500-file cap.
        for prefix, key in folder_map:
            folder = vault_root / prefix
            if folder.exists():
                result["counts"][key] = sum(1 for _ in folder.rglob("*.md"))

        result["files_walked"] = files_walked
        result["truncated"] = truncated

        # Folder hierarchy: at least the canonical top-level dirs exist.
        expected_top = (
            "Long-Term", "User Model", "Entities", "Inbox",
            "Sessions", "Maps of Content",
        )
        result["folder_hierarchy_present"] = all(
            (vault_root / sub).exists() for sub in expected_top
        )

        return result

    async def _query_proactive_state(
        self,
        db_path: Path | None = None,
    ) -> dict[str, Any]:
        """Query notifications, reminders, insights (Phase 8)."""
        op_db = db_path or (PROJECT_ROOT / "data" / "operational.db")
        result: dict[str, Any] = {"available": op_db.exists()}

        if not op_db.exists():
            result["error"] = "db_missing"
            return result

        try:
            import aiosqlite
        except ImportError:
            return {"available": False, "error": "aiosqlite not installed"}

        try:
            async with aiosqlite.connect(str(op_db)) as db:
                db.row_factory = aiosqlite.Row
                result["notifications"] = await self._q_notifications(db)
                result["reminders"] = await self._q_reminders(db)
        except Exception as e:
            result["available"] = False
            result["error"] = f"connect_failed: {e}"

        # Insights are not persisted to a table; ContextEngine emits
        # INSIGHT_AVAILABLE events. AT3 will wire event-stream tracking.
        result["insights"] = {
            "persisted": False,
            "total_if_persisted": None,
            "note": "ContextEngine emits INSIGHT_AVAILABLE events; "
                    "AT3 will track via event stream",
        }
        return result

    @staticmethod
    async def _q_notifications(db: Any, limit: int = 20) -> dict[str, Any]:
        """Summarise the notifications table with up to ``limit`` recent rows."""
        try:
            cur = await db.execute("SELECT COUNT(*) FROM notifications")
            row = await cur.fetchone()
            total = row[0] if row else 0

            by_tier: dict[str, int] = {}
            try:
                cur = await db.execute(
                    "SELECT delivery_tier, COUNT(*) AS cnt FROM notifications "
                    "GROUP BY delivery_tier"
                )
                by_tier = {
                    (r["delivery_tier"] or "unknown"): r["cnt"]
                    for r in await cur.fetchall()
                }
            except Exception:
                by_tier = {"_error": "delivery_tier_column_missing"}  # type: ignore[dict-item]

            by_reason: dict[str, int] = {}
            try:
                cur = await db.execute(
                    "SELECT reason, COUNT(*) AS cnt FROM notifications "
                    "GROUP BY reason"
                )
                by_reason = {
                    (r["reason"] or "none"): r["cnt"]
                    for r in await cur.fetchall()
                }
            except Exception:
                by_reason = {"_error": "reason_column_missing"}  # type: ignore[dict-item]

            recent: list[dict[str, Any]] = []
            try:
                cur = await db.execute(
                    """SELECT id, priority, content, category, delivered_at,
                              acknowledged_at, delivery_tier, reason
                       FROM notifications ORDER BY delivered_at DESC LIMIT ?""",
                    (limit,),
                )
                recent = [dict(r) for r in await cur.fetchall()]
            except Exception:
                # Fall back to columns guaranteed by the base schema.
                try:
                    cur = await db.execute(
                        """SELECT id, priority, content, category, delivered_at
                           FROM notifications ORDER BY delivered_at DESC LIMIT ?""",
                        (limit,),
                    )
                    recent = [dict(r) for r in await cur.fetchall()]
                except Exception:
                    recent = []

            return {
                "total": total,
                "by_tier": by_tier,
                "by_reason": by_reason,
                "recent": recent,
            }
        except Exception:
            return {"error": "table_missing", "table": "notifications"}

    @staticmethod
    async def _q_reminders(db: Any) -> dict[str, Any]:
        try:
            cur = await db.execute("SELECT COUNT(*) FROM reminders")
            row = await cur.fetchone()
            total = row[0] if row else 0

            cur = await db.execute(
                "SELECT status, COUNT(*) AS cnt FROM reminders GROUP BY status"
            )
            by_status = {r["status"]: r["cnt"] for r in await cur.fetchall()}

            # Mean delivery slip: delivered_at - due_at over delivered
            # rows. Both columns are added by Phase 8e migrations.
            mean_slip: float | None = None
            try:
                cur = await db.execute(
                    "SELECT due_at, delivered_at FROM reminders "
                    "WHERE delivered_at IS NOT NULL AND due_at IS NOT NULL "
                    "LIMIT 200"
                )
                slips: list[float] = []
                for r in await cur.fetchall():
                    try:
                        d_due = datetime.fromisoformat(
                            str(r["due_at"]).replace("Z", "+00:00")
                        )
                        d_del = datetime.fromisoformat(
                            str(r["delivered_at"]).replace("Z", "+00:00")
                        )
                        slips.append((d_del - d_due).total_seconds())
                    except Exception:
                        continue
                if slips:
                    mean_slip = sum(slips) / len(slips)
            except Exception:
                pass

            recent: list[dict[str, Any]] = []
            try:
                cur = await db.execute(
                    """SELECT id, title, status, due_at, delivered_at,
                              dismissed_at, source, created_at
                       FROM reminders ORDER BY created_at DESC LIMIT 20"""
                )
                recent = [dict(r) for r in await cur.fetchall()]
            except Exception:
                try:
                    cur = await db.execute(
                        """SELECT id, title, status, created_at
                           FROM reminders ORDER BY created_at DESC LIMIT 20"""
                    )
                    recent = [dict(r) for r in await cur.fetchall()]
                except Exception:
                    recent = []

            return {
                "total": total,
                "by_status": by_status,
                "mean_delivery_slip_seconds": mean_slip,
                "recent": recent,
            }
        except Exception:
            return {"error": "table_missing", "table": "reminders"}

    # ── Snapshot diff helpers ────────────────────────────────────────────

    @staticmethod
    def _diff_orchestration(
        s1: dict[str, Any], s2: dict[str, Any], lines: list[str],
    ) -> None:
        o1 = s1.get("orchestration_state") or {}
        o2 = s2.get("orchestration_state") or {}
        if not (o1.get("available") or o2.get("available")):
            return

        section_started = False

        def _start_section() -> None:
            nonlocal section_started
            if not section_started:
                lines.append("")
                lines.append("## Orchestration (Phase 7.5)")
                section_started = True

        # Pipeline instances
        p1 = o1.get("pipeline_instances") or {}
        p2 = o2.get("pipeline_instances") or {}
        if not p1.get("error") and not p2.get("error"):
            t1, t2 = p1.get("total", 0), p2.get("total", 0)
            if t1 != t2:
                _start_section()
                lines.append(
                    f"Pipeline instances: {t1} → {t2} (Δ{t2 - t1:+d})"
                )
            bs1 = p1.get("by_state", {}) or {}
            bs2 = p2.get("by_state", {}) or {}
            for st in sorted(set(bs1) | set(bs2)):
                c1, c2 = bs1.get(st, 0), bs2.get(st, 0)
                if c1 != c2:
                    _start_section()
                    lines.append(
                        f"  pipeline_instances[state={st}]: {c1} → {c2} (Δ{c2 - c1:+d})"
                    )
            bn1 = p1.get("by_name", {}) or {}
            bn2 = p2.get("by_name", {}) or {}
            for name in sorted(set(bn1) | set(bn2)):
                c1, c2 = bn1.get(name, 0), bn2.get(name, 0)
                if c1 != c2:
                    _start_section()
                    lines.append(
                        f"  pipeline_instances[name={name}]: {c1} → {c2} (Δ{c2 - c1:+d})"
                    )

        # Worker tasks
        w1 = o1.get("worker_tasks") or {}
        w2 = o2.get("worker_tasks") or {}
        if not w1.get("error") and not w2.get("error"):
            t1, t2 = w1.get("total", 0), w2.get("total", 0)
            if t1 != t2:
                _start_section()
                lines.append(
                    f"Worker tasks: {t1} → {t2} (Δ{t2 - t1:+d})"
                )
            a1, a2 = w1.get("active_count", 0), w2.get("active_count", 0)
            if a1 != a2:
                _start_section()
                lines.append(
                    f"  worker_tasks[active]: {a1} → {a2} (Δ{a2 - a1:+d})"
                )

        # Work ledger event types
        wl1 = o1.get("work_ledger") or {}
        wl2 = o2.get("work_ledger") or {}
        if not wl1.get("error") and not wl2.get("error"):
            t1, t2 = wl1.get("total", 0), wl2.get("total", 0)
            if t1 != t2:
                _start_section()
                lines.append(f"Work ledger events: {t1} → {t2} (Δ{t2 - t1:+d})")
            e1 = wl1.get("by_event_type", {}) or {}
            e2 = wl2.get("by_event_type", {}) or {}
            for et in sorted(set(e1) | set(e2)):
                c1, c2 = e1.get(et, 0), e2.get(et, 0)
                if c1 != c2:
                    _start_section()
                    lines.append(
                        f"  work_ledger[{et}]: {c1} → {c2} (Δ{c2 - c1:+d})"
                    )

        # System state log
        ss1 = o1.get("system_state_log") or {}
        ss2 = o2.get("system_state_log") or {}
        if not ss1.get("error") and not ss2.get("error"):
            t1, t2 = ss1.get("transitions_total", 0), ss2.get("transitions_total", 0)
            if t1 != t2:
                _start_section()
                lines.append(
                    f"Phase transitions: {t1} → {t2} (Δ{t2 - t1:+d})"
                )
            cur1, cur2 = ss1.get("current_phase"), ss2.get("current_phase")
            if cur1 != cur2:
                _start_section()
                lines.append(f"Current phase: {cur1} → {cur2}")

        # Request limiter
        rl1 = o1.get("request_limiter") or {}
        rl2 = o2.get("request_limiter") or {}
        if not rl1.get("error") and not rl2.get("error"):
            c1 = rl1.get("by_class", {}) or {}
            c2 = rl2.get("by_class", {}) or {}
            for cls in sorted(set(c1) | set(c2)):
                v1, v2 = c1.get(cls, 0), c2.get(cls, 0)
                if v1 != v2:
                    _start_section()
                    lines.append(
                        f"  request_limiter[{cls}]: {v1} → {v2} (Δ{v2 - v1:+d})"
                    )

    @staticmethod
    def _diff_memory_lifecycle(
        s1: dict[str, Any], s2: dict[str, Any], lines: list[str],
    ) -> None:
        m1 = s1.get("memory_lifecycle") or {}
        m2 = s2.get("memory_lifecycle") or {}
        if not (m1.get("available") or m2.get("available")):
            return

        section_started = False

        def _start_section() -> None:
            nonlocal section_started
            if not section_started:
                lines.append("")
                lines.append("## Memory lifecycle (Phase 8)")
                section_started = True

        for key in ("memories", "user_model_facts", "entities", "entity_links"):
            t1 = (m1.get(key) or {}).get("total", 0) or 0
            t2 = (m2.get(key) or {}).get("total", 0) or 0
            if t1 != t2:
                _start_section()
                lines.append(f"{key}: {t1} → {t2} (Δ{t2 - t1:+d})")

            bs1 = (m1.get(key) or {}).get("by_status", {}) or {}
            bs2 = (m2.get(key) or {}).get("by_status", {}) or {}
            if isinstance(bs1, dict) and isinstance(bs2, dict):
                for st in sorted(set(bs1) | set(bs2)):
                    c1, c2 = bs1.get(st, 0), bs2.get(st, 0)
                    if c1 != c2:
                        _start_section()
                        lines.append(
                            f"  {key}[{st}]: {c1} → {c2} (Δ{c2 - c1:+d})"
                        )

        # Sessions / signal_queue
        for key, label in [("sessions", "session_transcripts"),
                           ("signal_queue", "signal_queue"),
                           ("dedup_rejected_pairs", "dedup_rejected_pairs")]:
            d1 = m1.get(key) or {}
            d2 = m2.get(key) or {}
            if d1.get("error") or d2.get("error"):
                continue
            tot_key = "transcripts_total" if key == "sessions" else "total"
            t1, t2 = d1.get(tot_key, 0) or 0, d2.get(tot_key, 0) or 0
            if t1 != t2:
                _start_section()
                lines.append(f"{label}: {t1} → {t2} (Δ{t2 - t1:+d})")

    @staticmethod
    def _diff_vault(
        s1: dict[str, Any], s2: dict[str, Any], lines: list[str],
    ) -> None:
        v1 = s1.get("vault_state") or {}
        v2 = s2.get("vault_state") or {}
        if not (v1.get("exists") or v2.get("exists")):
            return

        section_started = False

        def _start_section() -> None:
            nonlocal section_started
            if not section_started:
                lines.append("")
                lines.append("## Vault (_KoraMemory/)")
                section_started = True

        c1 = v1.get("counts", {}) or {}
        c2 = v2.get("counts", {}) or {}
        for k in sorted(set(c1) | set(c2)):
            n1, n2 = c1.get(k, 0), c2.get(k, 0)
            if n1 != n2:
                _start_section()
                lines.append(f"  notes[{k}]: {n1} → {n2} (Δ{n2 - n1:+d})")

        # Working docs delta — set comparison by path.
        wd1 = {(w or {}).get("path") for w in (v1.get("working_docs") or [])}
        wd2 = {(w or {}).get("path") for w in (v2.get("working_docs") or [])}
        appeared = wd2 - wd1
        disappeared = wd1 - wd2
        if appeared:
            _start_section()
            lines.append(f"  working docs appeared: {len(appeared)}")
            for p in sorted(filter(None, appeared))[:5]:
                lines.append(f"    + {p}")
        if disappeared:
            _start_section()
            lines.append(f"  working docs disappeared: {len(disappeared)}")
            for p in sorted(filter(None, disappeared))[:5]:
                lines.append(f"    - {p}")

    @staticmethod
    def _diff_proactive(
        s1: dict[str, Any], s2: dict[str, Any], lines: list[str],
    ) -> None:
        p1 = s1.get("proactive_state") or {}
        p2 = s2.get("proactive_state") or {}
        if not (p1.get("available") or p2.get("available")):
            return

        section_started = False

        def _start_section() -> None:
            nonlocal section_started
            if not section_started:
                lines.append("")
                lines.append("## Proactive (notifications / reminders)")
                section_started = True

        n1 = p1.get("notifications") or {}
        n2 = p2.get("notifications") or {}
        if not n1.get("error") and not n2.get("error"):
            t1, t2 = n1.get("total", 0), n2.get("total", 0)
            if t1 != t2:
                _start_section()
                lines.append(f"Notifications: {t1} → {t2} (Δ{t2 - t1:+d})")

        r1 = p1.get("reminders") or {}
        r2 = p2.get("reminders") or {}
        if not r1.get("error") and not r2.get("error"):
            t1, t2 = r1.get("total", 0), r2.get("total", 0)
            if t1 != t2:
                _start_section()
                lines.append(f"Reminders: {t1} → {t2} (Δ{t2 - t1:+d})")
            bs1 = r1.get("by_status", {}) or {}
            bs2 = r2.get("by_status", {}) or {}
            for st in sorted(set(bs1) | set(bs2)):
                c1, c2 = bs1.get(st, 0), bs2.get(st, 0)
                if c1 != c2:
                    _start_section()
                    lines.append(
                        f"  reminders[{st}]: {c1} → {c2} (Δ{c2 - c1:+d})"
                    )

    async def _snapshot_full_state(self) -> dict[str, Any]:
        """Composite of every state-query method.

        Returned dict has keys for each AT2 dimension. Each query is
        independently fault-tolerant — a missing table or DB does not
        kill the whole snapshot.
        """
        return {
            "autonomous_state": await self._query_autonomous_state(),
            "orchestration_state": await self._query_orchestration_state(),
            "memory_lifecycle": await self._query_memory_lifecycle_state(),
            "vault_state": await self._query_vault_state(),
            "proactive_state": await self._query_proactive_state(),
        }

    async def cmd_idle_wait(
        self,
        min_soak: int = 60,
        timeout: int = 300,
        *,
        manifest: str | None = None,
    ) -> dict[str, Any]:
        """Wait min_soak seconds while monitoring daemon health and autonomous work.

        Phase 6 is live: idle phases now track autonomous session progress,
        item creation, checkpoint writes, and budget state — not just health.

        AT3: if ``manifest`` is provided, capture a before-snapshot of
        the full state, run the wait, then evaluate the named manifest
        against the before/after snapshots. Pass/fail plus per-check
        detail is returned in the result under ``manifest``.
        """
        start = time.monotonic()
        deadline = start + timeout
        polls = 0
        errors = 0
        disconnected_for_idle = await self._disconnect_for_idle()

        try:
            # Give the daemon a moment to process WebSocket close and
            # publish SESSION_END before measuring the idle soak.
            if disconnected_for_idle:
                await asyncio.sleep(1)

            # Snapshot autonomous state at start for delta calculation
            initial_auto = await self._query_autonomous_state()
            initial_items = initial_auto.get("total_items", 0)
            initial_checkpoints = initial_auto.get("checkpoint_count", 0)

            # AT3: capture the before-state for manifest evaluation (if asked).
            before_state: dict[str, Any] | None = None
            if manifest:
                try:
                    before_state = await self._snapshot_full_state()
                except Exception:
                    before_state = {}

            _update_monitor(
                self._state,
                f"idle-wait started: min_soak={min_soak}s timeout={timeout}s\n"
                f"autonomous: items={initial_items} active_plans={initial_auto.get('active_plan_count', 0)} "
                f"checkpoints={initial_checkpoints}",
            )

            while time.monotonic() < deadline:
                elapsed = time.monotonic() - start
                # Poll every 5 seconds
                await asyncio.sleep(5)
                polls += 1

                # Check daemon health
                is_healthy = await asyncio.get_event_loop().run_in_executor(None, self._check_health)
                if not is_healthy:
                    errors += 1
                    _log_event({"event": "idle_health_fail", "elapsed": elapsed, "errors": errors})
                    _update_monitor(self._state, f"idle-wait: health check failed (error #{errors})")
                    if errors >= 3:
                        return {
                            "elapsed": elapsed,
                            "polls": polls,
                            "errors": errors,
                            "error": "Daemon became unhealthy during idle wait",
                        }

                # Check autonomous state every 3 polls (~15s)
                if polls % 3 == 0:
                    current_auto = await self._query_autonomous_state()
                    current_items = current_auto.get("total_items", 0)
                    current_checkpoints = current_auto.get("checkpoint_count", 0)
                    active_plans = current_auto.get("active_plan_count", 0)
                    _update_monitor(
                        self._state,
                        f"idle-wait: elapsed={elapsed:.0f}s polls={polls}\n"
                        f"autonomous: items={current_items} (Δ{current_items - initial_items:+d}) "
                        f"active_plans={active_plans} checkpoints={current_checkpoints}",
                    )
                    _log_event({
                        "event": "idle_autonomous_check",
                        "elapsed": elapsed,
                        "items": current_items,
                        "items_delta": current_items - initial_items,
                        "active_plans": active_plans,
                        "checkpoints": current_checkpoints,
                        "checkpoints_delta": current_checkpoints - initial_checkpoints,
                    })

                if elapsed >= min_soak:
                    current_auto = await self._query_autonomous_state()
                    _update_monitor(self._state, f"idle-wait: complete after {elapsed:.0f}s")
                    result: dict[str, Any] = {
                        "elapsed": elapsed,
                        "polls": polls,
                        "errors": errors,
                        "quiescent": True,
                        "autonomous_state": current_auto,
                        "items_delta": current_auto.get("total_items", 0) - initial_items,
                        "checkpoints_delta": current_auto.get("checkpoint_count", 0) - initial_checkpoints,
                    }
                    if manifest:
                        result["manifest"] = await self._eval_idle_manifest(
                            manifest, before_state
                        )
                    return result

            elapsed = time.monotonic() - start
            current_auto = await self._query_autonomous_state()
            result_timeout: dict[str, Any] = {
                "elapsed": elapsed,
                "polls": polls,
                "errors": errors,
                "timeout": True,
                "autonomous_state": current_auto,
                "items_delta": current_auto.get("total_items", 0) - initial_items,
                "checkpoints_delta": current_auto.get("checkpoint_count", 0) - initial_checkpoints,
            }
            if manifest:
                result_timeout["manifest"] = await self._eval_idle_manifest(
                    manifest, before_state
                )
            return result_timeout
        finally:
            if disconnected_for_idle:
                await self._reconnect_after_idle()

    async def _eval_idle_manifest(
        self, manifest_name: str, before_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Evaluate the named manifest against before/after full-state snapshots."""
        from tests.acceptance.scenario.manifests import (
            SOAK_MANIFESTS,
            result_to_dict,
            run_manifest,
        )

        m = SOAK_MANIFESTS.get(manifest_name)
        if m is None:
            return {
                "error": f"Unknown soak manifest: {manifest_name}",
                "available": sorted(SOAK_MANIFESTS.keys()),
            }

        after = await self._snapshot_full_state()
        result = run_manifest(m, before_state or {}, after)
        return result_to_dict(result)

    def _check_health(self) -> bool:
        """Synchronous health probe."""
        data = _read_lockfile()
        if not data:
            return False
        port = data.get("api_port")
        host = data.get("api_host", "127.0.0.1")
        if not port:
            return False
        import urllib.error
        import urllib.request
        try:
            url = f"http://{host}:{port}/api/v1/health"
            with urllib.request.urlopen(url, timeout=3.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def cmd_advance(self, hours: float) -> dict[str, Any]:
        """Record a simulated time advance in session state."""
        self._state["simulated_hours_offset"] = (
            self._state.get("simulated_hours_offset", 0) + hours
        )
        adjusted: dict[str, int] = {}
        db_path = PROJECT_ROOT / "data" / "operational.db"
        if db_path.exists():
            try:
                import aiosqlite

                delta = timedelta(hours=float(hours))

                def shift_iso(value: str | None) -> str | None:
                    if not value:
                        return value
                    try:
                        parsed = datetime.fromisoformat(
                            str(value).replace("Z", "+00:00")
                        )
                    except (TypeError, ValueError):
                        return value
                    return (parsed - delta).isoformat()

                async with aiosqlite.connect(str(db_path)) as db:
                    db.row_factory = aiosqlite.Row
                    try:
                        cur = await db.execute(
                            "SELECT id, due_at FROM reminders "
                            "WHERE status != 'delivered' AND due_at IS NOT NULL"
                        )
                        rows = await cur.fetchall()
                        for row in rows:
                            await db.execute(
                                "UPDATE reminders SET due_at=? WHERE id=?",
                                (shift_iso(row["due_at"]), row["id"]),
                            )
                        adjusted["reminders"] = len(rows)
                    except Exception:
                        adjusted["reminders"] = 0
                    try:
                        cur = await db.execute(
                            "SELECT id, posed_at FROM open_decisions "
                            "WHERE status='open'"
                        )
                        rows = await cur.fetchall()
                        for row in rows:
                            await db.execute(
                                "UPDATE open_decisions SET posed_at=? WHERE id=?",
                                (shift_iso(row["posed_at"]), row["id"]),
                            )
                        adjusted["open_decisions"] = len(rows)
                    except Exception:
                        adjusted["open_decisions"] = 0
                    await db.commit()
            except Exception:
                adjusted["error"] = 1
        _save_session(self._state)
        _log_event({
            "event": "time_advance",
            "hours": hours,
            "total_offset": self._state["simulated_hours_offset"],
            "adjusted": adjusted,
        })
        _update_monitor(self._state)
        return {
            "hours_advanced": hours,
            "total_offset": self._state["simulated_hours_offset"],
            "adjusted": adjusted,
        }

    async def cmd_monitor(self) -> dict[str, Any]:
        """Return current monitor file contents."""
        if MONITOR_FILE.exists():
            return {"content": MONITOR_FILE.read_text()}
        return {"content": "(monitor not yet populated)"}

    async def cmd_report(self) -> dict[str, Any]:
        """Generate final acceptance report.

        Passes the in-memory compaction events explicitly so the report
        can display them — they live on ``self._compaction_events`` and
        were never serialized into session_state.
        """
        from tests.acceptance._report import build_report
        finalization = await self._drain_report_finalization()
        vault_finalization = await self._drain_vault_session_artifacts()
        path = await build_report(
            self._state,
            SNAPSHOTS_DIR,
            OUTPUT_DIR,
            compaction_events=list(self._compaction_events),
        )
        exports = {
            "conversation_json": OUTPUT_DIR / "acceptance_conversation.json",
            "conversation_markdown": OUTPUT_DIR / "acceptance_conversation.md",
            "demo_snapshot": OUTPUT_DIR / "acceptance_demo_snapshot.json",
        }
        return {
            "path": str(path),
            "exports": {
                key: str(export_path)
                for key, export_path in exports.items()
                if export_path.exists()
            },
            "finalization": finalization,
            "vault_finalization": vault_finalization,
        }

    @staticmethod
    def _acceptance_vault_session_state() -> dict[str, Any]:
        """Return the filesystem evidence used by coverage item 57."""
        vault_root = ACCEPT_DIR / "memory"
        sessions_dir = vault_root / "Sessions"
        index_file = sessions_dir / "index.md"
        session_notes = [
            path
            for path in sessions_dir.rglob("*.md")
            if path.name != "index.md"
        ] if sessions_dir.exists() else []
        return {
            "root": str(vault_root),
            "index_exists": index_file.exists(),
            "session_note_count": len(session_notes),
        }

    async def _drain_vault_session_artifacts(self) -> dict[str, Any]:
        """Wait briefly for post-vault files that coverage reads directly.

        The protected pipeline drain observes DB task state. The final
        filesystem writes for ``Sessions/index.md`` can land a few seconds
        later, so the report boundary also waits for the concrete vault
        artifacts that item 57 scores from.
        """
        timeout = float(
            os.environ.get("KORA_ACCEPTANCE_VAULT_DRAIN_SECONDS", "60")
        )
        timeout = max(0.0, min(timeout, 90.0))
        deadline = time.monotonic() + timeout
        state = self._acceptance_vault_session_state()
        if state["index_exists"] and state["session_note_count"] >= 1:
            return {"status": "ready", "timeout_s": timeout, **state}

        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            state = self._acceptance_vault_session_state()
            if state["index_exists"] and state["session_note_count"] >= 1:
                return {"status": "ready", "timeout_s": timeout, **state}

        return {"status": "timeout", "timeout_s": timeout, **state}

    async def _drain_report_finalization(self) -> dict[str, Any]:
        """Give protected memory/vault pipelines a bounded chance to finish.

        Normal background services may remain slow; this is only the
        acceptance report boundary, where the harness needs to avoid
        snapshotting immediately after it caused a SESSION_END memory pass.
        """
        timeout = float(os.environ.get("KORA_ACCEPTANCE_FINAL_DRAIN_SECONDS", "120"))
        timeout = max(0.0, min(timeout, 180.0))
        started = datetime.now(UTC)
        disconnected = False
        if timeout <= 0:
            return {"status": "skipped", "timeout_s": timeout}

        try:
            disconnected = await self._disconnect_for_idle()
        except Exception as exc:  # noqa: BLE001
            _log_event({"event": "report_finalization_disconnect_failed", "error": str(exc)})

        deadline = time.monotonic() + timeout
        quiet_polls = 0
        last_state: dict[str, Any] = {}

        while time.monotonic() < deadline:
            last_state = await self._query_protected_finalization_state(started)
            active = int(last_state.get("active_count", 0) or 0)
            if active == 0:
                quiet_polls += 1
                if quiet_polls >= 2:
                    status = "drained"
                    break
            else:
                quiet_polls = 0
            _update_monitor(
                self._state,
                "report finalization drain: "
                f"active protected pipelines={active}",
            )
            await asyncio.sleep(2.0)
        else:
            status = "timeout"

        # Do not reconnect before building the report. A reconnect opens a new
        # chat session; the next disconnect then creates fresh post-session and
        # post-vault work, so report generation can chase work it just caused.
        # Report generation only needs persisted snapshots and databases.

        elapsed = (datetime.now(UTC) - started).total_seconds()
        result = {
            "status": status,
            "elapsed_s": elapsed,
            "timeout_s": timeout,
            "disconnected_for_idle": disconnected,
            **last_state,
        }
        _log_event({"event": "report_finalization_drain", **result})
        return result

    @staticmethod
    async def _query_protected_finalization_state(
        since: datetime,
    ) -> dict[str, Any]:
        op_db = PROJECT_ROOT / "data" / "operational.db"
        if not op_db.exists():
            return {"active_count": 0, "error": "operational_db_missing"}
        try:
            import aiosqlite
        except ImportError:
            return {"active_count": 0, "error": "aiosqlite_missing"}

        names = (
            "post_session_memory",
            "post_memory_vault",
            "proactive_research",
            "user_autonomous_task",
            "cancel_probe",
        )
        active_states = ("pending", "running", "paused")
        task_active_states = (
            "pending",
            "running",
            "checkpointing",
            "paused_for_state",
            "paused_for_rate_limit",
            "paused_for_dependency",
        )
        try:
            async with aiosqlite.connect(str(op_db)) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    f"""
                    SELECT id, pipeline_name, state, started_at, updated_at,
                           completed_at, completion_reason
                    FROM pipeline_instances
                    WHERE pipeline_name IN ({",".join("?" for _ in names)})
                      AND state IN ({",".join("?" for _ in active_states)})
                    ORDER BY started_at DESC
                    """,
                    (*names, *active_states),
                )
                active = [dict(r) for r in await cur.fetchall()]

                cur = await db.execute(
                    f"""
                    SELECT wt.id, wt.pipeline_instance_id, pi.pipeline_name,
                           wt.stage_name, wt.state, wt.result_summary,
                           wt.error_message, wt.created_at, wt.last_step_at
                    FROM worker_tasks wt
                    JOIN pipeline_instances pi ON pi.id = wt.pipeline_instance_id
                    WHERE pi.pipeline_name IN ({",".join("?" for _ in names)})
                      AND wt.state IN ({",".join("?" for _ in task_active_states)})
                    ORDER BY wt.created_at DESC
                    LIMIT 20
                    """,
                    (*names, *task_active_states),
                )
                active_tasks = [dict(r) for r in await cur.fetchall()]

                cur = await db.execute(
                    f"""
                    SELECT pipeline_name, COUNT(*) AS cnt
                    FROM pipeline_instances
                    WHERE pipeline_name IN ({",".join("?" for _ in names)})
                      AND state='completed'
                      AND completed_at >= ?
                    GROUP BY pipeline_name
                    """,
                    (*names, since.isoformat()),
                )
                completed_since = {r["pipeline_name"]: r["cnt"] for r in await cur.fetchall()}
        except Exception as exc:  # noqa: BLE001
            return {"active_count": 0, "error": str(exc)}

        return {
            "active_count": len(active),
            "active": active,
            "active_tasks": active_tasks,
            "completed_since": completed_since,
        }

    async def cmd_test_auth(self) -> dict[str, Any]:
        """Enable auth test mode: deny first request, approve all subsequent."""
        self._auth_mode = "deny_once"
        self._auth_deny_count = 0
        return {
            "mode": "deny_once",
            "instructions": "Next auth request will be DENIED. Subsequent requests will be APPROVED.",
        }

    async def cmd_test_auth_reset(self) -> dict[str, Any]:
        """Restore auto-approve auth mode."""
        self._auth_mode = "auto"
        self._auth_deny_count = 0
        return {"mode": "auto"}

    async def cmd_test_error(self) -> dict[str, Any]:
        """Run error recovery tests: send malformed inputs, verify session survives."""
        results = []

        async def _reset_ws_after_probe() -> None:
            # Raw protocol-error probes can leave the websocket in a
            # half-closed state while the daemon restarts the service side.
            # Drop the harness connection and reconnect before the next
            # user-visible chat probe so recovery is measured from a clean
            # post-error session rather than a stale socket.
            if self._recv_task is not None:
                self._recv_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._recv_task
                self._recv_task = None
            if self._ws is not None:
                with contextlib.suppress(Exception):
                    await self._ws.close()
                self._ws = None
            self._response_data = None
            self._response_ready = None
            self._busy = False
            await asyncio.sleep(0.5)
            await self._ensure_ws_connected()

        async def _raw_ws_probe(test_name: str, payload: str) -> None:
            if not await self._ensure_ws_connected():
                results.append({
                    "test": test_name,
                    "survived": False,
                    "error": "Cannot connect to Kora daemon",
                })
                return
            if self._busy:
                results.append({
                    "test": test_name,
                    "survived": False,
                    "error": "Harness is busy with another request",
                })
                return
            self._busy = True
            self._response_data = {"tokens": [], "tool_calls": []}
            self._response_ready = asyncio.Event()
            try:
                await self._ws.send(payload)
                try:
                    await asyncio.wait_for(self._response_ready.wait(), timeout=20.0)
                except TimeoutError:
                    results.append({
                        "test": test_name,
                        "survived": False,
                        "error": "No graceful error response",
                    })
                    return
                error = self._response_data.get("error")
                results.append({
                    "test": test_name,
                    "survived": bool(error),
                    "response": error or "(no error frame)",
                    "error": None if error else "missing error frame",
                })
            except Exception as e:
                results.append({
                    "test": test_name,
                    "survived": False,
                    "error": str(e),
                })
            finally:
                self._busy = False
                self._response_data = None
                self._response_ready = None

        await _raw_ws_probe("malformed_json_frame", "{not valid json")
        await _reset_ws_after_probe()
        await _raw_ws_probe(
            "empty_chat_content",
            json.dumps({"type": "chat", "content": ""}),
        )
        await _reset_ws_after_probe()

        test_cases = [
            ("special_chars", "!@#$%^&*(){}[]|\\/<>?~`"),
            ("long_message", "test " * 500),
            ("unicode", "\u00e9\u00e8\u00ea \U0001F680\U0001F31F \u4f60\u597d\u4e16\u754c"),
            ("normal_after_errors", "hey, just checking you're still working fine"),
        ]

        for test_name, message in test_cases:
            try:
                result = await self.cmd_send(message or " ", timeout=240.0)
                survived = "error" not in result or not result.get("error")
                response = result.get("response", "")
                results.append({
                    "test": test_name,
                    "survived": survived,
                    "response": response[:120] if response else "(empty)",
                    "error": result.get("error"),
                })
                if not survived:
                    await _reset_ws_after_probe()
            except Exception as e:
                results.append({
                    "test": test_name,
                    "survived": False,
                    "error": str(e),
                })
                await _reset_ws_after_probe()

        all_survived = all(r.get("survived", False) for r in results)
        self._state["error_recovery_results"] = results
        _save_session(self._state)
        _log_event({
            "event": "error_recovery_complete",
            "all_survived": all_survived,
            "results": results,
        })
        return {"results": results, "all_survived": all_survived}

    async def cmd_compaction_status(self) -> dict[str, Any]:
        """Return compaction events detected during the test."""
        return {
            "compaction_detected": len(self._compaction_events) > 0,
            "event_count": len(self._compaction_events),
            "events": self._compaction_events,
        }

    async def cmd_skill_gating_check(self) -> dict[str, Any]:
        """Verify representative prompts activate distinct skill/tool sets."""
        try:
            from kora_v2.graph.supervisor import _infer_active_skills
            from kora_v2.skills.loader import SkillLoader

            loader = SkillLoader()
            loader.load_all()
            cases = {
                "life": "I just took my adderall and need a lunch reminder",
                "code": "Fix the failing python test in this repo",
                "web": "Research the latest local-first privacy tools",
            }
            results: dict[str, Any] = {}
            for name, text in cases.items():
                skills = _infer_active_skills(text, loader)
                tools = loader.get_active_tools(skills)
                results[name] = {"skills": skills, "tools": tools}

            passed = (
                "life_management" in results["life"]["skills"]
                and "code_work" not in results["life"]["skills"]
                and "web_research" not in results["life"]["skills"]
                and "code_work" in results["code"]["skills"]
                and "life_management" not in results["code"]["skills"]
                and "web_research" in results["web"]["skills"]
                and "life_management" not in results["web"]["skills"]
            )
            out = {"passed": passed, "cases": results}
            self._state["skill_gating_check"] = out
            _save_session(self._state)
            _log_event({"event": "skill_gating_check", **out})
            return out
        except Exception as exc:
            return {"passed": False, "error": str(exc)}

    async def cmd_life_management_check(self) -> dict[str, Any]:
        """Query life management DB tables."""
        op_db = PROJECT_ROOT / "data" / "operational.db"
        if not op_db.exists():
            return {"available": False, "error": "operational.db not found"}

        try:
            import aiosqlite
        except ImportError:
            return {"available": False, "error": "aiosqlite not installed"}

        result: dict[str, Any] = {"available": True}

        async with aiosqlite.connect(str(op_db)) as db:
            db.row_factory = aiosqlite.Row

            # Medications
            try:
                cur = await db.execute(
                    "SELECT * FROM medication_log ORDER BY taken_at DESC LIMIT 20"
                )
                rows = [dict(r) for r in await cur.fetchall()]
                result["medication_log"] = rows
                result["medication_count"] = len(rows)
            except Exception:
                result["medication_log"] = []
                result["medication_count"] = 0

            # Meals
            try:
                cur = await db.execute(
                    "SELECT * FROM meal_log ORDER BY logged_at DESC LIMIT 20"
                )
                rows = [dict(r) for r in await cur.fetchall()]
                result["meal_log"] = rows
                result["meal_count"] = len(rows)
            except Exception:
                result["meal_log"] = []
                result["meal_count"] = 0

            # Reminders
            try:
                cur = await db.execute(
                    "SELECT * FROM reminders ORDER BY created_at DESC LIMIT 20"
                )
                rows = [dict(r) for r in await cur.fetchall()]
                result["reminders"] = rows
                result["reminder_count"] = len(rows)
            except Exception:
                result["reminders"] = []
                result["reminder_count"] = 0

            # Quick notes
            try:
                cur = await db.execute(
                    "SELECT * FROM quick_notes ORDER BY created_at DESC LIMIT 20"
                )
                rows = [dict(r) for r in await cur.fetchall()]
                result["quick_notes"] = rows
                result["quick_note_count"] = len(rows)
            except Exception:
                result["quick_notes"] = []
                result["quick_note_count"] = 0

            # Focus blocks
            try:
                cur = await db.execute(
                    "SELECT * FROM focus_blocks ORDER BY started_at DESC LIMIT 20"
                )
                rows = [dict(r) for r in await cur.fetchall()]
                result["focus_blocks"] = rows
                result["focus_block_count"] = len(rows)
            except Exception:
                result["focus_blocks"] = []
                result["focus_block_count"] = 0

        # Post-Phase 8: enrich with memory / vault / reminder deliveries so
        # `life-management-check` answers "did things actually happen?" —
        # not just "were life tools invoked?".
        try:
            memory = await self._query_memory_lifecycle_state()
            mem_summary: dict[str, Any] = {}
            for k in ("memories", "user_model_facts", "entities"):
                entry = memory.get(k) or {}
                mem_summary[k] = {
                    "total": entry.get("total", 0),
                    "by_status": entry.get("by_status", {}),
                }
            mem_summary["sessions"] = memory.get("sessions", {})
            mem_summary["signal_queue"] = memory.get("signal_queue", {})
            result["memory_lifecycle"] = mem_summary
        except Exception as e:
            result["memory_lifecycle"] = {"error": str(e)}

        try:
            vault = await self._query_vault_state()
            result["vault_snapshot"] = {
                "root": vault.get("root"),
                "exists": vault.get("exists", False),
                "counts": vault.get("counts", {}),
                "working_docs_count": len(vault.get("working_docs", [])),
                "folder_hierarchy_present": vault.get("folder_hierarchy_present", False),
            }
        except Exception as e:
            result["vault_snapshot"] = {"error": str(e)}

        try:
            proactive = await self._query_proactive_state()
            reminder_stats = proactive.get("reminders") or {}
            result["reminder_delivery"] = {
                "total": reminder_stats.get("total", 0),
                "by_status": reminder_stats.get("by_status", {}),
                "mean_delivery_slip_seconds":
                    reminder_stats.get("mean_delivery_slip_seconds"),
            }
            notif_stats = proactive.get("notifications") or {}
            result["notifications_summary"] = {
                "total": notif_stats.get("total", 0),
                "by_tier": notif_stats.get("by_tier", {}),
                "by_reason": notif_stats.get("by_reason", {}),
            }
        except Exception as e:
            result["reminder_delivery"] = {"error": str(e)}
            result["notifications_summary"] = {"error": str(e)}

        return result

    async def cmd_tool_usage_summary(self) -> dict[str, Any]:
        """Analyze tool usage from conversation history.

        Buckets mirror ``tests/acceptance/_report.py:TOOL_BUCKETS`` — the
        two call sites must stay aligned. ``tests/unit/acceptance/
        test_tool_buckets.py`` enforces that.

        ``orchestration_tools_used`` replaces the retired
        ``autonomous_tools_used``/``start_autonomous`` bucket
        (Phase 7.5 retired ``start_autonomous``). ``pipelines_fired`` is
        an AT3 placeholder — pipelines are triggered, not called as
        tools, so the answer requires a separate query against
        ``pipeline_instances``.
        """
        from tests.acceptance._report import TOOL_BUCKETS

        tool_counts: dict[str, int] = {}
        for msg in self._state.get("messages", []):
            for tc in msg.get("tool_calls", []):
                # Strip auth prefix if present
                name = tc
                if name.startswith("[auth:"):
                    name = name.split(":")[1].rstrip("]").split(":")[0]
                tool_counts[name] = tool_counts.get(name, 0) + 1
        for name, count in _turn_trace_tool_counts(PROJECT_ROOT / "data" / "operational.db").items():
            tool_counts[name] = tool_counts.get(name, 0) + count

        return {
            "total_tool_calls": sum(tool_counts.values()),
            "unique_tools": len(tool_counts),
            "tool_counts": tool_counts,
            "life_management_tools_used": sorted(
                set(tool_counts) & TOOL_BUCKETS["life_tools"]
            ),
            "filesystem_tools_used": sorted(
                set(tool_counts) & TOOL_BUCKETS["filesystem_tools"]
            ),
            "mcp_tools_used": sorted(
                set(tool_counts) & TOOL_BUCKETS["mcp_tools"]
            ),
            "orchestration_tools_used": sorted(
                set(tool_counts) & TOOL_BUCKETS["orchestration_tools"]
            ),
            "memory_tools_used": sorted(
                set(tool_counts) & TOOL_BUCKETS["memory_tools"]
            ),
            # AT3 placeholder — populated from pipeline_instances later.
            "pipelines_fired": [],
        }

    async def cmd_capability_health_check(self) -> dict[str, Any]:
        """Invoke each capability pack's health_check() and return results.

        Read-only — does not start any MCP servers or spawn binaries beyond
        what health_check() itself does.  Returns a dict with pack names as
        keys and CapabilityHealth data as values.
        """
        try:
            from kora_v2.capabilities.registry import get_all_capabilities
        except ImportError:
            return {"error": "kora_v2.capabilities not importable"}

        results: dict[str, Any] = {}
        packs = get_all_capabilities()
        for pack in packs:
            try:
                health = await pack.health_check()
                results[pack.name] = {
                    "status": str(health.status),
                    "summary": health.summary,
                    "remediation": health.remediation,
                    "details": health.details,
                }
            except Exception as exc:
                results[pack.name] = {
                    "status": "error",
                    "summary": f"health_check() raised: {exc}",
                    "remediation": None,
                    "details": {},
                }

        _log_event({"event": "capability_health_check", "packs": list(results.keys())})
        return results

    # ── AT2 state-query CLI commands ──────────────────────────────────────

    async def cmd_orchestration_status(self) -> dict[str, Any]:
        """Return orchestration state summary (pipelines, ledger, phase)."""
        return await self._query_orchestration_state()

    async def cmd_pipeline_history(self, limit: int = 20) -> dict[str, Any]:
        """Return recent pipeline_instances with durations."""
        op_db = PROJECT_ROOT / "data" / "operational.db"
        if not op_db.exists():
            return {"available": False, "error": "db_missing"}
        try:
            import aiosqlite
        except ImportError:
            return {"available": False, "error": "aiosqlite not installed"}

        safe_limit = max(1, min(int(limit), 100))
        try:
            async with aiosqlite.connect(str(op_db)) as db:
                db.row_factory = aiosqlite.Row
                try:
                    cur = await db.execute(
                        """SELECT id, pipeline_name, state, goal, started_at,
                                  updated_at, completed_at, completion_reason
                           FROM pipeline_instances
                           ORDER BY started_at DESC LIMIT ?""",
                        (safe_limit,),
                    )
                    rows = [dict(r) for r in await cur.fetchall()]
                except Exception:
                    return {
                        "available": False,
                        "error": "table_missing",
                        "table": "pipeline_instances",
                    }

            for r in rows:
                started = r.get("started_at")
                completed = r.get("completed_at")
                duration_s: float | None = None
                if started and completed:
                    try:
                        s = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                        e = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
                        duration_s = (e - s).total_seconds()
                    except Exception:
                        pass
                r["duration_s"] = duration_s

            return {"available": True, "count": len(rows), "pipelines": rows}
        except Exception as e:
            return {"available": False, "error": str(e)}

    async def cmd_working_docs(self) -> dict[str, Any]:
        """List _KoraMemory/Inbox/*.md files with ``pipeline:`` frontmatter."""
        state = await self._query_vault_state()
        return {
            "available": state.get("exists", False),
            "root": state.get("root"),
            "working_docs": state.get("working_docs", []),
            "count": len(state.get("working_docs", [])),
        }

    async def cmd_edit_working_doc(
        self,
        text: str = "user-added acceptance plan item",
    ) -> dict[str, Any]:
        """Append an unchecked Current Plan item to the newest live working doc."""
        state = await self._query_vault_state()
        docs = [
            d for d in state.get("working_docs", [])
            if str(d.get("status") or "").strip() not in {
                "done", "failed", "cancelled",
            }
        ]
        if not docs:
            return {"ok": False, "error": "no active working docs"}
        user_docs = [
            d for d in docs
            if str(d.get("pipeline_name") or "") not in {
                "anticipatory_prep",
                "commitment_tracking",
                "connection_making",
                "contextual_engagement",
                "continuity_check",
                "post_memory_vault",
                "post_session_memory",
                "proactive_pattern_scan",
                "routine_morning_launch",
                "session_bridge_pruning",
                "skill_refinement",
                "stuck_detection",
                "wake_up_preparation",
                "weekly_adhd_profile",
            }
        ]
        if user_docs:
            docs = user_docs
        docs.sort(key=lambda d: str(d.get("mtime") or ""), reverse=True)
        path = Path(str(docs[0].get("path")))
        item_text = str(text).strip() or "user-added acceptance plan item"
        try:
            content = path.read_text(encoding="utf-8")
            line = f"- [ ] {item_text}"
            if line in content:
                return {
                    "ok": True,
                    "path": str(path),
                    "added": False,
                    "text": item_text,
                }
            lines = content.splitlines()
            insert_at: int | None = None
            for idx, existing in enumerate(lines):
                if existing.strip() != "# Current Plan":
                    continue
                insert_at = len(lines)
                for next_idx in range(idx + 1, len(lines)):
                    if (
                        lines[next_idx].startswith("# ")
                        and lines[next_idx].strip() != "# Current Plan"
                    ):
                        insert_at = next_idx
                        break
                break
            if insert_at is None:
                if lines and lines[-1].strip():
                    lines.append("")
                lines.extend(["# Current Plan", line])
            else:
                if insert_at > 0 and lines[insert_at - 1].strip():
                    lines.insert(insert_at, line)
                else:
                    lines.insert(insert_at, line)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            _log_event({
                "event": "working_doc_edited",
                "path": str(path),
                "text": item_text,
            })
            return {
                "ok": True,
                "path": str(path),
                "added": True,
                "text": item_text,
            }
        except Exception as exc:
            return {"ok": False, "path": str(path), "error": str(exc)}

    async def cmd_notifications(self, limit: int = 20) -> dict[str, Any]:
        """Return recent notifications with tier and reason."""
        op_db = PROJECT_ROOT / "data" / "operational.db"
        if not op_db.exists():
            return {"available": False, "error": "db_missing"}
        try:
            import aiosqlite
        except ImportError:
            return {"available": False, "error": "aiosqlite not installed"}

        safe_limit = max(1, min(int(limit), 100))
        try:
            async with aiosqlite.connect(str(op_db)) as db:
                db.row_factory = aiosqlite.Row
                notif = await self._q_notifications(db, limit=safe_limit)
                return {"available": True, **notif}
        except Exception as e:
            return {"available": False, "error": str(e)}

    async def cmd_insights(self, limit: int = 20) -> dict[str, Any]:
        """Return recent INSIGHT_AVAILABLE events (placeholder)."""
        op_db = PROJECT_ROOT / "data" / "operational.db"
        if not op_db.exists():
            return {"available": False, "error": "db_missing"}
        try:
            import aiosqlite
        except ImportError:
            return {"available": False, "error": "aiosqlite not installed"}

        safe_limit = max(1, min(int(limit), 100))
        result: dict[str, Any] = {
            "available": True,
            "persisted": False,
            "note": "Insights are not persisted to a table; "
                    "ContextEngine emits INSIGHT_AVAILABLE events. "
                    "AT3 will wire event-stream tracking.",
            "events": [],
        }
        # Best-effort: if there is an event_log or similar we can scrape it.
        # The live implementation emits over EventEmitter, not SQL, so this
        # stays empty until AT3 adds the event stream tap.
        try:
            async with aiosqlite.connect(str(op_db)) as db:
                db.row_factory = aiosqlite.Row
                try:
                    cur = await db.execute(
                        "SELECT event_type, metadata_json, timestamp "
                        "FROM work_ledger WHERE event_type LIKE 'insight_%' "
                        "ORDER BY id DESC LIMIT ?",
                        (safe_limit,),
                    )
                    result["events"] = [dict(r) for r in await cur.fetchall()]
                except Exception:
                    pass
        except Exception:
            pass
        return result

    async def cmd_phase_history(self, hours: int = 24) -> dict[str, Any]:
        """Return SystemStatePhase transitions over the last N hours."""
        op_db = PROJECT_ROOT / "data" / "operational.db"
        if not op_db.exists():
            return {"available": False, "error": "db_missing"}
        try:
            import aiosqlite
        except ImportError:
            return {"available": False, "error": "aiosqlite not installed"}

        safe_hours = max(1, min(int(hours), 24 * 30))
        try:
            async with aiosqlite.connect(str(op_db)) as db:
                db.row_factory = aiosqlite.Row
                try:
                    cur = await db.execute(
                        """SELECT previous_phase, new_phase, transitioned_at, reason
                           FROM system_state_log
                           WHERE transitioned_at >= datetime('now', ?)
                           ORDER BY id DESC LIMIT 200""",
                        (f"-{safe_hours} hours",),
                    )
                    transitions = [dict(r) for r in await cur.fetchall()]
                except Exception:
                    return {
                        "available": False,
                        "error": "table_missing",
                        "table": "system_state_log",
                    }
            return {
                "available": True,
                "hours": safe_hours,
                "count": len(transitions),
                "transitions": transitions,
            }
        except Exception as e:
            return {"available": False, "error": str(e)}

    async def cmd_vault_snapshot(self) -> dict[str, Any]:
        """Print vault counts, folder hierarchy check, working-doc count."""
        return await self._query_vault_state()

    # ── AT3 commands: manifests, phase gates, benchmarks, event tail ──────

    async def cmd_soak_manifest(
        self,
        phase: str,
        *,
        before_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the named soak manifest against the current state.

        If ``before_state`` is omitted, only the present-day checks fire
        (pipelines already present in the snapshot count as "satisfied",
        since ``before`` is an empty snapshot). The harness's
        ``cmd_idle_wait(--manifest)`` captures a proper before-snapshot
        and passes it in.
        """
        from tests.acceptance.scenario.manifests import (
            SOAK_MANIFESTS,
            result_to_dict,
            run_manifest,
        )

        manifest = SOAK_MANIFESTS.get(phase)
        if manifest is None:
            return {
                "error": f"Unknown soak manifest: {phase}",
                "available": sorted(SOAK_MANIFESTS.keys()),
            }

        before = before_state or {}
        after = await self._snapshot_full_state()
        result = run_manifest(manifest, before, after)
        return {
            "manifest": {
                "phase_name": manifest.phase_name,
                "min_soak_seconds": manifest.min_soak_seconds,
                "timeout_seconds": manifest.timeout_seconds,
                "expected_pipelines": list(manifest.expected_pipelines),
                "expected_ledger_events": list(manifest.expected_ledger_events),
                "expected_phase_transitions": list(
                    manifest.expected_phase_transitions
                ),
            },
            "result": result_to_dict(result),
        }

    async def cmd_phase_gate(
        self,
        phase_name: str,
        coverage_items: list[int] | None = None,
    ) -> dict[str, Any]:
        """Run the phase-gate check for ``phase_name``.

        If ``coverage_items`` is omitted, the gate is run against the
        phase's declared ``coverage_items`` from WEEK_PLAN / FAST_PLAN
        (with WEEK_PLAN taking precedence on name collision).
        """
        from tests.acceptance.scenario.gates import result_to_dict, run_phase_gate
        from tests.acceptance.scenario.week_plan import FAST_PLAN, WEEK_PLAN

        phase_found = coverage_items is not None
        if coverage_items is None:
            coverage_items = []
            for plan in (WEEK_PLAN, FAST_PLAN):
                for day in plan.values():
                    for phase in day.get("phases", []):
                        if phase.get("name") == phase_name:
                            items = phase.get("coverage_items") or []
                            coverage_items = [int(i) for i in items]
                            phase_found = True
                            break
                    if phase_found:
                        break
                if phase_found:
                    break

            if not phase_found:
                # Surface an explicit error rather than silently
                # returning a "0 checked / 0 missing" passing result —
                # that shape is indistinguishable from a real pass and
                # would hide typos or stale phase names in callers.
                known_phases: list[str] = []
                for plan in (WEEK_PLAN, FAST_PLAN):
                    for day in plan.values():
                        for phase in day.get("phases", []):
                            name = phase.get("name")
                            if name and name not in known_phases:
                                known_phases.append(name)
                return {
                    "error": f"phase_not_found: {phase_name}",
                    "known_phases": sorted(known_phases),
                }

        state = await self._snapshot_full_state()
        result = run_phase_gate(phase_name, coverage_items or [], state)
        return {"result": result_to_dict(result)}

    async def cmd_benchmarks(self) -> dict[str, Any]:
        """Collect benchmarks from the current run.

        Uses the harness's own message history for response-level
        metadata and the live full-state snapshot for everything else.
        """
        from tests.acceptance.scenario.benchmarks import (
            benchmarks_to_csv_row,
            benchmarks_to_json,
            collect_benchmarks,
        )

        # Pull per-turn metadata from session-state messages. The
        # assistant-role entries now carry latency_ms, token_count,
        # prompt_tokens, completion_tokens, and compaction_tier (see
        # cmd_send). Flag each meta with ``role='assistant'`` so the
        # benchmarks collector counts it in ``response_count``.
        metas: list[dict[str, Any]] = []
        for msg in self._state.get("messages", []):
            if msg.get("role") != "assistant":
                continue
            metas.append({
                "role": "assistant",
                "is_response": True,
                "latency_ms": msg.get("latency_ms", 0) or 0,
                "token_count": msg.get("token_count"),
                "prompt_tokens": msg.get("prompt_tokens"),
                "completion_tokens": msg.get("completion_tokens"),
                "compaction_tier": msg.get("compaction_tier") or "none",
            })

        # Rehydrate compaction tier counts from the persisted event list.
        # These are *synthetic* entries — they contribute only to the
        # compaction-tier histogram and must NOT be counted as responses
        # (so no role / is_response flag).
        for ev in self._compaction_events:
            tier = ev.get("tier") or "none"
            metas.append({
                "token_count": ev.get("token_count"),
                "compaction_tier": tier,
            })

        state = await self._snapshot_full_state()
        summary = await collect_benchmarks(metas, state, None)
        return {
            "json": benchmarks_to_json(summary),
            "csv_row": benchmarks_to_csv_row(summary),
        }

    async def cmd_event_tail(self, seconds: int = 10) -> dict[str, Any]:
        """Subscribe to daemon WebSocket events for ``seconds`` and return them.

        Uses the existing WebSocket connection — events that would
        otherwise route through ``_handle_ws_event`` are tapped into a
        local list for the duration. The tap is installed only for the
        requested window and removed afterward so subsequent conversation
        turns keep their response-assembly path intact.
        """
        if self._ws is None:
            return {
                "error": (
                    "WebSocket not connected; tail requires a live daemon "
                    "connection"
                ),
                "events": [],
            }

        captured: list[dict[str, Any]] = []
        safe_seconds = max(1, min(int(seconds), 300))

        original_handler = self._handle_ws_event

        async def _tap(data: dict[str, Any]) -> None:
            # Always mirror into the capture list first.
            try:
                captured.append({
                    "type": data.get("type"),
                    "content": data.get("content"),
                    "metadata": data.get("metadata"),
                    "ts": datetime.now(UTC).isoformat(),
                })
            except Exception:
                pass
            # Preserve original behaviour.
            await original_handler(data)

        self._handle_ws_event = _tap  # type: ignore[assignment]
        try:
            await asyncio.sleep(safe_seconds)
        finally:
            self._handle_ws_event = original_handler  # type: ignore[assignment]

        return {
            "seconds": safe_seconds,
            "event_count": len(captured),
            "events": captured,
        }

    async def cmd_clean_start_status(self) -> dict[str, Any]:
        """Return fresh-start and first-run acceptance startup metadata."""
        clean_start = dict(self._state.get("clean_start") or {})
        first_run = dict(self._state.get("first_run") or {})
        memory_root = Path(
            str(clean_start.get("isolated_memory_root") or ACCEPT_DIR / "memory")
        )
        resolved_accept_dir = ACCEPT_DIR.expanduser().resolve()
        resolved_memory_root = memory_root.expanduser().resolve()
        return {
            "run_id": self._state.get("run_id"),
            "started_at": self._state.get("started_at"),
            "mode": self._state.get("mode"),
            "first_run": first_run,
            "clean_start": clean_start,
            "checks": {
                "acceptance_dir_exists": ACCEPT_DIR.exists(),
                "output_dir_exists": OUTPUT_DIR.exists(),
                "session_file_exists": SESSION_FILE.exists(),
                "memory_root": str(memory_root),
                "memory_root_exists": memory_root.exists(),
                "memory_root_is_isolated": (
                    resolved_accept_dir in resolved_memory_root.parents
                    or resolved_memory_root == resolved_accept_dir
                ),
                "messages_count": len(self._state.get("messages", [])),
            },
        }

    async def cmd_mark_first_run_complete(
        self,
        evidence: str = "persona onboarding phase completed",
    ) -> dict[str, Any]:
        """Mark the simulated first-run setup complete with explicit evidence."""
        first_run = dict(self._state.get("first_run") or {})
        first_run.update(
            {
                "required": True,
                "status": "completed",
                "completed_at": datetime.now(UTC).isoformat(),
                "evidence": evidence,
            }
        )
        self._state["first_run"] = first_run
        _save_session(self._state)
        _log_event(
            {
                "event": "first_run_complete",
                "evidence": evidence,
            }
        )
        return {"first_run": first_run}

    async def cmd_stop_server(self) -> dict[str, Any]:
        """Shut down the harness server."""
        self._running = False
        return {"stopped": True}

    # ── Unix socket server ────────────────────────────────────────────────

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection."""
        try:
            data = await asyncio.wait_for(reader.readline(), timeout=5.0)
            request = json.loads(data.decode())
            cmd = request.get("cmd", "")

            if cmd == "send":
                result = await self.cmd_send(
                    request["message"],
                    timeout=request.get("timeout", 180.0),
                )
            elif cmd == "status":
                result = await self.cmd_status()
            elif cmd == "snapshot":
                result = await self.cmd_snapshot(request["name"])
            elif cmd == "diff":
                result = await self.cmd_diff(request["snap1"], request["snap2"])
            elif cmd == "idle-wait":
                result = await self.cmd_idle_wait(
                    min_soak=request.get("min_soak", 60),
                    timeout=request.get("timeout", 300),
                    manifest=request.get("manifest"),
                )
            elif cmd == "advance":
                result = await self.cmd_advance(float(request.get("hours", 0)))
            elif cmd == "test-auth":
                result = await self.cmd_test_auth()
            elif cmd == "test-auth-reset":
                result = await self.cmd_test_auth_reset()
            elif cmd == "test-error":
                result = await self.cmd_test_error()
            elif cmd == "compaction-status":
                result = await self.cmd_compaction_status()
            elif cmd == "skill-gating-check":
                result = await self.cmd_skill_gating_check()
            elif cmd == "life-management-check":
                result = await self.cmd_life_management_check()
            elif cmd == "tool-usage-summary":
                result = await self.cmd_tool_usage_summary()
            elif cmd == "monitor":
                result = await self.cmd_monitor()
            elif cmd == "report":
                result = await self.cmd_report()
            elif cmd == "capability-health-check":
                result = await self.cmd_capability_health_check()
            elif cmd == "orchestration-status":
                result = await self.cmd_orchestration_status()
            elif cmd == "pipeline-history":
                result = await self.cmd_pipeline_history(
                    limit=int(request.get("limit", 20)),
                )
            elif cmd == "working-docs":
                result = await self.cmd_working_docs()
            elif cmd == "edit-working-doc":
                result = await self.cmd_edit_working_doc(
                    text=str(
                        request.get("text")
                        or "user-added acceptance plan item"
                    )
                )
            elif cmd == "notifications":
                result = await self.cmd_notifications(
                    limit=int(request.get("limit", 20)),
                )
            elif cmd == "insights":
                result = await self.cmd_insights(
                    limit=int(request.get("limit", 20)),
                )
            elif cmd == "phase-history":
                result = await self.cmd_phase_history(
                    hours=int(request.get("hours", 24)),
                )
            elif cmd == "vault-snapshot":
                result = await self.cmd_vault_snapshot()
            elif cmd == "soak-manifest":
                result = await self.cmd_soak_manifest(request["phase"])
            elif cmd == "phase-gate":
                items = request.get("coverage_items")
                if items is not None:
                    items = [int(i) for i in items]
                result = await self.cmd_phase_gate(request["phase_name"], items)
            elif cmd == "benchmarks":
                result = await self.cmd_benchmarks()
            elif cmd == "event-tail":
                result = await self.cmd_event_tail(
                    seconds=int(request.get("seconds", 10)),
                )
            elif cmd == "clean-start-status":
                result = await self.cmd_clean_start_status()
            elif cmd == "mark-first-run-complete":
                result = await self.cmd_mark_first_run_complete(
                    evidence=str(
                        request.get("evidence")
                        or "persona onboarding phase completed"
                    )
                )
            elif cmd == "stop":
                result = await self.cmd_stop_server()
            elif cmd == "ping":
                result = {
                    "pong": True,
                    "session_id": self._kora_session_id,
                    "first_run": self._state.get("first_run", {}),
                    "clean_start": self._state.get("clean_start", {}),
                }
            else:
                result = {"error": f"Unknown command: {cmd}"}

            response = json.dumps(result, default=str) + "\n"
            try:
                writer.write(response.encode())
                await writer.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass

        except json.JSONDecodeError as e:
            try:
                writer.write(json.dumps({"error": f"JSON decode error: {e}"}).encode() + b"\n")
                await writer.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
        except Exception as e:
            try:
                writer.write(
                    json.dumps({"error": str(e), "traceback": traceback.format_exc()}).encode() + b"\n"
                )
                await writer.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        # Signal graceful shutdown if server stop was requested
        if not self._running and self._shutdown_event is not None:
            self._shutdown_event.set()

    async def run(self) -> None:
        """Start the Unix socket server and WebSocket recv loop."""
        _ensure_dirs()

        # Only a fresh run should wipe operational state. A harness
        # restart during the same acceptance session (e.g. the restart
        # resilience phase) must preserve the live conversation and
        # orchestration state so the reconnect can verify continuity.
        if (
            os.environ.get("KORA_ACCEPTANCE_PRE_CLEANED") != "1"
            and not self._state.get("messages")
        ):
            _reset_daemon_runtime_files(PROJECT_ROOT / "data")
            await _clean_stale_test_data(PROJECT_ROOT / "data" / "operational.db")
            _clean_stale_projection_data(PROJECT_ROOT / "data" / "projection.db")

        # Remove stale socket
        if HARNESS_SOCK.exists():
            HARNESS_SOCK.unlink()

        # Connect to Kora
        ok = await self.connect_to_kora()
        if ok:
            self._recv_task = asyncio.create_task(self._recv_loop())
            print(f"Connected to Kora (session: {self._kora_session_id})", file=sys.stderr)
        else:
            print("Warning: Could not connect to Kora WebSocket", file=sys.stderr)

        # Start Unix socket server
        server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(HARNESS_SOCK),
        )

        HARNESS_PID_FILE.write_text(str(os.getpid()))
        print(f"Harness server running (PID {os.getpid()}) at {HARNESS_SOCK}", file=sys.stderr)

        self._running = True
        self._shutdown_event = asyncio.Event()
        now_iso = datetime.now(UTC).isoformat()
        if not self._state.get("started_at"):
            self._state["started_at"] = now_iso
        self._state["harness_started_at"] = now_iso
        _save_session(self._state)
        _update_monitor(self._state, "Harness server started")

        try:
            async with server:
                await self._shutdown_event.wait()
                server.close()
                await server.wait_closed()
        except Exception:
            pass
        finally:
            if self._recv_task:
                self._recv_task.cancel()
            if self._ws:
                await self._ws.close()
            if HARNESS_SOCK.exists():
                HARNESS_SOCK.unlink()
            if HARNESS_PID_FILE.exists():
                HARNESS_PID_FILE.unlink()
            print("Harness server stopped", file=sys.stderr)


def main() -> None:
    server = HarnessServer()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
