"""Unit tests for kora_v2.tools.filesystem — real filesystem tools.

All file operations use pytest's tmp_path fixture.
Container is passed as None since filesystem tools don't need it.
"""

from __future__ import annotations

import json

import pytest

from kora_v2.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset ToolRegistry before/after each test to avoid cross-test pollution.

    After resetting, we re-register the filesystem tools by importing the module
    and calling the @tool-decorated functions through their existing module references.
    Python module import is cached, so we explicitly re-register via the module's
    register_all() helper or by accessing the decorated functions which self-register
    only at import time.  The cleanest approach: import once (module-level), reset
    before each test, then re-register by re-executing the decorator logic.
    """
    from kora_v2.tools import filesystem as fs_mod
    from kora_v2.tools.registry import ToolRegistry as _TR

    previous_tools = dict(_TR._tools)
    _TR.reset()

    # Re-register all 5 tools from the already-imported module objects.
    # The @tool decorator registers at import time, but _TR.reset() wipes the
    # registry.  We cannot re-run the decorator, so we call _TR.register()
    # directly using the metadata stored in each tool's __wrapped__ or via
    # a dedicated helper.
    _register_filesystem_tools(fs_mod)
    yield
    _TR._tools = previous_tools


def _register_filesystem_tools(fs_mod) -> None:
    """Re-register the 5 filesystem tools after a registry reset.

    Each @tool-decorated function is a plain coroutine — the decorator does
    NOT wrap the callable. We can call ToolRegistry.register() with the
    original input model classes and functions stored on the module.
    """
    from kora_v2.tools.registry import ToolRegistry
    from kora_v2.tools.types import AuthLevel, ToolCategory

    ToolRegistry.register(
        name="write_file",
        description="Create or overwrite a file with text content.",
        category=ToolCategory.FILESYSTEM,
        auth_level=AuthLevel.ASK_FIRST,
        func=fs_mod.write_file,
        input_model=fs_mod.WriteFileInput,
        is_read_only=False,
    )
    ToolRegistry.register(
        name="read_file",
        description="Read the text content of a file.",
        category=ToolCategory.FILESYSTEM,
        auth_level=AuthLevel.ALWAYS_ALLOWED,
        func=fs_mod.read_file,
        input_model=fs_mod.ReadFileInput,
        is_read_only=True,
    )
    ToolRegistry.register(
        name="create_directory",
        description="Create a directory (and any missing parents).",
        category=ToolCategory.FILESYSTEM,
        auth_level=AuthLevel.ASK_FIRST,
        func=fs_mod.create_directory,
        input_model=fs_mod.CreateDirectoryInput,
        is_read_only=False,
    )
    ToolRegistry.register(
        name="list_directory",
        description="List the immediate children of a directory.",
        category=ToolCategory.FILESYSTEM,
        auth_level=AuthLevel.ALWAYS_ALLOWED,
        func=fs_mod.list_directory,
        input_model=fs_mod.ListDirectoryInput,
        is_read_only=True,
    )
    ToolRegistry.register(
        name="file_exists",
        description="Check whether a path exists on disk.",
        category=ToolCategory.FILESYSTEM,
        auth_level=AuthLevel.ALWAYS_ALLOWED,
        func=fs_mod.file_exists,
        input_model=fs_mod.FileExistsInput,
        is_read_only=True,
    )


def _parse(result: str) -> dict:
    """Helper: parse a JSON result string."""
    return json.loads(result)


# ── write_file ────────────────────────────────────────────────────────────────


class TestWriteFile:
    """Tests for the write_file tool."""

    @pytest.mark.asyncio
    async def test_write_file_creates_and_reports_size(self, tmp_path):
        """write_file should create the file and return correct size."""
        from kora_v2.tools.filesystem import WriteFileInput, write_file

        target = tmp_path / "hello.txt"
        content = "Hello, Kora!"
        result = _parse(await write_file(WriteFileInput(path=str(target), content=content), None))

        assert result["success"] is True
        assert result["size_bytes"] == len(content.encode("utf-8"))
        assert target.exists()
        assert target.read_text() == content

    @pytest.mark.asyncio
    async def test_write_file_creates_parent_dirs(self, tmp_path):
        """write_file should create nested parent directories automatically."""
        from kora_v2.tools.filesystem import WriteFileInput, write_file

        target = tmp_path / "a" / "b" / "c" / "file.txt"
        result = _parse(await write_file(WriteFileInput(path=str(target), content="x"), None))

        assert result["success"] is True
        assert target.exists()

    @pytest.mark.asyncio
    async def test_write_file_blocked_path(self):
        """/etc/passwd must be blocked and return success=False."""
        from kora_v2.tools.filesystem import WriteFileInput, write_file

        result = _parse(await write_file(WriteFileInput(path="/etc/passwd", content="bad"), None))
        assert result["success"] is False
        assert "blocked" in result["error"].lower() or "invalid" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_write_file_blocked_sys(self):
        """/sys/kernel/kcore must be blocked."""
        from kora_v2.tools.filesystem import WriteFileInput, write_file

        result = _parse(await write_file(WriteFileInput(path="/sys/kernel/kcore", content=""), None))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_write_file_overwrites_existing(self, tmp_path):
        """write_file should overwrite an existing file."""
        from kora_v2.tools.filesystem import WriteFileInput, write_file

        target = tmp_path / "overwrite.txt"
        target.write_text("original")

        result = _parse(await write_file(WriteFileInput(path=str(target), content="updated"), None))
        assert result["success"] is True
        assert target.read_text() == "updated"

    @pytest.mark.asyncio
    async def test_write_file_empty_content(self, tmp_path):
        """write_file with empty content should succeed (0-byte file)."""
        from kora_v2.tools.filesystem import WriteFileInput, write_file

        target = tmp_path / "empty.txt"
        result = _parse(await write_file(WriteFileInput(path=str(target), content=""), None))
        assert result["success"] is True
        assert result["size_bytes"] == 0


# ── read_file ─────────────────────────────────────────────────────────────────


class TestReadFile:
    """Tests for the read_file tool."""

    @pytest.mark.asyncio
    async def test_read_file_returns_content(self, tmp_path):
        """read_file should return the file content."""
        from kora_v2.tools.filesystem import ReadFileInput, WriteFileInput, read_file, write_file

        target = tmp_path / "content.txt"
        await write_file(WriteFileInput(path=str(target), content="test content"), None)

        result = _parse(await read_file(ReadFileInput(path=str(target)), None))
        assert result["success"] is True
        assert result["content"] == "test content"

    @pytest.mark.asyncio
    async def test_read_file_missing(self, tmp_path):
        """read_file should return success=False for missing file."""
        from kora_v2.tools.filesystem import ReadFileInput, read_file

        result = _parse(await read_file(ReadFileInput(path=str(tmp_path / "missing.txt")), None))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_read_file_blocked_path(self):
        """/etc/hosts must be blocked."""
        from kora_v2.tools.filesystem import ReadFileInput, read_file

        result = _parse(await read_file(ReadFileInput(path="/etc/hosts"), None))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_read_file_directory_fails(self, tmp_path):
        """read_file on a directory should fail."""
        from kora_v2.tools.filesystem import ReadFileInput, read_file

        result = _parse(await read_file(ReadFileInput(path=str(tmp_path)), None))
        assert result["success"] is False
        assert "not a file" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_read_file_too_large(self, tmp_path):
        """Files > 1 MB should be refused."""
        from kora_v2.tools.filesystem import ReadFileInput, read_file

        target = tmp_path / "large.bin"
        # Write 1 MB + 1 byte
        target.write_bytes(b"x" * (1024 * 1024 + 1))
        result = _parse(await read_file(ReadFileInput(path=str(target)), None))
        assert result["success"] is False
        assert "too large" in result["error"].lower()


# ── file_exists ───────────────────────────────────────────────────────────────


class TestFileExists:
    """Tests for the file_exists tool."""

    @pytest.mark.asyncio
    async def test_file_exists_returns_file_type(self, tmp_path):
        """file_exists should return type='file' for an existing file."""
        from kora_v2.tools.filesystem import FileExistsInput, file_exists

        target = tmp_path / "exists.txt"
        target.write_text("hi")

        result = _parse(await file_exists(FileExistsInput(path=str(target)), None))
        assert result["success"] is True
        assert result["exists"] is True
        assert result["type"] == "file"

    @pytest.mark.asyncio
    async def test_file_exists_returns_directory_type(self, tmp_path):
        """file_exists should return type='directory' for a directory."""
        from kora_v2.tools.filesystem import FileExistsInput, file_exists

        result = _parse(await file_exists(FileExistsInput(path=str(tmp_path)), None))
        assert result["success"] is True
        assert result["exists"] is True
        assert result["type"] == "directory"

    @pytest.mark.asyncio
    async def test_file_exists_missing_returns_missing(self, tmp_path):
        """file_exists should return type='missing' for a nonexistent path."""
        from kora_v2.tools.filesystem import FileExistsInput, file_exists

        result = _parse(
            await file_exists(FileExistsInput(path=str(tmp_path / "nope.txt")), None)
        )
        assert result["success"] is True
        assert result["exists"] is False
        assert result["type"] == "missing"

    @pytest.mark.asyncio
    async def test_file_exists_blocked_path(self):
        """/proc/cpuinfo should be blocked."""
        from kora_v2.tools.filesystem import FileExistsInput, file_exists

        result = _parse(await file_exists(FileExistsInput(path="/proc/cpuinfo"), None))
        assert result["success"] is False


# ── list_directory ────────────────────────────────────────────────────────────


class TestListDirectory:
    """Tests for the list_directory tool."""

    @pytest.mark.asyncio
    async def test_list_directory_returns_children(self, tmp_path):
        """list_directory should list files and subdirs with correct types."""
        from kora_v2.tools.filesystem import ListDirectoryInput, list_directory

        (tmp_path / "file1.txt").write_text("a")
        (tmp_path / "file2.txt").write_text("bb")
        (tmp_path / "subdir").mkdir()

        result = _parse(await list_directory(ListDirectoryInput(path=str(tmp_path)), None))
        assert result["success"] is True
        assert result["count"] == 3

        names = {e["name"] for e in result["entries"]}
        assert "file1.txt" in names
        assert "file2.txt" in names
        assert "subdir" in names

        types = {e["name"]: e["type"] for e in result["entries"]}
        assert types["subdir"] == "directory"
        assert types["file1.txt"] == "file"

    @pytest.mark.asyncio
    async def test_list_directory_missing(self, tmp_path):
        """list_directory on a nonexistent path returns success=False."""
        from kora_v2.tools.filesystem import ListDirectoryInput, list_directory

        result = _parse(
            await list_directory(ListDirectoryInput(path=str(tmp_path / "no_dir")), None)
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_list_directory_on_file_fails(self, tmp_path):
        """list_directory on a file should fail."""
        from kora_v2.tools.filesystem import ListDirectoryInput, list_directory

        f = tmp_path / "f.txt"
        f.write_text("x")
        result = _parse(await list_directory(ListDirectoryInput(path=str(f)), None))
        assert result["success"] is False
        assert "not a directory" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_list_empty_directory(self, tmp_path):
        """list_directory on an empty directory should return empty list."""
        from kora_v2.tools.filesystem import ListDirectoryInput, list_directory

        empty = tmp_path / "empty"
        empty.mkdir()
        result = _parse(await list_directory(ListDirectoryInput(path=str(empty)), None))
        assert result["success"] is True
        assert result["count"] == 0
        assert result["entries"] == []


# ── create_directory ─────────────────────────────────────────────────────────


class TestCreateDirectory:
    """Tests for the create_directory tool."""

    @pytest.mark.asyncio
    async def test_create_directory_nested(self, tmp_path):
        """create_directory should create nested directories."""
        from kora_v2.tools.filesystem import CreateDirectoryInput, create_directory

        target = tmp_path / "a" / "b" / "c"
        result = _parse(await create_directory(CreateDirectoryInput(path=str(target)), None))
        assert result["success"] is True
        assert target.is_dir()

    @pytest.mark.asyncio
    async def test_create_directory_idempotent(self, tmp_path):
        """create_directory on an existing directory should succeed silently."""
        from kora_v2.tools.filesystem import CreateDirectoryInput, create_directory

        target = tmp_path / "exists"
        target.mkdir()

        result = _parse(await create_directory(CreateDirectoryInput(path=str(target)), None))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_create_directory_blocked_path(self):
        """/etc/newdir should be blocked."""
        from kora_v2.tools.filesystem import CreateDirectoryInput, create_directory

        result = _parse(
            await create_directory(CreateDirectoryInput(path="/etc/newdir"), None)
        )
        assert result["success"] is False


# ── Registry integration ──────────────────────────────────────────────────────


class TestFilesystemToolsRegistered:
    """Verify all 5 tools are present in the ToolRegistry after import."""

    def test_all_tools_registered(self):
        """All 5 filesystem tools should be in the registry."""
        from kora_v2.tools.types import ToolCategory

        names = set(ToolRegistry.tool_names())
        for expected in {"write_file", "read_file", "create_directory", "list_directory", "file_exists"}:
            assert expected in names, f"Missing tool: {expected}"

        fs_tools = ToolRegistry.get_by_category(ToolCategory.FILESYSTEM)
        assert len(fs_tools) == 5

    def test_write_file_auth_level(self):
        """write_file should require ASK_FIRST."""
        from kora_v2.tools.types import AuthLevel

        defn = ToolRegistry.get_definition("write_file")
        assert defn is not None
        assert defn.auth_level == AuthLevel.ASK_FIRST

    def test_read_tools_always_allowed(self):
        """read_file, file_exists, list_directory should be ALWAYS_ALLOWED."""
        from kora_v2.tools.types import AuthLevel

        for name in ("read_file", "file_exists", "list_directory"):
            defn = ToolRegistry.get_definition(name)
            assert defn is not None
            assert defn.auth_level == AuthLevel.ALWAYS_ALLOWED, (
                f"{name} should be ALWAYS_ALLOWED"
            )
