"""Supervisor LangGraph graph -- 5-node orchestration loop.

Graph topology::

    [receive] -> [build_suffix] -> [think] -> [tool_loop | synthesize]
                                                tool_loop -> think (loop)

Nodes:
  * **receive** -- parse incoming message, increment turn, reset per-turn state
  * **build_suffix** -- assemble dynamic suffix from state
  * **think** -- single LLM call (frozen prefix + suffix + tools)
  * **tool_loop** -- execute tool calls, append results, route back to think
  * **synthesize** -- format final response (pass-through if think already done)

The ``build_supervisor_graph`` factory accepts a *container* object that
provides ``container.llm`` (an ``LLMProviderBase``), ``container.settings``,
and ``container.event_emitter``.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from kora_v2.graph.dispatch import (
    _PROTECTED_SYSTEM_PIPELINES,
    SUPERVISOR_TOOLS,
    _explicitly_mentions_protected_pipeline,
    execute_tool,
    get_available_tools,
)
from kora_v2.graph.prompts import build_dynamic_suffix, build_frozen_prefix
from kora_v2.graph.state import SupervisorState
from kora_v2.llm.types import GenerationResult

log = structlog.get_logger(__name__)

# Maximum think -> tool_loop -> think iterations to prevent infinite loops.
# Raised from 8 to 12 after the 2026-04-11 acceptance run hit the cap
# during legitimate exploration (10 list_directory calls to anchor a
# project path). 12 gives honest exploration headroom; the fallback
# below turns the cap from a conversational dead end into a clarifying
# question so the user never sees a bail string.
_MAX_TOOL_ITERATIONS = 12

# Instructional suffix injected into the next think() call when the
# iteration cap has been hit. Forces the model to stop tool-calling
# and ask one focused clarifying question instead of bailing out.
_ITERATION_CAP_CLARIFY_SUFFIX = (
    "IMPORTANT: You have exhausted your tool-exploration budget for this "
    "turn. Do NOT call any more tools. Review what you have learned so "
    "far, then reply with ONE short, focused question to the user that "
    "would unblock your next concrete action (for example: asking for a "
    "file path, a preference, or a decision). Be specific. Do not "
    "apologize or describe the search you attempted — just ask the "
    "question plainly."
)

# Core skills that must always be visible to the LLM, even if the skill
# loader found zero skills on disk (cold start, missing YAML, parse error).
# Without this fallback, turn 1 would run with zero registry tools and the
# LLM would be tempted to hallucinate tool calls it couldn't actually make.
_CORE_SKILLS_FALLBACK = [
    "life_management",
    "web_research",
    "file_creation",
]

_CANCEL_CONTROL_WORDS = {
    "actually",
    "background",
    "cancel",
    "cancelled",
    "canceling",
    "cancelling",
    "don't",
    "extra",
    "keep",
    "pause",
    "paused",
    "right",
    "running",
    "stop",
    "stopped",
    "task",
    "tasks",
    "there",
    "waste",
    "work",
}


def _cancel_target_words(text: str) -> list[str]:
    normalized = text.lower().replace("_", " ").replace("-", " ")
    words: list[str] = []
    for raw in normalized.split():
        word = raw.strip(".,;:!?()[]{}\"'")
        if len(word) < 5 or word in _CANCEL_CONTROL_WORDS:
            continue
        words.append(word)
    return words


def _reason_preserves_research(text: str) -> bool:
    lowered = text.lower()
    return (
        "research" in lowered
        and any(
            phrase in lowered
            for phrase in (
                "keep",
                "preserve",
                "do not cancel",
                "don't cancel",
                "dont cancel",
                "do not disturb",
                "don't disturb",
                "dont disturb",
                "do not touch",
                "don't touch",
                "dont touch",
            )
        )
    )


def _target_words_from_cancel_request(text: str) -> list[str]:
    """Extract the positive target of a cancel request.

    Users often say things like "cancel only cancel-probe; do not cancel the
    research task". The protected clause is important context, but it must not
    contribute target words or the scorer will select the task the user asked
    us to preserve.
    """
    lowered = text.lower()
    if not re.search(r"\b(?:cancel|stop|pause|kill)\b(?![-_])", lowered):
        return []
    first_sentence = re.split(r"[.;\n]", lowered, maxsplit=1)[0]
    only_match = re.search(
        r"\b(?:cancel|stop|pause|kill)\s+only\s+(.+?)(?:\s+right\s+now|\s+now|$)",
        first_sentence,
    )
    if only_match:
        return _cancel_target_words(only_match.group(1))

    positive = re.split(
        r"\b(?:do not|don't|dont|keep|preserve)\b",
        lowered,
        maxsplit=1,
    )[0]
    return _cancel_target_words(positive)


def _latest_user_text(state: SupervisorState) -> str:
    """Return the latest user/human message text from graph state."""
    for msg in reversed(state.get("messages", [])):
        role = (
            msg.get("role", "")
            if isinstance(msg, dict)
            else getattr(msg, "type", "")
        )
        content = (
            msg.get("content", "")
            if isinstance(msg, dict)
            else getattr(msg, "content", "")
        )
        if role in ("user", "human") and isinstance(content, str):
            return content
    return ""


def _loaded_skill_names(skill_loader: Any | None) -> set[str]:
    if skill_loader is None:
        return set(_CORE_SKILLS_FALLBACK)
    try:
        names = {s.name for s in skill_loader.get_all_skills()}
    except Exception:
        names = set()
    return names or set(_CORE_SKILLS_FALLBACK)


def _infer_active_skills(
    user_text: str,
    skill_loader: Any | None,
) -> list[str]:
    """Infer the smallest practical skill set for this turn.

    The old graph passed every loaded skill as active on every turn, which
    made skill gating mostly decorative. This heuristic is intentionally
    conservative: include the domains that clearly match the user's words
    plus a tiny planning fallback for generic task requests.
    """
    available = _loaded_skill_names(skill_loader)
    text = user_text.lower()
    active: list[str] = []

    def add(name: str) -> None:
        if name in available and name not in active:
            active.append(name)

    def has_any(*needles: str) -> bool:
        return any(needle in text for needle in needles)

    def has_word(*words: str) -> bool:
        import re as _re

        return any(
            _re.search(rf"\b{_re.escape(word)}\b", text) is not None
            for word in words
        )

    if has_any(
        "med", "adderall", "vyvanse", "melatonin", "meal", "breakfast",
        "lunch", "dinner", "snack", "coffee", "focus", "remind",
        "reminder", "routine", "sleep", "energy", "scatter", "scattered",
        "tired", "overwhelmed", "quick note", "note to self",
        "note:", "remember:", "spent ", "bought ",
    ):
        add("life_management")

    if has_any(
        "plan", "schedule", "task", "todo", "to-do", "priority",
        "week", "today", "tomorrow", "briefing", "what should i do",
        "review", "decide", "decision", "can't decide", "cant decide",
        "weighing", "open question",
    ):
        add("planning")
        if "life_management" in available:
            add("life_management")

    if has_any(".py", ".tsx") or has_word(
        "code", "bug", "test", "tests", "repo", "python", "typescript",
        "react", "component", "function", "class", "database", "sqlite",
        "api", "implement", "fix",
    ):
        add("code_work")
        add("file_creation")

    if has_any(
        "file", "folder", "directory", "write", "read", "create",
        "save", "draft", "outline", "brief", "document", "markdown",
    ):
        add("file_creation")

    if has_any(
        "research", "search", "latest", "current", "look up", "deep dive",
        "compare", "web", "url", "privacy", "local-first", "tools",
        "landscape",
    ):
        add("web_research")
        add("browser_capability")

    if has_any("browser", "open ", "screenshot", "page", "click", "type"):
        add("browser_capability")

    if has_any("vault", "obsidian", "clip", "save this note"):
        add("vault_capability")

    if has_any(
        "gmail", "email", "calendar", "drive", "google doc", "google task",
        "meeting",
    ):
        add("workspace_capability")
        add("calendar")

    if has_any(
        "struggling", "anxious", "sad", "frustrated", "panic",
        "can't keep up", "i feel", "burned out", "burnt out",
    ):
        add("emotional_support")

    if not active:
        for name in _CORE_SKILLS_FALLBACK:
            add(name)

    return active


def _format_active_skill_guidance(
    skill_loader: Any | None,
    active_skills: list[str],
) -> str:
    if skill_loader is None or not active_skills:
        return ""
    blocks: list[str] = []
    for skill_name in active_skills:
        try:
            guidance = skill_loader.get_guidance(skill_name)
        except Exception:
            guidance = ""
        if guidance and guidance.strip():
            blocks.append(guidance.strip())
    if not blocks:
        return ""
    return "# Active Skill Guidance\n\n" + "\n\n".join(blocks)


def _forced_in_turn_decompose_call(user_text: str) -> dict[str, Any] | None:
    text = user_text.lower()
    if not any(signal in text for signal in ("break", "tasks", "steps", "plan")):
        return None
    if not any(signal in text for signal in ("this turn", "today", "finish", "implementation")):
        return None
    return {
        "name": "decompose_and_dispatch",
        "arguments": {
            "goal": user_text.strip()[:500],
            "pipeline_name": "in_turn_breakdown",
            "stages": [
                "identify implementation steps",
                "sequence the work",
                "define verification checks",
            ],
            "in_turn": True,
            "intent_duration": "short",
        },
    }


def _forced_quick_note_call(user_text: str) -> dict[str, Any] | None:
    text = user_text.strip()
    lowered = text.lower()
    markers = ("note to self:", "quick note:", "note:", "remember:")
    marker = next((m for m in markers if m in lowered), "")
    if not marker:
        return None
    idx = lowered.find(marker)
    content = text[idx + len(marker):].strip(" -:\n\t")
    if not content:
        content = text
    return {
        "name": "quick_note",
        "arguments": {
            "content": content[:1000],
            "tags": "acceptance,quick-capture",
        },
    }


def _forced_medication_call(user_text: str) -> dict[str, Any] | None:
    text = user_text.strip()
    lowered = text.lower()
    if not any(
        signal in lowered
        for signal in (
            "took my ",
            "took the ",
            "just took",
            "gonna take",
            "going to take",
            "i took",
        )
    ):
        return None
    med_name = ""
    if "melatonin" in lowered:
        med_name = "melatonin"
    elif "adderall" in lowered:
        med_name = "Adderall"
    elif "vyvanse" in lowered:
        med_name = "Vyvanse"
    elif "meds" in lowered or "medication" in lowered:
        med_name = "medication"
    if not med_name:
        return None
    dose_match = re.search(r"\b(\d+(?:\.\d+)?\s*mg)\b", lowered)
    return {
        "name": "log_medication",
        "arguments": {
            "medication_name": med_name,
            "dose": dose_match.group(1).replace(" ", "") if dose_match else "",
            "notes": text[:500],
        },
    }


_MEAL_WORDS = {
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
    "pasta",
    "protein",
    "salad",
    "sandwich",
    "snack",
    "soup",
    "toast",
}

_CONCRETE_FOOD_WORDS = _MEAL_WORDS - {
    "breakfast",
    "dinner",
    "food",
    "lunch",
    "meal",
    "snack",
}


def _forced_meal_call(user_text: str) -> dict[str, Any] | None:
    text = user_text.strip()
    lowered = text.lower()
    meal_match = re.search(
        r"\b(?:had|ate|grabbed|snacked\s+on)\s+"
        r"(?:a\s+|some\s+)?([^.;\n]+)",
        text,
        flags=re.IGNORECASE,
    )
    coffee_match = re.search(r"\bhad\s+coffee\b", text, flags=re.IGNORECASE)
    if not meal_match and not coffee_match:
        return None

    description = (
        meal_match.group(1).strip(" .")
        if meal_match
        else "coffee"
    )[:500]
    if not description:
        return None

    if any(
        phrase in lowered
        for phrase in (
            "did i eat",
            "don't think i ate",
            "dont think i ate",
            "haven't eaten",
            "havent eaten",
            "no lunch",
            "skipped lunch",
            "asked about dinner",
            "figure dinner out later",
            "create a file",
            "research notes",
            "acceptance routine",
            "stretch break",
        )
    ):
        if not any(word in description.lower() for word in _CONCRETE_FOOD_WORDS):
            return None

    desc_lower = description.lower()
    meal_type = "meal"
    for candidate in ("breakfast", "lunch", "dinner", "snack"):
        if candidate in desc_lower:
            meal_type = candidate
            break

    return {
        "name": "log_meal",
        "arguments": {
            "description": description,
            "meal_type": meal_type,
            "calories": 0,
        },
    }


def _forced_reminder_call(user_text: str) -> dict[str, Any] | None:
    text = user_text.strip()
    lowered = text.lower()
    if not any(phrase in lowered for phrase in ("remind me", "set a reminder")):
        return None
    title = text
    for marker in ("remind me to ", "remind me about ", "set a reminder to "):
        idx = lowered.find(marker)
        if idx >= 0:
            title = text[idx + len(marker):].strip(" .")
            break
    return {
        "name": "create_reminder",
        "arguments": {
            "title": title[:120] or "Reminder",
            "description": text[:500],
            "remind_at": "",
            "recurring": "",
        },
    }


def _forced_record_decision_call(user_text: str) -> dict[str, Any] | None:
    text = user_text.strip()
    lowered = text.lower()
    if not any(
        signal in lowered
        for signal in (
            "can't decide",
            "cant decide",
            "can't make yet",
            "cant make yet",
            "decision i can't make",
            "decision i cant make",
            "weighing",
            "open question",
            "decide later",
            "figure this out later",
            "unresolved decision",
        )
    ):
        return None
    return {
        "name": "record_decision",
        "arguments": {
            "prompt": text[:500],
            "context": "User explicitly left this decision unresolved.",
            "options": [],
            "category": "planning",
        },
    }


def _forced_recall_call(user_text: str) -> dict[str, Any] | None:
    text = user_text.strip()
    lowered = text.lower()
    if not any(
        signal in lowered
        for signal in (
            "what do you remember",
            "what do we remember",
            "remember from yesterday",
            "remember from saturday",
            "where did we land",
            "what survived restart",
            "before the restart",
        )
    ):
        return None
    return {
        "name": "recall",
        "arguments": {
            "query": text[:500],
            "layer": "all",
            "max_results": 8,
        },
    }


def _path_for_directory_listing(user_text: str) -> str:
    path_match = re.search(r"(/[\w .~@%+=:,/\\-]+)", user_text)
    if path_match:
        raw = path_match.group(1).strip(" .,:;)")
        path = Path(raw).expanduser()
        if path.suffix:
            return str(path.parent)
        return str(path)
    lowered = user_text.lower()
    if "dashboard" in lowered or "readme" in lowered or "launch-note" in lowered:
        return "/tmp/focus-dashboard"
    return "."


def _forced_list_directory_call(user_text: str) -> dict[str, Any] | None:
    text = user_text.strip()
    lowered = text.lower()
    file_review = any(
        phrase in lowered
        for phrase in (
            "list what files",
            "what files",
            "which files",
            "files created",
            "files we created",
            "files updated",
            "deliverables",
            "actual deliverables",
        )
    )
    if not file_review:
        return None
    if not any(
        signal in lowered
        for signal in (
            "file",
            "files",
            "deliverable",
            "deliverables",
            "dashboard",
            "readme",
            "launch-note",
            "research.md",
        )
    ):
        return None
    return {
        "name": "list_directory",
        "arguments": {
            "path": _path_for_directory_listing(text),
        },
    }


def _forced_focus_block_call(user_text: str) -> dict[str, Any] | None:
    text = user_text.strip()
    lowered = text.lower()
    if any(
        phrase in lowered
        for phrase in (
            "start a focus block",
            "start focus block",
            "start a focus session",
            "let's do a focus session",
            "begin a focus block",
        )
    ):
        return {
            "name": "start_focus_block",
            "arguments": {
                "label": "Dashboard deep work"
                if "dashboard" in lowered
                else "Focus Session",
                "notes": text[:500],
            },
        }
    if any(
        phrase in lowered
        for phrase in (
            "end the focus block",
            "end focus block",
            "end the focus session",
            "stop the focus block",
            "finish the focus session",
        )
    ):
        return {
            "name": "end_focus_block",
            "arguments": {
                "notes": text[:500],
                "completed": True,
            },
        }
    return None


def _forced_cancel_task_call(
    user_text: str,
    state: SupervisorState,
) -> dict[str, Any] | None:
    lowered = user_text.lower()
    # Treat "cancel" as an imperative only when it appears as a standalone
    # verb. Task names such as "cancel-probe" should not become cancellation
    # requests while the user is asking to create/start that task.
    explicit_stop = re.search(r"\b(?:stop|kill)\b|\bcancel\b(?![-_])", lowered)
    explicit_pause = any(
        phrase in lowered
        for phrase in (
            "pause that task",
            "pause this task",
            "pause the task",
            "pause that background",
            "pause this background",
            "pause the background",
        )
    )
    if not (explicit_stop or explicit_pause):
        return None
    if not any(
        signal in lowered
        for signal in ("task", "background", "research", "work", "job")
    ):
        return None

    exact_task_match = re.search(r"\btask-[a-z0-9]+\b", user_text, re.IGNORECASE)
    if exact_task_match is not None:
        return {
            "name": "cancel_task",
            "arguments": {
                "task_id": exact_task_match.group(0),
                "reason": user_text.strip()[:200] or "user_requested",
            },
        }

    tasks = state.get("_orchestration_tasks") or []
    candidates: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = task.get("task_id")
        task_state = str(task.get("state") or "").lower()
        if not task_id or task_state in {"completed", "failed", "cancelled"}:
            continue
        candidates.append(task)
    if not candidates:
        return None

    preserving_research = _reason_preserves_research(lowered)

    words = _target_words_from_cancel_request(lowered)

    def score(task: dict[str, Any]) -> int:
        pipeline_name = str(task.get("pipeline_name") or "")
        if (
            pipeline_name in _PROTECTED_SYSTEM_PIPELINES
            and not _explicitly_mentions_protected_pipeline(
                lowered,
                pipeline_name,
            )
        ):
            return -1
        haystack = " ".join(
            str(task.get(key) or "").lower()
            for key in (
                "stage",
                "goal",
                "pipeline_name",
                "pipeline_goal",
                "result_summary",
                "error_message",
            )
        ).replace("_", " ").replace("-", " ")
        semantic_haystack = haystack.replace("proactive research", "")
        word_score = sum(1 for word in words if word and word in haystack)
        if (
            preserving_research
            and word_score == 0
            and "research" in semantic_haystack
        ):
            return -1
        return word_score

    scored = [(candidate, score(candidate)) for candidate in candidates]
    selected_by_exclusion = False
    if preserving_research:
        non_preserved = [
            candidate for candidate, candidate_score in scored
            if candidate_score >= 0
        ]
        if len(non_preserved) == 1:
            selected = non_preserved[0]
            selected_score = score(selected)
            selected_by_exclusion = True
        else:
            selected, selected_score = max(scored, key=lambda item: item[1])
    else:
        selected, selected_score = max(scored, key=lambda item: item[1])
    if selected_score < 0:
        return None
    if words and selected_score == 0 and not selected_by_exclusion:
        return None
    if not words and len(candidates) > 1:
        return None
    return {
        "name": "cancel_task",
        "arguments": {
            "task_id": str(selected["task_id"]),
            "reason": user_text.strip()[:200] or "user_requested",
        },
    }


def _forced_tool_call_for_turn(
    user_text: str,
    state: SupervisorState,
) -> dict[str, Any] | None:
    calls = _forced_tool_calls_for_turn(user_text, state)
    return calls[0] if calls else None


def _forced_tool_calls_for_turn(
    user_text: str,
    state: SupervisorState,
) -> list[dict[str, Any]]:
    in_turn = _forced_in_turn_decompose_call(user_text)
    if in_turn:
        return [in_turn]

    calls: list[dict[str, Any]] = []
    for factory in (
        _forced_meal_call,
        _forced_medication_call,
        _forced_reminder_call,
        _forced_quick_note_call,
        _forced_focus_block_call,
        _forced_record_decision_call,
        _forced_list_directory_call,
        _forced_recall_call,
    ):
        call = factory(user_text)
        if call:
            calls.append(call)

    if calls:
        seen: set[tuple[str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for call in calls:
            name = str(call.get("name") or "")
            args = call.get("arguments") or {}
            key = (name, json.dumps(args, sort_keys=True, default=str))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(call)
        return deduped

    cancel_call = _forced_cancel_task_call(user_text, state)
    return [cancel_call] if cancel_call else []


def _infer_energy_self_report(user_text: str) -> dict[str, Any] | None:
    lowered = user_text.lower()
    if any(
        signal in lowered
        for signal in (
            "focus is shot",
            "meds wearing off",
            "brain is mush",
            "scattered",
            "dragging",
            "wiped",
            "exhausted",
            "can't focus",
            "cant focus",
        )
    ):
        return {
            "level": "low",
            "focus": "scattered",
            "confidence": 0.9,
            "source": "self_report",
            "signals": ["low energy self-report", user_text[:160]],
            "is_guess": False,
        }
    if any(
        signal in lowered
        for signal in (
            "feeling sharp",
            "focused",
            "good window",
            "locked in",
            "slept well",
        )
    ):
        return {
            "level": "high",
            "focus": "locked_in",
            "confidence": 0.85,
            "source": "self_report",
            "signals": ["high energy self-report", user_text[:160]],
            "is_guess": False,
        }
    if any(
        signal in lowered
        for signal in ("better now", "recovering", "settling down")
    ):
        return {
            "level": "medium",
            "focus": "moderate",
            "confidence": 0.75,
            "source": "self_report",
            "signals": ["medium energy self-report", user_text[:160]],
            "is_guess": False,
        }
    return None


async def _record_energy_self_report(
    container: Any,
    estimate_data: dict[str, Any],
    *,
    user_text: str,
) -> dict[str, Any]:
    from kora_v2.core.models import EnergyEstimate

    estimate = EnergyEstimate(**estimate_data)
    settings = getattr(container, "settings", None)
    data_dir = getattr(settings, "data_dir", None)
    if data_dir is not None:
        try:
            import aiosqlite

            async with aiosqlite.connect(str(data_dir / "operational.db")) as db:
                await db.execute(
                    "INSERT INTO energy_log "
                    "(id, level, focus, source, notes, logged_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"energy-{uuid.uuid4().hex[:12]}",
                        estimate.level,
                        estimate.focus,
                        estimate.source,
                        user_text[:500],
                        datetime.now(UTC).isoformat(),
                    ),
                )
                await db.commit()
        except Exception:
            log.debug("energy_self_report_persist_skipped", exc_info=True)

    session_mgr = getattr(container, "session_manager", None)
    session = getattr(session_mgr, "active_session", None)
    if session is not None:
        try:
            session.energy_estimate = estimate
        except Exception:
            log.debug("energy_self_report_session_update_skipped", exc_info=True)
    emitter = getattr(container, "event_emitter", None)
    if emitter is not None:
        try:
            from kora_v2.core.events import EventType

            await emitter.emit(
                EventType.INSIGHT_AVAILABLE,
                insight_type="energy_self_report",
                insight_title=f"Energy self-report: {estimate.level}/{estimate.focus}",
                confidence=estimate.confidence,
                domain="emotional",
            )
        except Exception:
            log.debug("energy_self_report_insight_emit_skipped", exc_info=True)
    orchestration = getattr(container, "orchestration_engine", None)
    ledger = getattr(orchestration, "ledger", None)
    if ledger is not None:
        try:
            from kora_v2.runtime.orchestration.ledger import LedgerEventType

            await ledger.record(
                LedgerEventType.TRIGGER_FIRED,
                trigger_name="INSIGHT_AVAILABLE",
                reason="energy_self_report",
                metadata={
                    "event_type": "INSIGHT_AVAILABLE",
                    "insight_type": "energy_self_report",
                    "level": estimate.level,
                    "focus": estimate.focus,
                    "confidence": estimate.confidence,
                },
            )
        except Exception:
            log.debug("energy_self_report_ledger_skipped", exc_info=True)
    return estimate.model_dump()


# =====================================================================
# Node Functions
# =====================================================================


def _compute_session_duration(container: Any) -> int:
    """Return the active session's duration in minutes, or 0."""
    if container is None:
        return 0
    session_manager = getattr(container, "session_manager", None)
    if session_manager is None:
        return 0
    session = getattr(session_manager, "active_session", None)
    if session is None:
        return 0
    started_at = getattr(session, "started_at", None)
    if started_at is None:
        return 0
    try:
        from datetime import UTC, datetime

        delta = datetime.now(UTC) - started_at
        return max(0, int(delta.total_seconds() // 60))
    except Exception:
        return 0


async def receive(state: SupervisorState) -> dict[str, Any]:
    """Parse incoming message, increment turn count, reset per-turn state.

    This is the entry node -- runs once at the start of every turn.
    """
    turn = state.get("turn_count", 0) + 1

    # Ensure session_id exists
    session_id = state.get("session_id") or uuid.uuid4().hex[:12]

    log.info("receive", turn=turn, session_id=session_id)

    return {
        "turn_count": turn,
        "session_id": session_id,
        # Reset per-turn state
        "active_workers": [],
        "tool_call_records": [],
        "response_content": "",
        # Reset per-turn overlap detection state
        "_overlap_score": 0.0,
        "_overlap_action": "",
        "_short_circuit_response": False,
    }


async def build_suffix(state: SupervisorState, container: Any = None) -> dict[str, Any]:
    """Build dynamic suffix, ensure frozen prefix, and run compaction if needed.

    The frozen prefix is built once (first turn) and cached.
    The dynamic suffix is rebuilt every turn.
    After building, checks the budget tier and runs compaction when needed.
    """
    # Build frozen prefix on first turn (or if missing)
    frozen = state.get("frozen_prefix") or ""
    if not frozen:
        # On first turn, pass user model snapshot if available
        user_snapshot = None
        if container and hasattr(container, 'session_manager') and container.session_manager:
            session = container.session_manager.active_session
            if session:
                # Placeholder: user model snapshot loaded during session init
                pass

        # Gather skill information for the frozen prefix
        skill_loader = getattr(container, "skill_loader", None) if container else None
        skill_names: list[str] | None = None
        if skill_loader is not None:
            all_skills = skill_loader.get_all_skills()
            skill_names = [s.name for s in all_skills]

        # Same fallback as build_supervisor_graph: if skills failed to load,
        # pretend the core set is active so the prompt still mentions them.
        if not skill_names:
            skill_names = list(_CORE_SKILLS_FALLBACK)

        # Phase 5: pull ADHD output guidance + overwhelm triggers from
        # the wired ADHDModule so they land in the frozen prefix.
        adhd_module = getattr(container, "adhd_module", None) if container else None
        adhd_guidance: list[str] | None = None
        user_triggers: list[str] | None = None
        if adhd_module is not None:
            try:
                adhd_guidance = adhd_module.output_guidance()
                sup_ctx = adhd_module.supervisor_context()
                if isinstance(sup_ctx, dict):
                    user_triggers = sup_ctx.get("overwhelm_triggers") or None
            except Exception:
                log.debug("adhd_module_prefix_hook_failed", exc_info=True)

        frozen = build_frozen_prefix(
            user_model_snapshot=user_snapshot,
            skill_index=skill_names,
            skill_loader=skill_loader,
            active_skills=None,
            adhd_output_guidance=adhd_guidance,
            user_triggers=user_triggers,
        )

    # Phase 5: rebuild DayContext every turn from the ContextEngine.
    # This is the single source of truth for the ## Today block.
    day_context_dict: dict[str, Any] | None = state.get("day_context")
    engine = getattr(container, "context_engine", None) if container else None
    session_duration_min = _compute_session_duration(container)
    if engine is not None:
        try:
            session_state = {
                "turns_in_current_topic": state.get("turns_in_current_topic", 0),
                "session_duration_min": session_duration_min,
            }
            dc = await engine.build_day_context(session_state=session_state)
            day_context_dict = dc.model_dump(mode="json")
        except Exception:
            log.debug("build_day_context_failed", exc_info=True)

    # Check for unread autonomous updates from the background loop
    unread: list[dict[str, Any]] = []
    if container is not None:
        session_id = state.get("session_id")
        if session_id:
            unread = await _fetch_unread_autonomous_updates(container, session_id)

    # Build dynamic suffix (includes autonomous updates if any)
    suffix_state = dict(state)
    if unread:
        suffix_state["_unread_autonomous_updates"] = unread
    if day_context_dict is not None:
        suffix_state["day_context"] = day_context_dict
    active_skills = list(state.get("_active_skills") or [])
    skill_loader = getattr(container, "skill_loader", None) if container else None
    active_guidance = _format_active_skill_guidance(skill_loader, active_skills)
    suffix_state["_active_skill_guidance"] = active_guidance

    suffix = build_dynamic_suffix(suffix_state)

    log.debug(
        "build_suffix",
        frozen_len=len(frozen),
        suffix_len=len(suffix),
    )

    update: dict[str, Any] = {
        "frozen_prefix": frozen,
        "_dynamic_suffix": suffix,
    }
    if day_context_dict is not None:
        update["day_context"] = day_context_dict
    if unread:
        update["_unread_autonomous_updates"] = unread
    update["_active_skill_guidance"] = active_guidance

    # Check budget tier and run compaction if needed
    messages = state.get("messages", [])
    if messages:
        from kora_v2.context.budget import BudgetTier, ContextBudgetMonitor

        monitor = ContextBudgetMonitor()
        # Convert messages to dicts for token counting. Preserve content
        # BLOCK STRUCTURE — a LangGraph AIMessage with thinking / tool_use
        # blocks stores content as a list; flattening with ``str()`` both
        # corrupts the shape and loses per-block overhead, leading to a
        # large (3×+) undercount that hides real compaction pressure.
        msg_dicts = []
        for msg in messages:
            if isinstance(msg, dict):
                msg_dicts.append(msg)
                continue
            msg_type = getattr(msg, "type", "user")
            role = {
                "human": "user",
                "ai": "assistant",
                "tool": "tool",
                "system": "system",
            }.get(msg_type, msg_type or "user")
            raw_content = getattr(msg, "content", "")
            # Keep list-shape content (thinking / tool_use / tool_result
            # blocks) so count_message_tokens walks the blocks correctly.
            msg_dicts.append({"role": role, "content": raw_content})

        # Include the active supervisor tool schemas in the monitor's
        # estimate. The MiniMax pre-call safety check counts them, so
        # excluding them here is the root cause of the monitor vs
        # provider divergence that lets context overflow past PRUNE.
        try:
            from kora_v2.graph.dispatch import SUPERVISOR_TOOLS as _tools
        except Exception:
            _tools = None

        # Compute usage once and cache both the tier and the raw token
        # estimate so the daemon can forward token_count in the response_complete
        # WebSocket metadata. Without this, observers only see tier names and
        # cannot track how close to the next escalation the conversation is.
        estimated_tokens = monitor.estimate_current_usage(msg_dicts, frozen, tools=_tools)
        tier = monitor.get_tier(msg_dicts, frozen, tools=_tools)
        update["compaction_tier"] = tier.name
        update["compaction_tokens"] = estimated_tokens

        if tier != BudgetTier.NORMAL:
            from kora_v2.context.compaction import run_compaction

            llm = container.llm if container else None
            existing_summary = state.get("compaction_summary", "")

            result = await run_compaction(
                messages=msg_dicts,
                tier=tier,
                llm=llm,
                existing_summary=existing_summary or None,
            )

            if result is not None:
                update["compaction_summary"] = result.summary_text or existing_summary
                update["messages"] = result.messages  # replace message list with compacted version
                log.info(
                    "compaction_ran",
                    stage=result.stage,
                    tokens_saved=result.tokens_saved,
                )

    return update


async def think(
    state: SupervisorState,
    container: Any,
    tools: list[dict[str, Any]] | None = None,
    *,
    extra_system_suffix: str | None = None,
) -> dict[str, Any]:
    """Single LLM call with frozen prefix + dynamic suffix + tools.

    Uses ``container.llm.generate_with_tools()`` which returns a
    ``GenerationResult`` with ``.content``, ``.tool_calls``, and
    ``.content_blocks``.

    Args:
        state: Current supervisor state.
        container: Service container with ``llm`` attribute.
        tools: Tool definitions to pass to the LLM. Defaults to
            ``SUPERVISOR_TOOLS`` for backward compatibility. Pass an
            empty list to force a text-only turn (used by the tool
            iteration cap fallback so the model cannot keep exploring).
        extra_system_suffix: Optional additional instruction appended
            to the system prompt for this single call only. Used by the
            iteration-cap fallback to instruct the model to ask one
            clarifying question instead of continuing to tool-call.
    """
    active_tools = tools if tools is not None else SUPERVISOR_TOOLS
    # Assemble system prompt
    frozen_prefix = state.get("frozen_prefix", "")
    suffix = state.get("_dynamic_suffix", "")
    system_prompt = frozen_prefix
    if suffix:
        system_prompt = f"{frozen_prefix}\n\n{suffix}"
    if extra_system_suffix:
        system_prompt = f"{system_prompt}\n\n{extra_system_suffix}".strip()

    # Extract messages for the LLM.
    # Apply tool-pair integrity sanitization here (not in the reducer):
    # state is append-only, but the LLM MUST only see complete
    # tool_use/tool_result pairs. Any dangling leftovers from a prior
    # aborted turn get dropped before the LLM sees them.
    from kora_v2.graph.reducers import ensure_tool_pair_integrity
    messages = ensure_tool_pair_integrity(state.get("messages", []))

    # Convert LangGraph message objects to dicts for the provider
    formatted_messages: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, dict):
            formatted_messages.append(msg)
        else:
            # LangGraph message objects (HumanMessage, AIMessage, ToolMessage)
            msg_dict: dict[str, Any] = {}
            msg_type = getattr(msg, "type", "")
            if msg_type == "human":
                msg_dict["role"] = "user"
            elif msg_type == "ai":
                msg_dict["role"] = "assistant"
            elif msg_type == "tool":
                msg_dict["role"] = "tool"
                msg_dict["tool_call_id"] = getattr(msg, "tool_call_id", "")
            elif msg_type == "system":
                msg_dict["role"] = "system"
            else:
                msg_dict["role"] = "user"

            raw_content = getattr(msg, "content", "")

            # LangGraph AIMessage.content can be a list of blocks.
            # Extract plain text and build content_blocks separately.
            if isinstance(raw_content, list):
                text_parts = []
                block_list = []
                for blk in raw_content:
                    if isinstance(blk, str):
                        text_parts.append(blk)
                    elif isinstance(blk, dict):
                        btype = blk.get("type", "")
                        if btype == "text":
                            text_parts.append(blk.get("text", ""))
                            block_list.append(blk)
                        elif btype == "tool_use":
                            block_list.append(blk)
                        elif btype == "thinking":
                            block_list.append(blk)
                        # Drop "tool_call" and other unsupported types
                msg_dict["content"] = " ".join(text_parts) if text_parts else ""
                if block_list:
                    msg_dict["content_blocks"] = block_list
            else:
                msg_dict["content"] = raw_content

            # Preserve tool_calls on AI messages
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", ""),
                        "name": tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", ""),
                        "arguments": (
                            tc.get("args", tc.get("arguments", {}))
                            if isinstance(tc, dict)
                            else getattr(tc, "arguments", {})
                        ),
                    }
                    for tc in tool_calls
                ]

            # Preserve content_blocks (only if not already set above)
            if "content_blocks" not in msg_dict:
                content_blocks = getattr(msg, "content_blocks", None)
                if content_blocks:
                    msg_dict["content_blocks"] = content_blocks

            formatted_messages.append(msg_dict)

    log.info(
        "think",
        message_count=len(formatted_messages),
        system_prompt_len=len(system_prompt),
    )

    # Call the LLM with retry on transient failures
    from kora_v2.core.errors import retry_with_backoff

    llm = container.llm
    result: GenerationResult = await retry_with_backoff(
        llm.generate_with_tools,
        messages=formatted_messages,
        tools=active_tools,
        system_prompt=system_prompt,
        temperature=0.7,
        thinking_enabled=False,
    )

    # Build state update
    update: dict[str, Any] = {}

    if result.content:
        update["response_content"] = result.content

    # If there are tool calls, store them so should_continue can route
    if result.has_tool_calls:
        # Add assistant message with tool calls to conversation.
        # LangGraph's add_messages -> AIMessage expects "args" (not "arguments")
        # for tool_calls dicts.
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": result.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "name": tc.name,
                    "args": tc.arguments,
                }
                for tc in result.tool_calls
            ],
        }
        if result.content_blocks:
            assistant_msg["content_blocks"] = result.content_blocks

        update["messages"] = [assistant_msg]
        update["_pending_tool_calls"] = [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
            for tc in result.tool_calls
        ]
    else:
        # Direct response -- add as assistant message
        if result.content:
            update["messages"] = [
                {"role": "assistant", "content": result.content}
            ]

    log.info(
        "think_result",
        has_content=bool(result.content),
        tool_call_count=len(result.tool_calls),
    )

    return update


