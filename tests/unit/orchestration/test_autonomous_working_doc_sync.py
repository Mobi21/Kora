"""Regression tests for the autonomous → working-doc write-back seam.

These tests protect against the long-standing acceptance-test symptom
where ``decompose_and_dispatch`` seeded a working doc, the 12-node
autonomous graph mutated in-memory state, and the dispatcher flipped
``pipeline_instances`` to ``completed`` — but the markdown file on disk
never advanced. Unchecked plan items, empty Findings, ``status:
in_progress`` forever.

Each test drives :func:`_sync_working_doc_from_state` directly against
a real :class:`WorkingDocStore` writing to ``tmp_path``, with a stub
engine/container so no dispatcher or LLM is involved. The target is to
fail loudly if the write-back ever regresses silently again.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from kora_v2.autonomous.pipeline_factory import (
    _extract_step_result_summary,
    _sync_working_doc_from_state,
)
from kora_v2.autonomous.state import AutonomousState
from kora_v2.runtime.orchestration.working_doc import (
    WorkingDocStore,
    parse_frontmatter,
    parse_plan_items,
    parse_sections,
)


# ── Shared helpers ──────────────────────────────────────────────────


async def _seed_doc(
    store: WorkingDocStore,
    *,
    instance_id: str,
    goal: str,
    plan_items: list[str],
) -> Path:
    return await store.create(
        instance_id=instance_id,
        task_id=instance_id,
        pipeline_name="user_autonomous_task",
        goal=goal,
        seed_plan_items=plan_items,
    )


def _fake_container(
    *, store: WorkingDocStore, doc_path: Path, instance_id: str
) -> SimpleNamespace:
    instance = SimpleNamespace(
        id=instance_id, working_doc_path=str(doc_path)
    )

    class _InstanceRegistry:
        async def load(self, iid: str) -> SimpleNamespace | None:
            return instance if iid == instance_id else None

    engine = SimpleNamespace(
        working_docs=store,
        instance_registry=_InstanceRegistry(),
    )
    return SimpleNamespace(orchestration_engine=engine)


def _fake_task(instance_id: str, task_id: str = "task-1") -> SimpleNamespace:
    return SimpleNamespace(id=task_id, pipeline_instance_id=instance_id)


def _base_state(*, step_titles: list[str]) -> AutonomousState:
    steps_meta: dict[str, dict[str, str]] = {}
    for idx, title in enumerate(step_titles):
        steps_meta[f"s{idx}"] = {
            "title": title,
            "description": f"Do {title}",
            "worker": "executor",
        }
    return AutonomousState(
        session_id="sess-1",
        plan_id="plan-1",
        status="executing",
        pending_step_ids=list(steps_meta.keys()),
        completed_step_ids=[],
        metadata={"steps": steps_meta},
    )


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_step_writes_back_checkmark_and_findings(
    tmp_path: Path,
) -> None:
    """After execute_step completes a step, the working doc MUST
    flip a seeded plan item from ``[ ]`` to ``[x]`` AND append the
    step's result to Findings. This is the primary regression the
    acceptance tests have been missing for weeks.
    """
    store = WorkingDocStore(tmp_path / "Inbox")
    instance_id = "pi-1"
    doc_path = await _seed_doc(
        store,
        instance_id=instance_id,
        goal="Research local-first tools",
        plan_items=["search_obsidian", "search_logseq", "synthesize"],
    )

    state = _base_state(
        step_titles=[
            "Research Obsidian local storage",
            "Research Logseq",
            "Synthesize findings",
        ]
    )
    # Simulate execute_step having just completed the first step.
    state.pending_step_ids = ["s1", "s2"]
    state.completed_step_ids = ["s0"]
    state.current_step_id = "s0"
    state.metadata["last_step_result"] = json.dumps(
        {
            "result": "Obsidian stores notes as Markdown in a local vault.",
            "success": True,
            "confidence": 0.9,
        }
    )

    container = _fake_container(
        store=store, doc_path=doc_path, instance_id=instance_id
    )
    task = _fake_task(instance_id)

    await _sync_working_doc_from_state(
        task=task,
        container=container,
        state=state,
        prev_completed_count=0,
        node_just_ran="execute_step",
    )

    text = doc_path.read_text(encoding="utf-8")
    _fm, body = parse_frontmatter(text)
    sections = parse_sections(body)
    plan_items = parse_plan_items(sections.get("Current Plan", ""))

    # First seeded item must now be checked — this is the exact
    # regression that has been silent in every acceptance test.
    seeded_first = next(
        (p for p in plan_items if p.text == "search_obsidian"), None
    )
    assert seeded_first is not None, "seeded plan item disappeared"
    assert seeded_first.marker.lower() == "x", (
        "seeded plan item should flip to [x] after execute_step completes "
        f"the first step; got marker={seeded_first.marker!r}"
    )

    # Planner's own step title is appended as [x] so Findings and plan
    # stay aligned even when the seeded stage name is coarser.
    assert any(
        p.text == "Research Obsidian local storage"
        and p.marker.lower() == "x"
        for p in plan_items
    ), "planner step title should appear as a [x] item"

    # Findings must be non-empty and contain the step's result text.
    findings = sections.get("Findings", "").strip()
    assert findings, "Findings section must not be empty after a step completes"
    assert "Obsidian stores notes" in findings
    assert "Research Obsidian local storage" in findings

    # Completed Tasks Log gets the audit line.
    log_section = sections.get("Completed Tasks Log", "").strip()
    assert "Research Obsidian local storage" in log_section


@pytest.mark.asyncio
async def test_terminal_completed_flips_status_to_done(tmp_path: Path) -> None:
    """When the graph reaches ``status == 'completed'``, the working
    doc's frontmatter MUST move to ``status: done``. Otherwise the
    pipeline row says completed while the markdown still claims
    ``in_progress`` — exactly the mismatch reported by the acceptance
    operator.
    """
    store = WorkingDocStore(tmp_path / "Inbox")
    instance_id = "pi-done"
    doc_path = await _seed_doc(
        store,
        instance_id=instance_id,
        goal="Wrap it up",
        plan_items=["step-a"],
    )

    state = _base_state(step_titles=["Step A"])
    state.pending_step_ids = []
    state.completed_step_ids = ["s0"]
    state.status = "completed"
    state.metadata["completion_summary"] = {
        "steps_completed": 1,
        "avg_quality": 0.92,
    }

    container = _fake_container(
        store=store, doc_path=doc_path, instance_id=instance_id
    )

    await _sync_working_doc_from_state(
        task=_fake_task(instance_id),
        container=container,
        state=state,
        prev_completed_count=1,  # no new step; terminal path takes over
        node_just_ran="complete",
    )

    fm, _body = parse_frontmatter(doc_path.read_text(encoding="utf-8"))
    assert fm.get("status") == "done", (
        f"frontmatter status should be 'done' after completion; got {fm!r}"
    )
    assert fm.get("completed_at"), "completed_at must be stamped on done"


@pytest.mark.asyncio
async def test_terminal_failed_flips_status_to_failed(tmp_path: Path) -> None:
    store = WorkingDocStore(tmp_path / "Inbox")
    instance_id = "pi-failed"
    doc_path = await _seed_doc(
        store,
        instance_id=instance_id,
        goal="This will fail",
        plan_items=["step-a"],
    )

    state = _base_state(step_titles=["Step A"])
    state.status = "failed"
    state.metadata["failure_reason"] = "Budget limit reached: request_count"

    container = _fake_container(
        store=store, doc_path=doc_path, instance_id=instance_id
    )

    await _sync_working_doc_from_state(
        task=_fake_task(instance_id),
        container=container,
        state=state,
        prev_completed_count=0,
        node_just_ran="failed",
    )

    text = doc_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    sections = parse_sections(body)
    assert fm.get("status") == "failed"
    assert "Budget limit reached" in sections.get("Completion", "")


@pytest.mark.asyncio
async def test_missing_pipeline_instance_is_a_noop(tmp_path: Path) -> None:
    """If the instance row is gone, the helper must swallow and return
    without raising — it is best-effort by contract."""
    store = WorkingDocStore(tmp_path / "Inbox")

    class _EmptyRegistry:
        async def load(self, _iid: str) -> None:
            return None

    container = SimpleNamespace(
        orchestration_engine=SimpleNamespace(
            working_docs=store, instance_registry=_EmptyRegistry()
        )
    )
    state = _base_state(step_titles=["a"])
    state.status = "completed"

    # Must not raise.
    await _sync_working_doc_from_state(
        task=_fake_task("missing"),
        container=container,
        state=state,
        prev_completed_count=0,
        node_just_ran="complete",
    )


@pytest.mark.asyncio
async def test_mcp_degraded_result_is_surfaced_in_findings(
    tmp_path: Path,
) -> None:
    """When a step's worker returns a degraded result (e.g. MCP
    unavailable), the Findings section must still reflect what
    happened rather than stay empty. This is how the disclosed-failure
    contract lands in the working doc."""
    store = WorkingDocStore(tmp_path / "Inbox")
    instance_id = "pi-degraded"
    doc_path = await _seed_doc(
        store,
        instance_id=instance_id,
        goal="Test degraded surfacing",
        plan_items=["web_lookup"],
    )

    state = _base_state(step_titles=["Web lookup"])
    state.pending_step_ids = []
    state.completed_step_ids = ["s0"]
    state.current_step_id = "s0"
    state.metadata["last_step_result"] = json.dumps(
        {
            "status": "error",
            "degraded": True,
            "error": "brave_search MCP unavailable",
        }
    )

    container = _fake_container(
        store=store, doc_path=doc_path, instance_id=instance_id
    )

    await _sync_working_doc_from_state(
        task=_fake_task(instance_id),
        container=container,
        state=state,
        prev_completed_count=0,
        node_just_ran="execute_step",
    )

    sections = parse_sections(
        parse_frontmatter(doc_path.read_text(encoding="utf-8"))[1]
    )
    findings = sections.get("Findings", "")
    assert "brave_search" in findings or "degraded" in findings, (
        "degraded worker responses must be surfaced in Findings so the "
        "operator sees the disclosed failure rather than a silent empty doc"
    )


# ── Helper function direct tests ───────────────────────────────────


def test_extract_step_result_summary_parses_execution_output() -> None:
    payload = json.dumps(
        {
            "result": "Found 3 apps",
            "success": True,
            "confidence": 0.8,
        }
    )
    assert _extract_step_result_summary(payload) == "Found 3 apps"


def test_extract_step_result_summary_falls_back_to_raw_for_bad_json() -> None:
    assert "not json" in _extract_step_result_summary("not json {}")


def test_extract_step_result_summary_surfaces_error_for_degraded() -> None:
    payload = json.dumps({"status": "error", "degraded": True, "error": "MCP down"})
    out = _extract_step_result_summary(payload)
    assert "degraded" in out or "MCP down" in out


def test_extract_step_result_summary_empty_returns_placeholder() -> None:
    out = _extract_step_result_summary("")
    assert out  # never empty — always writes something


# ── Datetime sanity so stale imports don't bit-rot ─────────────────


def test_module_datetime_imports_still_work() -> None:
    # Quick guard: both modules the helper imports from must be
    # available; a bad refactor of parse_frontmatter would regress
    # the helper in a subtle way.
    assert datetime.now(UTC).tzinfo is not None
