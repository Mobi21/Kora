"""Tests for kora_v2/capabilities/browser/__init__.py (BrowserCapability pack)."""
from __future__ import annotations

import pytest

from kora_v2.capabilities.base import HealthStatus
from kora_v2.capabilities.browser import BrowserCapability
from kora_v2.capabilities.browser.actions import _is_google_domain
from kora_v2.capabilities.registry import ActionRegistry
from tests.fixtures.fake_agent_browser import make_fake_binary

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_settings(enabled: bool, binary_path: str = "") -> object:
    """Return a minimal settings-like object for BrowserCapability.bind()."""
    class _Browser:
        def __init__(self):
            self.enabled = enabled
            self.binary_path = binary_path
            self.default_profile = ""
            self.clip_target = "vault"
            self.max_session_duration_seconds = 3600
            self.command_timeout_seconds = 10

    class _Settings:
        browser = _Browser()

    return _Settings()


# ── 1. settings.browser.enabled=False → UNCONFIGURED ─────────────────────────


async def test_health_check_disabled_returns_unconfigured():
    cap = BrowserCapability()
    cap.bind(_make_settings(enabled=False))
    health = await cap.health_check()
    assert health.status == HealthStatus.UNCONFIGURED
    assert health.remediation is not None


# ── 2. Unbound capability → UNCONFIGURED ──────────────────────────────────────


async def test_health_check_unbound_returns_unconfigured():
    cap = BrowserCapability()
    health = await cap.health_check()
    assert health.status == HealthStatus.UNCONFIGURED


# ── 3. With a fake binary → OK health ────────────────────────────────────────


async def test_health_check_with_fake_binary_returns_ok(tmp_path):
    binary = make_fake_binary(tmp_path)
    cap = BrowserCapability()
    cap.bind(_make_settings(enabled=True, binary_path=str(binary)))
    health = await cap.health_check()
    assert health.status == HealthStatus.OK
    assert "0.0.1" in health.summary or "agent-browser" in health.summary.lower()
    assert health.details.get("version") is not None


# ── 4. register_actions populates ≥9 actions ─────────────────────────────────


def test_register_actions_populates_nine_or_more(tmp_path):
    binary = make_fake_binary(tmp_path)
    cap = BrowserCapability()
    cap.bind(_make_settings(enabled=True, binary_path=str(binary)))
    registry = ActionRegistry()
    cap.register_actions(registry)
    actions = registry.get_by_capability("browser")
    assert len(actions) >= 9
    names = {a.name for a in actions}
    expected = {
        "browser.open",
        "browser.snapshot",
        "browser.screenshot",
        "browser.clip_page",
        "browser.clip_selection",
        "browser.close",
        "browser.click",
        "browser.type",
        "browser.fill",
    }
    assert expected.issubset(names), f"Missing actions: {expected - names}"


# ── 5. _is_google_domain covers expected hosts ────────────────────────────────


def test_is_google_domain_google_com():
    assert _is_google_domain("https://google.com/") is True


def test_is_google_domain_mail():
    assert _is_google_domain("https://mail.google.com/mail/u/0") is True


def test_is_google_domain_docs():
    assert _is_google_domain("https://docs.google.com/document/d/abc") is True


def test_is_google_domain_google_co_uk():
    assert _is_google_domain("https://google.co.uk/search") is True


def test_is_google_domain_non_google():
    assert _is_google_domain("https://bing.com/search") is False


def test_is_google_domain_non_google_2():
    assert _is_google_domain("https://example.com/") is False


# ── 6. get_policy returns a PolicyMatrix ─────────────────────────────────────


def test_get_policy_returns_policy_matrix():
    from kora_v2.capabilities.policy import PolicyMatrix
    cap = BrowserCapability()
    policy = cap.get_policy()
    assert isinstance(policy, PolicyMatrix)


# ── 7. bind() is tolerant of extra kwargs (DI wiring) ────────────────────────


def test_bind_accepts_extra_kwargs(tmp_path):
    """bind() must not raise when mcp_manager= is passed (DI container pattern)."""
    binary = make_fake_binary(tmp_path)
    cap = BrowserCapability()
    # Should not raise even with extra keyword args
    cap.bind(settings=_make_settings(enabled=True, binary_path=str(binary)), mcp_manager=None)
    assert cap._config is not None
