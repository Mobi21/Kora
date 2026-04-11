"""Browser capability config mirror."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BrowserCapabilityConfig:
    """Capability-level config derived from BrowserSettings."""

    binary_path: str
    profile: str
    clip_target: str
    max_session_duration_seconds: int
    command_timeout_seconds: int
    enabled: bool


def from_settings(settings: object) -> BrowserCapabilityConfig:
    """Build from the top-level Settings object."""
    browser = settings.browser  # type: ignore[attr-defined]
    return BrowserCapabilityConfig(
        binary_path=browser.binary_path,
        profile=browser.default_profile,
        clip_target=browser.clip_target,
        max_session_duration_seconds=browser.max_session_duration_seconds,
        command_timeout_seconds=browser.command_timeout_seconds,
        enabled=browser.enabled,
    )
