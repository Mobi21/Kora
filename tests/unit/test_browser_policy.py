"""Tests for kora_v2/capabilities/browser/policy.py."""
from __future__ import annotations

import pytest

from kora_v2.capabilities.browser.policy import build_browser_policy
from kora_v2.capabilities.policy import ApprovalMode, PolicyKey, SessionState

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def policy():
    return build_browser_policy()


@pytest.fixture()
def session():
    return SessionState(session_id="test-session")


# ── 1. Non-Google URLs → click/type/fill are NEVER_ASK ───────────────────────


def test_click_on_non_google_url_is_never_ask(policy, session):
    key = PolicyKey(
        capability="browser",
        action="browser.click",
        account=None,
        resource="https://example.com/",
    )
    decision = policy.evaluate(key, session=session)
    # The general rule (no resource) should match with NEVER_ASK
    # The resource-scoped google rule won't match because resource differs
    assert decision.allowed
    assert decision.mode == ApprovalMode.NEVER_ASK


def test_type_on_non_google_url_is_never_ask(policy, session):
    key = PolicyKey(
        capability="browser",
        action="browser.type",
        account=None,
        resource=None,
    )
    decision = policy.evaluate(key, session=session)
    assert decision.allowed
    assert decision.mode == ApprovalMode.NEVER_ASK


def test_fill_on_non_google_url_is_never_ask(policy, session):
    key = PolicyKey(
        capability="browser",
        action="browser.fill",
        account=None,
        resource=None,
    )
    decision = policy.evaluate(key, session=session)
    assert decision.allowed
    assert decision.mode == ApprovalMode.NEVER_ASK


# ── 2. google.com URLs → click/type/fill resolve to ALWAYS_ASK ───────────────


def test_click_on_google_url_is_always_ask(policy, session):
    key = PolicyKey(
        capability="browser",
        action="browser.click",
        account=None,
        resource="https://*.google.com/*",
    )
    decision = policy.evaluate(key, session=session)
    assert decision.allowed
    assert decision.mode == ApprovalMode.ALWAYS_ASK


def test_type_on_google_url_is_always_ask(policy, session):
    key = PolicyKey(
        capability="browser",
        action="browser.type",
        account=None,
        resource="https://*.google.com/*",
    )
    decision = policy.evaluate(key, session=session)
    assert decision.allowed
    assert decision.mode == ApprovalMode.ALWAYS_ASK


def test_fill_on_google_url_is_always_ask(policy, session):
    key = PolicyKey(
        capability="browser",
        action="browser.fill",
        account=None,
        resource="https://*.google.com/*",
    )
    decision = policy.evaluate(key, session=session)
    assert decision.allowed
    assert decision.mode == ApprovalMode.ALWAYS_ASK


# ── 3. browser.open is always NEVER_ASK ──────────────────────────────────────


def test_browser_open_is_never_ask(policy, session):
    key = PolicyKey(capability="browser", action="browser.open")
    decision = policy.evaluate(key, session=session)
    assert decision.allowed
    assert decision.mode == ApprovalMode.NEVER_ASK


def test_browser_open_google_url_is_never_ask(policy, session):
    """Opening a Google URL is still allowed — we need it to reach Google for reads."""
    key = PolicyKey(capability="browser", action="browser.open", resource="https://google.com/")
    decision = policy.evaluate(key, session=session)
    assert decision.allowed
    assert decision.mode == ApprovalMode.NEVER_ASK


# ── 4. browser.clip_page is always NEVER_ASK ─────────────────────────────────


def test_browser_clip_page_is_never_ask(policy, session):
    key = PolicyKey(capability="browser", action="browser.clip_page")
    decision = policy.evaluate(key, session=session)
    assert decision.allowed
    assert decision.mode == ApprovalMode.NEVER_ASK


def test_browser_clip_selection_is_never_ask(policy, session):
    key = PolicyKey(capability="browser", action="browser.clip_selection")
    decision = policy.evaluate(key, session=session)
    assert decision.allowed
    assert decision.mode == ApprovalMode.NEVER_ASK


def test_browser_snapshot_is_never_ask(policy, session):
    key = PolicyKey(capability="browser", action="browser.snapshot")
    decision = policy.evaluate(key, session=session)
    assert decision.allowed
    assert decision.mode == ApprovalMode.NEVER_ASK


def test_browser_screenshot_is_never_ask(policy, session):
    key = PolicyKey(capability="browser", action="browser.screenshot")
    decision = policy.evaluate(key, session=session)
    assert decision.allowed
    assert decision.mode == ApprovalMode.NEVER_ASK


def test_browser_close_is_never_ask(policy, session):
    key = PolicyKey(capability="browser", action="browser.close")
    decision = policy.evaluate(key, session=session)
    assert decision.allowed
    assert decision.mode == ApprovalMode.NEVER_ASK
