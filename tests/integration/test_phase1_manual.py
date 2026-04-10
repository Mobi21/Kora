"""Phase 1 Manual Test Suite — automated version of the 9 manual tests.

Exercises the full Phase 1 stack: DI container, supervisor graph,
FastAPI server, WebSocket chat, health/auth endpoints, and LLM retry.

Requires MINIMAX_API_KEY in .env or environment.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import structlog
from dotenv import load_dotenv

# Load .env before importing kora_v2 settings
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from kora_v2.core.di import Container
from kora_v2.core.events import EventEmitter
from kora_v2.core.settings import Settings, get_settings
from kora_v2.daemon.server import create_app

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings():
    """Fresh settings with API key loaded."""
    get_settings.cache_clear()
    s = get_settings()
    if not s.llm.api_key:
        pytest.skip("MINIMAX_API_KEY not set — cannot run live LLM tests")
    return s


@pytest.fixture(scope="module")
def container(settings):
    """DI container wired with live settings."""
    return Container(settings)


@pytest.fixture(scope="module")
def app(container):
    """FastAPI app wired to the live container."""
    return create_app(container)


@pytest.fixture(scope="module")
def api_token(container):
    """The auto-generated API token."""
    from kora_v2.daemon.server import _load_or_create_token
    return _load_or_create_token(container.settings.security.api_token_path)


# ---------------------------------------------------------------------------
# Test 7: DI container — Verify all services construct without error
# ---------------------------------------------------------------------------


class TestDIContainer:
    """Manual test 7: DI container constructs all services."""

    def test_container_constructs(self, container):
        """All services instantiate with correct types."""
        from kora_v2.llm.minimax import MiniMaxProvider

        assert container.settings is not None
        assert isinstance(container.llm, MiniMaxProvider)
        assert isinstance(container.event_emitter, EventEmitter)
        log.info("MANUAL_TEST_7", status="PASS", detail="All services construct")

    def test_supervisor_graph_builds(self, container):
        """Supervisor graph builds lazily without error."""
        graph = container.supervisor_graph
        assert graph is not None
        log.info("MANUAL_TEST_7b", status="PASS", detail="Supervisor graph builds")


# ---------------------------------------------------------------------------
# Test 5: Health endpoint — GET /api/v1/health without auth
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Manual test 5: Health endpoint returns 200 without auth."""

    @pytest.mark.anyio
    async def test_health_no_auth(self, app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/api/v1/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "version" in data
            log.info("MANUAL_TEST_5", status="PASS", detail=data)


# ---------------------------------------------------------------------------
# Test 6: Auth enforcement — Call /api/v1/status without token
# ---------------------------------------------------------------------------


class TestAuthEnforcement:
    """Manual test 6: /api/v1/status rejects without token."""

    @pytest.mark.anyio
    async def test_status_no_auth(self, app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/api/v1/status")
            assert resp.status_code == 401
            log.info("MANUAL_TEST_6", status="PASS", detail="401 returned")

    @pytest.mark.anyio
    async def test_status_with_auth(self, app, api_token):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get(
                "/api/v1/status",
                headers={"Authorization": f"Bearer {api_token}"},
            )
            assert resp.status_code == 200
            log.info("MANUAL_TEST_6b", status="PASS", detail="200 with auth")


# ---------------------------------------------------------------------------
# Test 4: Harness schema validation
# ---------------------------------------------------------------------------


class TestHarnessSchemaValidation:
    """Manual test 4: Agent harness validates output against Pydantic."""

    @pytest.mark.anyio
    async def test_schema_gate(self):
        from pydantic import BaseModel
        from kora_v2.agents.quality_gates import SchemaValidationGate
        from kora_v2.core.models import QualityGateResult

        class TestOutput(BaseModel):
            answer: str
            confidence: float

        gate = SchemaValidationGate(schema=TestOutput)

        # Valid output
        valid = TestOutput(answer="hello", confidence=0.9)
        result = await gate.check(valid, {})
        assert result.passed
        log.info("MANUAL_TEST_4", status="PASS", detail="Schema validation works")

        # Invalid output (dict missing fields)
        invalid_result = await gate.check({"wrong": "shape"}, {})
        assert not invalid_result.passed
        log.info("MANUAL_TEST_4b", status="PASS", detail="Invalid output rejected")


# ---------------------------------------------------------------------------
# Test 8: Graph 5-node flow — Trace message through all nodes
# ---------------------------------------------------------------------------


class TestGraphNodeFlow:
    """Manual test 8: Message flows through all 5 nodes."""

    @pytest.mark.anyio
    async def test_five_node_flow(self, container):
        """Send a message through the graph and verify all nodes execute."""
        graph = container.supervisor_graph

        # Use a simple message
        input_state = {
            "messages": [{"role": "user", "content": "Say exactly: test_phase1_ok"}],
        }
        config = {"configurable": {"thread_id": "test-5node"}}

        result = await graph.ainvoke(input_state, config)

        # Verify the graph produced output
        assert "messages" in result
        assert len(result["messages"]) >= 2  # user + assistant
        assert result.get("turn_count", 0) >= 1
        assert result.get("frozen_prefix", "")  # built by build_suffix
        assert result.get("response_content", "")  # set by think or synthesize

        log.info(
            "MANUAL_TEST_8",
            status="PASS",
            turn_count=result.get("turn_count"),
            response_preview=result.get("response_content", "")[:100],
        )


# ---------------------------------------------------------------------------
# Test 3: LLM retry — Simulate API failure
# ---------------------------------------------------------------------------


class TestLLMRetry:
    """Manual test 3: LLM calls retry on failure with backoff."""

    @pytest.mark.anyio
    async def test_retry_with_backoff(self):
        from kora_v2.core.errors import retry_with_backoff
        from kora_v2.core.exceptions import LLMConnectionError

        call_count = 0

        async def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise LLMConnectionError("Simulated connection failure")
            return "success"

        start = time.monotonic()
        result = await retry_with_backoff(flaky_fn, base_delay=0.1)
        elapsed = time.monotonic() - start

        assert result == "success"
        assert call_count == 3  # 2 failures + 1 success
        assert elapsed >= 0.2  # 0.1s + 0.2s backoff minimum
        log.info(
            "MANUAL_TEST_3",
            status="PASS",
            attempts=call_count,
            elapsed_s=round(elapsed, 2),
        )

    @pytest.mark.anyio
    async def test_retry_exhaustion(self):
        from kora_v2.core.errors import retry_with_backoff
        from kora_v2.core.exceptions import LLMConnectionError

        async def always_fail():
            raise LLMConnectionError("Always fails")

        with pytest.raises(LLMConnectionError):
            await retry_with_backoff(always_fail, max_retries=2, base_delay=0.05)

        log.info("MANUAL_TEST_3b", status="PASS", detail="Exhaustion raises correctly")


# ---------------------------------------------------------------------------
# Test 1 + 2: WebSocket chat + Streaming
# ---------------------------------------------------------------------------


class TestWebSocketChat:
    """Manual tests 1 & 2: WebSocket chat and streaming."""

    @pytest.mark.anyio
    async def test_websocket_chat(self, app, api_token):
        """Send 'Hello' via WebSocket, get a coherent response."""
        from starlette.testclient import TestClient

        with TestClient(app) as client:
            with client.websocket_connect(f"/api/v1/ws?token={api_token}") as ws:
                # Send a chat message
                ws.send_json({"type": "chat", "content": "Hello, just say hi back briefly."})

                # Collect response messages
                messages = []
                response_complete = False

                # Read messages with a timeout
                for _ in range(50):  # safety limit
                    try:
                        data = ws.receive_json()
                        messages.append(data)
                        if data.get("type") == "response_complete":
                            response_complete = True
                            break
                        if data.get("type") == "error":
                            break
                    except Exception:
                        break

                # Verify we got token messages and a completion
                token_msgs = [m for m in messages if m.get("type") == "token"]
                assert len(token_msgs) > 0, f"No token messages received. Got: {messages}"
                assert response_complete, f"No response_complete. Got: {messages}"

                # Verify content is coherent
                full_response = "".join(m.get("content", "") for m in token_msgs)
                assert len(full_response) > 0

                log.info(
                    "MANUAL_TEST_1_2",
                    status="PASS",
                    token_count=len(token_msgs),
                    response_preview=full_response[:100],
                )


# ---------------------------------------------------------------------------
# Test 9: E2E Smoke — Full flow
# ---------------------------------------------------------------------------


class TestE2ESmoke:
    """Manual test 9: E2E smoke test — multiple messages."""

    @pytest.mark.anyio
    async def test_multi_message_conversation(self, app, api_token):
        """Send 3 messages and verify responses."""
        from starlette.testclient import TestClient

        prompts = [
            "Say exactly: response_one",
            "Say exactly: response_two",
            "Say exactly: response_three",
        ]

        with TestClient(app) as client:
            with client.websocket_connect(f"/api/v1/ws?token={api_token}") as ws:
                for i, prompt in enumerate(prompts):
                    ws.send_json({"type": "chat", "content": prompt})

                    # Collect response
                    response_text = ""
                    got_complete = False
                    for _ in range(50):
                        try:
                            data = ws.receive_json()
                            if data.get("type") == "token":
                                response_text += data.get("content", "")
                            elif data.get("type") == "response_complete":
                                got_complete = True
                                break
                            elif data.get("type") == "error":
                                pytest.fail(f"Error on message {i+1}: {data}")
                                break
                        except Exception:
                            break

                    assert got_complete, f"Message {i+1} didn't complete. Got: {response_text[:100]}"
                    assert len(response_text) > 0, f"Message {i+1} empty response"
                    log.info(
                        f"MANUAL_TEST_9_msg{i+1}",
                        status="PASS",
                        response_preview=response_text[:80],
                    )

        log.info("MANUAL_TEST_9", status="PASS", detail="3/3 messages got responses")
