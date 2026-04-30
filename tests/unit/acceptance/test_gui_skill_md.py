"""Guard tests for the GUI-facing Life OS acceptance skill."""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGENTS_SKILL_DIR = _REPO_ROOT / ".agents" / "skills" / "acceptance-test-gui"
_CLAUDE_SKILL_DIR = _REPO_ROOT / ".claude" / "skills" / "acceptance-test-gui"
_SKILL_PATHS = (
    _AGENTS_SKILL_DIR / "SKILL.md",
    _CLAUDE_SKILL_DIR / "SKILL.md",
)


@pytest.fixture(scope="module", params=_SKILL_PATHS)
def skill_text(request: pytest.FixtureRequest) -> str:
    path = request.param
    assert path.exists(), f"missing SKILL.md at {path}"
    return path.read_text()


def test_gui_skill_md_centers_gui_runtime_reconciliation(skill_text: str) -> None:
    required = (
        "companion to `acceptance-test`, not a replacement",
        "GUI exposes the expected user-facing state",
        "Durable runtime evidence proves the claim",
        "global Kora chat",
        "Today / Plan Today",
        "Calendar",
        "Repair / Confirm Reality / Repair The Day",
        "Memory, Vault, Context packs, and provenance",
        "notification or proactive nudge surface",
    )
    for needle in required:
        assert needle in skill_text


def test_gui_skill_md_requires_browser_and_harness_evidence(skill_text: str) -> None:
    required = (
        "Browser Use",
        "computer-use",
        "screenshots before and after important actions",
        "console errors and failed network requests",
        "life-management-check",
        "orchestration-status",
        "working-docs",
        "notifications",
        "tool-usage-summary",
        "data/operational.db",
        "pipeline_instances",
        "worker_tasks",
        "work_ledger",
    )
    for needle in required:
        assert needle in skill_text


def test_gui_skill_md_keeps_life_os_support_tracks(skill_text: str) -> None:
    required = (
        "ADHD/executive dysfunction",
        "autism/sensory",
        "burnout/anxiety/low energy",
        "crisis boundary",
        "trusted support",
        "local-first/no-cloud",
        "one-day-at-a-time",
    )
    for needle in required:
        assert needle in skill_text


def test_gui_skill_references_and_scripts_exist_in_both_skill_copies() -> None:
    relative_paths = (
        "references/gui_operator_scenarios.md",
        "references/evidence_matrix.md",
        "scripts/start_gui_acceptance.py",
        "scripts/collect_gui_evidence.py",
        "scripts/reconcile_gui_report.py",
    )
    for base in (_AGENTS_SKILL_DIR, _CLAUDE_SKILL_DIR):
        for relative_path in relative_paths:
            assert (base / relative_path).exists(), f"missing {base / relative_path}"


def test_gui_skill_copies_are_synced() -> None:
    relative_paths = (
        "SKILL.md",
        "references/gui_operator_scenarios.md",
        "references/evidence_matrix.md",
        "scripts/start_gui_acceptance.py",
        "scripts/collect_gui_evidence.py",
        "scripts/reconcile_gui_report.py",
    )
    for relative_path in relative_paths:
        assert (_AGENTS_SKILL_DIR / relative_path).read_text() == (_CLAUDE_SKILL_DIR / relative_path).read_text()
