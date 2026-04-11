"""Tests for kora_v2/capabilities/vault/mirror.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from kora_v2.capabilities.vault.mirror import (
    FilesystemMirror,
    NullMirror,
    _render_frontmatter,
    _slugify,
)

pytestmark = pytest.mark.asyncio


# ── 1. NullMirror ─────────────────────────────────────────────────────────────


def test_null_mirror_is_not_enabled():
    assert NullMirror().is_enabled() is False


async def test_null_mirror_write_note_returns_failure():
    result = await NullMirror().write_note("foo.md", "content")
    assert result.success is False
    assert result.failure is not None
    assert result.failure.reason == "vault_disabled"
    assert result.failure.recoverable is True


async def test_null_mirror_write_clip_returns_failure():
    result = await NullMirror().write_clip(
        source_url="https://example.com",
        title="Test",
        content="body",
    )
    assert result.success is False
    assert result.failure is not None
    assert result.failure.reason == "vault_disabled"
    assert result.failure.recoverable is True


async def test_null_mirror_read_note_returns_failure():
    result = await NullMirror().read_note("foo.md")
    assert result.success is False
    assert result.failure is not None
    assert result.failure.reason == "vault_disabled"
    assert result.failure.recoverable is True


# ── 2. FilesystemMirror.write_note ────────────────────────────────────────────


async def test_filesystem_mirror_write_note_creates_file(tmp_path):
    mirror = FilesystemMirror(root=tmp_path)
    result = await mirror.write_note("my-note.md", "Hello world")
    assert result.success is True
    assert result.failure is None
    assert result.path is not None

    written = Path(result.path)
    assert written.exists()
    assert written.read_text() == "Hello world"
    # Should be under notes_subdir
    assert "Notes" in str(written)


async def test_filesystem_mirror_write_note_with_frontmatter(tmp_path):
    mirror = FilesystemMirror(root=tmp_path)
    metadata = {"author": "test", "tags": ["a", "b"]}
    result = await mirror.write_note("note-with-meta.md", "Body text", metadata)
    assert result.success is True
    content = Path(result.path).read_text()
    assert "---" in content
    assert "author" in content
    assert "Body text" in content


# ── 3. FilesystemMirror.write_clip ────────────────────────────────────────────


async def test_filesystem_mirror_write_clip_creates_under_clips_yyyy_mm(tmp_path):
    mirror = FilesystemMirror(root=tmp_path)
    result = await mirror.write_clip(
        source_url="https://example.com/article",
        title="My Great Article",
        content="Article body",
        metadata={"clipped_at": "2025-03-15T12:00:00+00:00"},
    )
    assert result.success is True
    assert result.path is not None

    written = Path(result.path)
    assert written.exists()
    # Check path segments include year/month
    parts = written.parts
    assert "2025" in parts
    assert "03" in parts
    assert "Clips" in parts


# ── 4. Slug generation: regular title ────────────────────────────────────────


def test_slugify_regular_title():
    assert _slugify("The Great Article!") == "the-great-article"


def test_slugify_mixed_case_spaces():
    assert _slugify("Hello World") == "hello-world"


def test_slugify_numbers_preserved():
    result = _slugify("Top 10 Tips")
    assert result == "top-10-tips"


def test_slugify_multiple_special_chars_collapsed():
    result = _slugify("foo -- bar && baz")
    assert result == "foo-bar-baz"


# ── 5. Slug generation: empty title ──────────────────────────────────────────


def test_slugify_empty_returns_untitled():
    assert _slugify("") == "untitled"


def test_slugify_only_special_chars_returns_untitled():
    assert _slugify("!!! ???") == "untitled"


# ── 6. Path traversal protection ──────────────────────────────────────────────


async def test_write_note_rejects_path_traversal(tmp_path):
    mirror = FilesystemMirror(root=tmp_path)
    result = await mirror.write_note("../../escape.md", "evil content")
    assert result.success is False
    assert result.failure is not None
    assert result.failure.reason == "unsafe_path"


async def test_write_note_rejects_absolute_path(tmp_path):
    mirror = FilesystemMirror(root=tmp_path)
    result = await mirror.write_note("/etc/passwd", "evil content")
    # /etc/passwd won't be under tmp_path
    assert result.success is False
    assert result.failure is not None


# ── 7. Frontmatter renders key fields ─────────────────────────────────────────


def test_render_frontmatter_contains_source_url():
    fm = _render_frontmatter({"source_url": "https://example.com", "title": "Test"})
    assert "source_url" in fm
    assert "https://example.com" in fm
    assert fm.startswith("---\n")
    assert "---" in fm[4:]  # closing delimiter


def test_render_frontmatter_contains_clipped_at():
    fm = _render_frontmatter({"clipped_at": "2025-01-01T00:00:00+00:00"})
    assert "clipped_at" in fm
    assert "2025-01-01" in fm


# ── 8. Frontmatter handles complex values without error ───────────────────────


def test_render_frontmatter_complex_values():
    metadata = {
        "title": "Complex",
        "tags": ["python", "testing"],
        "nested": {"key": "value", "num": 42},
        "score": 3.14,
    }
    fm = _render_frontmatter(metadata)
    assert "---" in fm
    assert "python" in fm
    assert "testing" in fm


# ── 9. Writing to non-existent nested directory creates parents ───────────────


async def test_write_note_creates_nested_directories(tmp_path):
    mirror = FilesystemMirror(root=tmp_path)
    result = await mirror.write_note("a/b/c/deep.md", "nested content")
    assert result.success is True
    written = Path(result.path)
    assert written.exists()
    assert written.read_text() == "nested content"


async def test_write_clip_creates_year_month_directories(tmp_path):
    mirror = FilesystemMirror(root=tmp_path)
    result = await mirror.write_clip(
        source_url="https://news.example.com",
        title="Breaking News",
        content="Story content",
        metadata={"clipped_at": "2024-06-01T08:00:00Z"},
    )
    assert result.success is True
    written = Path(result.path)
    assert written.exists()
    assert "2024" in str(written)
    assert "06" in str(written)
