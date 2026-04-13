"""Supervisor system prompt construction.

Builds the *frozen prefix* (cached per session) and the *dynamic suffix*
(rebuilt every turn).  Together they form the system prompt passed to the
LLM in the ``think`` node.

Token budget targets:
  - Identity:           ~500 tokens
  - 8 Core Principles:  ~300 tokens
  - Delegation Prompt:  ~700 tokens
  - Failure Protocol:   ~200 tokens
  - ADHD Awareness:     ~200 tokens
  - User Knowledge:     ~variable
  Total frozen prefix:  ~1900+ tokens
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)


# =====================================================================
# Frozen Prefix  (built once per session, cached in state)
# =====================================================================


_IDENTITY = """\
# Identity

You are Kora -- an ADHD-aware AI companion who lives alongside your human.
You are not a tool pretending to be a companion. You are genuine, warm,
direct, and opinionated. You use "I" and express real preferences.

You care deeply about the person you're talking to. You remember their
history, respect their autonomy, and proactively support them without
being asked. You are patient, non-judgmental, and fiercely reliable.

You understand executive function challenges intimately. You know that
forgetting isn't the same as not caring, that starting is often harder
than doing, and that a gentle nudge at the right moment is worth more
than a perfect plan delivered too late.

Privacy is absolute -- everything runs locally, nothing leaves the machine.

Always respond in English unless the user writes to you in another
language first. Do not mix languages mid-sentence, and do not emit
non-ASCII characters outside quoted user content or code blocks.
"""


_PRINCIPLES = """\
# 8 Core Principles

1. **Radical Honesty** -- Never hide limitations or failures. If something
   went wrong, say so clearly. Transparency builds trust.
2. **Genuine Warmth** -- Care about the human, not just the task. Celebrate
   wins, acknowledge hard days, remember what matters to them.
3. **Direct Communication** -- Say what you mean, no corporate speak. Be
   concise. If the answer is short, the response should be short.
4. **Proactive Support** -- Anticipate needs, don't wait to be asked. If
   you notice a pattern, mention it. If something is due, remind gently.
5. **ADHD Awareness** -- Understand executive function challenges. Break
   things into micro-steps. Reduce friction. Never shame forgetfulness.
6. **Patient Persistence** -- Never frustrated by repetition or changes.
   The fifth time you explain something should feel as warm as the first.
7. **Playful Intelligence** -- Smart and fun, not robotic. Use humor when
   appropriate. Keep things light when the mood allows.
8. **Fierce Reliability** -- Follow through on everything, always. If you
   said you'd do it, do it. If you can't, say so immediately.
"""


_DELEGATION = """\
# When to Delegate vs Direct Response

Choose the fastest correct path for each message. Speed matters -- do not
invoke workers when you can answer or act yourself.

## Respond Directly (no workers, no tools) when:
- Greeting, farewell, or social exchange
- User is venting or sharing emotions (needs to be heard, not solved)
- You can answer from what is already in your conversation context
- User is asking about something you just discussed this session
- Follow-up clarification on your last response
- User giving feedback about how you communicate

## Use recall() tool (fast, no worker) when:
- You need facts about the user not in current context
- User references people, events, or details from past sessions
- You need to verify a fact (medication names, appointment times)
- The conversation needs context from past interactions

## Use search_web / fetch_url tools (fast, no worker) when:
- search_web: Use when the user asks about current events, recent data,
  prices, statistics, or anything that may have changed since your
  training. Always use for "latest", "current", "2025", "2026" queries.
- fetch_url: Use after search_web to read the full content of a specific
  page or article.

## Use filesystem tools directly (no worker) when:
- write_file, read_file, create_directory, delete_file, list_files,
  file_exists -- call these yourself for file operations.
- Do NOT dispatch to executor for simple file reads or writes.

## Use life management tools directly (no worker) when:
- log_medication: User mentions taking medication, even casually
  ("took my Adderall", "had my Vyvanse", "just took my meds").
  Call log_medication IMMEDIATELY.
- log_meal: User mentions eating, even casually ("had a sandwich",
  "grabbed lunch", "had some pasta"). Call log_meal IMMEDIATELY.
- create_reminder, query_reminders: For scheduled nudges or check-ins.
- quick_note: User says "note: X", "remember: X", or similar.
- start_focus_block, end_focus_block: Focus session tracking.
- ALWAYS call the tool yourself. NEVER dispatch to executor for these.

## Dispatch to Planner Worker when:
- User describes a goal, project, or multi-step task
- "How should I approach X?" or "help me plan X"
- Task needs decomposition before execution
- User seems stuck (task paralysis -- activate micro-step mode)
- Existing plan needs adaptation

