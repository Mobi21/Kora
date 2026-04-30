"""Generate the final acceptance test report (V2-aligned).

Covers all V2 subsystems: conversation quality, compaction, auth relay,
life management, tool usage, autonomous execution, emotion/energy,
skills activation, and filesystem operations.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Anchored repo root — same strategy as the harness server. ``_report.py``
# lives at tests/acceptance/_report.py so parents[2] is the repo root.
_PROJECT_ROOT = Path(__file__).parents[2].resolve()


# ── Tool buckets ──────────────────────────────────────────────────────────────
# Single source of truth for the report and the harness ``tool-usage-summary``
# command. ``tests/unit/acceptance/test_tool_buckets.py`` enforces both
# call sites use the same dict.
#
# ``orchestration_tools`` replaces the retired ``start_autonomous`` /
# ``auto_tools`` bucket. The 7 supervisor orchestration tools come from
# ``SUPERVISOR_TOOLS`` in ``kora_v2/graph/dispatch.py`` (Phase 7.5b).
TOOL_BUCKETS: dict[str, set[str]] = {
    "life_tools": {
        "log_medication", "log_meal", "create_reminder",
        "query_reminders", "query_medications", "query_meals",
        "query_focus_blocks", "quick_note",
        "start_focus_block", "end_focus_block",
        "create_routine", "list_routines", "start_routine",
        "advance_routine", "routine_progress",
        "create_day_plan", "confirm_reality", "correct_reality",
        "assess_life_load", "repair_day_plan", "decide_life_nudge",
        "record_nudge_feedback", "create_context_pack", "bridge_tomorrow",
        "set_support_profile_status", "enter_stabilization_mode",
        "export_trusted_support", "check_crisis_boundary",
    },
    "filesystem_tools": {
        "read_file", "write_file", "list_directory",
        "create_directory", "file_exists",
    },
    "mcp_tools": {"search_web", "fetch_url"},
    "orchestration_tools": {
        "decompose_and_dispatch",
        "get_running_tasks",
        "get_task_progress",
        "get_working_doc",
        "cancel_task",
        "modify_task",
        "record_decision",
    },
    "memory_tools": {"recall"},
}


def _normalize_tool_name(tc: Any) -> str:
    """Strip auth wrapper / bracket markers to get the real tool name.

    Raw tool_calls entries can take three shapes:
      * a bare string ``"write_file"`` from tool_start
      * an auth-wrapped string ``"[auth:write_file:approved]"`` from
        auth_request/auth_response
      * a dict (newer protocol) with ``{"name": "write_file", ...}``

    The harness's ``cmd_tool_usage_summary`` has the reference logic. We
    mirror it here so the report counts tools the same way the CLI does.
    Previously ``.strip("[]").replace("auth:", "")`` left the trailing
    ``:approved]`` attached and split ``write_file:approved`` from
    ``write_file``.
    """
    if isinstance(tc, dict):
        return str(tc.get("name", "")) or "unknown"
    name = str(tc)
    if name.startswith("[auth:"):
        # "[auth:write_file:approved]" -> "write_file"
        return name.split(":")[1].rstrip("]").split(":")[0]
    return name.strip("[]")


def _auto_mark_coverage(
    *,
    tool_usage: dict[str, Any],
    life_data: dict[str, Any],
    auto_state: dict[str, Any],
    cap_health: dict[str, Any],
    compaction_events: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    auth_results: list[dict[str, Any]],
    latest_status: dict[str, Any] | None,
    orch_evidence: dict[str, Any] | None = None,
    benchmark_state: dict[str, Any] | None = None,
    error_results: list[dict[str, Any]] | None = None,
    snapshots_dir: Path | None = None,
    skill_gating_check: dict[str, Any] | None = None,
) -> dict[int, str]:
    """Derive coverage markers from observable run evidence.

    Returns a mapping ``{item_id: marker}`` where marker is one of
    ``"x"`` (satisfied) or ``"~"`` (partial / degraded-as-designed).
    The caller merges these with operator-written markers from
    ``coverage.md``; operator markers win on conflict. A few items
    (13 restart-resilience, 19 emotion-tone, 20 skill-gating) require
    semantic judgment and are intentionally not auto-marked.

    Before this helper existed, the auto-tracker was effectively inert:
    the 2026-04-11 audit ran with 22/23 items exercised but the report
    printed ``0/23 satisfied`` because it only read ``coverage.md``
    which nobody was writing during the automated run.
    """
    auto: dict[int, str] = {}
    tool_counts = tool_usage.get("tool_counts", {})

    def _msg_mentions(*needles: str) -> bool:
        for msg in messages:
            content = (msg.get("content") or "")
            if not isinstance(content, str):
                continue
            lowered = content.lower()
            for needle in needles:
                if needle.lower() in lowered:
                    return True
        return False

    def _assistant_after_user(
        user_needles: tuple[str, ...],
        response_needles: tuple[str, ...],
        *,
        require_all: bool = False,
    ) -> bool:
        for idx, msg in enumerate(messages):
            if msg.get("role") != "user":
                continue
            content = str(msg.get("content") or "").lower()
            if not any(needle.lower() in content for needle in user_needles):
                continue
            for follow in messages[idx + 1:]:
                if follow.get("role") != "assistant":
                    continue
                response = str(follow.get("content") or "").lower()
                if require_all:
                    if all(
                        needle.lower() in response
                        for needle in response_needles
                    ):
                        return True
                    break
                if any(
                    needle.lower() in response
                    for needle in response_needles
                ):
                    return True
                break
        return False

    def _has_all_term_groups(text: str, groups: tuple[tuple[str, ...], ...]) -> bool:
        lowered = text.lower()
        return all(any(term in lowered for term in group) for group in groups)

    def _assistant_message_has_groups(
        groups: tuple[tuple[str, ...], ...],
        *,
        extra_any: tuple[str, ...] = (),
    ) -> bool:
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            content = str(msg.get("content") or "")
            lowered = content.lower()
            if not _has_all_term_groups(content, groups):
                continue
            if extra_any and not any(term in lowered for term in extra_any):
                continue
            return True
        return False

    def _assistant_after_user_groups(
        user_needles: tuple[str, ...],
        groups: tuple[tuple[str, ...], ...],
    ) -> bool:
        for idx, msg in enumerate(messages):
            if msg.get("role") != "user":
                continue
            content = str(msg.get("content") or "").lower()
            if not any(needle.lower() in content for needle in user_needles):
                continue
            for follow in messages[idx + 1:]:
                if follow.get("role") != "assistant":
                    continue
                if _has_all_term_groups(
                    str(follow.get("content") or ""),
                    groups,
                ):
                    return True
                break
        return False

    def _external_capability_disclosed() -> bool:
        """True when external/web capability is absent and the run says so plainly."""
        if _msg_mentions(
            "unavailable",
            "can't pull",
            "no web search",
            "browser",
            "denied",
            "write also denied",
            "read and write both fail",
            "tools are constrained",
            "tool is constrained",
            "mcp web-search path failed",
            "mcp fetch path failed",
            "unconfigured",
            "not configured",
        ):
            return True
        for pack_name in ("workspace", "browser", "vault", "doctor"):
            info = cap_health.get(pack_name, {})
            if info.get("status") in (
                "unconfigured",
                "degraded",
                "unhealthy",
                "unimplemented",
            ) and info.get("remediation"):
                return True
        return False

    # Item 2: Life OS identity and support context.
    if (
        _msg_mentions("jordan")
        and _msg_mentions("adhd", "autism", "sensory", "anxiety", "burnout")
        and _msg_mentions("alex", "trusted support", "support")
        and _msg_mentions("local-first", "local first", "privacy")
    ):
        auto[2] = "x"

    # Item 3: internal calendar as the spine.
    if (
        _msg_mentions("calendar", "schedule", "appointment", "remind me")
        and _msg_mentions("today", "tomorrow", "this week", "monday", "tuesday")
    ):
        auto[3] = "x"

    # Item 4: ADHD / executive-dysfunction support.
    if _msg_mentions(
        "adhd", "time blind", "avoid", "forgot", "initiate", "executive"
    ) and _msg_mentions("tiny", "next action", "carry", "repair", "missed"):
        auto[4] = "x"

    # Item 5: autism / sensory-load support.
    if _msg_mentions(
        "autism", "sensory", "noise", "routine", "transition", "overload"
    ) and _msg_mentions("predictable", "low-ambiguity", "fewer decisions", "sequence"):
        auto[5] = "x"

    # Item 6: burnout/anxiety/low-energy support.
    if _msg_mentions("burnout", "anxious", "low energy", "dread", "frozen") and _msg_mentions(
        "stabilize", "essentials", "downshift", "smaller"
    ):
        auto[6] = "x"

    # Item 7: life management tools used. Full credit requires the actual
    # acceptance surfaces, not just one life tool somewhere in the run.
    life_required = ("medication", "meal", "reminder", "quick_note", "focus_block")
    life_hit_count = sum(
        1 for key in life_required if int(life_data.get(f"{key}_count", 0) or 0) > 0
    )
    if life_hit_count == len(life_required):
        auto[7] = "x"
    elif life_hit_count > 0 or tool_usage.get("life_management"):
        auto[7] = "~"

    # Item 9: web research — successful MCP/capability call OR disclosed
    # failure (the item description explicitly allows both).
    if tool_usage.get("mcp") or tool_usage.get("capability_browser"):
        auto[9] = "x"
    elif _external_capability_disclosed():
        auto[9] = "x"

    # Item 10 & 15: compaction pressure + metadata
    if compaction_events:
        auto[10] = "x"
        if any(
            ev.get("token_count") is not None and ev.get("tier")
            for ev in compaction_events
        ):
            auto[15] = "x"

    # Item 11: wrong inference or plan-drift repair.
    if _assistant_after_user(
        (
            "that's not what i meant",
            "wrong assumption",
            "you assumed",
            "actually",
            "correct that",
            "small correction",
            "correction",
            "not a phone call",
            "not phone",
            "never call",
            "pickup only if confirmed",
        ),
        ("update", "correct", "correction", "replan", "flagged"),
        require_all=False,
    ) or _msg_mentions("LIFE_EVENT_CORRECTED", "WRONG_INFERENCE_REPAIRED") or int(
        life_data.get("correction_event_count", 0) or 0
    ) > 0:
        auto[11] = "x"

    # Item 8: decompose_and_dispatch must create durable orchestration
    # evidence. A tool call alone only proves Kora acknowledged the work,
    # not that the scheduler received anything executable.
    if tool_counts.get("decompose_and_dispatch", 0) >= 1:
        if orch_evidence and _has_user_pipeline_with_tasks(orch_evidence):
            auto[8] = "x"
        else:
            auto[8] = "~"

    # Item 12: real background pipelines fire during DEEP_IDLE. The
    # retired BackgroundWorker no longer exists; post-7.5 evidence is
    # the core housekeeping pipelines themselves completing under
    # orchestration.
    if orch_evidence and _deep_idle_housekeeping_marker(orch_evidence):
        auto[12] = "x"
    elif latest_status and latest_status.get("background_worker_items", 0) >= 1:
        auto[12] = "x"

    # Item 14: lived-week review. Credit only concrete stateful review
    # language, not a vague "nice week" summary.
    if _assistant_after_user(
        (
            "weekly review",
            "weekly_review",
            "week review",
            "summary",
            "recap",
            "what actually happened",
            "artifact-backed weekly review",
            "artifact backed weekly review",
            "what actually still have",
            "before you end",
        ),
        ("missed", "repaired", "tomorrow", "reminder", "support", "open"),
        require_all=False,
    ) or _assistant_after_user(
        (
            "weekly review",
            "week review",
            "what state backs",
            "artifact-backed",
            "artifact backed",
        ),
        ("calendar", "reminder", "routine", "next week"),
        require_all=True,
    ):
        auto[14] = "x"

    # Item 16: memory recall
    if tool_counts.get("recall", 0) >= 1:
        auto[16] = "x"

    # Item 17: auth relay round-trip — need both an approve and a deny
    if auth_results:
        has_approved = any(ar.get("approved") is True for ar in auth_results)
        has_denied = any(ar.get("approved") is False for ar in auth_results)
        if has_approved and has_denied:
            auto[17] = "x"
        elif has_approved or has_denied:
            auto[17] = "~"

    # Item 18: malformed/empty/raw inputs produced graceful error frames
    # and the normal follow-up turn still completed.
    if error_results:
        required = {
            "malformed_json_frame",
            "empty_chat_content",
            "normal_after_errors",
        }
        passed = {
            str(r.get("test"))
            for r in error_results
            if r.get("survived") is True
        }
        if required.issubset(passed):
            auto[18] = "x"
        elif passed:
            auto[18] = "~"

    # Item 19: emotional/energy adaptation. This is backed by durable
    # self-report rows plus either emotion-shift runtime evidence or
    # contextual engagement that reacts to that shift.
    if int(life_data.get("energy_self_report_count", 0) or 0) > 0:
        emotion_runtime = False
        if orch_evidence:
            ledger_events = orch_evidence.get("ledger_events") or []
            emotion_runtime = any(
                evt.get("trigger_name") == "EMOTION_SHIFT_DETECTED"
                for evt in ledger_events
            ) or any(
                p.get("pipeline_name") == "contextual_engagement"
                for p in orch_evidence.get("pipeline_instances", [])
            )
        auto[19] = "x" if emotion_runtime else "~"

    # Item 13: restart preservation is evidenced by the pre/post restart
    # snapshots plus a healthy post-restart daemon. Working-doc and
    # orchestration continuity get deeper coverage in items 35/44.
    if snapshots_dir is not None:
        pre_restart = snapshots_dir / "pre_restart.json"
        post_restart = snapshots_dir / "post_restart.json"
        if pre_restart.exists() and post_restart.exists():
            try:
                post = json.loads(post_restart.read_text())
            except Exception:
                post = {}
            status = post.get("status") or {}
            continuity_response = _assistant_after_user(
                (
                    "before the restart",
                    "survived restart",
                    "restart",
                ),
                ("calendar", "reminder"),
                require_all=True,
            ) or _assistant_after_user(
                (
                    "before the restart",
                    "survived restart",
                    "restart",
                ),
                ("support", "routine"),
                require_all=True,
            ) or _assistant_after_user(
                (
                    "before the restart",
                    "survived restart",
                    "restart",
                ),
                ("tomorrow", "unfinished"),
                require_all=True,
            )
            if (
                status.get("status") in {"ok", "healthy", "running"}
                and continuity_response
            ):
                auto[13] = "x"
            elif (
                status.get("status") in {"ok", "healthy", "running"}
                and int(life_data.get("reminder_count", 0) or 0) > 0
                and int(life_data.get("support_profile_count", 0) or 0) > 0
                and int(life_data.get("routine_count", 0) or 0) > 0
                and orch_evidence
                and int(orch_evidence.get("open_decision_count", 0) or 0) > 0
                and any(
                    str(doc.get("pipeline_name") or "").startswith("routine_")
                    for doc in post.get("vault_state", {}).get("working_docs", [])
                    if isinstance(doc, dict)
                )
            ):
                auto[13] = "x"
            elif status.get("status") in {"ok", "healthy", "running"}:
                auto[13] = "~"
            post_working_docs = (
                post.get("vault_state", {}).get("working_docs", [])
            )
            if orch_evidence and post_working_docs:
                has_orch_after_restart = bool(
                    orch_evidence.get("worker_tasks")
                    or orch_evidence.get("pipeline_instances")
                )
                if has_orch_after_restart and status.get("status") in {
                    "ok", "healthy", "running",
                }:
                    auto[35] = "x"
                elif has_orch_after_restart:
                    auto[35] = "~"

    # Item 21: long-running autonomous execution. Dispatch is not enough:
    # require a long-intent pipeline plus ledger/task evidence. Completed
    # pipelines get full credit; running pipelines with task progress are
    # partial.
    long_marker = (
        _long_autonomous_marker(orch_evidence) if orch_evidence else None
    )
    if long_marker:
        auto[21] = long_marker
    elif auto_state.get("total_items", 0) > 0:
        auto[21] = "~"

    # Item 29: mid-flight progress query. The report cannot replay the
    # full tool payload, but a get_task_progress call during a run is
    # captured in the conversation tool log and the live evidence log
    # carries the exact returned state/elapsed fields.
    if tool_counts.get("get_task_progress", 0) >= 1:
        auto[29] = "x"

    if skill_gating_check:
        auto[20] = "x" if skill_gating_check.get("passed") else "~"

    # Item 37: re-engagement merge. Older reports expected the explicit
    # get_running_tasks tool; the runtime now also prefetches relevant
    # terminal tasks into the turn and the supervisor may inspect them
    # through get_task_progress/get_working_doc. Require both a
    # re-engagement surface and completed task evidence.
    reengagement_surface = (
        tool_counts.get("get_running_tasks", 0) >= 1
        or tool_counts.get("get_task_progress", 0) >= 1
        or tool_counts.get("get_working_doc", 0) >= 1
    )
    if reengagement_surface and orch_evidence:
        completed_background = any(
            task.get("state") == "completed"
            for task in orch_evidence.get("worker_tasks", [])
        )
        if completed_background:
            auto[37] = "x"

    # Item 22: filesystem operations. The coverage item names the full
    # read/write/list surface, so do not score it from a write-only run.
    if all(
        int(tool_counts.get(name, 0) or 0) >= 1
        for name in ("read_file", "write_file", "list_directory")
    ):
        auto[22] = "x"

    # Item 23: life management DB records persist
    if life_data.get("available"):
        required_records = (
            int(life_data.get("medication_count", 0) or 0),
            int(life_data.get("meal_count", 0) or 0),
            int(life_data.get("reminder_count", 0) or 0),
        )
        if all(count > 0 for count in required_records):
            auto[23] = "x"
        elif any(count > 0 for count in required_records):
            auto[23] = "~"

    # Item 100: capability pack surface — either a real call OR a pack
    # that reports unconfigured/degraded with a remediation hint.
    any_cap_calls = bool(
        tool_usage.get("capability_workspace")
        or tool_usage.get("capability_browser")
        or tool_usage.get("capability_vault")
    )
    if any_cap_calls:
        auto[100] = "x"
    elif cap_health:
        for pack_name in ("workspace", "browser", "vault", "doctor"):
            info = cap_health.get(pack_name, {})
            if info.get("status") in (
                "unconfigured", "degraded", "unhealthy", "unimplemented"
            ) and info.get("remediation"):
                auto[100] = "x"
                break

    # Item 101: disclosed-failure path — the assistant acknowledged a
    # tool failure plainly rather than silent fallback.
    if _external_capability_disclosed() or _msg_mentions(
        "failed",
        "permission denied",
        "couldn't",
        "not available",
    ):
        auto[101] = "x"

    # Item 102: policy matrix — 4 capability packs visible + policy
    # grants section has data.
    if len(cap_health) >= 4:
        auto[102] = "x"

    # Items 24-67 (Phase 7.5 + Phase 8 orchestration / memory / vault /
    # context / proactive coverage) — credit from real evidence in the
    # orchestration tables. Only items with matching SQL evidence are
    # auto-marked; items requiring semantic judgment (19 emotion tone,
    # 20 skill gating) or external observation stay for operator marking.
    if orch_evidence:
        for item_id, marker in _derive_orchestration_markers(
            orch_evidence
        ).items():
            auto.setdefault(item_id, marker)
        if (
            int(life_data.get("reminders_delivered_count", 0) or 0) >= 1
            and any(
                p.get("pipeline_name") == "continuity_check"
                and p.get("state") == "completed"
                for p in orch_evidence.get("pipeline_instances", [])
            )
        ):
            auto[66] = "x"

    if benchmark_state:
        if int(benchmark_state.get("memories_consolidated", 0) or 0) >= 1:
            auto.setdefault(48, "x")
        if int(benchmark_state.get("memories_dedup_merged", 0) or 0) >= 1:
            auto.setdefault(49, "x")
        if int(benchmark_state.get("entities_merged", 0) or 0) >= 1:
            auto.setdefault(50, "x")
        if int(benchmark_state.get("vault_entity_pages", 0) or 0) >= 1:
            auto.setdefault(55, "x")
        if int(benchmark_state.get("vault_moc_pages", 0) or 0) >= 1:
            auto.setdefault(56, "x")
        if int(benchmark_state.get("vault_sessions", 0) or 0) >= 1:
            auto.setdefault(57, "x")

    return auto


def _latest_benchmark_state(snapshots_dir: Path) -> dict[str, Any] | None:
    if not snapshots_dir.exists():
        return None
    bench_files = sorted(
        snapshots_dir.glob("*.benchmarks.json"),
        key=lambda p: p.stat().st_mtime,
    )
    if not bench_files:
        return None
    try:
        data = json.loads(bench_files[-1].read_text())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _current_vault_benchmark_state() -> dict[str, Any] | None:
    """Return direct vault counts for coverage items backed by files.

    Full acceptance runs usually collect these through a benchmark
    snapshot. Targeted mini-runs often do not, so the final report should
    still be able to score vault coverage from the current configured
    memory root instead of leaving real entity/session files invisible.
    """
    try:
        from kora_v2.core.settings import get_settings

        root = Path(get_settings().memory.kora_memory_path).expanduser()
    except Exception:
        root = _PROJECT_ROOT / "data" / "_KoraMemory"

    if not root.exists():
        return None

    def count_md(relative: str) -> int:
        folder = root / relative
        if not folder.exists():
            return 0
        return sum(1 for _ in folder.rglob("*.md"))

    entity_pages = (
        count_md("Entities/People")
        + count_md("Entities/Places")
        + count_md("Entities/Projects")
    )
    return {
        "vault_entity_pages": entity_pages,
        "vault_moc_pages": count_md("Maps of Content"),
        "vault_sessions": count_md("Sessions"),
    }


def _latest_snapshot_status(snapshots_dir: Path) -> dict[str, Any] | None:
    """Return the ``status`` dict from the newest snapshot, if any."""
    snaps = sorted(snapshots_dir.glob("*.json"))
    if not snaps:
        return None
    try:
        data = json.loads(snaps[-1].read_text())
    except Exception:
        return None
    status = data.get("status")
    return status if isinstance(status, dict) else None


def _snapshot_health_status(snap: dict[str, Any]) -> str:
    """Extract a concise health string from a snapshot JSON blob.

    Snapshots do not store a top-level ``health`` key — the doctor report
    is embedded under ``inspect_doctor`` (see harness ``cmd_snapshot``)
    and returns one of:
      * ``{"summary": "19/25 checks passed", "healthy": true, ...}`` on
        a working inspect endpoint, or
      * ``{"error": "HTTP Error 404: ..."}`` if the endpoint is broken.

    As a defensive secondary source, we also consult ``status.status``
    (``running``/``degraded``) and ``status.failed_subsystems``. The
    legacy code here read ``snap["health"]`` which does not exist,
    producing ``?`` on every row.
    """
    doctor = snap.get("inspect_doctor")
    if isinstance(doctor, dict):
        if doctor.get("error"):
            return f"doctor_unreachable:{str(doctor['error'])[:40]}"
        summary = doctor.get("summary")
        if summary:
            return str(summary)
        checks = doctor.get("checks")
        if isinstance(checks, list) and checks:
            passed = sum(1 for c in checks if c.get("passed"))
            return f"{passed}/{len(checks)} checks passed"

    status = snap.get("status")
    if isinstance(status, dict):
        daemon_status = status.get("status", "unknown")
        failed = status.get("failed_subsystems") or []
        if failed:
            return f"{daemon_status} ({len(failed)} failed: {','.join(failed)[:40]})"
        return str(daemon_status)

    return "?"


def _parse_coverage_file(coverage_path: Path) -> dict[int, str]:
    """Parse the operator-maintained coverage.md into ``{item_id: marker}``.

    The harness uses the convention ``- [x] 12. Description`` where the
    marker is one of ``x`` (satisfied), ``~`` (partial), or space
    (unsatisfied). This report previously pulled coverage from
    ``session_state["coverage"]`` which nothing populates — the operator
    edits coverage.md directly, so we must read that.
    """
    result: dict[int, str] = {}
    if not coverage_path.exists():
        return result
    try:
        text = coverage_path.read_text()
    except OSError:
        return result
    pattern = re.compile(r"^- \[([ x~])\]\s*(\d+)\.")
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if m:
            marker = m.group(1)
            try:
                item_id = int(m.group(2))
            except ValueError:
                continue
            result[item_id] = marker
    return result


def _with_startup_grace(started_at: str | None, *, seconds: int = 30) -> str | None:
    """Move the orchestration evidence boundary slightly before harness start.

    Startup-triggered pipelines can be seeded while ``cmd_start`` is still
    waiting for the daemon/harness handshake, a few seconds before the
    persisted acceptance ``started_at``. Without this grace window, the
    report misses current-run startup work like session_bridge_pruning even
    though it was created by this run.
    """
    if not started_at:
        return None
    try:
        parsed = datetime.fromisoformat(started_at)
    except ValueError:
        return started_at
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (parsed - timedelta(seconds=seconds)).isoformat()


def _extract_tool_usage(
    messages: list[dict[str, Any]],
    turn_traces: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Extract tool usage statistics from conversation messages."""
    tool_counts: dict[str, int] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            name = _normalize_tool_name(tc)
            if not name:
                continue
            tool_counts[name] = tool_counts.get(name, 0) + 1
    for trace in turn_traces or []:
        try:
            trace_tools = json.loads(str(trace.get("tools_invoked") or "[]"))
        except Exception:
            continue
        if not isinstance(trace_tools, list):
            continue
        for raw_name in trace_tools:
            name = _normalize_tool_name(raw_name)
            if not name:
                continue
            tool_counts[name] = tool_counts.get(name, 0) + 1

    # Buckets must mirror tests/acceptance/_harness_server.py:cmd_tool_usage_summary.
    # Test test_buckets_consistent enforces alignment; update both when adding tools.
    #
    # ``orchestration_tools`` replaces the retired ``auto_tools`` /
    # ``start_autonomous`` bucket. The 7 supervisor orchestration tools
    # below come from kora_v2/graph/dispatch.py SUPERVISOR_TOOLS (Phase 7.5b).
    life_tools = TOOL_BUCKETS["life_tools"]
    fs_tools = TOOL_BUCKETS["filesystem_tools"]
    # Legacy MCP tool names — kept for backward-compat; capability tools go in their own buckets
    mcp_tools = TOOL_BUCKETS["mcp_tools"]
    orchestration_tools = TOOL_BUCKETS["orchestration_tools"]
    memory_tools = TOOL_BUCKETS["memory_tools"]

    # Capability-pack buckets: any tool name starting with the pack prefix.
    capability_workspace = sorted(
        t for t in tool_counts if t.startswith("workspace.")
    )
    capability_browser = sorted(
        t for t in tool_counts if t.startswith("browser.")
    )
    capability_vault = sorted(
        t for t in tool_counts if t.startswith("vault.")
    )

    return {
        "tool_counts": tool_counts,
        "total": sum(tool_counts.values()),
        "unique": len(tool_counts),
        "life_management": sorted(t for t in tool_counts if t in life_tools),
        "filesystem": sorted(t for t in tool_counts if t in fs_tools),
        "mcp": sorted(t for t in tool_counts if t in mcp_tools),
        "orchestration": sorted(t for t in tool_counts if t in orchestration_tools),
        "memory": sorted(t for t in tool_counts if t in memory_tools),
        # AT3 placeholder: pipelines fire from triggers, not tool calls, so
        # they need a separate bucketing path (likely a query against the
        # pipeline_instances table). Empty list keeps the report shape
        # stable until AT3 wires it up.
        "pipelines": [],
        # Phase 9 capability-pack buckets
        "capability_workspace": capability_workspace,
        "capability_browser": capability_browser,
        "capability_vault": capability_vault,
    }


