"""Browser health checks."""
from __future__ import annotations

from kora_v2.capabilities.base import CapabilityHealth, HealthStatus
from kora_v2.capabilities.browser.binary import BrowserBinary
from kora_v2.capabilities.browser.config import BrowserCapabilityConfig


async def check_browser_health(config: BrowserCapabilityConfig) -> CapabilityHealth:
    """Return browser capability health.

    - config.enabled=False → UNCONFIGURED with remediation
    - binary_path resolves → try ``--version``:
      - success → OK with version in details
      - failure → UNHEALTHY with the error
    - binary_path missing → UNCONFIGURED with remediation
    """
    if not config.enabled:
        return CapabilityHealth(
            status=HealthStatus.UNCONFIGURED,
            summary="Browser capability is disabled in settings.",
            remediation=(
                "Set browser.enabled = true in ~/.kora/settings.toml "
                "and ensure agent-browser is installed."
            ),
        )

    binary = BrowserBinary(
        binary_path=config.binary_path,
        profile=config.profile,
        command_timeout_seconds=config.command_timeout_seconds,
    )

    resolved = binary.resolve_binary()
    if resolved is None:
        return CapabilityHealth(
            status=HealthStatus.UNCONFIGURED,
            summary="agent-browser binary not found.",
            remediation=(
                "Install agent-browser (npx @vercel/agent-browser) and set "
                "browser.binary_path in ~/.kora/settings.toml, "
                "or ensure it is on your PATH."
            ),
        )

    version = await binary.version()
    if version is None:
        return CapabilityHealth(
            status=HealthStatus.UNHEALTHY,
            summary="agent-browser binary was found but failed to return a version string.",
            details={"binary": resolved},
            remediation=(
                "Run 'agent-browser --version' manually to diagnose the error."
            ),
        )

    return CapabilityHealth(
        status=HealthStatus.OK,
        summary=f"agent-browser is available and healthy ({version.strip()}).",
        details={"binary": resolved, "version": version.strip()},
    )
