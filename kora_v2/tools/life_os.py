"""Life OS tools for Kora V2.

These tools expose the plan-reality-repair-bridge loop through the normal
ToolRegistry path. The services own the durable state; tools stay thin.
"""

import json
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, Field

from kora_v2.tools.registry import tool
from kora_v2.tools.types import AuthLevel, ToolCategory


def _ok(payload: dict[str, Any]) -> str:
    payload.setdefault("success", True)
    return json.dumps(payload, default=str)


def _err(message: str) -> str:
    return json.dumps({"success": False, "error": message})


def _parse_day(value: str | None) -> date:
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(UTC).date()


class CreateDayPlanInput(BaseModel):
    plan_date: str = Field("", description="Local date to plan, YYYY-MM-DD. Empty means today.")
    source: str = Field("conversation", description="Why the plan is being created or refreshed.")


class ConfirmRealityInput(BaseModel):
    text: str = Field(..., description="User's reality confirmation, correction, or status update.")
    day_plan_entry_id: str = Field("", description="Optional day plan entry id if known.")
    event_type: str = Field("reality_update", description="Event type such as medication, meal, partial, skipped, blocked.")
    reality_state: str = Field("unknown", description="done, partial, skipped, blocked, rejected, or unknown.")
    title: str = Field("", description="Short human title for the event.")


class CorrectRealityInput(BaseModel):
    event_id: str = Field(..., description="Life event id to correct or reject.")
    correction_text: str = Field(..., description="What the user corrected.")
    reality_state: str = Field("corrected", description="Corrected state, often rejected/not_done/partial/skipped.")


class AssessLoadInput(BaseModel):
    assessment_date: str = Field("", description="Local date to assess, YYYY-MM-DD. Empty means today.")
    force: bool = Field(False, description="Force a new assessment instead of reusing recent state.")


class RepairDayInput(BaseModel):
    plan_date: str = Field("", description="Local date to repair, YYYY-MM-DD. Empty means today.")
    apply_safe_repairs: bool = Field(True, description="Apply internal safe repairs automatically.")
    user_confirmed: bool = Field(False, description="Whether user approved confirmation-required repairs.")


class NudgeDecisionInput(BaseModel):
    candidate_type: str = Field(..., description="Type of proactive candidate.")
    payload: dict[str, Any] = Field(default_factory=dict, description="Candidate payload.")
    urgency: str = Field("normal", description="low, normal, high, critical.")
    support_tags: list[str] = Field(default_factory=list, description="Support tags such as anxiety or sensory.")


class NudgeFeedbackInput(BaseModel):
    nudge_decision_id: str = Field(..., description="Decision id to attach feedback to.")
    feedback: str = Field(..., description="helpful, too_much, wrong, bad_timing, done, not_done, reschedule, stop_this_type.")
    details: str = Field("", description="Optional user details.")


class ContextPackInput(BaseModel):
    title: str = Field(..., description="Context pack title.")
    pack_type: str = Field("admin", description="admin, anxiety, sensory, appointment, communication.")
    calendar_entry_id: str = Field("", description="Optional linked calendar entry.")
    item_id: str = Field("", description="Optional linked item.")
    summary: str = Field("", description="Optional situation summary.")


class BridgeTomorrowInput(BaseModel):
    bridge_date: str = Field("", description="Local date being bridged from, YYYY-MM-DD. Empty means today.")


class SupportProfileInput(BaseModel):
    profile_key: str = Field(..., description="Profile key such as adhd, anxiety, autism_sensory, low_energy, burnout.")
    status: str = Field("active", description="active, suggested, disabled, archived.")


class CrisisCheckInput(BaseModel):
    text: str = Field(..., description="User text to check against the crisis safety boundary.")
    preempted_flow: str = Field("", description="The normal workflow that would have run.")