async def _build_capability_health() -> dict[str, Any]:
    """Invoke each registered capability pack's health_check() and return results.

    Returns a dict keyed by pack name containing serialisable CapabilityHealth data.
    Falls back gracefully if the capabilities package is not importable.
    """
    try:
        from kora_v2.capabilities.registry import get_all_capabilities
    except ImportError:
        return {}

    results: dict[str, Any] = {}
    packs = get_all_capabilities()
    for pack in packs:
        try:
            health = await pack.health_check()
            results[pack.name] = {
                "status": str(health.status),
                "summary": health.summary,
                "remediation": health.remediation,
                "details": health.details,
            }
        except Exception as exc:
            results[pack.name] = {
                "status": "error",
                "summary": f"health_check() raised: {exc}",
                "remediation": None,
                "details": {},
            }
    return results


async def _query_life_management(output_dir: Path) -> dict[str, Any]:
    """Query life management DB tables for the report.

    Uses the anchored ``_PROJECT_ROOT`` rather than walking up from
    ``output_dir``. The previous implementation computed the repo root
    via ``output_dir.parents[2]`` which pointed to ``/tmp/claude`` under
    the default acceptance output path — unrelated to the real project.
    The harness's ``cmd_life_management_check`` uses the same anchored
    constant and works correctly.
    """
    op_db = _PROJECT_ROOT / "data" / "operational.db"
    if not op_db.exists():
        # Legacy fallback: walk up from output_dir (kept for dev shells
        # that point KORA_ACCEPTANCE_DIR inside the project tree).
        for parent in output_dir.parents:
            candidate = parent / "data" / "operational.db"
            if candidate.exists():
                op_db = candidate
                break
        else:
            return {"available": False}

    try:
        import aiosqlite

        result: dict[str, Any] = {"available": True}
        async with aiosqlite.connect(str(op_db)) as db:
            db.row_factory = aiosqlite.Row

            for table, key in [
                ("medication_log", "medication"),
                ("meal_log", "meal"),
                ("reminders", "reminder"),
                ("quick_notes", "quick_note"),
                ("focus_blocks", "focus_block"),
            ]:
                try:
                    cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
                    row = await cursor.fetchone()
                    result[f"{key}_count"] = row[0] if row else 0
                except Exception:
                    result[f"{key}_count"] = 0
            try:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM energy_log WHERE source = 'self_report'"
                )
                row = await cursor.fetchone()
                result["energy_self_report_count"] = row[0] if row else 0
            except Exception:
                result["energy_self_report_count"] = 0
            try:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM reminders "
                    "WHERE status='delivered' OR delivered_at IS NOT NULL"
                )
                row = await cursor.fetchone()
                result["reminders_delivered_count"] = row[0] if row else 0
            except Exception:
                result["reminders_delivered_count"] = 0
            try:
                cursor = await db.execute("SELECT COUNT(*) FROM routines")
                row = await cursor.fetchone()
                result["routine_count"] = row[0] if row else 0
            except Exception:
                result["routine_count"] = 0
            try:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM support_profiles WHERE status = 'active'"
                )
                row = await cursor.fetchone()
                result["support_profile_count"] = row[0] if row else 0
            except Exception:
                result["support_profile_count"] = 0
            try:
                cursor = await db.execute(
                    """
                    SELECT COUNT(*) FROM domain_events
                    WHERE event_type IN (
                        'LIFE_EVENT_CORRECTED',
                        'WRONG_INFERENCE_REPAIRED',
                        'SUPPORT_PROFILE_CORRECTED',
                        'SUPPORT_PROFILE_SIGNAL_RECORDED'
                    )
                    """
                )
                row = await cursor.fetchone()
                result["correction_event_count"] = row[0] if row else 0
            except Exception:
                result["correction_event_count"] = 0

        return result
    except Exception:
        return {"available": False}


