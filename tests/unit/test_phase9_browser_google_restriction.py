"""Phase 9 browser Google-domain restriction regression tests.

Verifies that:
- browser_click with approved=False on a Google session returns StructuredFailure
  with reason containing "google"
- browser_click with approved=True on a Google session calls the binary
- browser_click with approved=False on a non-Google session proceeds
- Parameterized: all key Google domains must trigger approval gate
"""
from __future__ import annotations

import pytest

from kora_v2.capabilities.base import StructuredFailure
from kora_v2.capabilities.browser.actions import (
    BrowserActionContext,
    BrowserSession,
    _is_google_domain,
    browser_click,
    browser_open,
)
from kora_v2.capabilities.browser.binary import BrowserBinary
from kora_v2.capabilities.browser.config import BrowserCapabilityConfig
from kora_v2.capabilities.browser.policy import build_browser_policy
from kora_v2.capabilities.policy import SessionState
from tests.fixtures.fake_agent_browser import make_fake_binary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(binary_path: str) -> BrowserCapabilityConfig:
    return BrowserCapabilityConfig(
        binary_path=binary_path,
        profile="",
        clip_target="vault",
        max_session_duration_seconds=3600,
        command_timeout_seconds=10,
        enabled=True,
    )


def _make_ctx(
    binary_path: str,
    current_url: str,
    session_id: str = "test-session",
) -> BrowserActionContext:
    config = _make_config(binary_path)
    binary = BrowserBinary(
        binary_path=binary_path,
        command_timeout_seconds=10,
    )
    open_sessions = {
        session_id: BrowserSession(
            id=session_id,
            current_url=current_url,
            opened_at=0.0,
            profile="",
        )
    }
    return BrowserActionContext(
        config=config,
        policy=build_browser_policy(),
        binary=binary,
        session=SessionState(session_id="kora-session"),
        task=None,
        open_sessions=open_sessions,
    )


# ---------------------------------------------------------------------------
# 1. browser_open on mail.google.com is always allowed (navigation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_open_gmail_is_allowed(tmp_path) -> None:
    """browser_open is always allowed regardless of domain."""
    binary = make_fake_binary(tmp_path)
    config = _make_config(str(binary))
    bin_obj = BrowserBinary(binary_path=str(binary), command_timeout_seconds=10)
    ctx = BrowserActionContext(
        config=config,
        policy=build_browser_policy(),
        binary=bin_obj,
        session=SessionState(session_id="s"),
        task=None,
    )
    result = await browser_open(ctx, "https://mail.google.com/")
    assert not isinstance(result, StructuredFailure), (
        f"browser_open should never be blocked, got: {result}"
    )


# ---------------------------------------------------------------------------
# 2. browser_click with approved=False on Google session → StructuredFailure
#    with reason containing "google"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_click_gmail_unapproved_returns_failure(tmp_path) -> None:
    binary = make_fake_binary(tmp_path)
    ctx = _make_ctx(str(binary), "https://mail.google.com/mail/u/0")
    result = await browser_click(ctx, "test-session", "ref-1", approved=False)
    assert isinstance(result, StructuredFailure), (
        f"Expected StructuredFailure for unapproved Google click, got: {result}"
    )
    assert "google" in result.reason.lower(), (
        f"StructuredFailure reason should mention 'google', got: {result.reason!r}"
    )


# ---------------------------------------------------------------------------
# 3. browser_click with approved=True on Google session calls the binary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_click_gmail_approved_calls_binary(tmp_path) -> None:
    binary = make_fake_binary(tmp_path)
    ctx = _make_ctx(str(binary), "https://mail.google.com/mail/u/0")
    result = await browser_click(ctx, "test-session", "ref-1", approved=True)
    assert not isinstance(result, StructuredFailure), (
        f"Expected success for approved Google click, got: {result}"
    )
    assert result.get("ok") is True


# ---------------------------------------------------------------------------
# 4. browser_click on example.com with approved=False proceeds (no Google check)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_click_non_google_unapproved_proceeds(tmp_path) -> None:
    binary = make_fake_binary(tmp_path)
    ctx = _make_ctx(str(binary), "https://example.com/")
    result = await browser_click(ctx, "test-session", "ref-1", approved=False)
    assert not isinstance(result, StructuredFailure), (
        f"Non-Google click should proceed unapproved, got: {result}"
    )
    assert result.get("ok") is True


# ---------------------------------------------------------------------------
# 5. All key Google domains must trigger the approval gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("google_url", [
    "https://mail.google.com/mail/u/0",
    "https://docs.google.com/document/d/abc",
    "https://calendar.google.com/calendar/u/0",
    "https://drive.google.com/drive/my-drive",
    "https://accounts.google.com/signin",
])
async def test_all_google_domains_trigger_approval(tmp_path, google_url: str) -> None:
    """Every Google domain URL must trigger the approval gate for write actions."""
    binary = make_fake_binary(tmp_path)
    ctx = _make_ctx(str(binary), google_url)
    result = await browser_click(ctx, "test-session", "ref-1", approved=False)
    assert isinstance(result, StructuredFailure), (
        f"Expected StructuredFailure for {google_url} (unapproved), got: {result}"
    )
    assert "google" in result.reason.lower(), (
        f"reason should mention 'google' for {google_url}, got: {result.reason!r}"
    )


# ---------------------------------------------------------------------------
# 6. _is_google_domain correctness (spot checks) — sync tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url,expected", [
    ("https://mail.google.com/", True),
    ("https://docs.google.com/", True),
    ("https://calendar.google.com/", True),
    ("https://drive.google.com/", True),
    ("https://accounts.google.com/", True),
    ("https://example.com/", False),
    ("https://notgoogle.com/", False),
    ("", False),
], ids=[
    "mail.google.com",
    "docs.google.com",
    "calendar.google.com",
    "drive.google.com",
    "accounts.google.com",
    "example.com",
    "notgoogle.com",
    "empty",
])
def test_is_google_domain_parameterized(url: str, expected: bool) -> None:
    assert _is_google_domain(url) == expected, (
        f"_is_google_domain({url!r}) should be {expected}"
    )
