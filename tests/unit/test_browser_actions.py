"""Tests for kora_v2/capabilities/browser/actions.py."""
from __future__ import annotations

import pytest

from kora_v2.capabilities.base import StructuredFailure
from kora_v2.capabilities.browser.actions import (
    BrowserActionContext,
    BrowserSession,
    _is_google_domain,
    browser_click,
    browser_clip_page,
    browser_open,
    browser_snapshot,
)
from kora_v2.capabilities.browser.binary import BrowserBinary
from kora_v2.capabilities.browser.config import BrowserCapabilityConfig
from kora_v2.capabilities.browser.policy import build_browser_policy
from kora_v2.capabilities.policy import SessionState
from tests.fixtures.fake_agent_browser import make_fake_binary

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_config(binary_path: str) -> BrowserCapabilityConfig:
    return BrowserCapabilityConfig(
        binary_path=binary_path,
        profile="",
        clip_target="vault",
        max_session_duration_seconds=3600,
        command_timeout_seconds=10,
        enabled=True,
    )


def _make_ctx(binary_path: str, open_sessions=None) -> BrowserActionContext:
    config = _make_config(binary_path)
    binary = BrowserBinary(
        binary_path=binary_path,
        command_timeout_seconds=10,
    )
    ctx = BrowserActionContext(
        config=config,
        policy=build_browser_policy(),
        binary=binary,
        session=SessionState(session_id="test-session"),
        task=None,
        open_sessions=open_sessions or {},
    )
    return ctx


# ── 1. browser_open opens a session and records it in ctx.open_sessions ───────


async def test_browser_open_records_session(tmp_path):
    binary = make_fake_binary(tmp_path)
    ctx = _make_ctx(str(binary))

    result = await browser_open(ctx, "https://example.com")
    assert not isinstance(result, StructuredFailure)
    assert result["session_id"] == "fake-session-1"
    assert "fake-session-1" in ctx.open_sessions
    recorded = ctx.open_sessions["fake-session-1"]
    assert "example.com" in recorded.current_url


# ── 2. browser_click on google.com session with approved=False returns StructuredFailure


async def test_browser_click_google_unapproved_returns_failure(tmp_path):
    binary = make_fake_binary(tmp_path)
    open_sessions = {
        "gs-1": BrowserSession(
            id="gs-1",
            current_url="https://mail.google.com/mail",
            opened_at=0.0,
            profile="",
        )
    }
    ctx = _make_ctx(str(binary), open_sessions=open_sessions)

    result = await browser_click(ctx, "gs-1", "ref-1", approved=False)
    assert isinstance(result, StructuredFailure)
    assert result.reason == "google_write_requires_approval"
    assert result.recoverable is True


# ── 3. browser_click on google.com with approved=True calls the binary ─────────


async def test_browser_click_google_approved_proceeds(tmp_path):
    binary = make_fake_binary(tmp_path)
    open_sessions = {
        "gs-1": BrowserSession(
            id="gs-1",
            current_url="https://mail.google.com/mail",
            opened_at=0.0,
            profile="",
        )
    }
    ctx = _make_ctx(str(binary), open_sessions=open_sessions)

    result = await browser_click(ctx, "gs-1", "ref-1", approved=True)
    assert not isinstance(result, StructuredFailure), f"Got failure: {result}"
    assert result.get("ok") is True


# ── 4. browser_click on a non-google session with approved=False proceeds ──────


async def test_browser_click_non_google_unapproved_proceeds(tmp_path):
    binary = make_fake_binary(tmp_path)
    open_sessions = {
        "es-1": BrowserSession(
            id="es-1",
            current_url="https://example.com/page",
            opened_at=0.0,
            profile="",
        )
    }
    ctx = _make_ctx(str(binary), open_sessions=open_sessions)

    result = await browser_click(ctx, "es-1", "ref-1", approved=False)
    assert not isinstance(result, StructuredFailure), f"Got failure: {result}"
    assert result.get("ok") is True


# ── 5. browser_clip_page returns structured {"url", "title", "text", "clipped_at"}


async def test_browser_clip_page_returns_structured_data(tmp_path):
    binary = make_fake_binary(tmp_path)
    open_sessions = {
        "s-1": BrowserSession(
            id="s-1",
            current_url="https://example.com/",
            opened_at=0.0,
            profile="",
        )
    }
    ctx = _make_ctx(str(binary), open_sessions=open_sessions)

    result = await browser_clip_page(ctx, "s-1")
    assert not isinstance(result, StructuredFailure)
    assert "url" in result
    assert "title" in result
    assert "text" in result
    assert "clipped_at" in result
    # clipped_at should be an ISO timestamp
    assert "T" in result["clipped_at"]


# ── 6. Binary missing → StructuredFailure with reason="binary_not_found" ──────


async def test_browser_open_binary_missing_returns_failure(monkeypatch):
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)
    ctx = _make_ctx(binary_path="")
    result = await browser_open(ctx, "https://example.com")
    assert isinstance(result, StructuredFailure)
    assert result.reason == "binary_not_found"
    assert result.recoverable is False


async def test_browser_click_binary_missing_returns_failure(monkeypatch):
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)
    ctx = _make_ctx(binary_path="")
    result = await browser_click(ctx, "s-1", "ref-1")
    assert isinstance(result, StructuredFailure)
    assert result.reason == "binary_not_found"


# ── 7. Binary command error → StructuredFailure, not raised ───────────────────


async def test_browser_open_command_error_returns_failure(tmp_path):
    from tests.fixtures.fake_agent_browser import make_failing_binary

    binary = make_failing_binary(tmp_path)
    ctx = _make_ctx(str(binary))
    result = await browser_open(ctx, "https://example.com")
    assert isinstance(result, StructuredFailure)
    assert "command_error" in result.reason


async def test_browser_snapshot_command_error_returns_failure(tmp_path):
    from tests.fixtures.fake_agent_browser import make_failing_binary

    binary = make_failing_binary(tmp_path)
    open_sessions = {
        "s-1": BrowserSession(id="s-1", current_url="https://example.com/", opened_at=0.0, profile="")
    }
    ctx = _make_ctx(str(binary), open_sessions=open_sessions)
    result = await browser_snapshot(ctx, "s-1")
    assert isinstance(result, StructuredFailure)


# ── 8. _is_google_domain correctness ─────────────────────────────────────────


def test_is_google_domain_google_com():
    assert _is_google_domain("https://google.com/") is True


def test_is_google_domain_mail_google_com():
    assert _is_google_domain("https://mail.google.com/mail") is True


def test_is_google_domain_docs_google_com():
    assert _is_google_domain("https://docs.google.com/document/d/123") is True


def test_is_google_domain_google_co_uk():
    assert _is_google_domain("https://google.co.uk/search?q=test") is True


def test_is_google_domain_rejects_non_google():
    assert _is_google_domain("https://example.com/") is False


def test_is_google_domain_rejects_not_google_lookalike():
    assert _is_google_domain("https://notgoogle.com/") is False


def test_is_google_domain_rejects_empty():
    assert _is_google_domain("") is False


def test_is_google_domain_youtube():
    assert _is_google_domain("https://www.youtube.com/watch?v=abc") is True