async def _query_policy_grants(since: str | None = None) -> list[dict[str, Any]]:
    """Return recorded permission decisions from operational.db.

    The WebSocket auth-test tracker is useful but volatile: if the
    harness restarts or the report is generated out of process, the raw
    ``auth_test_results`` list can be empty even though the daemon wrote
    durable decisions to ``permission_grants``. The DB table is the
    authoritative audit surface for the Policy Grants section.
    """
    op_db = _PROJECT_ROOT / "data" / "operational.db"
    if not op_db.exists():
        return []
    try:
        import aiosqlite

        async with aiosqlite.connect(str(op_db)) as db:
            db.row_factory = aiosqlite.Row
            if since:
                cursor = await db.execute(
                    "SELECT tool_name, risk_level, decision, granted_at "
                    "FROM permission_grants WHERE granted_at >= ? "
                    "ORDER BY granted_at ASC",
                    (since,),
                )
            else:
                cursor = await db.execute(
                    "SELECT tool_name, risk_level, decision, granted_at "
                    "FROM permission_grants ORDER BY granted_at ASC"
                )
            rows = await cursor.fetchall()
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    for row in rows:
        decision = str(row["decision"] or "").lower()
        approved: bool | None
        if decision == "approved":
            approved = True
        elif decision == "denied":
            approved = False
        else:
            approved = None
        results.append(
            {
                "tool": row["tool_name"],
                "risk": row["risk_level"],
                "approved": approved,
                "decision": decision,
                "ts": row["granted_at"],
                "source": "permission_grants",
            }
        )
    return results


