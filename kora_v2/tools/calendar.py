"""Calendar store and LLM-facing calendar tools (Phase 5).

Provides:

* ``expand_recurring`` — RFC 5545 RRULE expansion helper.
* ``CalendarSync`` — thin bidirectional sync helper against the Google
  Calendar MCP server. Phase 5 wires the interface; full sync scheduling
  is Phase 8.
* ``create_calendar_entry`` / ``query_calendar`` / ``update_calendar_entry``
  / ``delete_calendar_entry`` / ``sync_google_calendar`` — LLM tools.

Storage is UTC (``calendar_entries.starts_at`` in ISO 8601). Recurring
entries are stored as templates with ``recurring_rule`` populated; the
query layer expands them at read time and filters out occurrences that
have explicit exception rows.

Note: from __future__ import annotations is intentionally omitted so
the @tool decorator can resolve pydantic Input models via runtime
signatures (matches the pattern used by ``life_management.py``).
"""

import json
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite
import structlog
from dateutil.rrule import rrulestr
from pydantic import BaseModel, Field

from kora_v2.core.calendar_models import CalendarEntry, CalendarKind
from kora_v2.tools.registry import tool
from kora_v2.tools.types import AuthLevel, ToolCategory

# Valid kind literals for runtime validation of Google marker tags.
_VALID_KINDS: frozenset[str] = frozenset(CalendarKind.__args__)  # type: ignore[attr-defined]

log = structlog.get_logger(__name__)


# ── Constants ───────────────────────────────────────────────────────────────

SYNTHETIC_ID_SEP = "::"


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


def _get_user_tz(container: Any) -> ZoneInfo:
    """Return the container's user timezone, falling back to UTC."""
    settings = getattr(container, "settings", None)
    name = getattr(settings, "user_tz", None) if settings is not None else None
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _row_to_entry(row: aiosqlite.Row) -> CalendarEntry:
    metadata = {}
    if row["metadata"]:
        try:
            metadata = json.loads(row["metadata"])
        except json.JSONDecodeError:
            metadata = {}
    return CalendarEntry(
        id=row["id"],
        kind=row["kind"],
        title=row["title"],
        description=row["description"],
        starts_at=_parse_dt(row["starts_at"]) or datetime.now(UTC),
        ends_at=_parse_dt(row["ends_at"]),
        all_day=bool(row["all_day"]),
        source=row["source"],
        google_event_id=row["google_event_id"],
        recurring_rule=row["recurring_rule"],
        energy_match=row["energy_match"],
        location=row["location"],
        metadata=metadata,
        synced_at=_parse_dt(row["synced_at"]),
        status=row["status"],
        override_parent_id=row["override_parent_id"],
        override_occurrence_date=row["override_occurrence_date"],
        created_at=_parse_dt(row["created_at"]) or datetime.now(UTC),
        updated_at=_parse_dt(row["updated_at"]) or datetime.now(UTC),
    )


def _entry_to_dict(entry: CalendarEntry) -> dict[str, Any]:
    return entry.model_dump(mode="json")


# ── Recurring expansion ─────────────────────────────────────────────────────


def expand_recurring(
    entry: CalendarEntry, since: datetime, until: datetime
) -> list[CalendarEntry]:
    """Expand a recurring parent entry into synthetic occurrences.

    Synthetic instances carry an ID of the form ``{parent_id}::{date}``
    (see ``SYNTHETIC_ID_SEP``) so callers can reliably round-trip them
    through ``update_calendar_entry`` / ``delete_calendar_entry``.
    """
    if not entry.recurring_rule:
        return [entry]
    try:
        rule = rrulestr(entry.recurring_rule, dtstart=entry.starts_at)
    except (ValueError, TypeError):
        return []
    # Ensure tz-aware range for comparison
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    try:
        occurrences = list(rule.between(since, until, inc=True))
    except (ValueError, TypeError):
        return []
    duration = None
    if entry.ends_at is not None:
        duration = entry.ends_at - entry.starts_at
        # Guard against corrupted rows where ends_at < starts_at
        if duration.total_seconds() < 0:
            duration = None
    expanded: list[CalendarEntry] = []
    for occ in occurrences:
        new_ends = occ + duration if duration else None
        expanded.append(
            entry.model_copy(
                update={
                    "id": f"{entry.id}{SYNTHETIC_ID_SEP}{occ.date().isoformat()}",
                    "starts_at": occ,
                    "ends_at": new_ends,
                    "recurring_rule": None,
                }
            )
        )
    return expanded