@tool(
    name="create_day_plan",
    description="Create or refresh the active local Life OS day plan from calendar, items, reminders, routines, and current load.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def create_day_plan(input: CreateDayPlanInput, container: Any) -> str:
    service = getattr(container, "day_plan_service", None)
    if service is None:
        return _err("day plan service unavailable")
    plan = await service.create_or_refresh_day_plan(_parse_day(input.plan_date), source=input.source)
    return _ok({"day_plan": _dump(plan)})


@tool(
    name="confirm_reality",
    description="Record what actually happened today and update the linked day-plan entry when possible.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def confirm_reality(input: ConfirmRealityInput, container: Any) -> str:
    from kora_v2.life.models import RecordLifeEventInput

    ledger = getattr(container, "life_event_ledger", None)
    if ledger is None:
        return _err("life event ledger unavailable")
    event = await ledger.record_event(RecordLifeEventInput(
        event_type=input.event_type,
        title=input.title or input.text[:80],
        raw_text=input.text,
        details=input.text,
        source="user_confirmed",
        confirmation_state="confirmed",
        day_plan_entry_id=input.day_plan_entry_id or None,
    ))
    entry = None
    if input.day_plan_entry_id and input.reality_state != "unknown":
        service = getattr(container, "day_plan_service", None)
        if service is not None:
            entry = await service.mark_entry_reality(
                input.day_plan_entry_id,
                _normalize_reality_state(input.reality_state),
                getattr(event, "id", None) or event.get("id"),
            )
    return _ok({"life_event": _dump(event), "day_plan_entry": _dump(entry)})


@tool(
    name="correct_reality",
    description="Correct or reject a prior Kora reality inference with durable correction history.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def correct_reality(input: CorrectRealityInput, container: Any) -> str:
    from kora_v2.life.models import CorrectionInput

    ledger = getattr(container, "life_event_ledger", None)
    if ledger is None:
        return _err("life event ledger unavailable")
    event = await ledger.correct_event(
        input.event_id,
        CorrectionInput(
            details=input.correction_text,
            raw_text=input.correction_text,
            metadata={"reality_state": input.reality_state},
        ),
    )
    return _ok({"life_event": _dump(event)})


@tool(
    name="assess_life_load",
    description="Assess today's Life Load Meter band with explainable factors.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def assess_life_load(input: AssessLoadInput, container: Any) -> str:
    service = getattr(container, "life_load_engine", None)
    if service is None:
        return _err("life load engine unavailable")
    assessment = await service.assess_day(_parse_day(input.assessment_date), force=input.force)
    return _ok({"load_assessment": _dump(assessment)})


@tool(
    name="repair_day_plan",
    description="Detect plan-vs-reality divergence, propose repairs, and apply safe private repairs when allowed.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def repair_day_plan(input: RepairDayInput, container: Any) -> str:
    service = getattr(container, "day_repair_engine", None)
    if service is None:
        return _err("day repair engine unavailable")
    evaluation = await service.evaluate(_parse_day(input.plan_date))
    actions = await service.propose(evaluation)
    result = None
    if input.apply_safe_repairs:
        action_ids = [getattr(action, "id", None) or action.get("id") for action in actions]
        result = await service.apply([a for a in action_ids if a], user_confirmed=input.user_confirmed)
    return _ok({"evaluation": _dump(evaluation), "repair_actions": _dump(actions), "repair_result": _dump(result)})


@tool(
    name="decide_life_nudge",
    description="Run a proactive candidate through Kora's central Life OS nudge policy and persist the decision.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def decide_life_nudge(input: NudgeDecisionInput, container: Any) -> str:
    from kora_v2.life.proactivity_policy import NudgeCandidate

    service = getattr(container, "proactivity_policy_engine", None)
    if service is None:
        return _err("proactivity policy engine unavailable")
    decision = await service.decide(NudgeCandidate(
        candidate_type=input.candidate_type,
        payload=input.payload,
        urgency=input.urgency,
        support_tags=input.support_tags,
    ))
    return _ok({"nudge_decision": _dump(decision)})


@tool(
    name="record_nudge_feedback",
    description="Record user feedback that a Life OS nudge was helpful, wrong, too much, or badly timed.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def record_nudge_feedback(input: NudgeFeedbackInput, container: Any) -> str:
    from kora_v2.life.proactivity_policy import NudgeFeedbackInput as PolicyFeedbackInput

    service = getattr(container, "proactivity_policy_engine", None)
    if service is None:
        return _err("proactivity policy engine unavailable")
    result = await service.record_feedback(
        input.nudge_decision_id,
        PolicyFeedbackInput(feedback=input.feedback, details=input.details),
    )
    return _ok({"feedback": _dump(result)})


@tool(
    name="create_context_pack",
    description="Create a Life OS context pack for anxiety-prone, admin, sensory-heavy, appointment, or communication tasks.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def create_context_pack(input: ContextPackInput, container: Any) -> str:
    service = getattr(container, "context_pack_service", None)
    if service is None:
        return _err("context pack service unavailable")
    from kora_v2.life.context_packs import ContextPackTarget

    pack = await service.build_pack(ContextPackTarget(
        title=input.title,
        pack_type=input.pack_type,
        calendar_entry_id=input.calendar_entry_id or None,
        item_id=input.item_id or None,
        description=input.summary,
    ))
    return _ok({"context_pack": _dump(pack)})


@tool(
    name="bridge_tomorrow",
    description="Generate a shame-safe Future Self Bridge from today's active day plan into tomorrow.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def bridge_tomorrow(input: BridgeTomorrowInput, container: Any) -> str:
    service = getattr(container, "future_self_bridge_service", None)
    if service is None:
        return _err("future self bridge service unavailable")
    bridge = await service.build_bridge(_parse_day(input.bridge_date))
    return _ok({"future_self_bridge": _dump(bridge)})


@tool(
    name="set_support_profile_status",
    description="Activate, suggest, disable, or archive a Life OS support profile. Condition-specific profiles affect runtime only when active.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ASK_FIRST,
    is_read_only=False,
)
async def set_support_profile_status(input: SupportProfileInput, container: Any) -> str:
    registry = getattr(container, "support_registry", None)
    if registry is None:
        return _err("support registry unavailable")
    profile = await registry.set_profile_status(input.profile_key, input.status)
    return _ok({"support_profile": _dump(profile)})


@tool(
    name="check_crisis_boundary",
    description="Check whether crisis language preempts normal planning, repair, or proactivity workflows.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def check_crisis_boundary(input: CrisisCheckInput, container: Any) -> str:
    router = getattr(container, "crisis_safety_router", None)
    if router is None:
        return _err("crisis safety router unavailable")
    result = await router.route(input.text, preempted_flow=input.preempted_flow or None)
    return _ok({"crisis_boundary": _dump(result)})


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        return [_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return {
            k: _dump(v)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }
    return value


def _normalize_reality_state(value: str) -> str:
    normalized = (value or "unknown").strip().lower()
    return {
        "done": "confirmed_done",
        "complete": "confirmed_done",
        "completed": "confirmed_done",
        "partial": "confirmed_partial",
        "half": "confirmed_partial",
        "skipped": "confirmed_skipped",
        "missed": "confirmed_skipped",
        "blocked": "confirmed_blocked",
        "rejected": "rejected_inference",
        "not_done": "rejected_inference",
        "no": "rejected_inference",
    }.get(normalized, normalized)
