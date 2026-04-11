"""Browser capability actions."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from kora_v2.capabilities.base import StructuredFailure
from kora_v2.capabilities.browser.binary import BrowserBinary, BrowserCommandError
from kora_v2.capabilities.browser.config import BrowserCapabilityConfig
from kora_v2.capabilities.policy import PolicyMatrix, SessionState, TaskState

_CAP = "browser"
_DOCS_SETUP = "docs/phase9/setup.md"

_BINARY_NOT_FOUND_FAILURE = dict(
    capability=_CAP,
    action="",
    path="browser.binary",
    reason="binary_not_found",
    user_message=(
        "The agent-browser binary is not installed or not configured. "
        f"See {_DOCS_SETUP} for setup instructions."
    ),
    recoverable=False,
)

# Google-owned TLD base domains (checked against the registered domain)
_GOOGLE_DOMAINS = frozenset(
    [
        "google.com",
        "google.co.uk",
        "google.ca",
        "google.com.au",
        "google.de",
        "google.fr",
        "google.co.jp",
        "google.co.in",
        "google.com.br",
        "google.es",
        "google.it",
        "google.nl",
        "google.pl",
        "google.ru",
        "google.com.mx",
        "google.com.ar",
        "google.co.za",
        "google.com.tr",
        "google.com.hk",
        "google.com.sg",
        "googleapis.com",
        "googleusercontent.com",
        "youtube.com",
        "gmail.com",
    ]
)


def _is_google_domain(url: str) -> bool:
    """Return True if the URL's host is a Google-owned domain."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        return False
    host = host.lower()
    # Strip leading "www." for comparison
    if host.startswith("www."):
        host = host[4:]
    # Direct match
    if host in _GOOGLE_DOMAINS:
        return True
    # Subdomain match: *.google.com, *.googleapis.com, etc.
    for domain in _GOOGLE_DOMAINS:
        if host.endswith("." + domain):
            return True
    return False


@dataclass
class BrowserSession:
    """In-memory state for an open browser session."""

    id: str  # agent-browser's session id
    current_url: str
    opened_at: float
    profile: str
    last_snapshot: dict[str, Any] | None = None


@dataclass
class BrowserActionContext:
    config: BrowserCapabilityConfig
    policy: PolicyMatrix
    binary: BrowserBinary
    session: SessionState
    task: TaskState | None = None
    # In-memory session tracking (session_id → BrowserSession)
    open_sessions: dict[str, BrowserSession] = field(default_factory=dict)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _make_binary(config: BrowserCapabilityConfig) -> BrowserBinary:
    return BrowserBinary(
        binary_path=config.binary_path,
        profile=config.profile,
        command_timeout_seconds=config.command_timeout_seconds,
    )


def _binary_not_found(action: str) -> StructuredFailure:
    return StructuredFailure(
        **{**_BINARY_NOT_FOUND_FAILURE, "action": action},
    )


def _command_failure(action: str, exc: BrowserCommandError) -> StructuredFailure:
    return StructuredFailure(
        capability=_CAP,
        action=action,
        path=f"browser.binary.{action}",
        reason=f"command_error:{exc.reason}",
        user_message=(
            f"The browser command for '{action}' failed "
            f"(exit_code={exc.exit_code}, reason={exc.reason}). "
            f"stderr: {exc.stderr[:200]}"
        ),
        recoverable=exc.reason in ("timeout", "non_zero_exit"),
        machine_details={
            "exit_code": exc.exit_code,
            "reason": exc.reason,
            "stderr": exc.stderr,
            "argv": exc.argv,
        },
    )


def _google_write_denied(action: str) -> StructuredFailure:
    return StructuredFailure(
        capability=_CAP,
        action=action,
        path=f"browser.binary.{action}",
        reason="google_write_requires_approval",
        user_message=(
            "I can only interact with Google pages in the browser after explicit "
            "approval because it may modify your personal account."
        ),
        recoverable=True,
    )


# ── Actions ───────────────────────────────────────────────────────────────────