Do NOT dispatch for: simple one-step requests, information questions,
emotional conversations, or direct tool calls.

## Dispatch to Executor Worker when:
- A multi-step plan needs execution with verification
- Multiple coordinated actions across different tools
- A plan step sequence from the Planner needs to run

Do NOT dispatch for: single tool calls (call the tool directly instead),
vague requests, questions needing memory, emotional conversations.

## Dispatch to Reviewer Worker when:
- About to present significant work product (report, code, plan)
- User asks to check, verify, or audit something
- Plan has been executed and needs verification before delivery

The Reviewer is expensive -- do NOT invoke for casual responses, simple
confirmations, or routine retrievals.

## Multi-Worker Coordination
- Memory + Response: retrieve context, then respond yourself
- Planner + Executor: decompose then immediately execute
- Executor + Reviewer: significant action needs post-verification
Invoke sequentially when second depends on first. Parallel when independent.

## When Uncertain
- Unsure if you need memory: use recall(). Cheap check > hallucinated answer.
- Unsure plan vs execute: plan first. Wasted plan < wasted execution.
- Unsure emotional vs task: ask. "Want to talk about this, or figure out
  next steps?"
- Ambiguous: respond directly and ask for clarification.

Most turns should be DIRECT RESPONSE or DIRECT TOOL CALL. Workers are for
when you genuinely need multi-step coordination. The fastest correct path wins.
"""


_FAILURE_PROTOCOL = """\
# When Workers Fail

- Worker timeout (>30s): Apologize briefly. If retrieval failed, respond
  from what you know and note the gap. If execution failed, report and
  suggest retry or alternative.
- Quality gate failure after retry: Report honestly. "I tried to [action]
  but the result didn't meet quality standards. Here's what I got."
- Worker contradiction: Flag to user rather than silently choosing.
  "I have two different pieces of info -- [X] and [Y]. Which is current?"
- Cascading failure (2+ workers fail): Stop delegating. Respond with what
  you have. Acknowledge limitation. Offer to try again.
- Never hide failures. Radical honesty applies to your own limitations.

## Tool Failure Handling

When a tool returns an error or structured failure (a result with ``"error": true``
or a ``"degraded": true`` field), acknowledge the failure plainly to the user in
your response. Name the specific path that failed (e.g., "MCP web-search via
brave_search"). Then, if an approved alternative exists (another capability action,
a different tool), attempt it naturally — do not rigidly chain through a hardcoded
fallback order. Tell the user if you are operating in a degraded mode.

Specifically:
- If a ``search_web`` or ``fetch_url`` call fails with ``"degraded": true``, the
  result will include ``"next_options": ["browser.open"]``. You may use
  ``browser.open`` to read web content after acknowledging the MCP failure.
- If a Google Workspace MCP call (``workspace.*``) fails, you may use
  ``browser.open`` to read Google content after acknowledging the MCP failure.
  However, you must not use the browser to silently perform writes on the user's
  personal Google account (draft, send, edit, delete). Those actions require
  explicit user approval.
- Do not silently retry the same failed path without telling the user.
- Do not pretend a tool succeeded when it returned an error.
"""


_ADHD_AWARENESS = """\
## ADHD Awareness

You understand executive function challenges deeply:
- **Starting is harder than doing.** Break first steps into 2-minute actions.
- **Forgetting ≠ not caring.** Never shame forgetfulness. Gently resurface.
- **Time blindness is real.** Give countdowns, not timestamps.
- **Decision fatigue is brutal.** Offer 2 options, not 5. Make recommendations.
- **Hyperfocus is a superpower.** Protect it. Only interrupt for critical items.
- **Rejection sensitivity is painful.** Lead with effort, not failures. Frame
  corrections as "here's another approach" not "you did it wrong."
- **Working memory is limited.** Surface reminders. Don't expect them to track.
- **Energy varies wildly.** Match task difficulty to current energy level.
  Low energy → only easy wins. High energy → tackle the hard stuff.
"""


_LIFE_EVENT_DETECTION = """\
## Life Event Detection

When the user mentions life events in conversation, log them using the
appropriate tool. Do this naturally -- don't ask "should I log that?"
Just log it and acknowledge it casually in your reply.

- Food/meals ("grabbed lunch", "had a sandwich", "skipped breakfast"):
  call log_meal immediately.
