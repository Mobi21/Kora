"""Memory Steward stage handlers — Phase 8b.

Per-stage handler functions for the ``post_session_memory`` and
``weekly_adhd_profile`` pipelines. Each function matches the
``async (WorkerTask, StepContext) -> StepResult`` signature required
by the orchestration dispatcher.

Services are resolved via the process-level autonomous runtime context
(same pattern used by the autonomous pipeline step function) since the
dispatcher's ``StepContext`` deliberately does not carry the DI container.

Stage handlers:
    - ``extract_step``: Drain signal_queue + session_transcripts, LLM extraction
    - ``consolidate_step``: Merge semantically related notes
    - ``dedup_step``: Remove near-duplicates with LLM confirmation
    - ``entities_step``: Fuzzy-match entity resolution
    - ``vault_handoff_step``: Emit PIPELINE_COMPLETE
    - ``adhd_profile_refine_step``: Weekly ADHD profile refinement
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite
import structlog
import yaml

from kora_v2.agents.background.memory_steward import (
    ADHD_PROFILE_SYSTEM_PROMPT,
    ADHD_PROFILE_USER_TEMPLATE,
    CONSOLIDATION_SYSTEM_PROMPT,
    CONSOLIDATION_USER_TEMPLATE,
    DEDUP_SYSTEM_PROMPT,
    DEDUP_USER_TEMPLATE,
    ENTITY_FUZZY_THRESHOLD,
    ENTITY_RESOLUTION_SYSTEM_PROMPT,
    ENTITY_RESOLUTION_USER_TEMPLATE,
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_USER_TEMPLATE,
    MAX_CONSOLIDATION_GROUPS,
    MAX_DEDUP_PAIRS,
    MAX_ENTITY_PAIRS,
    MAX_SIGNALS_PER_INVOCATION,
    MAX_TRANSCRIPTS_PER_INVOCATION,
    SHRINKAGE_REJECTION_THRESHOLD,
    compute_shrinkage,
    jaro_winkler_similarity,
    parse_json_response,
    pick_richer_note,
    validate_extracted_facts,
)
from kora_v2.autonomous.runtime_context import get_autonomous_context
from kora_v2.core.events import EventType
from kora_v2.runtime.orchestration.worker_task import (
    StepContext,
    StepResult,
    WorkerTask,
)

if TYPE_CHECKING:
    from kora_v2.core.events import EventEmitter
    from kora_v2.memory.projection import ProjectionDB
    from kora_v2.memory.store import FilesystemMemoryStore
    from kora_v2.memory.write_pipeline import WritePipeline

log = structlog.get_logger(__name__)


# ── Service resolution ──────────────────────────────────────────────────


def _resolve_services(
    task: WorkerTask,
) -> tuple[Any, Path]:
    """Resolve container and db_path from the autonomous runtime context.

    Returns:
        (container, db_path) tuple, or raises RuntimeError if not available.
    """
    ctx = get_autonomous_context()
    if ctx is None:
        raise RuntimeError(
            "Memory Steward runtime context not set. "
            "OrchestrationEngine.start() must call set_autonomous_context() "
            "before dispatching memory steward tasks."
        )
    return ctx.container, ctx.db_path


def _get_write_pipeline(container: Any) -> WritePipeline:
    wp = getattr(container, "write_pipeline", None)
    if wp is None:
        raise RuntimeError("WritePipeline not initialized on container")
    return wp


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


def _get_event_emitter(container: Any) -> EventEmitter | None:
    return getattr(container, "event_emitter", None)


async def _llm_call(container: Any, system: str, user: str) -> str:
    """Make a single LLM call via the container's provider.

    Returns the assistant response text or raises on failure.
    """
    llm = getattr(container, "llm", None)
    if llm is None:
        raise RuntimeError("LLM provider not available on container")

    chat = getattr(llm, "chat", None)
    if callable(chat):
        response = await chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
    else:
        response = await llm.generate(
            [{"role": "user", "content": user}],
            system_prompt=system,
            temperature=0.2,
            max_tokens=2000,
        )

    # Handle various response shapes from the provider
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        return response.get("content", response.get("text", str(response)))
    if hasattr(response, "content"):
        return str(response.content)
    return str(response)


def _deterministic_consolidation(note_contents: list[str]) -> str:
    """Build a fact-preserving consolidation when the model over-shrinks."""
    sections: list[str] = [
        "Consolidated memory preserving the source notes verbatim."
    ]
    seen: set[str] = set()
    for idx, content in enumerate(note_contents, start=1):
        body = content.strip()
        if not body or body in seen:
            continue
        seen.add(body)
        sections.append(f"## Source Note {idx}\n\n{body}")
    return "\n\n".join(sections)


_RELATIONSHIP_ALIAS_CANONICAL_NAMES = {"partner", "roommate"}


def _is_relationship_alias_entity(entity: dict) -> bool:
    canonical = str(
        entity.get("canonical_name") or entity.get("name") or ""
    ).strip().lower()
    return canonical in _RELATIONSHIP_ALIAS_CANONICAL_NAMES


def _is_relationship_alias_pair(e1: dict, e2: dict) -> bool:
    if e1.get("entity_type") != "person" or e2.get("entity_type") != "person":
        return False
    return _is_relationship_alias_entity(e1) != _is_relationship_alias_entity(e2)


def _relationship_alias_context_confirms(
    e1: dict,
    e2: dict,
    context_a: str,
    context_b: str,
) -> bool:
    """Confirm relation aliases when source facts explicitly co-mention both.

    Entity rows such as ``partner`` and ``roommate`` are aliases, not separate
    people, when their linked facts also name the person. Those pairs do not
    pass fuzzy-name matching, so the entity stage handles the deterministic
    co-reference case before falling back to the LLM.
    """
    if not _is_relationship_alias_pair(e1, e2):
        return False
    alias = e1 if _is_relationship_alias_entity(e1) else e2
    named = e2 if alias is e1 else e1
    alias_key = str(alias.get("canonical_name") or alias.get("name")).lower()
    named_key = str(named.get("canonical_name") or named.get("name")).lower()
    combined = f"{context_a}\n{context_b}".lower()
    return alias_key in combined and named_key in combined


def _choose_entity_merge_target(e1: dict, e2: dict) -> tuple[dict, dict]:
    """Return ``(target, source)`` for an entity merge."""
    e1_alias = _is_relationship_alias_entity(e1)
    e2_alias = _is_relationship_alias_entity(e2)
    if e1_alias != e2_alias:
        return (e2, e1) if e1_alias else (e1, e2)
    if e1["link_count"] >= e2["link_count"]:
        return e1, e2
    return e2, e1


# ══════════════════════════════════════════════════════════════════════════
# Stage 1: Extract
# ══════════════════════════════════════════════════════════════════════════


async def extract_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Drain signal_queue and session_transcripts, extract facts via LLM.

    1. Mark up to MAX_SIGNALS_PER_INVOCATION pending signals as processing
    2. Load up to MAX_TRANSCRIPTS_PER_INVOCATION unprocessed transcripts
    3. For each transcript: LLM extraction -> WritePipeline.store()
    4. Mark transcripts processed, signals extracted/failed
    """
    container, db_path = _resolve_services(task)
    write_pipeline = _get_write_pipeline(container)

    now = datetime.now(UTC).isoformat(timespec="seconds")
    extracted_count = 0
    failed_count = 0
    request_count = 0
    transcripts: list[dict] = []
    signals: list[dict] = []

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row

            # 1. Drain signal_queue: up to MAX_SIGNALS_PER_INVOCATION pending
            cursor = await db.execute(
                "SELECT id, session_id, message_text, assistant_response, "
                "signal_types, priority "
                "FROM signal_queue "
                "WHERE status = 'pending' "
                "ORDER BY priority ASC, created_at ASC "
                "LIMIT ?",
                (MAX_SIGNALS_PER_INVOCATION,),
            )
            signals = [dict(row) for row in await cursor.fetchall()]

            signal_ids = [s["id"] for s in signals]
            if signal_ids:
                placeholders = ",".join("?" for _ in signal_ids)
                await db.execute(
                    f"UPDATE signal_queue SET status = 'processing' "  # noqa: S608
                    f"WHERE id IN ({placeholders})",
                    signal_ids,
                )
                await db.commit()

            # 2. Load unprocessed transcripts
            cursor = await db.execute(
                "SELECT session_id, messages, emotional_trajectory, "
                "tool_calls, message_count "
                "FROM session_transcripts "
                "WHERE processed_at IS NULL "
                "ORDER BY created_at ASC "
                "LIMIT ?",
                (MAX_TRANSCRIPTS_PER_INVOCATION,),
            )
            transcripts = [dict(row) for row in await cursor.fetchall()]

            # Build signal context per session
            signal_by_session: dict[str, list[dict]] = {}
            for sig in signals:
                sid = sig["session_id"]
                signal_by_session.setdefault(sid, []).append(sig)

            # 3. Process each transcript
            processed_session_ids: set[str] = set()
            failed_session_ids: set[str] = set()

            for transcript in transcripts:
                session_id = transcript["session_id"]
                try:
                    messages_json = transcript["messages"]
                    messages = json.loads(messages_json) if messages_json else []

                    # Build transcript text
                    transcript_text = ""
                    for msg in messages:
                        role = msg.get("role", "unknown")
                        content = msg.get("content", "")
                        transcript_text += f"{role}: {content}\n"

                    # Build signal context
                    signal_context = ""
                    session_signals = signal_by_session.get(session_id, [])
                    if session_signals:
                        signal_lines = []
                        for sig in session_signals:
                            signal_lines.append(
                                f"- Signal types: {sig['signal_types']}, "
                                f"priority: {sig['priority']}"
                            )
                        signal_context = (
                            "Signals detected in this session:\n"
                            + "\n".join(signal_lines)
                        )

                    # LLM extraction call
                    user_prompt = EXTRACTION_USER_TEMPLATE.format(
                        transcript=transcript_text[:4000],  # Truncate long transcripts
                        signal_context=signal_context,
                    )

                    response_text = await _llm_call(
                        container, EXTRACTION_SYSTEM_PROMPT, user_prompt
                    )
                    request_count += 1

                    # Parse and validate
                    raw_facts = parse_json_response(response_text)
                    facts = validate_extracted_facts(raw_facts)

                    # Store each fact via WritePipeline
                    for fact in facts:
                        try:
                            memory_type = fact["memory_type"]
                            if memory_type == "user_model":
                                await write_pipeline.store_user_model_fact(
                                    content=fact["content"],
                                    domain=fact.get("domain", "identity"),
                                    importance=fact["importance"],
                                    entity_hints=fact.get("entities", []),
                                    skip_dedup=False,
                                )
                            else:
                                await write_pipeline.store(
                                    content=fact["content"],
                                    memory_type=memory_type,
                                    importance=fact["importance"],
                                    tags=fact.get("tags", []),
                                    entity_hints=fact.get("entities", []),
                                    skip_dedup=False,
                                )
                            extracted_count += 1
                        except Exception:
                            log.exception(
                                "extract_fact_store_failed",
                                session_id=session_id,
                                content_preview=fact["content"][:80],
                            )
                            failed_count += 1

                    # Mark transcript as processed
                    await db.execute(
                        "UPDATE session_transcripts SET processed_at = ? "
                        "WHERE session_id = ?",
                        (now, session_id),
                    )
                    processed_session_ids.add(session_id)

                except Exception:
                    log.exception(
                        "extract_transcript_failed",
                        session_id=session_id,
                    )
                    failed_count += 1
                    failed_session_ids.add(session_id)
                    # Mark as processed anyway to avoid infinite retries
                    await db.execute(
                        "UPDATE session_transcripts SET processed_at = ? "
                        "WHERE session_id = ?",
                        (now, session_id),
                    )

            # 4. Mark signals based on transcript processing outcome.
            #    - Signals whose session was processed successfully: "extracted"
            #    - Signals whose session failed during processing: "failed"
            #    - Signals whose session transcript wasn't in this batch: leave
            #      as "pending" (don't update them).
            batch_session_ids = {t["session_id"] for t in transcripts}
            for sig in signals:
                sig_session = sig["session_id"]
                if sig_session in processed_session_ids:
                    await db.execute(
                        "UPDATE signal_queue SET status = ?, processed_at = ? "
                        "WHERE id = ?",
                        ("extracted", now, sig["id"]),
                    )
                elif sig_session in failed_session_ids:
                    await db.execute(
                        "UPDATE signal_queue SET status = ?, processed_at = ?, "
                        "error_message = ? WHERE id = ?",
                        ("failed", now, "transcript processing failed", sig["id"]),
                    )
                elif sig_session not in batch_session_ids:
                    # Transcript not in this batch — leave pending so a later
                    # extract invocation can process it when its transcript is
                    # within MAX_TRANSCRIPTS_PER_INVOCATION.
                    await db.execute(
                        "UPDATE signal_queue SET status = 'pending' "
                        "WHERE id = ?",
                        (sig["id"],),
                    )

            await db.commit()

    except RuntimeError:
        raise
    except Exception:
        log.exception("extract_step_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="extract_step_error",
            request_count_delta=request_count,
        )

    log.info(
        "extract_step_complete",
        task_id=task.id,
        extracted=extracted_count,
        failed=failed_count,
        transcripts=len(transcripts),
        signals=len(signals),
    )

    return StepResult(
        outcome="complete",
        result_summary=(
            f"extraction: {extracted_count} facts extracted, "
            f"{failed_count} failed"
        ),
        request_count_delta=request_count,
    )