async def _query_orchestration_evidence(since: str | None = None) -> dict[str, Any]:
    """Query orchestration tables for evidence of Phase 7.5/8 behaviours.

    Returns a summary dict used by :func:`_auto_mark_coverage` to credit
    items 24–67 based on real DB state. Missing tables (e.g. pre-7.5
    DB) return empty fields so the caller degrades gracefully.
    """
    op_db = _PROJECT_ROOT / "data" / "operational.db"
    proj_db = _PROJECT_ROOT / "data" / "projection.db"
    result: dict[str, Any] = {"available": False}
    if not op_db.exists():
        return result
    try:
        import aiosqlite

        result["available"] = True
        async with aiosqlite.connect(str(op_db)) as db:
            db.row_factory = aiosqlite.Row

            async def _scalar(q: str, params: tuple[Any, ...] = ()) -> int:
                try:
                    cur = await db.execute(q, params)
                    row = await cur.fetchone()
                    return int(row[0]) if row and row[0] is not None else 0
                except Exception:
                    return 0

            async def _rows(
                q: str, params: tuple[Any, ...] = ()
            ) -> list[dict[str, Any]]:
                try:
                    cur = await db.execute(q, params)
                    return [dict(r) for r in await cur.fetchall()]
                except Exception:
                    return []

            # SystemStatePhase transitions observed.
            since_phase = " WHERE transitioned_at >= ?" if since else ""
            phase_rows = await _rows(
                f"SELECT DISTINCT new_phase FROM system_state_log{since_phase}",
                (since,) if since else (),
            )
            result["system_phases_observed"] = sorted(
                {r["new_phase"] for r in phase_rows if r.get("new_phase")}
            )

            # Pipeline instance summary (by pipeline name + state).
            since_pipeline = " WHERE started_at >= ?" if since else ""
            pipeline_rows = await _rows(
                "SELECT id, pipeline_name, state, intent_duration, "
                "  parent_session_id, completion_reason, working_doc_path, goal "
                f"FROM pipeline_instances{since_pipeline}",
                (since,) if since else (),
            )
            result["pipeline_instances"] = pipeline_rows

            # Worker task summary (stage/preset/state/outcome). Outcome
            # summaries are needed to distinguish "stage ran" from
            # "stage actually merged/deduped/resolved something".
            if since:
                task_rows = await _rows(
                    "SELECT w.id, w.pipeline_instance_id, w.stage_name, w.state, "
                    "  w.task_preset, w.result_summary, w.error_message, "
                    "  w.cancellation_requested "
                    "FROM worker_tasks w "
                    "JOIN pipeline_instances p ON p.id = w.pipeline_instance_id "
                    "WHERE p.started_at >= ?",
                    (since,),
                )
            else:
                task_rows = await _rows(
                    "SELECT id, pipeline_instance_id, stage_name, state, "
                    "  task_preset, result_summary, error_message, "
                    "  cancellation_requested "
                    "FROM worker_tasks"
                )
            result["worker_tasks"] = task_rows

            # Work ledger event types by pipeline.
            since_ledger = " WHERE timestamp >= ?" if since else ""
            ledger_rows = await _rows(
                "SELECT event_type, pipeline_instance_id, reason, trigger_name, "
                "worker_task_id, metadata_json "
                f"FROM work_ledger{since_ledger}",
                (since,) if since else (),
            )
            result["ledger_events"] = ledger_rows

            trace_where = " WHERE started_at >= ?" if since else ""
            result["turn_traces"] = await _rows(
                "SELECT user_input, tool_call_count, tools_invoked, "
                f"final_output, succeeded FROM turn_traces{trace_where}",
                (since,) if since else (),
            )

            limiter_rows = await _rows(
                "SELECT class, COUNT(*) AS cnt FROM request_limiter_log "
                "GROUP BY class"
            )
            result["request_limiter_by_class"] = {
                r.get("class"): int(r.get("cnt") or 0)
                for r in limiter_rows
                if r.get("class")
            }

            # Notifications.
            notif_where = " WHERE delivered_at >= ?" if since else ""
            result["notification_count"] = await _scalar(
                f"SELECT COUNT(*) FROM notifications{notif_where}",
                (since,) if since else (),
            )
            result["delivered_notifications"] = await _scalar(
                "SELECT COUNT(*) FROM notifications "
                "WHERE delivered_at IS NOT NULL"
                + (" AND delivered_at >= ?" if since else ""),
                (since,) if since else (),
            )
            result["reminder_count"] = await _scalar(
                "SELECT COUNT(*) FROM reminders"
            )
            result["notifications"] = await _rows(
                "SELECT delivery_tier, template_id, reason, delivered_at "
                "FROM notifications"
                + (" WHERE delivered_at >= ?" if since else ""),
                (since,) if since else (),
            )

            # Runtime pipelines (user-registered).
            runtime_where = " WHERE created_at >= ?" if since else ""
            result["runtime_pipeline_count"] = await _scalar(
                f"SELECT COUNT(*) FROM runtime_pipelines{runtime_where}",
                (since,) if since else (),
            )

            # Open decisions are deliberately age-based. Acceptance may
            # backdate ``posed_at`` to prove DECISION_PENDING_3D, so do not
            # filter this count by run start or the report hides the very
            # decision it is trying to score.
            result["open_decision_count"] = await _scalar(
                "SELECT COUNT(*) FROM open_decisions",
            )

            # Session transcripts and signal queue (memory pipeline inputs).
            transcripts_where = " WHERE created_at >= ?" if since else ""
            result["session_transcripts"] = await _scalar(
                f"SELECT COUNT(*) FROM session_transcripts{transcripts_where}",
                (since,) if since else (),
            )
            signals_where = " WHERE created_at >= ?" if since else ""
            result["signal_queue_count"] = await _scalar(
                f"SELECT COUNT(*) FROM signal_queue{signals_where}",
                (since,) if since else (),
            )
    except Exception:  # noqa: BLE001
        pass

    # Projection DB evidence (memory notes / entities).
    if proj_db.exists():
        try:
            import aiosqlite as _aiosqlite

            async with _aiosqlite.connect(str(proj_db)) as db:
                db.row_factory = _aiosqlite.Row
                for table, key in [
                    ("notes", "notes_total"),
                    ("entities", "entities_total"),
                ]:
                    try:
                        cur = await db.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        )
                        row = await cur.fetchone()
                        result[key] = int(row[0]) if row else 0
                    except Exception:
                        result[key] = 0
        except Exception:  # noqa: BLE001
            pass

    return result


def _derive_orchestration_markers(
    orch: dict[str, Any],
) -> dict[int, str]:
    """Turn orchestration evidence into ``{item_id: marker}`` credits.

    Only runs evidence-based items 24–67; items requiring semantic
    judgment or external observation stay unmarked.
    """
    marks: dict[int, str] = {}
    if not orch.get("available"):
        return marks

    pipelines = orch.get("pipeline_instances") or []
    tasks = orch.get("worker_tasks") or []
    ledger = orch.get("ledger_events") or []
    turn_traces = orch.get("turn_traces") or []
    notifications = orch.get("notifications") or []
    phases = set(orch.get("system_phases_observed") or [])

    def pipes_by_name(name: str) -> list[dict[str, Any]]:
        return [p for p in pipelines if p.get("pipeline_name") == name]

    def completed(name: str) -> bool:
        return any(
            p.get("state") == "completed"
            for p in pipes_by_name(name)
        )

    def any_state(name: str, states: set[str]) -> bool:
        return any(
            p.get("state") in states for p in pipes_by_name(name)
        )

    def _task_summary_positive(stage_name: str, patterns: tuple[str, ...]) -> bool:
        for task in tasks:
            if (
                task.get("stage_name") != stage_name
                or task.get("state") != "completed"
            ):
                continue
            summary = str(task.get("result_summary") or "")
            for pattern in patterns:
                match = re.search(pattern, summary, flags=re.IGNORECASE)
                if match and int(match.group(1)) > 0:
                    return True
        return False

    def _pipeline_completed_clean(
        pipeline: dict[str, Any],
        *,
        require_substantive_doc: bool = False,
    ) -> bool:
        if pipeline.get("state") != "completed":
            return False
        if require_substantive_doc and not _pipeline_has_substantive_doc(pipeline):
            return False
        return not _pipeline_has_cancellation_evidence(pipeline, tasks, ledger)

    # 24: required idle progression observed.
    required_idle_phases = {
        "conversation",
        "active_idle",
        "light_idle",
        "deep_idle",
    }
    seen_required = phases & required_idle_phases
    if required_idle_phases.issubset(phases):
        marks[24] = "x"
    elif len(seen_required) >= 2:
        marks[24] = "~"

    # 25: long_background task dispatch — a worker_task with
    # preset='long_background' exists.
    if any(t.get("task_preset") == "long_background" for t in tasks):
        marks[25] = "x"

    # 26: working doc exists is filesystem evidence — handled inline
    # below (tested via pipeline presence and working_doc frontmatter
    # status). A pipeline instance having any task is sufficient AT3
    # credit.
    if any(p.get("state") in {"running", "completed"} for p in pipelines):
        marks[26] = "x"

    # 27 adaptive mutation — evidence: proposed_new_tasks or
    # user_added stage tasks exist.
    if any(t.get("stage_name") == "user_added" for t in tasks):
        marks[27] = "x"
        marks[31] = "x"

    # 28 Kora-judged completion: a user-facing completed pipeline should
    # have either a substantive working doc or be a non-research core
    # pipeline with task evidence.
    if any(
        _pipeline_completed_clean(
            p,
            require_substantive_doc=p.get("pipeline_name") == "proactive_research",
        )
        for p in pipelines
    ):
        marks[28] = "x"

    # 30 cancel_task evidence: the disposable cancel-probe must be
    # cancelled without collateral cancellation on the real research task.
    if _cancel_probe_isolated(pipelines, tasks, ledger):
        marks[30] = "x"
    elif any(evt.get("event_type") == "task_cancelled" for evt in ledger):
        marks[30] = "~"

    # 32 conversation reserve preserved under background load. Full credit
    # needs both background limiter traffic and a successful foreground turn
    # after rate-pressure evidence appears.
    limiter_by_class = orch.get("request_limiter_by_class") or {}
    any_successful_turn = any(
        int(trace.get("succeeded") or 0) == 1 for trace in turn_traces
    )
    if int(limiter_by_class.get("background", 0) or 0) > 0 and any_successful_turn:
        marks[32] = "~"

    # 33 rate-limit pause+resume.
    has_paused = any(
        evt.get("event_type") == "task_paused"
        and (evt.get("reason") or "").startswith("rate")
        for evt in ledger
    )
    has_resumed = any(
        evt.get("event_type") == "task_resumed"
        and (evt.get("reason") or "").startswith("rate")
        for evt in ledger
    )
    if has_paused and has_resumed:
        marks[33] = "x"
        if any_successful_turn:
            marks[32] = "x"
    elif has_paused:
        marks[33] = "~"

    # 34 templated fallback: the limiter hook sends a templated
    # rate_limit_paused notification. Score from NotificationGate rows, not
    # only from generic notification totals.
    if any(
        n.get("delivery_tier") == "templated"
        and n.get("template_id") == "rate_limit_paused"
        for n in notifications
    ):
        marks[34] = "x"

    # 36: >=2 long-intent pipelines completed.
    long_completed = [
        p for p in pipelines
        if p.get("intent_duration") == "long"
        and p.get("state") == "completed"
    ]
    if len(long_completed) >= 2:
        marks[36] = "x"
    elif len(long_completed) >= 1:
        marks[36] = "~"

    # 39 sequence_complete linking post_session_memory -> post_memory_vault.
    # This item verifies the trigger handoff. The vault pipeline can still
    # fail later in its own stage-specific items, so do not require final
    # post_memory_vault completion here.
    has_sequence_trigger = any(
        evt.get("event_type") == "trigger_fired"
        and (
            evt.get("trigger_name") == "post_memory_vault.seq.post_session_memory"
            or "sequence_complete" in str(evt.get("metadata_json") or "")
        )
        for evt in ledger
    )
    if completed("post_session_memory") and has_sequence_trigger:
        marks[39] = "x"
    elif completed("post_session_memory") and any_state(
        "post_memory_vault", {"running", "completed", "failed"}
    ):
        marks[39] = "~"

    # 40 WAKE_UP_WINDOW
    if "wake_up_window" in phases and completed("wake_up_preparation"):
        marks[40] = "x"
    elif "wake_up_window" in phases:
        marks[40] = "~"

    # 41 / 62 contextual_engagement
    if completed("contextual_engagement"):
        marks[41] = "x"
        marks[62] = "x"

    # 38 continuity_check mid-session evidence. Notification delivery is
    # covered by 66; completion itself proves the inline pipeline fired.
    if completed("continuity_check"):
        marks[38] = "x"

    insight_triggered = any(
        evt.get("event_type") == "trigger_fired"
        and (
            "INSIGHT_AVAILABLE" in str(evt.get("trigger_name") or "")
            or "INSIGHT_AVAILABLE" in str(evt.get("metadata_json") or "")
            or "EMOTION_SHIFT_DETECTED" in str(evt.get("trigger_name") or "")
            or "EMOTION_SHIFT_DETECTED" in str(evt.get("metadata_json") or "")
            or "MEMORY_STORED" in str(evt.get("trigger_name") or "")
            or "MEMORY_STORED" in str(evt.get("metadata_json") or "")
        )
        for evt in ledger
    )
    pattern_nudge_sent = any(
        n.get("delivery_tier") == "templated"
        and n.get("template_id") == "pattern_nudge"
        for n in notifications
    )

    # 42 proactive_pattern_scan consumes INSIGHT_AVAILABLE evidence. 59
    # specifically requires the resulting pattern nudge to hit NotificationGate.
    if completed("proactive_pattern_scan") and insight_triggered:
        marks[42] = "x"
    elif completed("proactive_pattern_scan"):
        marks[42] = "~"
    if completed("proactive_pattern_scan") and pattern_nudge_sent:
        marks[59] = "x"

    # 43 open decisions
    has_decision_aging = any(
        evt.get("event_type") == "trigger_fired"
        and evt.get("trigger_name") == "DECISION_PENDING_3D"
        for evt in ledger
    )
    if orch.get("open_decision_count", 0) > 0 and has_decision_aging:
        marks[43] = "x"
    elif orch.get("open_decision_count", 0) > 0:
        marks[43] = "~"

    # 44 runtime pipelines
    routine_runtime_fired = any(
        str(p.get("pipeline_name") or "").startswith("routine_")
        and p.get("state") in {"running", "completed"}
        for p in pipelines
    )
    if orch.get("runtime_pipeline_count", 0) > 0 and routine_runtime_fired:
        marks[44] = "x"
    elif orch.get("runtime_pipeline_count", 0) > 0:
        marks[44] = "~"

    # 45 WorkLedger answers.
    if len(ledger) > 0:
        marks[45] = "x"

    # 46 long-background templated ack: GraphTurnRunner records the final
    # output and invoked tool names for the turn. The runtime short-circuit
    # path makes the acknowledgement the final output immediately after the
    # dispatch tool result instead of asking the provider for another reply.
    for trace in turn_traces:
        tools_raw = str(trace.get("tools_invoked") or "")
        output = str(trace.get("final_output") or "")
        if (
            "decompose_and_dispatch" in tools_raw
            and output.startswith("I'll keep that running in the background")
        ):
            marks[46] = "x"
            break

    # Memory Steward stages (47-51)
    post_session_stages = {
        t.get("stage_name")
        for t in tasks
        if any(
            p.get("id") == t.get("pipeline_instance_id")
            and p.get("pipeline_name") == "post_session_memory"
            for p in pipelines
        )
        and t.get("state") == "completed"
    }
    if "extract" in post_session_stages:
        marks[47] = "x"
    # 48-50 need outcome evidence from benchmark/projection deltas; stage
    # completion alone only proves the code path ran.
    if _task_summary_positive("consolidate", (r"(\d+)\s+groups?\s+merged",)):
        marks[48] = "x"
    if _task_summary_positive(
        "dedup",
        (r"(\d+)\s+duplicates?\s+removed", r"(\d+)\s+deduped"),
    ):
        marks[49] = "x"
    if _task_summary_positive("entities", (r"(\d+)\s+merged",)):
        marks[50] = "x"
    if completed("weekly_adhd_profile"):
        marks[51] = "x"

    # Vault Organizer stages (52-57)
    vault_stages = {
        t.get("stage_name")
        for t in tasks
        if any(
            p.get("id") == t.get("pipeline_instance_id")
            and p.get("pipeline_name") == "post_memory_vault"
            for p in pipelines
        )
        and t.get("state") == "completed"
    }
    if "reindex" in vault_stages:
        marks[52] = "x"
    if "structure" in vault_stages:
        marks[53] = "x"
    if "links" in vault_stages:
        marks[54] = "x"
    # 56 is scored from vault benchmark/page-count evidence. A completed
    # moc_sessions stage that writes zero MOC pages is not enough.

    # 58 ContextEngine insight — INSIGHT_AVAILABLE event surfaced and was
    # consumed by proactive_pattern_scan.
    if completed("proactive_pattern_scan") and insight_triggered:
        marks[58] = "x"
    elif completed("proactive_pattern_scan"):
        marks[58] = "~"

    # 60 anticipatory_prep
    if completed("anticipatory_prep"):
        marks[60] = "x"

    # 61 proactive_research
    if any(
        p.get("pipeline_name") == "proactive_research"
        and _pipeline_completed_clean(p, require_substantive_doc=True)
        and _pipeline_doc_matches_goal(p)
        for p in pipelines
    ):
        marks[61] = "x"
    elif any(
        p.get("pipeline_name") == "proactive_research"
        and p.get("state") in {
            "running",
            "paused_for_rate_limit",
            "paused_for_state",
        }
        and not _pipeline_has_cancellation_evidence(p, tasks, ledger)
        and (p.get("working_doc_path") or any(
            t.get("pipeline_instance_id") == p.get("id")
            and t.get("stage_name") == "user_added"
            for t in tasks
        ))
        for p in pipelines
    ):
        marks[61] = "~"
    elif any(
        p.get("pipeline_name") == "proactive_research"
        and p.get("state") == "completed"
        and not _pipeline_has_cancellation_evidence(p, tasks, ledger)
        and (
            _pipeline_has_degraded_research_output(p)
            or any(
                t.get("pipeline_instance_id") == p.get("id")
                and t.get("stage_name") == "user_added"
                and t.get("state") == "completed"
                for t in tasks
            )
        )
        for p in pipelines
    ):
        marks[61] = "~"

    # 63 commitment_tracking
    if completed("commitment_tracking"):
        marks[63] = "x"

    # 64 stuck_detection
    if completed("stuck_detection"):
        marks[64] = "x"

    # 65 connection_making
    if completed("connection_making"):
        marks[65] = "x"

    # 66 reminders -> continuity_check. Partial credit requires an actual
    # reminder row; unrelated routine notifications are not enough.
    if (
        completed("continuity_check")
        and int(orch.get("reminder_count", 0) or 0) > 0
    ):
        marks[66] = "~"

    # 67 wake_up briefing delivered
    if completed("wake_up_preparation") and "wake_up_window" in phases:
        marks[67] = "x"
    elif completed("wake_up_preparation"):
        marks[67] = "~"

    return marks


