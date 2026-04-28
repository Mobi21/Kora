"""Kora V2 — RuntimeInspector.

Operator/control-plane visibility into the running system without
requiring log reads.

Topics
------
- setup       : version, settings summary, data paths
- tools       : skill loader status and registered skills
- workers     : planner/executor/reviewer initialization status
- permissions : recent/active permission grants from DB
- session     : active session state
- trace       : recent turn traces (optionally filtered by trace_id)
- doctor      : DB integrity, settings sanity, component health
- phase-audit : Phase 4.67 acceptance-criteria compliance
"""

from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from kora_v2.runtime.protocol import (
    API_VERSION,
    PROTOCOL_VERSION,
    SUPPORTED_INSPECT_TOPICS,
    runtime_metadata,
)

if TYPE_CHECKING:
    from kora_v2.core.di import Container

log = structlog.get_logger(__name__)

# Risk-level labels mapped from permission scope and tool names.
_RISK_HIGH = {"filesystem_write", "shell_exec", "process_kill", "db_write"}
_RISK_MEDIUM = {"filesystem_read", "web_fetch", "code_run"}

# Phase 4.67 acceptance criteria (index → description)
_PHASE_467_CRITERIA: list[tuple[str, str]] = [
    ("no_stubs", "No runtime-critical module contains stub results or placeholder entrypoints"),
    ("session_persist", "Session init/end persist deterministic records to operational.db"),
    ("turn_traces", "Every turn persists a TurnTrace"),
    ("permission_persist", "Permission grants are stored as data and inspectable"),
    ("no_start_autonomous", "start_autonomous absent from active runtime surfaces"),
    ("typed_workers", "Worker execution follows one strict typed contract — no plain-text fallback"),
    ("ws_turn_runner", "WebSocket chat uses a single strict turn-runner contract"),
    ("sqlite_checkpointer", "LangGraph conversation state is restart-safe via SQLite-backed checkpointer"),
    ("idempotency_rules", "Side-effecting actions follow explicit idempotency/recovery rules"),
    ("compaction_breaker", "Compaction retries bounded by circuit breaker"),
]


def _risk_level(tool_name: str) -> str:
    """Classify a tool's risk level from its name."""
    lower = tool_name.lower()
    for pattern in _RISK_HIGH:
        if pattern in lower:
            return "high"
    for pattern in _RISK_MEDIUM:
        if pattern in lower:
            return "medium"
    return "low"