def _is_synthetic(entry_id: str) -> bool:
    return SYNTHETIC_ID_SEP in entry_id


def _parse_synthetic(entry_id: str) -> tuple[str, str]:
    """Split a synthetic recurring-occurrence id into (parent_id, occ_date).

    Callers must gate with ``_is_synthetic`` — calling this on a non-
    synthetic id raises ``ValueError``.
    """
    if SYNTHETIC_ID_SEP not in entry_id:
        raise ValueError(f"not a synthetic entry id: {entry_id!r}")
    parent_id, occ_date = entry_id.split(SYNTHETIC_ID_SEP, 1)
    return parent_id, occ_date


# ── DB helpers (shared by tools and context engine) ─────────────────────────


async def _insert_entry(
    db: aiosqlite.Connection, entry: CalendarEntry
) -> None:
    await db.execute(
        """
        INSERT INTO calendar_entries
            (id, kind, title, description, starts_at, ends_at, all_day,
             source, google_event_id, recurring_rule, energy_match,
             location, metadata, synced_at, status,
             override_parent_id, override_occurrence_date,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.id,
            entry.kind,
            entry.title,
            entry.description,
            entry.starts_at.isoformat(),
            entry.ends_at.isoformat() if entry.ends_at else None,
            1 if entry.all_day else 0,
            entry.source,
            entry.google_event_id,
            entry.recurring_rule,
            entry.energy_match,
            entry.location,
            json.dumps(entry.metadata) if entry.metadata else None,
            entry.synced_at.isoformat() if entry.synced_at else None,
            entry.status,
            entry.override_parent_id,
            entry.override_occurrence_date,
            entry.created_at.isoformat(),
            entry.updated_at.isoformat(),
        ),
    )


async def _load_entries_between(
    db: aiosqlite.Connection, since: datetime, until: datetime
) -> list[CalendarEntry]:
    """Return all entries whose starts_at is in [since, until], expanding
    recurring templates and filtering out occurrences with exceptions.
    """
    # 1. Load one-off + exception rows in range.
    sql_direct = """
        SELECT * FROM calendar_entries
        WHERE status != 'cancelled'
          AND recurring_rule IS NULL
          AND starts_at >= ? AND starts_at < ?
        ORDER BY starts_at ASC
    """
    db.row_factory = aiosqlite.Row
    async with db.execute(
        sql_direct, (since.isoformat(), until.isoformat())
    ) as cur:
        direct_rows = await cur.fetchall()
    direct_entries = [_row_to_entry(r) for r in direct_rows]

    # 2. Load all recurring parents (we expand them against [since, until]).
    async with db.execute(
        "SELECT * FROM calendar_entries "
        "WHERE status != 'cancelled' AND recurring_rule IS NOT NULL"
    ) as cur:
        parent_rows = await cur.fetchall()
    parents = [_row_to_entry(r) for r in parent_rows]

    # 3. Load exceptions that target any parent we're expanding.
    async with db.execute(
        "SELECT override_parent_id, override_occurrence_date, status "
        "FROM calendar_entries "
        "WHERE override_parent_id IS NOT NULL"
    ) as cur:
        exc_rows = await cur.fetchall()
    # Map (parent_id, occ_date_iso) → exception status.
    exc_map: dict[tuple[str, str], str] = {
        (r["override_parent_id"], r["override_occurrence_date"]): r["status"]
        for r in exc_rows
    }

    expanded: list[CalendarEntry] = []
    for parent in parents:
        for occ in expand_recurring(parent, since, until):
            occ_date = occ.starts_at.date().isoformat()
            key = (parent.id, occ_date)
            if key in exc_map:
                # Parent occurrence is overridden or cancelled; the
                # replacement row (if any) is already in direct_entries.
                continue
            expanded.append(occ)

    merged = [*direct_entries, *expanded]
    merged.sort(key=lambda e: e.starts_at)
    return merged


# ── Buffer auto-insertion ────────────────────────────────────────────────────


async def _insert_buffer_if_needed(
    db: aiosqlite.Connection,
    new_entry: CalendarEntry,
    transition_buffer_minutes: int,
) -> CalendarEntry | None:
    """Insert a buffer entry if ``new_entry`` starts within 5 minutes
    after another active entry. Returns the buffer entry if created.

    Keeps the rule simple and per-spec: only runs for
    ``kind == 'event'`` or ``'focus_block'`` entries and only when the
    gap is <= 5 minutes.
    """
    if new_entry.kind not in ("event", "focus_block"):
        return None
    # Look for any entry ending within the 5 minutes before new_entry.starts_at
    window_start = (new_entry.starts_at - timedelta(minutes=5)).isoformat()
    window_end = new_entry.starts_at.isoformat()
    db.row_factory = aiosqlite.Row
    async with db.execute(
        """
        SELECT id, ends_at FROM calendar_entries
        WHERE status = 'active'
          AND ends_at IS NOT NULL
          AND ends_at >= ? AND ends_at <= ?
          AND kind != 'buffer'
          AND id != ?
        ORDER BY ends_at DESC
        LIMIT 1
        """,
        (window_start, window_end, new_entry.id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    prev_ends_at = _parse_dt(row["ends_at"])
    if prev_ends_at is None:
        return None
    buffer_start = prev_ends_at
    buffer_end = prev_ends_at + timedelta(minutes=transition_buffer_minutes)
    now = datetime.now(UTC)
    buffer_entry = CalendarEntry(
        id=_new_id(),
        kind="buffer",
        title="Transition Time",
        starts_at=buffer_start,
        ends_at=buffer_end,
        source="kora",
        metadata={"between": [row["id"], new_entry.id]},
        created_at=now,
        updated_at=now,
    )
    await _insert_entry(db, buffer_entry)
    return buffer_entry


# ── CalendarSync (Google Calendar MCP thin wrapper) ─────────────────────────


class CalendarSync:
    """Thin bidirectional sync helper for Google Calendar.

    Phase 5 wires the interface but does *not* schedule periodic syncs;
    Phase 8 ProactiveAgent will call these methods on a cadence.

    The sync is best-effort: if the MCP server is unavailable, each
    method returns an empty list / False so the daemon keeps working
    from the internal store.
    """

    KORA_KIND_MARKER = "[kora:kind="

    def __init__(self, container: Any):
        self._container = container

    # ── Public API ────────────────────────────────────────────────

    async def pull_range(
        self, since: datetime, until: datetime
    ) -> list[dict[str, Any]]:
        """Pull events from Google Calendar into the local store.

        Returns the list of upserted entry dicts. No-op if the Google
        Calendar MCP server is not configured/available.
        """
        mcp = self._get_mcp()
        if mcp is None:
            return []
        try:
            result = await mcp.call_tool(
                "google_calendar",
                "list_events",
                {
                    "time_min": since.isoformat(),
                    "time_max": until.isoformat(),
                },
            )
        except Exception as exc:
            log.debug("google_calendar_pull_failed", error=str(exc))
            return []
        try:
            payload = json.loads(result.text) if hasattr(result, "text") else []
        except (json.JSONDecodeError, TypeError):
            return []
        if isinstance(payload, list):
            items: list[Any] = payload
        elif isinstance(payload, dict):
            items = payload.get("items", []) or []
        else:
            items = []

        upserted: list[dict[str, Any]] = []
        db_path = _get_db_path(self._container)
        if db_path is None:
            return []
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            for evt in items:
                entry = _google_event_to_entry(evt)
                if entry is None:
                    continue
                await _upsert_google_entry(db, entry)
                upserted.append(_entry_to_dict(entry))
            await db.commit()
        return upserted

    async def push_entry(
        self, entry: CalendarEntry
    ) -> str | None:
        """Push a locally-created entry to Google Calendar.

        Returns the ``google_event_id`` on success, or ``None`` if the
        push failed / sync is not configured. Phase 5 only pushes
        ``source='kora'`` or ``'user'`` entries.
        """
        if entry.source == "google":
            return entry.google_event_id
        mcp = self._get_mcp()
        if mcp is None:
            return None
        payload = _entry_to_google_event(entry)
        try:
            result = await mcp.call_tool(
                "google_calendar", "create_event", payload
            )
            data = json.loads(result.text) if hasattr(result, "text") else {}
            return data.get("id") if isinstance(data, dict) else None
        except Exception as exc:
            log.debug("google_calendar_push_failed", error=str(exc))
            return None

    # ── Internal ──────────────────────────────────────────────────

    def _get_mcp(self):
        """Return the MCP manager iff the google_calendar server is
        configured and the overall sync is enabled."""
        settings = getattr(self._container, "settings", None)
        if settings is None:
            return None
        mcp_cfg = getattr(settings, "mcp", None)
        if mcp_cfg is None:
            return None
        servers = getattr(mcp_cfg, "servers", {}) or {}
        if "google_calendar" not in servers:
            return None
        return getattr(self._container, "mcp_manager", None)


def _google_event_to_entry(evt: dict[str, Any]) -> CalendarEntry | None:
    """Convert a Google Calendar event dict into a ``CalendarEntry``.

    Returns ``None`` if the event is missing required fields. Parses
    the ``[kora:kind=...]`` marker out of the description to restore
    Kora's original ``kind``.
    """
    start = evt.get("start", {})
    end = evt.get("end", {})
    starts_at = _parse_dt(start.get("dateTime") or start.get("date"))
    if starts_at is None:
        return None
    ends_at = _parse_dt(end.get("dateTime") or end.get("date"))
    description = evt.get("description") or ""
    kind: str = "event"
    marker = CalendarSync.KORA_KIND_MARKER
    if marker in description:
        try:
            before, after = description.split(marker, 1)
            tag, _, remainder = after.partition("]")
            # Only accept the marker if it's a known kind; otherwise leave
            # as a plain event and preserve the description verbatim.
            if tag in _VALID_KINDS:
                kind = tag
                description = (before + remainder).strip()
        except ValueError:
            pass
    now = datetime.now(UTC)
    return CalendarEntry(
        id=_new_id(),
        kind=kind,  # type: ignore[arg-type]
        title=evt.get("summary") or "(untitled)",
        description=description or None,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day="date" in start and "dateTime" not in start,
        source="google",
        google_event_id=evt.get("id"),
        location=evt.get("location"),
        synced_at=now,
        created_at=now,
        updated_at=now,
    )


def _entry_to_google_event(entry: CalendarEntry) -> dict[str, Any]:
    """Build the Google Calendar API payload for creating an event."""
    description = entry.description or ""
    if entry.kind and entry.kind != "event":
        marker = f"{CalendarSync.KORA_KIND_MARKER}{entry.kind}]"
        if marker not in description:
            description = f"{description}\n\n{marker}" if description else marker
    payload: dict[str, Any] = {
        "summary": entry.title,
        "description": description or None,
        "start": {"dateTime": entry.starts_at.isoformat()},
    }
    if entry.ends_at:
        payload["end"] = {"dateTime": entry.ends_at.isoformat()}
    if entry.location:
        payload["location"] = entry.location
    if entry.recurring_rule:
        payload["recurrence"] = [f"RRULE:{entry.recurring_rule}"]
    return payload


async def _upsert_google_entry(
    db: aiosqlite.Connection, entry: CalendarEntry
) -> None:
    """Insert or update an entry keyed by ``google_event_id``."""
    if not entry.google_event_id:
        await _insert_entry(db, entry)
        return
    async with db.execute(
        "SELECT id FROM calendar_entries WHERE google_event_id = ?",
        (entry.google_event_id,),
    ) as cur:
        existing = await cur.fetchone()
    if existing is None:
        await _insert_entry(db, entry)
        return
    await db.execute(
        """
        UPDATE calendar_entries
           SET title = ?, description = ?, starts_at = ?, ends_at = ?,
               all_day = ?, location = ?, synced_at = ?, updated_at = ?
         WHERE google_event_id = ?
        """,
        (
            entry.title,
            entry.description,
            entry.starts_at.isoformat(),
            entry.ends_at.isoformat() if entry.ends_at else None,
            1 if entry.all_day else 0,
            entry.location,
            _now_iso(),
            _now_iso(),
            entry.google_event_id,
        ),
    )


# ── LLM-facing tools ─────────────────────────────────────────────────────────


class CreateCalendarEntryInput(BaseModel):
    kind: Literal[
        "event",
        "medication_window",
        "focus_block",
        "routine_window",
        "buffer",
        "reminder",
        "deadline",
    ] = Field("event", description="Entry kind")
    title: str = Field(..., description="Title of the entry")
    starts_at: str = Field(
        ..., description="ISO 8601 start datetime (UTC preferred)"
    )
    ends_at: str = Field("", description="ISO 8601 end datetime (optional)")
    description: str = Field("", description="Optional description")
    location: str = Field("", description="Optional location")
    energy_match: str = Field(
        "", description="'low' | 'medium' | 'high' (optional)"
    )
    recurring_rule: str = Field(
        "", description="RFC 5545 RRULE string (optional)"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Domain-specific extras"
    )


class QueryCalendarInput(BaseModel):
    date: str = Field("", description="ISO date (YYYY-MM-DD); default=today")
    days_ahead: int = Field(1, description="Number of days to include")
    kinds: list[str] = Field(
        default_factory=list, description="Filter by kind"
    )


class UpdateCalendarEntryInput(BaseModel):
    entry_id: str = Field(
        ..., description="Entry ID (may be synthetic parent::date form)"
    )
    changes: dict[str, Any] = Field(
        default_factory=dict, description="Partial update dict"
    )


class DeleteCalendarEntryInput(BaseModel):
    entry_id: str = Field(..., description="Entry ID to delete")


class SyncGoogleCalendarInput(BaseModel):
    days_ahead: int = Field(2, description="Days to pull from Google")


@tool(
    name="create_calendar_entry",
    description=(
        "Create a calendar entry on Kora's unified timeline. Works for "
        "events, medication windows, focus blocks, routines, buffers, "
        "reminders, and deadlines. ISO 8601 datetimes preferred (UTC)."
    ),
    category=ToolCategory.CALENDAR,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def create_calendar_entry(
    input: CreateCalendarEntryInput, container: Any
) -> str:
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    starts_at = _parse_dt(input.starts_at)
    if starts_at is None:
        return _err(f"invalid starts_at: {input.starts_at!r}")
    ends_at = _parse_dt(input.ends_at) if input.ends_at else None

    now = datetime.now(UTC)
    entry = CalendarEntry(
        id=_new_id(),
        kind=input.kind,
        title=input.title,
        description=input.description or None,
        starts_at=starts_at,
        ends_at=ends_at,
        source="kora",
        recurring_rule=input.recurring_rule or None,
        energy_match=input.energy_match or None,  # type: ignore[arg-type]
        location=input.location or None,
        metadata=input.metadata or {},
        created_at=now,
        updated_at=now,
    )

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            await _insert_entry(db, entry)
            # Buffer auto-insertion uses default 15 min (transition_buffer)
            transition_buffer = _get_transition_buffer(container)
            buffer = await _insert_buffer_if_needed(
                db, entry, transition_buffer
            )
            await db.commit()
    except (OSError, aiosqlite.Error) as exc:
        log.warning("create_calendar_entry.error", error=str(exc))
        return _err(f"database error: {exc}")

    # Best-effort push to Google Calendar (async, non-blocking for failures).
    # Invariant: if push_entry returns a google_event_id, the UPDATE below is
    # the sole path to synced_at != NULL. If the UPDATE fails after the push
    # succeeds, the row is orphaned (row has no google_event_id locally, but
    # the event exists upstream). We log loud enough for an operator to find
    # and reconcile it — see warning below.
    try:
        sync = CalendarSync(container)
        gid = await sync.push_entry(entry)
        if gid:
            try:
                async with aiosqlite.connect(str(db_path)) as db:
                    await db.execute(
                        "UPDATE calendar_entries SET google_event_id = ?, "
                        "synced_at = ? WHERE id = ?",
                        (gid, _now_iso(), entry.id),
                    )
                    await db.commit()
            except (OSError, aiosqlite.Error) as update_exc:
                log.warning(
                    "google_calendar_push_orphaned",
                    entry_id=entry.id,
                    google_event_id=gid,
                    title=entry.title,
                    starts_at=entry.starts_at.isoformat(),
                    error=str(update_exc),
                    note=(
                        "google event was created but local row could not be "
                        "updated; reconcile by setting google_event_id and "
                        "synced_at on the entry"
                    ),
                )
    except Exception:
        log.debug("google_calendar_push_skipped", exc_info=True)

    return _ok(
        {
            "id": entry.id,
            "kind": entry.kind,
            "title": entry.title,
            "starts_at": entry.starts_at.isoformat(),
            "ends_at": entry.ends_at.isoformat() if entry.ends_at else None,
            "buffer_inserted": buffer is not None,
            "message": f"Calendar entry created: {entry.title}",
        }
    )


@tool(
    name="query_calendar",
    description=(
        "Query calendar entries for a time range. Expands recurring "
        "templates and applies exception rows. Returns entries ordered "
        "by start time."
    ),
    category=ToolCategory.CALENDAR,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=True,
)
async def query_calendar(input: QueryCalendarInput, container: Any) -> str:
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    user_tz = _get_user_tz(container)

    # Build the local-day window first (so "today" means today in the
    # user's wall clock, not UTC), then convert to UTC for the DB.
    if input.date:
        try:
            day = datetime.fromisoformat(input.date).date()
        except ValueError:
            return _err(f"invalid date: {input.date!r}")
    else:
        day = datetime.now(user_tz).date()
    days_ahead = max(1, input.days_ahead)
    since_local = datetime.combine(day, time.min, tzinfo=user_tz)
    until_local = since_local + timedelta(days=days_ahead)
    since = since_local.astimezone(UTC)
    until = until_local.astimezone(UTC)

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            entries = await _load_entries_between(db, since, until)
    except (OSError, aiosqlite.Error) as exc:
        return _err(f"database error: {exc}")

    if input.kinds:
        allowed = set(input.kinds)
        entries = [e for e in entries if e.kind in allowed]

    return _ok(
        {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "since_local": since_local.isoformat(),
            "until_local": until_local.isoformat(),
            "count": len(entries),
            "entries": [_entry_to_dict(e) for e in entries],
        }
    )


@tool(
    name="update_calendar_entry",
    description=(
        "Modify a calendar entry. Accepts a real entry ID or a synthetic "
        "'parent_id::YYYY-MM-DD' form; synthetic IDs create an exception "
        "row that overrides the parent's occurrence for that date."
    ),
    category=ToolCategory.CALENDAR,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def update_calendar_entry(
    input: UpdateCalendarEntryInput, container: Any
) -> str:
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    if not input.changes:
        return _err("no changes provided")

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row

            if _is_synthetic(input.entry_id):
                parent_id, occ_date = _parse_synthetic(input.entry_id)
                async with db.execute(
                    "SELECT * FROM calendar_entries WHERE id = ?",
                    (parent_id,),
                ) as cur:
                    parent_row = await cur.fetchone()
                if parent_row is None:
                    return _err(f"parent entry not found: {parent_id}")
                parent = _row_to_entry(parent_row)
                # Build exception from parent + changes
                now = datetime.now(UTC)
                exception = parent.model_copy(
                    update={
                        "id": _new_id(),
                        "recurring_rule": None,
                        "override_parent_id": parent.id,
                        "override_occurrence_date": occ_date,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                # Apply changes (supports starts_at/ends_at as ISO strings)
                exception = _apply_changes(exception, input.changes)
                await _insert_entry(db, exception)
                await db.commit()
                return _ok(
                    {
                        "id": exception.id,
                        "parent_id": parent.id,
                        "occurrence_date": occ_date,
                        "action": "exception_created",
                    }
                )

            # Real UUID → in-place update.
            async with db.execute(
                "SELECT * FROM calendar_entries WHERE id = ?",
                (input.entry_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return _err(f"entry not found: {input.entry_id}")
            entry = _row_to_entry(row)
            updated = _apply_changes(entry, input.changes)
            await _update_row(db, updated)
            await db.commit()
    except (OSError, aiosqlite.Error) as exc:
        return _err(f"database error: {exc}")

    return _ok(
        {
            "id": input.entry_id,
            "changes": list(input.changes.keys()),
            "action": "updated",
        }
    )


@tool(
    name="delete_calendar_entry",
    description=(
        "Remove a calendar entry. For synthetic recurring IDs, creates a "
        "cancelled exception row (leaves the parent rule intact)."
    ),
    category=ToolCategory.CALENDAR,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def delete_calendar_entry(
    input: DeleteCalendarEntryInput, container: Any
) -> str:
    db_path = _get_db_path(container)
    if db_path is None:
        return _err("no database available")

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            if _is_synthetic(input.entry_id):
                parent_id, occ_date = _parse_synthetic(input.entry_id)
                async with db.execute(
                    "SELECT * FROM calendar_entries WHERE id = ?",
                    (parent_id,),
                ) as cur:
                    parent_row = await cur.fetchone()
                if parent_row is None:
                    return _err(f"parent entry not found: {parent_id}")
                parent = _row_to_entry(parent_row)
                now = datetime.now(UTC)
                cancelled = parent.model_copy(
                    update={
                        "id": _new_id(),
                        "recurring_rule": None,
                        "override_parent_id": parent.id,
                        "override_occurrence_date": occ_date,
                        "status": "cancelled",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                await _insert_entry(db, cancelled)
                await db.commit()
                return _ok(
                    {
                        "id": cancelled.id,
                        "parent_id": parent.id,
                        "occurrence_date": occ_date,
                        "action": "cancelled_exception",
                    }
                )

            async with db.execute(
                "SELECT id FROM calendar_entries WHERE id = ?",
                (input.entry_id,),
            ) as cur:
                existing = await cur.fetchone()
            if existing is None:
                return _err(f"entry not found: {input.entry_id}")
            await db.execute(
                "UPDATE calendar_entries SET status = 'cancelled', "
                "updated_at = ? WHERE id = ?",
                (_now_iso(), input.entry_id),
            )
            await db.commit()
    except (OSError, aiosqlite.Error) as exc:
        return _err(f"database error: {exc}")

    return _ok({"id": input.entry_id, "action": "cancelled"})


@tool(
    name="sync_google_calendar",
    description=(
        "Trigger a manual sync with Google Calendar. Pulls today + the "
        "next N days from Google into Kora's calendar store."
    ),
    category=ToolCategory.CALENDAR,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def sync_google_calendar(
    input: SyncGoogleCalendarInput, container: Any
) -> str:
    sync = CalendarSync(container)
    since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    until = since + timedelta(days=max(1, input.days_ahead))
    upserted = await sync.pull_range(since, until)
    return _ok(
        {
            "pulled": len(upserted),
            "since": since.isoformat(),
            "until": until.isoformat(),
            "message": (
                f"Pulled {len(upserted)} event(s) from Google Calendar"
                if upserted
                else "Google Calendar sync is not configured or returned no events"
            ),
        }
    )


# ── Update helpers ───────────────────────────────────────────────────────────


def _apply_changes(
    entry: CalendarEntry, changes: dict[str, Any]
) -> CalendarEntry:
    """Apply a partial-update dict to a CalendarEntry. Parses ISO dates
    from string values for datetime fields."""
    parsed: dict[str, Any] = {}
    for key, value in changes.items():
        if key in ("starts_at", "ends_at") and isinstance(value, str):
            parsed[key] = _parse_dt(value)
        elif key == "updated_at":
            continue  # we always set this below
        else:
            parsed[key] = value
    parsed["updated_at"] = datetime.now(UTC)
    return entry.model_copy(update=parsed)


async def _update_row(db: aiosqlite.Connection, entry: CalendarEntry) -> None:
    await db.execute(
        """
        UPDATE calendar_entries
           SET kind = ?, title = ?, description = ?, starts_at = ?,
               ends_at = ?, all_day = ?, source = ?, google_event_id = ?,
               recurring_rule = ?, energy_match = ?, location = ?,
               metadata = ?, synced_at = ?, status = ?, updated_at = ?
         WHERE id = ?
        """,
        (
            entry.kind,
            entry.title,
            entry.description,
            entry.starts_at.isoformat(),
            entry.ends_at.isoformat() if entry.ends_at else None,
            1 if entry.all_day else 0,
            entry.source,
            entry.google_event_id,
            entry.recurring_rule,
            entry.energy_match,
            entry.location,
            json.dumps(entry.metadata) if entry.metadata else None,
            entry.synced_at.isoformat() if entry.synced_at else None,
            entry.status,
            entry.updated_at.isoformat(),
            entry.id,
        ),
    )


def _get_transition_buffer(container: Any) -> int:
    """Pull the ADHD profile transition buffer from the container, or 15."""
    try:
        profile = getattr(container, "adhd_profile", None)
        if profile is not None:
            return int(profile.transition_buffer_minutes)
    except Exception:
        pass
    return 15


__all__ = [
    "CalendarSync",
    "expand_recurring",
    "SYNTHETIC_ID_SEP",
    "_load_entries_between",
]
