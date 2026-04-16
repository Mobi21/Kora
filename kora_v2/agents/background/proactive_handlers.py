"""ProactiveAgent stage handlers -- Phase 8e.

Implements all 11 ProactiveAgent pipeline handlers plus the
``continuity_check`` and ``wake_up_preparation`` infrastructure
pipeline handlers. Each function matches the
``async (WorkerTask, StepContext) -> StepResult`` signature required
by the orchestration dispatcher.

Services are resolved via the process-level autonomous runtime context
(same pattern as Memory Steward handlers).

Handlers:

Area A -- Pattern-Based Noticing:
    - ``proactive_pattern_scan_step``

Area B -- Anticipatory Preparation:
    - ``anticipatory_prep_step``

Area C -- Research & Drafting:
    - ``proactive_research_step``
    - ``article_digest_step``
    - ``follow_through_draft_step``

Area D -- Contextual Engagement:
    - ``contextual_engagement_step``

Area E -- Background Support:
    - ``commitment_tracking_step``
    - ``stuck_detection_step``
    - ``weekly_triage_step``
    - ``draft_on_observation_step``
    - ``connection_making_step``

Infrastructure:
    - ``continuity_check_step``
    - ``wake_up_preparation_step``
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite
import structlog
import yaml

from kora_v2.autonomous.runtime_context import get_autonomous_context
from kora_v2.runtime.orchestration.worker_task import (
    StepContext,
    StepResult,
    WorkerTask,
)

if TYPE_CHECKING:
    from kora_v2.context.engine import ContextEngine
    from kora_v2.life.reminders import ReminderStore
    from kora_v2.runtime.orchestration.notifications import NotificationGate

log = structlog.get_logger(__name__)


# ---- Frontmatter helpers --------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown text.

    Mirrors the parser used in :mod:`kora_v2.memory.store`. Returns
    ``(meta_dict, body_text)``. If no valid frontmatter is found
    returns ``({}, full_text)``.
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    body = parts[2].lstrip("\n")
    return meta, body


def _render_with_frontmatter(meta: dict, body: str) -> str:
    """Render markdown with YAML frontmatter + body. Inverse of parse."""
    frontmatter = yaml.dump(
        meta, default_flow_style=False, sort_keys=False, allow_unicode=True,
    )
    return f"---\n{frontmatter}---\n\n{body}"


# ---- Service resolution (same pattern as memory_steward_handlers) --------


def _resolve_services(
    task: WorkerTask,
) -> tuple[Any, Path]:
    """Resolve container and db_path from the autonomous runtime context."""
    ctx = get_autonomous_context()
    if ctx is None:
        raise RuntimeError(
            "Proactive handler runtime context not set. "
            "OrchestrationEngine.start() must call set_autonomous_context() "
            "before dispatching proactive tasks."
        )
    return ctx.container, ctx.db_path


def _get_notification_gate(container: Any) -> NotificationGate | None:
    return getattr(container, "notification_gate", None)


def _get_context_engine(container: Any) -> ContextEngine | None:
    return getattr(container, "context_engine", None)


def _get_reminder_store(container: Any) -> ReminderStore | None:
    return getattr(container, "reminder_store", None)


async def _llm_call(container: Any, system: str, user: str) -> str:
    """Make a single LLM call via the container's provider."""
    llm = getattr(container, "llm", None)
    if llm is None:
        raise RuntimeError("LLM provider not available on container")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    response = await llm.chat(messages)
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        return response.get("content", response.get("text", str(response)))
    if hasattr(response, "content"):
        return str(response.content)
    return str(response)


async def _send_nudge(
    container: Any,
    template_id: str,
    **kwargs: Any,
) -> bool:
    """Send a templated notification. Returns True if delivered."""
    gate = _get_notification_gate(container)
    if gate is None:
        log.debug("nudge_skipped_no_gate", template_id=template_id)
        return False
    try:
        result = await gate.send_templated(template_id, **kwargs)
        return result.delivered
    except (KeyError, Exception):
        log.debug("nudge_delivery_failed", template_id=template_id)
        return False


async def _write_to_inbox(
    container: Any,
    filename: str,
    content: str,
) -> Path | None:
    """Write a markdown file to _KoraMemory/Inbox/. Returns the path."""
    store = getattr(container, "memory_store", None)
    if store is None:
        log.debug("inbox_write_skipped_no_store")
        return None
    base_path = getattr(store, "_base", None)
    if base_path is None:
        return None
    inbox = base_path / "Inbox"
    await asyncio.to_thread(inbox.mkdir, parents=True, exist_ok=True)
    path = inbox / filename
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")
    log.debug("inbox_file_written", path=str(path))
    return path