def _has_user_pipeline_with_tasks(orch: dict[str, Any]) -> bool:
    pipelines = orch.get("pipeline_instances") or []
    tasks = orch.get("worker_tasks") or []
    user_pipeline_ids = {
        p.get("id")
        for p in pipelines
        if p.get("parent_session_id") and p.get("state") in {"running", "completed"}
    }
    return any(t.get("pipeline_instance_id") in user_pipeline_ids for t in tasks)


def _pipeline_has_cancellation_evidence(
    pipeline: dict[str, Any],
    tasks: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
) -> bool:
    pipeline_id = pipeline.get("id")
    if not pipeline_id:
        return False
    pipeline_tasks = [
        task for task in tasks if task.get("pipeline_instance_id") == pipeline_id
    ]
    task_ids = {str(task.get("id")) for task in pipeline_tasks if task.get("id")}
    if any(
        task.get("state") == "cancelled"
        or int(task.get("cancellation_requested") or 0) > 0
        for task in pipeline_tasks
    ):
        return True
    return any(
        evt.get("event_type") == "task_cancelled"
        and (
            evt.get("pipeline_instance_id") == pipeline_id
            or str(evt.get("worker_task_id") or "") in task_ids
        )
        for evt in ledger
    )


def _cancel_probe_isolated(
    pipelines: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
) -> bool:
    probe_ids = {
        str(p.get("id"))
        for p in pipelines
        if _is_cancel_probe_pipeline(p)
    }
    if not probe_ids:
        probe_ids = {
            str(p.get("id"))
            for p in pipelines
            if p.get("pipeline_name") != "proactive_research"
            and (
                p.get("state") == "cancelled"
                or _pipeline_has_cancellation_evidence(p, tasks, ledger)
            )
            and (
                p.get("working_doc_path")
                or any(
                    task.get("pipeline_instance_id") == p.get("id")
                    and task.get("state") == "cancelled"
                    for task in tasks
                )
            )
        }
    if not probe_ids:
        return False
    probe_cancelled = any(
        str(p.get("id")) in probe_ids
        and p.get("state") == "cancelled"
        for p in pipelines
    ) or any(
        task.get("pipeline_instance_id") in probe_ids
        and (
            task.get("state") == "cancelled"
            or int(task.get("cancellation_requested") or 0) > 0
        )
        for task in tasks
    )
    if not probe_cancelled:
        return False
    for pipeline in pipelines:
        if _is_cancel_probe_pipeline(pipeline):
            continue
        if pipeline.get("pipeline_name") == "proactive_research" and (
            _pipeline_has_cancellation_evidence(pipeline, tasks, ledger)
            or pipeline.get("state") == "cancelled"
        ):
            return False
    return True


def _is_cancel_probe_pipeline(pipeline: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(pipeline.get(key) or "").lower()
        for key in (
            "id",
            "pipeline_name",
            "goal",
            "working_doc_path",
        )
    ).replace("_", "-")
    return any(
        signal in haystack
        for signal in (
            "cancel-probe",
            "phone-call fallback",
            "phone fallback",
            "pharmacy-phone-fallback",
            "noisy helper",
            "broad helper",
            "practical life-admin checklist",
        )
    )


def _deep_idle_housekeeping_marker(orch: dict[str, Any]) -> bool:
    pipelines = orch.get("pipeline_instances") or []
    phases = set(orch.get("system_phases_observed") or [])
    completed_housekeeping = {
        p.get("pipeline_name")
        for p in pipelines
        if p.get("state") == "completed"
        and p.get("pipeline_name") in {"session_bridge_pruning", "skill_refinement"}
    }
    return "deep_idle" in phases and {
        "session_bridge_pruning",
        "skill_refinement",
    }.issubset(completed_housekeeping)


def _pipeline_has_substantive_doc(pipeline: dict[str, Any]) -> bool:
    path_raw = pipeline.get("working_doc_path")
    if not path_raw:
        return False
    path = Path(str(path_raw))
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False

    try:
        from kora_v2.runtime.orchestration.working_doc import (
            parse_frontmatter,
            parse_sections,
        )

        _, body = parse_frontmatter(text)
        sections = parse_sections(body)
    except Exception:  # noqa: BLE001
        sections = {}

    def _substantive(value: str | None, *, min_len: int = 8) -> bool:
        cleaned = re.sub(r"(?m)^#+\s+.*$", "", value or "")
        cleaned = re.sub(r"(?m)^[-*]\s*$", "", cleaned)
        return len(cleaned.strip()) >= min_len

    if not sections:
        lower = text.lower()
        return (
            "summary" in lower
            and "findings" in lower
            and len(text.strip()) >= 300
            and "summary`/`findings` are blank" not in lower
        )

    summary_ok = _substantive(sections.get("Summary"))
    findings_ok = _substantive(sections.get("Findings"))
    if summary_ok and findings_ok:
        return True

    # Older proactive research writes embedded reports as top-level
    # non-canonical sections after the scaffold. That still represents
    # substantive user-visible progress, as long as it is not just a
    # completion ledger line.
    noncanonical_chunks = [
        body
        for name, body in sections.items()
        if name
        not in {
            "Goal",
            "Summary",
            "Current Plan",
            "Findings",
            "Notes",
            "Open Questions",
            "Dead Ends",
            "Completed Tasks Log",
            "Completion",
        }
    ]
    noncanonical_text = "\n\n".join(noncanonical_chunks).lower()
    if not summary_ok and not _substantive(noncanonical_text, min_len=80):
        return False
    research_terms = (
        "research",
        "finding",
        "comparison",
        "approach",
        "tradeoff",
        "recommendation",
        "source verification pending",
    )
    return any(term in noncanonical_text for term in research_terms)


def _pipeline_has_degraded_research_output(pipeline: dict[str, Any]) -> bool:
    path_raw = pipeline.get("working_doc_path")
    if not path_raw:
        return False
    path = Path(str(path_raw))
    try:
        text = path.read_text(encoding="utf-8").lower()
    except OSError:
        return False
    return any(
        marker in text
        for marker in (
            "source verification pending",
            "candidate to verify",
            "live search",
            "search was degraded",
            "web search was degraded",
            "could not run live web",
            "not live scraping",
        )
    )


def _pipeline_doc_matches_goal(pipeline: dict[str, Any]) -> bool:
    path_raw = pipeline.get("working_doc_path")
    if not path_raw:
        return False
    path = Path(str(path_raw))
    try:
        text = path.read_text(encoding="utf-8").lower()
    except OSError:
        return False
    goal = str(pipeline.get("goal") or "").lower()
    required_terms = [
        term for term in (
            "obsidian",
            "logseq",
            "anytype",
            "local-first",
            "local first",
            "privacy",
        )
        if term in goal
    ]
    if not required_terms:
        return True
    hits = sum(1 for term in required_terms if term in text)
    return hits >= max(1, min(2, len(required_terms)))