# ══════════════════════════════════════════════════════════════════════════
# Stage 2: Consolidate
# ══════════════════════════════════════════════════════════════════════════


async def consolidate_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Find semantically related notes, LLM-merge, soft-delete originals.

    1. ProjectionDB.consolidate(threshold=0.82) -> merge candidate groups
    2. For up to 3 groups: read, LLM consolidate, store new, soft-delete originals
    3. Shrinkage guard: reject if >40% shorter than sum of inputs
    """
    container, db_path = _resolve_services(task)
    projection_db = _get_projection_db(container)
    memory_store = _get_memory_store(container)
    write_pipeline = _get_write_pipeline(container)
    emitter = _get_event_emitter(container)

    request_count = 0
    consolidated_count = 0
    rejected_count = 0

    try:
        groups = await projection_db.consolidate(threshold=0.82)

        for group in groups[:MAX_CONSOLIDATION_GROUPS]:
            if len(group.records) < 2:
                continue

            try:
                # Read full content from filesystem
                note_contents: list[str] = []
                note_ids: list[str] = []
                for record in group.records:
                    note = await memory_store.read_note(record.id)
                    if note is not None:
                        note_contents.append(note.body)
                        note_ids.append(record.id)

                if len(note_contents) < 2:
                    continue

                # LLM consolidation
                notes_text = "\n\n---\n\n".join(
                    f"Note {i + 1} (ID: {note_ids[i]}):\n{content}"
                    for i, content in enumerate(note_contents)
                )

                user_prompt = CONSOLIDATION_USER_TEMPLATE.format(
                    notes=notes_text
                )
                consolidated_text = await _llm_call(
                    container, CONSOLIDATION_SYSTEM_PROMPT, user_prompt
                )
                request_count += 1

                # Shrinkage guard
                shrinkage = compute_shrinkage(note_contents, consolidated_text)
                if shrinkage > SHRINKAGE_REJECTION_THRESHOLD:
                    log.warning(
                        "consolidation_shrinkage_fallback",
                        shrinkage=f"{shrinkage:.2f}",
                        note_ids=note_ids,
                    )
                    consolidated_text = _deterministic_consolidation(
                        note_contents
                    )
                    fallback_shrinkage = compute_shrinkage(
                        note_contents, consolidated_text
                    )
                    if fallback_shrinkage > SHRINKAGE_REJECTION_THRESHOLD:
                        log.warning(
                            "consolidation_rejected_shrinkage",
                            shrinkage=f"{fallback_shrinkage:.2f}",
                            note_ids=note_ids,
                        )
                        rejected_count += 1
                        continue

                # Store consolidated note
                first_record = group.records[0]
                result = await write_pipeline.store(
                    content=consolidated_text,
                    memory_type=first_record.memory_type,
                    importance=max(r.importance for r in group.records),
                    tags=[],
                    skip_dedup=True,
                )

                # Soft-delete originals
                for note_id in note_ids:
                    await memory_store.soft_delete_note(
                        note_id=note_id,
                        reason="consolidated",
                        successor_id=result.note_id,
                        projection_db=projection_db,
                    )
                    if emitter is not None:
                        await emitter.emit(
                            EventType.MEMORY_SOFT_DELETED,
                            note_id=note_id,
                            reason="consolidated",
                            successor_id=result.note_id,
                        )

                consolidated_count += 1

            except Exception:
                log.exception(
                    "consolidation_group_failed",
                    note_ids=[r.id for r in group.records],
                )

    except Exception:
        log.exception("consolidate_step_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="consolidate_step_error",
            request_count_delta=request_count,
        )

    log.info(
        "consolidate_step_complete",
        task_id=task.id,
        consolidated=consolidated_count,
        rejected=rejected_count,
    )

    return StepResult(
        outcome="complete",
        result_summary=(
            f"consolidation: {consolidated_count} groups merged, "
            f"{rejected_count} rejected"
        ),
        request_count_delta=request_count,
    )


# ══════════════════════════════════════════════════════════════════════════
# Stage 3: Dedup
# ══════════════════════════════════════════════════════════════════════════


async def dedup_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Find near-duplicate pairs, LLM-confirm, merge with provenance.

    1. ProjectionDB.deduplicate(threshold=0.92) -> duplicate pairs
    2. For up to 5 pairs: read, LLM confirm, keep richer, soft-delete other
    3. If distinct: skip (mark as dedup_rejected so not re-checked)
    """
    container, db_path = _resolve_services(task)
    projection_db = _get_projection_db(container)
    memory_store = _get_memory_store(container)
    emitter = _get_event_emitter(container)

    request_count = 0
    deduped_count = 0
    skipped_count = 0

    try:
        # Load previously rejected pairs so we can exclude them
        rejected_pairs: set[tuple[str, str]] = set()
        try:
            async with aiosqlite.connect(str(db_path)) as db:
                cursor = await db.execute(
                    "SELECT id_a, id_b FROM dedup_rejected_pairs"
                )
                for row in await cursor.fetchall():
                    rejected_pairs.add((row[0], row[1]))
        except aiosqlite.OperationalError:
            pass  # Table may not exist yet in older DBs

        pairs = await projection_db.deduplicate(
            threshold=0.92,
            excluded_pairs=rejected_pairs,
        )

        for pair in pairs[:MAX_DEDUP_PAIRS]:
            try:
                # Read both notes
                note_a = await memory_store.read_note(pair.record_a.id)
                note_b = await memory_store.read_note(pair.record_b.id)

                if note_a is None or note_b is None:
                    continue

                body_a = " ".join(note_a.body.lower().split())
                body_b = " ".join(note_b.body.lower().split())
                if body_a and body_a == body_b:
                    result = {
                        "is_duplicate": True,
                        "reasoning": "exact normalized body match",
                    }
                else:
                    # LLM confirmation
                    user_prompt = DEDUP_USER_TEMPLATE.format(
                        note_a=note_a.body[:2000],
                        note_b=note_b.body[:2000],
                    )
                    response_text = await _llm_call(
                        container, DEDUP_SYSTEM_PROMPT, user_prompt
                    )
                    request_count += 1

                    result = parse_json_response(response_text)
                    if not isinstance(result, dict):
                        log.warning("dedup_llm_parse_failed", pair_ids=(pair.record_a.id, pair.record_b.id))
                        continue

                is_duplicate = result.get("is_duplicate", False)

                if is_duplicate:
                    # Pick richer note, soft-delete the other
                    richer, poorer = pick_richer_note(
                        {
                            "id": pair.record_a.id,
                            "content": note_a.body,
                            "importance": pair.record_a.importance,
                            "entities": pair.record_a.entities,
                            "created_at": pair.record_a.created_at,
                            "updated_at": pair.record_a.updated_at,
                        },
                        {
                            "id": pair.record_b.id,
                            "content": note_b.body,
                            "importance": pair.record_b.importance,
                            "entities": pair.record_b.entities,
                            "created_at": pair.record_b.created_at,
                            "updated_at": pair.record_b.updated_at,
                        },
                    )

                    poorer_id = poorer["id"]
                    richer_id = richer["id"]

                    # Soft-delete poorer note with merged_from provenance
                    await memory_store.soft_delete_note(
                        note_id=poorer_id,
                        reason="duplicate",
                        successor_id=richer_id,
                        projection_db=projection_db,
                    )

                    # Update richer note frontmatter with merged_from
                    await memory_store.update_frontmatter(
                        richer_id,
                        {"merged_from": [poorer_id]},
                    )

                    if emitter is not None:
                        await emitter.emit(
                            EventType.MEMORY_SOFT_DELETED,
                            note_id=poorer_id,
                            reason="duplicate",
                            successor_id=richer_id,
                        )

                    deduped_count += 1
                else:
                    # Persist the rejection so this pair is not re-evaluated
                    pair_key = tuple(sorted([pair.record_a.id, pair.record_b.id]))
                    now_ts = datetime.now(UTC).isoformat(timespec="seconds")
                    try:
                        async with aiosqlite.connect(str(db_path)) as db:
                            await db.execute(
                                "INSERT OR IGNORE INTO dedup_rejected_pairs "
                                "(id_a, id_b, rejected_at) VALUES (?, ?, ?)",
                                (pair_key[0], pair_key[1], now_ts),
                            )
                            await db.commit()
                    except aiosqlite.OperationalError:
                        log.warning("dedup_rejected_persist_failed")

                    log.debug(
                        "dedup_pair_distinct",
                        pair_ids=(pair.record_a.id, pair.record_b.id),
                        reasoning=result.get("reasoning", ""),
                    )
                    skipped_count += 1

            except Exception:
                log.exception(
                    "dedup_pair_failed",
                    pair_ids=(pair.record_a.id, pair.record_b.id),
                )

    except Exception:
        log.exception("dedup_step_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="dedup_step_error",
            request_count_delta=request_count,
        )

    log.info(
        "dedup_step_complete",
        task_id=task.id,
        deduped=deduped_count,
        skipped=skipped_count,
    )

    return StepResult(
        outcome="complete",
        result_summary=(
            f"dedup: {deduped_count} duplicates removed, "
            f"{skipped_count} distinct"
        ),
        request_count_delta=request_count,
    )