# ======================================================================
# Area A: Pattern-Based Noticing
# ======================================================================


async def proactive_pattern_scan_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Scan for actionable insights and deliver nudges.

    Triggered by INSIGHT_AVAILABLE, EMOTION_SHIFT_DETECTED,
    MEMORY_STORED, or interval. Calls ContextEngine.get_insights()
    and delivers a nudge for each actionable insight.
    """
    container, db_path = _resolve_services(task)
    engine = _get_context_engine(container)
    delivered = 0
    insights: list = []

    try:
        if engine is None:
            return StepResult(
                outcome="complete",
                result_summary="pattern_scan: no context engine available",
            )

        insights = await engine.get_insights(window_days=7, min_confidence=0.5)

        for insight in insights:
            nudge_text = (
                f"I noticed something: {insight.title}. {insight.description}"
            )
            sent = await _send_nudge(
                container,
                "reminder_generic",
                subject=nudge_text,
            )
            if sent:
                delivered += 1

    except RuntimeError:
        raise
    except Exception:
        log.exception("proactive_pattern_scan_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="proactive_pattern_scan_error",
        )

    log.info(
        "proactive_pattern_scan_complete",
        task_id=task.id,
        insights_found=len(insights),
        delivered=delivered,
    )

    return StepResult(
        outcome="complete",
        result_summary=f"pattern_scan: {delivered} nudges delivered",
    )


# ======================================================================
# Area B: Anticipatory Preparation
# ======================================================================


async def anticipatory_prep_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Check next 24h calendar events, build prep briefings.

    For events that need preparation (meetings with context,
    presentations), query memory for relevant past interactions
    and compose a preparation briefing.
    """
    container, db_path = _resolve_services(task)
    engine = _get_context_engine(container)
    request_count = 0
    briefings_written = 0

    try:
        if engine is None:
            return StepResult(
                outcome="complete",
                result_summary="anticipatory_prep: no context engine",
            )

        day_ctx = await engine.build_day_context()
        schedule = day_ctx.schedule

        # Filter to events in the next 24 hours that are actual events
        now = datetime.now(UTC)
        upcoming = [
            e for e in schedule
            if e.starts_at > now
            and (e.starts_at - now) < timedelta(hours=24)
            and e.kind not in ("buffer", "medication_window")
        ]

        if not upcoming:
            return StepResult(
                outcome="complete",
                result_summary="anticipatory_prep: no upcoming events",
            )

        # Query memory for relevant context per event
        projection_db = getattr(container, "projection_db", None)
        briefing_parts: list[str] = []

        for evt in upcoming[:5]:  # Cap at 5 events
            context_notes: list[str] = []

            if projection_db is not None:
                try:
                    results = await projection_db.search(
                        evt.title, limit=3
                    )
                    context_notes = [r.content[:200] for r in results]
                except Exception:
                    pass

            time_str = evt.starts_at.strftime("%H:%M")
            part = f"## {evt.title} at {time_str}\n"
            if context_notes:
                part += "**Related notes:**\n"
                for note in context_notes:
                    part += f"- {note}\n"
            else:
                part += "No related notes found.\n"
            briefing_parts.append(part)

        if briefing_parts:
            date_str = now.strftime("%Y-%m-%d")
            briefing = (
                f"---\ntype: anticipatory_prep\n"
                f"date: {date_str}\n"
                f"created_at: {now.isoformat(timespec='seconds')}\n---\n\n"
                f"# Preparation Briefing for {date_str}\n\n"
                + "\n".join(briefing_parts)
            )
            written = await _write_to_inbox(
                container,
                f"prep-briefing-{date_str}.md",
                briefing,
            )
            if written:
                briefings_written += 1

            # Notify if high-priority events
            if any(
                e.kind == "event" and (e.starts_at - now) < timedelta(hours=4)
                for e in upcoming
            ):
                await _send_nudge(
                    container,
                    "reminder_generic",
                    subject=(
                        f"Prep briefing ready for {len(upcoming)} "
                        f"upcoming event(s)"
                    ),
                )

    except RuntimeError:
        raise
    except Exception:
        log.exception("anticipatory_prep_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="anticipatory_prep_error",
            request_count_delta=request_count,
        )

    return StepResult(
        outcome="complete",
        result_summary=(
            f"anticipatory_prep: {briefings_written} briefing(s), "
            f"{len(upcoming) if engine else 0} events"
        ),
        request_count_delta=request_count,
    )


# ======================================================================
# Area C: Research & Drafting
# ======================================================================


