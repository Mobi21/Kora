import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kora_v2.tools.life_os import (
    BridgeTomorrowInput,
    ConfirmRealityInput,
    ContextPackInput,
    _normalize_reality_state,
    bridge_tomorrow,
    confirm_reality,
    create_context_pack,
)


def test_normalize_reality_state_accepts_common_model_words() -> None:
    assert _normalize_reality_state("confirmed") == "confirmed_done"
    assert _normalize_reality_state("corrected") == "rejected_inference"


@pytest.mark.asyncio
async def test_confirm_reality_activates_adhd_support_profile() -> None:
    registry = SimpleNamespace(
        set_profile_status=AsyncMock(),
        record_signal=AsyncMock(return_value="signal-adhd"),
    )
    ledger = SimpleNamespace(
        record_event=AsyncMock(return_value={"id": "event-adhd"}),
    )
    container = SimpleNamespace(
        support_registry=registry,
        life_event_ledger=ledger,
    )

    result = await confirm_reality(
        ConfirmRealityInput(
            text="I missed lunch and avoided the email because task initiation got hard.",
            event_type="missed_task",
            reality_state="blocked",
            title="Reality update",
        ),
        container,
    )

    data = json.loads(result)
    assert data["success"] is True
    registry.set_profile_status.assert_awaited_with(
        "adhd",
        "active",
        source="reality_confirmation",
        reason="executive_function_need observed",
    )
    registry.record_signal.assert_awaited()


@pytest.mark.asyncio
async def test_sensory_context_pack_activates_autism_sensory_profile() -> None:
    registry = SimpleNamespace(
        set_profile_status=AsyncMock(),
        record_signal=AsyncMock(return_value="signal-sensory"),
    )
    context_pack_service = SimpleNamespace(
        build_pack=AsyncMock(return_value=SimpleNamespace(id="pack-1")),
    )
    container = SimpleNamespace(
        support_registry=registry,
        context_pack_service=context_pack_service,
        life_event_ledger=None,
    )

    result = await create_context_pack(
        ContextPackInput(
            title="Low-ambiguity transition support",
            pack_type="sensory",
            summary="Noise and transitions are the blocker.",
        ),
        container,
    )

    data = json.loads(result)
    assert data["success"] is True
    registry.set_profile_status.assert_awaited_with(
        "autism_sensory",
        "active",
        source="context_pack",
        reason="sensory_or_transition_need observed",
    )
    registry.record_signal.assert_awaited()


@pytest.mark.asyncio
async def test_bridge_tomorrow_uses_future_bridge_service() -> None:
    future_bridge_service = SimpleNamespace(
        build_bridge=AsyncMock(return_value=SimpleNamespace(id="bridge-1")),
    )
    container = SimpleNamespace(future_self_bridge_service=future_bridge_service)

    result = await bridge_tomorrow(BridgeTomorrowInput(bridge_date="2026-04-27"), container)

    data = json.loads(result)
    assert data["success"] is True
    future_bridge_service.build_bridge.assert_awaited_once_with(date(2026, 4, 27))
