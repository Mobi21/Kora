"""MirrorTarget abstraction.

Two concrete targets:
  - FilesystemMirror: writes files under the configured vault root
  - NullMirror: no-op; used when vault is disabled or unconfigured
"""
from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from kora_v2.capabilities.base import StructuredFailure

_CAP = "vault"


@dataclass
class WriteResult:
    success: bool
    path: str | None       # absolute path when successful
    failure: StructuredFailure | None
    content: str | None = None  # populated on read_note success


class MirrorTarget(ABC):
    @abstractmethod
    def is_enabled(self) -> bool: ...

    @abstractmethod
    async def write_note(
        self,
        relative_path: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> WriteResult: ...

    @abstractmethod
    async def write_clip(
        self,
        *,
        source_url: str,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> WriteResult: ...

    @abstractmethod
    async def read_note(self, relative_path: str) -> WriteResult: ...


class NullMirror(MirrorTarget):
    def is_enabled(self) -> bool:
        return False

    async def write_note(
        self,
        relative_path: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> WriteResult:
        return WriteResult(
            success=False,
            path=None,
            failure=StructuredFailure(
                capability=_CAP,
                action="vault.write_note",
                path="vault.null",
                reason="vault_disabled",
                user_message="Vault is not configured; note was not mirrored.",
                recoverable=True,
            ),
        )

    async def write_clip(
        self,
        *,
        source_url: str,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> WriteResult:
        return WriteResult(
            success=False,
            path=None,
            failure=StructuredFailure(
                capability=_CAP,
                action="vault.write_clip",
                path="vault.null",
                reason="vault_disabled",
                user_message="Vault is not configured; clip was not mirrored.",
                recoverable=True,
            ),
        )

    async def read_note(self, relative_path: str) -> WriteResult:
        return WriteResult(
            success=False,
            path=None,
            failure=StructuredFailure(
                capability=_CAP,
                action="vault.read_note",
                path="vault.null",
                reason="vault_disabled",
                user_message="Vault is not configured; note could not be read.",
                recoverable=True,
            ),
        )


class FilesystemMirror(MirrorTarget):
    def __init__(
        self,
        root: Path,
        clips_subdir: str = "Clips",
        notes_subdir: str = "Notes",
    ) -> None:
        self._root = root
        self._clips_subdir = clips_subdir
        self._notes_subdir = notes_subdir

    def is_enabled(self) -> bool:
        return True

    async def write_note(
        self,
        relative_path: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> WriteResult:
        """Write a note under {root}/{notes_subdir}/{relative_path}.

        - Rejects relative_path that escapes the notes subdir (no ../ traversal)
        - Creates parent directories as needed
        - If metadata is provided, render as YAML frontmatter at the top of the file
        - Returns WriteResult(success=True, path=<absolute>, ...)
        """
        notes_base = self._root / self._notes_subdir
        resolved = _safe_relative_path(notes_base, relative_path)
        if resolved is None:
            return WriteResult(
                success=False,
                path=None,
                failure=StructuredFailure(
                    capability=_CAP,
                    action="vault.write_note",
                    path=f"vault.notes.{relative_path}",
                    reason="unsafe_path",
                    user_message=(
                        f"The path '{relative_path}' is invalid or escapes the notes directory."
                    ),
                    recoverable=False,
                    machine_details={"relative_path": relative_path},
                ),
            )

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            file_content = _render_frontmatter(metadata) + content if metadata else content
            resolved.write_text(file_content, encoding="utf-8")
        except OSError as exc:
            return WriteResult(
                success=False,
                path=None,
                failure=StructuredFailure(
                    capability=_CAP,
                    action="vault.write_note",
                    path=str(resolved),
                    reason="io_error",
                    user_message=f"Failed to write note: {exc}",
                    recoverable=False,
                    machine_details={"error": str(exc)},
                ),
            )

        return WriteResult(success=True, path=str(resolved), failure=None)

    async def write_clip(
        self,
        *,
        source_url: str,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> WriteResult:
        """Write a clip under {root}/{clips_subdir}/{YYYY}/{MM}/{slug}.md.

        - YYYY and MM from metadata["clipped_at"] if present, else from current UTC time
        - slug derived from title via a tiny slugify helper
        - Frontmatter includes source_url, title, clipped_at, any extra metadata
        - No visible Kora marker (spec requirement)
        """
        # Determine timestamp
        clipped_at_str: str | None = None
        if metadata and "clipped_at" in metadata:
            clipped_at_str = metadata["clipped_at"]

        try:
            if clipped_at_str:
                # Parse ISO format; handle trailing Z
                ts_str = clipped_at_str.replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts_str)
            else:
                dt = datetime.now(UTC)
        except (ValueError, TypeError):
            dt = datetime.now(UTC)

        year = dt.strftime("%Y")
        month = dt.strftime("%m")
        slug = _slugify(title)
        clipped_at_final = clipped_at_str or dt.isoformat()

        clips_base = self._root / self._clips_subdir / year / month
        target = clips_base / f"{slug}.md"

        # Build frontmatter
        fm: dict[str, Any] = {
            "source_url": source_url,
            "title": title,
            "clipped_at": clipped_at_final,
        }
        if metadata:
            for k, v in metadata.items():
                if k != "clipped_at":
                    fm[k] = v

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            file_content = _render_frontmatter(fm) + content
            target.write_text(file_content, encoding="utf-8")
        except OSError as exc:
            return WriteResult(
                success=False,
                path=None,
                failure=StructuredFailure(
                    capability=_CAP,
                    action="vault.write_clip",
                    path=str(target),
                    reason="io_error",
                    user_message=f"Failed to write clip: {exc}",
                    recoverable=False,
                    machine_details={"error": str(exc)},
                ),
            )

        return WriteResult(success=True, path=str(target), failure=None)

    async def read_note(self, relative_path: str) -> WriteResult:
        """Read a note from {root}/{notes_subdir}/{relative_path}."""
        notes_base = self._root / self._notes_subdir
        resolved = _safe_relative_path(notes_base, relative_path)
        if resolved is None:
            return WriteResult(
                success=False,
                path=None,
                failure=StructuredFailure(
                    capability=_CAP,
                    action="vault.read_note",
                    path=f"vault.notes.{relative_path}",
                    reason="unsafe_path",
                    user_message=(
                        f"The path '{relative_path}' is invalid or escapes the notes directory."
                    ),
                    recoverable=False,
                    machine_details={"relative_path": relative_path},
                ),
            )

        if not resolved.exists():
            return WriteResult(
                success=False,
                path=str(resolved),
                failure=StructuredFailure(
                    capability=_CAP,
                    action="vault.read_note",
                    path=str(resolved),
                    reason="not_found",
                    user_message=f"Note not found: {relative_path}",
                    recoverable=False,
                ),
            )

        try:
            text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            return WriteResult(
                success=False,
                path=str(resolved),
                failure=StructuredFailure(
                    capability=_CAP,
                    action="vault.read_note",
                    path=str(resolved),
                    reason="io_error",
                    user_message=f"Failed to read note: {exc}",
                    recoverable=False,
                    machine_details={"error": str(exc)},
                ),
            )

        return WriteResult(success=True, path=str(resolved), failure=None, content=text)


def _render_frontmatter(metadata: dict[str, Any]) -> str:
    """Render a metadata dict as YAML frontmatter string.

    We use yaml.safe_dump rather than a hand-rolled renderer because
    safe_dump handles all edge cases (quoting special chars, multi-line
    strings, nested structures) correctly. default_flow_style=False
    gives block style for readability; sort_keys=True for stability.
    """
    dumped = yaml.safe_dump(metadata, default_flow_style=False, sort_keys=True)
    return f"---\n{dumped}---\n\n"


def _slugify(text: str, max_length: int = 80) -> str:
    """Return a filesystem-safe lowercase slug from text.

    - lowercase, ASCII letters + digits + hyphens only
    - collapse runs of non-alnum to single hyphen
    - strip leading/trailing hyphens
    - truncate to max_length
    - fallback: 'untitled' if empty
    """
    # Normalise unicode to ASCII
    normalised = unicodedata.normalize("NFKD", text)
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_text.lower()
    # Replace any run of non-alphanumeric chars with a single hyphen
    slugged = re.sub(r"[^a-z0-9]+", "-", lowered)
    # Strip leading/trailing hyphens
    slugged = slugged.strip("-")
    # Truncate
    slugged = slugged[:max_length]
    # Strip again in case truncation left a trailing hyphen
    slugged = slugged.strip("-")
    return slugged or "untitled"


def _safe_relative_path(base: Path, relative: str) -> Path | None:
    """Resolve `base / relative` ensuring the result is under `base`.

    Returns None if the relative path escapes base (symlink or ..).
    """
    try:
        candidate = (base / relative).resolve()
        base_resolved = base.resolve()
        # Check that candidate is under base_resolved
        candidate.relative_to(base_resolved)
        return candidate
    except (ValueError, OSError):
        return None
