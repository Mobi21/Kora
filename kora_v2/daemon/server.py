"""Kora V2 — FastAPI server with WebSocket chat endpoint.

Provides:
- ``/api/v1/health`` -- auth-free health probe
- ``/api/v1/status`` -- authenticated system status
- ``/api/v1/ws``     -- WebSocket chat (token via ``?token=`` query param)
- ``/api/v1/daemon/shutdown`` -- graceful shutdown (authenticated)

The server binds to 127.0.0.1 only (security requirement).
Token auth: auto-generated file at ``settings.security.api_token_path``.

WebSocket envelope protocol::

    Client -> Server:  {"type": "chat", "content": "Hello Kora"}
    Server -> Client:  {"type": "token", "content": "Hey!"}
    Server -> Client:  {"type": "response_complete", "metadata": {...}}
    Server -> Client:  {"type": "error", "content": "..."}
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
import uuid
from pathlib import Path
from typing import Any

import structlog
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from kora_v2 import __version__
from kora_v2.core.di import Container
from kora_v2.core.events import EventType

log = structlog.get_logger(__name__)

# Module-level container reference; set by create_app().
_container: Container | None = None
_api_token: str = ""
_shutdown_event: asyncio.Event | None = None
_auth_relay: Any = None
# Phase 7.5b: ``BackgroundWorker`` has been deleted. The orchestration
# engine is the single scheduler. It is created lazily during
# ``create_app`` and started inside ``run_server`` so callers who only
# need the FastAPI app (e.g. tests that mount routes directly) don't
# pay the engine's startup cost.
_orchestration_engine: Any = None
_connected_clients: list[WebSocket] = []
# Uvicorn server instance; set by run_server(). The /daemon/shutdown
# endpoint flips `_server.should_exit = True` so uvicorn's main_loop
# actually unwinds — previously we only set an asyncio.Event that
# nothing awaited, and the process would hang forever.
_server: uvicorn.Server | None = None


# ── Token Management ─────────────────────────────────────────────────────


def _load_or_create_token(path: str) -> str:
    """Load API token from file, or generate and save one.

    Args:
        path: Filesystem path for the token file.

    Returns:
        The API token string.
    """
    p = Path(path)
    if p.exists():
        token = p.read_text().strip()
        if token:
            return token

    p.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    p.write_text(token)
    p.chmod(0o600)
    log.info("api_token_created", path=str(p))
    return token


# ── Auth Dependency ──────────────────────────────────────────────────────


async def verify_token(authorization: str | None = Header(None)) -> None:
    """FastAPI dependency that validates the Bearer token.

    Raises:
        HTTPException: 401 if token is missing or invalid.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")

    if not hmac.compare_digest(parts[1], _api_token):
        raise HTTPException(status_code=401, detail="Invalid token")


# ── App Factory ──────────────────────────────────────────────────────────


def create_app(container: Container) -> FastAPI:
    """Create and configure the FastAPI application.

    Wires the DI container, token auth, CORS, and all routes.

    Args:
        container: Initialized DI container.

    Returns:
        Configured FastAPI instance.
    """
    global _container, _api_token, _shutdown_event, _auth_relay, _orchestration_engine  # noqa: PLW0603

    _container = container
    _api_token = _load_or_create_token(container.settings.security.api_token_path)
    _shutdown_event = asyncio.Event()

    from kora_v2.daemon.auth_relay import AuthRelay
    _auth_relay = AuthRelay()
    container._auth_relay = _auth_relay  # make accessible to supervisor graph

    # Phase 7.5b: BackgroundWorker has been replaced with the
    # OrchestrationEngine. The engine itself is constructed in
    # ``run_server`` (which is async) and stored on the container;
    # ``create_app`` only needs the already-built instance so the
    # ``/api/v1/status`` endpoint can report pipeline counts.
    _orchestration_engine = container.orchestration_engine
    if _orchestration_engine is not None:
        log.info(
            "orchestration_engine_ready",
            pipelines=len(_orchestration_engine.pipelines.all()),
        )

    # Subscribe to events for WebSocket broadcasting
    _setup_event_subscriptions(container)

    app = FastAPI(
        title="Kora V2",
        version=__version__,
        docs_url=None,   # disable Swagger UI in production
        redoc_url=None,  # disable ReDoc in production
    )

    # CORS -- restricted to localhost origins from settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=container.settings.security.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    app.include_router(_build_router())

    # Register WebSocket endpoint
    _attach_websocket_route(app)

    log.info("fastapi_app_created", version=__version__)
    return app


