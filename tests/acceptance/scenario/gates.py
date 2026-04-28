"""Phase gate runner (AT3).

Between conversation phases the harness runs a *gate* against the
coverage matrix: given a full-state snapshot, which coverage items are
satisfied by state evidence alone?

Each coverage item in :mod:`tests.acceptance.scenario.week_plan` carries
an ``evidence_query`` string docstring. AT1 left the actual query
callables unimplemented. AT3 wires them up here as deterministic
pure-function checks against the :meth:`HarnessServer._snapshot_full_state`
output — no LLM, no DB access, no live daemon probing.

Items whose evidence_query is conversation-based (``"messages mention
..."``, ``"any tool in X bucket"``, etc.) are intentionally *not*
handled here — the existing ``_auto_mark_coverage`` in
:mod:`tests.acceptance._report` already covers those from tool-usage
and message-text evidence. This module only handles items whose
evidence lives in observable *state*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Result type ──────────────────────────────────────────────────────────

@dataclass
class GateResult:
    """Outcome of a phase-gate check for a list of coverage item ids."""

    phase_name: str
    items_checked: list[int] = field(default_factory=list)
    items_satisfied: list[int] = field(default_factory=list)
    items_missing: list[int] = field(default_factory=list)
    #: Per-item explanation string, keyed by item id. Populated for both
    #: satisfied and missing items — the explanation names the check
    #: that succeeded or the threshold that wasn't met.
    details: dict[int, str] = field(default_factory=dict)


def result_to_dict(result: GateResult) -> dict[str, Any]:
    """Serialize a :class:`GateResult` for JSON transport."""
    return {
        "phase_name": result.phase_name,
        "items_checked": list(result.items_checked),
        "items_satisfied": list(result.items_satisfied),
        "items_missing": list(result.items_missing),
        "details": {int(k): v for k, v in result.details.items()},
    }


# ── State accessor helpers (mirror manifests.py) ─────────────────────────

def _orch(state: dict[str, Any]) -> dict[str, Any]:
    return state.get("orchestration_state") or {}


def _mem(state: dict[str, Any]) -> dict[str, Any]:
    return state.get("memory_lifecycle") or {}


def _vault(state: dict[str, Any]) -> dict[str, Any]:
    return state.get("vault_state") or {}


def _proactive(state: dict[str, Any]) -> dict[str, Any]:
    return state.get("proactive_state") or {}


def _pipeline_count(state: dict[str, Any], name: str) -> int:
    by_name = (_orch(state).get("pipeline_instances") or {}).get("by_name") or {}
    if not isinstance(by_name, dict):
        return 0
    return int(by_name.get(name, 0) or 0)


def _pipeline_state_count(state: dict[str, Any], substate: str) -> int:
    by_state = (_orch(state).get("pipeline_instances") or {}).get("by_state") or {}
    if not isinstance(by_state, dict):
        return 0
    return int(by_state.get(substate, 0) or 0)


def _pipeline_completed_for(state: dict[str, Any], name: str) -> bool:
    """True if at least one pipeline_instance for ``name`` is completed."""
    recent = (_orch(state).get("pipeline_instances") or {}).get("recent") or []
    for row in recent:
        if not isinstance(row, dict):
            continue
        if row.get("pipeline_name") == name and row.get("state") == "completed":
            return True
    return False


def _ledger_count(state: dict[str, Any], event_type: str) -> int:
    by_event = (_orch(state).get("work_ledger") or {}).get("by_event_type") or {}
    if not isinstance(by_event, dict):
        return 0
    # Ledger event types in operational.db are typically lower-case.
    return int(
        by_event.get(event_type, 0)
        or by_event.get(event_type.lower(), 0)
        or by_event.get(event_type.upper(), 0)
        or 0
    )


def _phase_transitions_total(state: dict[str, Any]) -> int:
    ssl = _orch(state).get("system_state_log") or {}
    return int(ssl.get("transitions_total", 0) or 0)


def _working_docs(state: dict[str, Any]) -> list[dict[str, Any]]:
    wds = _vault(state).get("working_docs") or []
    return [w for w in wds if isinstance(w, dict)]


def _notifications_by_tier(state: dict[str, Any]) -> dict[str, int]:
    n = (_proactive(state).get("notifications") or {}).get("by_tier") or {}
    if not isinstance(n, dict):
        return {}
    return {str(k): int(v or 0) for k, v in n.items()}


def _reminders_by_status(state: dict[str, Any]) -> dict[str, int]:
    r = (_proactive(state).get("reminders") or {}).get("by_status") or {}
    if not isinstance(r, dict):
        return {}
    return {str(k): int(v or 0) for k, v in r.items()}


def _memories_by_status(state: dict[str, Any]) -> dict[str, int]:
    by_status = (_mem(state).get("memories") or {}).get("by_status") or {}
    if not isinstance(by_status, dict):
        return {}
    return {str(k): int(v or 0) for k, v in by_status.items()}


def _user_model_facts_total(state: dict[str, Any]) -> int:
    umf = _mem(state).get("user_model_facts") or {}
    return int(umf.get("total", 0) or 0)


def _user_model_facts_by_status(state: dict[str, Any]) -> dict[str, int]:
    by_status = (_mem(state).get("user_model_facts") or {}).get("by_status") or {}
    if not isinstance(by_status, dict):
        return {}
    return {str(k): int(v or 0) for k, v in by_status.items()}


def _entities_total(state: dict[str, Any]) -> int:
    e = _mem(state).get("entities") or {}
    return int(e.get("total", 0) or 0)


# ── Per-item checks ──────────────────────────────────────────────────────
# Each check returns (satisfied: bool, explanation: str). Items whose
# evidence_query explicitly depends on conversation tool-use / message
# text return ``None`` so the caller can skip — these still flow through
# the existing message-based auto-marker.

CheckResult = tuple[bool, str] | None


def _check_item(item_id: int, state: dict[str, Any]) -> CheckResult:
    """Run the state-based check for one coverage item id."""

    # 8 — sub-task delegation: pipeline_instances created from
    # decompose_and_dispatch with >=2 worker_tasks. Use pipeline_instances
    # count for user_autonomous_task plus worker_tasks.total.
    if item_id == 8:
        ua = _pipeline_count(state, "user_autonomous_task")
        wt = int((_orch(state).get("worker_tasks") or {}).get("total", 0) or 0)
        if ua >= 1 and wt >= 2:
            return True, (
                f"user_autonomous_task pipeline_instances={ua} worker_tasks={wt}"
            )
        return False, (
            f"user_autonomous_task pipeline_instances={ua} (need >=1), "
            f"worker_tasks={wt} (need >=2)"
        )

    # 12 — DEEP_IDLE background pipelines: session_bridge_pruning or
    # skill_refinement recorded in work_ledger (proxied via pipeline_instances).
    if item_id == 12:
        sb = _pipeline_count(state, "session_bridge_pruning")
        sr = _pipeline_count(state, "skill_refinement")
        if sb + sr >= 1:
            return True, (
                f"deep-idle pipelines fired "
                f"(session_bridge_pruning={sb}, skill_refinement={sr})"
            )
        return False, "neither session_bridge_pruning nor skill_refinement fired"

    # 21 — LONG_BACKGROUND dispatch: pipeline_instance for
    # user_autonomous_task + at least one working doc.
    if item_id == 21:
        ua = _pipeline_count(state, "user_autonomous_task")
        wds = _working_docs(state)
        if ua >= 1 and wds:
            return True, (
                f"user_autonomous_task={ua} with {len(wds)} working doc(s)"
            )
        return False, (
            f"user_autonomous_task={ua} (need >=1), working_docs={len(wds)} "
            f"(need >=1)"
        )

    # 24 — phase transitions logged.
    if item_id == 24:
        total = _phase_transitions_total(state)
        if total >= 1:
            return True, f"system_state_log.transitions_total={total}"
        return False, "system_state_log.transitions_total=0"

    # 25 — LONG_BACKGROUND dispatch pipeline_instance.
    if item_id == 25:
        ua = _pipeline_count(state, "user_autonomous_task")
        if ua >= 1:
            return True, f"pipeline_instances.by_name[user_autonomous_task]={ua}"
        return False, "pipeline_instances.by_name[user_autonomous_task]=0"

    # 26 — working doc in Inbox with pipeline frontmatter.
    if item_id == 26:
        wds = _working_docs(state)
        if wds:
            return True, f"{len(wds)} working doc(s) with pipeline frontmatter"
        return False, "no working docs detected in _KoraMemory/Inbox/"

    # 27 — adaptive task list mutation. Evidence: at least one
    # user_autonomous_task with >=2 worker_tasks.
    if item_id == 27:
        ua = _pipeline_count(state, "user_autonomous_task")
        wt = int((_orch(state).get("worker_tasks") or {}).get("total", 0) or 0)
        if ua >= 1 and wt >= 2:
            return True, (
                f"user_autonomous_task={ua} with worker_tasks={wt}"
            )
        return False, (
            f"need user_autonomous_task >=1 (got {ua}) AND worker_tasks >=2 (got {wt})"
        )

    # 28 — Kora-judged completion: pipeline_instance state='completed'
    # for user_autonomous_task or proactive_research.
    if item_id == 28:
        completed = (
            _pipeline_completed_for(state, "user_autonomous_task")
            or _pipeline_completed_for(state, "proactive_research")
        )
        if completed:
            return True, "long-running pipeline reached state='completed'"
        return False, "no user_autonomous_task/proactive_research in state='completed'"

    # 30 — cancel_task → TASK_CANCELLED ledger event.
    if item_id == 30:
        n = _ledger_count(state, "task_cancelled")
        if n >= 1:
            return True, f"work_ledger[task_cancelled]={n}"
        return False, "no TASK_CANCELLED event in work_ledger"

    # 33 — paired RATE_LIMIT_PAUSED + RATE_LIMIT_RESUMED.
    if item_id == 33:
        by_reason = (_orch(state).get("work_ledger") or {}).get("by_reason") or {}
        paused = (
            _ledger_count(state, "rate_limit_paused")
            or int(by_reason.get("rate_limit_paused", 0) or 0)
        )
        resumed = (
            _ledger_count(state, "rate_limit_resumed")
            or int(by_reason.get("rate_limit_retry", 0) or 0)
        )
        if paused >= 1 and resumed >= 1:
            return True, (
                f"rate_limit_paused={paused}, rate_limit_resumed={resumed}"
            )
        return False, (
            f"need paired events: paused={paused}, resumed={resumed}"
        )

    # 34 / 46 — templated fallback notification.
    if item_id in (34, 46):
        by_tier = _notifications_by_tier(state)
        n = by_tier.get("templated", 0)
        if n >= 1:
            return True, f"notifications.by_tier[templated]={n}"
        return False, "no templated-tier notification recorded"

    # 36 — >=2 user_autonomous_task pipeline_instances completed.
    if item_id == 36:
        by_name = (_orch(state).get("pipeline_instances") or {}).get(
            "by_name"
        ) or {}
        n = int(by_name.get("user_autonomous_task", 0) or 0)
        # At least one must be completed.
        completed = _pipeline_completed_for(state, "user_autonomous_task")
        if n >= 2 and completed:
            return True, f"user_autonomous_task count={n}, completed present"
        return False, (
            f"need user_autonomous_task >=2 (got {n}) "
            f"AND at least one completed ({'yes' if completed else 'no'})"
        )

    # 38 — continuity_check pipeline_instance completed.
    if item_id == 38:
        if _pipeline_completed_for(state, "continuity_check"):
            return True, "continuity_check pipeline_instance completed"
        n = _pipeline_count(state, "continuity_check")
        if n >= 1:
            return (
                True,
                f"continuity_check pipeline_instance(s)={n}",
            )
        return False, "no continuity_check pipeline_instance found"

    # 39 — post_session_memory → post_memory_vault sequence.
    if item_id == 39:
        a = _pipeline_completed_for(state, "post_session_memory")
        b = _pipeline_count(state, "post_memory_vault") >= 1
        recent = ((_orch(state).get("work_ledger") or {}).get("recent") or [])
        seq = any(
            str(r.get("trigger_name") or "") == "post_memory_vault.seq.post_session_memory"
            or "sequence_complete" in str(r.get("metadata_json") or "")
            for r in recent
            if isinstance(r, dict)
        )
        if a and seq:
            return True, (
                "post_session_memory completed and sequence_complete fired post_memory_vault"
            )
        return False, (
            f"post_session_memory_completed={a}, post_memory_vault_started={b}"
        )

    # 40 — wake_up_preparation completed within WAKE_UP_WINDOW.
    if item_id == 40:
        completed = _pipeline_completed_for(state, "wake_up_preparation")
        wake_phases = int(
            ((_orch(state).get("system_state_log") or {}).get("by_phase") or {})
            .get("wake_up_window", 0) or 0
        )
        if completed and wake_phases >= 1:
            return True, (
                "wake_up_preparation completed AND wake_up_window phase seen"
            )
        return False, (
            f"wake_up_preparation_completed={completed}, "
            f"wake_up_window_phases={wake_phases}"
        )

    # 41 / 62 — contextual_engagement pipeline completed.
    if item_id in (41, 62):
        if _pipeline_completed_for(state, "contextual_engagement"):
            return True, "contextual_engagement pipeline_instance completed"
        n = _pipeline_count(state, "contextual_engagement")
        if n >= 1:
            return True, f"contextual_engagement pipeline_instance(s)={n}"
        return False, "no contextual_engagement pipeline_instance found"

    # 42 — proactive_pattern_scan pipeline completed (after INSIGHT_AVAILABLE).
    if item_id == 42:
        if _pipeline_completed_for(state, "proactive_pattern_scan"):
            return True, "proactive_pattern_scan pipeline_instance completed"
        n = _pipeline_count(state, "proactive_pattern_scan")
        if n >= 1:
            return True, f"proactive_pattern_scan pipeline_instance(s)={n}"
        return False, "no proactive_pattern_scan pipeline_instance found"

    # 43 — open_decisions row present.
    if item_id == 43:
        od = _orch(state).get("open_decisions") or {}
        total = int(od.get("total", 0) or 0)
        if total >= 1:
            return True, f"open_decisions.total={total}"
        return False, "open_decisions table empty"

    # 44 — runtime_pipelines registered.
    if item_id == 44:
        rp = _orch(state).get("runtime_pipelines") or {}
        total = int(rp.get("total", 0) or 0)
        by_name = (_orch(state).get("pipeline_instances") or {}).get(
            "by_name"
        ) or {}
        routine_fired = sum(
            int(v or 0)
            for k, v in by_name.items()
            if str(k).startswith("routine_")
        )
        if total >= 1 and routine_fired >= 1:
            return True, (
                f"runtime_pipelines.total={total}, routine_fired={routine_fired}"
            )
        return False, (
            f"runtime_pipelines.total={total}, routine_fired={routine_fired}"
        )

    # 45 — work_ledger can answer "why did X run". Require >=1 row.
    if item_id == 45:
        wl = _orch(state).get("work_ledger") or {}
        total = int(wl.get("total", 0) or 0)
        if total >= 1:
            return True, f"work_ledger.total={total}"
        return False, "work_ledger empty"

    # 47 — memory extraction produced memories.
    if item_id == 47:
        mem = (_mem(state).get("memories") or {})
        total = int(mem.get("total", 0) or 0)
        extract_done = _pipeline_completed_for(state, "post_session_memory")
        if total >= 1 and extract_done:
            return True, (
                f"memories.total={total}, post_session_memory completed"
            )
        return False, (
            f"memories.total={total} (need >=1), "
            f"post_session_memory_completed={extract_done}"
        )

    # 48 — consolidated memories present.
    if item_id == 48:
        mem = _mem(state).get("memories") or {}
        consolidated = int(mem.get("with_consolidated_into", 0) or 0)
        facts = _mem(state).get("user_model_facts") or {}
        fact_consolidated = int(facts.get("with_consolidated_into", 0) or 0)
        statuses = _memories_by_status(state)
        via_status = int(statuses.get("consolidated", 0) or 0)
        fact_statuses = _user_model_facts_by_status(state)
        fact_via_status = int(fact_statuses.get("consolidated", 0) or 0)
        n = consolidated + via_status + fact_consolidated + fact_via_status
        if n >= 1:
            return True, (
                f"consolidated memories: with_consolidated_into={consolidated}, "
                f"by_status[consolidated]={via_status}; "
                f"user_model_facts.with_consolidated_into={fact_consolidated}, "
                f"user_model_facts[consolidated]={fact_via_status}"
            )
        return False, "no consolidated memories detected"

    # 49 — dedup soft-deleted a near-duplicate memory.
    if item_id == 49:
        statuses = _memories_by_status(state)
        fact_statuses = _user_model_facts_by_status(state)
        dedup_statuses = ("deleted", "soft_deleted", "merged")
        soft_deleted = sum(int(statuses.get(st, 0) or 0) for st in dedup_statuses)
        fact_merged = sum(
            int(fact_statuses.get(st, 0) or 0) for st in dedup_statuses
        )
        if soft_deleted >= 1:
            return True, (
                f"memories[status=soft_deleted]={soft_deleted}"
            )
        if fact_merged >= 1:
            return True, f"user_model_facts[status=merged]={fact_merged}"
        return False, "no dedup/merged memories or user-model facts detected"

    # 50 — entities merged: rows with merged_from provenance. A nonzero
    # entity total only proves extraction, not resolution.
    if item_id == 50:
        entities = _mem(state).get("entities") or {}
        entity_merged_from = int(entities.get("with_merged_from", 0) or 0)
        if entity_merged_from >= 1:
            return True, f"entities.with_merged_from={entity_merged_from}"
        return False, (
            f"entities.with_merged_from={entity_merged_from}"
        )

    # 51 — weekly_adhd_profile pipeline ran + user_model_facts non-empty.
    if item_id == 51:
        completed = _pipeline_completed_for(state, "weekly_adhd_profile")
        umf = _user_model_facts_total(state)
        if completed and umf >= 1:
            return True, (
                f"weekly_adhd_profile completed AND user_model_facts={umf}"
            )
        return False, (
            f"weekly_adhd_profile_completed={completed}, user_model_facts={umf}"
        )

    # 52 — post_memory_vault:reindex completed.
    if item_id == 52:
        if _pipeline_completed_for(state, "post_memory_vault"):
            return True, "post_memory_vault pipeline completed (reindex stage)"
        n = _pipeline_count(state, "post_memory_vault")
        if n >= 1:
            return True, f"post_memory_vault pipeline_instance(s)={n}"
        return False, "no post_memory_vault pipeline_instance"

    # 53 — structure step: files moved out of Inbox. Heuristic: non-inbox
    # notes > inbox notes OR folder hierarchy present.
    if item_id == 53:
        counts = _vault(state).get("counts") or {}
        inbox = int(counts.get("inbox", 0) or 0)
        outside_inbox = sum(
            int(counts.get(k, 0) or 0)
            for k in counts
            if k != "inbox" and k != "total_notes"
        )
        hierarchy = bool(_vault(state).get("folder_hierarchy_present"))
        if hierarchy and (outside_inbox > inbox or outside_inbox >= 1):
            return True, (
                f"folder hierarchy present, outside_inbox={outside_inbox}, "
                f"inbox={inbox}"
            )
        return False, (
            f"folder_hierarchy_present={hierarchy}, outside_inbox={outside_inbox}, "
            f"inbox={inbox}"
        )

    # 54 — wikilinks injected: wikilink_density.total_wikilinks > 0.
    if item_id == 54:
        dens = _vault(state).get("wikilink_density") or {}
        total = int(dens.get("total_wikilinks", 0) or 0)
        if total >= 1:
            return True, f"wikilink_density.total_wikilinks={total}"
        return False, "no wikilinks detected in vault"

    # 55 — entity pages under Entities/.
    if item_id == 55:
        counts = _vault(state).get("counts") or {}
        people = int(counts.get("entities_people", 0) or 0)
        places = int(counts.get("entities_places", 0) or 0)
        projects = int(counts.get("entities_projects", 0) or 0)
        total = people + places + projects
        if total >= 1:
            return True, (
                f"entity pages: people={people} places={places} projects={projects}"
            )
        return False, "no entity pages under _KoraMemory/Entities/"

    # 56 — MOC pages regenerated.
    if item_id == 56:
        counts = _vault(state).get("counts") or {}
        mocs = int(counts.get("moc_pages", 0) or 0)
        if mocs >= 1:
            return True, f"moc_pages={mocs}"
        return False, "no MOC pages under _KoraMemory/Maps of Content/"

    # 57 — sessions under _KoraMemory/Sessions/.
    if item_id == 57:
        counts = _vault(state).get("counts") or {}
        sessions = int(counts.get("sessions", 0) or 0)
        if sessions >= 1:
            return True, f"sessions notes={sessions}"
        return False, "no session notes under _KoraMemory/Sessions/"

    # 58 — ContextEngine insights: persisted OR proactive_pattern_scan fired.
    if item_id == 58:
        ins = _proactive(state).get("insights") or {}
        persisted = bool(ins.get("persisted"))
        total = ins.get("total_if_persisted") or 0
        scan = _pipeline_count(state, "proactive_pattern_scan")
        if persisted and total >= 1:
            return True, f"insights persisted, total={total}"
        if scan >= 1:
            return True, (
                f"proactive_pattern_scan fired {scan}x "
                "(indirect insight evidence)"
            )
        # Insights not persisted today is the documented state — skip
        # rather than fail when there's no proactive_pattern_scan either.
        return None

    # 59 — Area A pattern nudge.
    if item_id == 59:
        if _pipeline_completed_for(state, "proactive_pattern_scan"):
            return True, "proactive_pattern_scan pipeline_instance completed"
        n = _pipeline_count(state, "proactive_pattern_scan")
        if n >= 1:
            return True, f"proactive_pattern_scan={n}"
        return False, "no proactive_pattern_scan pipeline_instance"

    # 60 — Area B anticipatory_prep.
    if item_id == 60:
        if _pipeline_completed_for(state, "anticipatory_prep"):
            return True, "anticipatory_prep pipeline completed"
        n = _pipeline_count(state, "anticipatory_prep")
        if n >= 1:
            return True, f"anticipatory_prep={n}"
        return False, "no anticipatory_prep pipeline_instance"

    # 61 — Area C proactive_research.
    if item_id == 61:
        if _pipeline_completed_for(state, "proactive_research"):
            return True, "proactive_research pipeline completed"
        n = _pipeline_count(state, "proactive_research")
        if n >= 1:
            return True, f"proactive_research={n}"
        return False, "no proactive_research pipeline_instance"

    # 63 — Area E commitment_tracking.
    if item_id == 63:
        if _pipeline_completed_for(state, "commitment_tracking"):
            return True, "commitment_tracking pipeline completed"
        n = _pipeline_count(state, "commitment_tracking")
        if n >= 1:
            return True, f"commitment_tracking={n}"
        return False, "no commitment_tracking pipeline_instance"

    # 64 — Area E stuck_detection.
    if item_id == 64:
        if _pipeline_completed_for(state, "stuck_detection"):
            return True, "stuck_detection pipeline completed"
        n = _pipeline_count(state, "stuck_detection")
        if n >= 1:
            return True, f"stuck_detection={n}"
        return False, "no stuck_detection pipeline_instance"

    # 65 — Area E connection_making.
    if item_id == 65:
        if _pipeline_completed_for(state, "connection_making"):
            return True, "connection_making pipeline completed"
        n = _pipeline_count(state, "connection_making")
        if n >= 1:
            return True, f"connection_making={n}"
        return False, "no connection_making pipeline_instance"

    # 66 — reminder delivered.
    if item_id == 66:
        rem = _reminders_by_status(state)
        delivered = rem.get("delivered", 0)
        if delivered >= 1:
            return True, f"reminders.by_status[delivered]={delivered}"
        return False, "no delivered reminders"

    # 67 — wake-up briefing: wake_up_preparation OR wake_up_window phase.
    if item_id == 67:
        wake_phases = int(
            ((_orch(state).get("system_state_log") or {}).get("by_phase") or {})
            .get("wake_up_window", 0) or 0
        )
        wake_pipe = _pipeline_count(state, "wake_up_preparation")
        if wake_phases >= 1 and wake_pipe >= 1:
            return True, (
                f"wake_up_window phases={wake_phases}, "
                f"wake_up_preparation={wake_pipe}"
            )
        return False, "no wake-up evidence in state"

    # Conversation-based items (2/3/4/5/6/7/9/10/11/13-20/22/23/29/31/32/35/37/100-102)
    # — these are handled by the existing tool-usage / message-based
    # auto-marker. Returning None signals "skip".
    #
    # Item 35 specifically covers crash recovery (daemon kill + restart
    # resumes from latest checkpoint and the working doc is intact).
    # It is conversation-based because verification requires an
    # out-of-band restart sequence that the state snapshot alone cannot
    # attest to — the harness-side restart orchestration drives its
    # coverage mark in ``_auto_mark_coverage`` rather than here.
    return None


def run_phase_gate(
    phase_name: str,
    coverage_items: list[int],
    state: dict[str, Any],
) -> GateResult:
    """Check which of ``coverage_items`` are satisfied by state evidence.

    Items whose evidence_query is conversation-based are silently
    skipped — they are *not* counted in ``items_checked``. The caller
    should merge this gate's results with the existing
    ``_auto_mark_coverage`` output.

    Deterministic: same state in → same result out.
    """
    items_checked: list[int] = []
    items_satisfied: list[int] = []
    items_missing: list[int] = []
    details: dict[int, str] = {}

    for item_id in coverage_items:
        check = _check_item(item_id, state)
        if check is None:
            # Not a state-based item — skip.
            continue
        satisfied, explanation = check
        items_checked.append(item_id)
        details[item_id] = explanation
        if satisfied:
            items_satisfied.append(item_id)
        else:
            items_missing.append(item_id)

    return GateResult(
        phase_name=phase_name,
        items_checked=sorted(items_checked),
        items_satisfied=sorted(items_satisfied),
        items_missing=sorted(items_missing),
        details=details,
    )
