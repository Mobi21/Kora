"""Generate the final acceptance test report (V2-aligned).

Covers all V2 subsystems: conversation quality, compaction, auth relay,
life management, tool usage, autonomous execution, emotion/energy,
skills activation, and filesystem operations.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
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

    # Item 2: personal context — name + ADHD + pet or partner signal
    if _msg_mentions("adhd") and _msg_mentions("mochi", "alex"):
        auto[2] = "x"

    # Item 3: week planning with concrete tasks — any write_file + planning language
    if tool_counts.get("write_file", 0) >= 2 and _msg_mentions(
        "plan", "week", "this week", "schedule"
    ):
        auto[3] = "x"

    # Item 4: coding track — filesystem writes + code-ish language
    if tool_usage.get("filesystem") and _msg_mentions(
        "tsx", "component", ".py", "function", "class"
    ):
        auto[4] = "x"

    # Item 5: research track — research artifacts
    if _msg_mentions("research", "deep dive", "landscape", "compare"):
        auto[5] = "x"

    # Item 6: writing track — writing artifacts
    if _msg_mentions("outline", "draft", "brief", "paper", "essay"):
        auto[6] = "x"

    # Item 7: life management tools used
    if tool_usage.get("life_management"):
        auto[7] = "x"

    # Item 9: web research — successful MCP/capability call OR disclosed
    # failure (the item description explicitly allows both).
    if tool_usage.get("mcp") or tool_usage.get("capability_browser"):
        auto[9] = "x"
    elif _msg_mentions("unavailable", "can't pull", "no web search", "browser"):
        auto[9] = "~"

    # Item 10 & 15: compaction pressure + metadata
    if compaction_events:
        auto[10] = "x"
        if any(
            ev.get("token_count") is not None and ev.get("tier")
            for ev in compaction_events
        ):
            auto[15] = "x"

    # Item 11: revision wave
    if _msg_mentions(
        "rewrite", "restructure", "pivot", "revise", "rework"
    ):
        auto[11] = "x"

    # Item 8 (un-deferred in AT1): decompose_and_dispatch creates a
    # pipeline_instance with sub-tasks. Tool-call evidence is the AT1
    # signal; AT3 will add a real query against pipeline_instances /
    # worker_tasks for the multi-stage check.
    if tool_counts.get("decompose_and_dispatch", 0) >= 1:
        auto[8] = "x"

    # Item 12 (un-deferred in AT1): real background pipelines fire during
    # DEEP_IDLE. The legacy BackgroundWorker count was dropped along
    # with the worker itself (Phase 7.5); the equivalent post-7.5 signal
    # is "any orchestration core pipeline has logged a row in
    # work_ledger". Until AT3 wires that query, we accept the legacy
    # status field as a fallback if the harness still sets it, and
    # otherwise leave the item for operator marking.
    if latest_status and latest_status.get("background_worker_items", 0) >= 1:
        auto[12] = "x"

    # Item 14: weekly review
    if _msg_mentions("weekly review", "weekly_review", "week review"):
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

    # Item 21: long-running autonomous execution via decompose_and_dispatch.
    # AT3 will replace this with an evidence query against
    # pipeline_instances WHERE intent_duration='long'. For now we accept
    # any decompose_and_dispatch tool call as best-effort signal; the
    # legacy auto_state-derived counters have been removed (the
    # autonomous_plans / autonomous_checkpoints tables were retired in
    # Phase 7.5 along with start_autonomous).
    if (
        tool_counts.get("decompose_and_dispatch", 0) >= 1
        or auto_state.get("total_items", 0) > 0
    ):
        auto[21] = "x"

    # Item 22: filesystem operations
    if tool_usage.get("filesystem"):
        auto[22] = "x"

    # Item 23: life management DB records persist
    if life_data.get("available"):
        total_records = sum(
            life_data.get(f"{k}_count", 0)
            for k in ("medication", "meal", "reminder", "quick_note", "focus_block")
        )
        if total_records > 0:
            auto[23] = "x"

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
    if _msg_mentions(
        "unavailable", "failed", "permission denied", "can't pull",
        "couldn't", "not available",
    ):
        auto[101] = "x"

    # Item 102: policy matrix — 4 capability packs visible + policy
    # grants section has data.
    if len(cap_health) >= 4:
        auto[102] = "x"

    # Items 24-67 (Phase 7.5 + Phase 8 orchestration / memory / vault /
    # context / proactive coverage) all need real evidence queries
    # against orchestration tables (pipeline_instances, worker_tasks,
    # work_ledger, system_state_log, runtime_pipelines, open_decisions).
    # AT3 wires those queries; for AT1 they are intentionally left for
    # operator marking via coverage.md.

    return auto


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


def _extract_tool_usage(messages: list[dict[str, Any]]) -> dict[str, Any]:
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

        return result
    except Exception:
        return {"available": False}


async def _query_autonomous_state() -> dict[str, Any]:
    """Query live autonomous state directly from operational.db.

    Primary source: the ``autonomous_plans`` table written by
    ``persist_plan`` in the autonomous loop. Some acceptance run
    configurations wipe ``autonomous_plans`` between harness restarts
    (see ``_clean_stale_autonomous_data``) so the table can be empty
    even when a plan genuinely ran — the 2026-04-11 audit hit this.
    When that happens we synthesise a view from:

      * ``items`` rows whose ``autonomous_plan_id`` is set (root + steps)
      * ``autonomous_updates`` rows (checkpoint + completion summaries)

    so the report reflects actual work instead of emitting the
    misleading "no plans recorded in DB" line.
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

        unified_plans = plans or derived_plans
        active_plans = [
            p for p in unified_plans
            if p.get("status") not in ("completed", "cancelled", "failed")
        ]

        return {
            "available": True,
            "plans": unified_plans,
            "plans_source": "autonomous_plans" if plans else (
                "derived_from_items" if derived_plans else "empty"
            ),
            "plans_query_error": plans_query_error,
            "active_plan_count": len(active_plans),
            "checkpoint_count": checkpoint_count,
            "total_items": total_items,
            "items_by_status": items_by_status,
            "updates": updates,
        }
    except Exception:
        return {"available": False}


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
    tool_usage = _extract_tool_usage(messages)
    cap_health = await _build_capability_health()
    life_data = await _query_life_management(output_dir)
    auto_state = await _query_autonomous_state()
    auth_results = session_state.get("auth_test_results", [])
    compaction_events_resolved = (
        compaction_events or session_state.get("compaction_events", [])
    )
    latest_status = _latest_snapshot_status(snapshots_dir)

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
        auth_results=auth_results,
        latest_status=latest_status,
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
        if operator in ("x", "~", " "):
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
        f" (auto-derived: {auto_applied}, operator-edited: {len(operator_markers)})"
    )

    lines.append("\n## Coverage -- Deferred Items")
    for item_id, item in sorted(deferred_items.items()):
        lines.append(f"- [~] {item_id}. {item.description}")
        lines.append(f"      DEFERRED: {item.deferred_reason}")

    lines.append(f"\nDeferred: {len(deferred_items)} items (not tested, awaiting V2 implementation)")

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
    # auth_test_results was already read at the top of the function.
    # Here we count approvals / denials / timeouts for the policy-matrix
    # enforcement section (Phase 9).
    approval_prompts = len(auth_results)
    approved = sum(1 for ar in auth_results if ar.get("approved"))
    denied = sum(1 for ar in auth_results if not ar.get("approved") and ar.get("approved") is not None)
    timed_out = approval_prompts - approved - denied

    lines.append(f"\n## Policy Grants ({approval_prompts} approval prompts)")
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
    # auth_results was already fetched for the Policy Grants summary above.
    if auth_results:
        lines.append(f"\n## Auth Relay Test ({len(auth_results)} events)")
        for ar in auth_results:
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
                auto_state = snap.get("autonomous_state", {})
                auto_items = auto_state.get("total_items", 0) if auto_state.get("available") else "-"
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
    if not tool_usage["mcp"]:
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
