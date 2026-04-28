"""Vault Organizer stage handlers — Phase 8c.

Per-stage handler functions for the ``post_memory_vault`` pipeline.
Each function matches the ``async (WorkerTask, StepContext) -> StepResult``
signature required by the orchestration dispatcher.

Stage handlers:
    - ``reindex_step``: Detect stale entries, re-embed, handle new/deleted files
    - ``structure_step``: Enforce folder hierarchy, triage Inbox
    - ``links_step``: Inject wikilinks, generate entity pages
    - ``moc_sessions_step``: Regenerate MOC pages, mirror session bridges

Event-to-queue discipline (spec section 3.0):
    ``MEMORY_STORED`` event handlers ONLY append note IDs to the module-level
    ``pending_notes`` queue. NO filesystem writes inline in event handlers.
    The queue is drained at the start of ``links_step``.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from kora_v2.agents.background.vault_organizer import (
    ENTITY_TYPE_FOLDER,
    FOLDER_HIERARCHY,
    MAX_INBOX_TRIAGE,
    MAX_LINKS_PER_INVOCATION,
    MAX_REINDEX_ENTRIES,
    MEMORY_TYPE_FOLDER_MAP,
    MOC_REGEN_THRESHOLD,
    EntityPageData,
    build_entity_page,
    build_moc_page,
    build_session_index,
    build_session_note,
    inject_wikilinks,
)
from kora_v2.autonomous.runtime_context import get_autonomous_context
from kora_v2.core.events import EventType
from kora_v2.memory.store import _parse_frontmatter
from kora_v2.runtime.orchestration.worker_task import (
    StepContext,
    StepResult,
    WorkerTask,
)

if TYPE_CHECKING:
    from kora_v2.core.events import EventEmitter
    from kora_v2.memory.projection import ProjectionDB
    from kora_v2.memory.store import FilesystemMemoryStore

log = structlog.get_logger(__name__)


# ── Event-to-queue discipline ──────────────────────────────────────────
# Module-level queue. MEMORY_STORED handlers append here.
# links_step drains it. Never do filesystem writes in event handlers.

pending_notes: list[str] = []
pending_entity_merges: list[dict[str, str]] = []
_last_moc_regen_at: datetime | None = None


async def _on_memory_stored(payload: dict[str, Any]) -> None:
    """Event handler for MEMORY_STORED — queue-only, no filesystem writes."""
    note_id = payload.get("note_id") or payload.get("memory_id")
    if note_id and note_id not in pending_notes:
        pending_notes.append(note_id)
        log.debug("vault_organizer_note_queued", note_id=note_id)


async def _on_entity_merged(payload: dict[str, Any]) -> None:
    """Event handler for ENTITY_MERGED — queue-only, no filesystem writes."""
    source_name = payload.get("source_name", "")
    target_name = payload.get("target_name", "")
    if source_name and target_name:
        pending_entity_merges.append(
            {"source_name": source_name, "target_name": target_name}
        )
        log.debug(
            "vault_organizer_entity_merge_queued",
            source=source_name,
            target=target_name,
        )


def register_event_handlers(emitter: EventEmitter) -> None:
    """Register event handlers for MEMORY_STORED and ENTITY_MERGED.

    Called during pipeline registration to set up the event-to-queue
    wiring. Handlers ONLY append to module-level queues.
    """
    emitter.on(EventType.MEMORY_STORED, _on_memory_stored)
    emitter.on(EventType.ENTITY_MERGED, _on_entity_merged)
    log.debug("vault_organizer_event_handlers_registered")


# ── Service resolution ──────────────────────────────────────────────────


def _resolve_services(
    task: WorkerTask,
) -> tuple[Any, Path]:
    """Resolve container and db_path from the autonomous runtime context."""
    ctx = get_autonomous_context()
    if ctx is None:
        raise RuntimeError(
            "Vault Organizer runtime context not set. "
            "OrchestrationEngine.start() must call set_autonomous_context() "
            "before dispatching vault organizer tasks."
        )
    return ctx.container, ctx.db_path


def _get_projection_db(container: Any) -> ProjectionDB:
    db = getattr(container, "projection_db", None)
    if db is None:
        raise RuntimeError("ProjectionDB not initialized on container")
    return db


def _get_memory_store(container: Any) -> FilesystemMemoryStore:
    store = getattr(container, "memory_store", None)
    if store is None:
        raise RuntimeError("FilesystemMemoryStore not initialized on container")
    return store


def _get_runtime_memory_root(container: Any, memory_store: Any) -> Path:
    """Return the canonical runtime memory root.

    The daemon writes bridge/session material under
    ``settings.memory.kora_memory_path``. Use that when available so vault
    organization follows the live runtime root instead of a legacy fallback.
    """
    settings = vars(container).get("settings")
    memory_settings = getattr(settings, "memory", None) if settings is not None else None
    configured_root = (
        getattr(memory_settings, "kora_memory_path", None)
        if memory_settings is not None
        else None
    )
    if isinstance(configured_root, str) and configured_root:
        return Path(configured_root).expanduser()
    return Path(memory_store._base)


def _get_event_emitter(container: Any) -> EventEmitter | None:
    return getattr(container, "event_emitter", None)


async def _llm_call(container: Any, system: str, user: str) -> str:
    """Make a single LLM call via the container's provider."""
    llm = getattr(container, "llm", None)
    if llm is None:
        raise RuntimeError("LLM provider not available on container")

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    if hasattr(llm, "chat"):
        response = await llm.chat(messages)
    else:
        response = await llm.generate(
            messages,
            system_prompt=system,
            temperature=0.2,
            max_tokens=2000,
        )

    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        return response.get("content", response.get("text", str(response)))
    if hasattr(response, "content"):
        return str(response.content)
    return str(response)