async def proactive_research_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Conduct deep research dispatched by the user.

    Multi-step: queries memory, optionally uses web tools,
    builds an evolving working doc with findings. Uses checkpoint
    to track progress across steps.
    """
    container, db_path = _resolve_services(task)
    request_count = 0

    try:
        goal = task.goal or "Research task"

        # Check for existing progress via checkpoint
        checkpoint = task.checkpoint_blob
        progress = {}
        if checkpoint and checkpoint.scratch_state:
            progress = checkpoint.scratch_state

        step_index = progress.get("step_index", 0)
        findings: list[str] = progress.get("findings", [])

        if step_index == 0:
            # Step 1: Query memory for context
            projection_db = getattr(container, "projection_db", None)
            if projection_db is not None:
                try:
                    results = await projection_db.search(goal, limit=5)
                    for r in results:
                        findings.append(
                            f"Memory: {r.content[:300]}"
                        )
                except Exception:
                    pass

            step_index = 1

            # Update checkpoint
            if ctx.checkpoint_callback:
                await ctx.checkpoint_callback({
                    "step_index": step_index,
                    "findings": findings,
                })

            return StepResult(
                outcome="continue",
                progress_marker="Memory search complete, drafting report",
                request_count_delta=request_count,
            )

        if step_index == 1:
            # Step 2: LLM synthesis of findings
            if findings:
                findings_text = "\n".join(f"- {f}" for f in findings)
                system = (
                    "You are a research assistant. Synthesize the "
                    "following findings into a coherent research document."
                )
                user = (
                    f"Research goal: {goal}\n\n"
                    f"Findings:\n{findings_text}\n\n"
                    "Write a structured research document with sections "
                    "for key findings, connections, and open questions."
                )
                report = await _llm_call(container, system, user)
                request_count += 1
            else:
                report = (
                    f"# Research: {goal}\n\n"
                    "No relevant findings in memory. "
                    "Consider starting a conversation about this topic."
                )

            # Write to inbox
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
            await _write_to_inbox(
                container,
                f"research-{date_str}-{task.id[:8]}.md",
                (
                    f"---\ntype: research\n"
                    f"goal: {goal}\n"
                    f"created_at: {datetime.now(UTC).isoformat(timespec='seconds')}\n"
                    f"status: done\n---\n\n{report}"
                ),
            )

            await _send_nudge(
                container,
                "task_completed",
                goal=f"Research: {goal[:50]}",
                summary=f"Report with {len(findings)} source(s) written to Inbox",
            )

    except RuntimeError:
        raise
    except Exception:
        log.exception("proactive_research_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="proactive_research_error",
            request_count_delta=request_count,
        )

    return StepResult(
        outcome="complete",
        result_summary=f"research: report written with {len(findings)} sources",
        request_count_delta=request_count,
    )


async def article_digest_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Scan for saved content and produce digest summaries.

    Looks for unsummarized notes in the Inbox and produces a digest.
    """
    container, db_path = _resolve_services(task)
    request_count = 0
    digested = 0

    try:
        store = getattr(container, "memory_store", None)
        if store is None:
            return StepResult(
                outcome="complete",
                result_summary="article_digest: no memory store",
            )

        base_path = getattr(store, "_base", None)
        if base_path is None:
            return StepResult(
                outcome="complete",
                result_summary="article_digest: no base path",
            )

        inbox = base_path / "Inbox"
        if not inbox.exists():
            return StepResult(
                outcome="complete",
                result_summary="article_digest: empty inbox",
            )

        # Find undigested content (articles, bookmarks).
        # Skip files with a ``pipeline:`` frontmatter key — those are
        # Phase 7.5 LONG_BACKGROUND working documents and overwriting
        # them would corrupt mid-pipeline checkpoints (spec §3b/§3c).
        candidates: list[tuple[Path, dict, str]] = []
        for md_file in inbox.glob("*.md"):
            content = await asyncio.to_thread(md_file.read_text, encoding="utf-8")
            meta, body = _parse_frontmatter(content)
            if not isinstance(meta, dict):
                continue
            if "pipeline" in meta:
                # Working document for an active pipeline — never touch.
                continue
            if meta.get("type") != "article":
                continue
            if meta.get("digested") is True:
                continue
            candidates.append((md_file, meta, body))

        for article_path, meta, body in candidates[:3]:  # Process up to 3
            system = (
                "Summarize this article in 3-5 bullet points. "
                "Focus on key takeaways and actionable insights."
            )
            # Pass full original content (frontmatter + body) so the model
            # has the same context the previous implementation provided.
            full_text = await asyncio.to_thread(
                article_path.read_text, encoding="utf-8"
            )
            summary = await _llm_call(container, system, full_text[:4000])
            request_count += 1

            # Parse / edit / serialize: append digest to the body and set
            # ``digested: true`` in frontmatter. No string replacement.
            new_body = body.rstrip() + "\n\n## Digest\n" + summary + "\n"
            new_meta = dict(meta)
            new_meta["digested"] = True
            new_content = _render_with_frontmatter(new_meta, new_body)
            await asyncio.to_thread(
                article_path.write_text, new_content, encoding="utf-8"
            )
            digested += 1

    except RuntimeError:
        raise
    except Exception:
        log.exception("article_digest_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="article_digest_error",
            request_count_delta=request_count,
        )

    return StepResult(
        outcome="complete",
        result_summary=f"article_digest: {digested} articles summarized",
        request_count_delta=request_count,
    )


