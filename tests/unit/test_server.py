"""Tests for kora_v2.daemon.server — FastAPI app + WebSocket."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from kora_v2.core.di import Container
from kora_v2.core.settings import Settings
from kora_v2.daemon import server as server_module
from kora_v2.daemon.server import (
    _attach_websocket_route,
    _load_or_create_token,
    create_app,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

TEST_TOKEN = "test-token-abc123"


@pytest.fixture()
def settings(tmp_path) -> Settings:
    """Settings with a temporary api_token_path."""
    token_path = tmp_path / "api_token"
    token_path.write_text(TEST_TOKEN)
    s = Settings()
    s.security.api_token_path = str(token_path)
    return s


@pytest.fixture()
def container(settings: Settings) -> Container:
    """Container with mocked LLM provider (no real API calls)."""
    with patch("kora_v2.core.di.MiniMaxProvider"):
        c = Container(settings)
    return c


@pytest.fixture()
def app(container: Container) -> TestClient:
    """FastAPI TestClient for REST endpoint tests."""
    fastapi_app = create_app(container)
    _attach_websocket_route(fastapi_app)
    return TestClient(fastapi_app)


# ── Token Management Tests ───────────────────────────────────────────────


class TestTokenManagement:
    """Token loading, generation, and file permissions."""

    def test_load_existing_token(self, tmp_path):
        """Loads token from existing file."""
        token_file = tmp_path / "token"
        token_file.write_text("my-secret-token")
        assert _load_or_create_token(str(token_file)) == "my-secret-token"

    def test_create_new_token(self, tmp_path):
        """Generates a new token when file does not exist."""
        token_file = tmp_path / "new_token"
        token = _load_or_create_token(str(token_file))
        assert len(token) > 20
        assert token_file.exists()
        assert token_file.read_text() == token

    def test_create_token_in_nested_dir(self, tmp_path):
        """Creates parent directories if they don't exist."""
        token_file = tmp_path / "nested" / "dir" / "token"
        token = _load_or_create_token(str(token_file))
        assert token_file.exists()
        assert len(token) > 20


# ── Health Endpoint Tests ────────────────────────────────────────────────


class TestHealthEndpoint:
    """GET /api/v1/health — no auth required."""

    def test_health_returns_200_without_auth(self, app: TestClient):
        """Health endpoint is accessible without any auth."""
        resp = app.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body

    def test_health_includes_version(self, app: TestClient):
        """Health response includes the current version string."""
        resp = app.get("/api/v1/health")
        body = resp.json()
        assert body["version"] == "2.0.0a1"


# ── Status Endpoint Tests ────────────────────────────────────────────────