def _long_autonomous_marker(orch: dict[str, Any]) -> str | None:
    pipelines = orch.get("pipeline_instances") or []
    tasks = orch.get("worker_tasks") or []
    ledger = orch.get("ledger_events") or []
    long_pipeline_ids = {
        p.get("id")
        for p in pipelines
        if p.get("intent_duration") == "long"
        and p.get("parent_session_id")
    }
    if not long_pipeline_ids:
        return None
    completed_long = [
        p
        for p in pipelines
        if p.get("id") in long_pipeline_ids and p.get("state") == "completed"
    ]
    clean_completed_long = [
        p
        for p in completed_long
        if not _pipeline_has_cancellation_evidence(p, tasks, ledger)
    ]
    if clean_completed_long and any(
        p.get("pipeline_name") != "proactive_research"
        or _pipeline_has_substantive_doc(p)
        for p in clean_completed_long
    ):
        return "x"
    if completed_long:
        return "~"
    has_task = any(t.get("pipeline_instance_id") in long_pipeline_ids for t in tasks)
    has_ledger = any(
        evt.get("pipeline_instance_id") in long_pipeline_ids
        and evt.get("event_type") in {
            "task_started",
            "task_progress",
            "task_checkpointed",
            "task_completed",
            "pipeline_completed",
        }
        for evt in ledger
    )
    if has_task and has_ledger:
        return "~"
    return None


async def _query_autonomous_state() -> dict[str, Any]:
    """Query live autonomous state directly from operational.db.

    Phase 7.5 moved authoritative autonomous state onto the
    orchestration tables (``pipeline_instances`` + ``worker_tasks`` +
    ``work_ledger``). This function now prefers those tables and falls
    back to the retired ``autonomous_plans`` / ``autonomous_updates`` /
    ``autonomous_checkpoints`` view only when the new tables are empty,
    so runs that pre-date 7.5 still produce a legible report.
    """
    op_db = _PROJECT_ROOT / "data" / "operational.db"
    if not op_db.exists():
        return {"available": False}
    try:
        import aiosqlite

        async with aiosqlite.connect(str(op_db)) as db:
            db.row_factory = aiosqlite.Row

            plans: list[dict[str, Any]] = []
            plans_query_error: str | None = None
            try:
                cur = await db.execute(
                    """SELECT id, goal, status,
                              COALESCE(request_count, 0) AS request_count,
                              COALESCE(token_estimate, 0) AS token_estimate,
                              created_at,
                              COALESCE(updated_at, completed_at) AS updated_at
                       FROM autonomous_plans
                       ORDER BY created_at DESC
                       LIMIT 10"""
                )
                plans = [dict(r) for r in await cur.fetchall()]
            except Exception as exc:
                plans = []
                plans_query_error = str(exc)

            checkpoint_count = 0
            try:
                cur = await db.execute("SELECT COUNT(*) FROM autonomous_checkpoints")
                row = await cur.fetchone()
                checkpoint_count = row[0] if row else 0
            except Exception:
                pass
            try:
                cur = await db.execute(
                    "SELECT COUNT(*) FROM work_ledger "
                    "WHERE event_type='task_checkpointed'"
                )
                row = await cur.fetchone()
                checkpoint_count = max(checkpoint_count, row[0] if row else 0)
            except Exception:
                pass

            total_items = 0
            try:
                cur = await db.execute("SELECT COUNT(*) FROM items")
                row = await cur.fetchone()
                total_items = row[0] if row else 0
            except Exception:
                pass

            # Items grouped by status (for the snapshot summary line).
            items_by_status: dict[str, int] = {}
            try:
                cur = await db.execute(
                    "SELECT status, COUNT(*) AS cnt FROM items GROUP BY status"
                )
                items_by_status = {
                    r["status"]: r["cnt"] for r in await cur.fetchall()
                }
            except Exception:
                pass

            # Fallback: if autonomous_plans is empty but items reference
            # plans, synthesise a plan view grouped by autonomous_plan_id.
            derived_plans: list[dict[str, Any]] = []
            if not plans:
                try:
                    cur = await db.execute(
                        """SELECT i.autonomous_plan_id AS id,
                                  root.title           AS goal,
                                  root.status          AS status,
                                  MIN(i.created_at)    AS created_at,
                                  MAX(i.updated_at)    AS updated_at,
                                  COUNT(*)             AS step_count,
                                  SUM(CASE WHEN i.status='completed' THEN 1 ELSE 0 END) AS completed
                           FROM items i
                           LEFT JOIN items root ON root.id = i.autonomous_plan_id
                           WHERE i.autonomous_plan_id IS NOT NULL
                           GROUP BY i.autonomous_plan_id
                           ORDER BY MIN(i.created_at) DESC
                           LIMIT 10"""
                    )
                    derived_plans = [dict(r) for r in await cur.fetchall()]
                except Exception:
                    derived_plans = []

            # Autonomous updates (checkpoint / completion summaries).
            updates: list[dict[str, Any]] = []
            try:
                cur = await db.execute(
                    """SELECT plan_id, update_type, summary, created_at
                       FROM autonomous_updates
                       ORDER BY created_at DESC
                       LIMIT 20"""
                )
                updates = [dict(r) for r in await cur.fetchall()]
            except Exception:
                updates = []

        # Phase 7.5 orchestration view — the primary truth source.
        orchestration_plans: list[dict[str, Any]] = []
        orchestration_checkpoint_count = 0
        orchestration_tasks_by_state: dict[str, int] = {}
        orchestration_events_by_type: dict[str, int] = {}
        try:
            async with aiosqlite.connect(str(op_db)) as db:
                db.row_factory = aiosqlite.Row
                try:
                    cur = await db.execute(
                        """SELECT id, pipeline_name, goal, state,
                                  started_at,
                                  COALESCE(updated_at, started_at) AS updated_at,
                                  completed_at, completion_reason,
                                  parent_session_id
                           FROM pipeline_instances
                           ORDER BY started_at DESC
                           LIMIT 10"""
                    )
                    orchestration_plans = [dict(r) for r in await cur.fetchall()]
                except Exception:
                    orchestration_plans = []

                try:
                    cur = await db.execute(
                        "SELECT COUNT(*) FROM worker_tasks "
                        "WHERE checkpoint_blob IS NOT NULL"
                    )
                    row = await cur.fetchone()
                    orchestration_checkpoint_count = row[0] if row else 0
                except Exception:
                    pass

                try:
                    cur = await db.execute(
                        "SELECT state, COUNT(*) AS cnt "
                        "FROM worker_tasks GROUP BY state"
                    )
                    orchestration_tasks_by_state = {
                        r["state"]: r["cnt"] for r in await cur.fetchall()
                    }
                except Exception:
                    pass

                try:
                    cur = await db.execute(
                        "SELECT event_type, COUNT(*) AS cnt "
                        "FROM work_ledger GROUP BY event_type"
                    )
                    orchestration_events_by_type = {
                        r["event_type"]: r["cnt"] for r in await cur.fetchall()
                    }
                except Exception:
                    pass
        except Exception:
            orchestration_plans = []

        # Prefer the Phase 7.5 orchestration view when it has data, so the
        # report reflects what the dispatcher actually ran. Fall back to
        # the legacy autonomous_plans view for older runs.
        if orchestration_plans:
            unified_plans = [
                {
                    "id": p["id"],
                    "goal": p.get("goal"),
                    "status": p.get("state"),
                    "created_at": p.get("started_at"),
                    "updated_at": p.get("updated_at"),
                    "completion_reason": p.get("completion_reason"),
                }
                for p in orchestration_plans
            ]
            plans_source = "pipeline_instances"
        else:
            unified_plans = plans or derived_plans
            plans_source = (
                "autonomous_plans" if plans else (
                    "derived_from_items" if derived_plans else "empty"
                )
            )

        active_plans = [
            p for p in unified_plans
            if p.get("status") not in (
                "completed", "cancelled", "failed"
            )
        ]

        return {
            "available": True,
            "plans": unified_plans,
            "plans_source": plans_source,
            "plans_query_error": plans_query_error,
            "active_plan_count": len(active_plans),
            "checkpoint_count": (
                orchestration_checkpoint_count or checkpoint_count
            ),
            "total_items": total_items,
            "items_by_status": items_by_status,
            "updates": updates,
            # Phase 7.5 orchestration fields — authoritative when present.
            "orchestration": {
                "pipeline_instances": orchestration_plans,
                "tasks_by_state": orchestration_tasks_by_state,
                "events_by_type": orchestration_events_by_type,
                "checkpoint_count": orchestration_checkpoint_count,
            },
        }
    except Exception:
        return {"available": False}