async def follow_through_draft_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Draft follow-through based on user's stated intent.

    Triggered by USER_STATED_INTENT. Creates action items or
    a first draft based on what the user said they would do.
    """
    container, db_path = _resolve_services(task)
    request_count = 0

    try:
        intent = task.goal or "Follow through on stated intent"

        system = (
            "The user expressed an intention to do something. Draft a "
            "concise action plan with 3-5 concrete next steps. Keep it "
            "practical and ADHD-friendly: small steps, clear outcomes."
        )
        user_msg = (
            f"The user said they intend to: {intent}\n\n"
            "Create a short action plan with specific next steps."
        )
        draft = await _llm_call(container, system, user_msg)
        request_count += 1

        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        await _write_to_inbox(
            container,
            f"follow-through-{date_str}-{task.id[:8]}.md",
            (
                f"---\ntype: follow_through\n"
                f"intent: {intent[:100]}\n"
                f"created_at: {datetime.now(UTC).isoformat(timespec='seconds')}\n"
                f"---\n\n# Follow-Through: {intent[:80]}\n\n{draft}"
            ),
        )

        await _send_nudge(
            container,
            "reminder_generic",
            subject=f"I drafted a follow-through plan for: {intent[:60]}",
        )

    except RuntimeError:
        raise
    except Exception:
        log.exception("follow_through_draft_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="follow_through_draft_error",
            request_count_delta=request_count,
        )

    return StepResult(
        outcome="complete",
        result_summary="follow_through: action plan drafted",
        request_count_delta=request_count,
    )


# ======================================================================
# Area D: Contextual Engagement
# ======================================================================


async def contextual_engagement_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Respond to contextual triggers with appropriate engagement.

    Triggered by EMOTION_SHIFT_DETECTED, TASK_LINGERING,
    OPEN_DECISION_POSED, or LONG_FOCUS_BLOCK_ENDED.
    """
    container, db_path = _resolve_services(task)
    delivered = 0

    try:
        # Determine which event triggered this pipeline
        trigger_hint = task.goal or ""

        if "emotion" in trigger_hint.lower():
            # Empathetic check-in for emotion shifts
            await _send_nudge(
                container,
                "reminder_generic",
                subject=(
                    "I noticed a shift in your energy. "
                    "How are you doing? No pressure to answer."
                ),
            )
            delivered += 1

        elif "lingering" in trigger_hint.lower():
            # Gentle help offer for stuck tasks
            await _send_nudge(
                container,
                "reminder_generic",
                subject=(
                    "A task seems to have been sitting for a while. "
                    "Want help breaking it down?"
                ),
            )
            delivered += 1

        elif "decision" in trigger_hint.lower():
            # Resurface open decision with context
            await _send_nudge(
                container,
                "reminder_generic",
                subject=(
                    "You have an open decision that might benefit "
                    "from a fresh look. Want to revisit it?"
                ),
            )
            delivered += 1

        elif "focus" in trigger_hint.lower():
            # Acknowledge end of focus block
            await _send_nudge(
                container,
                "reminder_generic",
                subject=(
                    "Nice focus session! Consider taking a short "
                    "break before diving back in."
                ),
            )
            delivered += 1

        else:
            # Generic contextual engagement based on DB state
            async with aiosqlite.connect(str(db_path)) as db:
                db.row_factory = aiosqlite.Row

                # Check for lingering tasks
                cutoff = (
                    datetime.now(UTC) - timedelta(days=3)
                ).isoformat()
                cursor = await db.execute(
                    "SELECT title FROM items "
                    "WHERE status = 'in_progress' AND created_at < ? "
                    "LIMIT 1",
                    (cutoff,),
                )
                row = await cursor.fetchone()
                if row:
                    await _send_nudge(
                        container,
                        "reminder_generic",
                        subject=(
                            f"'{row['title']}' has been in progress "
                            f"for a few days. Want help with it?"
                        ),
                    )
                    delivered += 1

    except RuntimeError:
        raise
    except Exception:
        log.exception("contextual_engagement_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="contextual_engagement_error",
        )

    return StepResult(
        outcome="complete",
        result_summary=f"contextual_engagement: {delivered} messages sent",
    )