async def tool_loop(
    state: SupervisorState,
    container: Any,
    on_tool_event: Any | None = None,
) -> dict[str, Any]:
    """Execute pending tool calls and append results to messages.

    After execution, routes back to ``think`` for further processing
    if the LLM needs to make additional decisions based on tool results.

    Args:
        state: Current supervisor state.
        container: Service container with tool dispatch support.
        on_tool_event: Optional async callback invoked after each tool
            executes.  Signature: ``async (event_dict) -> None``.
            Used by the WebSocket handler to push real-time progress.
    """
    pending = state.get("_pending_tool_calls", [])
    if not pending:
        return {}

    tool_results: list[dict[str, Any]] = []
    tool_records: list[dict[str, Any]] = []
    short_circuit_response = ""

    for tc in pending:
        tool_name = tc["name"]
        tool_args = tc["arguments"]
        tool_id = tc["id"]

        log.info("tool_loop_execute", tool=tool_name, tool_id=tool_id)

        try:
            auth_relay = getattr(container, '_auth_relay', None)
            result_str = await execute_tool(tool_name, tool_args, container, auth_relay=auth_relay)
            success = True
        except Exception as e:
            log.error("tool_execution_error", tool=tool_name, error=str(e))
            result_str = json.dumps({"error": str(e)})
            success = False

        # Emit real-time tool event so the WebSocket handler can
        # push progress to the client while the graph is still running.
        if on_tool_event is not None:
            try:
                await on_tool_event({
                    "event": "tool_executed",
                    "tool_name": tool_name,
                    "success": success,
                    "tool_id": tool_id,
                })
            except Exception:
                log.debug("on_tool_event_callback_failed", tool=tool_name)

        # Add tool result message
        tool_results.append({
            "role": "tool",
            "tool_call_id": tool_id,
            "content": result_str,
        })

        tool_records.append({
            "tool_name": tool_name,
            "args": tool_args,
            "result_summary": result_str[:200],
            "success": success,
        })

        if tool_name == "decompose_and_dispatch" and success:
            try:
                parsed_result = json.loads(result_str)
            except json.JSONDecodeError:
                parsed_result = {}
            intent_duration = str(
                tool_args.get("intent_duration")
                or ("short" if tool_args.get("in_turn") else "long")
            )
            if (
                isinstance(parsed_result, dict)
                and parsed_result.get("status") == "ok"
                and not bool(tool_args.get("in_turn", False))
                and intent_duration in {"long", "indefinite"}
            ):
                doc_path = parsed_result.get("working_doc_path") or ""
                if doc_path:
                    short_circuit_response = (
                        "I'll keep that running in the background. "
                        f"The working doc is {doc_path}."
                    )
                else:
                    short_circuit_response = (
                        "I'll keep that running in the background."
                    )

    # Append existing records
    existing_records = list(state.get("tool_call_records", []))
    existing_records.extend(tool_records)

    # Phase 5: topic-tracking for hyperfocus detection.
    # Tool-footprint heuristic — see §4.4 of the life engine spec.
    topic_update = _update_topic_tracker(state, tool_records)

    messages_out = list(tool_results)
    update: dict[str, Any] = {
        "messages": tool_results,
        "tool_call_records": existing_records,
        "_pending_tool_calls": [],  # Clear pending
        **topic_update,
    }
    if short_circuit_response:
        messages_out.append({
            "role": "assistant",
            "content": short_circuit_response,
        })
        update["messages"] = messages_out
        update["response_content"] = short_circuit_response
        update["_short_circuit_response"] = True
    return update


