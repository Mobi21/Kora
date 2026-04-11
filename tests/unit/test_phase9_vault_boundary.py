"""Phase 9 vault boundary tests.

Verifies:
- VaultCapability with vault disabled still registers 3 actions
- Disabled vault actions route to NullMirror and return WriteResult with vault_disabled reason
- Real filesystem mirror writes clips with proper frontmatter (source_url, clipped_at)
  but NO visible "Kora" marker in the body
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kora_v2.capabilities.policy import SessionState
from kora_v2.capabilities.registry import ActionRegistry
from kora_v2.capabilities.vault import VaultCapability
from kora_v2.capabilities.vault.config import VaultCapabilityConfig
from kora_v2.capabilities.vault.mirror import FilesystemMirror, NullMirror, WriteResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_disabled_settings() -> object:
    """Settings with vault disabled."""

    class _Vault:
        enabled = False
        path = ""
        clips_subdir = "Clips"
        notes_subdir = "Notes"

    class _Settings:
        vault = _Vault()

    return _Settings()


def _make_enabled_settings(vault_path: str) -> object:
    """Settings with vault enabled at given path."""

    class _Vault:
        enabled = True
        path = vault_path
        clips_subdir = "Clips"
        notes_subdir = "Notes"

    class _Settings:
        vault = _Vault()

    return _Settings()


def _make_session() -> SessionState:
    return SessionState(session_id="vault-test-session")


# ---------------------------------------------------------------------------
# 1. Disabled vault: register_actions still populates 3 actions
# ---------------------------------------------------------------------------


def test_disabled_vault_registers_3_actions() -> None:
    cap = VaultCapability()
    cap.bind(settings=_make_disabled_settings())
    registry = ActionRegistry()
    cap.register_actions(registry)
    actions = registry.get_by_capability("vault")
    assert len(actions) >= 3, (
        f"Disabled vault must still register >= 3 actions, got {len(actions)}"
    )


def test_disabled_vault_registers_expected_action_names() -> None:
    cap = VaultCapability()
    cap.bind(settings=_make_disabled_settings())
    registry = ActionRegistry()
    cap.register_actions(registry)
    names = {a.name for a in registry.get_by_capability("vault")}
    assert {"vault.write_note", "vault.write_clip", "vault.read_note"} == names, (
        f"Expected exactly the 3 vault action names, got: {names}"
    )


# ---------------------------------------------------------------------------
# 2. Disabled vault actions → NullMirror returns WriteResult with vault_disabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_vault_write_note_returns_vault_disabled() -> None:
    from kora_v2.capabilities.vault.actions import VaultActionContext, vault_write_note
    from kora_v2.capabilities.vault.policy import build_vault_policy

    config = VaultCapabilityConfig(enabled=False, root=None)
    target = NullMirror()
    ctx = VaultActionContext(
        config=config,
        policy=build_vault_policy(),
        target=target,
        session=_make_session(),
    )
    result = await vault_write_note(ctx, "test.md", "content")
    assert isinstance(result, WriteResult)
    assert result.success is False
    assert result.failure is not None
    assert result.failure.reason == "vault_disabled", (
        f"Expected reason='vault_disabled', got: {result.failure.reason!r}"
    )


@pytest.mark.asyncio
async def test_disabled_vault_write_clip_returns_vault_disabled() -> None:
    from kora_v2.capabilities.vault.actions import VaultActionContext, vault_write_clip
    from kora_v2.capabilities.vault.policy import build_vault_policy

    config = VaultCapabilityConfig(enabled=False, root=None)
    target = NullMirror()
    ctx = VaultActionContext(
        config=config,
        policy=build_vault_policy(),
        target=target,
        session=_make_session(),
    )
    result = await vault_write_clip(
        ctx,
        source_url="https://example.com/article",
        title="Test Article",
        content="Article content",
    )
    assert isinstance(result, WriteResult)
    assert result.success is False
    assert result.failure is not None
    assert result.failure.reason == "vault_disabled"


@pytest.mark.asyncio
async def test_disabled_vault_read_note_returns_vault_disabled() -> None:
    from kora_v2.capabilities.vault.actions import VaultActionContext, vault_read_note
    from kora_v2.capabilities.vault.policy import build_vault_policy

    config = VaultCapabilityConfig(enabled=False, root=None)
    target = NullMirror()
    ctx = VaultActionContext(
        config=config,
        policy=build_vault_policy(),
        target=target,
        session=_make_session(),
    )
    result = await vault_read_note(ctx, "nonexistent.md")
    assert isinstance(result, WriteResult)
    assert result.success is False
    assert result.failure is not None
    assert result.failure.reason == "vault_disabled"


# ---------------------------------------------------------------------------
# 3. Real filesystem mirror: write_clip produces frontmatter with source_url
#    and clipped_at, but NO visible Kora marker in the body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filesystem_mirror_write_clip_has_source_url_frontmatter(
    tmp_path: Path,
) -> None:
    """write_clip must write a file with source_url in YAML frontmatter."""
    mirror = FilesystemMirror(root=tmp_path)
    result = await mirror.write_clip(
        source_url="https://example.com/article",
        title="Test Article",
        content="The article body text.",
    )
    assert result.success, f"write_clip failed: {result.failure}"
    assert result.path is not None

    file_path = Path(result.path)
    assert file_path.exists()
    content = file_path.read_text()

    assert "source_url" in content, (
        "Clip file must have source_url in frontmatter"
    )
    assert "https://example.com/article" in content


@pytest.mark.asyncio
async def test_filesystem_mirror_write_clip_has_clipped_at_frontmatter(
    tmp_path: Path,
) -> None:
    """write_clip must write a file with clipped_at in YAML frontmatter."""
    mirror = FilesystemMirror(root=tmp_path)
    result = await mirror.write_clip(
        source_url="https://example.com/",
        title="Another Article",
        content="Body text.",
    )
    assert result.success
    content = Path(result.path).read_text()
    assert "clipped_at" in content, "Clip file must have clipped_at in frontmatter"


@pytest.mark.asyncio
async def test_filesystem_mirror_write_clip_no_kora_marker_in_body(
    tmp_path: Path,
) -> None:
    """write_clip must NOT include any visible Kora attribution in the body."""
    mirror = FilesystemMirror(root=tmp_path)
    body = "Plain article content without any markers."
    result = await mirror.write_clip(
        source_url="https://example.com/article",
        title="Clean Article",
        content=body,
    )
    assert result.success
    file_text = Path(result.path).read_text()

    # The body portion (after the frontmatter closing ---)
    # Extract body: find the second "---" and take everything after it
    parts = file_text.split("---\n", 2)
    body_section = parts[2] if len(parts) > 2 else file_text

    # No visible Kora marker should appear in the body
    assert "[Created by Kora]" not in body_section, (
        "write_clip must NOT inject [Created by Kora] in the body"
    )
    # The original body content should be preserved intact
    assert body in body_section, "Original body content must be preserved in the file"


@pytest.mark.asyncio
async def test_filesystem_mirror_write_clip_frontmatter_is_valid_yaml(
    tmp_path: Path,
) -> None:
    """The frontmatter must be parseable YAML."""
    import yaml

    mirror = FilesystemMirror(root=tmp_path)
    result = await mirror.write_clip(
        source_url="https://example.com/",
        title="YAML Test",
        content="Body.",
        metadata={"custom_key": "custom_value"},
    )
    assert result.success
    text = Path(result.path).read_text()

    # Extract YAML frontmatter (between first --- and second ---)
    lines = text.splitlines()
    if lines and lines[0] == "---":
        end_idx = next(
            (i for i, ln in enumerate(lines[1:], 1) if ln == "---"), None
        )
        if end_idx is not None:
            fm_text = "\n".join(lines[1:end_idx])
            parsed = yaml.safe_load(fm_text)
            assert isinstance(parsed, dict)
            assert "source_url" in parsed
            assert "clipped_at" in parsed
