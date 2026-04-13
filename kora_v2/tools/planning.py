"""Kora-led life planning tools (Phase 5).

Provides the LLM-facing planning surface:

* ``draft_plan`` — Kora drafts a plan from live DayContext/LifeContext.
* ``update_plan`` — deterministic ripple analysis for plan changes.
* ``day_briefing`` — full DayContext rendered as readable text.
* ``create_item`` / ``complete_item`` / ``defer_item`` / ``query_items``
  — thin wrappers over the ``items`` table.
* ``life_summary`` — LifeContext wrapper for flexible time ranges.

Time correction (1.5x multiplier) is applied in exactly one place —
``apply_time_correction`` — so it can never be double-applied.

Note: from __future__ import annotations is intentionally omitted so
the @tool decorator can introspect pydantic input types at runtime.
"""

import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import aiosqlite
import structlog
from pydantic import BaseModel, Field

from kora_v2.adhd.profile import ADHDProfile
from kora_v2.tools.registry import tool
from kora_v2.tools.types import AuthLevel, ToolCategory

log = structlog.get_logger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _ok(payload: dict[str, Any]) -> str:
    payload.setdefault("success", True)
    return json.dumps(payload, default=str)


def _err(message: str) -> str:
    return json.dumps({"success": False, "error": message})


def _get_db_path(container: Any):
    settings = getattr(container, "settings", None)
    if settings is None:
        return None
    data_dir = getattr(settings, "data_dir", None)
    if data_dir is None:
        return None
    return data_dir / "operational.db"


def _get_profile(container: Any) -> ADHDProfile:
    """Return the container's live ``ADHDProfile``, or defaults."""
    profile = getattr(container, "adhd_profile", None)
    if isinstance(profile, ADHDProfile):
        return profile
    return ADHDProfile()


def apply_time_correction(minutes: int, profile: ADHDProfile) -> int:
    """Apply the ADHD time-correction multiplier to a minute estimate.

    This is the **only** place the multiplier is applied for life-
    planning tools — do not reimplement it elsewhere. The planner
    worker (agent execution planning) deliberately does NOT apply this
    multiplier, since those plans are agent-executed rather than
    user-executed.
    """
    if minutes <= 0:
        return minutes
    corrected = int(round(minutes * profile.time_correction_factor))
    return max(minutes, corrected)


# ── Natural-language scope parsing ──────────────────────────────────────────