# ======================================================================
# Area E: Background Support
# ======================================================================


async def commitment_tracking_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Daily scan of transcripts for commitments and promises.

    Runs at 01:00. Cross-references with calendar and tasks.
    Surfaces untracked commitments in morning briefing.
    """
    container, db_path = _resolve_services(task)
    request_count = 0
    commitments_found = 0

    try:
        # Query recent session transcripts (last 24h)
        cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()

        transcripts_text = ""
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT messages FROM session_transcripts "
                "WHERE created_at > ? ORDER BY created_at DESC LIMIT 5",
                (cutoff,),
            )
            rows = await cursor.fetchall()

            for row in rows:
                messages_json = row["messages"]
                if messages_json:
                    try:
                        messages = json.loads(messages_json)
                        for msg in messages:
                            if msg.get("role") == "user":
                                transcripts_text += msg.get("content", "") + "\n"
                    except (json.JSONDecodeError, TypeError):
                        pass

        if not transcripts_text.strip():
            return StepResult(
                outcome="complete",
                result_summary="commitment_tracking: no recent transcripts",
            )

        # LLM extraction of commitments
        system = (
            "Extract any commitments, promises, or stated intentions "
            "from the user's messages. Return a JSON array of objects "
            "with 'commitment' and 'urgency' (high/medium/low) fields. "
            "If none found, return []."
        )
        response = await _llm_call(
            container, system, transcripts_text[:4000]
        )
        request_count += 1

        # Parse commitments
        commitments: list[dict[str, str]] = []
        try:
            # Try to extract JSON from response
            text = response.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(
                    line for line in lines
                    if not line.strip().startswith("```")
                )
            parsed = json.loads(text)
            if isinstance(parsed, list):
                commitments = [
                    c for c in parsed
                    if isinstance(c, dict) and "commitment" in c
                ]
        except (json.JSONDecodeError, TypeError):
            pass

        commitments_found = len(commitments)

        if commitments:
            # Write commitments to inbox for morning briefing
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
            lines = [
                f"---\ntype: commitments\ndate: {date_str}\n"
                f"created_at: {datetime.now(UTC).isoformat(timespec='seconds')}\n---\n",
                f"# Commitments Found ({date_str})\n",
            ]
            for c in commitments:
                urgency = c.get("urgency", "medium")
                lines.append(
                    f"- [{urgency.upper()}] {c['commitment']}"
                )

            await _write_to_inbox(
                container,
                f"commitments-{date_str}.md",
                "\n".join(lines),
            )

    except RuntimeError:
        raise
    except Exception:
        log.exception("commitment_tracking_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="commitment_tracking_error",
            request_count_delta=request_count,
        )

    return StepResult(
        outcome="complete",
        result_summary=(
            f"commitment_tracking: {commitments_found} commitments found"
        ),
        request_count_delta=request_count,
    )


async def stuck_detection_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Detect tasks that haven't progressed and offer help.

    Every 6 hours during idle. Checks for tasks with stale
    updated_at and working docs that haven't changed.
    """
    container, db_path = _resolve_services(task)
    stuck_count = 0

    try:
        # Check for items stuck in 'in_progress' for > 2 days
        cutoff = (datetime.now(UTC) - timedelta(days=2)).isoformat()

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, title, created_at FROM items "
                "WHERE status = 'in_progress' AND created_at < ? "
                "ORDER BY created_at ASC LIMIT 3",
                (cutoff,),
            )
            stuck_items = await cursor.fetchall()

        for item in stuck_items:
            stuck_count += 1
            title = item["title"]
            await _send_nudge(
                container,
                "reminder_generic",
                subject=(
                    f"'{title}' hasn't moved in a while. "
                    f"Want help breaking it into smaller steps?"
                ),
            )

        # Also check working docs for staleness
        store = getattr(container, "memory_store", None)
        if store is not None:
            base_path = getattr(store, "_base", None)
            if base_path is not None:
                inbox = base_path / "Inbox"
                if inbox.exists():
                    stale_cutoff = datetime.now(UTC) - timedelta(days=3)
                    for md_file in inbox.glob("*.md"):
                        try:
                            mtime = datetime.fromtimestamp(
                                md_file.stat().st_mtime, tz=UTC
                            )
                            if mtime < stale_cutoff:
                                content = await asyncio.to_thread(
                                    md_file.read_text, encoding="utf-8"
                                )
                                if "status: in_progress" in content:
                                    stuck_count += 1
                        except (OSError, ValueError):
                            pass

    except RuntimeError:
        raise
    except Exception:
        log.exception("stuck_detection_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="stuck_detection_error",
        )

    return StepResult(
        outcome="complete",
        result_summary=f"stuck_detection: {stuck_count} stuck items found",
    )