_PRONOUN_RE = None  # lazy-compiled, see _update_topic_tracker

# Tool arg keys that commonly carry an entity ID we should pick up for
# topic-continuity tracking. Values that match (incl. regex-looking UUID
# hex strings) are added to the recent-entity set.
_ENTITY_ARG_KEYS = (
    "item_id",
    "entry_id",
    "calendar_entry_id",
    "parent_id",
    "affected_entry_ids",
    "routine_id",
    "focus_block_id",
    "medication_id",
)


def _extract_entity_ids(record: dict[str, Any]) -> set[str]:
    """Pick primary entity IDs out of a tool call's args + result."""
    ids: set[str] = set()
    args = record.get("args") or {}
    if isinstance(args, dict):
        for key, value in args.items():
            if key not in _ENTITY_ARG_KEYS:
                continue
            if isinstance(value, str) and value:
                ids.add(value)
            elif isinstance(value, list):
                for v in value:
                    if isinstance(v, str) and v:
                        ids.add(v)
    # Also try to parse {"id": "..."} out of the result string for
    # create-style tools that return a fresh entity id.
    result = record.get("result_summary") or ""
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                for key in ("id", "item_id", "entry_id"):
                    val = parsed.get(key)
                    if isinstance(val, str) and val:
                        ids.add(val)
        except json.JSONDecodeError:
            pass
    return ids