# ══════════════════════════════════════════════════════════════════════════
# Stage 4: Entity Resolution
# ══════════════════════════════════════════════════════════════════════════


async def entities_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Fuzzy-match entities, LLM-confirm merges, build relationship graph.

    1. Query all entities grouped by type
    2. Fuzzy name matching (Jaro-Winkler >= 0.85)
    3. For up to 5 candidate pairs: LLM confirm, merge if same
    4. Emit ENTITY_MERGED for confirmed merges
    """
    container, db_path = _resolve_services(task)
    projection_db = _get_projection_db(container)
    emitter = _get_event_emitter(container)

    request_count = 0
    merged_count = 0
    skipped_count = 0

    try:
        # Query entities through the projection DB for each common type
        candidate_pairs: list[tuple[dict, dict]] = []
        candidate_keys: set[tuple[str, str]] = set()

        for entity_type in ("person", "place", "project", "organization", "medication"):
            entities = await projection_db.get_entities_by_type(entity_type)
            if len(entities) < 2:
                continue

            # Find fuzzy match candidates
            for i in range(len(entities)):
                for j in range(i + 1, len(entities)):
                    e1 = entities[i]
                    e2 = entities[j]
                    similarity = jaro_winkler_similarity(
                        e1.canonical_name, e2.canonical_name
                    )
                    e1_payload = {
                        "id": e1.id,
                        "name": e1.name,
                        "canonical_name": e1.canonical_name,
                        "entity_type": e1.entity_type,
                        "link_count": e1.active_link_count,
                    }
                    e2_payload = {
                        "id": e2.id,
                        "name": e2.name,
                        "canonical_name": e2.canonical_name,
                        "entity_type": e2.entity_type,
                        "link_count": e2.active_link_count,
                    }
                    if (
                        similarity >= ENTITY_FUZZY_THRESHOLD
                        or _is_relationship_alias_pair(e1_payload, e2_payload)
                    ):
                        pair_key = tuple(sorted((e1.id, e2.id)))
                        if pair_key in candidate_keys:
                            continue
                        candidate_keys.add(pair_key)
                        candidate_pairs.append(
                            (e1_payload, e2_payload)
                        )

        # Process up to MAX_ENTITY_PAIRS
        for e1, e2 in candidate_pairs[:MAX_ENTITY_PAIRS]:
            try:
                # Get context notes for each entity
                memories_a = await projection_db.get_memories_by_entity(e1["name"])
                memories_b = await projection_db.get_memories_by_entity(e2["name"])

                context_a = "\n".join(
                    m.content[:200] for m in memories_a[:3]
                ) or "(no notes found)"
                context_b = "\n".join(
                    m.content[:200] for m in memories_b[:3]
                ) or "(no notes found)"

                if _relationship_alias_context_confirms(
                    e1, e2, context_a, context_b
                ):
                    is_same = True
                else:
                    # LLM confirmation
                    user_prompt = ENTITY_RESOLUTION_USER_TEMPLATE.format(
                        entity_a=e1["name"],
                        type_a=e1["entity_type"],
                        entity_b=e2["name"],
                        type_b=e2["entity_type"],
                        context_a=context_a,
                        context_b=context_b,
                    )
                    response_text = await _llm_call(
                        container, ENTITY_RESOLUTION_SYSTEM_PROMPT, user_prompt
                    )
                    request_count += 1

                    result = parse_json_response(response_text)
                    if not isinstance(result, dict):
                        log.warning(
                            "entity_resolution_parse_failed",
                            entities=(e1["name"], e2["name"]),
                        )
                        continue

                    is_same = result.get("is_same", False)

                if is_same:
                    target, source = _choose_entity_merge_target(e1, e2)

                    await projection_db.merge_entities(
                        source_id=source["id"],
                        target_id=target["id"],
                    )

                    if emitter is not None:
                        await emitter.emit(
                            EventType.ENTITY_MERGED,
                            source_id=source["id"],
                            source_name=source["name"],
                            target_id=target["id"],
                            target_name=target["name"],
                        )

                    merged_count += 1
                else:
                    skipped_count += 1

            except Exception:
                log.exception(
                    "entity_resolution_pair_failed",
                    entities=(e1["name"], e2["name"]),
                )

    except Exception:
        log.exception("entities_step_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="entities_step_error",
            request_count_delta=request_count,
        )

    log.info(
        "entities_step_complete",
        task_id=task.id,
        merged=merged_count,
        skipped=skipped_count,
    )

    return StepResult(
        outcome="complete",
        result_summary=(
            f"entity resolution: {merged_count} merged, "
            f"{skipped_count} distinct"
        ),
        request_count_delta=request_count,
    )


# ══════════════════════════════════════════════════════════════════════════
# Stage 5: Vault Handoff
# ══════════════════════════════════════════════════════════════════════════


async def vault_handoff_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Emit PIPELINE_COMPLETE to trigger post_memory_vault pipeline.

    Simple terminal stage that signals the Memory Steward is done.
    """
    try:
        container, _db_path = _resolve_services(task)
        emitter = _get_event_emitter(container)

        if emitter is not None:
            await emitter.emit(
                EventType.PIPELINE_COMPLETE,
                pipeline_name="post_session_memory",
                pipeline_instance_id=task.pipeline_instance_id,
            )

        log.info("vault_handoff_complete", task_id=task.id)

    except RuntimeError:
        # If runtime context is not set, just complete quietly
        log.warning("vault_handoff_no_context", task_id=task.id)

    # The dispatcher's sequence_complete trigger watches for
    # PIPELINE_COMPLETE events to fire post_memory_vault.
    return StepResult(
        outcome="complete",
        result_summary="vault_handoff: PIPELINE_COMPLETE emitted",
    )


