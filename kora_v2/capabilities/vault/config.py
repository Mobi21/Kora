"""Vault capability config derived from VaultSettings."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class VaultCapabilityConfig:
    enabled: bool
    root: Path | None  # None when disabled or unconfigured
    clips_subdir: str = "Clips"  # relative to root
    notes_subdir: str = "Notes"  # relative to root


def from_settings(settings: object) -> VaultCapabilityConfig:
    """Build from top-level Settings.

    Rules:
    - If settings.vault.enabled is False → enabled=False, root=None
    - If vault.path is empty → enabled=False, root=None (effectively unconfigured)
    - Else → enabled=True, root=Path(vault.path).expanduser()
    """
    vault = settings.vault  # type: ignore[attr-defined]

    if not vault.enabled:
        return VaultCapabilityConfig(enabled=False, root=None)

    if not vault.path:
        return VaultCapabilityConfig(enabled=False, root=None)

    return VaultCapabilityConfig(
        enabled=True,
        root=Path(vault.path).expanduser(),
    )