def _render_benchmarks_dashboard(snapshots_dir: Path) -> list[str]:
    """Render the AT4 Benchmarks dashboard from sidecar JSON files.

    Reads ``<snapshot>.benchmarks.json`` sidecars under ``snapshots_dir``
    (mtime-sorted), renders the latest as a multi-table markdown
    dashboard, and appends a trend mini-table if more than one sidecar
    is present. Returns an empty list when no sidecar exists so the
    caller can append unconditionally.
    """
    if not snapshots_dir.exists():
        return []
    bench_files = sorted(
        snapshots_dir.glob("*.benchmarks.json"),
        key=lambda p: p.stat().st_mtime,
    )
    if not bench_files:
        return []

    latest_path = bench_files[-1]
    try:
        bench = json.loads(latest_path.read_text())
    except Exception:
        return []
    if not isinstance(bench, dict):
        return []

    out: list[str] = []
    out.append(f"\n## Benchmarks (latest: {latest_path.stem})")

    # ── Latency ──
    out.append("\n### Latency")
    out.append("| Metric | Value |")
    out.append("| ------ | ----- |")
    out.append(f"| Responses | {int(bench.get('response_count', 0) or 0)} |")
    out.append(
        f"| p50 latency | {bench.get('response_latency_p50_ms', 0)} ms |"
    )
    out.append(
        f"| p95 latency | {bench.get('response_latency_p95_ms', 0)} ms |"
    )

    # ── Token usage ──
    prompt = int(bench.get("total_prompt_tokens", 0) or 0)
    completion = int(bench.get("total_completion_tokens", 0) or 0)
    mean_resp = bench.get("tokens_per_response_mean", 0)
    out.append("\n### Token Usage")
    out.append("| Prompt | Completion | Mean / response |")
    out.append("| ------ | ---------- | --------------- |")
    out.append(f"| {prompt} | {completion} | {mean_resp} |")

    # ── Request budget (5h sliding window) ──
    by_class = bench.get("requests_by_class") or {}
    if isinstance(by_class, dict) and by_class:
        out.append("\n### Request Budget (5h sliding window)")
        out.append("| Class | Count | Remaining fraction |")
        out.append("| ----- | ----- | ------------------ |")
        remaining = bench.get("remaining_budget_fraction", 1.0)
        for cls, count in sorted(by_class.items()):
            out.append(f"| {cls} | {int(count or 0)} | {remaining} |")

    # ── Compaction ──
    tier_counts = bench.get("compaction_tier_counts") or {}
    if isinstance(tier_counts, dict) and tier_counts:
        out.append("\n### Compaction")
        out.append("| Tier | Count |")
        out.append("| ---- | ----- |")
        for tier, count in sorted(tier_counts.items()):
            out.append(f"| {tier} | {int(count or 0)} |")

    # ── Pipelines ──
    fires_by_name = bench.get("pipeline_fires_by_name") or {}
    fires_by_trigger = bench.get("pipeline_fires_by_trigger_type") or {}
    if isinstance(fires_by_name, dict) and fires_by_name:
        out.append("\n### Pipelines")
        out.append("| Name | Fires |")
        out.append("| ---- | ----- |")
        for name, count in sorted(
            fires_by_name.items(), key=lambda kv: -int(kv[1] or 0)
        ):
            out.append(f"| {name} | {int(count or 0)} |")
    success = int(bench.get("pipeline_success_count", 0) or 0)
    fail = int(bench.get("pipeline_fail_count", 0) or 0)
    if isinstance(fires_by_trigger, dict) and fires_by_trigger:
        out.append("\n**By trigger type**")
        out.append("| Trigger | Fires |")
        out.append("| ------- | ----- |")
        for trig, count in sorted(fires_by_trigger.items()):
            out.append(f"| {trig} | {int(count or 0)} |")
    if success or fail or fires_by_name:
        out.append(f"\n_Pipeline outcomes: success={success}, fail={fail}_")

    # ── Notifications ──
    notif_tier = bench.get("notifications_by_tier") or {}
    notif_reason = bench.get("notifications_by_reason") or {}
    if (isinstance(notif_tier, dict) and notif_tier) or (
        isinstance(notif_reason, dict) and notif_reason
    ):
        out.append("\n### Notifications")
        if isinstance(notif_tier, dict) and notif_tier:
            out.append("| Tier | Count |")
            out.append("| ---- | ----- |")
            for tier, count in sorted(notif_tier.items()):
                out.append(f"| {tier} | {int(count or 0)} |")
        if isinstance(notif_reason, dict) and notif_reason:
            out.append("\n**By reason**")
            out.append("| Reason | Count |")
            out.append("| ------ | ----- |")
            for reason, count in sorted(notif_reason.items()):
                out.append(f"| {reason} | {int(count or 0)} |")

    # ── Memory lifecycle ──
    out.append("\n### Memory Lifecycle")
    out.append(
        "| Memories created | Consolidated | Dedup-merged | "
        "Entities created | Entities merged |"
    )
    out.append(
        "| ---------------- | ------------ | ------------ | "
        "---------------- | --------------- |"
    )
    out.append(
        f"| {int(bench.get('memories_created', 0) or 0)} "
        f"| {int(bench.get('memories_consolidated', 0) or 0)} "
        f"| {int(bench.get('memories_dedup_merged', 0) or 0)} "
        f"| {int(bench.get('entities_created', 0) or 0)} "
        f"| {int(bench.get('entities_merged', 0) or 0)} |"
    )

    # ── Vault ──
    out.append("\n### Vault")
    out.append(
        "| Notes | Wikilinks | Entity pages | MOC pages | "
        "Active working docs |"
    )
    out.append(
        "| ----- | --------- | ------------ | --------- | "
        "------------------- |"
    )
    out.append(
        f"| {int(bench.get('vault_notes_total', 0) or 0)} "
        f"| {int(bench.get('vault_wikilinks_total', 0) or 0)} "
        f"| {int(bench.get('vault_entity_pages', 0) or 0)} "
        f"| {int(bench.get('vault_moc_pages', 0) or 0)} "
        f"| {int(bench.get('vault_working_docs_active', 0) or 0)} |"
    )

    # ── Phase dwell time ──
    dwell = bench.get("phase_dwell_seconds") or {}
    if isinstance(dwell, dict) and dwell:
        out.append("\n### Phase Dwell Time")
        out.append("| SystemStatePhase | Seconds |")
        out.append("| ---------------- | ------- |")
        for phase, secs in sorted(dwell.items()):
            try:
                secs_f = float(secs)
            except (TypeError, ValueError):
                secs_f = 0.0
            out.append(f"| {phase} | {secs_f} |")

    # ── Trend across snapshots ──
    if len(bench_files) >= 2:
        trend_rows: list[dict[str, Any]] = []
        for path in bench_files:
            try:
                row = json.loads(path.read_text())
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            trend_rows.append({
                "snapshot": path.stem.replace(".benchmarks", ""),
                "p50": row.get("response_latency_p50_ms", 0),
                "p95": row.get("response_latency_p95_ms", 0),
                "remaining": row.get("remaining_budget_fraction", 1.0),
                "memories": int(row.get("vault_notes_total", 0) or 0),
                "working_docs": int(
                    row.get("vault_working_docs_active", 0) or 0
                ),
            })
        if trend_rows:
            out.append("\n### Trend across snapshots")
            out.append(
                "| Snapshot | p50 ms | p95 ms | Budget remaining "
                "| Vault notes | Working docs |"
            )
            out.append(
                "| -------- | ------ | ------ | ---------------- "
                "| ----------- | ------------ |"
            )
            for r in trend_rows:
                out.append(
                    f"| {r['snapshot']} | {r['p50']} | {r['p95']} "
                    f"| {r['remaining']} | {r['memories']} "
                    f"| {r['working_docs']} |"
                )
            out.append(
                f"\n_Trend store: `data/acceptance/benchmarks.csv` "
                f"({len(bench_files)} sidecar(s) in snapshots/)_"
            )

    return out


