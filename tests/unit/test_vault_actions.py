"""Tests for kora_v2/capabilities/vault/actions.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from kora_v2.capabilities.policy import SessionState
from kora_v2.capabilities.vault.actions import (
    VaultActionContext,
    vault_read_note,
    vault_write_clip,
    vault_write_note,
)
from kora_v2.capabilities.vault.config import VaultCapabilityConfig
from kora_v2.capabilities.vault.mirror import FilesystemMirror, NullMirror
from kora_v2.capabilities.vault.policy import build_vault_policy

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ctx(mirror, root=None) -> VaultActionContext:
    if root is not None:
        config = VaultCapabilityConfig(enabled=True, root=Path(root))
    else:
        config = VaultCapabilityConfig(enabled=False, root=None)
    return VaultActionContext(
        config=config,
        policy=build_vault_policy(),
        target=mirror,
        session=SessionState(session_id="test-session"),
    )


# ── 1. vault_write_note routes to mirror ─────────────────────────────────────


async def test_vault_write_note_routes_to_mirror(tmp_path):
    mirror = FilesystemMirror(root=tmp_path)
    ctx = _make_ctx(mirror, root=tmp_path)
    result = await vault_write_note(ctx, "my-note.md", "Hello from action")
    assert result.success is True
    assert result.path is not None
    written = Path(result.path)
    assert written.exists()
    assert "Hello from action" in written.read_text()


# ── 2. vault_write_clip produces file with source_url in frontmatter ──────────


async def test_vault_write_clip_source_url_in_frontmatter(tmp_path):
    mirror = FilesystemMirror(root=tmp_path)
    ctx = _make_ctx(mirror, root=tmp_path)
    result = await vault_write_clip(
        ctx,
        source_url="https://example.com/test-article",
        title="Test Article",
        content="Clipped content here",
        metadata={"clipped_at": "2025-04-10T10:00:00Z"},
    )
    assert result.success is True
    assert result.path is not None
    written = Path(result.path)
    assert written.exists()
    text = written.read_text()
    assert "https://example.com/test-article" in text
    assert "source_url" in text


# ── 3. NullMirror path: every action returns WriteResult(success=False, failure.recoverable=True) ──


async def test_null_mirror_write_note_returns_recoverable_failure():
    ctx = _make_ctx(NullMirror())
    result = await vault_write_note(ctx, "foo.md", "content")
    assert result.success is False
    assert result.failure is not None
    assert result.failure.recoverable is True


async def test_null_mirror_write_clip_returns_recoverable_failure():
    ctx = _make_ctx(NullMirror())
    result = await vault_write_clip(
        ctx,
        source_url="https://example.com",
        title="Test",
        content="body",
    )
    assert result.success is False
    assert result.failure is not None
    assert result.failure.recoverable is True


async def test_null_mirror_read_note_returns_recoverable_failure():
    ctx = _make_ctx(NullMirror())
    result = await vault_read_note(ctx, "foo.md")
    assert result.success is False
    assert result.failure is not None
    assert result.failure.recoverable is True


# ── 4. vault_read_note of a file you just wrote returns the content ────────────


async def test_vault_read_note_returns_written_content(tmp_path):
    mirror = FilesystemMirror(root=tmp_path)
    ctx = _make_ctx(mirror, root=tmp_path)

    write_result = await vault_write_note(ctx, "roundtrip.md", "Round-trip content")
    assert write_result.success is True

    # Read relative path (without the notes_subdir prefix — that's handled by the mirror)
    read_result = await vault_read_note(ctx, "roundtrip.md")
    assert read_result.success is True
    assert read_result.content is not None
    assert "Round-trip content" in read_result.content


# ── 5. vault_write_note with metadata produces frontmatter ───────────────────


async def test_vault_write_note_metadata_in_file(tmp_path):
    mirror = FilesystemMirror(root=tmp_path)
    ctx = _make_ctx(mirror, root=tmp_path)
    result = await vault_write_note(
        ctx,
        "meta-note.md",
        "Content with metadata",
        metadata={"title": "My Note", "tags": ["important", "work"]},
    )
    assert result.success is True
    text = Path(result.path).read_text()
    assert "---" in text
    assert "My Note" in text
    assert "important" in text


# ── 6. vault_write_clip slug from title ───────────────────────────────────────


async def test_vault_write_clip_slug_from_title(tmp_path):
    mirror = FilesystemMirror(root=tmp_path)
    ctx = _make_ctx(mirror, root=tmp_path)
    result = await vault_write_clip(
        ctx,
        source_url="https://example.com",
        title="Python Is Awesome!!!",
        content="body",
        metadata={"clipped_at": "2025-01-15T00:00:00Z"},
    )
    assert result.success is True
    # Slug should be filesystem-safe
    assert "python-is-awesome" in result.path