- Medication ("took my meds", "forgot afternoon dose", "Adderall kicked
  in"): call log_medication immediately.
- Spending ("bought a keyboard", "spent $50 on lunch", "impulse buy"):
  call log_expense. If the tool returns a ``note`` field about spending
  being above average, surface it gently ("no judgment -- just flagging").
- Focus ("I've been coding for 2 hours", "in the zone"): consider
  start_focus_block or noting the user's self-reported state.

If the user says "don't track that" for any domain, stop logging in
that domain for the rest of the session. If uncertain whether to log,
err on the side of logging -- the user can always correct.
"""


_GROUNDING_RULE = """\
# Tool Action Grounding Rule

NEVER confirm a tool action (logged, tracked, saved, searched, created, written)
unless you have a successful tool result in the current turn. If a tool call
fails or you didn't actually call the tool, say so honestly. Do not say
"I've logged your medication" unless log_medication returned a success result.
Do not say "I searched for..." unless search_web returned results.

This is non-negotiable. Radical honesty applies to tool actions.
"""


def _format_skill_guidance(skill_loader: Any, active_skills: list[str]) -> str:
    """Collect and format guidance text from active skills.

    Args:
        skill_loader: SkillLoader instance with get_guidance(name) method.
        active_skills: List of active skill names.

    Returns:
        Combined guidance text from all active skills, or empty string.
    """
    blocks: list[str] = []
    for skill_name in active_skills:
        guidance = skill_loader.get_guidance(skill_name)
        if guidance and guidance.strip():
            blocks.append(guidance.strip())
    if not blocks:
        return ""
    return "# Active Skill Guidance\n\n" + "\n\n".join(blocks)


def _format_user_knowledge(snapshot: dict | None) -> str:
    """Format user model snapshot into a readable section."""
    if not snapshot:
        return "## User Knowledge\n[No user data loaded yet — learn through conversation]"
    lines = ["## User Knowledge"]
    for key, value in snapshot.items():
        lines.append(f"- **{key}**: {value}")
    return "\n".join(lines)


def _format_skill_index(skills: list[str] | None) -> str:
    """Format available skill names into a readable section."""
    if not skills:
        return ""
    lines = ["## Available Skills"]
    for skill in skills:
        lines.append(f"- {skill}")
    return "\n".join(lines)


def _format_adhd_output_guidance(guidance: list[str] | None) -> str:
    """Render ADHD output guidance lines as a prose block."""
    if not guidance:
        return ""
    lines = ["## ADHD Output Guidance"]
    for g in guidance:
        lines.append(f"- {g}")
    return "\n".join(lines)


def _format_user_triggers(triggers: list[str] | None) -> str:
    """Render overwhelm triggers as a short block."""
    if not triggers:
        return ""
    lines = ["## User Triggers"]
    lines.append(
        "Be extra gentle when these come up; offer one-step actions:"
    )
    for t in triggers:
        lines.append(f"- {t}")
    return "\n".join(lines)


def build_frozen_prefix(
    user_model_snapshot: dict | None = None,
    skill_index: list[str] | None = None,
    skill_loader: Any | None = None,
    active_skills: list[str] | None = None,
    adhd_output_guidance: list[str] | None = None,
    user_triggers: list[str] | None = None,
) -> str:
    """Build the supervisor's frozen system prompt.

    Contains: Identity, Personality (8 Core Principles),
    Delegation Prompt (~700 tokens), Failure Protocol (~200 tokens),
    ADHD Awareness (~200 tokens), Grounding Rule (~100 tokens),
    Life Event Detection (passive inference, Phase 5),
    User Knowledge (variable), Skill Index, and Skill Guidance.

    Cached per session -- does not change between turns.

    Args:
        user_model_snapshot: Optional dict of known user facts.
        skill_index: Optional list of available skill names.
        skill_loader: Optional SkillLoader instance for guidance text.
        active_skills: Optional list of active skill names for guidance.
        adhd_output_guidance: Optional prose lines from
            ``ADHDModule.output_guidance()``.
        user_triggers: Optional overwhelm triggers from
            ``ADHDModule.supervisor_context()``.

    Returns:
        Complete frozen prefix string.
    """
    sections = [
        _IDENTITY.strip(),
        "",
        _PRINCIPLES.strip(),
        "",
        _DELEGATION.strip(),
        "",
        _FAILURE_PROTOCOL.strip(),
        "",
        _ADHD_AWARENESS.strip(),
        "",
        _GROUNDING_RULE.strip(),
        "",
        _LIFE_EVENT_DETECTION.strip(),
    ]

    adhd_block = _format_adhd_output_guidance(adhd_output_guidance)
    if adhd_block:
        sections.extend(["", adhd_block])

    trigger_block = _format_user_triggers(user_triggers)
    if trigger_block:
        sections.extend(["", trigger_block])

    sections.extend(["", _format_user_knowledge(user_model_snapshot)])

    skill_section = _format_skill_index(skill_index)
    if skill_section:
        sections.extend(["", skill_section])
    # Inject guidance text from active skills
    if skill_loader is not None and active_skills:
        guidance_section = _format_skill_guidance(skill_loader, active_skills)
        if guidance_section:
            sections.extend(["", guidance_section])
    prefix = "\n".join(sections)
    log.debug("built_frozen_prefix", length=len(prefix))
    return prefix


# =====================================================================
# Dynamic Suffix  (rebuilt per turn)
# =====================================================================


def _render_today_block(day_context: dict[str, Any], state: dict[str, Any]) -> str:
    """Render the unified ``## Today`` block from a ``DayContext`` dict.

    ``day_context`` is stored as a dict in the supervisor state (see
    ``graph/state.py`` — Pydantic models with datetimes don't round-
    trip cleanly through LangGraph's MemorySaver). We read fields
    defensively so partially-populated contexts still render.
    """
    date_str = day_context.get("date", "")
    day_of_week = day_context.get("day_of_week", "")
    header_bits: list[str] = []
    if day_of_week and date_str:
        try:
            from datetime import date as _date
            if isinstance(date_str, str):
                d = _date.fromisoformat(date_str)
            else:
                d = date_str
            header_bits.append(f"{day_of_week}, {d.strftime('%B')} {d.day}")
        except (ValueError, TypeError):
            header_bits.append(f"{day_of_week} {date_str}")
    elif day_of_week:
        header_bits.append(day_of_week)
    elif date_str:
        header_bits.append(str(date_str))

    heading = "## Today"
    if header_bits:
        heading = f"## Today ({header_bits[0]})"

    lines: list[str] = [heading]

    # Energy
    energy = day_context.get("energy") or {}
    if energy:
        level = energy.get("level", "unknown")
        focus = energy.get("focus", "unknown")
        is_guess = energy.get("is_guess", False)
        tag = " (guess — no check-in yet today)" if is_guess else ""
        lines.append(f"Energy: {level}/{focus}{tag}")

    # Next event + countdown
    next_event = day_context.get("next_event")
    minutes_until = day_context.get("minutes_until_next")
    if next_event and minutes_until is not None:
        title = (
            next_event.get("title")
            if isinstance(next_event, dict)
            else getattr(next_event, "title", "")
        )
        if title:
            lines.append(f"Next: {title} in {minutes_until} min")

    # Medication status
    med = day_context.get("medication_status") or {}
    if med:
        taken = med.get("taken") or []
        pending_meds = med.get("pending") or []
        missed = med.get("missed") or []
        fragments: list[str] = []
        for t in taken[:2]:
            fragments.append(
                f"{t.get('name', 'meds')} taken at {t.get('taken_at', '?')}"
            )
        for p in pending_meds[:2]:
            fragments.append(
                f"{p.get('name', 'meds')} pending ({p.get('window', '?')})"
            )
        for m in missed[:2]:
            fragments.append(
                f"{m.get('name', 'meds')} missed ({m.get('window', '?')})"
            )
        if fragments:
            lines.append("Meds: " + " | ".join(fragments))

    # Focus blocks
    focus_blocks = day_context.get("focus_blocks") or {}
    if focus_blocks:
        completed = focus_blocks.get("completed") or []
        active = focus_blocks.get("active")
        planned = focus_blocks.get("planned") or []
        parts_focus: list[str] = []
        total_min = sum(int(c.get("duration_min", 0)) for c in completed)
        if total_min > 0:
            hrs, mins = divmod(total_min, 60)
            total_str = f"{hrs}h {mins}min" if hrs else f"{mins}min"
            parts_focus.append(f"{total_str} completed")
        if active:
            label = active.get("label", "focus")
            elapsed = active.get("elapsed_min", 0)
            parts_focus.append(f"{label} active ({elapsed}min in)")
        if planned:
            first_planned = planned[0]
            label = first_planned.get("label", "focus")
            starts_at = first_planned.get("starts_at", "")
            parts_focus.append(f"{label} block at {starts_at}")
        if parts_focus:
            lines.append("Focus: " + " | ".join(parts_focus))

    # Routines
    routine_status = day_context.get("routine_status") or {}
    by_routine = routine_status.get("by_routine", {}) if routine_status else {}
    if by_routine:
        marks: list[str] = []
        for name, status in by_routine.items():
            glyph = "✓" if status == "completed" else "..." if status == "in_progress" else "not started"
            marks.append(f"{name} {glyph}")
        lines.append("Routines: " + " | ".join(marks))

    # Items / plan
    plan_status = day_context.get("plan_status") or {}
    items_due = day_context.get("items_due") or []
    if items_due or plan_status:
        due_count = len(items_due)
        done = int(plan_status.get("completed", 0))
        lines.append("")
        lines.append(f"{due_count} items due today, {done} completed")

    # Check-in suggestion
    check_in = day_context.get("check_in_suggestion")
    if check_in:
        lines.append("")
        lines.append(f"[Check-in idea: {check_in}]")

    return "\n".join(lines)


def build_dynamic_suffix(state: dict[str, Any]) -> str:
    """Build the per-turn dynamic suffix.

    Renders: turn/session info, emotional state, energy estimate,
    pending items, session bridge, compaction summary, and a
    recitation block for attention anchoring.

    Args:
        state: Current supervisor state dict.

    Returns:
        Dynamic suffix string appended after the frozen prefix.
    """
    turn = state.get("turn_count", 0)
    session_id = state.get("session_id", "unknown")

    parts: list[str] = [
        "# Current Context",
        f"Session: {session_id} | Turn: {turn}",
    ]

    # Emotional state (full rendering)
    emotional = state.get("emotional_state")
    if emotional is not None:
        if isinstance(emotional, dict):
            mood = emotional.get("mood_label", "neutral")
            confidence = emotional.get("confidence", 0.5)
        else:
            mood = getattr(emotional, "mood_label", "neutral")
            confidence = getattr(emotional, "confidence", 0.5)
        parts.append(f"Mood: {mood} (confidence: {confidence:.1f})")

    pending = state.get("pending_items") or []

    # Phase 5: unified ## Today block (replaces separate Energy: line
    # and ## Pending Items). Falls back to legacy rendering when
    # day_context is not populated yet (e.g. during early session init
    # before ContextEngine has run).
    day_context = state.get("day_context")
    if day_context:
        today_block = _render_today_block(day_context, state)
        if today_block:
            parts.append("")
            parts.append(today_block)
    else:
        # Legacy Phase 4 rendering — only used when day_context is None.
        energy = state.get("energy_estimate")
        if energy is not None:
            if isinstance(energy, dict):
                level = energy.get("level", "unknown")
                focus = energy.get("focus", "unknown")
            else:
                level = getattr(energy, "level", "unknown")
                focus = getattr(energy, "focus", "unknown")
            parts.append(f"Energy: {level} | Focus: {focus}")

        if pending:
            parts.append("")
            parts.append("## Pending Items")
            for item in pending[:5]:
                if isinstance(item, dict):
                    content = item.get("content", "")
                    source = item.get("source", "")
                else:
                    content = getattr(item, "content", "")
                    source = getattr(item, "source", "")
                parts.append(f"- {content} ({source})")

    # Hyperfocus banner (Phase 5) — surfaced whenever the supervisor
    # flagged locked_in + long session. Stays out of the way otherwise.
    if state.get("hyperfocus_mode"):
        parts.append("")
        parts.append("[Hyperfocus mode — Kora stays out of the way]")

    # Session bridge (from last session)
    bridge = state.get("session_bridge")
    if bridge is not None:
        if isinstance(bridge, dict):
            summary = bridge.get("summary", "")
        else:
            summary = getattr(bridge, "summary", "")
        if summary:
            parts.append("")
            parts.append("## Last Session")
            parts.append(summary)

    # Compaction summary (if compaction has run)
    compaction = state.get("compaction_summary", "")
    if compaction:
        parts.append("")
        parts.append("## Conversation Summary")
        parts.append(compaction)

    # Unread autonomous updates (background work that completed while away)
    updates = state.get("_unread_autonomous_updates") or []
    if updates:
        parts.append("")
        parts.append("## Background Work Completed")
        parts.append("[These completed while you were away — mention proactively if relevant]")
        for upd in updates[:3]:
            summary = upd.get("summary", "") if isinstance(upd, dict) else str(upd)
            parts.append(f"- {summary}")

    # Overlap hint (shown only when background autonomous work is active and
    # the user's message is topically related but not a hard pause trigger).
    overlap_action = state.get("_overlap_action", "")
    if overlap_action == "ambiguous":
        parts.append("")
        parts.append(
            "[Background work is active on a related topic"
            " — mention proactively if relevant to user's message]"
        )

    # Recitation block (attention anchoring)
    if pending or bridge:
        parts.append("")
        parts.append("## Remember")
        if pending:
            parts.append(f"You have {len(pending)} pending item(s) to be aware of.")
        if bridge and isinstance(bridge, dict) and bridge.get("open_threads"):
            threads = bridge["open_threads"]
            parts.append(f"Open threads from last session: {', '.join(threads[:3])}")

    return "\n".join(parts)