async def weekly_triage_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Weekly review of open decisions, lingering tasks, routines.

    Runs weekly at 09:00. Composes a summary with action suggestions.
    """
    container, db_path = _resolve_services(task)
    request_count = 0

    try:
        now = datetime.now(UTC)
        week_ago = (now - timedelta(days=7)).isoformat()

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row

            # Open tasks
            cursor = await db.execute(
                "SELECT title, status, created_at FROM items "
                "WHERE status NOT IN ('done', 'cancelled') "
                "ORDER BY priority ASC LIMIT 10"
            )
            open_items = [dict(row) for row in await cursor.fetchall()]

            # Routine completion this week
            cursor = await db.execute(
                "SELECT r.name, rs.status FROM routine_sessions rs "
                "JOIN routines r ON r.id = rs.routine_id "
                "WHERE rs.started_at > ?",
                (week_ago,),
            )
            routine_data = [dict(row) for row in await cursor.fetchall()]

            # Session count
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM sessions WHERE started_at > ?",
                (week_ago,),
            )
            session_row = await cursor.fetchone()
            session_count = session_row["cnt"] if session_row else 0

        # Build weekly summary
        date_str = now.strftime("%Y-%m-%d")
        lines = [
            f"---\ntype: weekly_triage\ndate: {date_str}\n"
            f"created_at: {now.isoformat(timespec='seconds')}\n---\n",
            f"# Weekly Triage ({date_str})\n",
            f"## Sessions This Week: {session_count}\n",
        ]

        # Open items section
        if open_items:
            lines.append("## Open Tasks\n")
            for item in open_items:
                lines.append(f"- [{item['status']}] {item['title']}")
            lines.append("")

        # Routine section
        if routine_data:
            completed = sum(
                1 for r in routine_data if r["status"] == "completed"
            )
            total = len(routine_data)
            lines.append(
                f"## Routines: {completed}/{total} completed this week\n"
            )

        # Suggestions
        lines.append("## Suggested Actions\n")
        stale_items = [
            i for i in open_items
            if i["status"] == "in_progress"
        ]
        if stale_items:
            lines.append(
                f"- Review {len(stale_items)} in-progress task(s) "
                f"that may need attention"
            )
        if session_count < 3:
            lines.append("- Low session count this week -- check in?")

        await _write_to_inbox(
            container,
            f"weekly-triage-{date_str}.md",
            "\n".join(lines),
        )

        await _send_nudge(
            container,
            "reminder_generic",
            subject="Your weekly triage summary is ready in the Inbox",
        )

    except RuntimeError:
        raise
    except Exception:
        log.exception("weekly_triage_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="weekly_triage_error",
            request_count_delta=request_count,
        )

    return StepResult(
        outcome="complete",
        result_summary="weekly_triage: summary written to Inbox",
        request_count_delta=request_count,
    )


async def draft_on_observation_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Draft something the user expressed wanting.

    Triggered by USER_STATED_NEED. Creates a ready-to-use draft
    (message, outline, plan) and writes it to the Inbox.
    """
    container, db_path = _resolve_services(task)
    request_count = 0

    try:
        need = task.goal or "Draft based on observation"

        system = (
            "The user expressed a need or desire. Create a practical, "
            "ready-to-use draft that addresses it. Be concise and helpful. "
            "Format as markdown."
        )
        user_msg = f"The user needs: {need}\n\nCreate a useful draft."
        draft = await _llm_call(container, system, user_msg)
        request_count += 1

        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        await _write_to_inbox(
            container,
            f"draft-{date_str}-{task.id[:8]}.md",
            (
                f"---\ntype: draft\n"
                f"need: {need[:100]}\n"
                f"created_at: {datetime.now(UTC).isoformat(timespec='seconds')}\n"
                f"---\n\n# Draft: {need[:80]}\n\n{draft}"
            ),
        )

        await _send_nudge(
            container,
            "reminder_generic",
            subject=f"I drafted something for you: {need[:60]}",
        )

    except RuntimeError:
        raise
    except Exception:
        log.exception("draft_on_observation_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="draft_on_observation_error",
            request_count_delta=request_count,
        )

    return StepResult(
        outcome="complete",
        result_summary="draft_on_observation: draft written to Inbox",
        request_count_delta=request_count,
    )


