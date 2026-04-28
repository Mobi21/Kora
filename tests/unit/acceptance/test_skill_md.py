"""SKILL.md guard tests for the Life OS acceptance operator."""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKILL_PATHS = (
    _REPO_ROOT / ".agents" / "skills" / "acceptance-test" / "SKILL.md",
    _REPO_ROOT / ".claude" / "skills" / "acceptance-test" / "SKILL.md",
)


@pytest.fixture(scope="module", params=_SKILL_PATHS)
def skill_text(request: pytest.FixtureRequest) -> str:
    path = request.param
    assert path.exists(), f"missing SKILL.md at {path}"
    return path.read_text()


def test_skill_md_centers_life_os_acceptance(skill_text: str) -> None:
    required = (
        "local-first Life OS",
        "internal calendar",
        "ADHD/executive-dysfunction support",
        "Autism/sensory support as a separate track",
        "Burnout/anxiety/low-energy support",
        "Wrong-inference recovery",
        "Trusted support boundaries",
        "Crisis boundaries",
        "Honest reporting",
    )
    for needle in required:
        assert needle in skill_text


def test_skill_md_demotes_old_capability_center(skill_text: str) -> None:
    assert "Coding, research, and writing can appear as optional capability checks" in skill_text
    assert "Old coding/research/writing checks are not Life OS gates" in skill_text
    assert "software engineer" not in skill_text
    assert "focus-week-dashboard" not in skill_text


def test_skill_md_mentions_runtime_truth_surfaces(skill_text: str) -> None:
    required = (
        "pipeline_instances",
        "worker_tasks",
        "work_ledger",
        "system_state_log",
        "notifications",
        "permission_grants",
        "open_decisions",
        "data/operational.db",
        "test_log.jsonl",
    )
    for needle in required:
        assert needle in skill_text


def test_skill_md_mentions_all_supervisor_tools(skill_text: str) -> None:
    tools = (
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
    for tool in tools:
        assert tool in skill_text


def test_skill_md_mentions_system_phases_and_harness_commands(skill_text: str) -> None:
    for phase in (
        "CONVERSATION",
        "ACTIVE_IDLE",
        "LIGHT_IDLE",
        "DEEP_IDLE",
        "WAKE_UP_WINDOW",
        "DND",
        "SLEEPING",
    ):
        assert phase in skill_text

    for command in (
        "orchestration-status",
        "pipeline-history",
        "working-docs",
        "notifications",
        "insights",
        "phase-history",
        "vault-snapshot",
        "soak-manifest",
        "phase-gate",
        "benchmarks",
        "event-tail",
        "life-management-check",
        "tool-usage-summary",
        "test-auth",
        "test-error",
        "skill-gating-check",
        "report",
    ):
        assert command in skill_text


def test_skill_md_keeps_autonomy_recipes_but_life_os_scoped(skill_text: str) -> None:
    for recipe in (
        "`IN_TURN`",
        "`BOUNDED_BACKGROUND`",
        "`LONG_BACKGROUND`",
        "Routine creation",
        "Reminder",
        "Adaptive research",
        "Mid-flight progress",
        "Cancellation",
        "Pose decision",
    ):
        assert recipe in skill_text
    assert "Appointment/admin/household prep over idle time" in skill_text


def test_skill_md_mentions_memory_vault_context_proactive_surfaces(skill_text: str) -> None:
    for needle in (
        "extract_step",
        "consolidate_step",
        "dedup_step",
        "entities_step",
        "vault_handoff_step",
        "reindex_step",
        "structure_step",
        "links_step",
        "moc_sessions_step",
        "ProactiveAgent Area A",
        "ProactiveAgent Area B",
        "ProactiveAgent Area C",
        "ProactiveAgent Area D",
        "ProactiveAgent Area E",
        "ContextEngine",
        "ReminderStore",
        "continuity_check",
    ):
        assert needle in skill_text


def test_skill_md_word_count_in_reasonable_range(skill_text: str) -> None:
    count = len(skill_text.split())
    assert 1800 <= count <= 5500
