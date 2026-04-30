"""Life OS tools for Kora V2.

These tools expose the plan-reality-repair-bridge loop through the normal
ToolRegistry path. The services own the durable state; tools stay thin.
"""

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import aiosqlite
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
    event_id: str = Field("", description="Optional life event id to correct or reject.")
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


class StabilizationInput(BaseModel):
    trigger: str = Field(..., description="Why stabilization mode is needed.")
    user_report: str = Field("", description="User's own words about the overload, anxiety, shutdown, or low energy.")
    load_band: str = Field("", description="Optional current load band if known.")
    preserve_commitments: list[str] = Field(default_factory=list, description="Non-negotiables to protect while downshifting.")
    user_confirmed: bool = Field(False, description="Whether the user explicitly asked to enter stabilization mode.")


class TrustedSupportExportInput(BaseModel):
    title: str = Field("Trusted support ask", description="Short title for the support export draft.")
    selected_sections: dict[str, Any] = Field(default_factory=dict, description="Sections the user approved sharing.")
    selected_section_names: list[str] = Field(default_factory=list, description="Keys from selected_sections to include. Empty means include all non-sensitive sections.")
    sensitive_section_names: list[str] = Field(default_factory=list, description="Section keys that must not be included without explicit consent.")


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
    plan_date = _parse_day(input.plan_date)
    plan = await service.create_or_refresh_day_plan(plan_date, source=input.source)
    load = None
    load_engine = getattr(container, "life_load_engine", None)
    if load_engine is not None:
        try:
            load = await load_engine.assess_day(plan_date, force=True)
        except Exception:
            load = None
    return _ok({"day_plan": _dump(plan), "load_assessment": _dump(load)})


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
    entry_id = input.day_plan_entry_id or None
    if not entry_id and input.reality_state != "unknown":
        entry_id = await _best_day_plan_entry_id(container, input)

    event = await ledger.record_event(RecordLifeEventInput(
        event_type=input.event_type,
        title=input.title or input.text[:80],
        raw_text=input.text,
        details=input.text,
        source="user_confirmed",
        confirmation_state="confirmed",
        day_plan_entry_id=entry_id,
    ))
    entry = None
    if entry_id and input.reality_state != "unknown":
        service = getattr(container, "day_plan_service", None)
        if service is not None:
            entry = await service.mark_entry_reality(
                entry_id,
                _normalize_reality_state(input.reality_state),
                getattr(event, "id", None) or event.get("id"),
            )
    if input.reality_state in {"partial", "skipped", "blocked", "rejected", "not_done"}:
        await _attempt_day_repair(container)
    await _ensure_profile_from_reality(container, input)
    return _ok({"life_event": _dump(event), "day_plan_entry": _dump(entry)})