def _parse_scope_window(scope: str) -> tuple[date, date, str]:
    """Parse a scope string into (since, until, label).

    Supports a small fixed vocabulary: 'today', 'tomorrow', 'this week',
    'next week', 'last N days', 'until <weekday>'. Unknown scopes fall
    back to today.
    """
    today = datetime.now(UTC).date()
    s = scope.lower().strip()
    if s in ("", "today"):
        return today, today, "today"
    if s == "tomorrow":
        return today + timedelta(days=1), today + timedelta(days=1), "tomorrow"
    if s == "this week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6), "this week"
    if s == "next week":
        start = today - timedelta(days=today.weekday()) + timedelta(days=7)
        return start, start + timedelta(days=6), "next week"
    m = re.match(r"last\s+(\d+)\s*days?", s)
    if m:
        n = int(m.group(1))
        return today - timedelta(days=n), today, f"last {n} days"
    m = re.match(r"until\s+(\w+)", s)
    if m:
        target = m.group(1).strip().lower()
        weekdays = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]
        if target in weekdays:
            target_idx = weekdays.index(target)
            days_ahead = (target_idx - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return today, today + timedelta(days=days_ahead), f"until {target}"
    return today, today, scope


# ── Input models ────────────────────────────────────────────────────────────


class DraftPlanInput(BaseModel):
    scope: str = Field(
        "today", description="Natural-language scope: 'today', 'this week', etc."
    )
    goal: str = Field("", description="Optional focus area or goal")


class UpdatePlanInput(BaseModel):
    summary: str = Field(..., description="Human-readable description of the change")
    affected_entry_ids: list[str] = Field(
        default_factory=list, description="Calendar entry IDs being modified"
    )
    action: Literal["delete", "reschedule", "shrink"] = Field(
        ..., description="What to do with each affected entry"
    )
    reschedule_to: str = Field(
        "",
        description="ISO datetime to move entries to (required for 'reschedule')",
    )
    shrink_to_minutes: int = Field(
        0, description="Target duration in minutes (required for 'shrink')"
    )


class DayBriefingInput(BaseModel):
    date: str = Field("", description="ISO date (default today)")


class CreateItemInput(BaseModel):
    title: str = Field(..., description="Item title")
    description: str = Field("", description="Optional description")
    due_date: str = Field("", description="ISO date")
    priority: int = Field(3, description="1-5, lower = higher priority")
    goal_scope: Literal[
        "task", "daily_goal", "weekly_goal", "monthly_goal", "someday"
    ] = Field("task", description="Planning horizon")
    energy_level: str = Field("", description="'low' | 'medium' | 'high'")
    estimated_minutes: int = Field(
        0, description="Raw minutes (1.5x correction applied automatically)"
    )


class CompleteItemInput(BaseModel):
    item_id: str = Field(..., description="Item ID to mark done")
    notes: str = Field("", description="Optional completion notes")


class DeferItemInput(BaseModel):
    item_id: str = Field(..., description="Item ID to defer")
    to_when: str = Field(
        "tomorrow", description="Natural-language target: 'tomorrow', 'next week', etc."
    )


class QueryItemsInput(BaseModel):
    status: str = Field("", description="Filter by status (planned/done/deferred...)")
    due_before: str = Field("", description="ISO date — only items due before this")
    goal_scope: str = Field("", description="Filter by goal_scope")


class LifeSummaryInput(BaseModel):
    since: str = Field(
        "last 7 days",
        description="ISO date or relative scope ('last 7 days', 'this week')",
    )
    until: str = Field("", description="ISO date (default today)")


# ── draft_plan ──────────────────────────────────────────────────────────────


@tool(
    name="draft_plan",
    description=(
        "Kora drafts a plan from live calendar + items data. Respects "
        "ADHD planning adjustments (1.5x time correction, micro-step "
        "first step). Returns a draft for conversational review — the "
        "LLM should present it and let the user adjust."
    ),
    category=ToolCategory.TASKS,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
)
async def draft_plan(input: DraftPlanInput, container: Any) -> str:
    engine = getattr(container, "context_engine", None)
    profile = _get_profile(container)
    since, until, label = _parse_scope_window(input.scope)

    if engine is None:
        return _err("context engine not initialized")

    if since == until:
        day_ctx = await engine.build_day_context(target_date=since)
        items = day_ctx.items_due
        schedule = [e.model_dump(mode="json") for e in day_ctx.schedule]
        draft = {
            "scope": label,
            "goal": input.goal,
            "items_due_today": len(items),
            "schedule_count": len(schedule),
            "adhd_adjustments": {
                "time_correction_factor": profile.time_correction_factor,
                "first_step_max_minutes": 10,
                "max_steps": 7,
            },
            "draft_items": items,
            "schedule": schedule,
            "note": (
                "Draft based on live DayContext. Review with the user; "
                "adjustments should go through update_plan or create_item."
            ),
        }
    else:
        life_ctx = await engine.build_life_context(since, until, label)
        draft = {
            "scope": label,
            "goal": input.goal,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "focus_summary": life_ctx.focus_summary,
            "items_summary": life_ctx.items_summary,
            "insights": life_ctx.insights,
            "adhd_adjustments": {
                "time_correction_factor": profile.time_correction_factor,
            },
            "note": (
                "Draft based on LifeContext. Present insights and open "
                "items to the user for review."
            ),
        }
    return _ok({"draft": draft})


# ── update_plan (deterministic ripple analysis) ─────────────────────────────


@tool(
    name="update_plan",
    description=(
        "Apply a structured plan change (delete/reschedule/shrink) to "
        "calendar entries and return a ripple analysis. The supervisor "
        "LLM identifies affected_entry_ids from DayContext.schedule "
        "BEFORE calling this tool — do not pass free text."
    ),
    category=ToolCategory.CALENDAR,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def update_plan(input: UpdatePlanInput, container: Any) -> str:
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    if not input.affected_entry_ids:
        return _err("no affected_entry_ids provided")

    moved: list[dict[str, Any]] = []
    warnings: list[str] = []

    reschedule_dt: datetime | None = None
    if input.action == "reschedule":
        if not input.reschedule_to:
            return _err("reschedule requires reschedule_to")
        try:
            reschedule_dt = datetime.fromisoformat(input.reschedule_to)
            if reschedule_dt.tzinfo is None:
                reschedule_dt = reschedule_dt.replace(tzinfo=UTC)
        except ValueError:
            return _err(f"invalid reschedule_to: {input.reschedule_to!r}")

    profile = _get_profile(container)

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            for entry_id in input.affected_entry_ids:
                async with db.execute(
                    "SELECT * FROM calendar_entries WHERE id = ?", (entry_id,)
                ) as cur:
                    row = await cur.fetchone()
                if row is None:
                    warnings.append(f"entry not found: {entry_id}")
                    continue

                if input.action == "delete":
                    await db.execute(
                        "UPDATE calendar_entries SET status = 'cancelled', "
                        "updated_at = ? WHERE id = ?",
                        (_now_iso(), entry_id),
                    )
                    moved.append({"id": entry_id, "action": "cancelled"})
                elif input.action == "reschedule" and reschedule_dt is not None:
                    # Preserve the original duration if ends_at was set
                    old_start = datetime.fromisoformat(row["starts_at"])
                    if old_start.tzinfo is None:
                        old_start = old_start.replace(tzinfo=UTC)
                    new_end_iso: str | None = None
                    if row["ends_at"]:
                        old_end = datetime.fromisoformat(row["ends_at"])
                        if old_end.tzinfo is None:
                            old_end = old_end.replace(tzinfo=UTC)
                        duration = old_end - old_start
                        new_end_iso = (reschedule_dt + duration).isoformat()
                    await db.execute(
                        "UPDATE calendar_entries SET starts_at = ?, "
                        "ends_at = COALESCE(?, ends_at), updated_at = ? "
                        "WHERE id = ?",
                        (
                            reschedule_dt.isoformat(),
                            new_end_iso,
                            _now_iso(),
                            entry_id,
                        ),
                    )
                    moved.append(
                        {
                            "id": entry_id,
                            "action": "rescheduled",
                            "new_starts_at": reschedule_dt.isoformat(),
                        }
                    )
                    # Crash window warning
                    local_hour = reschedule_dt.hour
                    for cs, ce in profile.crash_periods:
                        if cs <= local_hour < ce:
                            warnings.append(
                                f"{row['title']} rescheduled into your "
                                f"usual crash window ({cs}-{ce}h)"
                            )
                            break
                elif input.action == "shrink":
                    if input.shrink_to_minutes <= 0:
                        warnings.append(
                            f"shrink requested with 0 minutes for {entry_id}"
                        )
                        continue
                    old_start = datetime.fromisoformat(row["starts_at"])
                    if old_start.tzinfo is None:
                        old_start = old_start.replace(tzinfo=UTC)
                    new_end = old_start + timedelta(
                        minutes=input.shrink_to_minutes
                    )
                    await db.execute(
                        "UPDATE calendar_entries SET ends_at = ?, "
                        "updated_at = ? WHERE id = ?",
                        (new_end.isoformat(), _now_iso(), entry_id),
                    )
                    moved.append(
                        {
                            "id": entry_id,
                            "action": "shrunk",
                            "new_ends_at": new_end.isoformat(),
                        }
                    )
            await db.commit()
    except (OSError, aiosqlite.Error) as exc:
        return _err(f"database error: {exc}")

    # New conflict detection — for rescheduled entries, check overlaps
    new_conflicts: list[dict[str, Any]] = []
    if input.action == "reschedule" and reschedule_dt is not None:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            for entry in moved:
                eid = entry.get("id")
                async with db.execute(
                    "SELECT id, title FROM calendar_entries "
                    "WHERE id != ? AND status = 'active' "
                    "AND starts_at < ? AND COALESCE(ends_at, starts_at) > ?",
                    (eid, reschedule_dt.isoformat(), reschedule_dt.isoformat()),
                ) as cur:
                    async for row in cur:
                        new_conflicts.append(
                            {"id": row["id"], "title": row["title"]}
                        )

    return _ok(
        {
            "summary": input.summary,
            "moved": moved,
            "new_conflicts": new_conflicts,
            "warnings": warnings,
        }
    )


# ── day_briefing ────────────────────────────────────────────────────────────


@tool(
    name="day_briefing",
    description=(
        "Return the full DayContext for the given date (or today) as a "
        "readable structured summary. Use when the user asks for a "
        "full rundown of their day."
    ),
    category=ToolCategory.TASKS,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
)
async def day_briefing(input: DayBriefingInput, container: Any) -> str:
    engine = getattr(container, "context_engine", None)
    if engine is None:
        return _err("context engine not initialized")

    target: date | None = None
    if input.date:
        try:
            target = datetime.fromisoformat(input.date).date()
        except ValueError:
            return _err(f"invalid date: {input.date!r}")

    ctx = await engine.build_day_context(target_date=target)
    return _ok({"day_context": ctx.model_dump(mode="json")})


# ── Item CRUD tools ─────────────────────────────────────────────────────────


@tool(
    name="create_item",
    description=(
        "Create a task or goal in Kora's items table. Applies the 1.5x "
        "ADHD time correction to estimated_minutes. goal_scope sets the "
        "planning horizon (task/daily_goal/weekly_goal/monthly_goal/someday)."
    ),
    category=ToolCategory.TASKS,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def create_item(input: CreateItemInput, container: Any) -> str:
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    profile = _get_profile(container)
    corrected = apply_time_correction(
        input.estimated_minutes, profile
    ) if input.estimated_minutes > 0 else None

    now = _now_iso()
    item_id = _new_id()
    energy_level = input.energy_level or None
    try:
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                """
                INSERT INTO items
                    (id, type, owner, title, description, status,
                     energy_level, estimated_minutes, priority, due_date,
                     goal_scope, created_at, updated_at)
                VALUES (?, 'task', 'kora', ?, ?, 'planned', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    input.title,
                    input.description or None,
                    energy_level,
                    corrected,
                    input.priority,
                    input.due_date or None,
                    input.goal_scope,
                    now,
                    now,
                ),
            )
            await db.execute(
                "INSERT INTO item_state_history "
                "(item_id, from_status, to_status, reason, recorded_at) "
                "VALUES (?, NULL, 'planned', 'created', ?)",
                (item_id, now),
            )
            await db.commit()
    except (OSError, aiosqlite.Error) as exc:
        return _err(f"database error: {exc}")

    return _ok(
        {
            "id": item_id,
            "title": input.title,
            "goal_scope": input.goal_scope,
            "due_date": input.due_date or None,
            "estimated_minutes_raw": input.estimated_minutes or None,
            "estimated_minutes_corrected": corrected,
            "message": f"Created {input.goal_scope}: {input.title}",
        }
    )


@tool(
    name="complete_item",
    description="Mark an item done and record the transition.",
    category=ToolCategory.TASKS,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def complete_item(input: CompleteItemInput, container: Any) -> str:
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    now = _now_iso()
    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT status FROM items WHERE id = ?", (input.item_id,)
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return _err(f"item not found: {input.item_id}")
            prev_status = row["status"]
            await db.execute(
                "UPDATE items SET status = 'done', updated_at = ? WHERE id = ?",
                (now, input.item_id),
            )
            await db.execute(
                "INSERT INTO item_state_history "
                "(item_id, from_status, to_status, reason, recorded_at) "
                "VALUES (?, ?, 'done', ?, ?)",
                (input.item_id, prev_status, input.notes or None, now),
            )
            await db.commit()
    except (OSError, aiosqlite.Error) as exc:
        return _err(f"database error: {exc}")

    return _ok({"id": input.item_id, "status": "done"})


@tool(
    name="defer_item",
    description="Push an item to a later date. Sets status=deferred.",
    category=ToolCategory.TASKS,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def defer_item(input: DeferItemInput, container: Any) -> str:
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    since, _, _ = _parse_scope_window(input.to_when)
    # _parse_scope_window returns the start of the target window.
    new_due = since.isoformat()
    now = _now_iso()
    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT status FROM items WHERE id = ?", (input.item_id,)
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return _err(f"item not found: {input.item_id}")
            prev_status = row["status"]
            await db.execute(
                "UPDATE items SET status = 'deferred', due_date = ?, "
                "updated_at = ? WHERE id = ?",
                (new_due, now, input.item_id),
            )
            await db.execute(
                "INSERT INTO item_state_history "
                "(item_id, from_status, to_status, reason, recorded_at) "
                "VALUES (?, ?, 'deferred', ?, ?)",
                (input.item_id, prev_status, f"deferred to {new_due}", now),
            )
            await db.commit()
    except (OSError, aiosqlite.Error) as exc:
        return _err(f"database error: {exc}")

    return _ok({"id": input.item_id, "status": "deferred", "due_date": new_due})


@tool(
    name="query_items",
    description=(
        "Read items filtered by status, due_before, or goal_scope. "
        "Returns up to 50 items ordered by due_date then priority."
    ),
    category=ToolCategory.TASKS,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
)
async def query_items(input: QueryItemsInput, container: Any) -> str:
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    clauses: list[str] = []
    params: list[Any] = []
    if input.status:
        clauses.append("status = ?")
        params.append(input.status)
    if input.due_before:
        clauses.append("due_date IS NOT NULL AND due_date <= ?")
        params.append(input.due_before)
    if input.goal_scope:
        clauses.append("goal_scope = ?")
        params.append(input.goal_scope)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    # ``due_date IS NULL`` sort-key keeps SQLite's default NULL-first
    # ordering from burying items with real due dates.
    sql = (
        "SELECT id, title, status, priority, due_date, goal_scope, "
        "estimated_minutes, energy_level FROM items"
        + where
        + " ORDER BY (due_date IS NULL), due_date ASC, priority ASC LIMIT 50"
    )

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
    except (OSError, aiosqlite.Error) as exc:
        return _err(f"database error: {exc}")

    items = [
        {
            "id": r["id"],
            "title": r["title"],
            "status": r["status"],
            "priority": r["priority"],
            "due_date": r["due_date"],
            "goal_scope": r["goal_scope"],
            "estimated_minutes": r["estimated_minutes"],
            "energy_level": r["energy_level"],
        }
        for r in rows
    ]
    return _ok({"items": items, "count": len(items)})


# ── life_summary ────────────────────────────────────────────────────────────


@tool(
    name="life_summary",
    description=(
        "Get a LifeContext summary for a time range. Accepts relative "
        "scopes ('last 7 days', 'this week') or an ISO date for 'since'."
    ),
    category=ToolCategory.TASKS,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
)
async def life_summary(input: LifeSummaryInput, container: Any) -> str:
    engine = getattr(container, "context_engine", None)
    if engine is None:
        return _err("context engine not initialized")

    today = datetime.now(UTC).date()
    if input.until:
        try:
            until = datetime.fromisoformat(input.until).date()
        except ValueError:
            return _err(f"invalid until: {input.until!r}")
    else:
        until = today

    since: date
    label = input.since
    try:
        since = datetime.fromisoformat(input.since).date()
    except ValueError:
        parsed_since, parsed_until, parsed_label = _parse_scope_window(input.since)
        since = parsed_since
        if parsed_until > since and not input.until:
            until = parsed_until
        label = parsed_label

    lc = await engine.build_life_context(since, until, label)
    return _ok({"life_context": lc.model_dump(mode="json")})


__all__ = [
    "apply_time_correction",
]