async def connection_making_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Find connections between recent and old memory notes.

    Daily at 03:00. Scans recent memory writes, finds old vault
    notes that connect to new topics, composes nudges.
    """
    container, db_path = _resolve_services(task)
    connections_found = 0
    request_count = 0

    try:
        projection_db = getattr(container, "projection_db", None)
        if projection_db is None:
            return StepResult(
                outcome="complete",
                result_summary="connection_making: no projection DB",
            )

        # Get recent memories (last 24h)
        cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            # The projection DB has memories; query the operational DB for
            # recent signal_queue entries to find recent topics
            cursor = await db.execute(
                "SELECT DISTINCT message_text FROM signal_queue "
                "WHERE created_at > ? AND message_text IS NOT NULL "
                "LIMIT 5",
                (cutoff,),
            )
            recent_topics = [
                row["message_text"][:200]
                for row in await cursor.fetchall()
            ]

        if not recent_topics:
            # Fallback: try session transcripts
            async with aiosqlite.connect(str(db_path)) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT messages FROM session_transcripts "
                    "WHERE created_at > ? LIMIT 3",
                    (cutoff,),
                )
                for row in await cursor.fetchall():
                    try:
                        msgs = json.loads(row["messages"] or "[]")
                        for msg in msgs[-3:]:
                            if msg.get("role") == "user":
                                recent_topics.append(
                                    msg.get("content", "")[:200]
                                )
                    except (json.JSONDecodeError, TypeError):
                        pass

        # For each recent topic, search for old connections
        for topic in recent_topics[:3]:
            if not topic.strip():
                continue
            try:
                results = await projection_db.search(topic, limit=3)
                # Filter for notes older than 7 days
                old_results = []
                cutoff_dt = datetime.now(UTC) - timedelta(days=7)
                for r in results:
                    created = getattr(r, "created_at", None)
                    if created is not None:
                        if isinstance(created, str):
                            try:
                                created = datetime.fromisoformat(created)
                            except ValueError:
                                continue
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=UTC)
                        if created < cutoff_dt:
                            old_results.append(r)

                if old_results:
                    connections_found += 1
                    old_note = old_results[0]
                    preview = old_note.content[:100]
                    await _send_nudge(
                        container,
                        "reminder_generic",
                        subject=(
                            f"Did you know you also wrote about this? "
                            f"\"{preview}...\""
                        ),
                    )

            except Exception:
                log.debug("connection_search_failed", topic=topic[:50])

    except RuntimeError:
        raise
    except Exception:
        log.exception("connection_making_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="connection_making_error",
            request_count_delta=request_count,
        )

    return StepResult(
        outcome="complete",
        result_summary=(
            f"connection_making: {connections_found} connections found"
        ),
        request_count_delta=request_count,
    )


# ======================================================================
# Infrastructure Pipelines
# ======================================================================


async def continuity_check_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Poll for due reminders and routine nudges.

    Every 5 minutes: check ReminderStore.get_due_reminders() and
    get_routine_nudges() for medication windows and routines.
    Deliver due reminders via NotificationGate.
    """
    container, db_path = _resolve_services(task)
    delivered = 0

    try:
        # 1. Check due reminders
        reminder_store = _get_reminder_store(container)
        if reminder_store is not None:
            due_reminders = await reminder_store.get_due_reminders(
                window=timedelta(minutes=15)
            )
            for reminder in due_reminders:
                sent = await _send_nudge(
                    container,
                    "reminder_generic",
                    subject=f"{reminder.title}: {reminder.description}",
                )
                if sent:
                    # Atomic: mark delivered AND schedule the next
                    # occurrence (if recurring) in one transaction so a
                    # crash between the two cannot drop the next run.
                    await reminder_store.deliver_and_reschedule(
                        reminder.id
                    )
                    delivered += 1

        # 2. Check routine nudges
        engine = _get_context_engine(container)
        if engine is not None:
            try:
                day_ctx = await engine.build_day_context()

                from kora_v2.life.routines import get_routine_nudges

                nudges = await get_routine_nudges(db_path, day_ctx)
                for nudge in nudges:
                    if nudge.urgency in ("medium", "high"):
                        await _send_nudge(
                            container,
                            "reminder_generic",
                            subject=nudge.message,
                        )
                        delivered += 1
            except Exception:
                log.debug("continuity_routine_nudge_error")

    except RuntimeError:
        raise
    except Exception:
        log.exception("continuity_check_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="continuity_check_error",
        )

    return StepResult(
        outcome="complete",
        result_summary=f"continuity_check: {delivered} reminders/nudges delivered",
    )