async def _broadcast_to_clients(payload: dict) -> None:
    """Send a notification to all connected WebSocket clients."""
    notification = payload.get("notification", {})
    if not notification:
        notification = {
            k: v for k, v in payload.items()
            if k not in ("event_type", "correlation_id") and not callable(v)
        }
    msg = {"type": "notification", **notification}
    for ws in list(_connected_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            pass


def _setup_event_subscriptions(container: Container) -> None:
    """Subscribe to events that should broadcast to WebSocket clients."""
    container.event_emitter.on(EventType.NOTIFICATION_SENT, _broadcast_to_clients)

    async def _on_autonomous_checkpoint(payload: dict) -> None:
        """Broadcast AUTONOMOUS_CHECKPOINT as a typed message to all clients."""
        msg = {
            "type": "autonomous_checkpoint",
            "session_id": payload.get("session_id"),
            "steps_completed": payload.get("steps_completed", 0),
            "elapsed_seconds": payload.get("elapsed_seconds", 0),
        }
        for ws in list(_connected_clients):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    container.event_emitter.on(EventType.AUTONOMOUS_CHECKPOINT, _on_autonomous_checkpoint)

    async def _on_autonomous_failed(payload: dict) -> None:
        """Broadcast AUTONOMOUS_FAILED as a typed message to all clients."""
        msg = {
            "type": "autonomous_failed",
            "goal": payload.get("goal", ""),
            "reason": payload.get("reason", ""),
            "session_id": payload.get("session_id", ""),
        }
        for ws in list(_connected_clients):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    container.event_emitter.on(EventType.AUTONOMOUS_FAILED, _on_autonomous_failed)


# ── Routes ───────────────────────────────────────────────────────────────


def _build_router():
    """Build the API router with all endpoints."""
    from fastapi import APIRouter

    router = APIRouter(prefix="/api/v1")

    # --- Health (no auth) ---

    @router.get("/health")
    async def health() -> dict[str, str]:
        """Auth-free health check used by launcher probes."""
        return {"status": "ok", "version": __version__}

    # --- Status (auth required) ---

    @router.get("/status", dependencies=[Depends(verify_token)])
    async def status() -> dict[str, Any]:
        """System status with session and turn information."""
        session_mgr = getattr(_container, "session_manager", None)
        session = session_mgr.active_session if session_mgr else None
        failed = getattr(_container, "_failed_subsystems", []) if _container else []

        return {
            "status": "degraded" if failed else "running",
            "version": __version__,
            "session_active": session is not None,
            "session_id": session.session_id if session else None,
            "turn_count": session.turn_count if session else 0,
            "started_at": session.started_at.isoformat() if session else None,
            "failed_subsystems": failed,
            "orchestration_pipelines": (
                len(_orchestration_engine.pipelines.all())
                if _orchestration_engine
                else 0
            ),
        }

    # --- Shutdown (auth required) ---

    @router.post("/daemon/shutdown", dependencies=[Depends(verify_token)])
    async def shutdown() -> dict[str, str]:
        """Request graceful daemon shutdown.

        Flips ``_server.should_exit`` — the documented uvicorn signal
        that causes ``main_loop()`` to unwind on the next tick (~100ms).
        After that, ``run_server()``'s cleanup runs, the background
        worker stops, and ``launcher.py``'s finally block calls
        ``container.close()`` (which flushes the checkpointer).

        We also keep ``_shutdown_event.set()`` in case any future code
        chooses to await it, but the authoritative signal is
        ``server.should_exit``.
        """
        log.info("shutdown_requested")
        if _shutdown_event is not None:
            _shutdown_event.set()
        if _server is not None:
            _server.should_exit = True
        else:
            log.warning("shutdown_no_server_ref")
        return {"status": "shutting_down"}

    # --- Autonomous loop inspection (auth required) ---

    @router.get("/orchestration/status", dependencies=[Depends(verify_token)])
    async def orchestration_status() -> dict[str, Any]:
        """Phase 7.5b: snapshot of pipelines, tasks, and gate state.

        Returns:
            * ``pipelines`` — registered pipeline names + stage count.
            * ``live_tasks`` — state + stage of every non-terminal task.
            * ``open_decisions_count`` — how many decisions are pending.
            * ``system_phase`` — the state machine's current phase.
        """
        engine = _orchestration_engine
        if engine is None:
            return {
                "status": "unavailable",
                "pipelines": [],
                "live_tasks": [],
                "open_decisions_count": 0,
                "system_phase": None,
            }

        pipelines = [
            {"name": p.name, "stage_count": len(p.stages)}
            for p in engine.pipelines.all()
        ]

        try:
            live_tasks_raw = await engine.list_tasks()
        except Exception:  # noqa: BLE001
            live_tasks_raw = []
        live_tasks = [
            {
                "task_id": getattr(t, "id", None),
                "stage": getattr(t, "stage_name", None),
                "state": getattr(getattr(t, "state", None), "value", None)
                or str(getattr(t, "state", "")),
                "goal": getattr(t, "goal", None),
                "pipeline_instance_id": getattr(t, "pipeline_instance_id", None),
            }
            for t in live_tasks_raw
        ]

        try:
            pending = await engine.get_pending_decisions(limit=100)
        except Exception:  # noqa: BLE001
            pending = []

        phase = None
        try:
            from datetime import UTC as _UTC
            from datetime import datetime as _dt
            phase_obj = engine.state_machine.current_phase(_dt.now(_UTC))
            phase = getattr(phase_obj, "value", str(phase_obj))
        except Exception:  # noqa: BLE001
            phase = None

        return {
            "status": "ok",
            "pipelines": pipelines,
            "live_tasks": live_tasks,
            "open_decisions_count": len(pending),
            "system_phase": phase,
        }

    @router.get("/inspect/autonomous", dependencies=[Depends(verify_token)])
    async def inspect_autonomous() -> dict[str, Any]:
        """Return active autonomous loop state for all sessions."""
        loops = getattr(_container, "_autonomous_loops", {}) if _container else {}
        result: dict[str, Any] = {}
        for sid, entry in loops.items():
            task = entry.get("task")
            loop = entry.get("loop")
            state = loop.state if loop is not None else None
            result[sid] = {
                "goal": entry.get("goal", ""),
                "running": task is not None and not task.done(),
                "status": state.status if state is not None else "unknown",
                "steps_completed": len(state.completed_step_ids) if state is not None else 0,
                "steps_pending": len(state.pending_step_ids) if state is not None else 0,
                "request_count": state.request_count if state is not None else 0,
                "elapsed_seconds": state.elapsed_seconds if state is not None else 0,
            }
        return {"loops": result, "count": len(result)}

    # --- Generic inspect topic dispatcher (auth required) ---

    @router.get("/inspect/{topic}", dependencies=[Depends(verify_token)])
    async def inspect_topic(topic: str) -> dict[str, Any]:
        """Dispatch to RuntimeInspector for any supported topic.

        Supported topics: setup, tools, workers, permissions, session,
        trace, doctor, phase-audit. 'autonomous' has its own typed route
        registered above and will match before this generic handler.
        """
        if _container is None:
            raise HTTPException(status_code=503, detail="Container not initialized")
        from kora_v2.runtime.inspector import RuntimeInspector
        inspector = RuntimeInspector(_container)
        try:
            result = await inspector.inspect(topic)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail=f"Unknown inspect topic: {topic}"
            ) from exc
        except Exception as exc:
            log.error("inspect_topic_error", topic=topic, error=str(exc))
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        # RuntimeInspector.inspect() returns a dict with `valid_topics` for
        # unknown topics instead of raising — translate that to a 404.
        if isinstance(result, dict) and "valid_topics" in result and "error" in result:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown inspect topic: {topic}",
            )
        return result

    # --- Memory recall (auth required) ---

    @router.get("/memory/recall", dependencies=[Depends(verify_token)])
    async def memory_recall(q: str = "") -> dict[str, Any]:
        """Recall notes from the filesystem memory store.

        Returns a list of recent or matching notes with content and source.
        """
        memory_store = getattr(_container, "memory_store", None) if _container else None
        if not memory_store:
            return {"results": [], "error": "Memory store not available"}

        try:
            notes = await memory_store.list_notes(layer="all")
            # Most recent notes first (list_notes returns oldest-first)
            notes = list(reversed(notes))

            # Simple keyword filtering when a non-trivial query is given
            if q and q.lower() != "recent":
                query_lower = q.lower()
                filtered = [
                    n for n in notes
                    if query_lower in " ".join(n.tags).lower()
                    or query_lower in " ".join(n.entities).lower()
                    or query_lower in n.memory_type.lower()
                ]
                notes = filtered if filtered else notes

            # Limit to 10 and read full content
            notes = notes[:10]
            results = []
            for note_meta in notes:
                full = await memory_store.read_note(note_meta.id)
                body = full.body if full else ""
                results.append({
                    "content": body[:500] if body else f"[{note_meta.memory_type}] {note_meta.id}",
                    "source": note_meta.memory_type,
                    "id": note_meta.id,
                    "tags": note_meta.tags,
                    "created_at": note_meta.created_at,
                })
            return {"results": results}
        except Exception as e:
            log.warning("memory_recall_error", error=str(e))
            return {"results": [], "error": str(e)}

    # --- Compaction trigger (auth required) ---

    @router.post("/compact", dependencies=[Depends(verify_token)])
    async def compact() -> dict[str, Any]:
        """Signal that compaction should run on the next turn."""
        if _container is not None:
            _container._compact_requested = True  # type: ignore[attr-defined]
        return {"status": "compaction_requested"}

    # --- Permission grants (auth required) ---

    @router.get("/permissions", dependencies=[Depends(verify_token)])
    async def permissions() -> dict[str, Any]:
        """List permission grants from the operational database."""
        if _container is None:
            return {"grants": []}

        db_path = _container.settings.data_dir / "operational.db"
        if not db_path.exists():
            return {"grants": []}

        try:
            import aiosqlite

            async with aiosqlite.connect(str(db_path)) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT tool_name, scope, risk_level, decision, reason, granted_at "
                    "FROM permission_grants ORDER BY granted_at DESC LIMIT 50"
                )
                rows = await cur.fetchall()
                return {"grants": [dict(r) for r in rows]}
        except Exception as e:
            log.warning("permissions_query_error", error=str(e))
            return {"grants": []}

    # --- Auth-mode toggle (auth required) ---

    @router.post("/auth-mode", dependencies=[Depends(verify_token)])
    async def set_auth_mode(body: dict) -> dict:
        """Toggle auth mode at runtime for testing.

        Accepts: {"mode": "prompt"} or {"mode": "trust_all"}
        """
        if _container is None:
            raise HTTPException(status_code=503, detail="Container not initialized")
        mode = body.get("mode", "")
        if mode not in ("prompt", "trust_all"):
            raise HTTPException(
                status_code=400,
                detail="mode must be 'prompt' or 'trust_all'",
            )
        _container.settings.security.auth_mode = mode
        log.info("auth_mode_changed", mode=mode)
        return {"status": "ok", "auth_mode": mode}

    return router


