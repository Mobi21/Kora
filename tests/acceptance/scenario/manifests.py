"""Idle-soak manifests (AT3).

Each :class:`SoakManifest` declares the *observable effects* of a named
idle phase: pipelines that should fire, ledger events that should be
recorded, SystemStatePhase transitions that should appear, and thresholds
on memory / vault / notification state growth during the soak.

The runner compares two full-state snapshots (``before`` / ``after``)
against the manifest and returns a :class:`SoakResult` with per-check
pass/fail plus a human-readable summary. Evaluation is pure data —
**no LLM calls**, no network, no daemon probing. Same inputs produce
the same output so the manifest check stays reproducible.

The manifest names line up with the idle-phase names in
``WEEK_PLAN`` / ``IDLE_DEFAULTS`` — callers invoke
``run_manifest(SOAK_MANIFESTS[phase_name], before, after)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Manifest definition ──────────────────────────────────────────────────

@dataclass
class SoakManifest:
    """Expected observable effects of a named idle phase."""

    phase_name: str
    min_soak_seconds: int
    timeout_seconds: int
    #: Pipeline names that should fire during the soak (present in
    #: ``orchestration_state.pipeline_instances.by_name`` with a positive
    #: delta from ``before`` to ``after``).
    expected_pipelines: list[str] = field(default_factory=list)
    #: work_ledger ``event_type`` strings that should appear or grow.
    expected_ledger_events: list[str] = field(default_factory=list)
    #: :class:`SystemStatePhase` values (lower-case string form) that
    #: should appear in ``system_state_log.by_phase`` with a positive
    #: delta.
    expected_phase_transitions: list[str] = field(default_factory=list)
    #: Minimum number of *new* working docs (path-set delta) that should
    #: appear under ``vault_state.working_docs`` between before/after.
    expected_working_docs_min: int = 0
    #: Minimum number of *new* active memories (projection.db
    #: ``memories`` total delta, not status-filtered).
    expected_memories_min: int = 0
    #: Minimum number of new notifications.
    expected_notifications_min: int = 0
    #: Manifest items that are allowed to be absent without failing the
    #: check — useful for environments that don't have certain
    #: sub-systems wired up yet. Values match the strings stored in
    #: ``expected_pipelines`` / ``expected_ledger_events`` /
    #: ``expected_phase_transitions``.
    tolerate_missing: list[str] = field(default_factory=list)


# ── Registry ─────────────────────────────────────────────────────────────
# Names line up with the idle-phase names in
# ``tests/acceptance/scenario/week_plan.py`` (``IDLE_DEFAULTS`` + every
# ``phase.name`` with ``type == "idle"``).

SOAK_MANIFESTS: dict[str, SoakManifest] = {
    "post_session_memory_soak": SoakManifest(
        phase_name="post_session_memory_soak",
        min_soak_seconds=30,
        timeout_seconds=90,
        expected_pipelines=["post_session_memory", "post_memory_vault"],
        expected_ledger_events=["pipeline_started", "task_completed"],
        expected_phase_transitions=["active_idle", "light_idle"],
        expected_memories_min=1,
    ),
    "deep_idle_soak": SoakManifest(
        phase_name="deep_idle_soak",
        min_soak_seconds=45,
        timeout_seconds=120,
        expected_pipelines=["session_bridge_pruning"],
        expected_ledger_events=["trigger_fired"],
        expected_phase_transitions=["deep_idle"],
        tolerate_missing=["session_bridge_pruning"],
    ),
    "long_background_soak": SoakManifest(
        phase_name="long_background_soak",
        min_soak_seconds=60,
        timeout_seconds=300,
        expected_pipelines=["user_autonomous_task"],
        expected_ledger_events=["worker_dispatched", "task_checkpointed"],
        expected_working_docs_min=1,
    ),
    # Week-plan idle phase aliases — same names used in WEEK_PLAN /
    # IDLE_DEFAULTS so callers can `SOAK_MANIFESTS[phase_name]` directly
    # off the phase record.
    "planning_idle": SoakManifest(
        phase_name="planning_idle",
        min_soak_seconds=15,
        timeout_seconds=30,
        expected_phase_transitions=["active_idle"],
        tolerate_missing=["active_idle"],
    ),
    "post_deep_idle": SoakManifest(
        phase_name="post_deep_idle",
        min_soak_seconds=15,
        timeout_seconds=30,
        expected_phase_transitions=["light_idle", "deep_idle"],
        tolerate_missing=["light_idle", "deep_idle"],
    ),
    "post_long_background_idle": SoakManifest(
        phase_name="post_long_background_idle",
        min_soak_seconds=45,
        timeout_seconds=120,
        expected_pipelines=["user_autonomous_task"],
        expected_ledger_events=["task_checkpointed"],
        expected_working_docs_min=1,
        tolerate_missing=["user_autonomous_task", "task_checkpointed"],
    ),
    "post_revision_idle": SoakManifest(
        phase_name="post_revision_idle",
        min_soak_seconds=15,
        timeout_seconds=30,
    ),
    "memory_steward_idle": SoakManifest(
        phase_name="memory_steward_idle",
        min_soak_seconds=30,
        timeout_seconds=90,
        expected_pipelines=["post_session_memory"],
        expected_ledger_events=["pipeline_started"],
        expected_memories_min=1,
        tolerate_missing=["post_session_memory"],
    ),
    "vault_organizer_idle": SoakManifest(
        phase_name="vault_organizer_idle",
        min_soak_seconds=30,
        timeout_seconds=90,
        expected_pipelines=["post_memory_vault"],
        expected_ledger_events=["pipeline_started"],
        tolerate_missing=["post_memory_vault"],
    ),
    "late_idle": SoakManifest(
        phase_name="late_idle",
        min_soak_seconds=15,
        timeout_seconds=30,
        expected_pipelines=["wake_up_preparation"],
        expected_phase_transitions=["wake_up_window"],
        tolerate_missing=["wake_up_preparation", "wake_up_window"],
    ),
    "post_restart_idle": SoakManifest(
        phase_name="post_restart_idle",
        min_soak_seconds=15,
        timeout_seconds=30,
    ),
}


# ── Result type ──────────────────────────────────────────────────────────

@dataclass
class SoakResult:
    """Outcome of a single manifest evaluation."""

    manifest_name: str
    passed: bool
    #: Per-check boolean outcome, keyed by a stable string id:
    #: ``pipeline:<name>``, ``ledger:<event>``, ``phase:<phase>``,
    #: ``working_docs_min``, ``memories_min``, ``notifications_min``.
    checks: dict[str, bool] = field(default_factory=dict)
    #: Keys from ``checks`` that failed and were *not* in
    #: ``tolerate_missing`` — these caused the manifest to fail.
    missing: list[str] = field(default_factory=list)
    #: State items that were present but not declared in the manifest
    #: (new pipeline names / ledger events / phase transitions that
    #: fired during the soak). Informational — does not fail the check.
    unexpected: list[str] = field(default_factory=list)
    summary: str = ""


# ── Evaluation ───────────────────────────────────────────────────────────

def _pipeline_counts(state: dict[str, Any]) -> dict[str, int]:
    """Extract ``{pipeline_name: count}`` from a full-state snapshot."""
    orch = state.get("orchestration_state") or {}
    pi = orch.get("pipeline_instances") or {}
    by_name = pi.get("by_name") or {}
    if not isinstance(by_name, dict):
        return {}
    return {str(k): int(v or 0) for k, v in by_name.items()}


def _ledger_event_counts(state: dict[str, Any]) -> dict[str, int]:
    """Extract ``{event_type_lower: count}`` from a full-state snapshot."""
    orch = state.get("orchestration_state") or {}
    wl = orch.get("work_ledger") or {}
    by_event = wl.get("by_event_type") or {}
    if not isinstance(by_event, dict):
        return {}
    return {str(k).lower(): int(v or 0) for k, v in by_event.items()}


def _phase_counts(state: dict[str, Any]) -> dict[str, int]:
    """Extract ``{phase_lower: count}`` from a full-state snapshot."""
    orch = state.get("orchestration_state") or {}
    ssl = orch.get("system_state_log") or {}
    by_phase = ssl.get("by_phase") or {}
    if not isinstance(by_phase, dict):
        return {}
    return {str(k).lower(): int(v or 0) for k, v in by_phase.items()}


def _working_doc_paths(state: dict[str, Any]) -> set[str]:
    vault = state.get("vault_state") or {}
    wds = vault.get("working_docs") or []
    out: set[str] = set()
    for wd in wds:
        if isinstance(wd, dict):
            p = wd.get("path")
            if p:
                out.add(str(p))
    return out


def _memory_total(state: dict[str, Any]) -> int:
    mem = state.get("memory_lifecycle") or {}
    memories = mem.get("memories") or {}
    if isinstance(memories, dict) and not memories.get("error"):
        return int(memories.get("total") or 0)
    return 0


def _notification_total(state: dict[str, Any]) -> int:
    pro = state.get("proactive_state") or {}
    notifs = pro.get("notifications") or {}
    if isinstance(notifs, dict) and not notifs.get("error"):
        return int(notifs.get("total") or 0)
    return 0


def run_manifest(
    manifest: SoakManifest,
    before_state: dict[str, Any],
    after_state: dict[str, Any],
) -> SoakResult:
    """Evaluate ``manifest`` against two full-state snapshots.

    ``before_state`` / ``after_state`` must both follow the
    :meth:`HarnessServer._snapshot_full_state` contract (dict with
    ``orchestration_state`` / ``memory_lifecycle`` / ``vault_state`` /
    ``proactive_state`` keys). Missing subsystems are tolerated — the
    relevant checks simply fail (or pass, for zero-threshold checks).

    The result is deterministic: given the same inputs, the output
    object compares equal field-for-field.
    """

    checks: dict[str, bool] = {}

    # ── Pipelines ──
    pipes_before = _pipeline_counts(before_state)
    pipes_after = _pipeline_counts(after_state)
    for name in manifest.expected_pipelines:
        delta = pipes_after.get(name, 0) - pipes_before.get(name, 0)
        checks[f"pipeline:{name}"] = delta > 0

    # ── Ledger events ──
    ev_before = _ledger_event_counts(before_state)
    ev_after = _ledger_event_counts(after_state)
    for ev in manifest.expected_ledger_events:
        key = ev.lower()
        delta = ev_after.get(key, 0) - ev_before.get(key, 0)
        checks[f"ledger:{ev}"] = delta > 0

    # ── Phase transitions ──
    ph_before = _phase_counts(before_state)
    ph_after = _phase_counts(after_state)
    for ph in manifest.expected_phase_transitions:
        key = ph.lower()
        delta = ph_after.get(key, 0) - ph_before.get(key, 0)
        # A transition "appearing" either means it grew, or it wasn't
        # there before and is now present with any positive count.
        checks[f"phase:{ph}"] = delta > 0 or (
            key in ph_after and key not in ph_before
        )

    # ── Working-docs threshold ──
    if manifest.expected_working_docs_min > 0:
        new_docs = _working_doc_paths(after_state) - _working_doc_paths(
            before_state
        )
        checks["working_docs_min"] = (
            len(new_docs) >= manifest.expected_working_docs_min
        )

    # ── Memory / notifications thresholds ──
    if manifest.expected_memories_min > 0:
        delta = _memory_total(after_state) - _memory_total(before_state)
        checks["memories_min"] = delta >= manifest.expected_memories_min

    if manifest.expected_notifications_min > 0:
        delta = _notification_total(after_state) - _notification_total(
            before_state
        )
        checks["notifications_min"] = (
            delta >= manifest.expected_notifications_min
        )

    # ── Determine pass/fail ──
    tolerate = {t.lower() for t in manifest.tolerate_missing}
    missing: list[str] = []
    for key, ok in checks.items():
        if ok:
            continue
        # Derive the "bare" name of the failed check so tolerate_missing
        # can be declared without the "pipeline:"/"ledger:"/"phase:" prefix.
        bare = key.split(":", 1)[1].lower() if ":" in key else key.lower()
        if bare in tolerate:
            continue
        missing.append(key)

    # ── Informational: items present in after that weren't declared ──
    declared_pipes = set(manifest.expected_pipelines)
    declared_events = {e.lower() for e in manifest.expected_ledger_events}
    declared_phases = {p.lower() for p in manifest.expected_phase_transitions}
    unexpected: list[str] = []
    for name, count in pipes_after.items():
        if name in declared_pipes:
            continue
        if count > pipes_before.get(name, 0):
            unexpected.append(f"pipeline:{name}")
    for ev, count in ev_after.items():
        if ev in declared_events:
            continue
        if count > ev_before.get(ev, 0):
            unexpected.append(f"ledger:{ev}")
    for ph, count in ph_after.items():
        if ph in declared_phases:
            continue
        if count > ph_before.get(ph, 0):
            unexpected.append(f"phase:{ph}")

    passed = not missing
    summary_parts: list[str] = []
    total = len(checks)
    satisfied = sum(1 for v in checks.values() if v)
    summary_parts.append(
        f"{manifest.phase_name}: {satisfied}/{total} checks satisfied"
    )
    if missing:
        summary_parts.append(f"missing={len(missing)}")
    if unexpected:
        summary_parts.append(f"unexpected={len(unexpected)}")
    summary_parts.append("PASS" if passed else "FAIL")

    return SoakResult(
        manifest_name=manifest.phase_name,
        passed=passed,
        checks=checks,
        missing=sorted(missing),
        unexpected=sorted(unexpected),
        summary=" ".join(summary_parts),
    )


def result_to_dict(result: SoakResult) -> dict[str, Any]:
    """Serialize a :class:`SoakResult` for JSON transport."""
    return {
        "manifest_name": result.manifest_name,
        "passed": result.passed,
        "checks": dict(result.checks),
        "missing": list(result.missing),
        "unexpected": list(result.unexpected),
        "summary": result.summary,
    }