async def wake_up_preparation_step(
    task: WorkerTask, ctx: StepContext
) -> StepResult:
    """Build morning briefing and deliver at wake time.

    Assembles: today's schedule, pending reminders, recent insights,
    overnight task completions. Writes to Inbox and delivers a
    summary notification.
    """
    container, db_path = _resolve_services(task)
    request_count = 0

    try:
        now = datetime.now(UTC)
        today = now.date()
        date_str = today.isoformat()

        # 1. Build schedule section
        engine = _get_context_engine(container)
        schedule_section = "## Today's Schedule\n\nNo events loaded.\n"
        energy_section = ""
        items_section = ""

        if engine is not None:
            try:
                day_ctx = await engine.build_day_context(target_date=today)

                if day_ctx.schedule:
                    lines = []
                    for evt in day_ctx.schedule[:10]:
                        time_str = evt.starts_at.strftime("%H:%M")
                        lines.append(f"- {time_str} {evt.title}")
                    schedule_section = (
                        "## Today's Schedule\n\n"
                        + "\n".join(lines)
                        + "\n"
                    )

                if day_ctx.energy:
                    energy_section = (
                        f"\n## Energy Estimate\n\n"
                        f"Level: {day_ctx.energy.level}, "
                        f"Focus: {day_ctx.energy.focus}\n"
                    )

                if day_ctx.items_due:
                    item_lines = [
                        f"- {i['title']} ({i['status']})"
                        for i in day_ctx.items_due[:5]
                    ]
                    items_section = (
                        "\n## Items Due Today\n\n"
                        + "\n".join(item_lines)
                        + "\n"
                    )
            except Exception:
                log.debug("wake_up_day_context_error")

        # 2. Pending reminders
        reminder_section = ""
        reminder_store = _get_reminder_store(container)
        if reminder_store is not None:
            try:
                pending = await reminder_store.get_pending()
                if pending:
                    lines = [
                        f"- {r.title} (due: {r.due_at.strftime('%H:%M')})"
                        for r in pending[:5]
                    ]
                    reminder_section = (
                        "\n## Pending Reminders\n\n"
                        + "\n".join(lines)
                        + "\n"
                    )
            except Exception:
                log.debug("wake_up_reminders_error")

        # 3. Recent insights
        insight_section = ""
        if engine is not None:
            try:
                insights = await engine.get_insights(
                    window_days=7, min_confidence=0.6
                )
                if insights:
                    lines = [f"- {i.title}" for i in insights[:3]]
                    insight_section = (
                        "\n## Recent Insights\n\n"
                        + "\n".join(lines)
                        + "\n"
                    )
            except Exception:
                log.debug("wake_up_insights_error")

        # 4. Overnight completions
        completion_section = ""
        overnight_cutoff = (now - timedelta(hours=8)).isoformat()
        try:
            async with aiosqlite.connect(str(db_path)) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT title FROM items "
                    "WHERE status = 'done' AND created_at > ? "
                    "LIMIT 5",
                    (overnight_cutoff,),
                )
                completions = await cursor.fetchall()
                if completions:
                    lines = [f"- {r['title']}" for r in completions]
                    completion_section = (
                        "\n## Completed Overnight\n\n"
                        + "\n".join(lines)
                        + "\n"
                    )
        except Exception:
            log.debug("wake_up_completions_error")

        # Assemble briefing
        briefing = (
            f"---\ntype: morning_briefing\ndate: {date_str}\n"
            f"created_at: {now.isoformat(timespec='seconds')}\n---\n\n"
            f"# Good Morning ({date_str})\n\n"
            f"{schedule_section}"
            f"{energy_section}"
            f"{items_section}"
            f"{reminder_section}"
            f"{insight_section}"
            f"{completion_section}"
        )

        await _write_to_inbox(
            container,
            f"morning-briefing-{date_str}.md",
            briefing,
        )

        # Deliver summary notification — count events from schedule_section
        event_lines = [
            line for line in schedule_section.split("\n")
            if line.strip().startswith("- ")
        ]
        await _send_nudge(
            container,
            "background_digest_ready",
            count=len(event_lines),
        )

    except RuntimeError:
        raise
    except Exception:
        log.exception("wake_up_preparation_error", task_id=task.id)
        return StepResult(
            outcome="failed",
            error_message="wake_up_preparation_error",
            request_count_delta=request_count,
        )

    return StepResult(
        outcome="complete",
        result_summary=f"wake_up: morning briefing for {date_str} written",
        request_count_delta=request_count,
    )