def _is_working_document(file_path: Path) -> bool:
    """Check if a file is a Phase 7.5 working document (has pipeline frontmatter).

    Working documents have a ``pipeline`` key in their YAML frontmatter
    and must NOT be touched by Vault Organizer (spec section 3b step 4).

    Uses YAML parse, NOT filename patterns.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
        meta, _body = _parse_frontmatter(text)
        return "pipeline" in meta
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════
# Stage 1: Reindex
# ══════════════════════════════════════════════════════════════════════════


async def reindex_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Detect stale entries, re-embed, handle new/deleted files.

    1. Call ProjectionDB.detect_stale_entries() (mtime comparison)
    2. For each stale entry (up to 50): re-read, re-embed, update DB
    3. Scan for new files on disk (no DB row) -> index them
    4. Scan for deleted files (DB row, no file) -> soft-delete DB rows
    """
    container, db_path = _resolve_services(task)
    projection_db = _get_projection_db(container)
    memory_store = _get_memory_store(container)

    reindexed_count = 0
    new_count = 0
    deleted_count = 0

    try:
        # 1. Detect stale entries
        stale_entries = await projection_db.detect_stale_entries()

        for entry in stale_entries[:MAX_REINDEX_ENTRIES]:
            try:
                source_path = entry.source_path
                if not source_path or not Path(source_path).is_file():
                    continue

                file_path = Path(source_path)
                text = file_path.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter(text)

                # Skip working documents
                if "pipeline" in meta:
                    continue

                # Re-compute embedding
                embedding_model = getattr(container, "embedding_model", None)
                if embedding_model is not None:
                    embedding = embedding_model.embed(body, task_type="search_document")
                else:
                    embedding = [0.0] * 768

                now = datetime.now(UTC).isoformat(timespec="seconds")

                # Update the appropriate table
                if entry.source_table == "memories":
                    await projection_db.update_memory_content(
                        memory_id=entry.record_id,
                        content=body,
                        summary=body[:200] if body else None,
                        updated_at=now,
                        embedding=embedding,
                    )
                else:
                    await projection_db.update_user_model_fact(
                        fact_id=entry.record_id,
                        content=body,
                        confidence=meta.get("importance", 0.5),
                        evidence_count=meta.get("evidence_count", 1),
                        updated_at=now,
                        embedding=embedding,
                    )

                reindexed_count += 1

            except Exception:
                log.exception(
                    "reindex_entry_failed",
                    record_id=entry.record_id,
                )

        # 2. Scan for new files (on disk but no DB row)
        base_path = memory_store._base
        if base_path.is_dir():
            # Get all known source_paths from projection DB
            known_paths: set[str] = set()
            for table in ("memories", "user_model_facts"):
                cursor = await projection_db._db.execute(
                    f"SELECT source_path FROM {table} WHERE status = 'active'"  # noqa: S608
                )
                for row in await cursor.fetchall():
                    sp = row[0]
                    if sp:
                        known_paths.add(sp)

            # Walk the filesystem for .md files
            for md_file in base_path.rglob("*.md"):
                if str(md_file) in known_paths:
                    continue
                # Skip hidden dirs and .kora internal
                rel = md_file.relative_to(base_path)
                parts = rel.parts
                if any(p.startswith(".") for p in parts):
                    continue
                # Skip auto-generated pages
                if any(p in ("Maps of Content", "Sessions", "Entities") for p in parts):
                    continue

                try:
                    text = md_file.read_text(encoding="utf-8")
                    meta, body = _parse_frontmatter(text)

                    if "pipeline" in meta:
                        continue

                    # Infer memory_type from parent folder
                    memory_type = _infer_memory_type(md_file, base_path)
                    note_id = meta.get("id", md_file.stem)

                    embedding_model = getattr(container, "embedding_model", None)
                    if embedding_model is not None:
                        embedding = embedding_model.embed(body, task_type="search_document")
                    else:
                        embedding = [0.0] * 768

                    now = datetime.now(UTC).isoformat(timespec="seconds")

                    if memory_type == "user_model":
                        domain = _infer_domain(md_file, base_path)
                        await projection_db.index_user_model_fact(
                            fact_id=note_id,
                            domain=domain,
                            content=body,
                            confidence=meta.get("importance", 0.5),
                            evidence_count=1,
                            contradiction_count=0,
                            created_at=meta.get("created_at", now),
                            updated_at=now,
                            source_path=str(md_file),
                            embedding=embedding,
                        )
                    else:
                        await projection_db.index_memory(
                            memory_id=note_id,
                            content=body,
                            summary=body[:200] if body else None,
                            importance=meta.get("importance", 0.5),
                            memory_type=memory_type,
                            created_at=meta.get("created_at", now),
                            updated_at=now,
                            entities=json.dumps(meta.get("entities", [])),
                            tags=json.dumps(meta.get("tags", [])),
                            source_path=str(md_file),
                            embedding=embedding,
                        )

                    new_count += 1

                except Exception:
                    log.exception("reindex_new_file_failed", path=str(md_file))

        # 3. Scan for deleted files (DB row but no file on disk)
        for table in ("memories", "user_model_facts"):
            cursor = await projection_db._db.execute(
                f"SELECT id, source_path FROM {table} WHERE status = 'active'"  # noqa: S608
            )
            for row in await cursor.fetchall():
                record_id = row[0]
                source_path = row[1]
                if source_path and not Path(source_path).is_file():
                    await projection_db.soft_delete(
                        table=table,
                        record_id=record_id,
                        successor_id=None,
                        reason="file_deleted",
                    )
                    deleted_count += 1

    except RuntimeError:
        raise
    except Exception:
        log.exception("reindex_step_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="reindex_step_error",
        )

    log.info(
        "reindex_step_complete",
        task_id=task.id,
        reindexed=reindexed_count,
        new_files=new_count,
        deleted=deleted_count,
    )

    return StepResult(
        outcome="complete",
        result_summary=(
            f"reindex: {reindexed_count} updated, "
            f"{new_count} new, {deleted_count} deleted"
        ),
    )


