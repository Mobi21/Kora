"""Background work item factories for the daemon's BackgroundWorker.

Each factory function creates a WorkItem instance configured for a
specific background task. Called during daemon startup to register
items with the BackgroundWorker.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import structlog

from kora_v2.daemon.worker import WorkItem

log = structlog.get_logger(__name__)


def make_memory_consolidation_item(container: Any) -> WorkItem:
    """Coalesce recent memory writes, dedup projection DB, backfill embeddings."""

    async def handler() -> None:
        fs_store = getattr(container, "memory_store", None)
        if fs_store and hasattr(fs_store, "consolidate"):
            await fs_store.consolidate()

        proj_db = getattr(container, "projection_db", None)
        if proj_db and hasattr(proj_db, "deduplicate"):
            await proj_db.deduplicate()

        embedding_svc = getattr(container, "embedding_service", None)
        if embedding_svc and hasattr(embedding_svc, "backfill_missing"):
            await embedding_svc.backfill_missing()

        log.info("work_item_completed", item="memory_consolidation")

    return WorkItem(
        name="memory_consolidation",
        priority=1,
        tier="idle",
        interval_seconds=600,
        handler=handler,
    )


def make_signal_scanner_item(container: Any) -> WorkItem:
    """Scan recent conversation turns for extractable facts."""

    async def handler() -> None:
        scanner = getattr(container, "signal_scanner", None)
        session_mgr = getattr(container, "session_manager", None)
        if not scanner or not session_mgr:
            return

        if hasattr(session_mgr, "load_last_bridge"):
            bridge = await session_mgr.load_last_bridge()
            if bridge and hasattr(scanner, "scan_bridge"):
                await scanner.scan_bridge(bridge)

        log.info("work_item_completed", item="signal_scanner")

    return WorkItem(
        name="signal_scanner",
        priority=2,
        tier="safe",
        interval_seconds=120,
        handler=handler,
    )


def make_autonomous_update_item(container: Any) -> WorkItem:
    """Check for undelivered autonomous updates and emit events."""

    async def handler() -> None:
        db_path = container.settings.data_dir / "operational.db"
        if not db_path.exists():
            return

        import aiosqlite

        from kora_v2.core.events import EventType

        try:
            async with aiosqlite.connect(str(db_path)) as db:
                # Check if autonomous_updates table exists
                cur = await db.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='autonomous_updates'"
                )
                if not await cur.fetchone():
                    return

                cur = await db.execute(
                    "SELECT id, plan_id, update_type, summary, delivered "
                    "FROM autonomous_updates WHERE delivered = 0"
                )
                rows = await cur.fetchall()

                if not rows:
                    return

                emitter = getattr(container, "event_emitter", None)
                for row in rows:
                    if emitter:
                        await emitter.emit(
                            EventType.NOTIFICATION_SENT,
                            update_id=row[0],
                            plan_id=row[1],
                            update_type=row[2],
                            summary=row[3],
                        )
                    await db.execute(
                        "UPDATE autonomous_updates SET delivered = 1 WHERE id = ?",
                        (row[0],),
                    )
                await db.commit()

            log.info(
                "work_item_completed",
                item="autonomous_updates",
                count=len(rows),
            )
        except Exception:
            log.debug("autonomous_update_check_failed", exc_info=True)

    return WorkItem(
        name="autonomous_update_delivery",
        priority=3,
        tier="safe",
        interval_seconds=30,
        handler=handler,
    )


def make_bridge_pruning_item(container: Any) -> WorkItem:
    """Delete session bridge files older than retention period."""

    async def handler() -> None:
        memory_cfg = getattr(container.settings, "memory", None)
        memory_path: Path | None = None
        if memory_cfg and hasattr(memory_cfg, "kora_memory_path"):
            memory_path = Path(memory_cfg.kora_memory_path)
        if not memory_path:
            memory_path = container.settings.data_dir

        bridges_dir = memory_path / ".kora" / "bridges"
        if not bridges_dir.exists():
            return

        retention_days = 30
        cutoff = time.time() - (retention_days * 86400)

        pruned = 0
        for bridge_file in bridges_dir.glob("*.md"):
            try:
                if bridge_file.stat().st_mtime < cutoff:
                    bridge_file.unlink()
                    pruned += 1
            except OSError:
                continue

        if pruned:
            log.info(
                "work_item_completed",
                item="bridge_pruning",
                pruned=pruned,
            )

    return WorkItem(
        name="session_bridge_pruning",
        priority=4,
        tier="idle",
        interval_seconds=3600,
        handler=handler,
    )


def make_skill_refinement_item(container: Any) -> WorkItem:
    """Periodic LLM review of skill YAML effectiveness."""

    _state: dict[str, int] = {"last_idx": -1}

    async def handler() -> None:
        skill_loader = getattr(container, "_skill_loader", None)
        llm = getattr(container, "llm", None)
        if not skill_loader or not llm:
            return

        skills = skill_loader.get_all_skills()
        if not skills:
            return

        _state["last_idx"] = (_state["last_idx"] + 1) % len(skills)
        skill = skills[_state["last_idx"]]

        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a skill quality reviewer. Evaluate this skill "
                        "definition for clarity, completeness, and specificity. "
                        "Return a brief assessment (max 200 words)."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Skill: {skill.name}\n"
                        f"Display name: {skill.display_name}\n"
                        f"Tools: {skill.tools}\n"
                        f"Guidance:\n{skill.guidance}"
                    ),
                },
            ]
            await llm.generate(messages=messages, max_tokens=500)
            log.info(
                "work_item_completed",
                item="skill_refinement",
                skill=skill.name,
            )
        except Exception:
            log.debug("skill_refinement_failed", skill=skill.name)

    return WorkItem(
        name="skill_refinement",
        priority=5,
        tier="idle",
        interval_seconds=86400,
        handler=handler,
    )