# ── WebSocket Helpers ────────────────────────────────────────────────────


async def _safe_send_json(ws: WebSocket, payload: dict) -> None:
    """Send a JSON message to *ws*, silently ignoring send errors."""
    try:
        await ws.send_json(payload)
    except Exception:
        pass


async def _check_autonomous_overlap(
    message: str,
    container: Any,
    ws: WebSocket,
) -> None:
    """Compute topic overlap with any running autonomous loop.

    If an active loop is found, updates its overlap score via
    ``loop.set_overlap_score()``.  When the score is >= 0.70 an
    informational message is sent to the client notifying it that
    background work will pause at the next safe point.

    Args:
        message: Incoming user message text.
        container: DI container; may expose ``_autonomous_loops``.
        ws: Active WebSocket connection (for sending info messages).
    """
    if container is None:
        return

    loops = getattr(container, "_autonomous_loops", {})
    if not loops:
        return

    # Resolve current session_id from session manager when available.
    session_mgr = getattr(container, "session_manager", None)
    session_id: str | None = None
    if session_mgr is not None:
        active = getattr(session_mgr, "active_session", None)
        if active is not None:
            session_id = getattr(active, "session_id", None)

    loop_entry = loops.get(session_id) if session_id else None
    if loop_entry is None:
        # No session-specific loop — fall back to scanning all entries.
        for entry in loops.values():
            task = entry.get("task")
            if task is not None and not task.done():
                loop_entry = entry
                break

    if loop_entry is None:
        return

    task = loop_entry.get("task")
    loop = loop_entry.get("loop")
    if task is None or task.done() or loop is None:
        return

    # Extract goal and active step description from loop state.
    state = loop.state
    if state is None:
        return

    goal: str = state.metadata.get("goal", loop_entry.get("goal", ""))
    steps_meta: dict = state.metadata.get("steps", {})
    current_step_id: str | None = state.current_step_id
    active_step_desc: str = ""
    if current_step_id and current_step_id in steps_meta:
        active_step_desc = steps_meta[current_step_id].get("description", "")

    try:
        from kora_v2.autonomous.overlap import check_topic_overlap

        result = await check_topic_overlap(
            user_message=message,
            autonomous_goal=goal,
            active_step_description=active_step_desc or goal,
            container=container,
        )
        log.debug("overlap_check_result", score=result.score, action=result.action)

        # Propagate score to the loop (it will pause at the next safe boundary
        # when score >= 0.70 triggers the paused_for_overlap route).
        loop.set_overlap_score(result.score)

        # Store for injection into graph_input so the supervisor prompt
        # can mention active autonomous work to the LLM.
        container._last_overlap_score = result.score  # type: ignore[attr-defined]
        container._last_overlap_action = result.action  # type: ignore[attr-defined]

        if result.score >= 0.70 and result.message:
            await _safe_send_json(ws, {
                "type": "info",
                "content": result.message,
            })

    except Exception as exc:
        log.debug("overlap_check_error", error=str(exc))