# ══════════════════════════════════════════════════════════════════════════
# ADHD Profile Refinement (weekly_adhd_profile pipeline)
# ══════════════════════════════════════════════════════════════════════════


async def adhd_profile_refine_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Refine the user's ADHD profile based on observed patterns.

    Implements the user-edit preservation protocol (spec section 2e):
    1. mtime check: if file edited since last fire, enter merge mode
    2. locked_fields: never overwrite user-locked fields
    3. Merge mode conflicts: keep user values, write conflict report
    4. Atomic write discipline (temp file + rename)
    5. Default locked_fields: [] on first run
    """
    container, db_path = _resolve_services(task)
    memory_store = _get_memory_store(container)
    request_count = 0

    try:
        # Locate the ADHD profile directory
        base_path = memory_store._base
        adhd_dir = base_path / "User Model" / "adhd_profile"
        adhd_dir.mkdir(parents=True, exist_ok=True)

        # Find existing profile file
        profile_path: Path | None = None
        profile_content = ""
        profile_meta: dict[str, Any] = {}
        profile_body = ""

        profile_files = list(adhd_dir.glob("*.md"))
        if profile_files:
            profile_path = profile_files[0]
            raw = profile_path.read_text(encoding="utf-8")
            from kora_v2.memory.store import _parse_frontmatter

            profile_meta, profile_body = _parse_frontmatter(raw)
            profile_content = profile_body
        else:
            # First run: create default profile
            profile_meta = {
                "id": "adhd-profile",
                "memory_type": "user_model",
                "domain": "adhd_profile",
                "locked_fields": [],
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            profile_content = ""

        # Parse locked fields
        locked_fields: list[str] = profile_meta.get("locked_fields", [])
        if not isinstance(locked_fields, list):
            locked_fields = []

        # Determine merge mode via mtime check
        merge_mode = False
        if profile_path is not None:
            file_mtime = datetime.fromtimestamp(
                profile_path.stat().st_mtime, tz=UTC
            )
            # Check trigger_state for last_fired_at
            last_fired_at: datetime | None = None
            try:
                async with aiosqlite.connect(str(db_path)) as db:
                    cursor = await db.execute(
                        "SELECT last_fired_at FROM trigger_state "
                        "WHERE trigger_id = ?",
                        ("weekly_adhd_profile",),
                    )
                    row = await cursor.fetchone()
                    if row and row[0]:
                        last_fired_at = datetime.fromisoformat(row[0])
                        if last_fired_at.tzinfo is None:
                            last_fired_at = last_fired_at.replace(tzinfo=UTC)
            except (aiosqlite.OperationalError, ValueError):
                # Table or value missing — enter merge mode as safety measure
                merge_mode = True

            if last_fired_at is not None and file_mtime > last_fired_at:
                merge_mode = True
                log.info(
                    "adhd_profile_merge_mode",
                    file_mtime=file_mtime.isoformat(),
                    last_fired=last_fired_at.isoformat(),
                )

        # Query session data from last 7 days
        observed_patterns = await _gather_adhd_observations(db_path)

        # Build LLM prompt
        locked_fields_json = json.dumps(locked_fields) if locked_fields else "[]"
        merge_instruction = ""
        if merge_mode:
            merge_instruction = (
                "MERGE MODE: The user has edited this profile since the "
                "last refinement. For any field where your proposed value "
                "differs from the current value, keep the user's value. "
                "Return the profile with user values preserved."
            )

        user_prompt = ADHD_PROFILE_USER_TEMPLATE.format(
            current_profile=profile_content or "(empty — first run)",
            observed_patterns=observed_patterns or "(no data available)",
            merge_mode_instruction=merge_instruction,
        )

        system_prompt = ADHD_PROFILE_SYSTEM_PROMPT.format(
            locked_fields_json=locked_fields_json
        )

        response_text = await _llm_call(container, system_prompt, user_prompt)
        request_count += 1

        # Parse the new profile from LLM response
        new_profile_body = response_text.strip()

        # Strip YAML fences if present
        if new_profile_body.startswith("```"):
            lines = new_profile_body.split("\n")
            lines = [
                line
                for i, line in enumerate(lines)
                if not (
                    (i == 0 and line.startswith("```"))
                    or (i == len(lines) - 1 and line.strip() == "```")
                )
            ]
            new_profile_body = "\n".join(lines).strip()

        # Verify locked fields are preserved
        if locked_fields and profile_content:
            try:
                old_yaml = yaml.safe_load(profile_content) or {}
                new_yaml = yaml.safe_load(new_profile_body) or {}

                if isinstance(old_yaml, dict) and isinstance(new_yaml, dict):
                    for field in locked_fields:
                        if field in old_yaml:
                            if new_yaml.get(field) != old_yaml[field]:
                                log.info(
                                    "adhd_locked_field_drift_corrected",
                                    field=field,
                                )
                                new_yaml[field] = old_yaml[field]

                    new_profile_body = yaml.dump(
                        new_yaml,
                        default_flow_style=False,
                        sort_keys=False,
                        allow_unicode=True,
                    )
            except yaml.YAMLError:
                log.warning("adhd_profile_yaml_parse_failed")

        # Handle merge mode conflicts — revert conflicting fields to
        # user's values (spec §2e: "Keep the user's value as the
        # canonical value in the profile").
        if merge_mode and profile_content:
            conflicts = _detect_profile_conflicts(
                profile_content, new_profile_body, locked_fields
            )
            if conflicts:
                try:
                    old_yaml = yaml.safe_load(profile_content) or {}
                    new_yaml = yaml.safe_load(new_profile_body) or {}
                    if isinstance(old_yaml, dict) and isinstance(new_yaml, dict):
                        for conflict in conflicts:
                            field = conflict["field"]
                            new_yaml[field] = old_yaml[field]
                        new_profile_body = yaml.dump(
                            new_yaml,
                            default_flow_style=False,
                            sort_keys=False,
                            allow_unicode=True,
                        )
                except yaml.YAMLError:
                    log.warning("adhd_merge_conflict_revert_failed")
                _write_conflict_report(
                    base_path, conflicts, profile_content, new_profile_body
                )

        # Atomic write
        profile_meta["updated_at"] = datetime.now(UTC).isoformat(
            timespec="seconds"
        )
        profile_meta["locked_fields"] = locked_fields

        from kora_v2.memory.store import _render_note

        new_content = _render_note(profile_meta, new_profile_body)

        if profile_path is None:
            profile_path = adhd_dir / "adhd-profile.md"

        fd, tmp_path = tempfile.mkstemp(
            dir=str(profile_path.parent), suffix=".md.tmp"
        )
        try:
            os.write(fd, new_content.encode("utf-8"))
            os.close(fd)
            os.replace(tmp_path, str(profile_path))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        log.info(
            "adhd_profile_refined",
            task_id=task.id,
            merge_mode=merge_mode,
            locked_fields=locked_fields,
        )

    except RuntimeError:
        raise
    except Exception:
        log.exception("adhd_profile_refine_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="adhd_profile_refine_error",
            request_count_delta=request_count,
        )

    return StepResult(
        outcome="complete",
        result_summary=(
            f"adhd_profile: refined"
            f"{' (merge mode)' if merge_mode else ''}"
        ),
        request_count_delta=request_count,
    )


# ── ADHD Profile Helpers ────────────────────────────────────────────────


async def _gather_adhd_observations(db_path: Path) -> str:
    """Query session data from the last 7 days for ADHD pattern analysis.

    Returns a human-readable summary of observed patterns.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    observations: list[str] = []

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row

            # Session timing patterns
            cursor = await db.execute(
                "SELECT started_at, ended_at, turn_count, duration_seconds "
                "FROM sessions "
                "WHERE started_at > ? AND ended_at IS NOT NULL "
                "ORDER BY started_at",
                (cutoff,),
            )
            sessions = [dict(row) for row in await cursor.fetchall()]

            if sessions:
                durations = [
                    s["duration_seconds"]
                    for s in sessions
                    if s.get("duration_seconds")
                ]
                if durations:
                    avg_duration = sum(durations) / len(durations)
                    observations.append(
                        f"Session count: {len(sessions)}, "
                        f"avg duration: {avg_duration:.0f}s"
                    )

                # Time-of-day distribution
                hour_counts: dict[int, int] = {}
                for s in sessions:
                    try:
                        started = datetime.fromisoformat(s["started_at"])
                        hour_counts[started.hour] = (
                            hour_counts.get(started.hour, 0) + 1
                        )
                    except (ValueError, TypeError):
                        pass

                if hour_counts:
                    peak_hours = sorted(
                        hour_counts.items(), key=lambda x: x[1], reverse=True
                    )[:3]
                    observations.append(
                        "Peak activity hours: "
                        + ", ".join(
                            f"{h}:00 ({c} sessions)"
                            for h, c in peak_hours
                        )
                    )

            # Quality metrics (if available)
            try:
                cursor = await db.execute(
                    "SELECT metric_name, AVG(metric_value) as avg_val "
                    "FROM quality_metrics "
                    "WHERE recorded_at > ? "
                    "GROUP BY metric_name",
                    (cutoff,),
                )
                metrics = [dict(row) for row in await cursor.fetchall()]
                for m in metrics:
                    observations.append(
                        f"Avg {m['metric_name']}: {m['avg_val']:.2f}"
                    )
            except aiosqlite.OperationalError:
                pass

    except aiosqlite.OperationalError:
        observations.append("(could not query session data)")

    return "\n".join(observations) if observations else "(no recent data)"