def _infer_memory_type(file_path: Path, base_path: Path) -> str:
    """Infer memory_type from the file's location under base_path."""
    try:
        rel = file_path.relative_to(base_path)
        parts = rel.parts
        if parts and parts[0] == "Long-Term":
            if len(parts) > 1:
                subtype = parts[1].lower()
                if subtype in ("episodic", "reflective", "procedural"):
                    return subtype
            return "episodic"
        if parts and parts[0] == "User Model":
            return "user_model"
        if parts and parts[0] == "Inbox":
            return "episodic"
    except ValueError:
        pass
    return "episodic"


def _infer_domain(file_path: Path, base_path: Path) -> str:
    """Infer user model domain from the file's location."""
    try:
        rel = file_path.relative_to(base_path)
        parts = rel.parts
        if len(parts) >= 2 and parts[0] == "User Model":
            return parts[1].lower().replace(" ", "_")
    except ValueError:
        pass
    return "identity"


async def _write_entity_page(
    projection_db: ProjectionDB,
    base_path: Path,
    entity_info: dict[str, Any],
) -> bool:
    """Write one generated entity page from projection DB data."""
    entity_name = entity_info["name"]
    entity_type = entity_info["type"]
    entity_id = entity_info["id"]

    memories = await projection_db.get_memories_by_entity(entity_name)
    backlinks = [
        {
            "id": m.id,
            "content": m.content,
            "created_at": m.created_at,
        }
        for m in memories
    ]

    relationships_raw = await projection_db.get_entity_relationships(entity_id)
    relationships = [
        {
            "entity_name": r.entity_name,
            "co_occurrence_count": r.co_occurrence_count,
        }
        for r in relationships_raw
    ]

    page_data = EntityPageData(
        name=entity_name,
        entity_type=entity_type,
        backlinks=backlinks,
        relationships=relationships,
        first_mention=entity_info.get("first_mention"),
        last_mention=entity_info.get("last_mention"),
    )
    page_content = build_entity_page(page_data)

    folder_name = ENTITY_TYPE_FOLDER.get(entity_type, "Projects")
    entity_dir = base_path / "Entities" / folder_name
    entity_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^\w\s-]", "", entity_name).strip()
    safe_name = re.sub(r"\s+", "_", safe_name)
    if not safe_name:
        return False

    entity_file = entity_dir / f"{safe_name}.md"
    existing = entity_file.read_text(encoding="utf-8") if entity_file.exists() else None
    if existing != page_content:
        entity_file.write_text(page_content, encoding="utf-8")
    return True


