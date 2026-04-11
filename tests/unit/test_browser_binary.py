"""Tests for kora_v2/capabilities/browser/binary.py."""
from __future__ import annotations

import shutil

import pytest

from kora_v2.capabilities.browser.binary import BrowserBinary, BrowserCommandError
from tests.fixtures.fake_agent_browser import (
    make_failing_binary,
    make_fake_binary,
    make_slow_binary,
)

pytestmark = pytest.mark.asyncio


# ── 1. resolve_binary returns the path when configured ────────────────────────


def test_resolve_binary_returns_configured_path(tmp_path):
    binary = make_fake_binary(tmp_path)
    bb = BrowserBinary(binary_path=str(binary))
    result = bb.resolve_binary()
    assert result == str(binary)


# ── 2. resolve_binary falls back to PATH when path is empty ───────────────────


def test_resolve_binary_falls_back_to_path(monkeypatch):
    """If binary_path is empty, shutil.which should be called."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/agent-browser")
    bb = BrowserBinary(binary_path="")
    result = bb.resolve_binary()
    assert result == "/usr/local/bin/agent-browser"


# ── 3. resolve_binary returns None when neither works ─────────────────────────


def test_resolve_binary_returns_none_when_not_found(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    bb = BrowserBinary(binary_path="")
    result = bb.resolve_binary()
    assert result is None


def test_resolve_binary_returns_none_for_nonexistent_configured_path():
    bb = BrowserBinary(binary_path="/no/such/path/agent-browser")
    result = bb.resolve_binary()
    assert result is None


# ── 4. version() returns a version string from the fake binary ────────────────


async def test_version_returns_string(tmp_path):
    binary = make_fake_binary(tmp_path)
    bb = BrowserBinary(binary_path=str(binary), command_timeout_seconds=10)
    version = await bb.version()
    assert version is not None
    assert "agent-browser" in version.lower() or "0.0.1" in version


# ── 5. session_open returns dict with session_id and url ─────────────────────


async def test_session_open_returns_dict(tmp_path):
    binary = make_fake_binary(tmp_path)
    bb = BrowserBinary(binary_path=str(binary), command_timeout_seconds=10)
    result = await bb.session_open("https://example.com")
    assert isinstance(result, dict)
    assert result["session_id"] == "fake-session-1"
    assert "example.com" in result["url"]


# ── 6. session_snapshot returns dict ─────────────────────────────────────────


async def test_session_snapshot_returns_dict(tmp_path):
    binary = make_fake_binary(tmp_path)
    bb = BrowserBinary(binary_path=str(binary), command_timeout_seconds=10)
    result = await bb.session_snapshot("fake-session-1")
    assert isinstance(result, dict)
    assert "snapshot_id" in result or "url" in result


# ── 7. Timeout raises BrowserCommandError with reason="timeout" ───────────────


async def test_timeout_raises_browser_command_error(tmp_path):
    binary = make_slow_binary(tmp_path)
    bb = BrowserBinary(
        binary_path=str(binary),
        command_timeout_seconds=1,
    )
    # Override the version template to use the direct binary which sleeps forever
    with pytest.raises(BrowserCommandError) as exc_info:
        # We call _run directly to use the slow binary for any command
        await bb._run(["--version"], {})
    assert exc_info.value.reason == "timeout"


# ── 8. Non-zero exit raises BrowserCommandError ───────────────────────────────


async def test_nonzero_exit_raises_browser_command_error(tmp_path):
    binary = make_failing_binary(tmp_path)
    bb = BrowserBinary(binary_path=str(binary), command_timeout_seconds=10)
    with pytest.raises(BrowserCommandError) as exc_info:
        await bb._run(["--version"], {})
    err = exc_info.value
    assert err.reason == "non_zero_exit"
    assert err.exit_code == 1
    assert "simulated error" in err.stderr


# ── 9. BrowserCommandError raised when binary is missing ─────────────────────


async def test_run_raises_when_binary_missing(monkeypatch):
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)
    bb = BrowserBinary(binary_path="")
    with pytest.raises(BrowserCommandError) as exc_info:
        await bb._run(["--version"], {})
    assert exc_info.value.reason == "binary_not_found"