def _detect_profile_conflicts(
    old_body: str,
    new_body: str,
    locked_fields: list[str],
) -> list[dict[str, Any]]:
    """Detect conflicts between user-edited and LLM-proposed values.

    Returns a list of conflict dicts with field, user_value, proposed_value.
    """
    conflicts: list[dict[str, Any]] = []

    try:
        old_yaml = yaml.safe_load(old_body) or {}
        new_yaml = yaml.safe_load(new_body) or {}
    except yaml.YAMLError:
        return []

    if not isinstance(old_yaml, dict) or not isinstance(new_yaml, dict):
        return []

    for key in old_yaml:
        if key in locked_fields:
            continue  # Locked fields are already handled
        if key in new_yaml and old_yaml[key] != new_yaml[key]:
            conflicts.append(
                {
                    "field": key,
                    "user_value": old_yaml[key],
                    "proposed_value": new_yaml[key],
                }
            )

    return conflicts


def _write_conflict_report(
    base_path: Path,
    conflicts: list[dict[str, Any]],
    old_body: str,
    new_body: str,
) -> None:
    """Write a conflict report to _KoraMemory/Inbox/.

    The report lists conflicting fields with user vs. proposed values
    and instructions for resolving them.
    """
    inbox = base_path / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    report_path = inbox / f"adhd-profile-conflicts-{date_str}.md"

    lines = [
        "---",
        f"created_at: {datetime.now(UTC).isoformat(timespec='seconds')}",
        "type: conflict_report",
        "source: weekly_adhd_profile",
        "---",
        "",
        "# ADHD Profile Conflict Report",
        "",
        "Kora's weekly ADHD profile refinement detected changes that "
        "conflict with your edits. Your values have been kept as canonical.",
        "",
        "## Conflicts",
        "",
    ]

    for conflict in conflicts:
        lines.append(f"### {conflict['field']}")
        lines.append(f"- **Your value:** {conflict['user_value']}")
        lines.append(f"- **Kora's proposed value:** {conflict['proposed_value']}")
        lines.append(
            "- *If you want Kora to take over this field again, remove it "
            "from `locked_fields` or delete this line from the profile.*"
        )
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")

    log.info(
        "adhd_conflict_report_written",
        path=str(report_path),
        conflict_count=len(conflicts),
    )
