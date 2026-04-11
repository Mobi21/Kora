"""Week scenario plan for Jordan's 3-day acceptance test.

Each phase has: name, type (active/idle), description, goals, and
minimum soak time for idle phases.

Coverage items are tagged as ACTIVE (testable against V2 now) or
DEFERRED (requires V2 features not yet implemented).
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class CoverageStatus(Enum):
    """Whether a coverage item can be tested against the current V2 runtime."""
    ACTIVE = "active"
    DEFERRED = "deferred"


class CoverageItem(NamedTuple):
    description: str
    status: CoverageStatus
    deferred_reason: str | None = None


# ── Coverage checklist ───────────────────────────────────────────────────────

COVERAGE_ITEMS: dict[int, CoverageItem] = {
    # --- Active: testable against V2 now ---
    2: CoverageItem(
        "Jordan's personal context established (name, ADHD, Alex, Mochi, meds, job)",
        CoverageStatus.ACTIVE,
    ),
    3: CoverageItem(
        "Week planning with concrete tasks across all 3 tracks",
        CoverageStatus.ACTIVE,
    ),
    4: CoverageItem(
        "Coding track: planning -> implementation -> revision",
        CoverageStatus.ACTIVE,
    ),
    5: CoverageItem(
        "Research track: kickoff -> evidence gathering -> synthesis",
        CoverageStatus.ACTIVE,
    ),
    6: CoverageItem(
        "Writing track: outline -> draft -> revision",
        CoverageStatus.ACTIVE,
    ),
    7: CoverageItem(
        "Life management tools used (log_medication, log_meal, create_reminder, quick_note, focus blocks)",
        CoverageStatus.ACTIVE,
    ),
    9: CoverageItem(
        "Web research via capability or legacy tool — either a successful MCP-backed search/fetch "
        "completes, or an explicit MCP failure is surfaced and the model chooses a next step "
        "(e.g., `browser.open`) without silent fallback.",
        CoverageStatus.ACTIVE,
    ),
    10: CoverageItem(
        "Long-context compaction pressure survived",
        CoverageStatus.ACTIVE,
    ),
    11: CoverageItem(
        "Revision wave absorbed across all 3 tracks",
        CoverageStatus.ACTIVE,
    ),
    13: CoverageItem(
        "Restart resilience (daemon restart, continuity verified)",
        CoverageStatus.ACTIVE,
    ),
    14: CoverageItem(
        "Weekly review matches actual 3-day run",
        CoverageStatus.ACTIVE,
    ),

    # --- V2-specific items ---
    15: CoverageItem(
        "Compaction detected via response metadata (token count, tier change)",
        CoverageStatus.ACTIVE,
    ),
    16: CoverageItem(
        "Memory recall returns facts established earlier in conversation",
        CoverageStatus.ACTIVE,
    ),
    17: CoverageItem(
        "Auth relay round-trip (deny once, then approve, verify both paths)",
        CoverageStatus.ACTIVE,
    ),
    18: CoverageItem(
        "Error recovery: malformed input handled gracefully, session survives",
        CoverageStatus.ACTIVE,
    ),
    19: CoverageItem(
        "Emotion/energy assessment adapts response tone to Jordan's state",
        CoverageStatus.ACTIVE,
    ),
    20: CoverageItem(
        "Skill activation gates tools (life_management, code_work, web_research visible when needed)",
        CoverageStatus.ACTIVE,
    ),
    21: CoverageItem(
        "Autonomous execution: start_autonomous dispatches, plans, checkpoints, completes background work",
        CoverageStatus.ACTIVE,
    ),
    22: CoverageItem(
        "File operations via filesystem tools (read_file, write_file, list_directory)",
        CoverageStatus.ACTIVE,
    ),
    23: CoverageItem(
        "Life management DB records persist (medication_log, meal_log, reminders queryable after creation)",
        CoverageStatus.ACTIVE,
    ),

    # --- Phase 9 capability-pack items ---
    24: CoverageItem(
        "Capability pack surface — at least one of `workspace.*`, `browser.*`, or `vault.*` tool calls "
        "appears in the report's capability_* bucket, OR the capability-health-check shows at least "
        "one pack is UNCONFIGURED/DEGRADED with a remediation hint.",
        CoverageStatus.ACTIVE,
    ),
    25: CoverageItem(
        "Disclosed-failure path — if any MCP tool fails during the run, the assistant's user-visible "
        "reply acknowledges the failure plainly (grep for 'MCP' or 'unavailable' or 'failed' in an "
        "assistant message following a tool error event).",
        CoverageStatus.ACTIVE,
    ),
    26: CoverageItem(
        "Policy matrix enforcement — the harness's capability-health-check command returns 4 packs "
        "and the policy section of the report is present.",
        CoverageStatus.ACTIVE,
    ),

    # --- Deferred: requires V2 features not yet implemented ---
    1: CoverageItem(
        "First-run onboarding completes naturally",
        CoverageStatus.DEFERRED,
        "V2 has no first-run wizard yet",
    ),
    8: CoverageItem(
        "Natural subagent use via dispatch_worker (planner/reviewer harnesses)",
        CoverageStatus.DEFERRED,
        "executor harness exists but planner/reviewer .py files are missing",
    ),
    12: CoverageItem(
        "Monitored idle with grounded follow-through",
        CoverageStatus.DEFERRED,
        "V2 BackgroundWorker has zero registered work items",
    ),
}

# Convenience accessors
ACTIVE_ITEMS = {k: v for k, v in COVERAGE_ITEMS.items() if v.status == CoverageStatus.ACTIVE}
DEFERRED_ITEMS = {k: v for k, v in COVERAGE_ITEMS.items() if v.status == CoverageStatus.DEFERRED}

# ── Idle phase defaults ──────────────────────────────────────────────────────
# V2 idle-wait monitors both health and autonomous runtime state.
# Soak times are longer for phases that may have autonomous background work
# running (post_autonomous_idle).  --fast mode skips idle phases entirely.

IDLE_DEFAULTS = {
    "planning_idle":        {"min_soak": 15, "timeout": 30},
    "post_deep_idle":       {"min_soak": 15, "timeout": 30},
    "post_autonomous_idle": {"min_soak": 45, "timeout": 120},
    "post_revision_idle":   {"min_soak": 15, "timeout": 30},
    "late_idle":            {"min_soak": 15, "timeout": 30},
    "post_restart_idle":    {"min_soak": 15, "timeout": 30},
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
                "coverage_items": [2, 3, 7],
                "notes": "Item #1 (first-run wizard) is DEFERRED -- no wizard in V2. "
                         "Jordan establishes context through natural conversation instead. "
                         "Medication mention should trigger life management tool.",
            },
            {
                "name": "planning_idle",
                "type": "idle",
                "min_soak_seconds": IDLE_DEFAULTS["planning_idle"]["min_soak"],
                "timeout_seconds": IDLE_DEFAULTS["planning_idle"]["timeout"],
                "description": "Health-check soak",
                "goals": [
                    "Verify daemon stays healthy",
                    "Check session state is stable",
                ],
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
                "coverage_items": [4, 5, 6, 9, 10, 15, 19, 22],
                "notes": "Research requests should trigger search_web MCP tool. "
                         "File creation requests trigger filesystem tools. "
                         "Emotion assessment should adapt to Jordan's excited-then-scattered arc.",
            },
            {
                "name": "post_deep_idle",
                "type": "idle",
                "min_soak_seconds": IDLE_DEFAULTS["post_deep_idle"]["min_soak"],
                "timeout_seconds": IDLE_DEFAULTS["post_deep_idle"]["timeout"],
                "description": "Health-check soak after deep work",
                "goals": [
                    "Verify daemon survived long conversation",
                ],
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
                "name": "autonomous_kickoff",
                "type": "active",
                "description": "Jordan asks Kora to work on research in the background",
                "goals": [
                    "Ask Kora to do deep research autonomously (triggers start_autonomous)",
                    "Verify autonomous loop starts and creates a plan",
                    "Take a snapshot before idle to track autonomous progress",
                ],
                "coverage_items": [21],
                "notes": "Jordan says something like 'can you keep researching this in the background "
                         "while I take a break?' This should trigger start_autonomous.",
            },
            {
                "name": "post_autonomous_idle",
                "type": "idle",
                "min_soak_seconds": IDLE_DEFAULTS["post_autonomous_idle"]["min_soak"],
                "timeout_seconds": IDLE_DEFAULTS["post_autonomous_idle"]["timeout"],
                "description": "Autonomous runtime monitoring — track plan/item/checkpoint progress",
                "goals": [
                    "Verify autonomous loop creates items and checkpoints",
                    "Monitor budget consumption",
                    "Track items_delta and checkpoints_delta during soak",
                ],
                "coverage_items": [21],
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
                    "Observe Kora detecting scattered/frustrated state",
                ],
                "life_context": "feeling scattered, meds wearing off. did i eat lunch? i don't think i ate lunch. "
                                "ugh my focus is shot.",
                "coverage_items": [7, 11, 19],
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
                ],
                "coverage_items": [23],
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
                ],
                "coverage_items": [17, 18, 15],
            },
            {
                "name": "skill_and_tool_audit",
                "type": "active",
                "description": "Verify skill activation gates tools correctly",
                "goals": [
                    "Ask about code work (should activate code_work skill → filesystem tools)",
                    "Ask about meals/meds (should activate life_management skill → life tools)",
                    "Ask to search something (should activate web_research skill → search_web)",
                    "Verify tools visible in response match active skills",
                    "Take snapshot to inspect tool availability via inspect_tools",
                ],
                "coverage_items": [20],
            },
            {
                "name": "final_changes",
                "type": "active",
                "description": "Final requirement changes + quick note capture",
                "goals": [
                    "Coding: add carryover-to-tomorrow + test confidence",
                    "Research: favor lowest-maintenance option",
                    "Writing: change to README/launch-note hybrid",
                    "Capture a quick note about tomorrow's priorities (triggers quick_note)",
                    "Create a reminder for morning standup (triggers create_reminder)",
                ],
                "coverage_items": [7, 23],
                "notes": "Item #8 (planner/reviewer subagents) is DEFERRED -- executor exists but "
                         "planner/reviewer harness files are missing.",
            },
            {
                "name": "restart_resilience",
                "type": "active",
                "description": "Restart daemon, verify continuity including life management data",
                "goals": [
                    "Daemon restart",
                    "Verify Jordan / 3 tracks / revision history remembered",
                    "Check state survived",
                    "Verify life management records survived restart (query DB)",
                ],
                "coverage_items": [13, 23],
            },
            {
                "name": "late_idle",
                "type": "idle",
                "min_soak_seconds": IDLE_DEFAULTS["late_idle"]["min_soak"],
                "timeout_seconds": IDLE_DEFAULTS["late_idle"]["timeout"],
                "description": "Post-restart health-check soak",
                "goals": [
                    "Verify daemon healthy after restart",
                ],
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
                    "Ask about autonomous background work results",
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
                "coverage_items": [2, 3, 7],
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
                "coverage_items": [4, 5, 6, 9, 10, 15, 19, 22, 24],
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
                "coverage_items": [17, 18, 13],
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