# ── WebSocket ────────────────────────────────────────────────────────────


async def _websocket_handler(ws: WebSocket) -> None:
    """Main chat WebSocket handler.

    Authentication is done via ``?token=xxx`` query parameter.

    Protocol:
        Client sends: ``{"type": "chat", "content": "Hello"}``
        Client sends: ``{"type": "pong"}``  (heartbeat response)
        Server sends: ``{"type": "token", "content": "..."}``
        Server sends: ``{"type": "tool_start", "content": "tool_name"}``
        Server sends: ``{"type": "tool_result", "content": "completed"}``
        Server sends: ``{"type": "response_complete", "metadata": {...}}``
        Server sends: ``{"type": "ping"}``  (heartbeat)
        Server sends: ``{"type": "error", "content": "..."}``
    """
    # --- Auth via query param ---
    token = ws.query_params.get("token", "")
    if token != _api_token:
        await ws.close(code=4001, reason="Invalid token")
        return

    await ws.accept()
    log.info("websocket_connected")

    _connected_clients.append(ws)

    # Wire auth relay broadcast on first client connect
    if _auth_relay and _auth_relay._broadcast is None:
        async def _auth_broadcast(msg: dict) -> None:
            for client in list(_connected_clients):
                try:
                    await client.send_json(msg)
                except Exception:
                    pass
        _auth_relay.set_broadcast(_auth_broadcast)

    # Init session if session manager available
    session_mgr = getattr(_container, 'session_manager', None) if _container else None
    if session_mgr:
        try:
            await session_mgr.init_session()
        except Exception:
            log.warning("session_init_failed_on_connect")

    # --- Gap 4 & 7: Generate and send greeting at session start ---
    if session_mgr and session_mgr.active_session and _container is not None:
        try:
            graph = _container.supervisor_graph
            thread_id = session_mgr.get_thread_id()
            config = {"configurable": {"thread_id": thread_id}}
            greeting = await session_mgr.generate_greeting(graph, config)
            if greeting:
                await ws.send_json({"type": "token", "content": greeting})
                await ws.send_json({
                    "type": "response_complete",
                    "metadata": {"greeting": True},
                })
        except Exception:
            log.debug("greeting_generation_skipped_on_connect")

    # Start heartbeat task
    heartbeat_task = asyncio.create_task(_heartbeat(ws))

    # Per-connection turn state: busy flag and queued messages.
    turn_state: dict[str, Any] = {"busy": False, "queued_messages": []}

    async def _run_chat_task(content: str) -> None:
        """Execute a single chat turn and process any queued messages afterward."""
        turn_state["busy"] = True
        try:
            await _handle_chat(ws, content)
        finally:
            turn_state["busy"] = False
            # Drain the queue: process the next message if one arrived during
            # this turn.
            queued = turn_state.get("queued_messages", [])
            if queued:
                next_msg = queued.pop(0)
                asyncio.create_task(_run_chat_task(next_msg))

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "chat":
                content = data.get("content", "")
                if content:
                    # Compute overlap with any running autonomous task before
                    # deciding whether to queue or run immediately.
                    await _check_autonomous_overlap(content, _container, ws)

                    if turn_state.get("busy", False):
                        # Another turn is in flight — queue and notify user.
                        turn_state.setdefault("queued_messages", []).append(content)
                        await _safe_send_json(ws, {
                            "type": "info",
                            "content": "Message queued — finishing current response first.",
                        })
                    else:
                        asyncio.create_task(_run_chat_task(content))
                else:
                    await ws.send_json({
                        "type": "error",
                        "content": "Empty message content",
                    })
            elif msg_type == "pong":
                pass  # Heartbeat response, ignore
            elif msg_type == "auth_response":
                req_id = data.get("request_id", "")
                approved = data.get("approved", False)
                scope = data.get("scope", "allow_once")
                if _auth_relay and req_id:
                    _auth_relay.receive_response(req_id, approved, scope)
            elif msg_type == "decision_response":
                decision_id = data.get("decision_id", "")
                chosen = data.get("chosen", "")
                if decision_id and chosen:
                    loops = getattr(_container, "_autonomous_loops", {}) if _container else {}
                    for entry in loops.values():
                        loop = entry.get("loop")
                        if loop is not None:
                            try:
                                loop.submit_decision(decision_id, chosen)
                            except Exception as exc:
                                log.debug(
                                    "decision_submit_error",
                                    decision_id=decision_id,
                                    error=str(exc),
                                )
                    await _safe_send_json(ws, {
                        "type": "decision_ack",
                        "decision_id": decision_id,
                    })
            else:
                await ws.send_json({
                    "type": "error",
                    "content": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        log.info("websocket_disconnected")
    except Exception:
        log.exception("websocket_error")
        try:
            await ws.send_json({
                "type": "error",
                "content": "Internal server error",
            })
        except Exception:
            pass
    finally:
        heartbeat_task.cancel()
        if ws in _connected_clients:
            _connected_clients.remove(ws)
        # End session on disconnect.
        # Pull the latest message list from the LangGraph checkpointer so
        # the bridge note reflects the actual conversation, not an empty
        # placeholder. Without this, bridge.summary was always "Empty
        # session" because the handler never had the turns.
        if session_mgr and session_mgr.active_session:
            try:
                messages: list[dict] = []
                try:
                    graph = _container.supervisor_graph if _container is not None else None
                    if graph is not None:
                        thread_id = session_mgr.get_thread_id()
                        state_snapshot = await graph.aget_state(
                            {"configurable": {"thread_id": thread_id}}
                        )
                        raw_msgs = []
                        if state_snapshot is not None:
                            raw_msgs = (state_snapshot.values or {}).get("messages", [])
                        for msg in raw_msgs:
                            if isinstance(msg, dict):
                                messages.append(msg)
                            else:
                                role = getattr(msg, "type", "")
                                if role == "human":
                                    role = "user"
                                elif role == "ai":
                                    role = "assistant"
                                messages.append({
                                    "role": role,
                                    "content": getattr(msg, "content", ""),
                                })
                except Exception:
                    log.debug("session_end_message_pull_failed")

                from kora_v2.core.models import EmotionalState
                await session_mgr.end_session(
                    messages=messages,
                    emotional_state=EmotionalState(valence=0, arousal=0.3, dominance=0.5),
                )
            except Exception:
                log.warning("session_end_failed_on_disconnect")
        if _auth_relay:
            _auth_relay.clear_session_grants()


async def _heartbeat(ws: WebSocket) -> None:
    """Send ping every 30s to detect dead connections."""
    try:
        while True:
            await asyncio.sleep(30)
            try:
                await ws.send_json({"type": "ping"})
            except Exception:
                break
    except asyncio.CancelledError:
        pass


async def _handle_chat(ws: WebSocket, content: str) -> None:
    """Invoke the supervisor graph and stream the response back.

    Uses ``graph.astream_events()`` for token-by-token streaming when
    available.  Falls back to ``graph.ainvoke()`` if streaming yields
    no content (e.g., mock graphs in tests).

    Args:
        ws: Active WebSocket connection.
        content: User message text.
    """
    assert _container is not None  # noqa: S101

    graph = _container.supervisor_graph

    # Build input for the graph
    graph_input: dict[str, Any] = {
        "messages": [{"role": "user", "content": content}],
    }

    # Inject overlap detection results so the LLM is aware of
    # active autonomous work on a similar topic.
    overlap_score = getattr(_container, "_last_overlap_score", 0.0)
    overlap_action = getattr(_container, "_last_overlap_action", "continue")
    if overlap_score > 0.0:
        graph_input["_overlap_score"] = overlap_score
        graph_input["_overlap_action"] = overlap_action
        # Reset after consumption
        _container._last_overlap_score = 0.0  # type: ignore[attr-defined]
        _container._last_overlap_action = "continue"  # type: ignore[attr-defined]

    # Use session-scoped thread_id from SessionManager when available
    session_mgr = getattr(_container, 'session_manager', None)
    if session_mgr:
        thread_id = session_mgr.get_thread_id()
    else:
        thread_id = uuid.uuid4().hex[:12]

    config = {"configurable": {"thread_id": thread_id}}

    try:
        # Set up real-time tool event callback so clients see tool
        # progress during execution, not after.
        async def _on_tool_event(event: dict) -> None:
            t_name = event.get("tool_name", "unknown")
            success = event.get("success", True)
            await _safe_send_json(ws, {"type": "tool_start", "content": t_name})
            await _safe_send_json(ws, {
                "type": "tool_result",
                "content": "completed" if success else "failed",
                "tool_name": t_name,
            })

        _container._on_tool_event = _on_tool_event  # type: ignore[attr-defined]

        # Use ainvoke() — astream_events() doesn't work with MiniMax
        # (no on_chat_model_stream events; fallback causes double-run).
        result = await graph.ainvoke(graph_input, config)

        response_content = result.get("response_content", "")
        tool_call_records = result.get("tool_call_records", [])
        turn_count = result.get("turn_count", 0)
        compaction_tier = result.get("compaction_tier", "")
        compaction_tokens = result.get("compaction_tokens")

        if not response_content:
            messages = result.get("messages", [])
            for msg in reversed(messages):
                role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "type", "")
                msg_content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
                if role in ("assistant", "ai") and msg_content:
                    response_content = msg_content
                    break

        # Tool events already sent in real-time via _on_tool_event callback.

        if response_content:
            await ws.send_json({
                "type": "token",
                "content": response_content,
            })

        await ws.send_json({
            "type": "response_complete",
            "metadata": {
                "turn_count": turn_count,
                "tool_call_count": len(tool_call_records),
                "compaction_tier": compaction_tier,
                "token_count": compaction_tokens,
            },
        })

    except Exception as e:
        log.exception("graph_invocation_error")
        await ws.send_json({
            "type": "error",
            "content": f"Graph error: {e!s}",
        })
    finally:
        _container._on_tool_event = None  # type: ignore[attr-defined]


# ── Server Runner ────────────────────────────────────────────────────────


def _attach_websocket_route(app: FastAPI) -> FastAPI:
    """Attach the WebSocket route to the app instance.

    Must be called after create_app() to wire the WS handler.
    """
    @app.websocket("/api/v1/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await _websocket_handler(ws)

    return app


async def run_server(
    container: Container,
    host: str = "127.0.0.1",
    port: int = 0,
    on_bind: Any = None,
) -> None:
    """Start the uvicorn server with the given container.

    Args:
        container: Initialized DI container.
        host: Bind address. Must be 127.0.0.1 for security.
        port: Bind port. 0 = auto-assign.
        on_bind: Optional callback ``(host, actual_port) -> None`` called
                 after uvicorn binds successfully (before main loop).
    """
    # Security: enforce localhost binding
    if host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("refusing_non_localhost_bind", requested=host)
        host = "127.0.0.1"

    # Phase 7.5b: build the OrchestrationEngine before create_app so
    # ``container.orchestration_engine`` is non-None by the time
    # ``create_app`` needs it for the /status endpoint. The engine
    # itself is not *started* until the uvicorn bind callback runs.
    engine = await container.initialize_orchestration(
        websocket_broadcast=_broadcast_to_clients,
        session_active_fn=lambda: bool(_connected_clients),
    )
    from kora_v2.runtime.orchestration.core_pipelines import (
        register_core_pipelines,
    )
    register_core_pipelines(engine)
    log.info(
        "orchestration_engine_constructed_pre_app",
        pipelines=len(engine.pipelines.all()),
    )

    app = create_app(container)

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    # Publish the server instance so the /daemon/shutdown endpoint can
    # flip should_exit on the next tick.
    global _server
    _server = server

    log.info("server_starting", host=host, port=port)

    if _orchestration_engine is not None:
        await _orchestration_engine.start()

    try:
        # Split serve() into startup + main_loop + shutdown so we can
        # capture the actual bound port when port=0 (OS-assigned).
        # Replicate the setup that serve()/_serve() does before startup().
        if not config.loaded:
            config.load()
        server.lifespan = config.lifespan_class(config)

        await server.startup()
        if server.should_exit:
            return

        # Discover actual port from uvicorn's bound sockets
        actual_port = port
        for srv in getattr(server, "servers", []):
            sockets = getattr(srv, "sockets", None)
            if sockets:
                actual_port = sockets[0].getsockname()[1]
                break

        if on_bind is not None:
            on_bind(host, actual_port)

        await server.main_loop()
        if server.started:
            await server.shutdown()
    finally:
        if _orchestration_engine is not None:
            await _orchestration_engine.stop(graceful=True)
        _server = None