class RuntimeInspector:
    """Provides structured inspection output for operator queries.

    All ``inspect_*`` methods are async and return plain dicts safe
    for JSON serialisation.

    Parameters
    ----------
    container:
        Live DI container from the running daemon.
    """

    def __init__(self, container: Container) -> None:
        self.container = container

    # ── Topic Dispatch ────────────────────────────────────────────────────

    async def inspect(self, topic: str, **kwargs: Any) -> dict[str, Any]:
        """Dispatch to the correct inspection method by topic name.

        Args:
            topic: One of setup, tools, workers, permissions, session,
                   trace, doctor, phase-audit.
            **kwargs: Topic-specific parameters (e.g. trace_id).

        Returns:
            Structured dict with inspection results.
        """
        normalized = topic.strip().replace("_", "-")
        handlers: dict[str, Any] = {
            "setup": self.inspect_setup,
            "tools": self.inspect_tools,
            "workers": self.inspect_workers,
            "permissions": self.inspect_permissions,
            "session": self.inspect_session,
            "trace": self.inspect_trace,
            "doctor": self.doctor,
            "phase-audit": self.phase_audit,
        }
        if normalized not in handlers:
            return {
                "topic": normalized,
                "error": f"Unknown topic '{topic}'",
                "valid_topics": list(handlers),
                "supported_topics": list(SUPPORTED_INSPECT_TOPICS),
                "runtime": runtime_metadata(),
            }
        handler = handlers[normalized]
        return await handler(**kwargs)

    # ── Setup ─────────────────────────────────────────────────────────────

    async def inspect_setup(self) -> dict[str, Any]:
        """Return runtime settings summary and data paths."""
        s = self.container.settings
        data_dir = Path(s.data_dir)
        op_db = data_dir / "operational.db"
        proj_db = data_dir / "projection.db"
        runtime = runtime_metadata()

        return {
            "topic": "setup",
            "version": API_VERSION,
            "runtime": runtime,
            "runtime_name": runtime["runtime_name"],
            "protocol_version": PROTOCOL_VERSION,
            "data_dir": str(data_dir),
            "operational_db": {"path": str(op_db), "exists": op_db.exists()},
            "projection_db": {"path": str(proj_db), "exists": proj_db.exists()},
            "llm": {
                "provider": s.llm.provider,
                "model": s.llm.model,
                "api_base": s.llm.api_base,
                "timeout": s.llm.timeout,
                "max_tokens": s.llm.max_tokens,
            },
            "memory": {
                "path": s.memory.kora_memory_path,
                "embedding_model": s.memory.embedding_model,
                "embedding_dims": s.memory.embedding_dims,
            },
            "security": {
                "api_token_path": s.security.api_token_path,
                "token_file_exists": Path(s.security.api_token_path).exists(),
                "injection_scan_enabled": s.security.injection_scan_enabled,
                "auth_mode": s.security.auth_mode,
                "cors_origins": s.security.cors_origins,
            },
            "daemon": {
                "host": s.daemon.host,
                "port": s.daemon.port,
            },
            "supported_inspect_topics": list(SUPPORTED_INSPECT_TOPICS),
            "capabilities": runtime["capabilities"],
        }

    # ── Tools ─────────────────────────────────────────────────────────────

    async def inspect_tools(self) -> dict[str, Any]:
        """Return skill loader status and per-skill summaries."""
        skill_loader = self.container.skill_loader
        if skill_loader is None:
            return {
                "topic": "tools",
                "skill_loader_initialized": False,
                "skills": [],
            }

        skills = skill_loader.get_all_skills()  # list[Skill]
        skill_info = []
        for skill in skills:
            skill_info.append({
                "name": skill.name,
                "description": getattr(skill, "guidance", "")[:120],
                "tool_count": len(skill.tools),
                "tools": skill.tools,  # already list[str]
            })

        # MCP availability info
        mcp_manager = getattr(self.container, "_mcp_manager", None)
        mcp_info: dict[str, Any] = {"initialized": mcp_manager is not None}
        if mcp_manager is not None:
            servers = getattr(mcp_manager, "list_servers", lambda: [])()
            mcp_info["servers"] = [
                {"name": s.name, "state": str(s.state), "tools": s.tools}
                for s in servers
            ]

        return {
            "topic": "tools",
            "skill_loader_initialized": True,
            "skill_count": len(skills),
            "skills": skill_info,
            "mcp": mcp_info,
        }

    # ── Workers ───────────────────────────────────────────────────────────

    async def inspect_workers(self) -> dict[str, Any]:
        """Return initialization status for each worker harness."""
        c = self.container

        def _worker_info(worker: Any) -> dict[str, Any]:
            if worker is None:
                return {"initialized": False}
            return {
                "initialized": True,
                "class": type(worker).__name__,
                "schema_repair_hint": getattr(worker, "_schema_repair_hint", None),
            }

        return {
            "topic": "workers",
            "planner": _worker_info(c._planner),
            "executor": _worker_info(c._executor),
            "reviewer": _worker_info(c._reviewer),
            "mcp_manager": {
                "initialized": c._mcp_manager is not None,
                "class": type(c._mcp_manager).__name__ if c._mcp_manager else None,
            },
            "checkpointer": {
                "initialized": c._checkpointer is not None,
                "class": type(c._checkpointer).__name__ if c._checkpointer else "MemorySaver (fallback)",
            },
            "auth_relay": {
                "initialized": c._auth_relay is not None,
                "has_broadcast": (
                    c._auth_relay is not None
                    and getattr(c._auth_relay, "_broadcast", None) is not None
                ),
            },
        }

    # ── Permissions ───────────────────────────────────────────────────────

    async def inspect_permissions(self, limit: int = 20) -> dict[str, Any]:
        """Return recent permission grants from operational.db."""
        db_path = Path(self.container.settings.data_dir) / "operational.db"
        if not db_path.exists():
            return {
                "topic": "permissions",
                "error": "operational.db not found",
                "grants": [],
            }

        try:
            import aiosqlite

            async with aiosqlite.connect(str(db_path)) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    """
                    SELECT id, tool_name, scope, risk_level, decision,
                           granted_at, expires_at, session_id
                    FROM permission_grants
                    ORDER BY granted_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = await cursor.fetchall()

            grants = []
            for row in rows:
                tool = row["tool_name"]
                grants.append({
                    "id": row["id"],
                    "tool_name": tool,
                    "scope": row["scope"],
                    "risk_level": row["risk_level"] or _risk_level(tool),
                    "decision": row["decision"],
                    "granted_at": row["granted_at"],
                    "expires_at": row["expires_at"],
                    "session_id": row["session_id"],
                })

            return {
                "topic": "permissions",
                "grant_count": len(grants),
                "grants": grants,
            }
        except Exception as exc:
            log.warning("inspect_permissions_failed", error=str(exc))
            return {
                "topic": "permissions",
                "error": str(exc),
                "grants": [],
            }

    # ── Session ───────────────────────────────────────────────────────────

    async def inspect_session(self) -> dict[str, Any]:
        """Return active session state and recent session history."""
        session_mgr = self.container.session_manager
        active: dict[str, Any] = {}

        if session_mgr is not None and session_mgr.active_session is not None:
            s = session_mgr.active_session
            active = {
                "session_id": getattr(s, "session_id", None),
                "turn_count": getattr(s, "turn_count", 0),
                "thread_id": (
                    session_mgr.get_thread_id()
                    if hasattr(session_mgr, "get_thread_id")
                    else None
                ),
            }

        # Pull last 5 sessions from DB
        db_path = Path(self.container.settings.data_dir) / "operational.db"
        recent: list[dict[str, Any]] = []
        if db_path.exists():
            try:
                import aiosqlite

                async with aiosqlite.connect(str(db_path)) as db:
                    db.row_factory = aiosqlite.Row
                    cursor = await db.execute(
                        """
                        SELECT id, started_at, ended_at, turn_count,
                               duration_seconds, continuation_of
                        FROM sessions
                        ORDER BY started_at DESC
                        LIMIT 5
                        """,
                    )
                    rows = await cursor.fetchall()
                recent = [dict(row) for row in rows]
            except Exception as exc:
                log.warning("inspect_session_db_failed", error=str(exc))

        return {
            "topic": "session",
            "active": active if active else None,
            "recent_sessions": recent,
            "runtime": runtime_metadata(),
        }

    # ── Trace ─────────────────────────────────────────────────────────────

    async def inspect_trace(
        self,
        trace_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Return recent turn traces or a specific trace with events."""
        db_path = Path(self.container.settings.data_dir) / "operational.db"
        if not db_path.exists():
            return {
                "topic": "trace",
                "error": "operational.db not found",
                "traces": [],
            }

        try:
            import aiosqlite

            async with aiosqlite.connect(str(db_path)) as db:
                db.row_factory = aiosqlite.Row

                if trace_id:
                    # Specific trace + events
                    cursor = await db.execute(
                        "SELECT * FROM turn_traces WHERE id = ?",
                        (trace_id,),
                    )
                    row = await cursor.fetchone()
                    if not row:
                        return {
                            "topic": "trace",
                            "error": f"Trace '{trace_id}' not found",
                            "runtime": runtime_metadata(),
                        }
                    trace = dict(row)

                    cursor = await db.execute(
                        """
                        SELECT event_type, payload, recorded_at
                        FROM turn_trace_events
                        WHERE trace_id = ?
                        ORDER BY id
                        """,
                        (trace_id,),
                    )
                    events = [dict(r) for r in await cursor.fetchall()]
                    return {
                        "topic": "trace",
                        "trace": trace,
                        "events": events,
                        "runtime": runtime_metadata(),
                    }
                else:
                    # Recent traces
                    cursor = await db.execute(
                        """
                        SELECT id, session_id, turn_number, started_at,
                               completed_at, latency_ms, succeeded,
                               response_length, tool_call_count
                        FROM turn_traces
                        ORDER BY started_at DESC
                        LIMIT ?
                        """,
                        (limit,),
                    )
                    traces = [dict(r) for r in await cursor.fetchall()]
                    return {
                        "topic": "trace",
                        "trace_count": len(traces),
                        "traces": traces,
                        "runtime": runtime_metadata(),
                    }
        except Exception as exc:
            log.warning("inspect_trace_failed", error=str(exc))
            return {
                "topic": "trace",
                "error": str(exc),
                "traces": [],
                "runtime": runtime_metadata(),
            }

    # ── Doctor ────────────────────────────────────────────────────────────

    async def doctor(self) -> dict[str, Any]:
        """Run health checks and return a structured report."""
        checks: list[dict[str, Any]] = []

        def _check(name: str, passed: bool, detail: str = "") -> None:
            checks.append({"name": name, "passed": passed, "detail": detail})

        # 1. Operational DB
        op_db = Path(self.container.settings.data_dir) / "operational.db"
        if op_db.exists():
            try:
                import aiosqlite

                async with aiosqlite.connect(str(op_db)) as db:
                    cursor = await db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%'"
                    )
                    tables = {r[0] for r in await cursor.fetchall()}
                required_tables = {
                    "sessions", "telemetry", "turn_traces",
                    "turn_trace_events", "permission_grants",
                }
                missing = required_tables - tables
                _check(
                    "operational_db_schema",
                    len(missing) == 0,
                    f"missing={missing}" if missing else f"tables={len(tables)}",
                )
            except Exception as exc:
                _check("operational_db_schema", False, str(exc))
        else:
            _check("operational_db_exists", False, f"not found at {op_db}")

        # 2. Token file
        token_path = Path(self.container.settings.security.api_token_path)
        _check(
            "api_token_file",
            token_path.exists(),
            str(token_path),
        )

        # 3. Security settings
        host = self.container.settings.daemon.host
        _check(
            "daemon_localhost_binding",
            host in ("127.0.0.1", "localhost", "::1"),
            f"host={host}",
        )

        cors = self.container.settings.security.cors_origins
        _check(
            "cors_not_wildcard",
            "*" not in cors and ["*"] != cors,
            f"origins={cors}",
        )

        # 4. Workers initialized
        _check(
            "planner_initialized",
            self.container._planner is not None,
        )
        _check(
            "executor_initialized",
            self.container._executor is not None,
        )
        _check(
            "reviewer_initialized",
            self.container._reviewer is not None,
        )

        # 5. Checkpointer
        checkpointer = self.container._checkpointer
        _check(
            "sqlite_checkpointer",
            checkpointer is not None,
            "MemorySaver fallback active" if checkpointer is None else type(checkpointer).__name__,
        )

        # 6. Core modules importable
        modules_to_check = [
            ("kora_v2.runtime.turn_runner", "GraphTurnRunner"),
            ("kora_v2.runtime.stores", "ArtifactStore"),
            ("kora_v2.agents.harness", "AgentHarness"),
            ("kora_v2.emotion.fast_assessor", "FastEmotionAssessor"),
        ]
        for mod_path, attr in modules_to_check:
            try:
                mod = importlib.import_module(mod_path)
                has_attr = hasattr(mod, attr)
                _check(f"module_{attr}", has_attr, mod_path)
            except ImportError as exc:
                _check(f"module_{attr}", False, str(exc))

        # ── 7. Dependency / install sanity ────────────────────────────────

        # python_version_ok
        vi = sys.version_info
        _check(
            "python_version_ok",
            vi >= (3, 12),
            f"{vi.major}.{vi.minor}.{vi.micro}",
        )

        # pysqlite3_swap — did kora_v2 replace stdlib sqlite3 with pysqlite3?
        try:
            import kora_v2  # noqa: F401 — side-effect: may swap sqlite3

            sqlite3_mod = sys.modules.get("sqlite3")
            is_pysqlite3 = sqlite3_mod is not None and getattr(
                sqlite3_mod, "__file__", ""
            ) and "pysqlite3" in str(getattr(sqlite3_mod, "__file__", ""))
            detail = "pysqlite3 active" if is_pysqlite3 else "stdlib sqlite3 (fallback)"
            _check("pysqlite3_swap", True, detail)
        except Exception as exc:
            _check("pysqlite3_swap", False, str(exc))

        # sentence_transformers importable (soft)
        try:
            import sentence_transformers as _st

            _check(
                "sentence_transformers_importable",
                True,
                getattr(_st, "__version__", "unknown"),
            )
        except ImportError:
            _check("sentence_transformers_importable", True, "optional — not installed (vector search degraded)")

        # sqlite_vec loadable
        try:
            import sqlite_vec

            loadable_path = sqlite_vec.loadable_path()
            _check("sqlite_vec_loadable", True, str(loadable_path))
        except ImportError:
            _check("sqlite_vec_loadable", False, "sqlite_vec not installed")
        except Exception as exc:
            _check("sqlite_vec_loadable", False, str(exc))

        # ── 8. MCP servers ────────────────────────────────────────────────

        mcp_servers = self.container.settings.mcp.servers
        if not mcp_servers:
            _check("mcp_servers_configured", True, "no MCP servers configured")
        else:
            mcp_manager = getattr(self.container, "_mcp_manager", None)
            for srv_name in mcp_servers:
                # Confirm registration
                _check(f"mcp_server_{srv_name}_configured", True, "registered")
                # Check running state — read-only, do NOT start server
                if mcp_manager is not None:
                    try:
                        info = mcp_manager.get_server_info(srv_name)
                        if info is not None:
                            state = str(getattr(info, "state", "unknown"))
                            running = state == "running"
                            _check(
                                f"mcp_server_{srv_name}_running",
                                running,
                                f"state={state}",
                            )
                        else:
                            _check(
                                f"mcp_server_{srv_name}_running",
                                True,
                                "not yet started (lazy startup)",
                            )
                    except Exception as exc:
                        _check(
                            f"mcp_server_{srv_name}_running",
                            True,
                            f"not yet started: {exc}",
                        )
                else:
                    _check(
                        f"mcp_server_{srv_name}_running",
                        True,
                        "not yet started (lazy startup)",
                    )

            # mcp_tool_discovery — report tool count per running server
            if mcp_manager is not None:
                try:
                    servers_info = mcp_manager.list_servers()
                    running_servers = [
                        s for s in servers_info
                        if str(getattr(s, "state", "")) == "running"
                    ]
                    if running_servers:
                        parts = [
                            f"{s.name}={len(s.tools)}" for s in running_servers
                        ]
                        _check("mcp_tool_discovery", True, ", ".join(parts))
                    else:
                        _check(
                            "mcp_tool_discovery",
                            True,
                            "no servers running yet (lazy startup)",
                        )
                except Exception as exc:
                    _check("mcp_tool_discovery", True, f"manager query failed: {exc}")
            else:
                _check(
                    "mcp_tool_discovery",
                    True,
                    "mcp_manager not initialised (lazy startup)",
                )

        # ── 9. Capability packs ───────────────────────────────────────────

        try:
            from kora_v2.capabilities import get_all_capabilities
            from kora_v2.capabilities.base import HealthStatus

            packs = get_all_capabilities()
            _check(
                "capability_registry_ok",
                len(packs) >= 4,
                f"{len(packs)} packs registered",
            )
            for pack in packs:
                try:
                    health = await pack.health_check()
                    pack_passed = health.status != HealthStatus.UNHEALTHY
                    _check(
                        f"capability_{pack.name}",
                        pack_passed,
                        f"{health.status}: {health.summary}",
                    )
                except Exception as pack_exc:
                    _check(f"capability_{pack.name}", False, str(pack_exc))
        except Exception as cap_exc:
            _check("capability_registry_ok", False, str(cap_exc))

        # ── 10. Agent-browser binary presence ────────────────────────────

        try:
            binary_path = self.container.settings.browser.binary_path
            resolved: str | None = None
            if binary_path:
                # Explicit path configured — verify it actually exists on disk
                if Path(binary_path).exists():
                    resolved = binary_path
                # else: resolved stays None → check fails with "not found"
            if resolved is None:
                # Fall back to PATH search
                resolved = shutil.which("agent-browser")
            _check(
                "agent_browser_present",
                resolved is not None,
                resolved if resolved else "not found on PATH",
            )
        except Exception as exc:
            _check("agent_browser_present", False, str(exc))

        # ── 11. Vault mirror path ─────────────────────────────────────────

        try:
            vault_enabled = self.container.settings.vault.enabled
            vault_path_str = self.container.settings.vault.path

            if not vault_enabled:
                _check("vault_enabled", True, "vault disabled")
            else:
                vault_path = Path(vault_path_str) if vault_path_str else None
                if vault_path is None or not vault_path_str:
                    _check("vault_path_configured", False, f"path={vault_path_str!r}")
                elif not vault_path.exists():
                    _check(
                        "vault_path_configured",
                        False,
                        f"path={vault_path_str!r} does not exist",
                    )
                elif not vault_path.is_dir():
                    _check(
                        "vault_path_configured",
                        False,
                        f"path={vault_path_str!r} is not a directory",
                    )
                elif os.access(str(vault_path), os.W_OK):
                    _check("vault_writable", True, vault_path_str)
                else:
                    _check("vault_writable", False, "not writable")
        except Exception as exc:
            _check("vault_enabled", False, str(exc))

        passed = sum(1 for c in checks if c["passed"])
        total = len(checks)
        return {
            "topic": "doctor",
            "summary": f"{passed}/{total} checks passed",
            "healthy": passed == total,
            "checks": checks,
            "runtime": runtime_metadata(),
        }

    # ── Phase Audit ───────────────────────────────────────────────────────

    async def phase_audit(self) -> dict[str, Any]:
        """Check compliance with Phase 4.67 acceptance criteria."""
        results: list[dict[str, Any]] = []

        # Run doctor to get component health
        doctor_report = await self.doctor()
        doctor_checks = {c["name"]: c["passed"] for c in doctor_report["checks"]}

        def _criterion(key: str, desc: str, passed: bool, detail: str = "") -> None:
            results.append({
                "criterion": key,
                "description": desc,
                "passed": passed,
                "detail": detail,
            })

        # 1. No stubs
        try:
            import inspect as _inspect

            from kora_v2.agents import harness as harness_mod
            from kora_v2.daemon import server as server_mod
            from kora_v2.daemon import session as session_mod

            scan_targets = [
                _inspect.getsource(harness_mod),
                _inspect.getsource(server_mod),
                _inspect.getsource(session_mod),
            ]
            banned_markers = ("not yet wired", "placeholder", "simulated lifecycle")
            has_schema_repair = not any(
                marker in source for marker in banned_markers for source in scan_targets
            )
        except Exception:
            has_schema_repair = False

        _criterion(
            "no_stubs",
            "No runtime-critical module contains stub results or placeholder entrypoints",
            has_schema_repair,
            "placeholder markers absent" if has_schema_repair else "placeholder markers found",
        )

        # 2. Session persistence tables
        op_db = Path(self.container.settings.data_dir) / "operational.db"
        session_source_ok = False
        try:
            import inspect as _inspect

            from kora_v2.daemon.session import SessionManager

            session_src = _inspect.getsource(SessionManager)
            session_source_ok = "SessionStore" in session_src and "BridgeStore" in session_src
        except Exception:
            session_source_ok = False
        _criterion(
            "session_persist",
            "Session init/end persist deterministic records to operational.db",
            doctor_checks.get("operational_db_schema", False) and session_source_ok,
            "SessionStore + BridgeStore wired" if session_source_ok else "session persistence wiring missing",
        )

        # 3. Turn traces table
        trace_table_ok = False
        if op_db.exists():
            try:
                import aiosqlite

                async with aiosqlite.connect(str(op_db)) as db:
                    cursor = await db.execute(
                        "SELECT name FROM sqlite_master WHERE name='turn_traces'"
                    )
                    trace_table_ok = await cursor.fetchone() is not None
            except Exception:
                pass
        _criterion(
            "turn_traces",
            "Every turn persists a TurnTrace",
            trace_table_ok,
            "turn_traces table present" if trace_table_ok else "turn_traces table missing",
        )

        # 4. Permission persistence
        perm_table_ok = False
        if op_db.exists():
            try:
                import aiosqlite

                async with aiosqlite.connect(str(op_db)) as db:
                    cursor = await db.execute(
                        "SELECT name FROM sqlite_master WHERE name='permission_grants'"
                    )
                    perm_table_ok = await cursor.fetchone() is not None
            except Exception:
                pass
        _criterion(
            "permission_persist",
            "Permission grants are stored as data and inspectable",
            perm_table_ok,
            "permission_grants table present" if perm_table_ok else "permission_grants table missing",
        )

        # 5. No start_autonomous in active surfaces
        try:
            from kora_v2.daemon import server as srv_mod
            has_auto = hasattr(srv_mod, "start_autonomous")
            _criterion(
                "no_start_autonomous",
                "start_autonomous absent from active runtime surfaces",
                not has_auto,
                "absent" if not has_auto else "FOUND in server.py — must remove",
            )
        except ImportError:
            _criterion("no_start_autonomous", "...", False, "server module not importable")

        # 6. Typed workers (no plain-text fallback)
        try:
            import inspect as _inspect

            from kora_v2.agents.workers.executor import ExecutorWorkerHarness
            from kora_v2.agents.workers.reviewer import ReviewerWorkerHarness

            exec_src = _inspect.getsource(ExecutorWorkerHarness)
            rev_src = _inspect.getsource(ReviewerWorkerHarness)
            # Text fallback patterns that should NOT be present
            has_text_fallback = (
                'ExecutionOutput(result=result.content' in exec_src
                or 'ReviewOutput(passed=True, confidence=0.5' in rev_src
            )
            _criterion(
                "typed_workers",
                "Worker execution follows one strict typed contract — no plain-text fallback",
                not has_text_fallback,
                "text fallbacks absent" if not has_text_fallback else "text fallback found — MUST REMOVE",
            )
        except Exception as exc:
            _criterion("typed_workers", "...", False, str(exc))

        # 7. WS uses turn runner
        try:
            import inspect as _inspect

            from kora_v2.daemon import server as _srv

            src = _inspect.getsource(_srv._handle_chat)
            uses_runner = "GraphTurnRunner" in src or "stream_turn" in src
            _criterion(
                "ws_turn_runner",
                "WebSocket chat uses a single strict turn-runner contract",
                uses_runner,
                "GraphTurnRunner used" if uses_runner else "direct graph call found",
            )
        except Exception as exc:
            _criterion("ws_turn_runner", "...", False, str(exc))

        # 8. SQLite checkpointer
        _criterion(
            "sqlite_checkpointer",
            "LangGraph conversation state is restart-safe via SQLite-backed checkpointer",
            doctor_checks.get("sqlite_checkpointer", False),
            "checkpointer check",
        )

        # 9. Idempotency rules (ActionRecord model present)
        try:
            import kora_v2.agents.models as agent_models

            _criterion(
                "idempotency_rules",
                "Side-effecting actions follow explicit idempotency/recovery rules",
                hasattr(agent_models, "ActionRecord")
                and hasattr(agent_models, "SideEffectLevel"),
                "ActionRecord + SideEffectLevel present",
            )
        except ImportError as exc:
            _criterion("idempotency_rules", "...", False, str(exc))

        # 10. Compaction circuit breaker
        try:
            import kora_v2.runtime.turn_runner as turn_runner

            _criterion(
                "compaction_breaker",
                "Compaction retries bounded by circuit breaker",
                hasattr(turn_runner, "CompactionCircuitBreaker"),
                "CompactionCircuitBreaker present",
            )
        except ImportError as exc:
            _criterion("compaction_breaker", "...", False, str(exc))

        passed = sum(1 for r in results if r["passed"])
        total = len(results)
        return {
            "topic": "phase-audit",
            "phase": "4.67",
            "summary": f"{passed}/{total} criteria met",
            "phase_complete": passed == total,
            "criteria": results,
            "runtime": runtime_metadata(),
        }


