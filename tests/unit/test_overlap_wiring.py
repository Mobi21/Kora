"""Tests for Workstream 4: overlap detection + decision routing wired in server.py.

Covers:
- _check_autonomous_overlap does nothing when no active loops
- _check_autonomous_overlap calls set_overlap_score on active loop
- Score >= 0.70 causes _safe_send_json to emit an "info" message
- decision_response message type routes to loop.submit_decision()
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kora_v2.daemon.server import _check_autonomous_overlap, _safe_send_json


# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_ws() -> MagicMock:
    """Return a mock WebSocket with an async send_json."""
    ws = MagicMock()
    ws.send_json = AsyncMock()
    return ws


def _make_loop_entry(
    *,
    task_done: bool = False,
    state_goal: str = "research tools",
    current_step_id: str | None = None,
    steps_meta: dict | None = None,
) -> dict:
    """Build a fake _autonomous_loops entry."""
    state = MagicMock()
    state.metadata = {"goal": state_goal, "steps": steps_meta or {}}
    state.current_step_id = current_step_id

    task = MagicMock()
    task.done.return_value = task_done

    loop = MagicMock()
    loop.state = state
    loop.set_overlap_score = MagicMock()

    return {"task": task, "loop": loop, "goal": state_goal}


def _make_container(loop_entry: dict | None = None, session_id: str = "sess-1") -> MagicMock:
    """Return a fake container with optional autonomous loop."""
    container = MagicMock()
    container.embedding_service = None  # no real embeddings in tests

    if loop_entry is not None:
        container._autonomous_loops = {session_id: loop_entry}
    else:
        container._autonomous_loops = {}

    session_mgr = MagicMock()
    active = MagicMock()
    active.session_id = session_id
    session_mgr.active_session = active
    container.session_manager = session_mgr

    return container


# ── _safe_send_json ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safe_send_json_forwards_payload():
    """_safe_send_json sends the payload to the WebSocket."""
    ws = _make_ws()
    await _safe_send_json(ws, {"type": "info", "content": "hello"})
    ws.send_json.assert_awaited_once_with({"type": "info", "content": "hello"})


@pytest.mark.asyncio
async def test_safe_send_json_silently_swallows_errors():
    """_safe_send_json does not propagate exceptions from send_json."""
    ws = _make_ws()
    ws.send_json.side_effect = RuntimeError("connection closed")
    # Must not raise
    await _safe_send_json(ws, {"type": "ping"})


# ── _check_autonomous_overlap — no loops ──────────────────────────────────


@pytest.mark.asyncio
async def test_check_overlap_noop_when_container_is_none():
    """Does nothing when container is None."""
    ws = _make_ws()
    await _check_autonomous_overlap("hello", None, ws)
    ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_overlap_noop_when_no_loops():
    """Does nothing when _autonomous_loops is empty."""
    container = _make_container(loop_entry=None)
    ws = _make_ws()
    await _check_autonomous_overlap("hello", container, ws)
    ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_overlap_noop_when_task_done():
    """Does nothing when the loop task has already completed."""
    entry = _make_loop_entry(task_done=True)
    container = _make_container(loop_entry=entry)
    ws = _make_ws()

    overlap_result = MagicMock(score=0.9, action="pause", message="pausing")
    with patch(
        "kora_v2.autonomous.overlap.check_topic_overlap",
        new=AsyncMock(return_value=overlap_result),
    ):
        await _check_autonomous_overlap("hello", container, ws)

    # Loop set_overlap_score should NOT have been called (task is done)
    entry["loop"].set_overlap_score.assert_not_called()


# ── _check_autonomous_overlap — active loop ───────────────────────────────


@pytest.mark.asyncio
async def test_check_overlap_calls_set_overlap_score():
    """When an active loop exists, set_overlap_score is called with the score."""
    entry = _make_loop_entry(task_done=False, state_goal="research PM tools")
    container = _make_container(loop_entry=entry)
    ws = _make_ws()

    from kora_v2.autonomous.overlap import OverlapResult

    overlap_result = OverlapResult(score=0.3, action="continue", message=None)

    with patch(
        "kora_v2.autonomous.overlap.check_topic_overlap",
        new=AsyncMock(return_value=overlap_result),
    ):
        await _check_autonomous_overlap("what time is it?", container, ws)

    entry["loop"].set_overlap_score.assert_called_once_with(pytest.approx(0.3, abs=1e-6))
    # No info message for low score
    ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_overlap_sends_info_when_score_high():
    """Score >= 0.70 causes an 'info' message to be sent to the client."""
    entry = _make_loop_entry(task_done=False, state_goal="research PM tools")
    container = _make_container(loop_entry=entry)
    ws = _make_ws()

    from kora_v2.autonomous.overlap import OverlapResult

    overlap_result = OverlapResult(
        score=0.85,
        action="pause",
        message="That sounds related. I am pausing at the next safe point.",
    )

    with patch(
        "kora_v2.autonomous.overlap.check_topic_overlap",
        new=AsyncMock(return_value=overlap_result),
    ):
        await _check_autonomous_overlap("can you check on the PM research?", container, ws)

    entry["loop"].set_overlap_score.assert_called_once_with(pytest.approx(0.85, abs=1e-6))

    ws.send_json.assert_awaited_once()
    call_payload = ws.send_json.call_args[0][0]
    assert call_payload["type"] == "info"
    assert "safe point" in call_payload["content"]


@pytest.mark.asyncio
async def test_check_overlap_no_info_for_ambiguous_score():
    """Ambiguous score (0.45–0.70) calls set_overlap_score but no 'info' message."""
    entry = _make_loop_entry(task_done=False, state_goal="research PM tools")
    container = _make_container(loop_entry=entry)
    ws = _make_ws()

    from kora_v2.autonomous.overlap import OverlapResult

    overlap_result = OverlapResult(score=0.55, action="ambiguous", message=None)

    with patch(
        "kora_v2.autonomous.overlap.check_topic_overlap",
        new=AsyncMock(return_value=overlap_result),
    ):
        await _check_autonomous_overlap("project update?", container, ws)

    entry["loop"].set_overlap_score.assert_called_once_with(pytest.approx(0.55, abs=1e-6))
    ws.send_json.assert_not_awaited()


# ── decision_response WebSocket message routing ────────────────────────────
# We test this via the FastAPI TestClient to exercise the full WS handler.


def _build_test_app(loop_entry: dict | None = None):
    """Build a minimal test FastAPI app with a patched container."""
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient

    from kora_v2.core.di import Container
    from kora_v2.core.settings import Settings
    from kora_v2.daemon import server as server_module
    from kora_v2.daemon.server import _attach_websocket_route, create_app

    with tempfile.TemporaryDirectory() as tmpdir:
        token_path = Path(tmpdir) / "token"
        token_path.write_text("test-tok")

        s = Settings()
        s.security.api_token_path = str(token_path)

        with patch("kora_v2.core.di.MiniMaxProvider"):
            container = Container(s)

        if loop_entry is not None:
            container._autonomous_loops = {"sess-1": loop_entry}
        else:
            container._autonomous_loops = {}

        app = create_app(container)
        _attach_websocket_route(app)
        return TestClient(app), "test-tok", server_module


def test_decision_response_routes_to_submit_decision():
    """decision_response WS message calls loop.submit_decision()."""
    entry = _make_loop_entry(task_done=False)
    client, token, _mod = _build_test_app(loop_entry=entry)

    with client.websocket_connect(f"/api/v1/ws?token={token}") as ws:
        ws.send_json({
            "type": "decision_response",
            "decision_id": "dec-abc",
            "chosen": "continue",
        })
        ack = ws.receive_json()

    assert ack["type"] == "decision_ack"
    assert ack["decision_id"] == "dec-abc"
    entry["loop"].submit_decision.assert_called_once_with("dec-abc", "continue")


def test_decision_response_missing_fields_no_crash():
    """decision_response with empty fields does not crash the handler."""
    client, token, _mod = _build_test_app(loop_entry=None)

    with client.websocket_connect(f"/api/v1/ws?token={token}") as ws:
        # Missing decision_id and chosen — should not produce an error
        ws.send_json({"type": "decision_response", "decision_id": "", "chosen": ""})
        # Send a harmless pong to confirm the connection is still alive
        ws.send_json({"type": "pong"})
        # Connection should still be usable (no crash); unknown_type would
        # produce an error but pong produces nothing.