def _update_topic_tracker(
    state: SupervisorState, tool_records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Run the tool-footprint heuristic and return state updates.

    Continuity rules (§4.4):
    * Same topic if current tools overlap with any recent turn's tool
      set, OR current entities overlap with any recent turn's entity
      set, OR current tools is empty (pure conversation continues).
    * Otherwise, check pronoun continuity ("it", "that", etc.) in the
      last user message — pronouns also mean "still on topic".
    * If changed → reset ``turns_in_current_topic`` to 1.
      Else → increment by 1.
    """
    global _PRONOUN_RE
    if _PRONOUN_RE is None:
        import re as _re

        _PRONOUN_RE = _re.compile(
            r"\b(it|that|this|them|those|these)\b", _re.IGNORECASE
        )

    tracker = dict(state.get("topic_tracker") or {})
    recent_tool_sets: list[list[str]] = list(tracker.get("recent_tool_sets", []))
    recent_entity_sets: list[list[str]] = list(
        tracker.get("recent_entity_ids", [])
    )

    current_tools = {r.get("tool_name", "") for r in tool_records if r.get("tool_name")}
    current_entities: set[str] = set()
    for record in tool_records:
        current_entities |= _extract_entity_ids(record)

    prior_turns = int(state.get("turns_in_current_topic", 0))
    same_topic = False

    if not current_tools:
        # Pure conversation — continue whatever was active.
        same_topic = True
    else:
        for prior in recent_tool_sets:
            if current_tools & set(prior):
                same_topic = True
                break
        if not same_topic and current_entities:
            for prior in recent_entity_sets:
                if current_entities & set(prior):
                    same_topic = True
                    break
        if not same_topic:
            # Pronoun continuity in the last user message — still on topic.
            for msg in reversed(state.get("messages", [])):
                role = (
                    msg.get("role", "")
                    if isinstance(msg, dict)
                    else getattr(msg, "type", "")
                )
                if role in ("user", "human"):
                    content = (
                        msg.get("content", "")
                        if isinstance(msg, dict)
                        else getattr(msg, "content", "")
                    )
                    if isinstance(content, str) and _PRONOUN_RE.search(content):
                        same_topic = True
                    break

    turns_in_topic = prior_turns + 1 if same_topic else 1

    # Append + cap the deques at 3 entries each.
    if current_tools:
        recent_tool_sets.append(sorted(current_tools))
    if current_entities:
        recent_entity_sets.append(sorted(current_entities))
    recent_tool_sets = recent_tool_sets[-3:]
    recent_entity_sets = recent_entity_sets[-3:]

    # Hyperfocus gate reads the session duration from day_context if
    # populated (Phase 5 path). Without a session duration, we can't
    # decide yet — leave hyperfocus_mode whatever it currently is.
    day_context = state.get("day_context") or {}
    session_minutes = int(day_context.get("session_duration_min", 0))
    hyperfocus = turns_in_topic >= 3 and session_minutes >= 45

    return {
        "topic_tracker": {
            "recent_tool_sets": recent_tool_sets,
            "recent_entity_ids": recent_entity_sets,
        },
        "turns_in_current_topic": turns_in_topic,
        "hyperfocus_mode": hyperfocus,
    }


_CJK_RANGES_RE = None  # lazy-compiled regex, see _strip_unintended_cjk


def _strip_unintended_cjk(response: str, user_messages: list[str]) -> str:
    """Remove CJK leaks from MiniMax output when the user wrote in English.

    MiniMax M2.7 occasionally emits a Chinese token mid-English-sentence
    (observed during acceptance testing: ``"from今天的对话"``). This is
    a model behavior, not a code bug — the remedy is (1) instruct the
    model to stay in English in the system prompt, and (2) strip any
    stray characters that slip through.

    We leave content inside code fences untouched so code samples with
    legitimate comments in another language keep rendering, and we skip
    the filter entirely if any user message contains CJK characters
    (meaning the user wrote in that language first and the model is
    answering appropriately).
    """
    global _CJK_RANGES_RE
    if _CJK_RANGES_RE is None:
        import re as _re

        # CJK Unified Ideographs, Hiragana, Katakana, Hangul syllables.
        _CJK_RANGES_RE = _re.compile(
            r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]+",
        )

    if not response:
        return response

    # If the user wrote in CJK, don't touch the response.
    for msg in user_messages:
        if isinstance(msg, str) and _CJK_RANGES_RE.search(msg):
            return response

    # Fast path: if there's no CJK in the response, nothing to do.
    if not _CJK_RANGES_RE.search(response):
        return response

    # Preserve code-fence blocks verbatim; strip CJK from everything else.
    # ``split("```")`` gives alternating chunks: even index = outside
    # fence, odd index = inside fence.
    import re as _re

    chunks = response.split("```")
    rebuilt: list[str] = []
    for idx, chunk in enumerate(chunks):
        if idx % 2 == 1:
            # Inside a code fence — leave untouched.
            rebuilt.append(chunk)
        else:
            rebuilt.append(_CJK_RANGES_RE.sub("", chunk))
    out = "```".join(rebuilt)
    # Collapse any double-spaces left behind by stripped tokens.
    out = _re.sub(r" {2,}", " ", out)
    log.warning("synthesize_stripped_cjk_leak", original_len=len(response), new_len=len(out))
    return out


async def synthesize(state: SupervisorState) -> dict[str, Any]:
    """Format final response.

    If the ``think`` node already produced a complete response (no tools),
    this is a pass-through. Otherwise, use the last assistant message
    content as the response.
    """
    response = state.get("response_content", "")

    if not response:
        # Pull from the last assistant message
        messages = state.get("messages", [])
        for msg in reversed(messages):
            role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "type", "")
            content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            if role in ("assistant", "ai") and content:
                response = content
                break

    # Post-filter accidental CJK leaks (see _strip_unintended_cjk).
    user_texts: list[str] = []
    for msg in state.get("messages", []):
        role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "type", "")
        if role in ("user", "human"):
            content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            if isinstance(content, str):
                user_texts.append(content)
    response = _strip_unintended_cjk(response, user_texts)

    log.info("synthesize", response_len=len(response))

    return {"response_content": response}


# =====================================================================
# Routing Function
# =====================================================================


def should_continue(state: SupervisorState) -> str:
    """Route from think: if tool calls pending, go to tool_loop; else synthesize."""
    pending = state.get("_pending_tool_calls", [])
    if pending:
        return "tool_loop"
    return "synthesize"


# =====================================================================
# Autonomous Update Fetch
# =====================================================================


async def _fetch_unread_autonomous_updates(
    container: Any,
    session_id: str,
) -> list[dict[str, Any]]:
    """Fetch undelivered autonomous updates from operational.db.

    After reading, marks them delivered so they are not surfaced again.
    Handles the table not existing yet (older DBs).
    """
    from pathlib import Path

    settings = getattr(container, "settings", None)
    data_dir = getattr(settings, "data_dir", None) or Path("data")
    db_path = Path(data_dir) / "operational.db"

    if not db_path.exists():
        return []

    import aiosqlite as _aiosqlite

    try:
        async with _aiosqlite.connect(str(db_path)) as db:
            db.row_factory = _aiosqlite.Row
            try:
                async with db.execute(
                    """
                    SELECT * FROM autonomous_updates
                    WHERE session_id = ? AND delivered = 0
                    ORDER BY created_at ASC
                    LIMIT 5
                    """,
                    (session_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
            except Exception:
                # Table may not exist yet
                return []

            if not rows:
                return []

            updates = [dict(row) for row in rows]

            # Mark delivered
            await db.execute(
                "UPDATE autonomous_updates SET delivered = 1 "
                "WHERE session_id = ? AND delivered = 0",
                (session_id,),
            )
            await db.commit()

        return updates
    except Exception as exc:
        log.debug(
            "fetch_unread_autonomous_updates_failed",
            session_id=session_id,
            error=str(exc),
        )
        return []


# =====================================================================
# Graph Builder
# =====================================================================


def build_supervisor_graph(container: Any) -> Any:
    """Build and compile the supervisor LangGraph graph.

    Args:
        container: Service container providing:
            - ``container.llm`` -- LLMProviderBase instance
            - ``container.settings`` -- Settings instance
            - ``container.event_emitter`` -- EventEmitter instance

    Returns:
        Compiled LangGraph graph with MemorySaver checkpointer.

    Graph topology::

        START -> receive -> build_suffix -> think -> [tool_loop | synthesize] -> END
                                                      tool_loop -> think (loop)
    """
    # Gather loaded skills for prompt indexing. Per-turn tool gating happens
    # in _receive/_think from the latest user message.
    skill_loader = getattr(container, "skill_loader", None)
    skill_names: list[str] | None = None
    if skill_loader is not None:
        all_skills = skill_loader.get_all_skills()
        skill_names = [s.name for s in all_skills]

    # Fallback: if the loader returned zero skills (cold start, missing YAML,
    # parse error), fall back to the core set so the LLM still sees essential
    # tools on turn 1. This is what prevents log_medication from being filtered
    # out when the skill loader is partially initialized.
    if not skill_names:
        log.warning(
            "supervisor_empty_skills_using_fallback",
            fallback=_CORE_SKILLS_FALLBACK,
        )
        skill_names = list(_CORE_SKILLS_FALLBACK)

    # Log the cold-start baseline without making it the permanent tool list.
    startup_tools = get_available_tools(
        container,
        active_skills=list(_CORE_SKILLS_FALLBACK),
    )
    log.info(
        "supervisor_tools_resolved",
        tool_count=len(startup_tools),
        tools=[t["name"] for t in startup_tools],
    )

    # Track iterations to prevent infinite tool loops
    iteration_count = {"value": 0}

    # Wrap node functions with container closure
    async def _receive(state: SupervisorState) -> dict[str, Any]:
        iteration_count["value"] = 0  # Reset on new turn
        container._turn_start_time = time.monotonic()  # Track for quality metrics
        base = await receive(state)
        turn = base["turn_count"]

        latest_user_text = _latest_user_text(state)
        active_skills = _infer_active_skills(latest_user_text, skill_loader)
        base["_active_skills"] = active_skills
        base["_latest_user_text"] = latest_user_text
        forced_calls = _forced_tool_calls_for_turn(latest_user_text, state)
        forced_call = forced_calls[0] if forced_calls else None
        base["_forced_tool_calls"] = forced_calls
        base["_forced_tool_call"] = forced_call or {}
        log.debug(
            "active_skills_inferred",
            turn=turn,
            active_skills=active_skills,
            forced_tool=forced_call.get("name") if forced_call else "",
            forced_tools=[call.get("name") for call in forced_calls],
        )

        # --- Gap 1 & 2: Populate emotion / energy / pending / bridge ---
        session_mgr = getattr(container, "session_manager", None)
        fast_emotion = getattr(container, "fast_emotion", None)
        llm_emotion_assessor = getattr(container, "llm_emotion", None)

        # On the first turn, seed state from the session manager's init data
        if turn == 1 and session_mgr and session_mgr.active_session:
            session = session_mgr.active_session
            if session.emotional_state is not None:
                base["emotional_state"] = session.emotional_state.model_dump()
            if session.energy_estimate is not None:
                base["energy_estimate"] = session.energy_estimate.model_dump()
            if session.pending_items:
                base["pending_items"] = session.pending_items

            # Load bridge from last session
            try:
                bridge = await session_mgr.load_last_bridge()
                if bridge is not None:
                    base["session_bridge"] = bridge.model_dump()
            except Exception:
                log.debug("bridge_load_skipped")

        # --- Gap 2: Run fast emotion assessment every turn ---
        if fast_emotion is not None:
            messages = state.get("messages", [])
            # Extract latest user message
            latest_user = ""
            for msg in reversed(messages):
                role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "type", "")
                content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
                if role in ("user", "human") and isinstance(content, str) and content:
                    latest_user = content
                    break

            if latest_user:
                # Gather recent messages for trajectory
                recent_texts: list[str] = []
                for msg in messages[-5:]:
                    c = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
                    if isinstance(c, str) and c:
                        recent_texts.append(c)

                # Reconstruct current emotional state from state dict
                current_emotional: EmotionalState | None = None
                raw_emo = state.get("emotional_state")
                if raw_emo is not None:
                    try:
                        from kora_v2.core.models import EmotionalState
                        if isinstance(raw_emo, dict):
                            current_emotional = EmotionalState(**{
                                k: v for k, v in raw_emo.items()
                                if k in EmotionalState.model_fields
                            })
                        else:
                            current_emotional = raw_emo
                    except Exception:
                        pass

                # FastEmotionAssessor is synchronous
                fast_result = fast_emotion.assess(latest_user, recent_texts, current_emotional)
                base["emotional_state"] = fast_result.model_dump()
                # Async event emission (EMOTION_STATE_ASSESSED +
                # EMOTION_SHIFT_DETECTED) is decoupled from the sync
                # assess() call so callers on the sync path stay sync.
                try:
                    await fast_emotion.emit_assessment(
                        getattr(container, "event_emitter", None),
                        fast_result,
                        current_emotional,
                    )
                except Exception:
                    log.debug("fast_emotion_emit_skipped")

                # Check whether LLM-tier emotion assessment should fire
                if llm_emotion_assessor is not None:
                    from kora_v2.emotion.llm_assessor import should_trigger_llm_assessment

                    # Compute cooldown: turns since last LLM emotion assessment
                    llm_last = getattr(container, '_llm_emotion_last_turn', 0)
                    turns_since = turn - llm_last if llm_last > 0 else 0

                    if should_trigger_llm_assessment(
                        fast_result, current_emotional,
                        turns_since_last_llm=turns_since,
                    ):
                        try:
                            llm_result = await llm_emotion_assessor.assess(
                                recent_texts, fast_result,
                            )
                            base["emotional_state"] = llm_result.model_dump()
                            container._llm_emotion_last_turn = turn
                        except Exception:
                            log.debug("llm_emotion_assess_skipped")

        # --- Gap 6: Refresh energy every 10 turns (or first turn) ---
        if turn == 1 or turn % 10 == 0:
            from kora_v2.context.working_memory import estimate_energy
            energy = estimate_energy()
            base["energy_estimate"] = energy.model_dump()

        energy_self_report = _infer_energy_self_report(latest_user_text)
        if energy_self_report is not None:
            base["energy_estimate"] = await _record_energy_self_report(
                container,
                energy_self_report,
                user_text=latest_user_text,
            )

        return base

    async def _build_suffix(state: SupervisorState) -> dict[str, Any]:
        return await build_suffix(state, container)

    async def _think(state: SupervisorState) -> dict[str, Any]:
        iteration_count["value"] += 1
        forced_calls = state.get("_forced_tool_calls") or []
        if not forced_calls:
            legacy_forced_call = state.get("_forced_tool_call") or {}
            if (
                isinstance(legacy_forced_call, dict)
                and legacy_forced_call.get("name")
            ):
                forced_calls = [legacy_forced_call]
        if isinstance(forced_calls, list) and forced_calls:
            tool_calls: list[dict[str, Any]] = []
            pending_calls: list[dict[str, Any]] = []
            for forced_call in forced_calls:
                if not isinstance(forced_call, dict) or not forced_call.get("name"):
                    continue
                tool_id = f"forced-{uuid.uuid4().hex[:10]}"
                tool_name = str(forced_call["name"])
                tool_args = dict(forced_call.get("arguments") or {})
                tool_calls.append(
                    {
                        "id": tool_id,
                        "name": tool_name,
                        "args": tool_args,
                    }
                )
                pending_calls.append(
                    {
                        "id": tool_id,
                        "name": tool_name,
                        "arguments": tool_args,
                    }
                )
            if not tool_calls:
                return {"_forced_tool_calls": [], "_forced_tool_call": {}}
            return {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": tool_calls,
                    }
                ],
                "_pending_tool_calls": pending_calls,
                "_forced_tool_calls": [],
                "_forced_tool_call": {},
            }
        if iteration_count["value"] > _MAX_TOOL_ITERATIONS:
            log.warning(
                "max_tool_iterations_reached",
                iterations=iteration_count["value"],
            )
            # Cap-hit fallback: re-enter think() with tool calls disabled
            # and a clarifying-question instruction so the user gets a
            # focused question instead of a "giving up" bail string.
            clarify_update = await think(
                state,
                container,
                tools=[],
                extra_system_suffix=_ITERATION_CAP_CLARIFY_SUFFIX,
            )
            clarify_update["_pending_tool_calls"] = []
            # Defensive: strip any stray tool_calls the model might have
            # produced despite the empty tools list.
            msgs = clarify_update.get("messages") or []
            sanitized: list[dict[str, Any]] = []
            for m in msgs:
                if isinstance(m, dict) and m.get("tool_calls"):
                    m = {**m, "tool_calls": []}
                sanitized.append(m)
            if sanitized:
                clarify_update["messages"] = sanitized
            # Final floor: if the LLM produced nothing at all, emit a
            # brief neutral prompt so the turn does not return empty.
            if not clarify_update.get("response_content"):
                fallback_text = (
                    "I need a bit more direction to move forward — "
                    "could you point me at the specific place or file "
                    "you want me to work in?"
                )
                clarify_update["response_content"] = fallback_text
                clarify_update["messages"] = [
                    {"role": "assistant", "content": fallback_text}
                ]
            return clarify_update
        active_skills = list(state.get("_active_skills") or _CORE_SKILLS_FALLBACK)
        turn_tools = get_available_tools(container, active_skills=active_skills)
        log.debug(
            "turn_tools_resolved",
            active_skills=active_skills,
            tool_count=len(turn_tools),
        )
        return await think(state, container, tools=turn_tools)

    async def _tool_loop(state: SupervisorState) -> dict[str, Any]:
        on_tool_event = getattr(container, '_on_tool_event', None)
        return await tool_loop(state, container, on_tool_event=on_tool_event)

    async def _synthesize(state: SupervisorState) -> dict[str, Any]:
        result = await synthesize(state)

        # Auto-record quality metrics
        quality = getattr(container, "quality_collector", None)
        session_mgr = getattr(container, "session_manager", None)
        if quality is not None and session_mgr is not None:
            active = getattr(session_mgr, "active_session", None)
            if active is not None:
                session_id = active.session_id
                turn = state.get("turn_count", 0)
                tool_calls = len(state.get("tool_call_records", []))
                start_t = getattr(container, "_turn_start_time", None)
                elapsed = int((time.monotonic() - start_t) * 1000) if start_t else 0
                try:
                    quality.record_turn(
                        session_id=session_id,
                        turn=turn,
                        latency_ms=elapsed,
                        tool_calls=tool_calls,
                    )
                except Exception:
                    log.debug("quality_record_skipped")

        return result

    def _should_continue(state: SupervisorState) -> str:
        return should_continue(state)

    def _after_tool_loop(state: SupervisorState) -> str:
        if state.get("_short_circuit_response"):
            return "synthesize"
        return "think"

    # Build the graph
    graph = StateGraph(SupervisorState)

    # Add nodes
    graph.add_node("receive", _receive)
    graph.add_node("build_suffix", _build_suffix)
    graph.add_node("think", _think)
    graph.add_node("tool_loop", _tool_loop)
    graph.add_node("synthesize", _synthesize)

    # Add edges
    graph.add_edge(START, "receive")
    graph.add_edge("receive", "build_suffix")
    graph.add_edge("build_suffix", "think")
    graph.add_conditional_edges("think", _should_continue, {
        "tool_loop": "tool_loop",
        "synthesize": "synthesize",
    })
    graph.add_conditional_edges("tool_loop", _after_tool_loop, {
        "think": "think",
        "synthesize": "synthesize",
    })
    graph.add_edge("synthesize", END)

    # Use the container's persistent checkpointer when one has been wired
    # up by initialize_checkpointer() (Phase 4.67 — SQLite-backed, durable
    # across daemon restarts). Fall back to in-memory MemorySaver when the
    # SQLite backend is unavailable (e.g. langgraph-checkpoint-sqlite not
    # installed) — this keeps tests and cold-start paths working.
    checkpointer = getattr(container, "_checkpointer", None)
    if checkpointer is None:
        log.warning(
            "supervisor_using_memory_checkpointer",
            hint="container._checkpointer not set — conversation state will NOT survive daemon restart",
        )
        checkpointer = MemorySaver()
    else:
        log.info(
            "supervisor_using_container_checkpointer",
            backend=type(checkpointer).__name__,
        )
    compiled = graph.compile(checkpointer=checkpointer)

    log.info("supervisor_graph_built")

    return compiled