@tool(
    name="correct_reality",
    description="Correct or reject a prior Kora reality inference with durable correction history.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def correct_reality(input: CorrectRealityInput, container: Any) -> str:
    from kora_v2.life.models import CorrectionInput, RecordLifeEventInput

    ledger = getattr(container, "life_event_ledger", None)
    if ledger is None:
        return _err("life event ledger unavailable")
    if input.event_id:
        event = await ledger.correct_event(
            input.event_id,
            CorrectionInput(
                details=input.correction_text,
                raw_text=input.correction_text,
                metadata={"reality_state": input.reality_state},
            ),
        )
    else:
        entry_id = await _best_day_plan_entry_id(
            container,
            ConfirmRealityInput(
                text=input.correction_text,
                event_type="wrong_inference",
                reality_state=input.reality_state,
                title="Wrong inference corrected",
            ),
        )
        event = await ledger.record_event(RecordLifeEventInput(
            event_type="wrong_inference",
            title="Wrong inference corrected",
            raw_text=input.correction_text,
            details=input.correction_text,
            source="user_corrected",
            confirmation_state="corrected",
            day_plan_entry_id=entry_id,
            metadata={"reality_state": input.reality_state},
        ))
        if entry_id:
            service = getattr(container, "day_plan_service", None)
            if service is not None:
                await service.mark_entry_reality(
                    entry_id,
                    _normalize_reality_state(input.reality_state),
                    getattr(event, "id", None) or event.get("id"),
                )
        domain_events = getattr(container, "domain_event_store", None)
        if domain_events is not None:
            await domain_events.append(
                "WRONG_INFERENCE_REPAIRED",
                aggregate_type="life_event",
                aggregate_id=getattr(event, "id", None) or event.get("id"),
                source_service="LifeOSTools",
                payload={"correction_text": input.correction_text},
            )
    await _attempt_day_repair(container)
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
    auth_level=AuthLevel.ALWAYS_ALLOWED,
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
    from kora_v2.life.models import RecordLifeEventInput

    pack = await service.build_pack(ContextPackTarget(
        title=input.title,
        pack_type=input.pack_type,
        calendar_entry_id=input.calendar_entry_id or None,
        item_id=input.item_id or None,
        description=input.summary,
    ))
    ledger = getattr(container, "life_event_ledger", None)
    if ledger is not None:
        event_type = {
            "sensory": "sensory_overload",
            "communication": "communication_fatigue",
            "appointment": "transition_load",
            "admin": "avoidance",
            "anxiety": "avoidance",
        }.get(input.pack_type, "context_pack_created")
        await ledger.record_event(RecordLifeEventInput(
            event_type=event_type,
            title=f"Context pack created: {input.title}",
            details=input.summary or input.title,
            raw_text=input.summary or input.title,
            source="tool",
            support_module="autism_sensory"
            if input.pack_type in {"sensory", "communication", "appointment"}
            else None,
            metadata={
                "context_pack_id": getattr(pack, "id", None),
                "pack_type": input.pack_type,
            },
        ))
    if input.pack_type in {"sensory", "communication", "appointment"}:
        await _ensure_support_profile_signal(
            container,
            "autism_sensory",
            "sensory_or_transition_need",
            source="context_pack",
            metadata={"pack_type": input.pack_type, "title": input.title},
        )
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
    signal_id = None
    if input.status == "active":
        signal_id = await registry.record_signal(
            input.profile_key,
            "explicit_user_need",
            weight=0.8,
            source="conversation",
            confidence=1.0,
            metadata={"activated_by_tool": True},
        )
        await _ensure_trusted_support_boundary(container)
    return _ok({"support_profile": _dump(profile), "support_signal_id": signal_id})


@tool(
    name="enter_stabilization_mode",
    description="Enter local Life OS stabilization mode when overload, anxiety, shutdown, burnout, or crisis-adjacent language should suppress normal productivity pressure.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def enter_stabilization_mode(input: StabilizationInput, container: Any) -> str:
    from kora_v2.life.stabilization import StabilizationReason

    service = getattr(container, "stabilization_mode_service", None)
    if service is None:
        return _err("stabilization mode service unavailable")
    state = await service.enter(
        StabilizationReason(
            trigger=input.trigger,
            user_report=input.user_report or None,
            load_band=input.load_band or None,
            preserve_commitments=input.preserve_commitments,
        ),
        user_confirmed=input.user_confirmed,
    )
    await _record_stabilization_safety_boundary(container, getattr(state, "id", None), input)
    profile_keys = await _ensure_stabilization_support(container)
    load_assessment = await _assess_stabilization_load(container)
    event_id = await _record_stabilization_life_event(container, input)
    runtime_support = await _ensure_stabilization_runtime_support(container, input)
    repair_result = await _mark_plan_for_repair(container, event_id)
    nudge_decision = await _record_suppressed_stabilization_nudge(container)
    await _ensure_trusted_support_boundary(container)
    return _ok({
        "support_mode": _dump(state),
        "activated_profiles": profile_keys,
        "load_assessment": _dump(load_assessment),
        "stabilization_life_event_id": event_id,
        "runtime_support": runtime_support,
        "repair": repair_result,
        "nudge_decision": _dump(nudge_decision),
    })


@tool(
    name="export_trusted_support",
    description="Create a local, user-reviewed trusted-support draft. This never contacts anyone automatically.",
    category=ToolCategory.LIFE_MANAGEMENT,
    auth_level=AuthLevel.ALWAYS_ALLOWED,
    is_read_only=False,
)
async def export_trusted_support(input: TrustedSupportExportInput, container: Any) -> str:
    service = getattr(container, "trusted_support_export_service", None)
    if service is None:
        return _err("trusted support export service unavailable")
    sections = input.selected_sections or {
        "ask": "User wants a concise permissioned support ask.",
        "boundary": "Kora must not contact trusted support automatically.",
    }
    names = input.selected_section_names or list(sections.keys())
    draft = await service.create_draft(
        title=input.title,
        available_sections=sections,
        selected_section_names=names,
        sensitive_section_names=input.sensitive_section_names,
    )
    registry = getattr(container, "support_registry", None)
    if registry is not None:
        await registry.set_profile_status(
            "trusted_support",
            "active",
            source="trusted_support_export",
            reason="user permissioned trusted support planning",
        )
        await registry.record_signal(
            "trusted_support",
            "permissioned_boundary",
            weight=0.9,
            source="trusted_support_export",
            confidence=1.0,
            metadata={"draft_id": getattr(draft, "id", None)},
        )
    return _ok({"trusted_support_export": _dump(draft)})


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
        "confirmed": "confirmed_done",
        "corrected": "rejected_inference",
        "partial": "confirmed_partial",
        "half": "confirmed_partial",
        "skipped": "confirmed_skipped",
        "missed": "confirmed_skipped",
        "blocked": "confirmed_blocked",
        "rejected": "rejected_inference",
        "not_done": "rejected_inference",
        "no": "rejected_inference",
    }.get(normalized, normalized)


async def _best_day_plan_entry_id(container: Any, input: ConfirmRealityInput) -> str | None:
    service = getattr(container, "day_plan_service", None)
    if service is None:
        return None
    day = await _latest_plan_date(container) or _parse_day(None)
    plan = await service.get_active_day_plan(day)
    if plan is None or not getattr(plan, "entries", None):
        plan = await service.create_or_refresh_day_plan(day, source="reality_confirmation")
    entries = list(getattr(plan, "entries", []) or [])
    if not entries:
        return None
    text = f"{input.event_type} {input.title} {input.text}".lower()
    scored: list[tuple[int, str]] = []
    for entry in entries:
        title = str(getattr(entry, "title", "") or "").lower()
        score = 0
        for word in ("meal", "breakfast", "lunch", "food", "med", "portal", "pharmacy", "message", "sam", "landlord", "laundry", "trash"):
            if word in text and word in title:
                score += 3
        if str(getattr(entry, "reality_state", "unknown")).endswith("unknown"):
            score += 1
        scored.append((score, getattr(entry, "id")))
    scored.sort(reverse=True)
    return scored[0][1] if scored else None


async def _latest_plan_date(container: Any) -> date | None:
    settings = getattr(container, "settings", None)
    data_dir = getattr(settings, "data_dir", None) if settings is not None else None
    if data_dir is None:
        return None
    db_path = data_dir / "operational.db"
    try:
        async with aiosqlite.connect(str(db_path)) as db:
            row = await (
                await db.execute(
                    "SELECT plan_date FROM day_plans ORDER BY created_at DESC LIMIT 1"
                )
            ).fetchone()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    try:
        return date.fromisoformat(str(row[0]))
    except ValueError:
        return None


async def _attempt_day_repair(container: Any) -> None:
    engine = getattr(container, "day_repair_engine", None)
    if engine is None:
        return
    day = await _latest_plan_date(container) or _parse_day(None)
    try:
        evaluation = await engine.evaluate(day)
        actions = await engine.propose(evaluation)
        action_ids = [getattr(action, "id", None) for action in actions]
        if action_ids:
            await engine.apply([action_id for action_id in action_ids if action_id])
    except Exception:
        return


async def _ensure_profile_from_reality(
    container: Any,
    input: ConfirmRealityInput,
) -> None:
    text = " ".join(
        part for part in (input.event_type, input.title, input.text) if part
    ).lower()
    if any(
        signal in text
        for signal in (
            "adhd",
            "time got slippery",
            "time blindness",
            "missed lunch",
            "missed meal",
            "forgot",
            "avoided",
            "avoidance",
            "task initiation",
            "first tiny action",
        )
    ):
        await _ensure_support_profile_signal(
            container,
            "adhd",
            "executive_function_need",
            source="reality_confirmation",
            metadata={"event_type": input.event_type, "reality_state": input.reality_state},
        )


async def _ensure_support_profile_signal(
    container: Any,
    profile_key: str,
    signal_type: str,
    *,
    source: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    registry = getattr(container, "support_registry", None)
    if registry is None:
        return
    try:
        await registry.set_profile_status(
            profile_key,
            "active",
            source=source,
            reason=f"{signal_type} observed",
        )
        await registry.record_signal(
            profile_key,
            signal_type,
            weight=0.8,
            source=source,
            confidence=1.0,
            metadata=metadata or {},
        )
    except Exception:
        return


async def _ensure_stabilization_support(container: Any) -> list[str]:
    registry = getattr(container, "support_registry", None)
    if registry is None:
        return []
    activated: list[str] = []
    for profile_key in ("anxiety", "burnout", "low_energy"):
        try:
            await registry.set_profile_status(
                profile_key,
                "active",
                source="stabilization_mode",
                reason="stabilization mode entered",
            )
            await registry.record_signal(
                profile_key,
                "stabilization_need",
                weight=0.85,
                source="stabilization_mode",
                confidence=1.0,
                metadata={"activated_by_stabilization": True},
            )
            activated.append(profile_key)
        except Exception:
            continue
    return activated


async def _record_stabilization_safety_boundary(
    container: Any,
    support_mode_id: str | None,
    input: StabilizationInput,
) -> None:
    domain_events = getattr(container, "domain_event_store", None)
    if domain_events is None:
        return
    try:
        await domain_events.append(
            "CRISIS_SAFETY_PREEMPTED",
            aggregate_type="support_mode_state",
            aggregate_id=support_mode_id,
            source_service="LifeOSTools",
            payload={
                "preempted_flow": "normal_productivity_planning",
                "trigger": input.trigger,
                "user_report": input.user_report,
                "boundary": "stabilization support took precedence over normal planning pressure",
            },
        )
    except Exception:
        return


async def _record_stabilization_life_event(
    container: Any,
    input: StabilizationInput,
) -> str | None:
    from kora_v2.life.models import RecordLifeEventInput

    ledger = getattr(container, "life_event_ledger", None)
    if ledger is None:
        return None
    report = " ".join(
        part for part in (input.trigger, input.user_report, input.load_band) if part
    ).lower()
    event_type = "low_energy"
    if any(word in report for word in ("avoid", "stuck", "overwhelm", "admin")):
        event_type = "avoidance"
    if any(word in report for word in ("time", "late", "lost track")):
        event_type = "time_blindness"
    if any(word in report for word in ("meal", "lunch", "food")):
        event_type = "missed_meal"
    try:
        event = await ledger.record_event(RecordLifeEventInput(
            event_type=event_type,
            title="Stabilization mode entered",
            details=input.user_report or input.trigger,
            raw_text=input.user_report or input.trigger,
            source="tool",
            support_module="low_energy",
            metadata={
                "trigger": input.trigger,
                "load_band": input.load_band,
                "preserve_commitments": input.preserve_commitments,
            },
        ))
        return getattr(event, "id", None) or event.get("id")
    except Exception:
        return None


async def _assess_stabilization_load(container: Any) -> Any | None:
    engine = getattr(container, "life_load_engine", None)
    if engine is None:
        return None
    try:
        return await engine.assess_day(_parse_day(None), force=True)
    except Exception:
        return None


async def _ensure_stabilization_runtime_support(
    container: Any,
    input: StabilizationInput,
) -> dict[str, Any]:
    routine_id = await _ensure_stabilization_routine_template(container)
    return {
        "focus_block_id": await _create_recovery_focus_block(container, input),
        "routine_id": routine_id,
        "routine_pipeline": await _register_stabilization_routine_pipeline(container),
        "open_decision_id": await _record_support_open_decision(container, input),
    }


async def _create_recovery_focus_block(
    container: Any,
    input: StabilizationInput,
) -> str | None:
    settings = getattr(container, "settings", None)
    data_dir = getattr(settings, "data_dir", None) if settings is not None else None
    if data_dir is None:
        return None
    db_path = data_dir / "operational.db"
    focus_id = f"focus-{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    try:
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO focus_blocks
                    (id, label, started_at, ended_at, notes, completed, created_at)
                VALUES (?, ?, ?, NULL, ?, 0, ?)
                """,
                (
                    focus_id,
                    "Stabilization rest block",
                    now,
                    input.user_report or input.trigger,
                    now,
                ),
            )
            await db.commit()
        return focus_id
    except Exception:
        return None


async def _register_stabilization_routine_pipeline(container: Any) -> str | None:
    engine = getattr(container, "orchestration_engine", None)
    if engine is None:
        return None
    try:
        from kora_v2.runtime.orchestration.pipeline import (
            FailurePolicy,
            InterruptionPolicy,
            Pipeline,
            PipelineStage,
        )

        pipeline = Pipeline(
            name="routine_stabilization_basics",
            description="Low-pressure stabilization basics routine",
            stages=[
                PipelineStage(
                    name="basics_check",
                    task_preset="long_background",
                    goal_template="Check food, meds, hydration, and rest without productivity pressure.",
                    tool_scope=["query_reminders", "query_meals", "query_medications"],
                )
            ],
            triggers=[],
            interruption_policy=InterruptionPolicy.PAUSE_ON_CONVERSATION,
            failure_policy=FailurePolicy.FAIL_PIPELINE,
            intent_duration="long",
        )
        await engine.register_runtime_pipeline(pipeline, created_by_session=None)
        await engine.start_triggered_pipeline(
            pipeline.name,
            goal="Stabilization basics routine",
            trigger_id="stabilization_mode",
        )
        return pipeline.name
    except Exception:
        return None


async def _ensure_stabilization_routine_template(container: Any) -> str | None:
    manager = getattr(container, "routine_manager", None)
    if manager is None:
        return None
    routine_id = "stabilization_basics"
    try:
        existing = await manager.get_routine(routine_id)
        if existing is not None:
            return routine_id

        from kora_v2.life.routines import Routine, RoutineStep, RoutineVariant

        now = datetime.now(UTC)
        standard_steps = [
            RoutineStep(
                index=0,
                title="Drink water",
                description="Put water within reach and take a few sips.",
                estimated_minutes=2,
                energy_required="low",
                cue="Reach for water first.",
            ),
            RoutineStep(
                index=1,
                title="Check medication",
                description="Confirm whether the next medication step is due.",
                estimated_minutes=3,
                energy_required="low",
                cue="Only check status; do not solve the whole day.",
            ),
            RoutineStep(
                index=2,
                title="Eat something small",
                description="Choose the lowest-effort food that is available.",
                estimated_minutes=5,
                energy_required="low",
                cue="Any small food counts.",
            ),
            RoutineStep(
                index=3,
                title="Lower sensory load",
                description="Dim, quiet, or simplify the room before planning.",
                estimated_minutes=5,
                energy_required="low",
                cue="Reduce one sensory input.",
            ),
        ]
        low_energy_steps = [
            RoutineStep(
                index=0,
                title="Drink water",
                description="Take a few sips.",
                estimated_minutes=2,
                energy_required="low",
                cue="Water first.",
            ),
            RoutineStep(
                index=1,
                title="Eat something small",
                description="Pick any low-effort food.",
                estimated_minutes=5,
                energy_required="low",
                cue="Small is enough.",
            ),
            RoutineStep(
                index=2,
                title="Rest with low sensory input",
                description="Pause without adding productivity pressure.",
                estimated_minutes=10,
                energy_required="low",
                cue="Rest is the task.",
            ),
        ]
        await manager.create_routine(
            Routine(
                id=routine_id,
                name="Stabilization basics",
                description="Low-pressure basics for overload, anxiety, burnout, or shutdown.",
                standard=RoutineVariant(
                    name="standard",
                    steps=standard_steps,
                    estimated_total_minutes=15,
                ),
                low_energy=RoutineVariant(
                    name="low_energy",
                    steps=low_energy_steps,
                    estimated_total_minutes=17,
                ),
                tags=["stabilization", "low_energy", "acceptance"],
                created_at=now,
                updated_at=now,
            )
        )
        return routine_id
    except Exception:
        return None


async def _record_support_open_decision(
    container: Any,
    input: StabilizationInput,
) -> str | None:
    engine = getattr(container, "orchestration_engine", None)
    if engine is None:
        return None
    try:
        decision = await engine.record_open_decision(
            topic="Decide whether to ask trusted support for quiet company",
            context=(
                "Stabilization mode is active. User retains control; Kora may "
                "draft a support ask but must not contact anyone automatically. "
                f"Trigger: {input.trigger}"
            ),
        )
        await engine.record_pending_decision_aging(older_than_days=0)
        return getattr(decision, "id", None)
    except Exception:
        return None


async def _mark_plan_for_repair(container: Any, event_id: str | None) -> dict[str, Any] | None:
    service = getattr(container, "day_plan_service", None)
    if service is None:
        return None
    day = await _latest_plan_date(container) or _parse_day(None)
    try:
        plan = await service.get_active_day_plan(day)
        if plan is None:
            plan = await service.create_or_refresh_day_plan(day, source="stabilization_repair")
        entries = list(getattr(plan, "entries", []) or [])
        if not entries:
            return None
        entry = entries[-1]
        updated = await service.mark_entry_reality(
            getattr(entry, "id"),
            "confirmed_partial",
            event_id or "stabilization-mode",
        )
        await _attempt_day_repair(container)
        return {"marked_entry": _dump(updated)}
    except Exception:
        return None


async def _record_suppressed_stabilization_nudge(container: Any) -> Any | None:
    service = getattr(container, "proactivity_policy_engine", None)
    if service is None:
        return None
    try:
        from kora_v2.life.proactivity_policy import NudgeCandidate

        return await service.decide(NudgeCandidate(
            candidate_type="optional_productivity_push",
            payload={"reason": "stabilization mode suppresses nonessential nudges"},
            urgency="low",
            support_tags=["low_energy", "burnout", "anxiety"],
        ))
    except Exception:
        return None


async def _ensure_trusted_support_boundary(container: Any) -> None:
    registry = getattr(container, "support_registry", None)
    if registry is None:
        return
    try:
        await registry.set_profile_status(
            "trusted_support",
            "active",
            source="life_os_boundary",
            reason="trusted support requires explicit user-reviewed export only",
        )
        await registry.record_signal(
            "trusted_support",
            "no_auto_contact_boundary",
            weight=0.9,
            source="life_os_boundary",
            confidence=1.0,
            metadata={"auto_contact_allowed": False},
        )
        domain_events = getattr(container, "domain_event_store", None)
        if domain_events is not None:
            await domain_events.append(
                "TRUSTED_SUPPORT_CONSENT_RECORDED",
                aggregate_type="support_profile",
                aggregate_id="trusted_support",
                source_service="LifeOSTools",
                payload={
                    "auto_contact_allowed": False,
                    "boundary": "local draft only; user must explicitly review before sharing",
                },
            )
    except Exception:
        return