async def browser_open(
    ctx: BrowserActionContext,
    url: str,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Open a new browser session at *url*.

    Navigation is always allowed regardless of URL.
    """
    if ctx.binary.resolve_binary() is None:
        return _binary_not_found("browser.open")

    try:
        result = await ctx.binary.session_open(url)
    except BrowserCommandError as exc:
        return _command_failure("browser.open", exc)

    session_id = result.get("session_id", "")
    current_url = result.get("url", url)
    ctx.open_sessions[session_id] = BrowserSession(
        id=session_id,
        current_url=current_url,
        opened_at=time.time(),
        profile=ctx.config.profile,
    )
    return result


async def browser_snapshot(
    ctx: BrowserActionContext,
    session_id: str,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Take a DOM snapshot of the session."""
    if ctx.binary.resolve_binary() is None:
        return _binary_not_found("browser.snapshot")

    try:
        result = await ctx.binary.session_snapshot(session_id)
    except BrowserCommandError as exc:
        return _command_failure("browser.snapshot", exc)

    # Update last_snapshot
    if session_id in ctx.open_sessions:
        ctx.open_sessions[session_id].last_snapshot = result

    return result


async def browser_click(
    ctx: BrowserActionContext,
    session_id: str,
    ref: str,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Click element *ref* in the session."""
    if ctx.binary.resolve_binary() is None:
        return _binary_not_found("browser.click")

    # Check google-write restriction
    browser_session = ctx.open_sessions.get(session_id)
    if browser_session is not None and _is_google_domain(browser_session.current_url):
        if not approved:
            return _google_write_denied("browser.click")

    try:
        return await ctx.binary.session_click(session_id, ref)
    except BrowserCommandError as exc:
        return _command_failure("browser.click", exc)


async def browser_type(
    ctx: BrowserActionContext,
    session_id: str,
    ref: str,
    text: str,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Type *text* into element *ref* in the session."""
    if ctx.binary.resolve_binary() is None:
        return _binary_not_found("browser.type")

    browser_session = ctx.open_sessions.get(session_id)
    if browser_session is not None and _is_google_domain(browser_session.current_url):
        if not approved:
            return _google_write_denied("browser.type")

    try:
        return await ctx.binary.session_type(session_id, ref, text)
    except BrowserCommandError as exc:
        return _command_failure("browser.type", exc)


async def browser_fill(
    ctx: BrowserActionContext,
    session_id: str,
    ref: str,
    value: str,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Fill element *ref* with *value* in the session."""
    if ctx.binary.resolve_binary() is None:
        return _binary_not_found("browser.fill")

    browser_session = ctx.open_sessions.get(session_id)
    if browser_session is not None and _is_google_domain(browser_session.current_url):
        if not approved:
            return _google_write_denied("browser.fill")

    try:
        return await ctx.binary.session_fill(session_id, ref, value)
    except BrowserCommandError as exc:
        return _command_failure("browser.fill", exc)


async def browser_screenshot(
    ctx: BrowserActionContext,
    session_id: str,
    out_path: str | None = None,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Take a screenshot of the session and save it to *out_path*."""
    if ctx.binary.resolve_binary() is None:
        return _binary_not_found("browser.screenshot")

    import tempfile

    if out_path is None:
        out_path = str(
            tempfile.mktemp(suffix=".png", prefix="kora_browser_")
        )

    try:
        return await ctx.binary.session_screenshot(session_id, out_path)
    except BrowserCommandError as exc:
        return _command_failure("browser.screenshot", exc)


async def browser_clip_page(
    ctx: BrowserActionContext,
    session_id: str,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Clip the full page content of the session.

    Returns structured data: {url, title, text, html, clipped_at}.
    Clipping is a read operation; no approval needed regardless of URL.
    """
    if ctx.binary.resolve_binary() is None:
        return _binary_not_found("browser.clip_page")

    try:
        snapshot = await ctx.binary.session_snapshot(session_id)
    except BrowserCommandError as exc:
        return _command_failure("browser.clip_page", exc)

    browser_session = ctx.open_sessions.get(session_id)
    current_url = browser_session.current_url if browser_session else ""

    return {
        "url": snapshot.get("url", current_url),
        "title": snapshot.get("title", ""),
        "text": snapshot.get("text", ""),
        "html": snapshot.get("html", ""),
        "clipped_at": datetime.now(tz=UTC).isoformat(),
    }


async def browser_clip_selection(
    ctx: BrowserActionContext,
    session_id: str,
    ref: str,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Clip a specific element *ref* from the session.

    Returns structured data: {url, title, text, html, ref, clipped_at}.
    Clipping is a read operation; no approval needed regardless of URL.
    """
    if ctx.binary.resolve_binary() is None:
        return _binary_not_found("browser.clip_selection")

    try:
        snapshot = await ctx.binary.session_snapshot(session_id)
    except BrowserCommandError as exc:
        return _command_failure("browser.clip_selection", exc)

    browser_session = ctx.open_sessions.get(session_id)
    current_url = browser_session.current_url if browser_session else ""

    # Find the element in the snapshot's refs list
    refs = snapshot.get("refs", [])
    element = next((r for r in refs if r.get("ref") == ref), {})

    return {
        "url": snapshot.get("url", current_url),
        "title": snapshot.get("title", ""),
        "text": element.get("text", snapshot.get("text", "")),
        "html": element.get("html", ""),
        "ref": ref,
        "clipped_at": datetime.now(tz=UTC).isoformat(),
    }


async def browser_close(
    ctx: BrowserActionContext,
    session_id: str,
    *,
    approved: bool = False,
) -> dict[str, Any] | StructuredFailure:
    """Close the browser session."""
    if ctx.binary.resolve_binary() is None:
        return _binary_not_found("browser.close")

    try:
        result = await ctx.binary.session_close(session_id)
    except BrowserCommandError as exc:
        return _command_failure("browser.close", exc)

    # Remove from in-memory tracking
    ctx.open_sessions.pop(session_id, None)
    return result
