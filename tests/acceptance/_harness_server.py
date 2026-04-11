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
import json
import os
import sys
import time
import traceback
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
LOG_FILE = OUTPUT_DIR / "test_log.jsonl"

LOCKFILE = PROJECT_ROOT / "data" / "kora.lock"
TOKEN_FILE = PROJECT_ROOT / "data" / ".api_token"


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


# ── Test data cleanup ────────────────────────────────────────────────────────

async def _clean_stale_autonomous_data(db_path: Path) -> None:
    """Wipe stale autonomous data from previous test runs.

    The previous implementation only wiped ``autonomous_plans`` and
    ``autonomous_checkpoints``, which left orphan rows in ``items``,
    ``autonomous_updates``, and ``item_state_history``. That caused the
    2026-04-11 acceptance report to say "no plans in DB" while the
    items table still showed work from a previous run — confusingly
    mixing old and new state. Always clean the full set atomically.
    """
    if not db_path.exists():
        return
    try:
        import aiosqlite
    except ImportError:
        return
    try:
        async with aiosqlite.connect(str(db_path)) as db:
            # Order matters for FK-respecting deletes: children first.
            for table in (
                "item_state_history",
                "item_artifact_links",
                "item_deps",
                "items",
                "autonomous_updates",
                "autonomous_checkpoints",
                "autonomous_plans",
            ):
                try:
                    await db.execute(f"DELETE FROM {table}")
                except Exception:
                    pass  # Table may not exist yet
            await db.commit()
    except Exception:
        pass  # DB may be locked or corrupted — not fatal for test startup


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

    async def _handle_ws_event(self, data: dict[str, Any]) -> None:
        """Route a single WebSocket event."""
        msg_type = data.get("type", "")

        if msg_type == "ping":
            if self._ws:
                await self._ws.send(json.dumps({"type": "pong"}))
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

    async def cmd_send(self, message: str, timeout: float = 180.0) -> dict[str, Any]:
        """Send a message to Kora and return the response."""
        if self._ws is None:
            ok = await self.connect_to_kora()
            if not ok:
                return {"error": "Cannot connect to Kora daemon"}

        if self._busy:
            return {"error": "Harness is busy with another request"}

        self._busy = True
        self._response_data = {"tokens": [], "tool_calls": []}
        self._response_ready = asyncio.Event()

        try:
            await self._ws.send(json.dumps({"type": "chat", "content": message}))
            try:
                await asyncio.wait_for(self._response_ready.wait(), timeout=timeout)
            except TimeoutError:
                return {"error": f"Response timed out after {timeout}s"}

            tokens = self._response_data.get("tokens", [])
            response_text = "".join(tokens)

            result = {
                "response": response_text,
                "trace_id": self._response_data.get("trace_id"),
                "latency_ms": self._response_data.get("latency_ms", 0),
                "tool_call_count": self._response_data.get("tool_call_count", 0),
                "tool_calls": self._response_data.get("tool_calls", []),
                "session_id": self._kora_session_id,
                "error": self._response_data.get("error"),
            }

            # Record in conversation state
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

        # Always capture autonomous runtime state directly from DB
        snapshot["autonomous_state"] = await self._query_autonomous_state()

        path = SNAPSHOTS_DIR / f"{name}.json"
        path.write_text(json.dumps(snapshot, indent=2, default=str))
        _log_event({"event": "snapshot", "name": name, "path": str(path)})
        return {"path": str(path), "name": name}

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

        # Autonomous runtime state
        a1 = s1.get("autonomous_state") or {}
        a2 = s2.get("autonomous_state") or {}
        if a1.get("available") or a2.get("available"):
            items1 = a1.get("total_items", 0)
            items2 = a2.get("total_items", 0)
            if items1 != items2:
                lines.append(f"Items (tasks): {items1} → {items2} (Δ{items2 - items1:+d})")

            chk1 = a1.get("checkpoint_count", 0)
            chk2 = a2.get("checkpoint_count", 0)
            if chk1 != chk2:
                lines.append(f"Autonomous checkpoints: {chk1} → {chk2} (Δ{chk2 - chk1:+d})")

            plans1 = len(a1.get("autonomous_plans", []))
            plans2 = len(a2.get("autonomous_plans", []))
            if plans1 != plans2:
                lines.append(f"Autonomous plans total: {plans1} → {plans2}")

            active1 = a1.get("active_plan_count", 0)
            active2 = a2.get("active_plan_count", 0)
            if active1 != active2:
                lines.append(f"Active autonomous plans: {active1} → {active2}")

            # Status breakdown change
            st1 = a1.get("items_by_status", {})
            st2 = a2.get("items_by_status", {})
            all_statuses = set(st1) | set(st2)
            for status in sorted(all_statuses):
                c1, c2 = st1.get(status, 0), st2.get(status, 0)
                if c1 != c2:
                    lines.append(f"  items[{status}]: {c1} → {c2} (Δ{c2 - c1:+d})")

            # New items created since snap1
            new_items = [
                it for it in a2.get("recent_items", [])
                if it.get("created_at", "") > (
                    s1.get("captured_at") or ""
                )
            ]
            if new_items:
                lines.append("")
                lines.append(f"## New autonomous items ({len(new_items)}):")
                for it in new_items[:5]:
                    content = (it.get("content") or "")[:120]
                    status = it.get("status", "?")
                    owner = it.get("owner", "?")
                    lines.append(f"  [{status}/{owner}] {content}")

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
        """Query live autonomous runtime state directly from operational.db."""
        op_db = PROJECT_ROOT / "data" / "operational.db"
        if not op_db.exists():
            return {"available": False}

        try:
            import aiosqlite

            async with aiosqlite.connect(str(op_db)) as db:
                db.row_factory = aiosqlite.Row

                # Active + recent autonomous plans
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

                # Items by status
                cursor = await db.execute(
                    "SELECT status, COUNT(*) as cnt FROM items GROUP BY status"
                )
                items_by_status = {r["status"]: r["cnt"] for r in await cursor.fetchall()}

                # Total item count
                cursor = await db.execute("SELECT COUNT(*) FROM items")
                row = await cursor.fetchone()
                total_items = row[0] if row else 0

                # Recent items (last 10 created)
                cursor = await db.execute(
                    """SELECT id, title AS content, status, owner, spawned_from, created_at
                       FROM items ORDER BY created_at DESC LIMIT 10"""
                )
                recent_items = [dict(r) for r in await cursor.fetchall()]

                # Checkpoint count
                checkpoint_count = 0
                try:
                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM autonomous_checkpoints"
                    )
                    row = await cursor.fetchone()
                    checkpoint_count = row[0] if row else 0
                except Exception:
                    pass

                # Active plans (not in terminal state)
                active_plans = [
                    p for p in plans
                    if p.get("status") not in ("completed", "cancelled", "failed")
                ]

                return {
                    "available": True,
                    "total_items": total_items,
                    "items_by_status": items_by_status,
                    "recent_items": recent_items,
                    "autonomous_plans": plans,
                    "active_plan_count": len(active_plans),
                    "checkpoint_count": checkpoint_count,
                }
        except Exception as e:
            return {"available": False, "error": str(e)}

    async def cmd_idle_wait(self, min_soak: int = 60, timeout: int = 300) -> dict[str, Any]:
        """Wait min_soak seconds while monitoring daemon health and autonomous work.

        Phase 6 is live: idle phases now track autonomous session progress,
        item creation, checkpoint writes, and budget state — not just health.
        """
        start = time.monotonic()
        deadline = start + timeout
        polls = 0
        errors = 0

        # Snapshot autonomous state at start for delta calculation
        initial_auto = await self._query_autonomous_state()
        initial_items = initial_auto.get("total_items", 0)
        initial_checkpoints = initial_auto.get("checkpoint_count", 0)

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
                return {
                    "elapsed": elapsed,
                    "polls": polls,
                    "errors": errors,
                    "quiescent": True,
                    "autonomous_state": current_auto,
                    "items_delta": current_auto.get("total_items", 0) - initial_items,
                    "checkpoints_delta": current_auto.get("checkpoint_count", 0) - initial_checkpoints,
                }

        elapsed = time.monotonic() - start
        current_auto = await self._query_autonomous_state()
        return {
            "elapsed": elapsed,
            "polls": polls,
            "errors": errors,
            "timeout": True,
            "autonomous_state": current_auto,
            "items_delta": current_auto.get("total_items", 0) - initial_items,
            "checkpoints_delta": current_auto.get("checkpoint_count", 0) - initial_checkpoints,
        }

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
        _save_session(self._state)
        _log_event({"event": "time_advance", "hours": hours,
                    "total_offset": self._state["simulated_hours_offset"]})
        _update_monitor(self._state)
        return {
            "hours_advanced": hours,
            "total_offset": self._state["simulated_hours_offset"],
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
        path = await build_report(
            self._state,
            SNAPSHOTS_DIR,
            OUTPUT_DIR,
            compaction_events=list(self._compaction_events),
        )
        return {"path": str(path)}

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

        test_cases = [
            ("empty_message", ""),
            ("special_chars", "!@#$%^&*(){}[]|\\/<>?~`"),
            ("long_message", "test " * 500),
            ("unicode", "\u00e9\u00e8\u00ea \U0001F680\U0001F31F \u4f60\u597d\u4e16\u754c"),
            ("normal_after_errors", "hey, just checking you're still working fine"),
        ]

        for test_name, message in test_cases:
            try:
                result = await self.cmd_send(message or " ", timeout=60.0)
                survived = "error" not in result or not result.get("error")
                response = result.get("response", "")
                results.append({
                    "test": test_name,
                    "survived": survived,
                    "response": response[:120] if response else "(empty)",
                    "error": result.get("error"),
                })
            except Exception as e:
                results.append({
                    "test": test_name,
                    "survived": False,
                    "error": str(e),
                })

        all_survived = all(r.get("survived", False) for r in results)
        return {"results": results, "all_survived": all_survived}

    async def cmd_compaction_status(self) -> dict[str, Any]:
        """Return compaction events detected during the test."""
        return {
            "compaction_detected": len(self._compaction_events) > 0,
            "event_count": len(self._compaction_events),
            "events": self._compaction_events,
        }

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

        return result

    async def cmd_tool_usage_summary(self) -> dict[str, Any]:
        """Analyze tool usage from conversation history."""
        tool_counts: dict[str, int] = {}
        for msg in self._state.get("messages", []):
            for tc in msg.get("tool_calls", []):
                # Strip auth prefix if present
                name = tc
                if name.startswith("[auth:"):
                    name = name.split(":")[1].rstrip("]").split(":")[0]
                tool_counts[name] = tool_counts.get(name, 0) + 1

        # Categorize
        life_tools = {"log_medication", "log_meal", "create_reminder", "query_reminders",
                       "quick_note", "start_focus_block", "end_focus_block"}
        fs_tools = {"read_file", "write_file", "list_directory", "create_directory"}
        mcp_tools = {"search_web", "fetch_url"}
        auto_tools = {"start_autonomous"}
        memory_tools = {"recall", "dispatch_worker"}

        return {
            "total_tool_calls": sum(tool_counts.values()),
            "unique_tools": len(tool_counts),
            "tool_counts": tool_counts,
            "life_management_tools_used": sorted(set(tool_counts) & life_tools),
            "filesystem_tools_used": sorted(set(tool_counts) & fs_tools),
            "mcp_tools_used": sorted(set(tool_counts) & mcp_tools),
            "autonomous_tools_used": sorted(set(tool_counts) & auto_tools),
            "memory_tools_used": sorted(set(tool_counts) & memory_tools),
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
            elif cmd == "stop":
                result = await self.cmd_stop_server()
            elif cmd == "ping":
                result = {"pong": True, "session_id": self._kora_session_id}
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

        # Clean stale autonomous data from previous test runs
        await _clean_stale_autonomous_data(PROJECT_ROOT / "data" / "operational.db")

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
        self._state["started_at"] = datetime.now(UTC).isoformat()
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
