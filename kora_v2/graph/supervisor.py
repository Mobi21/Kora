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
import time
import uuid
from typing import Any

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from kora_v2.graph.dispatch import SUPERVISOR_TOOLS, execute_tool, get_available_tools
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
            active_skills=skill_names,
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

    # Check budget tier and run compaction if needed
    messages = state.get("messages", [])
    if messages:
        from kora_v2.context.budget import BudgetTier, ContextBudgetMonitor

        monitor = ContextBudgetMonitor()
        # Convert messages to dicts for token counting
        msg_dicts = []
        for msg in messages:
            if isinstance(msg, dict):
                msg_dicts.append(msg)
            else:
                msg_dicts.append({
                    "role": getattr(msg, "type", "user"),
                    "content": getattr(msg, "content", ""),
                })

        # Compute usage once and cache both the tier and the raw token
        # estimate so the daemon can forward token_count in the response_complete
        # WebSocket metadata. Without this, observers only see tier names and
        # cannot track how close to the next escalation the conversation is.
        estimated_tokens = monitor.estimate_current_usage(msg_dicts, frozen)
        tier = monitor.get_tier(msg_dicts, frozen)
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

    # Append existing records
    existing_records = list(state.get("tool_call_records", []))
    existing_records.extend(tool_records)

    # Phase 5: topic-tracking for hyperfocus detection.
    # Tool-footprint heuristic — see §4.4 of the life engine spec.
    topic_update = _update_topic_tracker(state, tool_records)

    return {
        "messages": tool_results,
        "tool_call_records": existing_records,
        "_pending_tool_calls": [],  # Clear pending
        **topic_update,
    }


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
    # Gather active skills for tool gating
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

    # Build tools list based on what's actually available + skill gating
    available_tools = get_available_tools(container, active_skills=skill_names)
    log.info(
        "supervisor_tools_resolved",
        tool_count=len(available_tools),
        tools=[t["name"] for t in available_tools],
    )

    # Track iterations to prevent infinite tool loops
    iteration_count = {"value": 0}

    # Wrap node functions with container closure
    async def _receive(state: SupervisorState) -> dict[str, Any]:
        iteration_count["value"] = 0  # Reset on new turn
        container._turn_start_time = time.monotonic()  # Track for quality metrics
        base = await receive(state)
        turn = base["turn_count"]

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

        return base

    async def _build_suffix(state: SupervisorState) -> dict[str, Any]:
        return await build_suffix(state, container)

    async def _think(state: SupervisorState) -> dict[str, Any]:
        iteration_count["value"] += 1
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
        return await think(state, container, tools=available_tools)

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
    graph.add_edge("tool_loop", "think")  # Loop back for further processing
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
