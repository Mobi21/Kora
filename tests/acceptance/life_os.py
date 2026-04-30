"""Life OS acceptance proof collection and report rendering.

The Life OS acceptance gate is intentionally DB/event backed. Conversation text
and tool-call logs are useful supporting evidence, but they cannot make a check
green without the durable state rows and ``domain_events`` rows that prove the
runtime path was wired.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LIFE_OS_TOOL_NAMES = {
    "plan_today",
    "create_day_plan",
    "record_life_event",
    "confirm_life_event",
    "confirm_reality",
    "correct_life_event",
    "correct_reality",
    "repair_day",
    "repair_day_plan",
    "apply_repair_action",
    "assess_life_load",
    "decide_nudge",
    "decide_life_nudge",
    "record_nudge_feedback",
    "build_context_pack",
    "create_context_pack",
    "enter_stabilization_mode",
    "build_future_self_bridge",
    "bridge_tomorrow",
    "activate_support_profile",
    "set_support_profile_status",
    "export_trusted_support",
    "assess_crisis_safety",
    "check_crisis_boundary",
}


@dataclass(frozen=True)
class LifeOSEvidence:
    """One durable proof requirement for a Life OS acceptance scenario."""

    label: str
    satisfied: bool
    source: str
    detail: str
    required: bool = True


@dataclass(frozen=True)
class LifeOSScenarioProof:
    """Acceptance proof for one Life OS product-center scenario."""

    key: str
    title: str
    evidence: tuple[LifeOSEvidence, ...]
    tool_calls: tuple[str, ...] = ()

    @property
    def acceptance_verified(self) -> bool:
        required = [item for item in self.evidence if item.required]
        return bool(required) and all(item.satisfied for item in required)

    @property
    def implemented(self) -> bool:
        required = [item for item in self.evidence if item.required]
        return bool(required) and all(
            not item.detail.startswith("missing ") for item in required
        )

    @property
    def debt(self) -> tuple[LifeOSEvidence, ...]:
        return tuple(item for item in self.evidence if item.required and not item.satisfied)


@dataclass(frozen=True)
class LifeOSAcceptanceSummary:
    """Collected Life OS proof plus old-suite capability-pack status."""

    available: bool
    db_path: str
    scenarios: tuple[LifeOSScenarioProof, ...] = ()
    capability_pack_status: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def acceptance_verified_count(self) -> int:
        return sum(1 for scenario in self.scenarios if scenario.acceptance_verified)

    @property
    def implemented_count(self) -> int:
        return sum(1 for scenario in self.scenarios if scenario.implemented)


class LifeOSProofCollector:
    """Query operational.db and transcript/tool-call logs for Life OS evidence."""

    def __init__(
        self,
        db_path: Path,
        *,
        messages: list[dict[str, Any]] | None = None,
        capability_pack_status: dict[str, Any] | None = None,
    ) -> None:
        self.db_path = db_path
        self.messages = messages or []
        self.capability_pack_status = capability_pack_status or {}
        self._tables: set[str] = set()
        self._columns: dict[str, set[str]] = {}

    def collect(self) -> LifeOSAcceptanceSummary:
        if not self.db_path.exists():
            return LifeOSAcceptanceSummary(
                available=False,
                db_path=str(self.db_path),
                capability_pack_status=self.capability_pack_status,
                error="operational.db not found",
            )

        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                self._load_schema(conn)
                tool_calls = self._collect_tool_calls(conn)
                scenarios = (
                    self._calendar_spine(conn, tool_calls),
                    self._adhd_week(conn, tool_calls),
                    self._autism_sensory_week(conn, tool_calls),
                    self._burnout_anxiety_week(conn, tool_calls),
                    self._confirm_reality(conn, tool_calls),
                    self._repair_day(conn, tool_calls),
                    self._wrong_inference_recovery(conn, tool_calls),
                    self._bridge_tomorrow(conn, tool_calls),
                    self._trusted_support(conn, tool_calls),
                    self._crisis_boundary(conn, tool_calls),
                    self._proactivity_suppression(conn, tool_calls),
                    self._context_packs(conn, tool_calls),
                )
        except Exception as exc:
            return LifeOSAcceptanceSummary(
                available=False,
                db_path=str(self.db_path),
                capability_pack_status=self.capability_pack_status,
                error=f"{type(exc).__name__}: {exc}",
            )

        return LifeOSAcceptanceSummary(
            available=True,
            db_path=str(self.db_path),
            scenarios=scenarios,
            capability_pack_status=self.capability_pack_status,
        )

    def _load_schema(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        self._tables = {str(row["name"]) for row in rows}
        for table in self._tables:
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            self._columns[table] = {str(col["name"]) for col in cols}

    def _has_table(self, table: str) -> bool:
        return table in self._tables

    def _has_columns(self, table: str, *columns: str) -> bool:
        return self._has_table(table) and set(columns).issubset(
            self._columns.get(table, set())
        )

    def _count(self, conn: sqlite3.Connection, table: str) -> int | None:
        if not self._has_table(table):
            return None
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        return int(row["count"]) if row else 0

    def _count_where(
        self,
        conn: sqlite3.Connection,
        table: str,
        where: str,
        params: tuple[Any, ...] = (),
        *,
        required_columns: tuple[str, ...] = (),
    ) -> int | None:
        if not self._has_table(table):
            return None
        if required_columns and not self._has_columns(table, *required_columns):
            return None
        row = conn.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE {where}", params
        ).fetchone()
        return int(row["count"]) if row else 0

    def _domain_event_count(
        self, conn: sqlite3.Connection, *event_types: str
    ) -> int | None:
        if not self._has_columns("domain_events", "event_type"):
            return None
        placeholders = ", ".join("?" for _ in event_types)
        return self._count_where(
            conn,
            "domain_events",
            f"event_type IN ({placeholders})",
            event_types,
            required_columns=("event_type",),
        )

    def _evidence_count(
        self,
        conn: sqlite3.Connection,
        *,
        label: str,
        table: str,
        minimum: int = 1,
        where: str | None = None,
        params: tuple[Any, ...] = (),
        columns: tuple[str, ...] = (),
        source: str | None = None,
    ) -> LifeOSEvidence:
        if not self._has_table(table):
            return LifeOSEvidence(
                label=label,
                satisfied=False,
                source=source or table,
                detail=f"missing table `{table}`",
            )
        if columns and not self._has_columns(table, *columns):
            missing = sorted(set(columns) - self._columns.get(table, set()))
            return LifeOSEvidence(
                label=label,
                satisfied=False,
                source=source or table,
                detail=f"missing columns on `{table}`: {', '.join(missing)}",
            )
        count = (
            self._count_where(
                conn, table, where, params, required_columns=columns
            )
            if where
            else self._count(conn, table)
        )
        actual = int(count or 0)
        return LifeOSEvidence(
            label=label,
            satisfied=actual >= minimum,
            source=source or table,
            detail=f"{actual} row(s), need >= {minimum}",
        )

    def _event_evidence(
        self,
        conn: sqlite3.Connection,
        label: str,
        *event_types: str,
    ) -> LifeOSEvidence:
        count = self._domain_event_count(conn, *event_types)
        if count is None:
            return LifeOSEvidence(
                label=label,
                satisfied=False,
                source="domain_events",
                detail="missing table `domain_events` or `event_type` column",
            )
        return LifeOSEvidence(
            label=label,
            satisfied=count > 0,
            source="domain_events",
            detail=f"{count} matching event(s): {', '.join(event_types)}",
        )

    def _collect_tool_calls(self, conn: sqlite3.Connection) -> tuple[str, ...]:
        names: list[str] = []
        for message in self.messages:
            for call in message.get("tool_calls", []) or []:
                if isinstance(call, dict):
                    name = call.get("name") or call.get("tool")
                else:
                    name = str(call)
                if name:
                    names.append(str(name))

        if self._has_columns("session_transcripts", "tool_calls"):
            rows = conn.execute(
                "SELECT tool_calls FROM session_transcripts WHERE tool_calls IS NOT NULL"
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["tool_calls"] or "[]")
                except json.JSONDecodeError:
                    continue
                for call in payload:
                    if isinstance(call, dict):
                        name = call.get("name") or call.get("tool")
                    else:
                        name = str(call)
                    if name:
                        names.append(str(name))

        return tuple(sorted(set(names)))

    def _tool_evidence(
        self, tool_calls: tuple[str, ...], expected: set[str]
    ) -> tuple[str, ...]:
        return tuple(name for name in tool_calls if name in expected)

    def _calendar_spine(
        self, conn: sqlite3.Connection, tool_calls: tuple[str, ...]
    ) -> LifeOSScenarioProof:
        return LifeOSScenarioProof(
            key="calendar_spine",
            title="Internal Calendar Spine",
            tool_calls=self._tool_evidence(
                tool_calls,
                {
                    "plan_today",
                    "create_day_plan",
                    "assess_life_load",
                    "create_reminder",
                    "create_routine",
                },
            ),
            evidence=(
                self._evidence_count(conn, label="day plan row", table="day_plans"),
                self._evidence_count(
                    conn, label="day plan entries", table="day_plan_entries"
                ),
                self._evidence_count(
                    conn, label="load assessment", table="load_assessments"
                ),
                self._event_evidence(
                    conn,
                    "calendar/domain event",
                    "DAY_PLAN_CREATED",
                    "CALENDAR_EVENT_CREATED",
                    "REMINDER_SCHEDULED",
                    "ROUTINE_CREATED",
                ),
            ),
        )

    def _support_profile_evidence(
        self, conn: sqlite3.Connection, label: str, *profile_keys: str
    ) -> LifeOSEvidence:
        if not self._has_table("support_profiles"):
            return LifeOSEvidence(
                label=label,
                satisfied=False,
                source="support_profiles",
                detail="missing table `support_profiles`",
            )
        if not self._has_columns("support_profiles", "status", "profile_key"):
            return LifeOSEvidence(
                label=label,
                satisfied=False,
                source="support_profiles",
                detail="missing columns on `support_profiles`: profile_key/status",
            )
        placeholders = ", ".join("?" for _ in profile_keys)
        count = self._count_where(
            conn,
            "support_profiles",
            f"status = 'active' AND profile_key IN ({placeholders})",
            tuple(profile_keys),
            required_columns=("status", "profile_key"),
        )
        actual = int(count or 0)
        return LifeOSEvidence(
            label=label,
            satisfied=actual >= len(profile_keys),
            source="support_profiles",
            detail=f"{actual} active matching profile(s), need >= {len(profile_keys)}",
        )

    def _support_signal_evidence(
        self, conn: sqlite3.Connection, label: str, *profile_keys: str
    ) -> LifeOSEvidence:
        if not self._has_table("support_profile_signals"):
            return LifeOSEvidence(
                label=label,
                satisfied=False,
                source="support_profile_signals",
                detail="missing table `support_profile_signals`",
            )
        if not self._has_columns("support_profile_signals", "profile_key"):
            return LifeOSEvidence(
                label=label,
                satisfied=False,
                source="support_profile_signals",
                detail="missing column on `support_profile_signals`: profile_key",
            )
        placeholders = ", ".join("?" for _ in profile_keys)
        count = self._count_where(
            conn,
            "support_profile_signals",
            f"profile_key IN ({placeholders})",
            tuple(profile_keys),
            required_columns=("profile_key",),
        )
        actual = int(count or 0)
        return LifeOSEvidence(
            label=label,
            satisfied=actual >= 1,
            source="support_profile_signals",
            detail=f"{actual} matching signal(s), need >= 1",
        )

    def _adhd_week(
        self, conn: sqlite3.Connection, tool_calls: tuple[str, ...]
    ) -> LifeOSScenarioProof:
        return LifeOSScenarioProof(
            key="adhd_week",
            title="ADHD / Executive Dysfunction Week",
            tool_calls=self._tool_evidence(
                tool_calls,
                {
                    "activate_support_profile",
                    "assess_life_load",
                    "repair_day",
                    "repair_day_plan",
                    "create_reminder",
                    "start_focus_block",
                    "end_focus_block",
                },
            ),
            evidence=(
                self._support_profile_evidence(conn, "active ADHD profile", "adhd"),
                self._support_signal_evidence(conn, "ADHD runtime signal", "adhd"),
                self._evidence_count(
                    conn,
                    label="executive-function life events",
                    table="life_events",
                    where=(
                        "event_type IN ('avoidance', 'missed_meal', 'missed_task', "
                        "'medication', 'medication_taken', 'meal_logged', "
                        "'focus_block', 'focus_block_started', 'focus_block_ended', "
                        "'quick_note_captured', 'low_energy', 'time_blindness')"
                    ),
                    columns=("event_type",),
                    source="life_events.event_type",
                ),
                self._event_evidence(
                    conn,
                    "ADHD support events",
                    "SUPPORT_SIGNAL_DETECTED",
                    "SUPPORT_PROFILE_SIGNAL_RECORDED",
                    "PLAN_REALITY_DIVERGED",
                    "DAY_PLAN_REPAIRED",
                ),
            ),
        )

    def _autism_sensory_week(
        self, conn: sqlite3.Connection, tool_calls: tuple[str, ...]
    ) -> LifeOSScenarioProof:
        return LifeOSScenarioProof(
            key="autism_sensory_week",
            title="Autism / Sensory Load Week",
            tool_calls=self._tool_evidence(
                tool_calls,
                {
                    "activate_support_profile",
                    "assess_life_load",
                    "enter_stabilization_mode",
                    "build_context_pack",
                    "create_context_pack",
                },
            ),
            evidence=(
                self._support_profile_evidence(
                    conn, "active autism/sensory profile", "autism_sensory"
                ),
                self._support_signal_evidence(
                    conn, "autism/sensory runtime signal", "autism_sensory"
                ),
                self._evidence_count(
                    conn,
                    label="sensory/transition life events",
                    table="life_events",
                    where=(
                        "event_type IN ('sensory_overload', 'transition_load', "
                        "'routine_disruption', 'communication_fatigue')"
                    ),
                    columns=("event_type",),
                    source="life_events.event_type",
                ),
                self._event_evidence(
                    conn,
                    "sensory support events",
                    "SUPPORT_SIGNAL_DETECTED",
                    "SUPPORT_PROFILE_SIGNAL_RECORDED",
                    "STABILIZATION_MODE_ENTERED",
                    "CONTEXT_PACK_READY",
                ),
            ),
        )

    def _burnout_anxiety_week(
        self, conn: sqlite3.Connection, tool_calls: tuple[str, ...]
    ) -> LifeOSScenarioProof:
        return LifeOSScenarioProof(
            key="burnout_anxiety_week",
            title="Burnout / Anxiety / Low-Energy Week",
            tool_calls=self._tool_evidence(
                tool_calls,
                {
                    "activate_support_profile",
                    "assess_life_load",
                    "enter_stabilization_mode",
                    "repair_day",
                    "repair_day_plan",
                },
            ),
            evidence=(
                self._support_profile_evidence(
                    conn,
                    "active burnout/anxiety profiles",
                    "burnout",
                    "anxiety",
                    "low_energy",
                ),
                self._support_signal_evidence(
                    conn,
                    "burnout/anxiety runtime signal",
                    "burnout",
                    "anxiety",
                    "low_energy",
                ),
                self._evidence_count(
                    conn,
                    label="low-energy load assessment",
                    table="load_assessments",
                    where=(
                        "band IN ('high', 'overloaded', 'stabilization', "
                        "'low_energy', 'shutdown')"
                    ),
                    columns=("band",),
                    source="load_assessments.band",
                ),
                self._event_evidence(
                    conn,
                    "burnout/anxiety support events",
                    "STABILIZATION_MODE_ENTERED",
                    "DAY_PLAN_REPAIRED",
                    "SUPPORT_SIGNAL_DETECTED",
                    "SUPPORT_PROFILE_SIGNAL_RECORDED",
                    "SUPPORT_PROFILE_STATUS_CHANGED",
                ),
            ),
        )

    def _confirm_reality(
        self, conn: sqlite3.Connection, tool_calls: tuple[str, ...]
    ) -> LifeOSScenarioProof:
        return LifeOSScenarioProof(
            key="confirm_reality",
            title="Confirm Reality",
            tool_calls=self._tool_evidence(
                tool_calls,
                {
                    "record_life_event",
                    "confirm_life_event",
                    "correct_life_event",
                    "confirm_reality",
                    "correct_reality",
                },
            ),
            evidence=(
                self._evidence_count(
                    conn, label="life event rows", table="life_events", minimum=3
                ),
                self._event_evidence(
                    conn,
                    "life event domain event",
                    "LIFE_EVENT_RECORDED",
                    "LIFE_EVENT_CORRECTED",
                ),
                self._evidence_count(
                    conn,
                    label="day plan reality state",
                    table="day_plan_entries",
                    where=(
                        "reality_state IS NOT NULL "
                        "AND reality_state IN ('done', 'partial', 'skipped', "
                        "'blocked', 'corrected', 'rejected', 'confirmed_done', "
                        "'confirmed_partial', 'confirmed_skipped', "
                        "'confirmed_blocked', 'rejected_inference')"
                    ),
                    columns=("reality_state",),
                    source="day_plan_entries.reality_state",
                ),
            ),
        )

    def _repair_day(
        self, conn: sqlite3.Connection, tool_calls: tuple[str, ...]
    ) -> LifeOSScenarioProof:
        return LifeOSScenarioProof(
            key="repair_day",
            title="Repair The Day",
            tool_calls=self._tool_evidence(
                tool_calls, {"repair_day", "repair_day_plan", "apply_repair_action"}
            ),
            evidence=(
                self._evidence_count(
                    conn, label="repair action row", table="plan_repair_actions"
                ),
                self._evidence_count(
                    conn,
                    label="applied or proposed repair action",
                    table="plan_repair_actions",
                    where=(
                        "status IN ('proposed', 'awaiting_confirmation', "
                        "'approved', 'applied')"
                    ),
                    columns=("status",),
                    source="plan_repair_actions.status",
                ),
                self._evidence_count(
                    conn,
                    label="day plan revisions preserved",
                    table="day_plans",
                    minimum=2,
                ),
                self._event_evidence(
                    conn,
                    "repair domain events",
                    "PLAN_REALITY_DIVERGED",
                    "DAY_PLAN_REPAIRED",
                ),
            ),
        )

    def _bridge_tomorrow(
        self, conn: sqlite3.Connection, tool_calls: tuple[str, ...]
    ) -> LifeOSScenarioProof:
        return LifeOSScenarioProof(
            key="bridge_tomorrow",
            title="Bridge Tomorrow",
            tool_calls=self._tool_evidence(tool_calls, {"build_future_self_bridge"}),
            evidence=(
                self._evidence_count(
                    conn, label="future bridge row", table="future_self_bridges"
                ),
                self._event_evidence(
                    conn,
                    "future bridge domain event",
                    "FUTURE_SELF_BRIDGE_READY",
                    "FUTURE_SELF_BRIDGE_CREATED",
                ),
            ),
        )

    def _wrong_inference_recovery(
        self, conn: sqlite3.Connection, tool_calls: tuple[str, ...]
    ) -> LifeOSScenarioProof:
        return LifeOSScenarioProof(
            key="wrong_inference_recovery",
            title="Wrong Inference Recovery",
            tool_calls=self._tool_evidence(
                tool_calls,
                {
                    "correct_life_event",
                    "correct_reality",
                    "repair_day",
                    "repair_day_plan",
                    "activate_support_profile",
                    "set_support_profile_status",
                },
            ),
            evidence=(
                self._event_evidence(
                    conn,
                    "correction domain event",
                    "LIFE_EVENT_CORRECTED",
                    "WRONG_INFERENCE_REPAIRED",
                    "SUPPORT_PROFILE_CORRECTED",
                    "SUPPORT_PROFILE_SIGNAL_RECORDED",
                ),
                self._evidence_count(
                    conn,
                    label="corrected reality state",
                    table="day_plan_entries",
                    where="reality_state IN ('corrected', 'rejected', 'rejected_inference')",
                    columns=("reality_state",),
                    source="day_plan_entries.reality_state",
                ),
            ),
        )

    def _trusted_support(
        self, conn: sqlite3.Connection, tool_calls: tuple[str, ...]
    ) -> LifeOSScenarioProof:
        return LifeOSScenarioProof(
            key="trusted_support",
            title="Trusted Support Boundary",
            tool_calls=self._tool_evidence(tool_calls, {"export_trusted_support"}),
            evidence=(
                self._evidence_count(
                    conn,
                    label="trusted support profile",
                    table="support_profiles",
                    where=(
                        "status = 'active' AND profile_key IN "
                        "('trusted_support', 'support_boundary')"
                    ),
                    columns=("status", "profile_key"),
                    source="support_profiles",
                ),
                self._event_evidence(
                    conn,
                    "trusted support event",
                    "TRUSTED_SUPPORT_EXPORT_CREATED",
                    "TRUSTED_SUPPORT_CONSENT_RECORDED",
                    "TRUSTED_SUPPORT_EXPORT_DRAFTED",
                    "TRUSTED_SUPPORT_EXPORT_REVIEWED",
                ),
            ),
        )

    def _support_modules(
        self, conn: sqlite3.Connection, tool_calls: tuple[str, ...]
    ) -> LifeOSScenarioProof:
        profile_keys = (
            "adhd",
            "anxiety",
            "autism_sensory",
            "low_energy",
            "burnout",
        )
        profiles = self._evidence_count(
            conn,
            label="active support profiles",
            table="support_profiles",
            minimum=len(profile_keys),
            where=(
                "status = 'active' AND profile_key IN "
                "('adhd', 'anxiety', 'autism_sensory', 'low_energy', 'burnout')"
            ),
            columns=("status", "profile_key"),
        )
        return LifeOSScenarioProof(
            key="support_modules",
            title="Support Module Behavior",
            tool_calls=self._tool_evidence(tool_calls, {"activate_support_profile"}),
            evidence=(
                profiles,
                self._evidence_count(
                    conn,
                    label="support profile runtime signals",
                    table="support_profile_signals",
                ),
                self._event_evidence(
                    conn,
                    "support domain event",
                    "SUPPORT_SIGNAL_DETECTED",
                    "SUPPORT_PROFILE_SIGNAL_RECORDED",
                    "SUPPORT_PROFILE_ACTIVATED",
                    "SUPPORT_PROFILE_STATUS_CHANGED",
                ),
            ),
        )

    def _crisis_boundary(
        self, conn: sqlite3.Connection, tool_calls: tuple[str, ...]
    ) -> LifeOSScenarioProof:
        return LifeOSScenarioProof(
            key="crisis_boundary",
            title="Crisis Safety Boundary",
            tool_calls=self._tool_evidence(tool_calls, {"assess_crisis_safety"}),
            evidence=(
                self._event_evidence(
                    conn,
                    "crisis boundary domain event",
                    "SAFETY_BOUNDARY_TRIGGERED",
                    "CRISIS_SAFETY_PREEMPTED",
                ),
                self._crisis_no_normal_workflow_evidence(conn),
            ),
        )

    def _crisis_no_normal_workflow_evidence(
        self, conn: sqlite3.Connection
    ) -> LifeOSEvidence:
        if not self._has_columns("domain_events", "event_type"):
            return LifeOSEvidence(
                label="normal workflow suppressed",
                satisfied=False,
                source="domain_events",
                detail="missing domain event surface",
            )
        crisis_count = (
            self._domain_event_count(
                conn, "SAFETY_BOUNDARY_TRIGGERED", "CRISIS_SAFETY_PREEMPTED"
            )
            or 0
        )
        if crisis_count == 0:
            return LifeOSEvidence(
                label="normal workflow suppressed",
                satisfied=False,
                source="domain_events",
                detail="no crisis boundary event to evaluate",
            )
        if not self._has_table("plan_repair_actions") and not self._has_table(
            "nudge_decisions"
        ):
            return LifeOSEvidence(
                label="normal workflow suppressed",
                satisfied=True,
                source="plan_repair_actions,nudge_decisions",
                detail="normal workflow tables absent; no linked rows possible",
            )
        event_ids, correlations = self._crisis_event_identifiers(conn)
        if not event_ids and not correlations:
            return LifeOSEvidence(
                label="normal workflow suppressed",
                satisfied=False,
                source="domain_events",
                detail="crisis events have no ids/correlation ids for suppression proof",
            )

        repair_result = self._linked_crisis_workflow_count(
            conn,
            table="plan_repair_actions",
            event_ids=event_ids,
            correlations=correlations,
        )
        nudge_suffix = (
            " AND decision NOT IN ('suppress', 'suppressed', 'defer', "
            "'deferred', 'queue', 'queued')"
            if self._has_columns("nudge_decisions", "decision")
            else ""
        )
        nudge_result = self._linked_crisis_workflow_count(
            conn,
            table="nudge_decisions",
            event_ids=event_ids,
            correlations=correlations,
            where_suffix=nudge_suffix,
        )
        missing_link_tables = [
            table
            for table, result in (
                ("plan_repair_actions", repair_result),
                ("nudge_decisions", nudge_result),
            )
            if result is None and self._has_table(table)
        ]
        if "nudge_decisions" in missing_link_tables:
            nudge_result = self._unlinked_non_suppressed_nudge_count(conn)
            missing_link_tables.remove("nudge_decisions")
        if missing_link_tables:
            return LifeOSEvidence(
                label="normal workflow suppressed",
                satisfied=False,
                source=",".join(missing_link_tables),
                detail=(
                    "cannot prove crisis suppression; missing source_event_id "
                    "or correlation_id link columns"
                ),
            )

        repair_count = repair_result or 0
        nudge_count = nudge_result or 0
        return LifeOSEvidence(
            label="normal workflow suppressed",
            satisfied=repair_count == 0 and nudge_count == 0,
            source="plan_repair_actions,nudge_decisions",
            detail=(
                f"{repair_count} linked repair action(s), "
                f"{nudge_count} linked non-suppressed nudge(s)"
            ),
        )

    def _unlinked_non_suppressed_nudge_count(self, conn: sqlite3.Connection) -> int:
        if not self._has_table("nudge_decisions"):
            return 0
        if not self._has_columns("nudge_decisions", "decision"):
            return 0
        return self._count_where(
            conn,
            "nudge_decisions",
            (
                "decision NOT IN ('suppress', 'suppressed', 'defer', "
                "'deferred', 'queue', 'queued')"
            ),
        )

    def _crisis_event_identifiers(
        self, conn: sqlite3.Connection
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if not self._has_columns("domain_events", "event_type"):
            return (), ()
        columns = ["id"] if self._has_columns("domain_events", "id") else []
        if self._has_columns("domain_events", "correlation_id"):
            columns.append("correlation_id")
        if not columns:
            return (), ()
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM domain_events WHERE event_type = ?",
            ("SAFETY_BOUNDARY_TRIGGERED",),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM domain_events WHERE event_type = ?",
                ("CRISIS_SAFETY_PREEMPTED",),
            ).fetchall()
        event_ids = tuple(
            str(row["id"]) for row in rows if "id" in row.keys() and row["id"]
        )
        correlations = tuple(
            str(row["correlation_id"])
            for row in rows
            if "correlation_id" in row.keys() and row["correlation_id"]
        )
        return event_ids, correlations

    def _linked_crisis_workflow_count(
        self,
        conn: sqlite3.Connection,
        *,
        table: str,
        event_ids: tuple[str, ...],
        correlations: tuple[str, ...],
        where_suffix: str = "",
    ) -> int | None:
        if not self._has_table(table):
            return 0
        clauses: list[str] = []
        params: list[str] = []
        if self._has_columns(table, "source_event_id"):
            if event_ids:
                placeholders = ", ".join("?" for _ in event_ids)
                clauses.append(f"source_event_id IN ({placeholders})")
                params.extend(event_ids)
        if self._has_columns(table, "correlation_id") and correlations:
            placeholders = ", ".join("?" for _ in correlations)
            clauses.append(f"correlation_id IN ({placeholders})")
            params.extend(correlations)
        if not clauses:
            return None
        where = f"({' OR '.join(clauses)}){where_suffix}"
        return self._count_where(conn, table, where, tuple(params))

    def _proactivity_suppression(
        self, conn: sqlite3.Connection, tool_calls: tuple[str, ...]
    ) -> LifeOSScenarioProof:
        return LifeOSScenarioProof(
            key="proactivity_suppression",
            title="Proactivity Suppression",
            tool_calls=self._tool_evidence(
                tool_calls, {"decide_nudge", "decide_life_nudge", "record_nudge_feedback"}
            ),
            evidence=(
                self._evidence_count(
                    conn,
                    label="suppressed/deferred nudge decision",
                    table="nudge_decisions",
                    where=(
                        "decision IN ('suppress', 'suppressed', 'defer', "
                        "'deferred', 'queue', 'queued')"
                    ),
                    columns=("decision",),
                ),
                self._event_evidence(
                    conn,
                    "nudge decision domain event",
                    "NUDGE_DECISION_RECORDED",
                    "NUDGE_FEEDBACK_RECEIVED",
                    "NUDGE_FEEDBACK_RECORDED",
                ),
            ),
        )

    def _context_packs(
        self, conn: sqlite3.Connection, tool_calls: tuple[str, ...]
    ) -> LifeOSScenarioProof:
        return LifeOSScenarioProof(
            key="context_packs",
            title="Context Packs",
            tool_calls=self._tool_evidence(tool_calls, {"build_context_pack", "create_context_pack"}),
            evidence=(
                self._evidence_count(conn, label="context pack row", table="context_packs"),
                self._event_evidence(
                    conn, "context pack domain event", "CONTEXT_PACK_READY"
                ),
            ),
        )


def collect_life_os_acceptance(
    db_path: Path,
    *,
    messages: list[dict[str, Any]] | None = None,
    capability_pack_status: dict[str, Any] | None = None,
) -> LifeOSAcceptanceSummary:
    """Collect Life OS acceptance evidence from DB and tool-call logs."""

    return LifeOSProofCollector(
        db_path,
        messages=messages,
        capability_pack_status=capability_pack_status,
    ).collect()


def render_life_os_acceptance(
    summary: LifeOSAcceptanceSummary,
    *,
    manual_verification: dict[str, Any] | None = None,
) -> list[str]:
    """Render the Life OS report section.

    The section separates implementation presence, manual verification notes,
    acceptance-verified proof, remaining debt, and old capability-pack status.
    """

    manual_verification = manual_verification or {}
    lines: list[str] = ["\n## Life OS Acceptance"]
    lines.append(
        "Product-center gate: calendar spine plus separate ADHD, autism/sensory, "
        "and burnout/anxiety lived-week proof."
    )
    lines.append(
        "Tool calls are supporting evidence only; green checks require durable DB rows and domain events."
    )

    if not summary.available:
        lines.append(f"- DB: unavailable at `{summary.db_path}`")
        if summary.error:
            lines.append(f"- Error: {summary.error}")
        lines.extend(_render_capability_pack_status(summary.capability_pack_status))
        return lines

    lines.append(f"- DB: `{summary.db_path}`")
    lines.append(
        f"- Acceptance verified: {summary.acceptance_verified_count}/{len(summary.scenarios)} scenarios"
    )
    lines.append(
        f"- Implemented surfaces present: {summary.implemented_count}/{len(summary.scenarios)} scenarios"
    )

    lines.append("\n### Implemented")
    implemented = [s for s in summary.scenarios if s.implemented]
    if implemented:
        for scenario in implemented:
            lines.append(f"- {scenario.title}")
    else:
        lines.append("- None proven by the current DB schema.")

    lines.append("\n### Manually Verified")
    if manual_verification:
        for key, value in sorted(manual_verification.items()):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- No manual Life OS verification notes were attached to this report.")

    lines.append("\n### Acceptance Verified")
    verified = [s for s in summary.scenarios if s.acceptance_verified]
    if verified:
        for scenario in verified:
            tool_text = (
                f" tools={', '.join(scenario.tool_calls)}"
                if scenario.tool_calls
                else " tools=(none logged)"
            )
            lines.append(f"- [x] {scenario.title};{tool_text}")
    else:
        lines.append("- No Life OS scenario is fully acceptance-verified yet.")

    lines.append("\n### Evidence Detail")
    for scenario in summary.scenarios:
        marker = "x" if scenario.acceptance_verified else " "
        lines.append(f"- [{marker}] {scenario.title}")
        for item in scenario.evidence:
            item_marker = "x" if item.satisfied else " "
            req = "required" if item.required else "supporting"
            lines.append(
                f"  - [{item_marker}] {item.label} ({req}, {item.source}): {item.detail}"
            )
        if scenario.tool_calls:
            lines.append(f"  - Tool calls logged: {', '.join(scenario.tool_calls)}")

    lines.append("\n### Remaining Debt")
    debts = [
        (scenario.title, item)
        for scenario in summary.scenarios
        for item in scenario.debt
    ]
    if debts:
        for title, item in debts:
            lines.append(f"- {title}: {item.label} -> {item.detail}")
    else:
        lines.append("- None from Life OS acceptance proof collector.")

    lines.extend(_render_capability_pack_status(summary.capability_pack_status))
    return lines


def _render_capability_pack_status(capability_pack_status: dict[str, Any]) -> list[str]:
    lines = ["\n### Old Suite Capability-Pack Status"]
    lines.append(
        "Old coding/research/writing capability-pack checks are reported separately and do not gate Life OS core."
    )
    if not capability_pack_status:
        lines.append("- No capability-pack health data available.")
        return lines

    for name, info in sorted(capability_pack_status.items()):
        if isinstance(info, dict):
            status = info.get("status", "unknown")
            summary = info.get("summary", "")
            lines.append(f"- {name}: status={status}; {summary}")
        else:
            lines.append(f"- {name}: {info}")
    return lines
