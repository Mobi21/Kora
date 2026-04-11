"""Browser capability pack — headless browser automation via agent-browser."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kora_v2.capabilities.base import Action, CapabilityHealth, CapabilityPack, HealthStatus
from kora_v2.capabilities.browser.actions import (
    BrowserActionContext,
    browser_click,
    browser_clip_page,
    browser_clip_selection,
    browser_close,
    browser_fill,
    browser_open,
    browser_screenshot,
    browser_snapshot,
    browser_type,
)
from kora_v2.capabilities.browser.binary import BrowserBinary
from kora_v2.capabilities.browser.config import BrowserCapabilityConfig, from_settings
from kora_v2.capabilities.browser.health import check_browser_health
from kora_v2.capabilities.browser.policy import build_browser_policy
from kora_v2.capabilities.policy import PolicyMatrix, SessionState, TaskState
from kora_v2.capabilities.registry import ActionRegistry

if TYPE_CHECKING:
    from kora_v2.core.settings import Settings


# ── Action metadata table ─────────────────────────────────────────────────────
# (action_name, description, read_only, requires_approval)
_ACTION_METADATA: list[tuple[str, str, bool, bool]] = [
    # Navigation / reads — never ask
    ("browser.open",            "Open a new browser session at a URL",             True,  False),
    ("browser.snapshot",        "Take a DOM snapshot of the current page",          True,  False),
    ("browser.screenshot",      "Take a screenshot of the current page",            True,  False),
    ("browser.clip_page",       "Clip the full page content to structured data",    True,  False),
    ("browser.clip_selection",  "Clip a specific element to structured data",       True,  False),
    ("browser.close",           "Close the browser session",                        True,  False),
    # Writes — may require approval on Google domains (enforced in action layer)
    ("browser.click",           "Click an element in the browser",                  False, False),
    ("browser.type",            "Type text into an element in the browser",         False, False),
    ("browser.fill",            "Fill an element with a value in the browser",      False, False),
]

# Map full action names → callable coroutine functions
_ACTION_HANDLERS: dict[str, Any] = {
    "browser.open":           browser_open,
    "browser.snapshot":       browser_snapshot,
    "browser.screenshot":     browser_screenshot,
    "browser.clip_page":      browser_clip_page,
    "browser.clip_selection": browser_clip_selection,
    "browser.close":          browser_close,
    "browser.click":          browser_click,
    "browser.type":           browser_type,
    "browser.fill":           browser_fill,
}


class BrowserCapability(CapabilityPack):
    """Headless browser automation via the agent-browser CLI."""

    name = "browser"
    description = "Headless browser automation for web research and interaction."

    def __init__(self) -> None:
        self._config: BrowserCapabilityConfig | None = None
        self._policy: PolicyMatrix = build_browser_policy()
        self._settings: Settings | None = None

    def bind(self, settings: Settings, **kwargs: Any) -> None:
        """Late-bind runtime dependencies. Called by the DI container.

        Accepts **kwargs so that callers passing mcp_manager= (or other
        capability-specific deps) do not cause a TypeError.
        """
        self._settings = settings
        self._config = from_settings(settings)

    async def health_check(self) -> CapabilityHealth:
        if self._config is None:
            return CapabilityHealth(
                status=HealthStatus.UNCONFIGURED,
                summary="Browser capability not bound to runtime yet.",
                remediation="Container must call .bind(settings) first.",
            )
        return await check_browser_health(self._config)

    def register_actions(self, registry: ActionRegistry) -> None:
        """Register one Action per browser action into the registry."""
        cap_instance = self

        for full_name, description, read_only, requires_approval in _ACTION_METADATA:
            handler_fn = _ACTION_HANDLERS.get(full_name)

            def _make_handler(fn: Any, cap: BrowserCapability) -> Any:
                async def _handler(
                    session: SessionState,
                    task: TaskState | None = None,
                    **call_kwargs: Any,
                ) -> Any:
                    ctx = cap.make_context(session=session, task=task)
                    return await fn(ctx, **call_kwargs)

                return _handler

            action = Action(
                name=full_name,
                description=description,
                capability=self.name,
                input_schema={"type": "object", "properties": {}},
                requires_approval=requires_approval,
                read_only=read_only,
                handler=_make_handler(handler_fn, cap_instance) if handler_fn else None,
            )
            registry.register(action)

    def get_policy(self) -> PolicyMatrix:
        return self._policy

    def make_context(
        self,
        session: SessionState,
        task: TaskState | None = None,
    ) -> BrowserActionContext:
        """Build a BrowserActionContext for the current session/task."""
        if self._config is None:
            raise RuntimeError(
                "BrowserCapability not bound — call .bind(settings) first."
            )
        binary = BrowserBinary(
            binary_path=self._config.binary_path,
            profile=self._config.profile,
            command_timeout_seconds=self._config.command_timeout_seconds,
        )
        return BrowserActionContext(
            config=self._config,
            policy=self._policy,
            binary=binary,
            session=session,
            task=task,
        )
