"""Filesystem Memory Store — canonical markdown notes in _KoraMemory/.

The filesystem is the source of truth. Projection DB is derived from these
notes. Each note is a markdown file with YAML frontmatter containing metadata.

Directory structure:
    _KoraMemory/
    ├── Long-Term/
    │   ├── {note_id}.md          # episodic, reflective, procedural memories
    │   └── ...
    └── User Model/
        ├── identity/
        │   └── {note_id}.md
        ├── preferences/
        │   └── {note_id}.md
        └── ... (20 domains + ADHD Profile)
"""

from __future__ import annotations

import os
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from kora_v2.memory.projection import ProjectionDB

logger = structlog.get_logger()


# ============================================================
# ID generation
# ============================================================


def generate_note_id() -> str:
    """Generate a sortable unique ID: timestamp prefix + random suffix.

    Format: 13-char hex timestamp (ms since epoch) + hyphen + 8-char random hex.
    Lexicographic sort order tracks creation time.
    """
    ts = int(time.time() * 1000)
    rand = uuid.uuid4().hex[:8]
    return f"{ts:013x}-{rand}"


# ============================================================
# Models
# ============================================================


class NoteMetadata(BaseModel):
    """Metadata extracted from a note's YAML frontmatter."""

    id: str
    memory_type: str = "episodic"
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    entities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    source_path: str = ""


class NoteContent(BaseModel):
    """Full note: metadata plus the body text after frontmatter."""

    metadata: NoteMetadata
    body: str = ""


# ============================================================
# Frontmatter helpers
# ============================================================


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown text.

    Expects ``---`` delimiters. Returns (meta_dict, body_text).
    If no valid frontmatter is found, returns ({}, full_text).
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return meta, body


