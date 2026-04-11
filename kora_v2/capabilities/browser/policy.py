"""Browser policy: disclosed reads are fine, silent writes to personal Google are denied."""
from __future__ import annotations

from kora_v2.capabilities.policy import (
    ApprovalMode,
    PolicyKey,
    PolicyMatrix,
    PolicyRule,
)

_CAP = "browser"

GOOGLE_WRITE_ACTIONS = ("browser.click", "browser.type", "browser.fill")

# Read/navigation actions that need no approval regardless of URL.
_READ_ACTIONS = (
    "browser.open",
    "browser.snapshot",
    "browser.screenshot",
    "browser.clip_page",
    "browser.clip_selection",
    "browser.close",
)


def build_browser_policy() -> PolicyMatrix:
    """Default browser policy.

    - browser.open, browser.snapshot, browser.screenshot, browser.clip_page,
      browser.clip_selection, browser.close → NEVER_ASK (reads / navigation)
    - browser.click, browser.type, browser.fill on non-Google → NEVER_ASK
    - browser.click, browser.type, browser.fill on *.google.com/* → ALWAYS_ASK
      (resource-scoped rule, most-specific wins)

    The hard google-write restriction is also enforced by the action layer
    inspecting the session's current URL before dispatching.  The policy
    matrix records the intent via resource-scoped rules.
    """
    rules: list[PolicyRule] = []

    # Navigation / read actions — never ask
    for action in _READ_ACTIONS:
        rules.append(
            PolicyRule(
                key=PolicyKey(capability=_CAP, action=action),
                mode=ApprovalMode.NEVER_ASK,
                reason=f"'{action}' is a read/navigation operation; no approval needed.",
            )
        )

    # Write actions on non-Google URLs — never ask (default for the action, no resource)
    for action in GOOGLE_WRITE_ACTIONS:
        rules.append(
            PolicyRule(
                key=PolicyKey(capability=_CAP, action=action),
                mode=ApprovalMode.NEVER_ASK,
                reason=f"'{action}' on non-Google pages requires no approval.",
            )
        )

    # Write actions scoped to google.com — ALWAYS_ASK (more specific, wins)
    for action in GOOGLE_WRITE_ACTIONS:
        rules.append(
            PolicyRule(
                key=PolicyKey(
                    capability=_CAP,
                    action=action,
                    account=None,
                    resource="https://*.google.com/*",
                ),
                mode=ApprovalMode.ALWAYS_ASK,
                reason=(
                    f"'{action}' on a Google domain requires explicit approval "
                    "because it may modify your personal account."
                ),
            )
        )

    return PolicyMatrix(rules=rules, default=ApprovalMode.ALWAYS_ASK)
