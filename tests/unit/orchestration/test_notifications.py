"""Unit tests for the Slice 7.5b NotificationGate (spec §10.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kora_v2.runtime.orchestration.notifications import (
    DeliveryChannel,
    GeneratedNotification,
    NotificationGate,
)
from kora_v2.runtime.orchestration.system_state import UserScheduleProfile
from kora_v2.runtime.orchestration.templates import (
    TemplatePriority,
    TemplateRegistry,
)


@pytest.fixture
def template_registry(tmp_path: Path) -> TemplateRegistry:
    reg = TemplateRegistry(tmp_path / "templates")
    reg.ensure_defaults()
    reg.reload_if_changed()
    return reg


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    # Gate tolerates a missing notifications table; an empty file keeps
    # the aiosqlite.connect() call alive and exercises the error paths.
    p = tmp_path / "operational.db"
    p.touch()
    return p


def _make_gate(
    *,
    db_path: Path,
    templates: TemplateRegistry,
    profile: UserScheduleProfile | None = None,
    session_active: bool = True,
    hyperfocus: bool = False,
    broadcast_sink: list[dict] | None = None,
) -> NotificationGate:
    async def broadcast(payload: dict) -> None:
        if broadcast_sink is not None:
            broadcast_sink.append(payload)

    return NotificationGate(
        db_path=db_path,
        templates=templates,
        schedule_profile=profile,
        websocket_broadcast=broadcast,
        session_active_fn=lambda: session_active,
        hyperfocus_active_fn=lambda: hyperfocus,
    )


async def test_send_llm_delivers_via_websocket(
    db_path: Path, template_registry: TemplateRegistry
) -> None:
    sink: list[dict] = []
    gate = _make_gate(
        db_path=db_path, templates=template_registry, broadcast_sink=sink
    )
    result = await gate.send_llm(
        GeneratedNotification(text="hello", priority=TemplatePriority.MEDIUM),
    )
    assert result.delivered
    assert result.channel is DeliveryChannel.WEBSOCKET
    assert sink and sink[0]["text"] == "hello"
    assert sink[0]["tier"] == "llm"


async def test_send_templated_renders_and_delivers(
    db_path: Path, template_registry: TemplateRegistry
) -> None:
    sink: list[dict] = []
    gate = _make_gate(
        db_path=db_path, templates=template_registry, broadcast_sink=sink
    )
    result = await gate.send_templated("rate_limit_paused", minutes=7)
    assert result.delivered
    assert result.tier == "templated"
    assert result.template_id == "rate_limit_paused"
    assert "7" in result.text
    assert sink and sink[0]["template_id"] == "rate_limit_paused"


async def test_suppress_until_blocks_non_bypass(
    db_path: Path, template_registry: TemplateRegistry
) -> None:
    gate = _make_gate(db_path=db_path, templates=template_registry)
    future = datetime.now(UTC) + timedelta(minutes=5)
    await gate.suppress_until(future, reason="manual")
    result = await gate.send_llm(
        GeneratedNotification(text="blocked?", priority=TemplatePriority.MEDIUM),
    )
    assert not result.delivered
    assert result.channel is DeliveryChannel.SUPPRESSED
    assert result.reason.startswith("suppressed:manual")


async def test_suppress_until_bypassed_for_bypass_dnd_templates(
    db_path: Path, template_registry: TemplateRegistry
) -> None:
    sink: list[dict] = []
    gate = _make_gate(
        db_path=db_path, templates=template_registry, broadcast_sink=sink
    )
    future = datetime.now(UTC) + timedelta(minutes=5)
    await gate.suppress_until(future, reason="manual")
    # background_digest_ready ships with bypass_dnd=True
    result = await gate.send_templated("background_digest_ready", count=3)
    assert result.delivered
    assert result.channel is DeliveryChannel.WEBSOCKET


async def test_clear_suppression_restores_delivery(
    db_path: Path, template_registry: TemplateRegistry
) -> None:
    gate = _make_gate(db_path=db_path, templates=template_registry)
    await gate.suppress_until(
        datetime.now(UTC) + timedelta(minutes=5), reason="x"
    )
    gate.clear_suppression()
    result = await gate.send_llm(
        GeneratedNotification(text="ok", priority=TemplatePriority.LOW),
    )
    assert result.delivered


async def test_hyperfocus_suppresses_non_bypass(
    db_path: Path, template_registry: TemplateRegistry
) -> None:
    profile = UserScheduleProfile(timezone="UTC", hyperfocus_suppression=True)
    gate = _make_gate(
        db_path=db_path,
        templates=template_registry,
        profile=profile,
        hyperfocus=True,
    )
    result = await gate.send_llm(
        GeneratedNotification(text="busy", priority=TemplatePriority.MEDIUM),
    )
    assert not result.delivered
    assert result.reason == "hyperfocus"


async def test_hyperfocus_suppresses_even_when_bypass_dnd_true(
    db_path: Path, template_registry: TemplateRegistry
) -> None:
    """Spec §10: hyperfocus is independent of bypass_dnd.

    ``bypass_dnd=True`` only overrides the DND window. Hyperfocus
    suppression is a separate axis and stays in effect regardless.
    """
    profile = UserScheduleProfile(timezone="UTC", hyperfocus_suppression=True)
    gate = _make_gate(
        db_path=db_path,
        templates=template_registry,
        profile=profile,
        hyperfocus=True,
    )
    # background_digest_ready ships with bypass_dnd=True — it must
    # still be suppressed during hyperfocus.
    result = await gate.send_templated("background_digest_ready", count=1)
    assert not result.delivered
    assert result.reason == "hyperfocus"


async def test_hyperfocus_suppression_disabled_via_profile(
    db_path: Path, template_registry: TemplateRegistry
) -> None:
    """When ``hyperfocus_suppression=False``, hyperfocus is ignored."""
    profile = UserScheduleProfile(timezone="UTC", hyperfocus_suppression=False)
    gate = _make_gate(
        db_path=db_path,
        templates=template_registry,
        profile=profile,
        hyperfocus=True,
    )
    result = await gate.send_llm(
        GeneratedNotification(text="ok", priority=TemplatePriority.LOW),
    )
    assert result.delivered


async def test_dnd_window_queues_rather_than_delivers(
    db_path: Path, template_registry: TemplateRegistry
) -> None:
    # Local time is UTC; build a DND window that includes "right now".
    now_t = datetime.now(UTC).time()
    # +/- 30 minutes guarantees "now" is inside
    start = (datetime.now(UTC) - timedelta(minutes=30)).time()
    end = (datetime.now(UTC) + timedelta(minutes=30)).time()
    _ = now_t
    profile = UserScheduleProfile(
        timezone="UTC",
        dnd_start=start,
        dnd_end=end,
    )
    gate = _make_gate(
        db_path=db_path, templates=template_registry, profile=profile
    )
    result = await gate.send_llm(
        GeneratedNotification(text="wake up", priority=TemplatePriority.HIGH),
    )
    assert not result.delivered
    assert result.channel is DeliveryChannel.QUEUE
    assert result.reason == "dnd_queued"
    # queued items are drainable
    pending = gate.drain()
    assert len(pending) == 1
    assert pending[0].message == "wake up"


async def test_turn_response_channel_marks_delivered_without_broadcast(
    db_path: Path, template_registry: TemplateRegistry
) -> None:
    gate = _make_gate(db_path=db_path, templates=template_registry)
    result = await gate.send_templated(
        "task_started",
        via=DeliveryChannel.TURN_RESPONSE,
        goal="X",
    )
    assert result.delivered
    assert result.channel is DeliveryChannel.TURN_RESPONSE


async def test_queue_fallback_when_no_session(
    db_path: Path, template_registry: TemplateRegistry
) -> None:
    gate = _make_gate(
        db_path=db_path, templates=template_registry, session_active=False
    )
    result = await gate.send_llm(
        GeneratedNotification(text="queued", priority=TemplatePriority.MEDIUM),
    )
    # No session → WEBSOCKET branch falls through to QUEUE default.
    assert not result.delivered
    assert result.channel is DeliveryChannel.QUEUE
    assert len(gate) == 1
