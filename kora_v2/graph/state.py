"""Supervisor graph state definition.

Minimal TypedDict with Annotated reducers for fields that need
merge semantics under concurrent or sequential node writes.

Workers have their own typed state within their subgraphs.
The supervisor state tracks coordination concerns only.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from kora_v2.graph.reducers import (
    add_messages_reducer,
    bounded_errors_reducer,
    last_value_list_reducer,
)


class SupervisorState(TypedDict, total=False):
    """State for the supervisor LangGraph graph.

    Fields marked ``total=False`` are optional -- nodes only write
    the keys they need to update.

    Reducers
    --------
    * ``messages`` -- unbounded conversation log (add_messages_reducer
      delegates to LangGraph ``add_messages`` + tool pair integrity).
    * ``pending_items``, ``active_workers``, ``tool_call_records`` --
      last-value-wins lists (each write replaces the prior value).
    * ``errors`` -- bounded append-only list (oldest evicted first).

    All other fields are last-value-wins scalars (LangGraph default).
    """

    # ── Conversation ──────────────────────────────────────────────
    messages: Annotated[list[dict[str, Any]], add_messages_reducer]
    session_id: str
    turn_count: int

    # ── Context (built by build_suffix) ───────────────────────────
    # None until Phase 2+ when emotion / energy systems come online
    emotional_state: dict[str, Any] | None
    energy_estimate: dict[str, Any] | None
    pending_items: Annotated[list[dict[str, Any]], last_value_list_reducer]

    # ── Per-turn (reset by receive node) ──────────────────────────
    active_workers: Annotated[list[dict[str, Any]], last_value_list_reducer]
    tool_call_records: Annotated[list[dict[str, Any]], last_value_list_reducer]

    # ── Frozen prefix (built once per session, cached) ────────────
    frozen_prefix: str

    # ── Response tracking ─────────────────────────────────────────
    response_content: str

    # ── Error tracking ────────────────────────────────────────────
    errors: Annotated[list[str], bounded_errors_reducer]

    # ── Compaction (Phase 4) ────────────────────────────────────
    compaction_tier: str  # Budget tier name: "NORMAL", "PRUNE", "SUMMARIZE", "AGGRESSIVE", "HARD_STOP"
    compaction_tokens: int  # Estimated token count used to determine the tier
    compaction_summary: str  # Structured summary from compaction, empty if none
    session_bridge: dict[str, Any] | None  # Last session's bridge note, loaded at init
    greeting_sent: bool  # Whether session greeting has been sent this session

    # ── Phase 5: ADHD life engine ─────────────────────────────────
    # Populated by build_suffix from ContextEngine.build_day_context()
    # and consumed by the "## Today" block renderer. Stored as dict so
    # it survives LangGraph checkpointing (raw Pydantic models with
    # datetimes don't round-trip cleanly through MemorySaver).
    day_context: dict[str, Any] | None
    # Topic-tracking state used for hyperfocus detection. Reset when
    # the tool-footprint heuristic says "new topic".
    turns_in_current_topic: int
    hyperfocus_mode: bool
    topic_tracker: dict[str, Any] | None

    # ── Internal (graph-private, not part of public contract) ─────
    # Dynamic suffix assembled by build_suffix, consumed by think.
    _dynamic_suffix: str
    # Pending tool calls from the think node, consumed by tool_loop.
    _pending_tool_calls: Annotated[list[dict[str, Any]], last_value_list_reducer]
    # Autonomous updates (checkpoint + completion summaries) that finished
    # while the user was away — injected into the dynamic suffix by
    # build_suffix so the supervisor can mention them proactively.
    _unread_autonomous_updates: list[dict[str, Any]]
    # Overlap detection from _check_autonomous_overlap (injected via graph_input).
    # Score 0.0–1.0; action one of "continue" | "ambiguous" | "pause".
    _overlap_score: float
    _overlap_action: str
