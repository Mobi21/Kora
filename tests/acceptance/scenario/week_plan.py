"""Week scenario plan for Jordan's 3-day acceptance test.

Coverage matrix is the post-Phase 8 surface (67 numbered items + a
``capability_pack`` namespace at 100+).  Items 1-23 are the legacy
acceptance items rewritten for the orchestration era; items 24-46 land
with Phase 7.5 (orchestration layer); items 47-67 land with Phase 8
(Memory Steward, Vault Organizer, ContextEngine, ProactiveAgent,
reminders, wake-up briefing).

Items track:

- ``description``   one-line behavioural statement, grounded in real code
- ``status``        ACTIVE | DEFERRED — DEFERRED items skip auto-marking
- ``category``      bucket used by the report (e.g. ``orchestration``)
- ``evidence_query``  optional string description of the database / file
                      evidence that proves the item satisfied. The actual
                      query callable lands in slice AT3 — for now the
                      field is documentation only.
- ``deferred_reason`` short reason an item is parked DEFERRED

Each phase in :data:`WEEK_PLAN` references item ids via ``coverage_items``.
The harness uses the same ids when emitting auto-markers in the report.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CoverageStatus(Enum):
    """Whether a coverage item can be tested against the current V2 runtime."""

    ACTIVE = "active"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class CoverageItem:
    """One coverage row.

    ``evidence_query`` is a *string* description of what state proves the
    item satisfied (e.g. ``"row in pipeline_instances with name='post_session_memory'
    and state='completed'"``). The actual query callable wires up in
    slice AT3; storing a string today keeps the matrix self-documenting
    without forcing the harness to run queries it cannot yet support.
    """

    description: str
    status: CoverageStatus
    category: str
    evidence_query: str | None = None
    deferred_reason: str | None = None


# ── Coverage checklist ───────────────────────────────────────────────────────

COVERAGE_ITEMS: dict[int, CoverageItem] = {
    # ── 1-23: legacy acceptance items, rewritten for the post-Phase 8
    #         system (orchestration engine + Memory Steward + Vault
    #         Organizer + ContextEngine + ProactiveAgent).
    1: CoverageItem(
        description="First-run onboarding completes naturally (identity / vault path / "
        "schedule confirmed inside the first session).",
        status=CoverageStatus.DEFERRED,
        category="core",
        deferred_reason=(
            "V2 has no first-run wizard — neither kora_v2/daemon/server.py "
            "nor kora_v2/cli/ exposes one. Jordan establishes context "
            "through conversation instead."
        ),
    ),
    2: CoverageItem(
        description="Jordan's personal context established: name, ADHD, partner Alex, "
        "cat Mochi, Adderall + melatonin meds, current job.",
        status=CoverageStatus.ACTIVE,
        category="core",
        evidence_query="messages mention 'adhd' AND ('mochi' OR 'alex')",
    ),
    3: CoverageItem(
        description="Week-planning session establishes concrete tasks across all 3 "
        "tracks (coding, research, writing).",
        status=CoverageStatus.ACTIVE,
        category="core",
        evidence_query="write_file count >= 2 AND messages mention 'plan'/'week'",
    ),
    4: CoverageItem(
        description="Coding track moves planning -> implementation -> revision (file "
        "operations + code-shaped messages).",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    5: CoverageItem(
        description="Research track moves kickoff -> evidence gathering -> synthesis.",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    6: CoverageItem(
        description="Writing track moves outline -> draft -> revision.",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    7: CoverageItem(
        description="Life management tools fire: log_medication, log_meal, "
        "create_reminder, quick_note, start/end focus block.",
        status=CoverageStatus.ACTIVE,
        category="life_management",
        evidence_query="any tool in life_tools bucket called",
    ),
    8: CoverageItem(
        description="Sub-task delegation via decompose_and_dispatch creates a "
        "pipeline_instance with multiple stages and aggregates structured "
        "output back to the supervisor.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query=(
            "row in pipeline_instances created from decompose_and_dispatch "
            "tool call AND >=2 worker_tasks completed under it"
        ),
    ),
    9: CoverageItem(
        description="Web research via search_web/fetch_url or browser.* capability — "
        "either a successful MCP-backed call completes, or an explicit "
        "MCP failure is surfaced and Kora picks a documented next step "
        "without silent fallback.",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    10: CoverageItem(
        description="Long-context compaction pressure survived without losing "
        "in-conversation facts.",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    11: CoverageItem(
        description="Mid-week revision wave absorbed across all 3 tracks (Kora "
        "actually replans rather than just acknowledging).",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    12: CoverageItem(
        description="Real background pipelines fire during DEEP_IDLE — "
        "session_bridge_pruning and skill_refinement (see core_pipelines "
        "items 19-20) execute and write to work_ledger.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query=(
            "rows in work_ledger with pipeline name in "
            "{'session_bridge_pruning', 'skill_refinement'}"
        ),
    ),
    13: CoverageItem(
        description="Daemon restart preserves session continuity, working docs, "
        "and orchestration state.",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    14: CoverageItem(
        description="Weekly review reflects the actual 3-day run (concrete "
        "accomplishments per track, no vague claims).",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    15: CoverageItem(
        description="Compaction detected via response metadata (token_count + "
        "compaction tier change observed in assistant messages).",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    16: CoverageItem(
        description="recall tool returns facts established earlier in conversation "
        "(memory layer hybrid vector + FTS5 search succeeds).",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    17: CoverageItem(
        description="Auth relay round-trip: deny once, then approve, both paths "
        "verified and logged.",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    18: CoverageItem(
        description="Error recovery: malformed input handled gracefully and the "
        "session survives without the daemon crashing.",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    19: CoverageItem(
        description="Emotion / energy assessment adapts response tone to Jordan's "
        "state (focused -> scattered -> recovering).",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    20: CoverageItem(
        description="Skill activation gates tools (life_management, code_work, "
        "web_research surfaces on-demand only).",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    21: CoverageItem(
        description="Long-running autonomous execution via "
        "decompose_and_dispatch(intent_duration='long') — pipeline_instance "
        "is created, working doc seeded in _KoraMemory/Inbox, work_ledger "
        "rows accumulate, completion summary delivered.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query=(
            "row in pipeline_instances with intent_duration='long' AND "
            "matching working_docs/<slug>.md AND >=1 work_ledger row"
        ),
    ),
    22: CoverageItem(
        description="Filesystem operations exercised (read_file, write_file, "
        "list_directory) without permission errors.",
        status=CoverageStatus.ACTIVE,
        category="core",
    ),
    23: CoverageItem(
        description="Life management DB records persist — medication_log, meal_log, "
        "reminders queryable from operational.db after creation.",
        status=CoverageStatus.ACTIVE,
        category="life_management",
        evidence_query=(
            "SELECT COUNT(*) FROM medication_log/meal_log/reminders > 0"
        ),
    ),

    # ── 24-46: Phase 7.5 orchestration layer (spec §18.2). Wording is
    #         taken directly from the spec; deferral table in §18.7 is
    #         honoured below.
    24: CoverageItem(
        description="SystemStatePhase transitions logged: CONVERSATION -> "
        "ACTIVE_IDLE -> LIGHT_IDLE -> DEEP_IDLE observed in "
        "system_state_log during the 3-day run.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="distinct phase values in system_state_log >= 3",
    ),
    25: CoverageItem(
        description="LONG_BACKGROUND task dispatch — Jordan asks for overnight "
        "research; supervisor calls decompose_and_dispatch with the "
        "long preset and replies with a templated acknowledgment.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query=(
            "worker_tasks row with preset='long_background' AND turn ended "
            "with templated ack (provider_request_count_for_turn=0)"
        ),
    ),
    26: CoverageItem(
        description="Working document visible at _KoraMemory/Inbox/{slug}.md after "
        "dispatch; status frontmatter reads 'in_progress' and the doc "
        "grows as the task runs.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="file at _KoraMemory/Inbox/{slug}.md with frontmatter status",
    ),
    27: CoverageItem(
        description="Adaptive task list mutation: the running task adds new items "
        "to its working doc's Current Plan; dispatcher picks them up "
        "as fresh worker_tasks.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="working_doc.current_plan grows AND new worker_tasks appear",
    ),
    28: CoverageItem(
        description="Kora-judged completion: working doc frontmatter transitions "
        "to status: done without an external counter; pipeline_instance "
        "moves to completed and a notification is delivered.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="frontmatter status='done' AND pipeline_instance.state='completed'",
    ),
    29: CoverageItem(
        description="Mid-flight get_task_progress returns accurate task state and "
        "elapsed time when called during a running pipeline.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="get_task_progress tool call returns running task with elapsed >0",
    ),
    30: CoverageItem(
        description="cancel_task respects cancellation at the next checkpoint, "
        "preserves partial results in the working doc, and writes a "
        "TASK_CANCELLED ledger event.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="work_ledger row event_type='TASK_CANCELLED' for the cancelled task",
    ),
    31: CoverageItem(
        description="User edit to the working doc (added Current Plan item) is "
        "picked up by the dispatcher; a new WorkerTask is created for it.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="working doc edit timestamp < new worker_task.created_at",
    ),
    32: CoverageItem(
        description="CONVERSATION reserve preserved during heavy background work — "
        "starting a session never fails on rate-limit even when "
        "BACKGROUND tasks are saturating the limiter.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="RequestLimiter.reserve_ok(class=CONVERSATION) returns True under load",
    ),
    33: CoverageItem(
        description="Rate-limit graceful pause + resume: BACKGROUND class hits its "
        "sliding window, tasks pause cleanly, window reopens, tasks "
        "resume from checkpoint.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="paired RATE_LIMIT_PAUSED + RATE_LIMIT_RESUMED rows in work_ledger",
    ),
    34: CoverageItem(
        description="Templated fallback when CONVERSATION reserve exhausted — "
        "daemon delivers a templated rate-limit message via "
        "NotificationGate without making any provider request.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="templated reply emitted AND provider_request_count_for_turn=0",
    ),
    35: CoverageItem(
        description="Crash recovery of a long-running task at each lifecycle state "
        "— daemon kill + restart resumes from latest checkpoint and "
        "the working doc is intact.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="task resumes after restart AND working doc parses cleanly",
    ),
    36: CoverageItem(
        description="Multiple concurrent autonomous tasks interleave correctly "
        "(no starvation, both reach completion independently).",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query=">=2 pipeline_instances with intent_duration='long' both completed",
    ),
    37: CoverageItem(
        description="Merge on re-engagement: after a long task completes during "
        "idle, Jordan starts a new session and the supervisor surfaces "
        "the completed result automatically (via get_running_tasks "
        "relevant_to_session).",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="completed task surfaced in first assistant turn of new session",
    ),
    38: CoverageItem(
        description="continuity_check pipeline fires inline during a long session "
        "for a time-critical notification (medication window or meeting "
        "reminder); supervisor surfaces it without breaking the turn.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="continuity_check pipeline_instance completed mid-session",
    ),
    39: CoverageItem(
        description="post_session_memory completion triggers post_memory_vault via "
        "the sequence_complete trigger (see core_pipelines L327).",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="sequence_complete trigger row links the two pipeline_instances",
    ),
    40: CoverageItem(
        description="WAKE_UP_WINDOW phase derived correctly + wake_up_preparation "
        "pipeline runs before Jordan's simulated wake time.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query=(
            "system_state_log row phase='wake_up_window' AND "
            "wake_up_preparation pipeline_instance completed within window"
        ),
    ),
    41: CoverageItem(
        description="contextual_engagement pipeline fires on EMOTION_SHIFT_DETECTED "
        "(see core_pipelines L499-L513) and Kora delivers a "
        "context-appropriate nudge.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="contextual_engagement pipeline_instance completed after EMOTION_SHIFT event",
    ),
    42: CoverageItem(
        description="proactive_pattern_scan fires on injected ContextEngine insight "
        "(INSIGHT_AVAILABLE event) and a nudge appears in notifications.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="proactive_pattern_scan pipeline_instance completed after INSIGHT_AVAILABLE",
    ),
    43: CoverageItem(
        description="record_decision recorded an open decision; after simulated "
        "3 days the open_decisions tracker fires DECISION_PENDING_3D.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="open_decisions row + DECISION_PENDING_3D event in work_ledger",
    ),
    44: CoverageItem(
        description="Routine creation registers a runtime pipeline (row in "
        "runtime_pipelines table); pipeline survives daemon restart and "
        "fires at its scheduled time.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="runtime_pipelines row persists across restart AND fired",
    ),
    45: CoverageItem(
        description="WorkLedger answers 'why did X run' for any pipeline_instance "
        "(triggers, parent task, completion summary all queryable).",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="work_ledger query returns trigger + parent + completion for any pipeline_instance_id",
    ),
    46: CoverageItem(
        description="LONG_BACKGROUND dispatches end the supervisor turn with a "
        "templated acknowledgment — provider request count for that "
        "turn is exactly zero after the dispatch tool returns.",
        status=CoverageStatus.ACTIVE,
        category="orchestration",
        evidence_query="provider_request_count_for_turn=0 after long-background dispatch",
    ),

    # ── 47-67: Phase 8 content-level coverage (spec §"Acceptance Test
    #         Coverage Additions"). Wording adapted to ground in the
    #         actual handlers in kora_v2/agents/background/.
    47: CoverageItem(
        description="Memory extraction (memory_steward_handlers.extract_step) "
        "produces domain-typed facts from session transcripts and "
        "writes them through WritePipeline.",
        status=CoverageStatus.ACTIVE,
        category="memory_steward",
        evidence_query="post_session_memory:extract stage completed AND WritePipeline rows added",
    ),
    48: CoverageItem(
        description="Memory consolidation (consolidate_step) merges semantically "
        "related notes without losing facts.",
        status=CoverageStatus.ACTIVE,
        category="memory_steward",
        evidence_query="post_session_memory:consolidate completed; consolidated note count > 0",
    ),
    49: CoverageItem(
        description="Memory deduplication (dedup_step) preserves the richer note "
        "and soft-deletes its near-duplicate.",
        status=CoverageStatus.ACTIVE,
        category="memory_steward",
        evidence_query="dedup_step ran AND notes table has rows with deleted_at NOT NULL",
    ),
    50: CoverageItem(
        description="Entity resolution (entities_step) merges fuzzy variants "
        "(e.g. 'Alex' / 'alex' / 'my partner') across sessions.",
        status=CoverageStatus.ACTIVE,
        category="memory_steward",
        evidence_query="entities_step ran AND entity row count decreased OR canonical_id assigned",
    ),
    51: CoverageItem(
        description="ADHD profile weekly refinement (adhd_profile_refine_step / "
        "weekly_adhd_profile pipeline) runs and updates the User Model.",
        status=CoverageStatus.ACTIVE,
        category="memory_steward",
        evidence_query="weekly_adhd_profile pipeline_instance completed AND User Model updated",
    ),
    52: CoverageItem(
        description="Vault Organizer reindexing (reindex_step) detects "
        "filesystem-edited notes (mtime changed without a corresponding "
        "internal write).",
        status=CoverageStatus.ACTIVE,
        category="vault_organizer",
        evidence_query="post_memory_vault:reindex completed AND >=1 stale entry re-embedded",
    ),
    53: CoverageItem(
        description="Vault Organizer structure step (structure_step) enforces "
        "folder hierarchy on Inbox triage — files move from Inbox/ "
        "into the canonical folder.",
        status=CoverageStatus.ACTIVE,
        category="vault_organizer",
        evidence_query="structure_step completed AND files moved out of Inbox/",
    ),
    54: CoverageItem(
        description="Wikilinks injected (links_step) into notes without corrupting "
        "frontmatter or fenced code blocks.",
        status=CoverageStatus.ACTIVE,
        category="vault_organizer",
        evidence_query="links_step completed AND wikilink count > 0 AND frontmatter still parses",
    ),
    55: CoverageItem(
        description="Entity pages generated with backlinks, relationships, and "
        "mention dates.",
        status=CoverageStatus.ACTIVE,
        category="vault_organizer",
        evidence_query="files in _KoraMemory/Entities/ with backlink section populated",
    ),
    56: CoverageItem(
        description="MOC (map-of-content) pages regenerated when the threshold of "
        "structural changes is reached.",
        status=CoverageStatus.ACTIVE,
        category="vault_organizer",
        evidence_query="moc_sessions_step completed AND MOC files updated",
    ),
    57: CoverageItem(
        description="Session index and per-session notes populated under "
        "_KoraMemory/Sessions/.",
        status=CoverageStatus.ACTIVE,
        category="vault_organizer",
        evidence_query="files in _KoraMemory/Sessions/ AND index.md present",
    ),
    58: CoverageItem(
        description="ContextEngine emits cross-domain insights consumed by "
        "proactive_pattern_scan (INSIGHT_AVAILABLE event observed).",
        status=CoverageStatus.ACTIVE,
        category="context_engine",
        evidence_query="INSIGHT_AVAILABLE event in event log AND proactive_pattern_scan triggered",
    ),
    59: CoverageItem(
        description="ProactiveAgent Area A: pattern-based nudge delivered "
        "(proactive_pattern_scan_step writes through NotificationGate).",
        status=CoverageStatus.ACTIVE,
        category="proactive_agent",
        evidence_query="NotificationGate row of kind='pattern_nudge'",
    ),
    60: CoverageItem(
        description="ProactiveAgent Area B: anticipatory_prep_step assembles a "
        "briefing before an upcoming event.",
        status=CoverageStatus.ACTIVE,
        category="proactive_agent",
        evidence_query="anticipatory_prep pipeline_instance completed AND briefing artifact written",
    ),
    61: CoverageItem(
        description="ProactiveAgent Area C: rabbit-hole research dispatched via "
        "proactive_research_step; adaptive task list mutates; Kora "
        "judges done.",
        status=CoverageStatus.ACTIVE,
        category="proactive_agent",
        evidence_query="proactive_research pipeline_instance state='completed' AND working_doc grew",
    ),
    62: CoverageItem(
        description="ProactiveAgent Area D: contextual_engagement_step fires on "
        "an emotion shift and surfaces a contextual nudge.",
        status=CoverageStatus.ACTIVE,
        category="proactive_agent",
        evidence_query="contextual_engagement pipeline_instance completed after EMOTION_SHIFT_DETECTED",
    ),
    63: CoverageItem(
        description="ProactiveAgent Area E: commitment_tracking_step surfaces "
        "yesterday's promises in today's notifications.",
        status=CoverageStatus.ACTIVE,
        category="proactive_agent",
        evidence_query="commitment_tracking pipeline_instance completed AND commitment_log rows surfaced",
    ),
    64: CoverageItem(
        description="ProactiveAgent Area E: stuck_detection_step offers help "
        "without being asked when work has been lingering.",
        status=CoverageStatus.ACTIVE,
        category="proactive_agent",
        evidence_query="stuck_detection pipeline_instance completed AND nudge written",
    ),
    65: CoverageItem(
        description="ProactiveAgent Area E: connection_making_step surfaces old "
        "vault notes relevant to a new topic.",
        status=CoverageStatus.ACTIVE,
        category="proactive_agent",
        evidence_query="connection_making pipeline_instance completed AND >=1 vault crossref nudge",
    ),
    66: CoverageItem(
        description="Reminders created via routines fire through the "
        "continuity_check pipeline at their scheduled time.",
        status=CoverageStatus.ACTIVE,
        category="life_management",
        evidence_query="reminders row + continuity_check pipeline_instance that fired it",
    ),
    67: CoverageItem(
        description="Wake-up briefing assembled overnight (anticipatory_prep / "
        "wake_up_preparation) and delivered at the user's wake time.",
        status=CoverageStatus.ACTIVE,
        category="life_management",
        evidence_query="wake_up_preparation pipeline_instance completed within WAKE_UP_WINDOW",
    ),

    # ── 100-series: capability-pack items (renumbered out of the
    #               24-26 collision they originally occupied; previous
    #               24/25/26 belonged to the Phase 9 capability work).
    100: CoverageItem(
        description="Capability pack surface — at least one of workspace.*, "
        "browser.*, vault.* tool calls appears in the report's "
        "capability bucket, OR the capability-health-check shows at "
        "least one pack UNCONFIGURED/DEGRADED with a remediation hint.",
        status=CoverageStatus.ACTIVE,
        category="capability_pack",
        evidence_query=(
            "any tool with prefix workspace./browser./vault. OR "
            "capability_health[pack].status in {unconfigured,degraded,unhealthy,unimplemented}"
        ),
    ),
    101: CoverageItem(
        description="Disclosed-failure path — when an MCP tool fails, the "
        "user-visible reply acknowledges the failure plainly (no "
        "silent fallback).",
        status=CoverageStatus.ACTIVE,
        category="capability_pack",
        evidence_query="assistant message after tool error contains 'unavailable'/'failed'/'MCP'",
    ),
    102: CoverageItem(
        description="Policy matrix enforcement — capability-health-check returns "
        "the 4 packs (workspace, browser, vault, doctor) and the policy "
        "section is present in the report.",
        status=CoverageStatus.ACTIVE,
        category="capability_pack",
        evidence_query="capability_health has >=4 entries AND policy section rendered",
    ),
}


# Convenience accessors
ACTIVE_ITEMS = {k: v for k, v in COVERAGE_ITEMS.items() if v.status == CoverageStatus.ACTIVE}
DEFERRED_ITEMS = {k: v for k, v in COVERAGE_ITEMS.items() if v.status == CoverageStatus.DEFERRED}


# ── Idle phase defaults ──────────────────────────────────────────────────────
# V2 idle-wait monitors both health and orchestration runtime state.
# Soak times are longer for phases that may have long-background pipelines
# running (post_long_background_idle, memory_steward_idle, vault_organizer_idle).
# --fast mode skips idle phases entirely.

IDLE_DEFAULTS = {
    "planning_idle":              {"min_soak": 15, "timeout": 30},
    "post_deep_idle":             {"min_soak": 15, "timeout": 30},
    "post_long_background_idle":  {"min_soak": 45, "timeout": 120},
    "post_revision_idle":         {"min_soak": 15, "timeout": 30},
    "memory_steward_idle":        {"min_soak": 30, "timeout": 90},
    "vault_organizer_idle":       {"min_soak": 30, "timeout": 90},
    "late_idle":                  {"min_soak": 15, "timeout": 30},
    "post_restart_idle":          {"min_soak": 15, "timeout": 30},
}

# ── Week plan ────────────────────────────────────────────────────────────────

WEEK_PLAN = {
    "day1": {
        "phases": [
            {
                "name": "first_launch",
                "type": "active",
                "description": "Jordan introduces herself, establishes context, plans the week",
                "goals": [
                    "Establish identity: name, ADHD, Alex, Mochi, Portland, job",
                    "Mention taking morning Adderall (triggers log_medication)",
                    "Introduce all 3 project tracks",
                    "Ask Kora to help plan the week",
                    "Test multi-turn memory within session",
                ],
                "coverage_items": [2, 3, 7, 24, 32],
                "notes": "Item #1 (first-run wizard) is DEFERRED -- no wizard in V2. "
                         "Jordan establishes context through natural conversation instead. "
                         "Medication mention should trigger life management tool. "
                         "Item 24 (state phase log) and 32 (conversation reserve) are "
                         "passively satisfied here.",
            },
            {
                "name": "planning_idle",
                "type": "idle",
                "min_soak_seconds": IDLE_DEFAULTS["planning_idle"]["min_soak"],
                "timeout_seconds": IDLE_DEFAULTS["planning_idle"]["timeout"],
                "description": "Health-check soak; orchestration enters ACTIVE_IDLE",
                "goals": [
                    "Verify daemon stays healthy",
                    "Verify SystemStatePhase moves CONVERSATION -> ACTIVE_IDLE",
                ],
                "coverage_items": [24],
            },
            {
                "name": "post_idle_return",
                "type": "active",
                "description": "Jordan returns, verifies continuity, starts focus session",
                "goals": [
                    "Test continuity after gap",
                    "Challenge vague responses",
                    "Ask Kora to start a focus block for deep work (triggers start_focus_block)",
                ],
                "coverage_items": [7],
            },
            {
                "name": "deep_work",
                "type": "active",
                "description": "Long architecture discussion with research and file ops",
                "goals": [
                    "Get deep technical engagement on dashboard",
                    "Ask Kora to research current productivity tools (triggers search_web via MCP)",
                    "Ask Kora to create a notes file or outline (triggers write_file / filesystem tools)",
                    "Test long-conversation quality",
                    "Push past compaction threshold (many exchanges)",
                    "Observe Kora adapting tone to Jordan's energy/focus state",
                ],
                "coverage_items": [4, 5, 6, 9, 10, 15, 19, 22, 45],
                "notes": "Research requests should trigger search_web MCP tool. "
                         "File creation requests trigger filesystem tools. "
                         "Emotion assessment should adapt to Jordan's excited-then-scattered arc. "
                         "Item 45 (WorkLedger auditability) is satisfied passively as "
                         "ledger rows accumulate from background pipelines.",
            },
            {
                "name": "subagent_delegation",
                "type": "active",
                "description": "Jordan asks for a multi-step task that decomposes into sub-tasks",
                "goals": [
                    "Ask for something that decompose_and_dispatch should split (in_turn=true)",
                    "Verify a pipeline_instance is created with multiple stages",
                    "Verify aggregated structured output returns to the supervisor",
                ],
                "coverage_items": [8],
                "notes": "Item 8 (un-deferred): the supervisor's decompose_and_dispatch "
                         "tool now stands in for the planner/reviewer subagents.",
            },
            {
                "name": "post_deep_idle",
                "type": "idle",
                "min_soak_seconds": IDLE_DEFAULTS["post_deep_idle"]["min_soak"],
                "timeout_seconds": IDLE_DEFAULTS["post_deep_idle"]["timeout"],
                "description": "Health-check soak after deep work; LIGHT_IDLE entered",
                "goals": [
                    "Verify daemon survived long conversation",
                    "Observe LIGHT_IDLE -> DEEP_IDLE transition in system_state_log",
                ],
                "coverage_items": [12, 24],
            },
            {
                "name": "evening_audit",
                "type": "active",
                "description": "End-of-day state check + memory recall + life management verify",
                "goals": [
                    "End the focus block (triggers end_focus_block)",
                    "Mention taking evening melatonin (triggers log_medication)",
                    "Review what was covered",
                    "Verify internal state via /status",
                    "Test memory recall of facts from Day 1",
                    "Query life management records to verify DB persistence",
                ],
                "coverage_items": [7, 16, 23],
            },
        ],
        "advance_hours": 14,
    },
    "day2": {
        "phases": [
            {
                "name": "morning_return",
                "type": "active",
                "description": "Jordan returns after 14h gap with life context",
                "goals": [
                    "Test recall of Day 1 context after gap",
                    "Verify memory persists across time advance",
                    "Mention taking morning Adderall (triggers log_medication)",
                    "Mention eating breakfast (triggers log_meal)",
                    "Observe emotional adaptation to 'focused morning' state",
                ],
                "life_context": "slept well, feeling focused. took my adderall already. had coffee and a bagel. "
                                "alex asked about dinner plans. afternoon might be short.",
                "coverage_items": [7, 16, 19],
            },
            {
                "name": "implementation_work",
                "type": "active",
                "description": "Push coding into implementation with file ops and research",
                "goals": [
                    "Concrete implementation planning",
                    "Ask Kora to look up a specific library/tool online (triggers search_web + fetch_url)",
                    "Ask Kora to create/read project files (triggers filesystem tools)",
                    "Writing outline",
                    "Verify skills activate appropriate tools for each track",
                ],
                "coverage_items": [4, 5, 6, 9, 20, 22],
            },
            {
                "name": "long_background_dispatch",
                "type": "active",
                "description": "Jordan asks for overnight rabbit-hole research; supervisor dispatches LONG_BACKGROUND",
                "goals": [
                    "Ask 'can you keep researching this in the background while I take a break?'",
                    "Verify decompose_and_dispatch fires with intent_duration='long'",
                    "Verify supervisor turn ends with templated ack (no LLM-generated reply)",
                    "Verify _KoraMemory/Inbox/{slug}.md appears with status: in_progress",
                    "Verify the working doc grows as the pipeline runs (adaptive task additions)",
                ],
                "coverage_items": [21, 25, 26, 27, 46],
                "notes": "This phase is the canonical Phase 7.5 long-running dispatch test. "
                         "Items 25, 26, 27, 46 land here.",
            },
            {
                "name": "mid_flight_inspection",
                "type": "active",
                "description": "Jordan checks task progress mid-flight, edits the working doc, observes inline continuity_check",
                "goals": [
                    "Ask 'how's that research coming?' (triggers get_task_progress)",
                    "Use harness command edit-working-doc to add a Current Plan item",
                    "Verify dispatcher picks up the edit and creates a new worker_task",
                    "Wait for continuity_check to fire mid-conversation (medication window)",
                ],
                "coverage_items": [29, 31, 38],
                "notes": "Item 38 (continuity_check inline) lands once the 8c handler is wired.",
            },
            {
                "name": "task_cancellation",
                "type": "active",
                "description": "Jordan cancels the long task mid-flight",
                "goals": [
                    "Ask 'actually, stop that research, it's not what I want'",
                    "Verify cancel_task is called",
                    "Verify partial results preserved in working doc",
                    "Verify TASK_CANCELLED event in work_ledger",
                ],
                "coverage_items": [30],
            },
            {
                "name": "post_long_background_idle",
                "type": "idle",
                "min_soak_seconds": IDLE_DEFAULTS["post_long_background_idle"]["min_soak"],
                "timeout_seconds": IDLE_DEFAULTS["post_long_background_idle"]["timeout"],
                "description": "Long-background runtime monitoring — track pipeline/task/checkpoint progress",
                "goals": [
                    "Verify long-running pipeline progresses through stages",
                    "Monitor request limiter usage by class",
                    "Track work_ledger row growth during soak",
                ],
                "coverage_items": [21, 33, 36],
            },
            {
                "name": "revision_wave",
                "type": "active",
                "description": "Jordan changes requirements across all 3 tracks + life context",
                "goals": [
                    "Coding: simplify to one-day view",
                    "Research: pivot to local-first/privacy focus",
                    "Writing: change audience to stakeholder/collaborator",
                    "Verify Kora actually replans vs just acknowledging",
                    "Mention not having eaten lunch (triggers log_meal or create_reminder)",
                    "Observe Kora detecting scattered/frustrated state -> EMOTION_SHIFT_DETECTED",
                    "Verify contextual_engagement nudge appears",
                ],
                "life_context": "feeling scattered, meds wearing off. did i eat lunch? i don't think i ate lunch. "
                                "ugh my focus is shot.",
                "coverage_items": [7, 11, 19, 41, 62],
            },
            {
                "name": "post_revision_idle",
                "type": "idle",
                "min_soak_seconds": IDLE_DEFAULTS["post_revision_idle"]["min_soak"],
                "timeout_seconds": IDLE_DEFAULTS["post_revision_idle"]["timeout"],
                "description": "Health-check soak after revision wave",
                "goals": [
                    "Verify daemon survived revision pressure",
                ],
            },
            {
                "name": "coordination_audit",
                "type": "active",
                "description": "Check cross-track coherence + life management records",
                "goals": [
                    "Ask for concise multi-project status",
                    "Probe for stale or contradictory answers",
                    "Query life management records (harness: life-management-check)",
                    "Verify medication, meal, and reminder records persisted correctly",
                    "Mention weighing a decision Jordan can't make yet (triggers record_decision)",
                ],
                "coverage_items": [23, 43],
            },
        ],
        "advance_hours": 14,
    },
    "day3": {
        "phases": [
            {
                "name": "v2_mechanical_tests",
                "type": "active",
                "description": "V2-specific mechanical verification",
                "goals": [
                    "Auth relay: disable auto-approve, trigger auth prompt, verify deny then approve",
                    "Error recovery: send malformed/empty input, verify graceful handling",
                    "Compaction: verify compaction metadata if not already detected",
                    "Force CONVERSATION reserve exhaustion -> templated fallback (item 34)",
                ],
                "coverage_items": [15, 17, 18, 34],
            },
            {
                "name": "skill_and_tool_audit",
                "type": "active",
                "description": "Verify skill activation gates tools correctly",
                "goals": [
                    "Ask about code work (should activate code_work skill -> filesystem tools)",
                    "Ask about meals/meds (should activate life_management skill -> life tools)",
                    "Ask to search something (should activate web_research skill -> search_web)",
                    "Verify tools visible in response match active skills",
                    "Take snapshot to inspect tool availability via inspect_tools",
                ],
                "coverage_items": [20, 100, 101, 102],
            },
            {
                "name": "merge_on_reengagement",
                "type": "active",
                "description": "Jordan starts a fresh session and Kora surfaces a completed-during-idle task",
                "goals": [
                    "Verify get_running_tasks(relevant_to_session=true) returns the task",
                    "Verify Kora's first turn surfaces the result without being asked",
                ],
                "coverage_items": [37],
            },
            {
                "name": "memory_steward_verification",
                "type": "idle",
                "min_soak_seconds": IDLE_DEFAULTS["memory_steward_idle"]["min_soak"],
                "timeout_seconds": IDLE_DEFAULTS["memory_steward_idle"]["timeout"],
                "description": "Memory Steward stages run during DEEP_IDLE; verify content correctness",
                "goals": [
                    "Wait for post_session_memory pipeline (extract -> consolidate -> dedup -> entities -> vault_handoff)",
                    "Verify domain-typed facts appear in the projection DB",
                    "Verify near-duplicates soft-deleted, fuzzy entities merged",
                    "Verify weekly_adhd_profile fired and User Model updated",
                    "Verify post_session_memory triggers post_memory_vault via sequence_complete",
                ],
                "coverage_items": [39, 47, 48, 49, 50, 51],
            },
            {
                "name": "vault_organizer_verification",
                "type": "idle",
                "min_soak_seconds": IDLE_DEFAULTS["vault_organizer_idle"]["min_soak"],
                "timeout_seconds": IDLE_DEFAULTS["vault_organizer_idle"]["timeout"],
                "description": "Vault Organizer stages run; verify vault layout, links, MOC pages",
                "goals": [
                    "Wait for post_memory_vault pipeline (reindex -> structure -> links -> moc_sessions)",
                    "Verify reindex picks up filesystem-edited notes",
                    "Verify Inbox files moved into folder hierarchy",
                    "Verify wikilinks injected without breaking frontmatter / code blocks",
                    "Verify entity pages + MOC pages + session index populated",
                ],
                "coverage_items": [52, 53, 54, 55, 56, 57],
            },
            {
                "name": "context_engine_insights",
                "type": "active",
                "description": "ContextEngine produces a cross-domain insight; proactive_pattern_scan consumes it",
                "goals": [
                    "Inject or wait for an INSIGHT_AVAILABLE event from ContextEngine",
                    "Verify proactive_pattern_scan fires and writes a nudge through NotificationGate",
                ],
                "coverage_items": [42, 58, 59],
            },
            {
                "name": "proactive_areas_b_and_e",
                "type": "active",
                "description": "Trigger ProactiveAgent areas B (anticipatory prep) and E (commitments / stuck / connections)",
                "goals": [
                    "Mention an upcoming event so anticipatory_prep has something to prep for",
                    "Make a commitment ('I'll send the doc tomorrow') so commitment_tracking has signal",
                    "Leave an unfinished task hanging so stuck_detection fires",
                    "Mention an old project so connection_making surfaces vault crossrefs",
                ],
                "coverage_items": [60, 63, 64, 65],
            },
            {
                "name": "proactive_research_kickoff",
                "type": "active",
                "description": "Trigger ProactiveAgent area C — rabbit-hole research with adaptive task list",
                "goals": [
                    "Ask Kora to research a topic deeply over the next idle period",
                    "Verify proactive_research_step dispatches and the working doc grows",
                    "Verify Kora judges done (status: done in frontmatter)",
                ],
                "coverage_items": [28, 61],
            },
            {
                "name": "final_changes",
                "type": "active",
                "description": "Final requirement changes + quick note capture + reminder creation",
                "goals": [
                    "Coding: add carryover-to-tomorrow + test confidence",
                    "Research: favor lowest-maintenance option",
                    "Writing: change to README/launch-note hybrid",
                    "Capture a quick note about tomorrow's priorities (triggers quick_note)",
                    "Create a routine that registers a runtime pipeline (item 44)",
                    "Create a reminder for morning standup (triggers create_reminder)",
                ],
                "coverage_items": [7, 23, 44, 66],
            },
            {
                "name": "restart_resilience",
                "type": "active",
                "description": "Restart daemon, verify continuity including life management + orchestration state",
                "goals": [
                    "Daemon restart",
                    "Verify Jordan / 3 tracks / revision history remembered",
                    "Verify any in-flight long pipeline resumed from latest checkpoint",
                    "Check state survived",
                    "Verify life management records survived restart (query DB)",
                    "Verify runtime_pipelines row from item 44 still fires post-restart",
                ],
                "coverage_items": [13, 23, 35, 44],
            },
            {
                "name": "late_idle",
                "type": "idle",
                "min_soak_seconds": IDLE_DEFAULTS["late_idle"]["min_soak"],
                "timeout_seconds": IDLE_DEFAULTS["late_idle"]["timeout"],
                "description": "Post-restart health-check soak; wake-up window approaches",
                "goals": [
                    "Verify daemon healthy after restart",
                    "Verify wake_up_preparation pipeline runs at WAKE_UP_WINDOW",
                    "Verify briefing artifact assembled and delivered",
                ],
                "coverage_items": [40, 67],
            },
            {
                "name": "weekly_review",
                "type": "active",
                "description": "Comprehensive weekly review covering all subsystems",
                "goals": [
                    "Cover all 3 tracks with specific accomplishments",
                    "Challenge vague claims",
                    "Cross-reference against actual observed behavior",
                    "Ask Kora to summarize life management activity (meds taken, meals logged, reminders)",
                    "Ask about long-background results (research, briefings)",
                    "Final snapshot for report comparison",
                ],
                "coverage_items": [14, 23],
            },
        ],
    },
}


# ── Fast mode plan ───────────────────────────────────────────────────────────
# Compressed single-day run (~10 min) that hits the critical V2 active items.
# No idle phases.  Invoked via: python3 -m tests.acceptance.automated start --fast

FAST_PLAN = {
    "day1": {
        "phases": [
            {
                "name": "establish_context",
                "type": "active",
                "description": "Establish Jordan's identity + 3 tracks + life management",
                "goals": [
                    "Introduce self, mention Adderall (triggers log_medication)",
                    "Introduce 3 project tracks",
                    "Plan the week",
                ],
                "coverage_items": [2, 3, 7, 24, 32],
            },
            {
                "name": "deep_exchange",
                "type": "active",
                "description": "Extended exchange with research, file ops, and compaction",
                "goals": [
                    "Deep technical engagement on all 3 tracks",
                    "Ask to search for tools online (triggers search_web or browser.open fallback)",
                    "Ask to check calendar or email (may trigger workspace capability)",
                    "Ask to create a notes file (triggers write_file)",
                    "Push toward compaction threshold",
                    "Observe emotional adaptation",
                ],
                "coverage_items": [4, 5, 6, 9, 10, 15, 19, 22, 100],
            },
            {
                "name": "long_background_dispatch",
                "type": "active",
                "description": "Dispatch a LONG_BACKGROUND task and verify templated ack",
                "goals": [
                    "Ask Kora to research something in the background",
                    "Verify decompose_and_dispatch fires with intent_duration='long'",
                    "Verify templated ack ends the turn",
                ],
                "coverage_items": [21, 25, 26, 46],
            },
            {
                "name": "recall_and_life",
                "type": "active",
                "description": "Verify memory recall + life management records",
                "goals": [
                    "Test recall of established facts",
                    "Mention a meal or snack (triggers log_meal)",
                    "Query life management records (harness: life-management-check)",
                ],
                "coverage_items": [7, 16, 23],
            },
            {
                "name": "revision_wave",
                "type": "active",
                "description": "Change all 3 tracks, verify replanning + skill activation",
                "goals": [
                    "Revise all 3 tracks",
                    "Verify skills gate tools appropriately",
                    "Observe scattered-state detection",
                ],
                "life_context": "feeling scattered, meds wearing off.",
                "coverage_items": [11, 19, 20],
            },
            {
                "name": "mechanical_tests",
                "type": "active",
                "description": "Auth relay + error recovery + restart",
                "coverage_items": [13, 17, 18],
            },
            {
                "name": "final_review",
                "type": "active",
                "description": "Quick review covering all subsystems",
                "goals": [
                    "Review all 3 tracks",
                    "Review life management activity",
                    "Capture a quick note for tomorrow (triggers quick_note)",
                ],
                "coverage_items": [7, 14, 23],
            },
        ],
    },
}
