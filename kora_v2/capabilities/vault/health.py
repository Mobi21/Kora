"""Vault health checks."""
from __future__ import annotations

import os

from kora_v2.capabilities.base import CapabilityHealth, HealthStatus
from kora_v2.capabilities.vault.config import VaultCapabilityConfig


async def check_vault_health(config: VaultCapabilityConfig) -> CapabilityHealth:
    """Return vault capability health.

    - config.enabled=False → UNCONFIGURED with remediation="Set KORA_VAULT__PATH"
    - root doesn't exist → UNCONFIGURED with remediation="Create the directory"
    - root exists but not writable → UNHEALTHY with detail
    - root writable → OK with path and subdir layout in details
    """
    if not config.enabled or config.root is None:
        return CapabilityHealth(
            status=HealthStatus.UNCONFIGURED,
            summary="Vault capability is not configured.",
            remediation=(
                "Set KORA_VAULT__PATH to your vault directory path, "
                "or add vault.path to ~/.kora/settings.toml."
            ),
        )

    root = config.root

    if not root.exists():
        return CapabilityHealth(
            status=HealthStatus.UNCONFIGURED,
            summary=f"Vault root directory does not exist: {root}",
            remediation=f"Create the directory: mkdir -p '{root}'",
            details={"path": str(root)},
        )

    # Check writability by testing os.access
    if not os.access(root, os.W_OK):
        return CapabilityHealth(
            status=HealthStatus.UNHEALTHY,
            summary=f"Vault root directory is not writable: {root}",
            remediation=f"Check permissions on '{root}' and ensure the current user can write to it.",
            details={"path": str(root)},
        )

    clips_path = root / config.clips_subdir
    notes_path = root / config.notes_subdir

    return CapabilityHealth(
        status=HealthStatus.OK,
        summary=f"Vault is configured and writable at {root}.",
        details={
            "root": str(root),
            "clips_dir": str(clips_path),
            "notes_dir": str(notes_path),
            "clips_subdir": config.clips_subdir,
            "notes_subdir": config.notes_subdir,
        },
    )
