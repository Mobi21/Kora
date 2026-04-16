"""SKILL.md guard tests (AT4).

Lock the rewritten ``acceptance-test`` skill against accidental drift:

* No stale references to retired surfaces
  (``start_autonomous`` / ``BackgroundWorker`` /
  ``autonomous_plans`` / ``autonomous_checkpoints``)
* Every post-Phase 8 subsystem must be named (OrchestrationEngine,
  Memory Steward 5 stages, Vault Organizer 4 stages, ContextEngine,
  ProactiveAgent Areas A-E, ReminderStore)
* All 11 supervisor tools, all 7 SystemStatePhase values, and the
  AT2/AT3 harness commands must appear at least once
* All 9 autonomy recipes must be enumerated
* Word count stays in the published 5000-9000 range so the file is
  comprehensive without becoming unreadable
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Anchor relative to the repo root: tests/unit/acceptance/test_skill_md.py
# parents[3] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKILL_PATH = (
    _REPO_ROOT / ".claude" / "skills" / "acceptance-test" / "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    assert _SKILL_PATH.exists(), f"missing SKILL.md at {_SKILL_PATH}"
    return _SKILL_PATH.read_text()


# ── Stale reference guards ──────────────────────────────────────────────

_STALE_TOKENS = (
    "start_autonomous",
    "BackgroundWorker",
    "autonomous_plans",
    "autonomous_checkpoints",
)


def test_skill_md_no_stale_references(skill_text: str) -> None:
    """Retired V2 surfaces must not appear in the rewritten skill."""
    for token in _STALE_TOKENS:
        assert token not in skill_text, (
            f"stale reference {token!r} present in SKILL.md — "
            "the post-Phase 7.5 / 8 rewrite must not name retired surfaces"
        )

    # Items 8 and 12 are explicitly un-deferred in Phase 7.5 / 8. The
    # skill must not mark either as DEFERRED.
    lowered = skill_text.lower()
    for needle in ("item 8 stays deferred", "item 12 stays deferred"):
        assert needle not in lowered, (
            f"SKILL.md still treats {needle!r} as deferred — "
            "items 8 and 12 are active after Phase 7.5 / 8"
        )

    # Stronger guard: scan the markdown coverage table for items 8 and 12
    # and assert the row body does NOT contain the word DEFERRED. This
    # catches a regression that puts DEFERRED in the cell rather than
    # using the sentence prose checked above.
    for item_num in (8, 12):
        row_match = re.search(rf"\|\s*{item_num}\s*\|(.+)", skill_text)
        if row_match:
            assert "DEFERRED" not in row_match.group(1).upper(), (
                f"Item {item_num} should not be marked DEFERRED in SKILL.md"
            )


# ── Subsystem coverage ──────────────────────────────────────────────────


def test_skill_md_mentions_orchestration_engine(skill_text: str) -> None:
    assert "OrchestrationEngine" in skill_text


_MEMORY_STEWARD_STAGES = (
    "extract_step",
    "consolidate_step",
    "dedup_step",
    "entities_step",
    "vault_handoff_step",
)


def test_skill_md_mentions_memory_steward_5_stages(skill_text: str) -> None:
    for stage in _MEMORY_STEWARD_STAGES:
        assert stage in skill_text, (
            f"Memory Steward stage {stage!r} missing from SKILL.md"
        )


_VAULT_ORGANIZER_STAGES = (
    "reindex_step",
    "structure_step",
    "links_step",
    "moc_sessions_step",
)


def test_skill_md_mentions_vault_organizer_4_stages(
    skill_text: str,
) -> None:
    for stage in _VAULT_ORGANIZER_STAGES:
        assert stage in skill_text, (
            f"Vault Organizer stage {stage!r} missing from SKILL.md"
        )


def test_skill_md_mentions_proactive_agent_areas_a_through_e(
    skill_text: str,
) -> None:
    for letter in ("A", "B", "C", "D", "E"):
        # Tolerate ``Area A`` with separator hyphen or em-dash.
        pattern = rf"Area {letter}\b"
        assert re.search(pattern, skill_text), (
            f"ProactiveAgent Area {letter} missing from SKILL.md"
        )


def test_skill_md_mentions_context_engine(skill_text: str) -> None:
    assert "ContextEngine" in skill_text
    # All 5 insight rules should appear by name.
    for rule in (
        "_rule_energy_calendar_mismatch",
        "_rule_medication_focus_correlation",
        "_rule_routine_adherence_trend",
        "_rule_emotional_pattern",
        "_rule_sleep_energy_correlation",
    ):
        assert rule in skill_text, (
            f"ContextEngine insight rule {rule!r} missing from SKILL.md"
        )


def test_skill_md_mentions_reminder_store(skill_text: str) -> None:
    assert "ReminderStore" in skill_text
    assert "continuity_check" in skill_text


# ── Supervisor tools ───────────────────────────────────────────────────

_SUPERVISOR_TOOLS = (
    "dispatch_worker",
    "recall",
    "search_web",
    "fetch_url",
    "decompose_and_dispatch",
    "get_running_tasks",
    "get_task_progress",
    "get_working_doc",
    "cancel_task",
    "modify_task",
    "record_decision",
)


def test_skill_md_mentions_all_11_supervisor_tools(skill_text: str) -> None:
    assert len(_SUPERVISOR_TOOLS) == 11
    for tool in _SUPERVISOR_TOOLS:
        assert tool in skill_text, (
            f"supervisor tool {tool!r} missing from SKILL.md"
        )


# ── SystemStatePhase values ────────────────────────────────────────────

_SYSTEM_PHASES = (
    "CONVERSATION",
    "ACTIVE_IDLE",
    "LIGHT_IDLE",
    "DEEP_IDLE",
    "WAKE_UP_WINDOW",
    "DND",
    "SLEEPING",
)


def test_skill_md_mentions_all_7_system_phases(skill_text: str) -> None:
    assert len(_SYSTEM_PHASES) == 7
    for phase in _SYSTEM_PHASES:
        # Must be the bare upper-case name; the lower-case ``deep_idle``
        # appears in manifest names so that doesn't count.
        assert re.search(rf"\b{phase}\b", skill_text), (
            f"SystemStatePhase {phase!r} missing from SKILL.md"
        )


# ── AT2 + AT3 harness commands ─────────────────────────────────────────

_AT_COMMANDS = (
    # AT2 commands (7)
    "orchestration-status",
    "pipeline-history",
    "working-docs",
    "notifications",
    "insights",
    "phase-history",
    "vault-snapshot",
    # AT3 commands (4)
    "soak-manifest",
    "phase-gate",
    "benchmarks",
    "event-tail",
)


def test_skill_md_mentions_all_at2_at3_commands(skill_text: str) -> None:
    assert len(_AT_COMMANDS) == 11
    for cmd in _AT_COMMANDS:
        assert cmd in skill_text, (
            f"harness command {cmd!r} missing from SKILL.md commands ref"
        )


# ── Nine autonomy recipes ──────────────────────────────────────────────


def test_skill_md_includes_autonomy_recipes(skill_text: str) -> None:
    """Every one of the 9 autonomy intents has to appear by name."""
    required = (
        "IN_TURN",
        "BOUNDED_BACKGROUND",
        "LONG_BACKGROUND",
        "Routine creation",
        "Reminder",
        "Adaptive research",
        "Mid-flight progress",
        "Cancellation",
        # Either "Pose decision" (rewrite wording) or "Open decision"
        # (legacy wording) is acceptable — both reference recipe #9.
    )
    for needle in required:
        assert needle in skill_text, (
            f"autonomy recipe label {needle!r} missing from SKILL.md"
        )
    assert ("Pose decision" in skill_text) or (
        "Open decision" in skill_text
    ), "9th autonomy recipe (decision) missing from SKILL.md"

    # The recipe table itself should be present and number all nine rows.
    # We grep for the literal "1 |" through "9 |" markdown table cells.
    for n in range(1, 10):
        assert re.search(rf"\|\s*{n}\s*\|", skill_text), (
            f"autonomy recipe row {n} missing from the recipe table"
        )


# ── Word count budget ──────────────────────────────────────────────────


def test_skill_md_word_count_in_range(skill_text: str) -> None:
    """5000 <= words <= 9000 keeps the skill comprehensive but readable."""
    words = skill_text.split()
    count = len(words)
    assert 5000 <= count <= 9000, (
        f"SKILL.md word count {count} outside published 5000-9000 range"
    )