# ══════════════════════════════════════════════════════════════════════════
# Stage 2: Structure
# ══════════════════════════════════════════════════════════════════════════


async def structure_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Enforce folder hierarchy, move misplaced notes, triage Inbox.

    1. Ensure folder hierarchy exists
    2. Check for misplaced notes, move via move_note()
    3. Triage Inbox: LLM classify, only auto-move if confidence > 0.75
    4. CRITICAL: Skip files with ``pipeline`` frontmatter key
    5. Never modify body of existing notes
    """
    container, db_path = _resolve_services(task)
    memory_store = _get_memory_store(container)
    projection_db = _get_projection_db(container)

    folders_created = 0
    notes_moved = 0
    inbox_triaged = 0
    request_count = 0

    try:
        base_path = memory_store._base

        # 1. Ensure folder hierarchy
        for folder in FOLDER_HIERARCHY:
            folder_path = base_path / folder
            if not folder_path.exists():
                folder_path.mkdir(parents=True, exist_ok=True)
                folders_created += 1

        # Also ensure Sessions/YYYY/MM structure for current and recent months
        now = datetime.now(UTC)
        for month_offset in range(3):
            dt = datetime(now.year, now.month, 1) if month_offset == 0 else datetime(
                now.year if now.month - month_offset > 0 else now.year - 1,
                now.month - month_offset if now.month - month_offset > 0 else 12 + (now.month - month_offset),
                1,
            )
            session_dir = base_path / "Sessions" / str(dt.year) / f"{dt.month:02d}"
            if not session_dir.exists():
                session_dir.mkdir(parents=True, exist_ok=True)

        # 2. Check for misplaced notes
        table_queries = {
            "memories": (
                "SELECT id, source_path, memory_type FROM memories "
                "WHERE status = 'active' AND source_path != ''"
            ),
            "user_model_facts": (
                "SELECT id, source_path, 'user_model' AS memory_type "
                "FROM user_model_facts "
                "WHERE status = 'active' AND source_path != ''"
            ),
        }
        for table, query in table_queries.items():
            cursor = await projection_db._db.execute(query)
            for row in await cursor.fetchall():
                record_id = row[0]
                source_path = row[1]
                memory_type = row[2] if table == "memories" else "user_model"

                if not source_path:
                    continue

                file_path = Path(source_path)
                if not file_path.is_file():
                    continue

                # Skip working documents
                if _is_working_document(file_path):
                    continue

                # Check if the file is in the right folder
                expected_prefix = MEMORY_TYPE_FOLDER_MAP.get(memory_type, "Long-Term")
                try:
                    rel = file_path.relative_to(base_path)
                    parts = rel.parts
                    if parts and parts[0] != expected_prefix:
                        # Misplaced: move to correct location
                        target_dir = memory_store._resolve_directory(memory_type, None)
                        target_dir.mkdir(parents=True, exist_ok=True)
                        new_path = target_dir / file_path.name

                        if not new_path.exists():
                            await memory_store.move_note(
                                note_id=record_id,
                                new_path=new_path,
                                projection_db=projection_db,
                            )
                            notes_moved += 1
                except ValueError:
                    continue

        # 3. Triage Inbox
        inbox_path = base_path / "Inbox"
        if inbox_path.is_dir():
            inbox_files = sorted(inbox_path.glob("*.md"))[:MAX_INBOX_TRIAGE]

            for inbox_file in inbox_files:
                # Skip working documents
                if _is_working_document(inbox_file):
                    continue

                try:
                    text = inbox_file.read_text(encoding="utf-8")
                    meta, body = _parse_frontmatter(text)
                    note_id = meta.get("id", inbox_file.stem)

                    if not body.strip():
                        continue

                    # LLM classification
                    system_prompt = (
                        "You are a note classifier. Given a note's content, determine "
                        "which folder it should go to. Respond with ONLY a JSON object:\n"
                        '{"folder": "Long-Term/Episodic" or "Long-Term/Reflective" or '
                        '"Long-Term/Procedural" or "References" or "Ideas", '
                        '"confidence": 0.0 to 1.0}\n'
                        "Do not choose Inbox."
                    )
                    user_prompt = f"Classify this note:\n\n{body[:2000]}"

                    response_text = await _llm_call(
                        container, system_prompt, user_prompt
                    )
                    request_count += 1

                    # Parse response
                    try:
                        # Strip markdown code fences if present
                        cleaned = response_text.strip()
                        if cleaned.startswith("```"):
                            lines = cleaned.split("\n")
                            cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
                        result = json.loads(cleaned)
                    except json.JSONDecodeError:
                        log.warning(
                            "inbox_triage_parse_failed",
                            note_id=note_id,
                        )
                        continue

                    folder = result.get("folder", "")
                    confidence = result.get("confidence", 0.0)

                    # Only auto-move if confidence > 0.75
                    if confidence <= 0.75:
                        log.debug(
                            "inbox_triage_low_confidence",
                            note_id=note_id,
                            folder=folder,
                            confidence=confidence,
                        )
                        continue

                    # Validate folder
                    valid_folders = {
                        "Long-Term/Episodic",
                        "Long-Term/Reflective",
                        "Long-Term/Procedural",
                        "References",
                        "Ideas",
                    }
                    if folder not in valid_folders:
                        continue

                    # Move the note
                    target_dir = base_path / folder
                    target_dir.mkdir(parents=True, exist_ok=True)
                    new_path = target_dir / inbox_file.name

                    if not new_path.exists():
                        await memory_store.move_note(
                            note_id=note_id,
                            new_path=new_path,
                            projection_db=projection_db,
                        )
                        inbox_triaged += 1

                except Exception:
                    log.exception(
                        "inbox_triage_failed",
                        path=str(inbox_file),
                    )

    except RuntimeError:
        raise
    except Exception:
        log.exception("structure_step_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="structure_step_error",
            request_count_delta=request_count,
        )

    log.info(
        "structure_step_complete",
        task_id=task.id,
        folders_created=folders_created,
        notes_moved=notes_moved,
        inbox_triaged=inbox_triaged,
    )

    return StepResult(
        outcome="complete",
        result_summary=(
            f"structure: {folders_created} folders created, "
            f"{notes_moved} moved, {inbox_triaged} triaged"
        ),
        request_count_delta=request_count,
    )


# ══════════════════════════════════════════════════════════════════════════
# Stage 3: Links & Entities
# ══════════════════════════════════════════════════════════════════════════


async def links_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Inject wikilinks, generate/update entity pages.

    1. Drain pending_notes queue (up to 30)
    2. For each note: parse, inject wikilinks, write updated body
    3. Generate/update entity pages
    4. Handle ENTITY_MERGED events
    """
    container, db_path = _resolve_services(task)
    projection_db = _get_projection_db(container)
    memory_store = _get_memory_store(container)

    linked_count = 0
    entity_pages_count = 0

    try:
        # 1. Drain the pending_notes queue
        notes_to_process: list[str] = []
        while pending_notes and len(notes_to_process) < MAX_LINKS_PER_INVOCATION:
            notes_to_process.append(pending_notes.pop(0))

        # 2. Get all known entity names for wikilink injection
        entity_names: list[str] = []
        entities_by_name: dict[str, dict[str, Any]] = {}
        for entity_type in ("person", "place", "project", "organization", "medication"):
            entities = await projection_db.get_entities_by_type(entity_type)
            for entity in entities:
                entity_names.append(entity.name)
                entities_by_name[entity.name] = {
                    "id": entity.id,
                    "name": entity.name,
                    "type": entity.entity_type,
                    "active_link_count": entity.active_link_count,
                    "first_mention": entity.first_mention,
                    "last_mention": entity.last_mention,
                }

        # 3. Process each queued note
        updated_entities: set[str] = set()

        for note_id in notes_to_process:
            try:
                note = await memory_store.read_note(note_id)
                if note is None:
                    continue

                # Skip working documents
                note_path = Path(note.metadata.source_path) if note.metadata.source_path else None
                if note_path and _is_working_document(note_path):
                    continue

                # Inject wikilinks
                original_body = note.body
                updated_body = inject_wikilinks(original_body, entity_names)

                if updated_body != original_body:
                    await memory_store.update_body(note_id, updated_body)
                    linked_count += 1

                # Track which entities were actually linked in this note
                for entity_name in entity_names:
                    if f"[[{entity_name}]]" in updated_body:
                        updated_entities.add(entity_name)

            except Exception:
                log.exception("links_note_failed", note_id=note_id)

        # 4. Generate/update entity pages for all active projection entities.
        # The pending queue only tells us what to wikilink this invocation;
        # entity pages need to backfill existing projection rows too.
        base_path = _get_runtime_memory_root(container, memory_store)
        entities_to_render = [
            name
            for name, entity_info in entities_by_name.items()
            if entity_info.get("active_link_count", 1) > 0 or name in updated_entities
        ]
        for entity_name in entities_to_render:
            try:
                entity_info = entities_by_name.get(entity_name)
                if entity_info is None:
                    continue

                if await _write_entity_page(projection_db, base_path, entity_info):
                    entity_pages_count += 1

            except Exception:
                log.exception(
                    "entity_page_failed",
                    entity_name=entity_name,
                )

        # 5. Handle ENTITY_MERGED events
        merges_processed = 0
        while pending_entity_merges:
            merge = pending_entity_merges.pop(0)
            source_name = merge["source_name"]
            target_name = merge["target_name"]

            try:
                # Find notes referencing the old entity name and update them
                old_memories = await projection_db.get_memories_by_entity(source_name)
                for mem in old_memories:
                    note = await memory_store.read_note(mem.id)
                    if note is None:
                        continue
                    # Replace old entity references with new canonical name
                    updated = note.body.replace(
                        f"[[{source_name}]]", f"[[{target_name}]]"
                    )
                    if updated != note.body:
                        await memory_store.update_body(mem.id, updated)

                merges_processed += 1
            except Exception:
                log.exception(
                    "entity_merge_update_failed",
                    source=source_name,
                    target=target_name,
                )

    except RuntimeError:
        raise
    except Exception:
        log.exception("links_step_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="links_step_error",
        )

    log.info(
        "links_step_complete",
        task_id=task.id,
        linked=linked_count,
        entity_pages=entity_pages_count,
        queue_remaining=len(pending_notes),
    )

    return StepResult(
        outcome="complete",
        result_summary=(
            f"links: {linked_count} notes linked, "
            f"{entity_pages_count} entity pages"
        ),
    )