def _frontmatter_str(value: object) -> str:
    """Normalize YAML scalar values that Pydantic stores as strings."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _render_note(meta: dict, body: str) -> str:
    """Render a note with YAML frontmatter + body content."""
    frontmatter = yaml.dump(
        meta, default_flow_style=False, sort_keys=False, allow_unicode=True,
    )
    return f"---\n{frontmatter}---\n\n{body}\n"


def _now_iso() -> str:
    """Current UTC time as ISO 8601 string."""
    return datetime.now(UTC).isoformat(timespec="seconds")


# ============================================================
# User Model domains (canonical subdirectory names)
# ============================================================

USER_MODEL_DOMAINS = frozenset({
    "identity",
    "preferences",
    "relationships",
    "routines",
    "health",
    "work",
    "education",
    "finances",
    "hobbies",
    "goals",
    "values",
    "communication_style",
    "emotional_patterns",
    "triggers",
    "strengths",
    "challenges",
    "medications",
    "pets",
    "living_situation",
    "diet",
    "adhd_profile",
})


# ============================================================
# FilesystemMemoryStore
# ============================================================


class FilesystemMemoryStore:
    """Read/write canonical markdown notes under ``_KoraMemory/``.

    Each note is a ``.md`` file with YAML frontmatter for metadata.
    The store creates the directory structure on init if it doesn't
    already exist.

    Thread-safety: individual file operations are atomic (write to
    temp then rename is NOT implemented here because local FS writes
    are effectively atomic for small files). For concurrent access,
    callers should serialize at a higher level.
    """

    def __init__(self, base_path: Path) -> None:
        """Initialise the store rooted at *base_path* (``_KoraMemory/``).

        Creates ``Long-Term/`` and ``User Model/`` directories if absent.
        """
        self._base = base_path
        self._long_term = base_path / "Long-Term"
        self._user_model = base_path / "User Model"

        # Ensure top-level directories exist
        self._long_term.mkdir(parents=True, exist_ok=True)
        self._user_model.mkdir(parents=True, exist_ok=True)

        logger.debug(
            "filesystem_memory_store_init",
            base_path=str(base_path),
        )

    # ── Write ────────────────────────────────────────────────────────

    async def write_note(
        self,
        content: str,
        memory_type: str = "episodic",
        domain: str | None = None,
        entities: list[str] | None = None,
        tags: list[str] | None = None,
        importance: float = 0.5,
        note_id: str | None = None,
    ) -> NoteMetadata:
        """Write a new note to the filesystem.

        Args:
            content: The note body text.
            memory_type: One of ``episodic``, ``reflective``, ``procedural``,
                or ``user_model``.
            domain: For ``user_model`` type, the subdomain directory
                (e.g. ``identity``, ``preferences``). Ignored for other types.
            entities: Extracted entity names (people, places, topics).
            tags: Freeform tags for categorisation.
            importance: Importance score 0.0–1.0.
            note_id: Optional pre-generated note ID. One is created if omitted.

        Returns:
            NoteMetadata for the newly written note.
        """
        note_id = note_id or generate_note_id()
        now = _now_iso()

        # Determine target directory
        target_dir = self._resolve_directory(memory_type, domain)
        target_dir.mkdir(parents=True, exist_ok=True)

        file_path = target_dir / f"{note_id}.md"

        meta_dict = {
            "id": note_id,
            "memory_type": memory_type,
            "importance": importance,
            "entities": entities or [],
            "tags": tags or [],
            "created_at": now,
            "updated_at": now,
        }

        file_path.write_text(_render_note(meta_dict, content), encoding="utf-8")

        logger.debug(
            "note_written",
            note_id=note_id,
            memory_type=memory_type,
            path=str(file_path),
        )

        return NoteMetadata(
            id=note_id,
            memory_type=memory_type,
            importance=importance,
            entities=entities or [],
            tags=tags or [],
            created_at=now,
            updated_at=now,
            source_path=str(file_path),
        )

    # ── Read ─────────────────────────────────────────────────────────

    async def read_note(self, note_id: str) -> NoteContent | None:
        """Read a note by its ID, searching all directories.

        Returns:
            NoteContent with metadata and body, or ``None`` if not found.
        """
        file_path = self._find_note_file(note_id)
        if file_path is None:
            logger.debug("note_not_found", note_id=note_id)
            return None

        text = file_path.read_text(encoding="utf-8")
        meta_dict, body = _parse_frontmatter(text)

        metadata = NoteMetadata(
            id=meta_dict.get("id", note_id),
            memory_type=meta_dict.get("memory_type", "episodic"),
            importance=meta_dict.get("importance", 0.5),
            entities=meta_dict.get("entities", []),
            tags=meta_dict.get("tags", []),
            created_at=_frontmatter_str(meta_dict.get("created_at", "")),
            updated_at=_frontmatter_str(meta_dict.get("updated_at", "")),
            source_path=str(file_path),
        )

        return NoteContent(metadata=metadata, body=body)

    # ── Update ───────────────────────────────────────────────────────

    async def update_note(
        self,
        note_id: str,
        content: str,
        updated_at: str | None = None,
    ) -> NoteMetadata | None:
        """Update the body of an existing note, preserving its frontmatter.

        Args:
            note_id: ID of the note to update.
            content: New body text.
            updated_at: Optional explicit timestamp; defaults to now.

        Returns:
            Updated NoteMetadata, or ``None`` if the note was not found.
        """
        file_path = self._find_note_file(note_id)
        if file_path is None:
            logger.debug("note_not_found_for_update", note_id=note_id)
            return None

        text = file_path.read_text(encoding="utf-8")
        meta_dict, _old_body = _parse_frontmatter(text)

        meta_dict["updated_at"] = updated_at or _now_iso()

        file_path.write_text(
            _render_note(meta_dict, content), encoding="utf-8",
        )

        logger.debug("note_updated", note_id=note_id, path=str(file_path))

        return NoteMetadata(
            id=meta_dict.get("id", note_id),
            memory_type=meta_dict.get("memory_type", "episodic"),
            importance=meta_dict.get("importance", 0.5),
            entities=meta_dict.get("entities", []),
            tags=meta_dict.get("tags", []),
            created_at=_frontmatter_str(meta_dict.get("created_at", "")),
            updated_at=_frontmatter_str(meta_dict["updated_at"]),
            source_path=str(file_path),
        )

    # ── List ─────────────────────────────────────────────────────────

    async def list_notes(
        self,
        layer: str = "all",
        domain: str | None = None,
    ) -> list[NoteMetadata]:
        """List note metadata from the specified layer.

        Args:
            layer: ``"long_term"``, ``"user_model"``, or ``"all"`` (default).
            domain: If *layer* is ``"user_model"``, restrict to this domain
                subdirectory.

        Returns:
            List of NoteMetadata (no body text — use read_note for full
            content). Sorted by filename (i.e. creation time, oldest first).
        """
        dirs: list[Path] = []

        if layer in ("long_term", "all"):
            dirs.append(self._long_term)
        if layer in ("user_model", "all"):
            if domain:
                sub = self._user_model / domain
                if sub.is_dir():
                    dirs.append(sub)
            else:
                dirs.append(self._user_model)

        results: list[NoteMetadata] = []
        for d in dirs:
            for md_file in sorted(d.rglob("*.md")):
                try:
                    text = md_file.read_text(encoding="utf-8")
                    meta_dict, _body = _parse_frontmatter(text)
                    results.append(NoteMetadata(
                        id=meta_dict.get("id", md_file.stem),
                        memory_type=meta_dict.get("memory_type", "episodic"),
                        importance=meta_dict.get("importance", 0.5),
                        entities=meta_dict.get("entities", []),
                        tags=meta_dict.get("tags", []),
                        created_at=_frontmatter_str(meta_dict.get("created_at", "")),
                        updated_at=_frontmatter_str(meta_dict.get("updated_at", "")),
                        source_path=str(md_file),
                    ))
                except Exception:
                    logger.warning(
                        "note_parse_error",
                        path=str(md_file),
                        exc_info=True,
                    )
        return results

    # ── Delete ───────────────────────────────────────────────────────

    async def delete_note(self, note_id: str) -> bool:
        """Delete a note by its ID.

        Returns:
            True if the note was found and deleted, False otherwise.
        """
        file_path = self._find_note_file(note_id)
        if file_path is None:
            logger.debug("note_not_found_for_delete", note_id=note_id)
            return False

        file_path.unlink()
        logger.debug("note_deleted", note_id=note_id, path=str(file_path))
        return True

    # ── Frontmatter & body updates ──────────────────────────────────

    async def update_frontmatter(
        self,
        note_id: str,
        updates: dict,
    ) -> NoteMetadata | None:
        """Parse existing YAML frontmatter, merge in updates, rewrite note preserving body.

        Uses atomic write (temp file + rename) for crash safety.

        Args:
            note_id: ID of the note to update.
            updates: Dict of frontmatter keys to set/update.

        Returns:
            Updated NoteMetadata, or None if the note was not found.
        """
        file_path = self._find_note_file(note_id)
        if file_path is None:
            logger.debug("note_not_found_for_frontmatter_update", note_id=note_id)
            return None

        text = file_path.read_text(encoding="utf-8")
        meta_dict, body = _parse_frontmatter(text)

        # Merge updates into existing frontmatter
        meta_dict.update(updates)
        meta_dict["updated_at"] = _now_iso()

        # Atomic write: write to temp file, then rename
        new_content = _render_note(meta_dict, body)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(file_path.parent), suffix=".md.tmp"
        )
        try:
            os.write(fd, new_content.encode("utf-8"))
            os.close(fd)
            os.replace(tmp_path, str(file_path))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        logger.debug("frontmatter_updated", note_id=note_id, keys=list(updates.keys()))

        return NoteMetadata(
            id=meta_dict.get("id", note_id),
            memory_type=meta_dict.get("memory_type", "episodic"),
            importance=meta_dict.get("importance", 0.5),
            entities=meta_dict.get("entities", []),
            tags=meta_dict.get("tags", []),
            created_at=_frontmatter_str(meta_dict.get("created_at", "")),
            updated_at=_frontmatter_str(meta_dict["updated_at"]),
            source_path=str(file_path),
        )

    async def soft_delete_note(
        self,
        note_id: str,
        reason: str,
        successor_id: str | None,
        *,
        projection_db: ProjectionDB | None = None,
    ) -> bool:
        """Mark a note as soft-deleted in both filesystem frontmatter and projection DB.

        Updates the note's frontmatter with ``status: soft_deleted``,
        ``deleted_at``, and ``consolidated_into`` fields. Also calls
        ``projection_db.soft_delete()`` if a projection DB is provided.

        Args:
            note_id: ID of the note to soft-delete.
            reason: Reason for deletion (e.g. ``consolidated``, ``duplicate``).
            successor_id: Optional ID of the replacement note.
            projection_db: Optional ProjectionDB instance for atomic DB update.

        Returns:
            True if the note was found and soft-deleted, False otherwise.
        """
        file_path = self._find_note_file(note_id)
        if file_path is None:
            logger.debug("note_not_found_for_soft_delete", note_id=note_id)
            return False

        now = _now_iso()
        status = "merged" if reason in ("consolidated", "duplicate", "merged") else "soft_deleted"

        fm_updates: dict = {
            "status": status,
            "deleted_at": now,
        }
        if successor_id:
            fm_updates["consolidated_into"] = successor_id

        # Update projection DB first (rollback-capable) then filesystem
        if projection_db is not None:
            # Determine which table the record is in
            for table in ("memories", "user_model_facts"):
                record = await projection_db.get_memory_by_id(
                    note_id, include_soft_deleted=True
                ) if table == "memories" else await projection_db.get_fact_by_id(
                    note_id, include_soft_deleted=True
                )
                if record is not None:
                    await projection_db.soft_delete(
                        table=table,
                        record_id=note_id,
                        successor_id=successor_id,
                        reason=reason,
                    )
                    break

        # Update filesystem frontmatter (best-effort after DB commit)
        await self.update_frontmatter(note_id, fm_updates)

        logger.debug(
            "note_soft_deleted",
            note_id=note_id,
            reason=reason,
            successor=successor_id,
        )
        return True

    async def update_body(
        self,
        note_id: str,
        new_body: str,
    ) -> NoteMetadata | None:
        """Rewrite a note's body content, preserving frontmatter.

        Atomic write via temp file + rename.

        Args:
            note_id: ID of the note to update.
            new_body: New body text to replace the existing body.

        Returns:
            Updated NoteMetadata, or None if the note was not found.
        """
        file_path = self._find_note_file(note_id)
        if file_path is None:
            logger.debug("note_not_found_for_body_update", note_id=note_id)
            return None

        text = file_path.read_text(encoding="utf-8")
        meta_dict, _old_body = _parse_frontmatter(text)
        meta_dict["updated_at"] = _now_iso()

        new_content = _render_note(meta_dict, new_body)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(file_path.parent), suffix=".md.tmp"
        )
        try:
            os.write(fd, new_content.encode("utf-8"))
            os.close(fd)
            os.replace(tmp_path, str(file_path))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        logger.debug("body_updated", note_id=note_id, path=str(file_path))

        return NoteMetadata(
            id=meta_dict.get("id", note_id),
            memory_type=meta_dict.get("memory_type", "episodic"),
            importance=meta_dict.get("importance", 0.5),
            entities=meta_dict.get("entities", []),
            tags=meta_dict.get("tags", []),
            created_at=_frontmatter_str(meta_dict.get("created_at", "")),
            updated_at=_frontmatter_str(meta_dict["updated_at"]),
            source_path=str(file_path),
        )

    async def move_note(
        self,
        note_id: str,
        new_path: Path,
        *,
        projection_db: ProjectionDB | None = None,
    ) -> NoteMetadata | None:
        """Move a note file to a new location and update projection DB.

        Args:
            note_id: ID of the note to move.
            new_path: Destination path for the note file.
            projection_db: Optional ProjectionDB to update ``source_path``.

        Returns:
            Updated NoteMetadata, or None if the note was not found.
        """
        file_path = self._find_note_file(note_id)
        if file_path is None:
            logger.debug("note_not_found_for_move", note_id=note_id)
            return None

        # Ensure destination directory exists
        new_path.parent.mkdir(parents=True, exist_ok=True)

        # Read content and update source_path in metadata
        text = file_path.read_text(encoding="utf-8")
        meta_dict, body = _parse_frontmatter(text)

        # Atomic move: write to temp file in destination dir, then os.replace
        new_content = _render_note(meta_dict, body)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(new_path.parent), suffix=".md.tmp"
        )
        try:
            os.write(fd, new_content.encode("utf-8"))
            os.close(fd)
            os.replace(tmp_path, str(new_path))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        if file_path != new_path:
            file_path.unlink()

        # Update projection DB source_path
        if projection_db is not None:
            for table in ("memories", "user_model_facts"):
                try:
                    await projection_db._db.execute(
                        f"UPDATE {table} SET source_path = ? WHERE id = ?",  # noqa: S608
                        (str(new_path), note_id),
                    )
                    await projection_db._db.commit()
                except Exception:
                    pass

        logger.debug(
            "note_moved", note_id=note_id,
            from_path=str(file_path), to_path=str(new_path),
        )

        return NoteMetadata(
            id=meta_dict.get("id", note_id),
            memory_type=meta_dict.get("memory_type", "episodic"),
            importance=meta_dict.get("importance", 0.5),
            entities=meta_dict.get("entities", []),
            tags=meta_dict.get("tags", []),
            created_at=_frontmatter_str(meta_dict.get("created_at", "")),
            updated_at=_frontmatter_str(meta_dict.get("updated_at", "")),
            source_path=str(new_path),
        )

    # ── Private helpers ──────────────────────────────────────────────

    def _resolve_directory(
        self, memory_type: str, domain: str | None,
    ) -> Path:
        """Determine the target directory for a note.

        ``user_model`` type goes under ``User Model/{domain}/``.
        Everything else goes under ``Long-Term/``.
        """
        if memory_type == "user_model":
            if domain and domain in USER_MODEL_DOMAINS:
                return self._user_model / domain
            # Fall back to top-level User Model dir if domain is unknown
            return self._user_model
        return self._long_term

    def _find_note_file(self, note_id: str) -> Path | None:
        """Locate a note file by its ID across all directories.

        Searches Long-Term first, then User Model recursively.
        """
        # Direct lookup in Long-Term
        candidate = self._long_term / f"{note_id}.md"
        if candidate.is_file():
            return candidate

        for md_file in self._long_term.rglob(f"{note_id}.md"):
            return md_file

        # Recursive search in User Model (subdirectories)
        for md_file in self._user_model.rglob(f"{note_id}.md"):
            return md_file

        return None
