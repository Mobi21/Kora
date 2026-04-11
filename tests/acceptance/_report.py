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

    life_tools = {
        "log_medication", "log_meal", "create_reminder",
        "query_reminders", "query_medications", "query_meals",
        "query_focus_blocks", "quick_note",
        "start_focus_block", "end_focus_block",
    }
    fs_tools = {"read_file", "write_file", "list_directory", "create_directory", "file_exists"}
    # Legacy MCP tool names — kept for backward-compat; capability tools go in their own buckets
    mcp_tools = {"search_web", "fetch_url"}
    auto_tools = {"start_autonomous"}
    memory_tools = {"recall"}

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
        "autonomous": sorted(t for t in tool_counts if t in auto_tools),
        "memory": sorted(t for t in tool_counts if t in memory_tools),
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

    Mirrors the harness's ``_query_autonomous_state`` so the report sees
    the same rich view used by snapshots and ``diff``. Previously the
    report read autonomous plans off the life-mgmt dict fallback which
    failed silently when ``autonomous_plans`` schema differed.
    """
    op_db = _PROJECT_ROOT / "data" / "operational.db"
    if not op_db.exists():
        return {"available": False}
    try:
        import aiosqlite

        async with aiosqlite.connect(str(op_db)) as db:
            db.row_factory = aiosqlite.Row

            plans: list[dict[str, Any]] = []
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
            except Exception:
                plans = []

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

        active_plans = [
            p for p in plans
            if p.get("status") not in ("completed", "cancelled", "failed")
        ]

        return {
            "available": True,
            "plans": plans,
            "active_plan_count": len(active_plans),
            "checkpoint_count": checkpoint_count,
            "total_items": total_items,
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

    # ── Coverage ──────────────────────────────────────────────────────────
    # Parse the operator-maintained coverage.md file. This is the source of
    # truth for active-item status — the skill instructs operators to mark
    # items as `[x]`/`[~]` there during the run.
    coverage_markers = _parse_coverage_file(output_dir / "coverage.md")
    from tests.acceptance.scenario.week_plan import COVERAGE_ITEMS, CoverageStatus

    active_items = {k: v for k, v in COVERAGE_ITEMS.items() if v.status == CoverageStatus.ACTIVE}
    deferred_items = {k: v for k, v in COVERAGE_ITEMS.items() if v.status == CoverageStatus.DEFERRED}

    lines.append("\n## Coverage -- Active Items")
    active_covered = 0
    active_partial = 0
    for item_id, item in sorted(active_items.items()):
        marker = coverage_markers.get(item_id, " ")
        lines.append(f"- [{marker}] {item_id}. {item.description}")
        if marker == "x":
            active_covered += 1
        elif marker == "~":
            active_partial += 1

    lines.append(
        f"\nActive coverage: {active_covered}/{len(active_items)} satisfied"
        f" + {active_partial} partial"
    )

    lines.append("\n## Coverage -- Deferred Items")
    for item_id, item in sorted(deferred_items.items()):
        lines.append(f"- [~] {item_id}. {item.description}")
        lines.append(f"      DEFERRED: {item.deferred_reason}")

    lines.append(f"\nDeferred: {len(deferred_items)} items (not tested, awaiting V2 implementation)")

    # ── Tool Usage Summary ────────────────────────────────────────────────
    tool_usage = _extract_tool_usage(messages)
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

    if tool_usage["autonomous"]:
        lines.append(f"- Autonomous: {', '.join(tool_usage['autonomous'])}")
    else:
        lines.append("- Autonomous: (no tools called)")

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
    cap_health = await _build_capability_health()
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
    # auth_test_results already tracks deny/approve events; here we also count
    # auth_request events that represent approval prompts surfaced for new
    # capability/account/action fields (Phase 9 policy matrix enforcement).
    auth_results = session_state.get("auth_test_results", [])
    approval_prompts = len(auth_results)
    approved = sum(1 for ar in auth_results if ar.get("approved"))
    denied = sum(1 for ar in auth_results if not ar.get("approved") and ar.get("approved") is not None)
    timed_out = approval_prompts - approved - denied

    lines.append(f"\n## Policy Grants ({approval_prompts} approval prompts)")
    lines.append(f"- Approved: {approved}")
    lines.append(f"- Denied: {denied}")
    lines.append(f"- Timed out / unknown: {timed_out}")

    # ── Life Management Records ──────────────────────────────────────────
    life_data = await _query_life_management(output_dir)
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
    # Dedicated query so we see plans + checkpoints + item count (same
    # view as snapshots), not the narrow fallback in life_data.
    auto_state = await _query_autonomous_state()
    auto_plans = auto_state.get("plans", []) if auto_state.get("available") else []
    if auto_plans:
        lines.append(
            f"\n## Autonomous Execution ({len(auto_plans)} plans, "
            f"{auto_state.get('checkpoint_count', 0)} checkpoints, "
            f"{auto_state.get('total_items', 0)} items)"
        )
        for plan in auto_plans:
            lines.append(
                f"- [{plan.get('status', '?')}] {(plan.get('goal') or '')[:100]}"
                f" (req={plan.get('request_count', 0)})"
            )
    elif tool_usage["autonomous"]:
        lines.append("\n## Autonomous Execution")
        lines.append("start_autonomous was called but no plans recorded in DB.")
    else:
        lines.append("\n## Autonomous Execution")
        lines.append("No autonomous work initiated during this test run.")
        lines.append("Coverage item 21 is NOT satisfied.")

    # ── Compaction ────────────────────────────────────────────────────────
    # compaction_events comes from the harness's in-memory tracker
    # (passed in). The legacy fallback to session_state is kept for any
    # older paths that wrote into session state, but the primary source
    # is the argument.
    events = compaction_events or session_state.get("compaction_events", [])
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
                health = snap.get("health", {})
                health_status = health.get("status", "?") if isinstance(health, dict) else "?"
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

        h1 = (first.get("health") or {}).get("status", "?")
        h2 = (last.get("health") or {}).get("status", "?")
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
    if not tool_usage["autonomous"]:
        gap_warnings.append("No autonomous work started (item 21)")
    # Phase 9 capability-pack gap check (item 24)
    any_cap_calls = (
        tool_usage["capability_workspace"]
        or tool_usage["capability_browser"]
        or tool_usage["capability_vault"]
    )
    if not any_cap_calls:
        # Check if at least one pack is UNCONFIGURED/DEGRADED (still satisfies item 24)
        cap_gap = True
        for pack_name in ("workspace", "browser", "vault"):
            info = cap_health.get(pack_name, {})
            if info.get("status") in ("unconfigured", "degraded", "unhealthy", "unimplemented"):
                cap_gap = False
                break
        if cap_gap:
            gap_warnings.append(
                "No capability-pack tool calls and no degraded/unconfigured packs (item 24)"
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
