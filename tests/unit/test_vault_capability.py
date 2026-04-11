"""Tests for kora_v2/capabilities/vault/__init__.py (VaultCapability pack)."""
from __future__ import annotations

import os
import stat
import sys

import pytest

from kora_v2.capabilities.base import HealthStatus
from kora_v2.capabilities.policy import PolicyMatrix
from kora_v2.capabilities.registry import ActionRegistry
from kora_v2.capabilities.vault import VaultCapability

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_settings(enabled: bool, path: str) -> object:
    """Return a minimal settings-like object for VaultCapability.bind()."""

    class _Vault:
        def __init__(self):
            self.enabled = enabled
            self.path = path

    class _Settings:
        vault = _Vault()

    return _Settings()


# ── 1. enabled=False → UNCONFIGURED ──────────────────────────────────────────


async def test_health_disabled_returns_unconfigured():
    cap = VaultCapability()
    cap.bind(settings=_make_settings(enabled=False, path="/some/path"))
    health = await cap.health_check()
    assert health.status == HealthStatus.UNCONFIGURED


# ── 2. enabled=True but path="" → UNCONFIGURED ───────────────────────────────


async def test_health_enabled_empty_path_returns_unconfigured():
    cap = VaultCapability()
    cap.bind(settings=_make_settings(enabled=True, path=""))
    health = await cap.health_check()
    assert health.status == HealthStatus.UNCONFIGURED


# ── 3. Real tmp_path → OK ─────────────────────────────────────────────────────


async def test_health_real_path_returns_ok(tmp_path):
    cap = VaultCapability()
    cap.bind(settings=_make_settings(enabled=True, path=str(tmp_path)))
    health = await cap.health_check()
    assert health.status == HealthStatus.OK
    assert str(tmp_path) in health.details.get("root", "")


# ── 4. Non-writable path → UNHEALTHY ─────────────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32" or os.getuid() == 0,
    reason="Cannot reliably test non-writable dirs on Windows or as root",
)
async def test_health_non_writable_path_returns_unhealthy(tmp_path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    # Remove write permission
    vault_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        cap = VaultCapability()
        cap.bind(settings=_make_settings(enabled=True, path=str(vault_dir)))
        health = await cap.health_check()
        assert health.status == HealthStatus.UNHEALTHY
    finally:
        # Restore permissions so tmp_path cleanup works
        vault_dir.chmod(stat.S_IRWXU)


# ── 5. register_actions populates ≥3 actions ─────────────────────────────────


def test_register_actions_populates_at_least_three(tmp_path):
    cap = VaultCapability()
    cap.bind(settings=_make_settings(enabled=True, path=str(tmp_path)))
    registry = ActionRegistry()
    cap.register_actions(registry)
    actions = registry.get_by_capability("vault")
    assert len(actions) >= 3
    names = {a.name for a in actions}
    expected = {"vault.write_note", "vault.write_clip", "vault.read_note"}
    assert expected.issubset(names), f"Missing actions: {expected - names}"


# ── 6. make_context without bind raises ───────────────────────────────────────


def test_make_context_without_bind_raises():
    from kora_v2.capabilities.policy import SessionState

    cap = VaultCapability()
    session = SessionState(session_id="test-session")
    with pytest.raises(RuntimeError, match="not bound"):
        cap.make_context(session=session)


# ── 7. get_policy returns PolicyMatrix ───────────────────────────────────────


def test_get_policy_returns_policy_matrix():
    cap = VaultCapability()
    policy = cap.get_policy()
    assert isinstance(policy, PolicyMatrix)


# ── 8. bind accepts extra kwargs (DI wiring tolerance) ───────────────────────


def test_bind_accepts_extra_kwargs(tmp_path):
    cap = VaultCapability()
    # Should not raise with mcp_manager keyword
    cap.bind(settings=_make_settings(enabled=True, path=str(tmp_path)), mcp_manager=None)
    assert cap._config is not None
    assert cap._target is not None