# ══════════════════════════════════════════════════════════════════════════
# Stage 4: MOCs & Sessions
# ══════════════════════════════════════════════════════════════════════════


async def moc_sessions_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Regenerate MOC pages, mirror session bridges, update session index.

    1. Regenerate MOC pages if >= N notes changed since last regen
    2. Mirror session bridges from .kora/bridges/ into Sessions/YYYY/MM/
    3. Update session index at Sessions/index.md
    """
    container, db_path = _resolve_services(task)
    projection_db = _get_projection_db(container)
    memory_store = _get_memory_store(container)

    mocs_regenerated = 0
    sessions_mirrored = 0

    try:
        base_path = _get_runtime_memory_root(container, memory_store)

        # 1. Regenerate MOC pages
        # Query note counts per domain from projection DB
        # Only count notes changed since last MOC regen (spec section 3d)
        global _last_moc_regen_at  # noqa: PLW0603
        domains_to_regen: list[str] = []

        # Check which domains have enough changed notes since last regen
        changed_total = 0
        for table, type_col in [("memories", "memory_type"), ("user_model_facts", None)]:
            if _last_moc_regen_at is not None:
                since_ts = _last_moc_regen_at.isoformat(timespec="seconds")
                if type_col:
                    cursor = await projection_db._db.execute(
                        f"SELECT {type_col}, COUNT(*) as cnt FROM {table} "  # noqa: S608
                        f"WHERE status = 'active' AND updated_at > ? "
                        f"GROUP BY {type_col}",
                        (since_ts,),
                    )
                else:
                    cursor = await projection_db._db.execute(
                        "SELECT domain, COUNT(*) as cnt FROM user_model_facts "
                        "WHERE status = 'active' AND updated_at > ? "
                        "GROUP BY domain",
                        (since_ts,),
                    )
            else:
                # First run: count all active notes
                if type_col:
                    cursor = await projection_db._db.execute(
                        f"SELECT {type_col}, COUNT(*) as cnt FROM {table} "  # noqa: S608
                        f"WHERE status = 'active' "
                        f"GROUP BY {type_col}"
                    )
                else:
                    cursor = await projection_db._db.execute(
                        "SELECT domain, COUNT(*) as cnt FROM user_model_facts "
                        "WHERE status = 'active' "
                        "GROUP BY domain"
                    )

            for row in await cursor.fetchall():
                domain_name = row[0]
                count = row[1]
                changed_total += int(count or 0)
                if count >= MOC_REGEN_THRESHOLD:
                    domains_to_regen.append(domain_name)
        if not domains_to_regen and changed_total >= MOC_REGEN_THRESHOLD:
            domains_to_regen.append("__overview__")

        # Generate MOC pages
        moc_dir = base_path / "Maps of Content"
        moc_dir.mkdir(parents=True, exist_ok=True)

        for domain in domains_to_regen:
            try:
                # Get notes for this domain. If enough notes changed
                # across the vault but no single domain crossed the
                # threshold, generate an overview MOC instead of writing
                # zero pages after a meaningful structural update.
                notes_data: list[dict[str, Any]] = []

                # Try memories table first
                if domain == "__overview__":
                    cursor = await projection_db._db.execute(
                        "SELECT id, content, importance, created_at FROM memories "
                        "WHERE status = 'active' "
                        "ORDER BY importance DESC, created_at DESC "
                        "LIMIT 100"
                    )
                else:
                    cursor = await projection_db._db.execute(
                        "SELECT id, content, importance, created_at FROM memories "
                        "WHERE status = 'active' AND memory_type = ? "
                        "ORDER BY importance DESC, created_at DESC "
                        "LIMIT 100",
                        (domain,),
                    )
                for row in await cursor.fetchall():
                    notes_data.append({
                        "id": row[0],
                        "content": row[1],
                        "importance": row[2],
                        "created_at": row[3],
                    })

                # Also try user_model_facts
                if domain == "__overview__":
                    cursor = await projection_db._db.execute(
                        "SELECT id, content, confidence, created_at FROM user_model_facts "
                        "WHERE status = 'active' "
                        "ORDER BY confidence DESC, created_at DESC "
                        "LIMIT 100"
                    )
                else:
                    cursor = await projection_db._db.execute(
                        "SELECT id, content, confidence, created_at FROM user_model_facts "
                        "WHERE status = 'active' AND domain = ? "
                        "ORDER BY confidence DESC, created_at DESC "
                        "LIMIT 100",
                        (domain,),
                    )
                for row in await cursor.fetchall():
                    notes_data.append({
                        "id": row[0],
                        "content": row[1],
                        "importance": row[2],
                        "created_at": row[3],
                    })

                if not notes_data:
                    continue

                moc_domain = "overview" if domain == "__overview__" else domain
                moc_content = build_moc_page(moc_domain, notes_data)

                # Write MOC file
                safe_domain = moc_domain.replace("_", " ").title()
                moc_file = moc_dir / f"MOC - {safe_domain}.md"
                moc_file.write_text(moc_content, encoding="utf-8")
                mocs_regenerated += 1

            except Exception:
                log.exception("moc_generation_failed", domain=domain)

        # Update last regen timestamp after successful MOC generation
        if mocs_regenerated > 0:
            _last_moc_regen_at = datetime.now(UTC)

        # 2. Mirror canonical session bridges from the runtime memory root.
        bridges_dir = base_path / ".kora" / "bridges"
        sessions_dir = base_path / "Sessions"
        session_records: list[dict[str, Any]] = []

        if bridges_dir.is_dir():
            bridge_files = [
                path
                for path in sorted(
                    [*bridges_dir.glob("*.json"), *bridges_dir.glob("*.md")]
                )
                if not path.name.endswith("-snapshot.json")
            ]
            for bridge_file in bridge_files:
                try:
                    bridge_text = bridge_file.read_text(encoding="utf-8")
                    if bridge_file.suffix == ".json":
                        bridge_data = json.loads(bridge_text)
                    else:
                        meta, body = _parse_frontmatter(bridge_text)
                        bridge_data = dict(meta)
                        if body.strip():
                            bridge_data.setdefault("summary", body.strip())
                        open_threads = bridge_data.get("open_threads")
                        if isinstance(open_threads, list):
                            bridge_data.setdefault("topics", open_threads[:3])

                    session_id = bridge_data.get("session_id", bridge_file.stem)
                    session_date = bridge_data.get(
                        "ended_at",
                        bridge_data.get("created_at", ""),
                    )
                    if not session_date:
                        continue

                    # Parse date for folder structure
                    try:
                        dt = datetime.fromisoformat(session_date.replace("Z", "+00:00"))
                    except ValueError:
                        continue

                    year = str(dt.year)
                    month = f"{dt.month:02d}"
                    date_str = dt.strftime("%Y-%m-%d")

                    target_dir = sessions_dir / year / month
                    target_dir.mkdir(parents=True, exist_ok=True)

                    topics = bridge_data.get("topics", [])
                    topic_slug = "_".join(topics[:3]) if topics else "session"
                    topic_slug = re.sub(r"[^\w]+", "_", topic_slug)[:40]
                    note_name = f"{date_str}_{topic_slug}"
                    target_file = target_dir / f"{note_name}.md"

                    if not target_file.exists():
                        session_content = build_session_note(
                            bridge_data, date_str
                        )
                        target_file.write_text(session_content, encoding="utf-8")
                        sessions_mirrored += 1

                    session_records.append({
                        "date": date_str,
                        "topics": topics,
                        "note_name": note_name,
                        "session_id": session_id,
                    })

                except Exception:
                    log.exception(
                        "session_mirror_failed",
                        bridge_file=str(bridge_file),
                    )

        # Also gather existing session notes for the index
        if sessions_dir.is_dir():
            for session_note in sessions_dir.rglob("*.md"):
                if session_note.name == "index.md":
                    continue
                try:
                    text = session_note.read_text(encoding="utf-8")
                    meta, _body = _parse_frontmatter(text)
                    session_date = meta.get("session_date", "")
                    topics = meta.get("topics", [])
                    note_name = session_note.stem

                    # Avoid duplicates
                    existing_ids = {r["note_name"] for r in session_records}
                    if note_name not in existing_ids:
                        session_records.append({
                            "date": session_date,
                            "topics": topics if isinstance(topics, list) else [],
                            "note_name": note_name,
                        })
                except Exception:
                    pass

        # 3. Update session index
        if session_records:
            index_content = build_session_index(session_records)
            index_file = sessions_dir / "index.md"
            index_file.write_text(index_content, encoding="utf-8")

    except RuntimeError:
        raise
    except Exception:
        log.exception("moc_sessions_step_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="moc_sessions_step_error",
        )

    log.info(
        "moc_sessions_step_complete",
        task_id=task.id,
        mocs=mocs_regenerated,
        sessions=sessions_mirrored,
    )

    return StepResult(
        outcome="complete",
        result_summary=(
            f"moc_sessions: {mocs_regenerated} MOCs, "
            f"{sessions_mirrored} sessions mirrored"
        ),
    )
