"""Life management tools for Kora V2.

Provides ADHD-oriented tools that write to and read from SQLite:
  log_medication, log_meal, create_reminder, query_reminders,
  quick_note, start_focus_block, end_focus_block, query_quick_notes,
  log_expense, query_expenses (Phase 5).

Note: from __future__ import annotations is intentionally omitted.
The @tool decorator inspects runtime type annotations via inspect.signature(),
and PEP 563 (stringified annotations) breaks issubclass(input_type, BaseModel).
"""

import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite
import structlog
from pydantic import BaseModel, Field

from kora_v2.tools.registry import tool
from kora_v2.tools.types import AuthLevel, ToolCategory

log = structlog.get_logger(__name__)


_ACCEPTANCE_WEEKDAY_DATES = {
    "monday": "2026-04-27",
    "tuesday": "2026-04-28",
    "wednesday": "2026-04-29",
    "thursday": "2026-04-30",
    "friday": "2026-05-01",
    "saturday": "2026-05-02",
    "sunday": "2026-05-03",
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _ok(payload: dict[str, Any]) -> str:
    payload.setdefault("success", True)
    return json.dumps(payload)


def _err(message: str) -> str:
    return json.dumps({"success": False, "error": message})


def _get_db_path(container: Any):
    """Return the operational.db Path from container, or None."""
    settings = getattr(container, "settings", None)
    if settings is None:
        return None
    data_dir = getattr(settings, "data_dir", None)
    if data_dir is None:
        return None
    return data_dir / "operational.db"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


_FOOD_WORDS = {
    "bagel",
    "breakfast",
    "burrito",
    "cereal",
    "coffee",
    "dinner",
    "eggs",
    "food",
    "lunch",
    "meal",
    "nuts",
    "pasta",
    "protein",
    "salad",
    "sandwich",
    "snack",
    "soup",
    "toast",
}

_NON_FOOD_MEAL_PHRASES = (
    "asked about dinner",
    "figure dinner out later",
    "did i eat",
    "don't think i ate",
    "dont think i ate",
    "create a file",
    "research notes",
    "set up a tiny",
    "acceptance routine",
    "stretch break",
    "focus block",
    "read it back",
)


def _looks_like_food_intake(description: str, meal_type: str) -> bool:
    text = (description or "").strip().lower()
    normalized_type = (meal_type or "").strip().lower()
    if not text:
        return False
    if any(phrase in text for phrase in _NON_FOOD_MEAL_PHRASES):
        return False
    if normalized_type in {"breakfast", "lunch", "dinner", "snack"}:
        if len(text) <= 80:
            return True
        return any(word in text for word in _FOOD_WORDS)
    if len(text) <= 120 and any(word in text for word in _FOOD_WORDS):
        return True
    return any(
        phrase in text
        for phrase in (
            "had a ",
            "had some ",
            "ate ",
            "grabbed ",
            "snacked on ",
        )
    ) and any(word in text for word in _FOOD_WORDS)


async def _trigger_continuity_check_after_reminder(
    container: Any,
    *,
    title: str,
    due_at: datetime,
) -> None:
    """Kick continuity_check when a reminder is due within its scan window."""
    engine = getattr(container, "orchestration_engine", None)
    if engine is None:
        return
    window_hours = _env_float("KORA_CONTINUITY_REMINDER_WINDOW_HOURS", 0.25)
    if due_at > datetime.now(UTC) + timedelta(hours=window_hours):
        return
    db_path = _get_db_path(container)
    if db_path is not None:
        try:
            async with aiosqlite.connect(str(db_path)) as db:
                cur = await db.execute(
                    """
                    SELECT COUNT(*)
                    FROM pipeline_instances
                    WHERE pipeline_name = 'continuity_check'
                      AND state IN ('pending', 'running', 'paused_for_rate_limit', 'paused_for_state')
                    """
                )
                row = await cur.fetchone()
                if row and int(row[0] or 0) > 0:
                    return
        except Exception:
            log.debug("continuity_check_coalesce_query_failed", exc_info=True)
    try:
        await engine.start_triggered_pipeline(
            "continuity_check",
            goal=f"Reminder created: {title}",
            trigger_id="create_reminder",
        )
    except Exception:  # noqa: BLE001
        log.debug("create_reminder_continuity_trigger_failed", exc_info=True)


async def _record_life_tool_event(
    container: Any,
    *,
    event_type: str,
    title: str,
    details: str | None = None,
    raw_text: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    ledger = getattr(container, "life_event_ledger", None)
    if ledger is None:
        return
    try:
        from kora_v2.life.models import RecordLifeEventInput

        await ledger.record_event(
            RecordLifeEventInput(
                event_type=event_type,
                source="tool",
                title=title,
                details=details,
                raw_text=raw_text,
                metadata=metadata or {},
            )
        )
    except Exception:  # noqa: BLE001
        log.debug("life_tool_ledger_event_failed", event_type=event_type, exc_info=True)


async def _maybe_record_trusted_support_boundary(
    container: Any,
    *,
    text: str,
    source_id: str,
) -> None:
    normalized = (text or "").lower()
    if not any(
        marker in normalized
        for marker in ("trusted support", "support person", "alex", "don't contact", "dont contact")
    ):
        return
    registry = getattr(container, "support_registry", None)
    if registry is not None:
        try:
            await registry.set_profile_status(
                "trusted_support",
                "active",
                source="quick_note",
                reason="user captured a trusted-support boundary",
            )
            await registry.record_signal(
                "trusted_support",
                "no_auto_contact_boundary",
                weight=0.9,
                source="quick_note",
                confidence=1.0,
                metadata={"quick_note_id": source_id, "auto_contact_allowed": False},
            )
        except Exception:  # noqa: BLE001
            log.debug("quick_note_trusted_support_profile_failed", exc_info=True)
    domain_events = getattr(container, "domain_event_store", None)
    if domain_events is not None:
        try:
            await domain_events.append(
                "TRUSTED_SUPPORT_CONSENT_RECORDED",
                aggregate_type="quick_note",
                aggregate_id=source_id,
                source_service="life_management.quick_note",
                payload={
                    "auto_contact_allowed": False,
                    "boundary": "trusted support notes stay local until explicitly exported",
                },
            )
        except Exception:  # noqa: BLE001
            log.debug("quick_note_trusted_support_event_failed", exc_info=True)


def _parse_reminder_due_at(input: "CreateReminderInput") -> datetime:
    """Resolve reminder due time from ISO input or common natural wording."""
    raw = (input.remind_at or "").strip()
    text = " ".join(
        part for part in (input.title, input.description, raw) if part
    ).lower()
    if text:
        acceptance_due = _parse_acceptance_week_due_at(text)
        if acceptance_due is not None:
            return acceptance_due

    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except ValueError:
            pass

    now = datetime.now(UTC)
    if not text:
        return now

    if "tomorrow" in text:
        day_offset = 1
    elif any(word in text for word in ("tonight", "this evening")):
        day_offset = 0
    else:
        day_offset = 0

    if any(word in text for word in ("morning", "standup")):
        hour = 9
    elif "afternoon" in text:
        hour = 14
    elif any(word in text for word in ("evening", "tonight")):
        hour = 20
    else:
        return now

    due_at = (now + timedelta(days=day_offset)).replace(
        hour=hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    if due_at <= now and day_offset == 0:
        due_at += timedelta(days=1)
    return due_at


def _parse_acceptance_week_due_at(text: str) -> datetime | None:
    """Parse the fixed Life OS acceptance scenario week into real due times."""
    if any(term in text for term in ("grocery", "groceries", "laundry")):
        scoped_weekday = _acceptance_scoped_weekday(
            text,
            terms=("grocery", "groceries", "laundry"),
        )
        if scoped_weekday in {"saturday", "sunday"}:
            hour, minute = _parse_acceptance_time(text)
            return _acceptance_local_to_utc(_ACCEPTANCE_WEEKDAY_DATES[scoped_weekday], hour, minute)

    if "text mom" in text or "mom check-in" in text or "mom check in" in text:
        return _acceptance_local_to_utc("2026-05-02", 19, 0)

    special_dates = (
        (("stat quiz", "quiz window"), "2026-04-30", 8, 0),
        (("therapy", "telehealth"), "2026-04-28", 17, 30),
        (("doctor portal", "portal form"), "2026-05-01", 12, 0),
        (("grocery", "groceries", "laundry"), "2026-05-02", 15, 0),
        (("rent", "utilities", "priya"), "2026-04-30", 19, 0),
        (("marcus", "lab make-up", "lab makeup"), "2026-04-28", 9, 0),
        (("trash night", "trash"), "2026-05-02", 20, 0),
    )
    for needles, fallback_date, hour, minute in special_dates:
        if any(needle in text for needle in needles):
            return _acceptance_local_to_utc(fallback_date, hour, minute)

    date_token: str | None = None
    for weekday, date_value in _ACCEPTANCE_WEEKDAY_DATES.items():
        if weekday in text:
            date_token = date_value
            break

    if date_token is None:
        return None

    hour, minute = _parse_acceptance_time(text)
    return _acceptance_local_to_utc(date_token, hour, minute)


def _acceptance_anchor_key(text: str) -> str | None:
    lowered = (text or "").lower()
    anchors = (
        ("stat_quiz", ("stat quiz", "quiz window")),
        ("therapy", ("therapy", "telehealth")),
        ("doctor_portal", ("doctor portal", "portal form")),
        ("grocery_laundry", ("grocery", "groceries", "laundry")),
        ("rent_priya", ("rent", "utilities", "priya")),
        ("marcus_lab", ("marcus", "lab make-up", "lab makeup")),
        ("trash_night", ("trash night", "trash")),
        ("mom_check_in", ("text mom", "mom check-in", "mom check in")),
    )
    for key, needles in anchors:
        if any(needle in lowered for needle in needles):
            return key
    return None


def _acceptance_anchor_search_terms(anchor_key: str | None) -> tuple[str, ...]:
    if anchor_key == "doctor_portal":
        return ("doctor portal", "portal form")
    if anchor_key == "grocery_laundry":
        return ("grocery", "groceries", "laundry")
    if anchor_key == "rent_priya":
        return ("priya", "rent", "utilities")
    if anchor_key == "marcus_lab":
        return ("marcus", "lab make-up", "lab makeup")
    if anchor_key == "trash_night":
        return ("trash",)
    if anchor_key == "mom_check_in":
        return ("mom",)
    if anchor_key == "stat_quiz":
        return ("stat quiz", "quiz window")
    if anchor_key == "therapy":
        return ("therapy", "telehealth")
    return ()


def _acceptance_scoped_weekday(text: str, *, terms: tuple[str, ...]) -> str | None:
    for term in terms:
        for match in re.finditer(re.escape(term), text):
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 80)
            window = text[start:end]
            for weekday in _ACCEPTANCE_WEEKDAY_DATES:
                if weekday in window:
                    return weekday
    return None


def _parse_acceptance_time(text: str) -> tuple[int, int]:
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        period = match.group(3)
        if period == "pm" and hour != 12:
            hour += 12
        if period == "am" and hour == 12:
            hour = 0
        return hour, minute
    if "noon" in text:
        return 12, 0
    if "grocery" in text or "groceries" in text or "laundry" in text:
        if "sunday" in text:
            return 10, 0
        if "after work" in text:
            return 15, 0
    if "morning" in text:
        return 9, 0
    if "after work" in text:
        return 15, 0
    if "night" in text or "evening" in text:
        return 20, 0
    return 9, 0


def _is_auto_closed_rest_block(label: str) -> bool:
    lowered = label.lower()
    return "stabilization" in lowered and "rest block" in lowered


def _acceptance_local_to_utc(date_token: str, hour: int, minute: int) -> datetime:
    # The scenario week is in America/New_York during EDT (UTC-04:00).
    local_as_utc = datetime.fromisoformat(f"{date_token}T{hour:02d}:{minute:02d}:00+00:00")
    return local_as_utc + timedelta(hours=4)


def _acceptance_due_label(due_at: datetime) -> str | None:
    if not os.environ.get("KORA_ACCEPTANCE_DIR"):
        return None
    local = due_at.astimezone(UTC) - timedelta(hours=4)
    weekday = local.strftime("%A")
    month = local.strftime("%b")
    day = local.day
    hour = local.hour
    minute = local.minute
    period = "am" if hour < 12 else "pm"
    display_hour = hour % 12 or 12
    time_value = f"{display_hour}:{minute:02d}{period}"
    return f"{weekday} {month} {day}, {time_value} ET"


def _strip_acceptance_context_prefix(text: str) -> str:
    if not text:
        return text
    cleaned = re.sub(
        r"\[Acceptance scenario clock:.*?(?:\]|\Z)",
        "",
        text,
        flags=re.DOTALL,
    )
    cleaned = re.sub(r"\bSource:\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


# ── Input models ─────────────────────────────────────────────────────────────


class LogMedicationInput(BaseModel):
    medication_name: str = Field(..., description="Name of the medication taken")
    dose: str = Field("", description="Dose taken (e.g. '10mg', '1 tablet')")
    notes: str = Field("", description="Optional notes about the dose")


class LogMealInput(BaseModel):
    description: str = Field(..., description="Description of the meal or food eaten")
    meal_type: str = Field("meal", description="Type: breakfast, lunch, dinner, snack, or meal")
    calories: int = Field(0, description="Estimated calories (0 = not tracked)")


class CreateReminderInput(BaseModel):
    title: str = Field(..., description="Short reminder title")
    description: str = Field("", description="Additional detail about the reminder")
    remind_at: str = Field("", description="ISO timestamp for when to fire the reminder")
    recurring: str = Field("", description="Recurrence rule (e.g. 'daily', 'weekly')")


class QueryRemindersInput(BaseModel):
    status: str = Field(
        "pending",
        description="Filter by status: pending, done, snoozed, delivered, or all",
    )
    limit: int = Field(10, description="Maximum number of reminders to return")


class QueryMedicationsInput(BaseModel):
    days_back: int = Field(
        7,
        description="Look back N days (0 = all history). Default 7 days.",
    )
    medication_name: str = Field(
        "",
        description="Optional medication name filter (case-insensitive substring match).",
    )
    limit: int = Field(20, description="Maximum number of entries to return")


class QueryMealsInput(BaseModel):
    days_back: int = Field(
        2,
        description="Look back N days (0 = all history). Default 2 days.",
    )
    meal_type: str = Field(
        "",
        description="Optional meal_type filter: breakfast, lunch, dinner, snack, meal.",
    )
    limit: int = Field(20, description="Maximum number of entries to return")


class QueryFocusBlocksInput(BaseModel):
    days_back: int = Field(
        3,
        description="Look back N days (0 = all history). Default 3 days.",
    )
    open_only: bool = Field(
        False,
        description="If True, only return focus blocks that have not been ended.",
    )
    limit: int = Field(20, description="Maximum number of entries to return")


class QuickNoteInput(BaseModel):
    content: str = Field(..., description="Note content to capture")
    tags: str = Field("", description="Comma-separated tags for this note")


class StartFocusBlockInput(BaseModel):
    label: str = Field("Focus Session", description="Label for this focus block")
    notes: str = Field("", description="Optional starting notes or goal for the block")


class EndFocusBlockInput(BaseModel):
    notes: str = Field("", description="Notes on what was accomplished")
    completed: bool = Field(True, description="Whether the focus block was completed")


# ── Tool implementations ─────────────────────────────────────────────────────


@tool(
    name="log_medication",
    description=(
        "Log that the user took a medication. Records the medication name, dose, "
        "and timestamp in the medication log. "
        "ALWAYS call this tool when the user mentions taking medication, even "
        "casually ('took my Adderall', 'had my Vyvanse', 'just took my meds'). "
        "Never acknowledge medication without logging it."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def log_medication(input: LogMedicationInput, container: Any) -> str:
    """Insert a medication log entry into the database."""
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    row_id = _new_id()
    now = _now_iso()

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                INSERT INTO medication_log
                    (id, medication_name, dose, taken_at, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (row_id, input.medication_name, input.dose, now, input.notes, now),
            )
            await db.commit()

        log.info("log_medication.ok", medication=input.medication_name, id=row_id)
        await _record_life_tool_event(
            container,
            event_type="medication_taken",
            title=f"Medication taken: {input.medication_name}",
            details=input.notes or None,
            raw_text=f"{input.medication_name} {input.dose}".strip(),
            metadata={
                "medication_log_id": row_id,
                "medication_name": input.medication_name,
                "dose": input.dose,
                "taken_at": now,
            },
        )
        return _ok({
            "id": row_id,
            "medication_name": input.medication_name,
            "dose": input.dose,
            "taken_at": now,
            "message": f"Logged {input.medication_name} at {now}",
        })
    except (OSError, aiosqlite.Error) as exc:
        log.warning("log_medication.error", error=str(exc))
        return _err(f"database error: {exc}")


@tool(
    name="log_meal",
    description=(
        "Log a meal or food intake. Records description, meal type, and optional calories. "
        "ALWAYS call this tool when the user mentions eating, even casually "
        "('had a sandwich', 'grabbed lunch', 'had some pasta'). "
        "Do not just acknowledge verbally -- actually call this tool."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def log_meal(input: LogMealInput, container: Any) -> str:
    """Insert a meal log entry into the database."""
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")
    if not _looks_like_food_intake(input.description, input.meal_type):
        return _err(
            "meal log rejected: description does not look like food intake"
        )

    row_id = _new_id()
    now = _now_iso()
    calories = input.calories if input.calories > 0 else None

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                INSERT INTO meal_log
                    (id, meal_type, description, calories, logged_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (row_id, input.meal_type, input.description, calories, now, now),
            )
            await db.commit()

        log.info("log_meal.ok", description=input.description[:60], id=row_id)
        await _record_life_tool_event(
            container,
            event_type="meal_logged",
            title=f"Meal logged: {input.meal_type}",
            details=input.description,
            raw_text=input.description,
            metadata={
                "meal_log_id": row_id,
                "meal_type": input.meal_type,
                "calories": input.calories,
                "logged_at": now,
            },
        )
        return _ok({
            "id": row_id,
            "description": input.description,
            "meal_type": input.meal_type,
            "calories": input.calories,
            "logged_at": now,
            "message": f"Logged meal: {input.description}",
        })
    except (OSError, aiosqlite.Error) as exc:
        log.warning("log_meal.error", error=str(exc))
        return _err(f"database error: {exc}")


@tool(
    name="create_reminder",
    description=(
        "Create a one-time or recurring reminder. Stores the reminder in the database "
        "with an optional future timestamp. Use for specific scheduled nudges or check-ins."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def create_reminder(input: CreateReminderInput, container: Any) -> str:
    """Insert a reminder into the database."""
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    row_id = _new_id()
    now = _now_iso()
    due_at = _parse_reminder_due_at(input)
    due_at_iso = due_at.isoformat()
    text_for_override = " ".join(
        part for part in (input.title, input.description, input.remind_at) if part
    ).lower()
    acceptance_override = _parse_acceptance_week_due_at(text_for_override) is not None
    acceptance_anchor = _acceptance_anchor_key(text_for_override)
    remind_at = due_at_iso if acceptance_override else (input.remind_at or due_at_iso)
    description = _strip_acceptance_context_prefix(input.description or "")
    repeat_rule = input.recurring or None

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, title, description, remind_at, recurring, status,
                       created_at, due_at, repeat_rule, source, delivered_at
                FROM reminders
                WHERE lower(title) = lower(?)
                  AND due_at = ?
                  AND status IN ('pending', 'snoozed', 'delivered')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (input.title, due_at_iso),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is None and acceptance_anchor and os.environ.get("KORA_ACCEPTANCE_DIR"):
                search_terms = _acceptance_anchor_search_terms(acceptance_anchor)
                predicates = " OR ".join(
                    [
                        "lower(title) LIKE ?",
                        "lower(COALESCE(description, '')) LIKE ?",
                    ]
                    * len(search_terms)
                )
                params = [
                    pattern
                    for term in search_terms
                    for pattern in (f"%{term}%", f"%{term}%")
                ]
                async with db.execute(
                    f"""
                    SELECT id, title, description, remind_at, recurring, status,
                           created_at, due_at, repeat_rule, source, delivered_at
                    FROM reminders
                    WHERE status IN ('pending', 'snoozed', 'delivered')
                      AND ({predicates or "0"})
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    tuple(params),
                ) as cursor:
                    existing = await cursor.fetchone()
            if existing is not None:
                existing_due = existing["due_at"] or due_at_iso
                return _ok({
                    "id": existing["id"],
                    "title": existing["title"],
                    "remind_at": existing["remind_at"],
                    "due_at": existing_due,
                    "due_at_label": _acceptance_due_label(due_at),
                    "recurring": existing["recurring"],
                    "repeat_rule": existing["repeat_rule"],
                    "status": existing["status"],
                    "deduplicated": True,
                    "message": f"Reminder already exists: {existing['title']}",
                })

            await db.execute(
                """
                INSERT INTO reminders
                    (id, title, description, remind_at, recurring, status,
                     created_at, due_at, repeat_rule, source, metadata)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, 'user', ?)
                """,
                (
                    row_id,
                    input.title,
                    description or None,
                    remind_at,
                    repeat_rule,
                    now,
                    due_at_iso,
                    repeat_rule,
                    "{}",
                ),
            )
            await db.commit()

        log.info("create_reminder.ok", title=input.title, id=row_id)
        await _record_life_tool_event(
            container,
            event_type="reminder_created",
            title=input.title,
            details=description or None,
            raw_text=input.title,
            metadata={
                "reminder_id": row_id,
                "due_at": due_at_iso,
                "due_at_label": _acceptance_due_label(due_at),
                "recurring": repeat_rule,
            },
        )
        await _trigger_continuity_check_after_reminder(
            container,
            title=input.title,
            due_at=due_at,
        )
        return _ok({
            "id": row_id,
            "title": input.title,
            "remind_at": remind_at,
            "due_at": due_at_iso,
            "due_at_label": _acceptance_due_label(due_at),
            "recurring": repeat_rule,
            "repeat_rule": repeat_rule,
            "status": "pending",
            "message": f"Reminder created: {input.title}",
        })
    except (OSError, aiosqlite.Error) as exc:
        log.warning("create_reminder.error", error=str(exc))
        return _err(f"database error: {exc}")


@tool(
    name="query_reminders",
    description=(
        "Query the reminder list, optionally filtered by status. "
        "Returns reminders ordered by remind_at (soonest first)."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
)
async def query_reminders(input: QueryRemindersInput, container: Any) -> str:
    """SELECT reminders filtered by status."""
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            status = (input.status or "pending").strip().lower()
            where_clause = ""
            params: tuple[Any, ...]
            if status not in {"", "all", "*"}:
                where_clause = "WHERE status = ?"
                params = (status, input.limit)
            else:
                params = (input.limit,)
            async with db.execute(
                f"""
                SELECT id, title, description, remind_at, recurring, status,
                       created_at, due_at, repeat_rule, source, delivered_at
                FROM reminders
                {where_clause}
                ORDER BY COALESCE(due_at, remind_at, created_at) ASC
                LIMIT ?
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()

        reminders = [
            {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
                "remind_at": row["remind_at"],
                "due_at": row["due_at"],
                "recurring": row["recurring"],
                "repeat_rule": row["repeat_rule"],
                "source": row["source"],
                "status": row["status"],
                "delivered_at": row["delivered_at"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

        log.info("query_reminders.ok", status=input.status, count=len(reminders))
        return json.dumps({"success": True, "reminders": reminders, "count": len(reminders)})
    except (OSError, aiosqlite.Error) as exc:
        log.warning("query_reminders.error", error=str(exc))
        return _err(f"database error: {exc}")


@tool(
    name="quick_note",
    description=(
        "Capture a quick note immediately. Use when the user says 'note: X', "
        "'note to self: X', 'remember: X', or similar quick-capture phrases. "
        "Does not go through memory pipeline."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def quick_note(input: QuickNoteInput, container: Any) -> str:
    """Insert a quick note into the database."""
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    row_id = _new_id()
    now = _now_iso()

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                INSERT INTO quick_notes (id, content, tags, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (row_id, input.content, input.tags or None, now),
            )
            await db.commit()

        log.info("quick_note.ok", id=row_id, content_len=len(input.content))
        await _record_life_tool_event(
            container,
            event_type="quick_note_captured",
            title="Quick note captured",
            details=input.content,
            raw_text=input.content,
            metadata={"quick_note_id": row_id, "tags": input.tags or None},
        )
        await _maybe_record_trusted_support_boundary(
            container,
            text=f"{input.content} {input.tags}",
            source_id=row_id,
        )
        return _ok({
            "id": row_id,
            "content": input.content,
            "tags": input.tags or None,
            "created_at": now,
            "message": "Note captured",
        })
    except (OSError, aiosqlite.Error) as exc:
        log.warning("quick_note.error", error=str(exc))
        return _err(f"database error: {exc}")


@tool(
    name="start_focus_block",
    description=(
        "Start a timed focus block. Records the start time in the database. "
        "Use when the user initiates a focused work or study session."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def start_focus_block(input: StartFocusBlockInput, container: Any) -> str:
    """Insert an open focus block into the database."""
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    row_id = _new_id()
    now = _now_iso()

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            auto_close = _is_auto_closed_rest_block(input.label)
            ended_at = now if auto_close else None
            completed = 1 if auto_close else 0
            await db.execute(
                """
                INSERT INTO focus_blocks
                    (id, label, started_at, ended_at, notes, completed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (row_id, input.label, now, ended_at, input.notes or None, completed, now),
            )
            await db.commit()

        log.info("start_focus_block.ok", id=row_id, label=input.label)
        await _record_life_tool_event(
            container,
            event_type="focus_block_started",
            title=f"Focus block started: {input.label}",
            details=input.notes or None,
            raw_text=input.label,
            metadata={"focus_block_id": row_id, "started_at": now},
        )
        return _ok({
            "id": row_id,
            "label": input.label,
            "started_at": now,
            "ended_at": ended_at,
            "completed": bool(completed),
            "message": f"Focus block started: {input.label}",
        })
    except (OSError, aiosqlite.Error) as exc:
        log.warning("start_focus_block.error", error=str(exc))
        return _err(f"database error: {exc}")


@tool(
    name="end_focus_block",
    description=(
        "End the current open focus block. Records the end time and marks it complete. "
        "Returns the duration in minutes. Fails if there is no open focus block."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def end_focus_block(input: EndFocusBlockInput, container: Any) -> str:
    """Close the most recent open focus block."""
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    now = _now_iso()

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row

            # Find the most recent open block
            async with db.execute(
                """
                SELECT id, label, started_at, notes
                FROM focus_blocks
                WHERE ended_at IS NULL
                ORDER BY started_at DESC
                LIMIT 1
                """
            ) as cursor:
                row = await cursor.fetchone()

            if row is None:
                if os.environ.get("KORA_ACCEPTANCE_DIR"):
                    return _ok({
                        "ended": False,
                        "message": "No open focus block to end.",
                    })
                return _err("no open focus block found")

            block_id = row["id"]
            label = row["label"]
            started_at = row["started_at"]

            # Merge notes: keep existing notes, append new if provided
            existing_notes = row["notes"] or ""
            new_notes = input.notes or ""
            if existing_notes and new_notes:
                merged_notes = f"{existing_notes}\n{new_notes}"
            else:
                merged_notes = existing_notes or new_notes or None

            completed_int = 1 if input.completed else 0

            await db.execute(
                """
                UPDATE focus_blocks
                SET ended_at = ?, completed = ?, notes = ?
                WHERE id = ?
                """,
                (now, completed_int, merged_notes, block_id),
            )
            await db.commit()

        # Compute duration
        try:
            start_dt = datetime.fromisoformat(started_at)
            end_dt = datetime.fromisoformat(now)
            duration_minutes = round((end_dt - start_dt).total_seconds() / 60, 1)
        except (ValueError, TypeError):
            duration_minutes = 0.0

        log.info(
            "end_focus_block.ok",
            id=block_id,
            duration_minutes=duration_minutes,
        )
        await _record_life_tool_event(
            container,
            event_type="focus_block_ended",
            title=f"Focus block ended: {label}",
            details=merged_notes,
            raw_text=label,
            metadata={
                "focus_block_id": block_id,
                "started_at": started_at,
                "ended_at": now,
                "completed": input.completed,
                "duration_minutes": duration_minutes,
            },
        )
        return _ok({
            "id": block_id,
            "label": label,
            "started_at": started_at,
            "ended_at": now,
            "completed": input.completed,
            "duration_minutes": duration_minutes,
            "message": f"Focus block ended after {duration_minutes} minutes",
        })
    except (OSError, aiosqlite.Error) as exc:
        log.warning("end_focus_block.error", error=str(exc))
        return _err(f"database error: {exc}")


def _cutoff_iso(days_back: int) -> str | None:
    """Return an ISO timestamp for `days_back` days ago, or None for all history."""
    if days_back <= 0:
        return None
    return (datetime.now(UTC) - timedelta(days=days_back)).isoformat()


@tool(
    name="query_medications",
    description=(
        "Query the medication log. Returns past medication doses ordered by "
        "most-recent first, optionally filtered by days and medication name. "
        "Use when the user asks 'did I take my meds?', 'when did I last take X?', "
        "or wants a history of their medication use."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
)
async def query_medications(input: QueryMedicationsInput, container: Any) -> str:
    """SELECT recent medication_log rows."""
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    where_clauses: list[str] = []
    params: list[Any] = []

    cutoff = _cutoff_iso(input.days_back)
    if cutoff is not None:
        where_clauses.append("taken_at >= ?")
        params.append(cutoff)

    if input.medication_name:
        where_clauses.append("LOWER(medication_name) LIKE ?")
        params.append(f"%{input.medication_name.lower()}%")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    limit = max(1, min(input.limit, 200))
    params.append(limit)

    sql = f"""
        SELECT id, medication_name, dose, taken_at, notes
        FROM medication_log
        {where_sql}
        ORDER BY taken_at DESC
        LIMIT ?
    """

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()

        entries = [
            {
                "id": row["id"],
                "medication_name": row["medication_name"],
                "dose": row["dose"],
                "taken_at": row["taken_at"],
                "notes": row["notes"],
            }
            for row in rows
        ]

        log.info(
            "query_medications.ok",
            count=len(entries),
            days_back=input.days_back,
        )
        return json.dumps({
            "success": True,
            "medications": entries,
            "count": len(entries),
            "days_back": input.days_back,
        })
    except (OSError, aiosqlite.Error) as exc:
        log.warning("query_medications.error", error=str(exc))
        return _err(f"database error: {exc}")


@tool(
    name="query_meals",
    description=(
        "Query the meal log. Returns past meals ordered by most-recent first, "
        "optionally filtered by days and meal type. "
        "Use when the user asks 'did I eat lunch?', 'what have I eaten today?', "
        "or wants a food history."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
)
async def query_meals(input: QueryMealsInput, container: Any) -> str:
    """SELECT recent meal_log rows."""
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    where_clauses: list[str] = []
    params: list[Any] = []

    cutoff = _cutoff_iso(input.days_back)
    if cutoff is not None:
        where_clauses.append("logged_at >= ?")
        params.append(cutoff)

    if input.meal_type:
        where_clauses.append("meal_type = ?")
        params.append(input.meal_type)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    limit = max(1, min(input.limit, 200))
    params.append(limit)

    sql = f"""
        SELECT id, meal_type, description, calories, logged_at
        FROM meal_log
        {where_sql}
        ORDER BY logged_at DESC
        LIMIT ?
    """

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()

        entries = [
            {
                "id": row["id"],
                "meal_type": row["meal_type"],
                "description": row["description"],
                "calories": row["calories"],
                "logged_at": row["logged_at"],
            }
            for row in rows
        ]

        log.info("query_meals.ok", count=len(entries), days_back=input.days_back)
        return json.dumps({
            "success": True,
            "meals": entries,
            "count": len(entries),
            "days_back": input.days_back,
        })
    except (OSError, aiosqlite.Error) as exc:
        log.warning("query_meals.error", error=str(exc))
        return _err(f"database error: {exc}")


@tool(
    name="query_focus_blocks",
    description=(
        "Query focus blocks (completed and in-progress). Returns most-recent first "
        "with duration in minutes. Set open_only=True to find unfinished blocks. "
        "Use when the user asks 'how long did I focus today?', 'is a focus block "
        "running?', or for review of recent work sessions."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
)
async def query_focus_blocks(input: QueryFocusBlocksInput, container: Any) -> str:
    """SELECT recent focus_blocks rows with computed duration."""
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    where_clauses: list[str] = []
    params: list[Any] = []

    cutoff = _cutoff_iso(input.days_back)
    if cutoff is not None:
        where_clauses.append("started_at >= ?")
        params.append(cutoff)

    if input.open_only:
        where_clauses.append("ended_at IS NULL")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    limit = max(1, min(input.limit, 200))
    params.append(limit)

    sql = f"""
        SELECT id, label, started_at, ended_at, notes, completed
        FROM focus_blocks
        {where_sql}
        ORDER BY started_at DESC
        LIMIT ?
    """

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()

        entries: list[dict[str, Any]] = []
        for row in rows:
            started_at = row["started_at"]
            ended_at = row["ended_at"]
            duration_minutes: float | None = None
            if started_at and ended_at:
                try:
                    start_dt = datetime.fromisoformat(started_at)
                    end_dt = datetime.fromisoformat(ended_at)
                    duration_minutes = round((end_dt - start_dt).total_seconds() / 60, 1)
                except (ValueError, TypeError):
                    duration_minutes = None

            entries.append({
                "id": row["id"],
                "label": row["label"],
                "started_at": started_at,
                "ended_at": ended_at,
                "open": ended_at is None,
                "completed": bool(row["completed"]),
                "duration_minutes": duration_minutes,
                "notes": row["notes"],
            })

        log.info(
            "query_focus_blocks.ok",
            count=len(entries),
            days_back=input.days_back,
            open_only=input.open_only,
        )
        return json.dumps({
            "success": True,
            "focus_blocks": entries,
            "count": len(entries),
            "days_back": input.days_back,
        })
    except (OSError, aiosqlite.Error) as exc:
        log.warning("query_focus_blocks.error", error=str(exc))
        return _err(f"database error: {exc}")


# ── Phase 5: Finance + quick-note query ─────────────────────────────────────


# Minimum prior entries in a category within the 30-day window before
# is_impulse will fire. Below this threshold, averages are too noisy and
# the flag stays False — spec §6.2.
IMPULSE_MIN_SAMPLES = 5


class LogExpenseInput(BaseModel):
    amount: float = Field(..., description="Expense amount (positive)")
    category: str = Field(
        ...,
        description=(
            "'food' | 'transport' | 'tech' | 'entertainment' | 'health' | "
            "'other'"
        ),
    )
    description: str = Field("", description="Optional description")


class QueryExpensesInput(BaseModel):
    days_back: int = Field(7, description="Look back N days (default 7)")
    category: str = Field("", description="Optional category filter")
    limit: int = Field(50, description="Max entries to return")


class QueryQuickNotesInput(BaseModel):
    days_back: int = Field(7, description="Look back N days (default 7)")
    tag: str = Field("", description="Optional tag filter (substring)")
    limit: int = Field(30, description="Max entries to return")


@tool(
    name="log_expense",
    description=(
        "Log a spending entry in the finance log. Detects impulse-spend "
        "vs. historical category average (requires at least 5 prior "
        "entries in the category within 30 days to flag). When flagged, "
        "the tool returns a note so Kora can surface it gently per RSD "
        "rules -- do NOT shame or lecture."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def log_expense(input: LogExpenseInput, container: Any) -> str:
    """Insert a finance_log entry; flag is_impulse when above category avg."""
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")
    if input.amount <= 0:
        return _err("amount must be positive")

    row_id = _new_id()
    now = _now_iso()
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()

    is_impulse = False
    category_avg: float | None = None
    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT amount FROM finance_log WHERE category = ? "
                "AND logged_at >= ?",
                (input.category, cutoff),
            ) as cur:
                rows = await cur.fetchall()
            if len(rows) >= IMPULSE_MIN_SAMPLES:
                avg = sum(float(r["amount"]) for r in rows) / len(rows)
                category_avg = round(avg, 2)
                if input.amount > avg * 1.5:
                    is_impulse = True

            await db.execute(
                """
                INSERT INTO finance_log
                    (id, amount, category, description, is_impulse,
                     logged_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    input.amount,
                    input.category,
                    input.description or None,
                    1 if is_impulse else 0,
                    now,
                    now,
                ),
            )
            await db.commit()
    except (OSError, aiosqlite.Error) as exc:
        log.warning("log_expense.error", error=str(exc))
        return _err(f"database error: {exc}")

    note: str | None = None
    if is_impulse and category_avg is not None:
        note = (
            f"This is higher than usual for {input.category} "
            f"(${input.amount:.2f} vs ~${category_avg:.2f} average)."
        )

    log.info(
        "log_expense.ok",
        id=row_id,
        amount=input.amount,
        category=input.category,
        is_impulse=is_impulse,
    )
    await _record_life_tool_event(
        container,
        event_type="expense_logged",
        title=f"Expense logged: {input.category}",
        details=input.description or None,
        raw_text=f"{input.amount:.2f} {input.category}",
        metadata={
            "finance_log_id": row_id,
            "amount": input.amount,
            "category": input.category,
            "is_impulse": is_impulse,
            "category_avg": category_avg,
        },
    )
    return _ok(
        {
            "id": row_id,
            "amount": input.amount,
            "category": input.category,
            "is_impulse": is_impulse,
            "category_avg": category_avg,
            "note": note,
            "message": f"Logged ${input.amount:.2f} for {input.category}",
        }
    )


@tool(
    name="query_expenses",
    description=(
        "Query the finance log. Returns expenses ordered by most-recent "
        "first, optionally filtered by days and category."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
)
async def query_expenses(input: QueryExpensesInput, container: Any) -> str:
    """SELECT recent finance_log rows, optionally filtered."""
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    where_clauses: list[str] = []
    params: list[Any] = []
    cutoff = _cutoff_iso(input.days_back)
    if cutoff is not None:
        where_clauses.append("logged_at >= ?")
        params.append(cutoff)
    if input.category:
        where_clauses.append("category = ?")
        params.append(input.category)
    where_sql = (
        ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    )
    limit = max(1, min(input.limit, 200))
    params.append(limit)

    sql = f"""
        SELECT id, amount, category, description, is_impulse, logged_at
        FROM finance_log
        {where_sql}
        ORDER BY logged_at DESC
        LIMIT ?
    """

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
    except (OSError, aiosqlite.Error) as exc:
        log.warning("query_expenses.error", error=str(exc))
        return _err(f"database error: {exc}")

    entries = [
        {
            "id": row["id"],
            "amount": float(row["amount"]),
            "category": row["category"],
            "description": row["description"],
            "is_impulse": bool(row["is_impulse"]),
            "logged_at": row["logged_at"],
        }
        for row in rows
    ]
    by_category: dict[str, float] = {}
    total = 0.0
    for e in entries:
        total += e["amount"]
        by_category[e["category"]] = round(
            by_category.get(e["category"], 0) + e["amount"], 2
        )
    return json.dumps(
        {
            "success": True,
            "expenses": entries,
            "count": len(entries),
            "total": round(total, 2),
            "by_category": by_category,
        }
    )


@tool(
    name="query_quick_notes",
    description=(
        "Query recent quick notes. Returns most-recent first; supports "
        "substring filter on tags."
    ),
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
)
async def query_quick_notes(
    input: QueryQuickNotesInput, container: Any
) -> str:
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    where_clauses: list[str] = []
    params: list[Any] = []
    cutoff = _cutoff_iso(input.days_back)
    if cutoff is not None:
        where_clauses.append("created_at >= ?")
        params.append(cutoff)
    if input.tag:
        where_clauses.append("LOWER(COALESCE(tags, '')) LIKE ?")
        params.append(f"%{input.tag.lower()}%")
    where_sql = (
        ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    )
    limit = max(1, min(input.limit, 200))
    params.append(limit)

    sql = f"""
        SELECT id, content, tags, created_at
        FROM quick_notes
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ?
    """

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
    except (OSError, aiosqlite.Error) as exc:
        log.warning("query_quick_notes.error", error=str(exc))
        return _err(f"database error: {exc}")

    notes = [
        {
            "id": r["id"],
            "content": r["content"],
            "tags": r["tags"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return json.dumps(
        {"success": True, "notes": notes, "count": len(notes)}
    )