async def build_report(
    session_state: dict[str, Any],
    snapshots_dir: Path,
    output_dir: Path,
    compaction_events: list[dict[str, Any]] | None = None,
) -> Path:
    """Build and write the acceptance test final report.

    ``compaction_events`` is passed in by the harness (which holds the
    in-memory list). Previously the report read from
    ``session_state["compaction_events"]`` which is never populated.
    """
    lines: list[str] = []

    lines.append("# Kora V2 Acceptance Test Report")
    lines.append(f"\nGenerated: {datetime.now(UTC).isoformat()}")
    lines.append(f"Started: {session_state.get('started_at', 'unknown')}")
    lines.append(f"Simulated time elapsed: +{session_state.get('simulated_hours_offset', 0):.1f}h")

    # Conversation summary
    messages = session_state.get("messages", [])
    user_turns = [m for m in messages if m.get("role") == "user"]
    assistant_turns = [m for m in messages if m.get("role") == "assistant"]
    lines.append(f"\nConversation: {len(user_turns)} user turns, {len(assistant_turns)} assistant turns")

    # ── Gather evidence up-front so coverage derives from live data ──────
    cap_health = await _build_capability_health()
    life_data = await _query_life_management(output_dir)
    from tests.acceptance.life_os import (
        collect_life_os_acceptance,
        render_life_os_acceptance,
    )

    life_os_summary = collect_life_os_acceptance(
        _PROJECT_ROOT / "data" / "operational.db",
        messages=messages,
        capability_pack_status=cap_health,
    )
    auto_state = await _query_autonomous_state()
    run_started_at = session_state.get("started_at")
    run_started_filter = run_started_at if isinstance(run_started_at, str) else None
    orch_evidence = await _query_orchestration_evidence(
        _with_startup_grace(run_started_filter)
    )
    auth_results = session_state.get("auth_test_results", [])
    tool_usage = _extract_tool_usage(messages, orch_evidence.get("turn_traces") or [])
    policy_grants = await _query_policy_grants(
        run_started_filter
    )
    auth_evidence = auth_results or policy_grants
    compaction_events_resolved = (
        compaction_events or session_state.get("compaction_events", [])
    )
    latest_status = _latest_snapshot_status(snapshots_dir)
    latest_benchmark = _latest_benchmark_state(snapshots_dir)
    current_vault_benchmark = _current_vault_benchmark_state()
    benchmark_state = dict(latest_benchmark or {})
    for key, value in (current_vault_benchmark or {}).items():
        if isinstance(value, int) and isinstance(benchmark_state.get(key), int):
            benchmark_state[key] = max(int(benchmark_state[key]), value)
        else:
            benchmark_state.setdefault(key, value)
    benchmark_state = benchmark_state or None

    # ── Coverage ──────────────────────────────────────────────────────────
    # Merge operator-edited markers from ``coverage.md`` with
    # evidence-derived markers. Operator markers take precedence so a
    # manual override is always respected.
    operator_markers = _parse_coverage_file(output_dir / "coverage.md")
    auto_markers = _auto_mark_coverage(
        tool_usage=tool_usage,
        life_data=life_data,
        auto_state=auto_state,
        cap_health=cap_health,
        compaction_events=compaction_events_resolved,
        messages=messages,
        auth_results=auth_evidence,
        latest_status=latest_status,
        orch_evidence=orch_evidence,
        benchmark_state=benchmark_state,
        error_results=session_state.get("error_recovery_results", []),
        snapshots_dir=snapshots_dir,
        skill_gating_check=session_state.get("skill_gating_check", {}),
    )
    from tests.acceptance.scenario.week_plan import COVERAGE_ITEMS, CoverageStatus

    active_items = {k: v for k, v in COVERAGE_ITEMS.items() if v.status == CoverageStatus.ACTIVE}
    deferred_items = {k: v for k, v in COVERAGE_ITEMS.items() if v.status == CoverageStatus.DEFERRED}

    lines.append("\n## Coverage -- Active Items")
    active_covered = 0
    active_partial = 0
    auto_applied = 0
    for item_id, item in sorted(active_items.items()):
        operator = operator_markers.get(item_id)
        # Only treat an operator marker as authoritative when it carries
        # explicit intent ("x" = satisfied, "~" = partial). Blank
        # markers mean "operator has not edited this row" and should
        # fall through to the auto-derived evidence instead of
        # overriding it with an empty cell. The previous behaviour
        # printed 0/N coverage despite hundreds of auto-derived tool
        # calls because every template row ships as blank.
        if operator in ("x", "~"):
            marker = operator
            provenance = ""
        elif item_id in auto_markers:
            marker = auto_markers[item_id]
            provenance = " _(auto)_"
            auto_applied += 1
        else:
            marker = " "
            provenance = ""
        lines.append(f"- [{marker}] {item_id}. {item.description}{provenance}")
        if marker == "x":
            active_covered += 1
        elif marker == "~":
            active_partial += 1

    lines.append(
        f"\nActive coverage: {active_covered}/{len(active_items)} satisfied"
        f" + {active_partial} partial"
        f" (auto-derived: {auto_applied}, operator-edited: "
        f"{sum(1 for value in operator_markers.values() if value in ('x', '~'))})"
    )

    lines.append("\n## Coverage -- Deferred Items")
    for item_id, item in sorted(deferred_items.items()):
        lines.append(f"- [~] {item_id}. {item.description}")
        lines.append(f"      DEFERRED: {item.deferred_reason}")

    lines.append(f"\nDeferred: {len(deferred_items)} items (not tested, awaiting V2 implementation)")

    lines.extend(
        render_life_os_acceptance(
            life_os_summary,
            manual_verification=session_state.get("life_os_manual_verification", {}),
        )
    )

    # ── Tool Usage Summary ────────────────────────────────────────────────
    lines.append(f"\n## Tool Usage ({tool_usage['total']} calls, {tool_usage['unique']} unique tools)")

    if tool_usage["life_management"]:
        lines.append(f"- Life management: {', '.join(tool_usage['life_management'])}")
    else:
        lines.append("- Life management: (no tools called)")

    if tool_usage["filesystem"]:
        lines.append(f"- Filesystem: {', '.join(tool_usage['filesystem'])}")
    else:
        lines.append("- Filesystem: (no tools called)")

    if tool_usage["mcp"]:
        lines.append(f"- MCP (web): {', '.join(tool_usage['mcp'])}")
    else:
        lines.append("- MCP (web): (no tools called)")

    if tool_usage["orchestration"]:
        lines.append(
            f"- Orchestration: {', '.join(tool_usage['orchestration'])}"
        )
    else:
        lines.append("- Orchestration: (no tools called)")

    # AT3 will populate the pipelines bucket from pipeline_instances / work_ledger.
    lines.append(
        "- Pipelines: (AT3 will fill this in; pipelines fire from triggers, "
        "not tool calls)"
    )

    if tool_usage["capability_workspace"]:
        lines.append(f"- Capability (workspace): {', '.join(tool_usage['capability_workspace'])}")
    else:
        lines.append("- Capability (workspace): (no calls)")

    if tool_usage["capability_browser"]:
        lines.append(f"- Capability (browser): {', '.join(tool_usage['capability_browser'])}")
    else:
        lines.append("- Capability (browser): (no calls)")

    if tool_usage["capability_vault"]:
        lines.append(f"- Capability (vault): {', '.join(tool_usage['capability_vault'])}")
    else:
        lines.append("- Capability (vault): (no calls)")

    if tool_usage["tool_counts"]:
        lines.append("")
        lines.append("Call counts:")
        for name, count in sorted(tool_usage["tool_counts"].items(), key=lambda x: -x[1]):
            lines.append(f"  {name}: {count}")

    # ── Capability Pack Health ─────────────────────────────────────────────
    # cap_health already resolved at the top of the function for coverage.
    _EXPECTED_PACKS = ("workspace", "browser", "vault", "doctor")
    lines.append(f"\n## Capability Packs ({len(cap_health)} packs)")
    for pack_name in _EXPECTED_PACKS:
        info = cap_health.get(pack_name, {})
        status = info.get("status", "unknown")
        summary = info.get("summary", "(not registered)")
        remediation = info.get("remediation")
        # Count capability actions in this pack from tool_counts
        pack_calls = sum(
            count
            for tool_name, count in tool_usage["tool_counts"].items()
            if tool_name.startswith(f"{pack_name}.")
        )
        line = f"- {pack_name}: status={status} calls={pack_calls} — {summary}"
        if remediation:
            line += f"\n  Remediation: {remediation}"
        lines.append(line)

    # ── Policy Grants ──────────────────────────────────────────────────────
    # Prefer durable permission_grants rows for the policy-matrix audit.
    # The in-memory WebSocket auth test list is used only when the DB has
    # no rows (for isolated report tests).
    rendered_grants = policy_grants or auth_results
    approval_prompts = len(rendered_grants)
    approved = sum(1 for ar in rendered_grants if ar.get("approved"))
    denied = sum(
        1
        for ar in rendered_grants
        if not ar.get("approved") and ar.get("approved") is not None
    )
    timed_out = approval_prompts - approved - denied

    lines.append(f"\n## Policy Grants ({approval_prompts} recorded decisions)")
    lines.append(f"- Approved: {approved}")
    lines.append(f"- Denied: {denied}")
    lines.append(f"- Timed out / unknown: {timed_out}")

    # ── Life Management Records ──────────────────────────────────────────
    # life_data was already resolved at the top of the function.
    if life_data.get("available"):
        lines.append("\n## Life Management Records (DB)")
        lines.append(f"- Medications: {life_data.get('medication_count', 0)}")
        lines.append(f"- Meals: {life_data.get('meal_count', 0)}")
        lines.append(f"- Reminders: {life_data.get('reminder_count', 0)}")
        lines.append(f"- Quick notes: {life_data.get('quick_note_count', 0)}")
        lines.append(f"- Focus blocks: {life_data.get('focus_block_count', 0)}")

        total_records = sum(
            life_data.get(f"{k}_count", 0)
            for k in ("medication", "meal", "reminder", "quick_note", "focus_block")
        )
        if total_records == 0:
            lines.append("\nWARNING: No life management records created during test.")
            lines.append("Coverage items 7 and 23 may not be satisfied.")
    else:
        lines.append("\n## Life Management Records")
        lines.append("operational.db not available for life management query.")

    # ── Autonomous Execution ──────────────────────────────────────────────
    # auto_state was already resolved at the top of the function (needed
    # for coverage auto-marking). Here we render its details.
    auto_plans = auto_state.get("plans", []) if auto_state.get("available") else []
    plans_source = auto_state.get("plans_source", "empty")
    plan_query_error = auto_state.get("plans_query_error")
    total_items_auto = auto_state.get("total_items", 0)
    items_by_status = auto_state.get("items_by_status") or {}
    updates = auto_state.get("updates") or []
    if auto_plans:
        lines.append(
            f"\n## Autonomous Execution ({len(auto_plans)} plans, "
            f"{auto_state.get('checkpoint_count', 0)} checkpoints, "
            f"{total_items_auto} items)"
        )
        if plans_source == "derived_from_items":
            lines.append(
                "(plans table was empty; view synthesised from "
                "`items.autonomous_plan_id` — this happens when the "
                "harness wipes autonomous_plans between restarts.)"
            )
        if plan_query_error:
            lines.append(
                f"(autonomous_plans query warning: {plan_query_error[:120]})"
            )
        for plan in auto_plans:
            goal = (plan.get("goal") or "")[:100]
            status = plan.get("status", "?")
            req = plan.get("request_count")
            step_count = plan.get("step_count")
            extras: list[str] = []
            if req is not None:
                extras.append(f"req={req}")
            if step_count is not None:
                completed = plan.get("completed", 0)
                extras.append(f"steps={completed}/{step_count}")
            extras_str = f" ({', '.join(extras)})" if extras else ""
            lines.append(f"- [{status}] {goal}{extras_str}")
        if items_by_status:
            status_summary = ", ".join(
                f"{k}={v}" for k, v in sorted(items_by_status.items())
            )
            lines.append(f"Items by status: {status_summary}")
        if updates:
            lines.append("Recent autonomous updates:")
            for u in updates[:5]:
                lines.append(
                    f"- [{u.get('update_type', '?')}] "
                    f"{(u.get('summary') or '')[:140]}"
                )
    elif tool_usage["orchestration"] or total_items_auto > 0:
        lines.append("\n## Autonomous Execution")
        lines.append(
            "decompose_and_dispatch fired but no plan rows were found — "
            "AT3 will wire an evidence query against pipeline_instances / "
            f"worker_tasks (total items in legacy DB: {total_items_auto}, "
            f"checkpoints: {auto_state.get('checkpoint_count', 0)})."
        )
        if items_by_status:
            status_summary = ", ".join(
                f"{k}={v}" for k, v in sorted(items_by_status.items())
            )
            lines.append(f"Items by status: {status_summary}")
    else:
        lines.append("\n## Autonomous Execution")
        lines.append("No long-background dispatch observed during this test run.")
        lines.append("Coverage item 21 is NOT satisfied.")

    # ── Compaction ────────────────────────────────────────────────────────
    # compaction_events resolved at the top of the function from the
    # harness's in-memory tracker (now mirrored into session state so
    # a harness restart does not drop events).
    events = compaction_events_resolved
    if events:
        lines.append(f"\n## Compaction ({len(events)} events detected)")
        for ev in events:
            tokens = ev.get("token_count")
            tokens_str = f"{tokens}" if tokens is not None else "?"
            lines.append(
                f"- tier={ev.get('tier')} tokens={tokens_str} at {ev.get('ts', '?')}"
            )
    else:
        lines.append("\n## Compaction")
        lines.append("No compaction events detected during the test run.")

    # ── Auth Test Results (detail log) ───────────────────────────────────────
    # auth_evidence was already fetched for coverage above.
    if auth_evidence:
        lines.append(f"\n## Auth Relay Test ({len(auth_evidence)} events)")
        for ar in auth_evidence:
            ar_status = "APPROVED" if ar.get("approved") else "DENIED"
            lines.append(f"- [{ar_status}] tool={ar.get('tool')} risk={ar.get('risk')} at {ar.get('ts', '?')}")

    # ── Snapshots summary ─────────────────────────────────────────────────
    snapshots = sorted(snapshots_dir.glob("*.json"))
    if snapshots:
        lines.append(f"\n## Snapshots ({len(snapshots)} captured)")
        for snap_path in snapshots:
            try:
                snap = json.loads(snap_path.read_text())
                ts = snap.get("captured_at", "?")[:19]
                msg_count = snap.get("conversation", {}).get("message_count", "?")
                health_status = _snapshot_health_status(snap)
                # Include autonomous state in snapshot summary
                snap_auto_state = snap.get("autonomous_state", {})
                auto_items = (
                    snap_auto_state.get("total_items", 0)
                    if snap_auto_state.get("available")
                    else "-"
                )
                lines.append(
                    f"- **{snap_path.stem}** @ {ts}: "
                    f"{msg_count} msgs | health={health_status} | items={auto_items}"
                )
            except Exception:
                lines.append(f"- {snap_path.stem}: (unreadable)")

    # First + last snapshot comparison
    if len(snapshots) >= 2:
        first = json.loads(snapshots[0].read_text())
        last = json.loads(snapshots[-1].read_text())
        lines.append("\n## Overall State Change (first -> last snapshot)")
        m1 = first.get("conversation", {}).get("message_count", 0)
        m2 = last.get("conversation", {}).get("message_count", 0)
        lines.append(f"Messages: {m1} -> {m2}")

        h1 = _snapshot_health_status(first)
        h2 = _snapshot_health_status(last)
        lines.append(f"Health: {h1} -> {h2}")

        c1 = len(first.get("compaction_events", []))
        c2 = len(last.get("compaction_events", []))
        if c1 != c2:
            lines.append(f"Compaction events: {c1} -> {c2}")

        # Autonomous state change
        a1 = first.get("autonomous_state") or {}
        a2 = last.get("autonomous_state") or {}
        if a1.get("available") or a2.get("available"):
            items1 = a1.get("total_items", 0)
            items2 = a2.get("total_items", 0)
            if items1 != items2:
                lines.append(f"Autonomous items: {items1} -> {items2}")
            chk1 = a1.get("checkpoint_count", 0)
            chk2 = a2.get("checkpoint_count", 0)
            if chk1 != chk2:
                lines.append(f"Autonomous checkpoints: {chk1} -> {chk2}")

    # ── Coverage Gap Warnings ─────────────────────────────────────────────
    gap_warnings: list[str] = []
    if not tool_usage["life_management"]:
        gap_warnings.append("No life management tools used (items 7, 23)")
    if not tool_usage["filesystem"]:
        gap_warnings.append("No filesystem tools used (item 22)")
    if not tool_usage["mcp"] and auto_markers.get(9) != "x":
        gap_warnings.append("No MCP/web tools used (item 9)")
    if not tool_usage["orchestration"]:
        gap_warnings.append(
            "No orchestration tools used "
            "(items 8, 21, 25-31, 43 — decompose_and_dispatch / "
            "get_task_progress / cancel_task / record_decision)"
        )
    # Phase 9 capability-pack gap check (item 100)
    any_cap_calls = (
        tool_usage["capability_workspace"]
        or tool_usage["capability_browser"]
        or tool_usage["capability_vault"]
    )
    if not any_cap_calls:
        # Check if at least one pack is UNCONFIGURED/DEGRADED (still satisfies item 100)
        cap_gap = True
        for pack_name in ("workspace", "browser", "vault"):
            info = cap_health.get(pack_name, {})
            if info.get("status") in ("unconfigured", "degraded", "unhealthy", "unimplemented"):
                cap_gap = False
                break
        if cap_gap:
            gap_warnings.append(
                "No capability-pack tool calls and no degraded/unconfigured packs (item 100)"
            )

    if gap_warnings:
        lines.append(f"\n## Coverage Gap Warnings ({len(gap_warnings)})")
        for w in gap_warnings:
            lines.append(f"- {w}")

    # ── Benchmarks dashboard (AT4) ──────────────────────────────────────
    # Render the latest ``<snapshot>.benchmarks.json`` sidecar as a real
    # markdown dashboard with per-category tables. When more than one
    # sidecar exists, append a "Trend across snapshots" mini-table so
    # the operator can see how key metrics evolved across the run. If
    # no sidecar exists, the entire section is omitted gracefully.
    lines.extend(_render_benchmarks_dashboard(snapshots_dir))

    # ── Errors ────────────────────────────────────────────────────────────
    errors = session_state.get("errors", [])
    if errors:
        lines.append(f"\n## Errors ({len(errors)} found)")
        for err in errors:
            lines.append(f"- {err}")

    # ── Conversation log (last 20 turns) ──────────────────────────────────
    if messages:
        lines.append("\n## Conversation Log (last 20 turns)")
        for m in messages[-20:]:
            role = m.get("role", "?")
            content = (m.get("content") or "")[:300]
            ts = (m.get("ts") or "")[:19]
            if role == "user":
                lines.append(f"\n**Jordan** [{ts}]: {content}")
            else:
                tool_calls = m.get("tool_calls", [])
                trace = m.get("trace_id", "")[:8] if m.get("trace_id") else ""
                compaction_tier = m.get("compaction_tier", "")
                header = f"\n**Kora** [{ts}]"
                if trace:
                    header += f" (trace:{trace})"
                if tool_calls:
                    header += f" [tools: {', '.join(tool_calls[:3])}]"
                if compaction_tier and compaction_tier != "none":
                    header += f" [compaction:{compaction_tier}]"
                lines.append(header + f": {content}")

    report_text = "\n".join(lines)
    report_path = output_dir / "acceptance_report.md"
    report_path.write_text(report_text)
    return report_path