class TestStatusEndpoint:
    """GET /api/v1/status — auth required."""

    def test_status_returns_401_without_token(self, app: TestClient):
        """Status endpoint rejects unauthenticated requests."""
        resp = app.get("/api/v1/status")
        assert resp.status_code == 401

    def test_status_returns_401_with_wrong_token(self, app: TestClient):
        """Status endpoint rejects incorrect tokens."""
        resp = app.get(
            "/api/v1/status",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_status_returns_200_with_valid_token(self, app: TestClient):
        """Status endpoint accepts valid Bearer token."""
        resp = app.get(
            "/api/v1/status",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "running"

    def test_status_returns_401_with_malformed_header(self, app: TestClient):
        """Status endpoint rejects non-Bearer auth schemes."""
        resp = app.get(
            "/api/v1/status",
            headers={"Authorization": f"Basic {TEST_TOKEN}"},
        )
        assert resp.status_code == 401


# ── Shutdown Endpoint Tests ──────────────────────────────────────────────


class TestShutdownEndpoint:
    """POST /api/v1/daemon/shutdown — auth required."""

    def test_shutdown_returns_401_without_token(self, app: TestClient):
        """Shutdown requires authentication."""
        resp = app.post("/api/v1/daemon/shutdown")
        assert resp.status_code == 401

    def test_shutdown_returns_200_with_valid_token(self, app: TestClient):
        """Authenticated shutdown returns status."""
        resp = app.post(
            "/api/v1/daemon/shutdown",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "shutting_down"


# ── WebSocket Tests ──────────────────────────────────────────────────────


class TestWebSocket:
    """WebSocket endpoint at /api/v1/ws."""

    def test_ws_rejects_without_token(self, app: TestClient):
        """WebSocket connection without token is closed with 4001."""
        with pytest.raises(Exception):
            # TestClient raises when WS is rejected
            with app.websocket_connect("/api/v1/ws"):
                pass

    def test_ws_rejects_wrong_token(self, app: TestClient):
        """WebSocket connection with wrong token is closed."""
        with pytest.raises(Exception):
            with app.websocket_connect("/api/v1/ws?token=bad-token"):
                pass

    def test_ws_connects_with_valid_token(self, app: TestClient):
        """WebSocket accepts connection with correct token."""
        with app.websocket_connect(f"/api/v1/ws?token={TEST_TOKEN}") as ws:
            # Connection established -- just verify it doesn't raise
            assert ws is not None

    def test_ws_unknown_message_type_returns_error(self, app: TestClient):
        """Sending an unknown message type gets an error response."""
        with app.websocket_connect(f"/api/v1/ws?token={TEST_TOKEN}") as ws:
            ws.send_json({"type": "unknown_type", "content": "test"})
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert "Unknown message type" in resp["content"]

    def test_ws_empty_content_returns_error(self, app: TestClient):
        """Sending a chat message with empty content gets an error."""
        with app.websocket_connect(f"/api/v1/ws?token={TEST_TOKEN}") as ws:
            ws.send_json({"type": "chat", "content": ""})
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert "Empty message" in resp["content"]

    def test_ws_chat_invokes_graph_and_returns_response(self, app: TestClient):
        """Chat message invokes supervisor graph and streams back response."""
        # Mock the supervisor graph to return a canned state
        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "response_content": "Hello from Kora!",
            "turn_count": 1,
            "tool_call_records": [],
        }

        # Patch the container's supervisor graph
        assert server_module._container is not None
        original_graph = server_module._container._supervisor_graph
        server_module._container._supervisor_graph = mock_graph

        try:
            with app.websocket_connect(f"/api/v1/ws?token={TEST_TOKEN}") as ws:
                ws.send_json({"type": "chat", "content": "Hello!"})

                # Should receive token message with the response
                token_msg = ws.receive_json()
                assert token_msg["type"] == "token"
                assert token_msg["content"] == "Hello from Kora!"

                # Should receive response_complete
                complete_msg = ws.receive_json()
                assert complete_msg["type"] == "response_complete"
                assert complete_msg["metadata"]["turn_count"] == 1
        finally:
            server_module._container._supervisor_graph = original_graph

    def test_ws_graph_error_returns_error_message(self, app: TestClient):
        """Graph invocation error is sent back as an error message."""
        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = RuntimeError("LLM exploded")

        assert server_module._container is not None
        original_graph = server_module._container._supervisor_graph
        server_module._container._supervisor_graph = mock_graph

        try:
            with app.websocket_connect(f"/api/v1/ws?token={TEST_TOKEN}") as ws:
                ws.send_json({"type": "chat", "content": "Trigger error"})

                error_msg = ws.receive_json()
                assert error_msg["type"] == "error"
                assert "LLM exploded" in error_msg["content"]
        finally:
            server_module._container._supervisor_graph = original_graph

    def test_ws_pong_ignored(self, app: TestClient):
        """Pong messages are silently accepted (heartbeat response)."""
        with app.websocket_connect(f"/api/v1/ws?token={TEST_TOKEN}") as ws:
            # Sending pong should not produce an error response
            ws.send_json({"type": "pong"})
            # Send a chat to verify the connection is still alive
            mock_graph = AsyncMock()
            mock_graph.ainvoke.return_value = {
                "response_content": "Still here!",
                "turn_count": 1,
                "tool_call_records": [],
            }
            assert server_module._container is not None
            original_graph = server_module._container._supervisor_graph
            server_module._container._supervisor_graph = mock_graph
            try:
                ws.send_json({"type": "chat", "content": "ping"})
                token_msg = ws.receive_json()
                assert token_msg["type"] == "token"
            finally:
                server_module._container._supervisor_graph = original_graph

    def test_ws_session_manager_thread_id(self, app: TestClient):
        """When session_manager is available, thread_id comes from it."""
        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "response_content": "Hi!",
            "turn_count": 1,
            "tool_call_records": [],
        }

        assert server_module._container is not None
        original_graph = server_module._container._supervisor_graph
        server_module._container._supervisor_graph = mock_graph

        # Set up a mock session manager on the container
        mock_session_mgr = MagicMock()
        mock_session_mgr.get_thread_id.return_value = "session-test-123"
        mock_session_mgr.active_session = None
        mock_session_mgr.init_session = AsyncMock()
        server_module._container.session_manager = mock_session_mgr

        try:
            with app.websocket_connect(f"/api/v1/ws?token={TEST_TOKEN}") as ws:
                ws.send_json({"type": "chat", "content": "Hello!"})
                token_msg = ws.receive_json()
                assert token_msg["type"] == "token"

                # Verify the graph was called with the session thread_id
                call_args = mock_graph.ainvoke.call_args
                config = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("config", call_args[0][1])
                assert config["configurable"]["thread_id"] == "session-test-123"
        finally:
            server_module._container._supervisor_graph = original_graph
            server_module._container.session_manager = None
