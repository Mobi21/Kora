"""NotificationGate — spec §10.3.

Single chokepoint for all outbound communication. Anyone that wants to
notify the user calls ``gate.send_llm(...)`` (for generative replies)
or ``gate.send_templated(...)`` (for zero-request deterministic
messages). The gate:

1. Resolves the tier (llm vs templated).
2. Respects DND window from the User Model schedule profile — templated
   entries with ``bypass_dnd=True`` override this.
3. Respects the hyperfocus-suppression flag on the profile.
4. Enforces an RSD filter hook (currently a no-op pass-through — the
   real softening lives in the supervisor prompt today; the gate is
   the place to plug in a future wording sanitiser).
5. Honours a manual ``suppress_until`` deadline.
6. Routes delivery to the active channel (WebSocket session, notification
   queue, Inbox, or the turn-response payload).
7. Records the delivery in the ``notifications`` table with the new
   ``delivery_tier`` / ``template_id`` / ``template_vars`` columns.

Only deterministic messages use the templated path; it costs zero
provider requests, so even when the rate limiter is exhausted Kora can
still tell the user "I'm catching up on my window."
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite
import structlog

from kora_v2.runtime.orchestration.templates import (
    RenderedTemplate,
    TemplatePriority,
    TemplateRegistry,
)

if TYPE_CHECKING:
    from kora_v2.runtime.orchestration.system_state import UserScheduleProfile

log = structlog.get_logger(__name__)


# ── Data model ───────────────────────────────────────────────────────────


class DeliveryChannel(StrEnum):
    """Where the gate delivered a notification."""

    WEBSOCKET = "websocket"
    QUEUE = "queue"
    INBOX = "inbox"
    TURN_RESPONSE = "turn_response"
    SUPPRESSED = "suppressed"


@dataclass
class GeneratedNotification:
    """An LLM-generated message to deliver.

    This path consumes provider capacity (the LLM already produced the
    text before calling the gate); the gate only handles routing.
    """

    text: str
    priority: TemplatePriority
    topic: str = ""
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    bypass_dnd: bool = False


@dataclass
class DeliveryResult:
    """Summary of a completed delivery attempt."""

    notification_id: str
    delivered: bool
    channel: DeliveryChannel
    tier: str                         # "llm" | "templated"
    template_id: str | None
    reason: str
    text: str
    priority: TemplatePriority
    delivered_at: datetime


@dataclass
class PendingNotification:
    """Queued notification held while the user is unreachable or DND is active."""

    task_id: str
    message: str
    tier: str = "templated"
    priority: TemplatePriority = TemplatePriority.MEDIUM
    template_id: str | None = None
    template_vars: dict[str, Any] = field(default_factory=dict)
    channel: DeliveryChannel = DeliveryChannel.QUEUE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ── Gate ──────────────────────────────────────────────────────────────────


class NotificationGate:
    """The single delivery chokepoint.

    Constructed by :class:`OrchestrationEngine` with the template
    registry, schedule profile, operational DB path, and a broadcast
    callable (typically the daemon's WebSocket broadcast hook). The
    broadcast callable is optional — a gate without it still queues
    notifications and records them to the DB, which is correct for
    daemon-less unit tests.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        templates: TemplateRegistry,
        schedule_profile: UserScheduleProfile | None = None,
        websocket_broadcast: (
            Callable[[dict[str, Any]], Awaitable[None]] | None
        ) = None,
        session_active_fn: Callable[[], bool] | None = None,
        hyperfocus_active_fn: Callable[[], bool] | None = None,
    ) -> None:
        self._db_path = db_path
        self._templates = templates
        self._profile = schedule_profile
        self._broadcast = websocket_broadcast
        self._session_active_fn = session_active_fn or (lambda: False)
        self._hyperfocus_active_fn = hyperfocus_active_fn or (lambda: False)

        self._suppress_until: datetime | None = None
        self._suppress_reason: str = ""
        self._queue: list[PendingNotification] = []

    # ── Suppression controls ─────────────────────────────────────

    async def suppress_until(self, until: datetime, reason: str) -> None:
        """Block all non-bypass deliveries until *until*."""
        self._suppress_until = until
        self._suppress_reason = reason
        log.info(
            "notifications_suppressed", until=until.isoformat(), reason=reason
        )

    def clear_suppression(self) -> None:
        self._suppress_until = None
        self._suppress_reason = ""

    def update_profile(self, profile: UserScheduleProfile) -> None:
        self._profile = profile

    # ── Public sends ─────────────────────────────────────────────

    async def send_llm(
        self,
        notification: GeneratedNotification,
        *,
        via: DeliveryChannel = DeliveryChannel.WEBSOCKET,
    ) -> DeliveryResult:
        """Deliver an LLM-generated message."""
        notification_id = f"note-{uuid.uuid4().hex[:12]}"
        return await self._deliver(
            notification_id=notification_id,
            text=notification.text,
            priority=notification.priority,
            tier="llm",
            template_id=None,
            template_vars={},
            bypass_dnd=notification.bypass_dnd,
            via=via,
            metadata=notification.metadata,
        )

    async def send_templated(
        self,
        template_id: str,
        *,
        via: DeliveryChannel = DeliveryChannel.WEBSOCKET,
        priority_override: TemplatePriority | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> DeliveryResult:
        """Deliver a YAML-templated message (zero provider requests)."""
        rendered: RenderedTemplate = self._templates.render(
            template_id, priority_override=priority_override, **kwargs
        )
        notification_id = f"note-{uuid.uuid4().hex[:12]}"
        return await self._deliver(
            notification_id=notification_id,
            text=rendered.text,
            priority=rendered.priority,
            tier="templated",
            template_id=template_id,
            template_vars=rendered.vars,
            bypass_dnd=rendered.bypass_dnd,
            via=via,
            metadata=metadata or {},
        )

    # ── Core delivery ────────────────────────────────────────────

    async def _deliver(
        self,
        *,
        notification_id: str,
        text: str,
        priority: TemplatePriority,
        tier: str,
        template_id: str | None,
        template_vars: dict[str, Any],
        bypass_dnd: bool,
        via: DeliveryChannel,
        metadata: dict[str, Any],
    ) -> DeliveryResult:
        now = datetime.now(UTC)

        # 1) Manual suppression
        if self._suppress_until is not None and now < self._suppress_until and not bypass_dnd:
            await self._record(
                notification_id=notification_id,
                tier=tier,
                template_id=template_id,
                template_vars=template_vars,
                text=text,
                priority=priority,
                channel=DeliveryChannel.SUPPRESSED,
                delivered=False,
                reason=f"suppressed:{self._suppress_reason}",
                when=now,
            )
            return DeliveryResult(
                notification_id=notification_id,
                delivered=False,
                channel=DeliveryChannel.SUPPRESSED,
                tier=tier,
                template_id=template_id,
                reason=f"suppressed:{self._suppress_reason}",
                text=text,
                priority=priority,
                delivered_at=now,
            )

        # 2) Hyperfocus suppression — unconditional (bypass_dnd does
        #    NOT override hyperfocus per spec §10 + review rubric).
        #    Hyperfocus is a separate axis from DND; bypass_dnd only
        #    overrides the DND window, never the hyperfocus check.
        if self._hyperfocus_active_fn():
            if self._profile and getattr(
                self._profile, "hyperfocus_suppression", True
            ):
                await self._record(
                    notification_id=notification_id,
                    tier=tier,
                    template_id=template_id,
                    template_vars=template_vars,
                    text=text,
                    priority=priority,
                    channel=DeliveryChannel.SUPPRESSED,
                    delivered=False,
                    reason="hyperfocus",
                    when=now,
                )
                return DeliveryResult(
                    notification_id=notification_id,
                    delivered=False,
                    channel=DeliveryChannel.SUPPRESSED,
                    tier=tier,
                    template_id=template_id,
                    reason="hyperfocus",
                    text=text,
                    priority=priority,
                    delivered_at=now,
                )

        # 3) DND window
        if (
            self._profile is not None
            and not bypass_dnd
            and _in_dnd_now(self._profile, now)
        ):
            self._queue.append(
                PendingNotification(
                    task_id=metadata.get("task_id", ""),
                    message=text,
                    tier=tier,
                    priority=priority,
                    template_id=template_id,
                    template_vars=template_vars,
                    channel=via,
                )
            )
            await self._record(
                notification_id=notification_id,
                tier=tier,
                template_id=template_id,
                template_vars=template_vars,
                text=text,
                priority=priority,
                channel=DeliveryChannel.QUEUE,
                delivered=False,
                reason="dnd_queued",
                when=now,
            )
            return DeliveryResult(
                notification_id=notification_id,
                delivered=False,
                channel=DeliveryChannel.QUEUE,
                tier=tier,
                template_id=template_id,
                reason="dnd_queued",
                text=text,
                priority=priority,
                delivered_at=now,
            )

        # 4) RSD filter pass-through — current hook is the identity
        #    function so future wording sanitisers slot in cleanly.
        text = self._rsd_filter(text)

        # 5) Channel routing
        channel = via
        delivered = False
        reason = "ok"
        if via == DeliveryChannel.WEBSOCKET and self._broadcast and self._session_active_fn():
            try:
                await self._broadcast(
                    {
                        "type": "notification",
                        "notification_id": notification_id,
                        "text": text,
                        "priority": priority.value,
                        "tier": tier,
                        "template_id": template_id,
                    }
                )
                delivered = True
            except Exception as exc:
                log.warning("notification_broadcast_failed", error=str(exc))
                reason = f"broadcast_failed:{exc}"
                channel = DeliveryChannel.QUEUE
                self._queue.append(
                    PendingNotification(
                        task_id=metadata.get("task_id", ""),
                        message=text,
                        tier=tier,
                        priority=priority,
                        template_id=template_id,
                        template_vars=template_vars,
                        channel=via,
                    )
                )
        elif via == DeliveryChannel.TURN_RESPONSE:
            # Caller will read the DeliveryResult and append to the
            # current turn's response payload directly.
            delivered = True
        elif via == DeliveryChannel.INBOX:
            # Inbox delivery is handled by the working-doc writer; the
            # gate just records the delivery intent.
            delivered = True
        else:
            # Queue — caller can drain later
            channel = DeliveryChannel.QUEUE
            self._queue.append(
                PendingNotification(
                    task_id=metadata.get("task_id", ""),
                    message=text,
                    tier=tier,
                    priority=priority,
                    template_id=template_id,
                    template_vars=template_vars,
                    channel=via,
                )
            )
            delivered = False
            reason = "queued"

        await self._record(
            notification_id=notification_id,
            tier=tier,
            template_id=template_id,
            template_vars=template_vars,
            text=text,
            priority=priority,
            channel=channel,
            delivered=delivered,
            reason=reason,
            when=now,
        )
        return DeliveryResult(
            notification_id=notification_id,
            delivered=delivered,
            channel=channel,
            tier=tier,
            template_id=template_id,
            reason=reason,
            text=text,
            priority=priority,
            delivered_at=now,
        )

    # ── Queue API ────────────────────────────────────────────────
    def drain(self) -> list[PendingNotification]:
        out, self._queue = self._queue, []
        return out

    def __len__(self) -> int:
        return len(self._queue)

    # ── Hooks ────────────────────────────────────────────────────
    def _rsd_filter(self, text: str) -> str:
        """Placeholder RSD softening hook.

        The real filter lives in the supervisor system prompt today; the
        gate is the future home of a wording sanitiser so the rule has
        a single enforcement point. For now, identity.
        """
        return text

    async def _record(
        self,
        *,
        notification_id: str,
        tier: str,
        template_id: str | None,
        template_vars: dict[str, Any],
        text: str,
        priority: TemplatePriority,
        channel: DeliveryChannel,
        delivered: bool,
        reason: str,
        when: datetime,
    ) -> None:
        """Append a row to the ``notifications`` table.

        The notifications table is owned by ``kora_v2/core/db.py`` (see
        the V1 schema block there). The orchestration migration
        ``002_notifications_templates.sql`` adds the two-tier columns
        (``delivery_tier``, ``template_id``, ``template_vars``,
        ``reason``) on top of that schema. This writer handles three
        cases without loud failures:

        1. Full schema (post-migration) — insert everything.
        2. Pre-migration schema (table exists, new columns missing) —
           insert core columns only.
        3. No notifications table at all (orchestration-only test DB) —
           silently skip so the gate still routes messages.
        """
        full_sql = (
            "INSERT INTO notifications "
            "(id, priority, content, category, delivered_at, delivery_channel, "
            " delivery_tier, template_id, template_vars, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        minimal_sql = (
            "INSERT INTO notifications "
            "(id, priority, content, category, delivered_at, delivery_channel) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
        full_params = (
            notification_id,
            priority.value,
            text,
            tier,
            when.isoformat(),
            channel.value,
            tier,
            template_id,
            json.dumps(template_vars),
            reason,
        )
        minimal_params = (
            notification_id,
            priority.value,
            text,
            tier,
            when.isoformat(),
            channel.value,
        )
        try:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(full_sql, full_params)
                await db.commit()
        except aiosqlite.OperationalError as exc:
            # Fall back: minimal insert (table present, new columns missing).
            log.debug("notifications_full_insert_failed", error=str(exc))
            try:
                async with aiosqlite.connect(str(self._db_path)) as db:
                    await db.execute(minimal_sql, minimal_params)
                    await db.commit()
            except aiosqlite.OperationalError:
                log.debug(
                    "notifications_table_missing",
                    note="gate operating without DB trace",
                )
        # `delivered` is implicit in the presence of `delivered_at`;
        # the core schema represents suppression via NULL timestamps
        # elsewhere, so we keep the row but don't store a separate
        # boolean. Suppress/queue outcomes still get a row via
        # `reason` and `delivery_channel` for auditability.
        _ = delivered  # intentional: kept in signature for callers


# ── DND evaluation helper ────────────────────────────────────────────────


def _in_dnd_now(profile: UserScheduleProfile, now: datetime) -> bool:
    """True if *now* falls inside the profile's DND window."""
    start = getattr(profile, "dnd_start", None)
    end = getattr(profile, "dnd_end", None)
    if start is None or end is None:
        return False
    local = now.astimezone(profile.tz()).time()
    return _in_window(local, start, end)


def _in_window(now_local: time, start: time | None, end: time | None) -> bool:
    if start is None or end is None:
        return False
    if start <= end:
        return start <= now_local < end
    return now_local >= start or now_local < end


_ = timedelta  # keep timedelta in the imports for future cooldown logic