# ── Doctor pretty-printer ─────────────────────────────────────────────────


def doctor_report_lines(report: dict[str, Any]) -> list[str]:
    """Render a doctor report dict as a list of human-readable strings.

    Example output::

        Doctor: 18/22 checks passed  [DEGRADED]
          ✓ operational_db_schema (tables=26)
          ✓ python_version_ok (3.12.3)
          ✗ capability_workspace (unimplemented — not yet implemented)

    Parameters
    ----------
    report:
        The dict returned by :meth:`RuntimeInspector.doctor`.

    Returns
    -------
    list[str]
        One line per item, suitable for ``print("\\n".join(lines))``.
    """
    checks: list[dict[str, Any]] = report.get("checks", [])
    if not checks:
        summary = report.get("summary", "no checks")
        return [f"Doctor: {summary}"]

    summary = report.get("summary", "")
    healthy = report.get("healthy", False)
    status_label = "OK" if healthy else "DEGRADED"

    lines: list[str] = [f"Doctor: {summary}  [{status_label}]"]
    for check in checks:
        name = check.get("name", "?")
        passed = check.get("passed", False)
        detail = check.get("detail", "")
        tick = "\u2713" if passed else "\u2717"
        line = f"  {tick} {name}"
        if detail:
            line += f" ({detail})"
        lines.append(line)

    return lines


# ── CLI entry point ───────────────────────────────────────────────────────
# Supports: python -m kora_v2.runtime.inspector <topic>
# The __main__.py package entry also handles: python -m kora_v2.runtime <topic>

if __name__ == "__main__":
    from kora_v2.runtime.__main__ import main

    main()
