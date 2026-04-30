"""Desktop view-model assembly for the Electron renderer."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from kora_v2 import __version__
from kora_v2.desktop.models import (
    AutonomousCheckpointView,
    AutonomousDecisionView,
    AutonomousPlanView,
    AutonomousView,
    CalendarEditPreview,
    CalendarEditRequest,
    CalendarEditResult,
    CalendarEventView,
    CalendarLayerState,
    CalendarRangeView,
    ContextPackSummary,
    DesktopSettings,
    DesktopStatusView,
    FutureBridgeSummary,
    IntegrationStatusView,
    IntegrationsView,
    IntegrationToolView,
    LoadState,
    MedicationDayView,
    MedicationDose,
    MedicationLogPreview,
    MedicationLogRequest,
    MedicationLogResult,
    RepairActionPreview,
    RepairApplyRequest,
    RepairApplyResult,
    RepairPreview,
    RepairPreviewRequest,
    RepairStateView,
    RoutineActionRequest,
    RoutineActionResult,
    RoutineDayView,
    RoutineRunView,
    RoutineStepView,
    SettingsValidationIssue,
    SettingsValidationView,
    TimelineItem,
    TodayBlock,
    TodayViewModel,
    VaultContextView,
    VaultCorrectionPreview,
    VaultCorrectionRequest,
    VaultCorrectionResult,
    VaultMemoryItem,
    VaultSearchView,
    VaultState,
)


class DesktopViewService:
    """Build UI-facing desktop view models from Kora runtime services."""

    def __init__(self, container: Any) -> None:
        self.container = container
        self.settings = container.settings
        self.db_path = self.settings.data_dir / "operational.db"
        self.settings_path = self.settings.data_dir / "desktop_settings.json"

    async def status(self) -> DesktopStatusView:
        session_mgr = getattr(self.container, "session_manager", None)
        session = session_mgr.active_session if session_mgr else None
        failed = getattr(self.container, "_failed_subsystems", [])
        engine = getattr(self.container, "_orchestration_engine", None)
        pipeline_count = 0
        if engine is not None:
            try:
                pipeline_count = len(engine.pipelines.all())
            except Exception:
                pipeline_count = 0
        return DesktopStatusView(
            status="degraded" if failed else "connected",
            version=__version__,
            host=self.settings.daemon.host,
            port=self.settings.daemon.port,
            session_active=session is not None,
            session_id=session.session_id if session else None,
            turn_count=session.turn_count if session else 0,
            failed_subsystems=list(failed),
            orchestration_pipelines=pipeline_count,
            vault=self._vault_state(),
            support_mode=await self._active_support_mode(),
            generated_at=_now(),
        )

    async def today(self, day: date) -> TodayViewModel:
        plan = None
        service = getattr(self.container, "day_plan_service", None)
        if service is not None:
            try:
                plan = await service.get_active_day_plan(day)
            except Exception:
                plan = None

        entries = list(getattr(plan, "entries", []) if plan else [])
        items = [_timeline_from_entry(entry) for entry in entries]
        reminder_items = await self._reminder_timeline_items(day)
        existing_reminder_ids = {
            item.id.removeprefix("reminder:")
            for item in items
            if item.id.startswith("reminder:") or "reminder" in item.provenance
        }
        items.extend(
            item
            for item in reminder_items
            if item.id.removeprefix("reminder:") not in existing_reminder_ids
        )
        items.sort(key=lambda item: item.starts_at or datetime.max.replace(tzinfo=UTC))
        now_dt = _now()
        now_items = [
            item for item in items
            if item.starts_at and item.starts_at <= now_dt and (item.ends_at is None or item.ends_at >= now_dt)
        ]
        next_items = [
            item for item in items
            if item.starts_at and item.starts_at > now_dt and item.starts_at < now_dt + timedelta(hours=4)
        ][:5]
        later_items = [item for item in items if item not in now_items and item not in next_items]
        undated_later = [item for item in later_items if item.starts_at is None]
        dated_later = [item for item in later_items if item.starts_at is not None]
        later_items = [*dated_later, *undated_later][:12]

        load = await self._latest_load(day)
        return TodayViewModel(
            date=day,
            plan_id=getattr(plan, "id", None),
            revision=getattr(plan, "revision", None),
            summary=getattr(plan, "summary", None),
            now=TodayBlock(
                title="Now",
                subtitle="Reality anchor for the current moment",
                items=now_items,
                empty_label="No active block is confirmed right now.",
            ),
            next=TodayBlock(
                title="Next",
                subtitle="Near-future commitments, prep, and transitions",
                items=next_items,
                empty_label="Nothing scheduled in the next few hours.",
            ),
            later=TodayBlock(
                title="Later",
                subtitle="Flexible items, routines, and tomorrow bridge candidates",
                items=later_items,
                empty_label="No later items are on the current plan.",
            ),
            timeline=items,
            load=load,
            support_mode=load.recommended_mode,
            repair_available=any(item.risk == "repair" for item in items) or load.band in {"high", "overloaded", "stabilization"},
            generated_at=_now(),
        )

    async def calendar(self, start: datetime, end: datetime) -> CalendarRangeView:
        events: list[CalendarEventView] = []
        if self.db_path.exists() and await _table_exists(self.db_path, "calendar_entries"):
            from kora_v2.tools.calendar import _load_entries_between

            async with aiosqlite.connect(str(self.db_path)) as db:
                for entry in await _load_entries_between(db, start, end):
                    metadata = dict(getattr(entry, "metadata", {}) or {})
                    kind = str(getattr(entry, "kind", "event"))
                    layer_ids = _layers_for_kind(kind, metadata)
                    events.append(
                        CalendarEventView(
                            id=entry.id,
                            title=entry.title,
                            kind=kind,
                            starts_at=entry.starts_at,
                            ends_at=entry.ends_at,
                            all_day=bool(entry.all_day),
                            source=entry.source,
                            status=entry.status,
                            layer_ids=layer_ids,
                            provenance=_event_provenance(entry.source, metadata),
                            metadata=metadata,
                        )
                    )
        settings = await self.get_settings()
        reminder_events = await self._reminder_calendar_events(start, end)
        existing_event_ids = {event.id for event in events}
        events.extend(event for event in reminder_events if event.id not in existing_event_ids)
        events.sort(key=lambda event: event.starts_at)
        return CalendarRangeView(
            start=start,
            end=end,
            default_view=settings.calendar_default_view if settings.calendar_default_view in {"day", "week", "month", "agenda"} else "week",
            layers=_calendar_layers(settings.calendar_layers),
            events=events,
            quiet_hours={"start": _time_or_none(self.settings.notifications.dnd_start), "end": _time_or_none(self.settings.notifications.dnd_end)},
            working_hours={"start": "09:00", "end": "17:00"},
            generated_at=_now(),
        )

    async def repair_state(self, day: date) -> RepairStateView:
        today = await self.today(day)
        preview = await self.repair_preview(RepairPreviewRequest(date=day))
        broken = [item for item in today.timeline if item.risk == "repair"]
        protected = [item for item in today.timeline if item.item_type in {"event", "calendar"}]
        flexible = [item for item in today.timeline if item.item_type not in {"event", "calendar"}]
        return RepairStateView(
            date=day,
            day_plan_id=today.plan_id,
            what_changed_options=[
                "I'm behind",
                "Too tired",
                "Event changed",
                "Skipped something",
                "Need to move things",
                "Need a smaller version",
                "Need tomorrow help",
            ],
            broken_or_at_risk=broken,
            suggested_repairs=preview.actions,
            protected_commitments=protected[:8],
            flexible_items=flexible[:10],
            move_to_tomorrow=[item for item in flexible if item.status in {"planned", "active"}][:6],
            generated_at=_now(),
        )

    async def repair_preview(self, request: RepairPreviewRequest) -> RepairPreview:
        evaluation = None
        engine = getattr(self.container, "day_repair_engine", None)
        if engine is not None:
            try:
                evaluation = await engine.evaluate(request.date)
            except Exception:
                evaluation = None
        actions = _actions_from_evaluation(evaluation, request)
        if not actions and request.change_type:
            actions = [_generic_repair_action(request)]
        return RepairPreview(
            date=request.date,
            day_plan_id=getattr(evaluation, "day_plan_id", None),
            summary=(
                "Preview only. No plan changes are applied until you choose Apply Repair."
                if actions
                else "No repair actions are needed for this date."
            ),
            actions=actions,
            mutates_state=False,
            generated_at=_now(),
        )

    async def repair_apply(self, request: RepairApplyRequest) -> RepairApplyResult:
        engine = getattr(self.container, "day_repair_engine", None)
        if engine is None:
            return RepairApplyResult(status="unavailable", message="Repair engine is unavailable.")
        try:
            evaluation = await engine.evaluate(request.date)
            proposed = await engine.propose(evaluation)
            selected = proposed
            if request.preview_action_ids:
                selected_indexes = _preview_indexes(request.preview_action_ids)
                selected = [action for index, action in enumerate(proposed) if index in selected_indexes]
            result = await engine.apply([action.id for action in selected], user_confirmed=request.user_confirmed)
        except Exception as exc:
            return RepairApplyResult(status="unavailable", message=f"Repair could not be applied: {exc}")
        status = "applied" if result.applied_action_ids else "skipped"
        return RepairApplyResult(
            status=status,
            applied_action_ids=result.applied_action_ids,
            skipped_action_ids=result.skipped_action_ids,
            new_day_plan_id=result.new_day_plan_id,
            message="Repair applied." if status == "applied" else "No repair actions were applied.",
        )

    async def vault_search(self, query: str) -> VaultSearchView:
        results = await self._memory_items(query=query, limit=24)
        return VaultSearchView(query=query, results=results, vault=self._vault_state(), generated_at=_now())

    async def vault_context(self) -> VaultContextView:
        recent = await self._memory_items(query="", limit=12)
        corrections = [item for item in recent if item.certainty == "correction"]
        uncertain = [item for item in recent if item.certainty in {"guess", "stale", "unknown"}]
        return VaultContextView(
            vault=self._vault_state(),
            recent_memories=recent,
            corrections=corrections,
            uncertain_or_stale=uncertain,
            context_packs=await self._context_packs(),
            future_bridges=await self._future_bridges(),
            generated_at=_now(),
        )

    async def get_settings(self) -> DesktopSettings:
        if self.settings_path.exists():
            try:
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
                return DesktopSettings.model_validate(data)
            except Exception:
                pass
        settings = DesktopSettings(calendar_layers={layer.id: layer.enabled for layer in _calendar_layers({})})
        await self.save_settings(settings)
        return settings

    async def patch_settings(self, patch: dict[str, Any]) -> DesktopSettings:
        current = await self.get_settings()
        data = current.model_dump(mode="json")
        allowed = set(data)
        for key, value in patch.items():
            if key in allowed:
                data[key] = value
        data["updated_at"] = _now().isoformat()
        settings = DesktopSettings.model_validate(data)
        await self.save_settings(settings)
        return settings

    async def save_settings(self, settings: DesktopSettings) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")

    async def _latest_load(self, day: date) -> LoadState:
        engine = getattr(self.container, "life_load_engine", None)
        if engine is not None:
            try:
                assessment = await engine.assess_day(day)
                return LoadState(
                    band=assessment.band,
                    score=assessment.score,
                    recommended_mode=assessment.recommended_mode,
                    factors=[factor.label for factor in assessment.factors[:5]],
                    confidence=assessment.confidence,
                )
            except Exception:
                pass
        return LoadState()

    async def _active_support_mode(self) -> str:
        if not self.db_path.exists() or not await _table_exists(self.db_path, "support_mode_state"):
            return "normal"
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    """
                    SELECT mode FROM support_mode_state
                    WHERE ended_at IS NULL
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                )
            ).fetchone()
        return str(row["mode"]) if row else "normal"

    async def _memory_items(self, *, query: str, limit: int) -> list[VaultMemoryItem]:
        items: list[VaultMemoryItem] = await self._quick_note_memory_items(
            query=query,
            limit=limit,
        )
        store = getattr(self.container, "memory_store", None)
        if store is None:
            return items[:limit]
        try:
            notes = await store.list_notes(
                layer="all",
                limit=max(limit * 4, 32),
                newest_first=True,
            )
        except Exception:
            return []
        if query:
            q = query.lower()
            notes = [
                note for note in notes
                if q in note.id.lower()
                or q in note.memory_type.lower()
                or q in " ".join(note.tags).lower()
                or q in " ".join(note.entities).lower()
            ]
        for note in notes:
            if len(items) >= limit:
                break
            full = await store.read_note(note.id)
            body = full.body if full else ""
            if _hidden_memory_note(note, body):
                continue
            items.append(
                VaultMemoryItem(
                    id=note.id,
                    title=_memory_title(body, note),
                    body_preview=_preview(body),
                    memory_type=note.memory_type,
                    certainty=_certainty(note.tags, body),
                    tags=note.tags,
                    entities=note.entities,
                    provenance=_memory_provenance(note),
                    vault_note_path=note.source_path,
                    updated_at=note.updated_at or note.created_at,
                )
            )
        return items

    async def _quick_note_memory_items(
        self,
        *,
        query: str,
        limit: int,
    ) -> list[VaultMemoryItem]:
        if not self.db_path.exists() or not await _table_exists(self.db_path, "quick_notes"):
            return []
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT id, content, tags, created_at
                    FROM quick_notes
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (max(limit, 12),),
                )
            ).fetchall()
        q = query.lower().strip()
        items: list[VaultMemoryItem] = []
        for row in rows:
            content = str(row["content"] or "")
            tags = [tag.strip() for tag in str(row["tags"] or "").split(",") if tag.strip()]
            haystack = " ".join([content, *tags]).lower()
            if q and q not in haystack:
                continue
            items.append(
                VaultMemoryItem(
                    id=f"quick_note:{row['id']}",
                    title=next(
                        (line.strip("# ").strip() for line in content.splitlines() if line.strip()),
                        "quick_note memory",
                    )[:80],
                    body_preview=_preview(content),
                    memory_type="quick_note",
                    certainty=_certainty(tags, content),
                    tags=tags,
                    entities=[],
                    provenance=["quick_notes"],
                    vault_note_path=None,
                    updated_at=str(row["created_at"] or ""),
                )
            )
            if len(items) >= limit:
                break
        return items

    async def _context_packs(self) -> list[ContextPackSummary]:
        if not self.db_path.exists() or not await _table_exists(self.db_path, "context_packs"):
            return []
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    "SELECT id, title, pack_type, content_path AS artifact_path, created_at FROM context_packs ORDER BY created_at DESC LIMIT 8"
                )
            ).fetchall()
        return [ContextPackSummary(**dict(row)) for row in rows]

    async def _future_bridges(self) -> list[FutureBridgeSummary]:
        if not self.db_path.exists() or not await _table_exists(self.db_path, "future_self_bridges"):
            return []
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    "SELECT id, summary, bridge_date AS to_date, content_path AS artifact_path FROM future_self_bridges ORDER BY created_at DESC LIMIT 8"
                )
            ).fetchall()
        return [FutureBridgeSummary(**dict(row)) for row in rows]

    # ── Calendar mutations ────────────────────────────────────────────────

    async def calendar_preview(self, request: CalendarEditRequest) -> CalendarEditPreview:
        before = await self._find_calendar_event(request.event_id) if request.event_id else None
        after: CalendarEventView | None = None
        if request.operation in {"move", "resize", "create"}:
            after = (before.model_copy() if before else _new_calendar_event(request))
            if request.starts_at is not None:
                after.starts_at = request.starts_at
            if request.ends_at is not None:
                after.ends_at = request.ends_at
            if request.title is not None:
                after.title = request.title
        elif request.operation == "cancel":
            after = None
        summary = _calendar_preview_summary(request, before, after)
        return CalendarEditPreview(
            operation=request.operation,
            event_id=request.event_id,
            before=before,
            after=after,
            conflicts=[],
            summary=summary,
            mutates_state=False,
            requires_confirmation=True,
            generated_at=_now(),
        )

    async def calendar_apply(self, request: CalendarEditRequest) -> CalendarEditResult:
        return CalendarEditResult(
            status="unavailable",
            event_id=request.event_id,
            message=(
                "Calendar mutations are not yet wired through the desktop API. "
                "Use Kora chat for calendar edits until the desktop mutation pipeline ships."
            ),
        )

    # ── Medication ───────────────────────────────────────────────────────

    async def medication(self, day: date) -> MedicationDayView:
        manager = getattr(self.container, "medication_manager", None)
        if manager is None:
            return MedicationDayView(
                date=day,
                enabled=False,
                health="unconfigured",
                message="Medication tracking is not configured for this profile.",
                generated_at=_now(),
            )
        try:
            doses_raw = await manager.list_doses(day) if hasattr(manager, "list_doses") else []
        except Exception as exc:
            return MedicationDayView(
                date=day,
                enabled=True,
                health="unavailable",
                message=f"Medication subsystem unavailable: {exc}",
                generated_at=_now(),
            )
        doses = [_dose_view(raw) for raw in doses_raw]
        history: dict[str, int] = {}
        for dose in doses:
            history[dose.status] = history.get(dose.status, 0) + 1
        last_taken = max(
            (dose.scheduled_at for dose in doses if dose.status == "taken" and dose.scheduled_at),
            default=None,
        )
        return MedicationDayView(
            date=day,
            enabled=True,
            doses=doses,
            history_summary=history,
            last_taken_at=last_taken,
            health_signals=[],
            health="ok",
            generated_at=_now(),
        )

    async def medication_preview(self, request: MedicationLogRequest) -> MedicationLogPreview:
        before = MedicationDose(
            id=request.dose_id,
            medication_id="unknown",
            name="Medication dose",
            dose_label="—",
            status="pending",
        )
        after = before.model_copy(update={"status": request.status, "notes": request.note})
        return MedicationLogPreview(
            dose_id=request.dose_id,
            before=before,
            after=after,
            summary=_medication_preview_summary(request),
            mutates_state=False,
            generated_at=_now(),
        )

    async def medication_apply(self, request: MedicationLogRequest) -> MedicationLogResult:
        manager = getattr(self.container, "medication_manager", None)
        if manager is None or not hasattr(manager, "log_dose"):
            return MedicationLogResult(
                status="unavailable",
                dose_id=request.dose_id,
                message="Medication logging service is not yet available.",
            )
        try:
            await manager.log_dose(
                dose_id=request.dose_id,
                status=request.status,
                note=request.note,
                occurred_at=request.occurred_at,
            )
        except Exception as exc:
            return MedicationLogResult(
                status="unavailable",
                dose_id=request.dose_id,
                message=f"Failed to log dose: {exc}",
            )
        return MedicationLogResult(
            status="applied",
            dose_id=request.dose_id,
            message=f"Dose marked {request.status}.",
        )

    # ── Routines ────────────────────────────────────────────────────────

    async def routines(self, day: date) -> RoutineDayView:
        manager = getattr(self.container, "routine_manager", None)
        if manager is None:
            return RoutineDayView(
                date=day,
                health="unconfigured",
                message="Routines are not configured for this profile.",
                generated_at=_now(),
            )
        try:
            runs_raw = (
                await manager.list_runs_for_date(day) if hasattr(manager, "list_runs_for_date") else []
            )
            upcoming_raw = (
                await manager.list_upcoming(day) if hasattr(manager, "list_upcoming") else []
            )
        except Exception as exc:
            return RoutineDayView(
                date=day,
                health="unavailable",
                message=f"Routine subsystem unavailable: {exc}",
                generated_at=_now(),
            )
        return RoutineDayView(
            date=day,
            runs=[_routine_run_view(raw) for raw in runs_raw],
            upcoming=[_routine_run_view(raw) for raw in upcoming_raw],
            health="ok",
            generated_at=_now(),
        )

    async def routines_apply(self, request: RoutineActionRequest) -> RoutineActionResult:
        manager = getattr(self.container, "routine_manager", None)
        if manager is None:
            return RoutineActionResult(
                status="unavailable",
                run_id=request.run_id,
                message="Routine manager is not configured.",
            )
        method = getattr(manager, "apply_action", None)
        if method is None:
            return RoutineActionResult(
                status="unavailable",
                run_id=request.run_id,
                message=f"Routine action {request.action} is not yet exposed via the desktop API.",
            )
        try:
            await method(request.model_dump())
        except Exception as exc:
            return RoutineActionResult(
                status="unavailable",
                run_id=request.run_id,
                message=f"Failed to apply routine action: {exc}",
            )
        return RoutineActionResult(
            status="applied",
            run_id=request.run_id,
            message=f"Routine action {request.action} applied.",
        )

    # ── Vault corrections ───────────────────────────────────────────────

    async def vault_correction_preview(
        self, request: VaultCorrectionRequest
    ) -> VaultCorrectionPreview:
        store = getattr(self.container, "memory_store", None)
        if store is None:
            raise RuntimeError("Memory store not available")
        full = await store.read_note(request.memory_id)
        if full is None:
            raise ValueError(f"Memory {request.memory_id} not found")
        before = VaultMemoryItem(
            id=full.id,
            title=_memory_title(full.body, full),
            body_preview=_preview(full.body),
            memory_type=full.memory_type,
            certainty=_certainty(full.tags, full.body),  # type: ignore[arg-type]
            tags=list(full.tags),
            entities=list(full.entities),
            provenance=_memory_provenance(full),
            vault_note_path=full.source_path,
            updated_at=full.updated_at or full.created_at,
        )
        after: VaultMemoryItem | None = None
        if request.operation == "delete":
            after = None
        else:
            updates: dict[str, Any] = {}
            if request.new_text is not None:
                updates["body_preview"] = _preview(request.new_text)
                updates["title"] = _memory_title(request.new_text, full)
            if request.operation == "confirm":
                updates["certainty"] = "confirmed"
            elif request.operation == "mark_stale":
                updates["certainty"] = "stale"
            elif request.operation == "correct":
                updates["certainty"] = "correction"
            after = before.model_copy(update=updates)
        return VaultCorrectionPreview(
            memory_id=request.memory_id,
            operation=request.operation,
            before=before,
            after=after,
            summary=_correction_summary(request),
            mutates_state=False,
            generated_at=_now(),
        )

    async def vault_correction_apply(
        self, request: VaultCorrectionRequest
    ) -> VaultCorrectionResult:
        return VaultCorrectionResult(
            status="unavailable",
            memory_id=request.memory_id,
            message=(
                "Memory corrections are not yet wired through the desktop API. "
                "Use Kora chat to record corrections until the desktop write path ships."
            ),
        )

    # ── Autonomous ───────────────────────────────────────────────────────

    async def autonomous(self) -> AutonomousView:
        engine = getattr(self.container, "_orchestration_engine", None)
        if engine is None:
            return AutonomousView(
                enabled=False,
                health="unconfigured",
                message="Orchestration engine not initialized.",
                generated_at=_now(),
            )
        active: list[AutonomousPlanView] = []
        queued: list[AutonomousPlanView] = []
        completed: list[AutonomousPlanView] = []
        for view in await self._pipeline_instance_views():
            if view.status in {"queued"}:
                queued.append(view)
            elif view.status in {"completed", "failed", "cancelled"}:
                completed.append(view)
            else:
                active.append(view)
        decisions: list[AutonomousDecisionView] = []
        decision_queue = getattr(engine, "open_decisions", None) or getattr(self.container, "decision_queue", None)
        if decision_queue is not None and hasattr(decision_queue, "list_open"):
            try:
                for decision in await decision_queue.list_open():  # type: ignore[func-returns-value]
                    decisions.append(_decision_view(decision))
            except Exception:
                pass
        return AutonomousView(
            enabled=bool(self.settings.autonomous.enabled),
            active=active,
            queued=queued,
            recently_completed=completed[:5],
            open_decisions=decisions,
            health="ok",
            generated_at=_now(),
        )

    async def _reminder_timeline_items(self, day: date) -> list[TimelineItem]:
        if not self.db_path.exists() or not await _table_exists(self.db_path, "reminders"):
            return []
        start = datetime.combine(day, time.min, tzinfo=UTC)
        end = start + timedelta(days=1)
        rows = await self._reminder_rows_between(start, end)
        return [_reminder_timeline_item(row) for row in rows]

    async def _reminder_calendar_events(
        self,
        start: datetime,
        end: datetime,
    ) -> list[CalendarEventView]:
        if not self.db_path.exists() or not await _table_exists(self.db_path, "reminders"):
            return []
        rows = await self._reminder_rows_between(start, end)
        return [_reminder_calendar_event(row) for row in rows]

    async def _reminder_rows_between(
        self,
        start: datetime,
        end: datetime,
    ) -> list[aiosqlite.Row]:
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            column_rows = await (await db.execute("PRAGMA table_info(reminders)")).fetchall()
            columns = {str(row["name"]) for row in column_rows}
            date_columns = [name for name in ("due_at", "remind_at", "scheduled_at", "created_at") if name in columns]
            if not date_columns:
                return []
            starts_expr = f"COALESCE({', '.join(date_columns)})"
            select_parts = [
                "id" if "id" in columns else "'' AS id",
                "title" if "title" in columns else "'Reminder' AS title",
                "description" if "description" in columns else "NULL AS description",
                "status" if "status" in columns else "'pending' AS status",
                "created_at" if "created_at" in columns else f"{starts_expr} AS created_at",
                f"{starts_expr} AS starts_at",
                "source" if "source" in columns else "'kora' AS source",
                "metadata" if "metadata" in columns else "'{}' AS metadata",
            ]
            rows = await (
                await db.execute(
                    f"""
                    SELECT {", ".join(select_parts)}
                    FROM reminders
                    WHERE {("status != 'dismissed' AND ") if "status" in columns else ""}
                          {starts_expr} >= ?
                      AND {starts_expr} < ?
                    ORDER BY {starts_expr} ASC
                    """,
                    (start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()),
                )
            ).fetchall()
        return list(rows)

    async def _pipeline_instance_views(self) -> list[AutonomousPlanView]:
        if not self.db_path.exists() or not await _table_exists(self.db_path, "pipeline_instances"):
            return []
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT * FROM pipeline_instances
                    ORDER BY updated_at DESC, started_at DESC
                    LIMIT 40
                    """
                )
            ).fetchall()
            task_rows = await (
                await db.execute(
                    """
                    SELECT pipeline_instance_id, state, stage_name, completed_at, last_step_at
                    FROM worker_tasks
                    WHERE pipeline_instance_id IS NOT NULL
                    """
                )
            ).fetchall() if await _table_exists(self.db_path, "worker_tasks") else []

        by_pipeline: dict[str, list[aiosqlite.Row]] = {}
        for task in task_rows:
            by_pipeline.setdefault(str(task["pipeline_instance_id"]), []).append(task)
        return [_pipeline_instance_view(row, by_pipeline.get(str(row["id"]), [])) for row in rows]

    # ── Integrations ────────────────────────────────────────────────────

    async def integrations(self) -> IntegrationsView:
        integrations: list[IntegrationStatusView] = []
        tools: list[IntegrationToolView] = []
        # Workspace
        workspace = self.settings.workspace
        google_email = getattr(workspace, "user_google_email", "") or ""
        account = getattr(workspace, "account", "") or ""
        configured = bool(google_email)
        ws_health = "ok" if configured else "unconfigured"
        integrations.append(
            IntegrationStatusView(
                id="workspace",
                label=f"Workspace · {account}" if account else "Workspace",
                kind="workspace",
                enabled=configured,
                health=ws_health,
                detail=google_email or "Not connected",
                metadata={
                    "read_only": getattr(workspace, "read_only", False),
                    "default_calendar_id": getattr(workspace, "default_calendar_id", None),
                    "mcp_server_name": getattr(workspace, "mcp_server_name", None),
                },
            )
        )
        # Vault
        vault = self._vault_state()
        integrations.append(
            IntegrationStatusView(
                id="vault",
                label="Obsidian Vault",
                kind="vault",
                enabled=vault.enabled,
                health=("ok" if vault.health == "ok" else "unconfigured" if vault.health == "unconfigured" else "degraded"),
                detail=vault.message,
                metadata={"path": vault.path},
            )
        )
        # Browser
        browser = self.settings.browser
        integrations.append(
            IntegrationStatusView(
                id="browser",
                label="Browser",
                kind="browser",
                enabled=browser.enabled,
                health="ok" if browser.enabled else "unconfigured",
                detail=str(browser.binary_path) if getattr(browser, "binary_path", None) else None,
            )
        )
        # Claude Code delegation
        autonomous = self.settings.autonomous
        integrations.append(
            IntegrationStatusView(
                id="claude_code",
                label="Claude Code delegation",
                kind="claude_code",
                enabled=getattr(autonomous, "claude_code_enabled", False),
                health="ok" if getattr(autonomous, "claude_code_enabled", False) else "unconfigured",
                detail=getattr(autonomous, "claude_code_path", None),
            )
        )
        # MCP servers
        mcp_manager = getattr(self.container, "mcp_manager", None)
        if mcp_manager is not None and hasattr(mcp_manager, "list_servers"):
            try:
                for server in mcp_manager.list_servers():
                    integrations.append(
                        IntegrationStatusView(
                            id=f"mcp:{server.name}",
                            label=server.name,
                            kind="mcp",
                            enabled=getattr(server, "enabled", True),
                            health="ok" if getattr(server, "ready", False) else "degraded",
                            detail=getattr(server, "status_detail", None),
                            tools_available=len(getattr(server, "tools", []) or []),
                        )
                    )
                    for tool in getattr(server, "tools", []) or []:
                        tools.append(
                            IntegrationToolView(
                                integration_id=f"mcp:{server.name}",
                                name=getattr(tool, "name", str(tool)),
                                description=getattr(tool, "description", None),
                                status="available",
                            )
                        )
            except Exception:
                pass
        return IntegrationsView(integrations=integrations, tools=tools, generated_at=_now())

    # ── Settings validation ─────────────────────────────────────────────

    async def validate_settings(self, patch: dict[str, Any]) -> SettingsValidationView:
        issues: list[SettingsValidationIssue] = []
        allowed_themes = {
            "warm-neutral",
            "quiet-dark",
            "low-stimulation",
            "high-contrast",
            "soft-color",
            "compact-focus",
        }
        allowed_density = {"cozy", "balanced", "compact"}
        allowed_motion = {"normal", "reduced", "none"}
        allowed_views = {"day", "week", "month", "agenda"}
        if "theme_family" in patch and patch["theme_family"] not in allowed_themes:
            issues.append(
                SettingsValidationIssue(
                    path="theme_family",
                    severity="error",
                    message=f"theme_family must be one of {sorted(allowed_themes)}",
                )
            )
        if "density" in patch and patch["density"] not in allowed_density:
            issues.append(
                SettingsValidationIssue(
                    path="density",
                    severity="error",
                    message=f"density must be one of {sorted(allowed_density)}",
                )
            )
        if "motion" in patch and patch["motion"] not in allowed_motion:
            issues.append(
                SettingsValidationIssue(
                    path="motion",
                    severity="error",
                    message=f"motion must be one of {sorted(allowed_motion)}",
                )
            )
        if "calendar_default_view" in patch and patch["calendar_default_view"] not in allowed_views:
            issues.append(
                SettingsValidationIssue(
                    path="calendar_default_view",
                    severity="error",
                    message=f"calendar_default_view must be one of {sorted(allowed_views)}",
                )
            )
        if "chat_panel_width" in patch:
            try:
                width = int(patch["chat_panel_width"])
                if width < 320 or width > 520:
                    issues.append(
                        SettingsValidationIssue(
                            path="chat_panel_width",
                            severity="warning",
                            message="chat_panel_width should be between 320 and 520 px",
                        )
                    )
            except (TypeError, ValueError):
                issues.append(
                    SettingsValidationIssue(
                        path="chat_panel_width",
                        severity="error",
                        message="chat_panel_width must be an integer",
                    )
                )
        return SettingsValidationView(
            valid=not any(issue.severity == "error" for issue in issues),
            issues=issues,
            generated_at=_now(),
        )

    async def _find_calendar_event(self, event_id: str) -> CalendarEventView | None:
        if not self.db_path.exists() or not await _table_exists(self.db_path, "calendar_entries"):
            return None
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT * FROM calendar_entries WHERE id = ? LIMIT 1",
                    (event_id,),
                )
            ).fetchone()
        if row is None:
            return None
        metadata: dict[str, Any] = {}
        try:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        except Exception:
            metadata = {}
        kind = str(row["kind"] if "kind" in row.keys() else "event")
        return CalendarEventView(
            id=str(row["id"]),
            title=str(row["title"]),
            kind=kind,
            starts_at=_iso_datetime(row["starts_at"]),
            ends_at=_iso_datetime(row["ends_at"]),
            all_day=bool(row["all_day"]) if "all_day" in row.keys() else False,
            source=str(row["source"]) if "source" in row.keys() else "kora",
            status=str(row["status"]) if "status" in row.keys() else "active",
            layer_ids=_layers_for_kind(kind, metadata),
            provenance=_event_provenance(
                str(row["source"]) if "source" in row.keys() else "kora",
                metadata,
            ),
            metadata=metadata,
        )

    def _vault_state(self) -> VaultState:
        memory_root = str(Path(self.settings.memory.kora_memory_path).expanduser())
        vault_path = str(Path(self.settings.vault.path).expanduser()) if self.settings.vault.path else None
        configured = bool(vault_path)
        if configured and not Path(vault_path or "").exists():
            health = "missing"
            message = "Vault path is configured but not currently reachable."
        elif configured:
            health = "ok"
            message = "Obsidian-facing vault connection is configured."
        elif self.settings.vault.enabled:
            health = "unconfigured"
            message = "Vault integration is enabled but no Obsidian path is configured."
        else:
            health = "unconfigured"
            message = "Vault integration is disabled."
        return VaultState(
            enabled=bool(self.settings.vault.enabled),
            configured=configured,
            path=vault_path,
            memory_root=memory_root,
            health=health,
            message=message,
        )


async def _table_exists(db_path: Path, table: str) -> bool:
    async with aiosqlite.connect(str(db_path)) as db:
        row = await (
            await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
        ).fetchone()
    return row is not None


def _now() -> datetime:
    return datetime.now(UTC)


def _time_or_none(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value else None


def _timeline_from_entry(entry: Any) -> TimelineItem:
    status = str(getattr(entry, "status", "planned"))
    reality = str(getattr(entry, "reality_state", "unknown"))
    if "." in status:
        status = status.rsplit(".", 1)[-1]
    if "." in reality:
        reality = reality.rsplit(".", 1)[-1]
    risk = "repair" if reality in {"confirmed_skipped", "confirmed_blocked", "confirmed_partial", "rejected_inference"} else "none"
    item_type = str(getattr(entry, "entry_type", "task"))
    provenance = []
    for attr, label in (
        ("calendar_entry_id", "calendar"),
        ("item_id", "task"),
        ("reminder_id", "reminder"),
        ("routine_id", "routine"),
    ):
        if getattr(entry, attr, None):
            provenance.append(label)
    return TimelineItem(
        id=str(getattr(entry, "id", "")),
        title=str(getattr(entry, "title", "Untitled")),
        item_type=item_type,
        starts_at=getattr(entry, "intended_start", None),
        ends_at=getattr(entry, "intended_end", None),
        status=status,
        reality_state=reality,
        support_tags=list(getattr(entry, "support_tags", []) or []),
        provenance=provenance or ["day_plan"],
        risk=risk,
    )


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        if key in row.keys():
            value = row[key]
            return default if value is None else value
    except Exception:
        pass
    return default


def _reminder_timeline_item(row: Any) -> TimelineItem:
    status = str(_row_value(row, "status", "pending"))
    title = str(_row_value(row, "title", "Reminder"))
    starts_at = _iso_datetime(_row_value(row, "starts_at")) or _now()
    risk = "none" if status in {"pending", "delivered", "active"} else "watch"
    return TimelineItem(
        id=f"reminder:{_row_value(row, 'id', title)}",
        title=title,
        item_type="reminder",
        starts_at=starts_at,
        status=status,
        reality_state="confirmed",
        support_tags=["reminder"],
        provenance=["reminders"],
        risk=risk,
    )


def _reminder_calendar_event(row: Any) -> CalendarEventView:
    title = str(_row_value(row, "title", "Reminder"))
    starts_at = _iso_datetime(_row_value(row, "starts_at")) or _now()
    metadata: dict[str, Any] = {}
    try:
        raw_metadata = _row_value(row, "metadata", "{}")
        metadata = json.loads(raw_metadata) if raw_metadata else {}
    except Exception:
        metadata = {}
    metadata.setdefault("provenance", ["reminders"])
    kind = "reminder"
    return CalendarEventView(
        id=f"reminder:{_row_value(row, 'id', title)}",
        title=title,
        kind=kind,
        starts_at=starts_at,
        ends_at=None,
        all_day=False,
        source=str(_row_value(row, "source", "kora")),
        status=str(_row_value(row, "status", "pending")),
        layer_ids=_layers_for_kind(kind, metadata),
        provenance=_event_provenance(str(_row_value(row, "source", "kora")), metadata),
        metadata=metadata,
    )


def _hidden_memory_note(note: Any, body: str) -> bool:
    if "Consolidated memory preserving the source notes verbatim." in body:
        return True
    source_path = getattr(note, "source_path", None)
    if not source_path:
        return False
    try:
        raw = Path(source_path).read_text(encoding="utf-8")
    except Exception:
        raw = body
    if not raw.startswith("---"):
        return False
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return False
    frontmatter = parts[1].lower()
    return (
        "status: merged" in frontmatter
        or "status: soft_deleted" in frontmatter
        or "status: deleted" in frontmatter
        or "\ndeleted_at:" in frontmatter
    )


def _calendar_layers(saved: dict[str, bool]) -> list[CalendarLayerState]:
    defaults = [
        ("events", "Events", True, "#ba6b57", "Fixed calendar commitments."),
        ("reminders", "Reminders", True, "#c28b3c", "Kora reminders and due items."),
        ("buffers", "Buffers", True, "#8aa879", "Transition and decompression buffers."),
        ("routines", "Routines", True, "#6f91a8", "Routine anchors and guided sequences."),
        ("load", "Load overlay", False, "#8f7ab8", "Life load and overload markers."),
        ("repair", "Repair risk", True, "#c55f66", "At-risk or diverged plan items."),
        ("provenance", "Provenance", False, "#6f6b62", "Source and sync origin markers."),
    ]
    return [
        CalendarLayerState(
            id=layer_id,
            label=label,
            enabled=bool(saved.get(layer_id, enabled)),
            color=color,
            description=description,
        )
        for layer_id, label, enabled, color, description in defaults
    ]


def _layers_for_kind(kind: str, metadata: dict[str, Any]) -> list[str]:
    layers = ["events"]
    if kind == "reminder":
        layers.append("reminders")
    if kind == "buffer":
        layers.append("buffers")
    if kind == "routine":
        layers.append("routines")
    if metadata.get("repair_risk"):
        layers.append("repair")
    if metadata.get("load_score") is not None:
        layers.append("load")
    if metadata.get("provenance") or metadata.get("source"):
        layers.append("provenance")
    return layers


def _event_provenance(source: str, metadata: dict[str, Any]) -> list[str]:
    values = [source]
    raw = metadata.get("provenance")
    if isinstance(raw, list):
        values.extend(str(item) for item in raw)
    elif isinstance(raw, str):
        values.append(raw)
    return list(dict.fromkeys(value for value in values if value))


def _actions_from_evaluation(evaluation: Any, request: RepairPreviewRequest) -> list[RepairActionPreview]:
    divergences = list(getattr(evaluation, "divergences", []) if evaluation else [])
    actions: list[RepairActionPreview] = []
    seen: set[tuple[str, str, str]] = set()
    for index, divergence in enumerate(divergences):
        if request.selected_entry_ids and divergence.day_plan_entry_id not in request.selected_entry_ids:
            continue
        action_type = _action_type_for_divergence(divergence.divergence_type, request.change_type)
        key = (
            str(divergence.day_plan_entry_id or divergence.calendar_entry_id or divergence.item_id or ""),
            action_type,
            _repair_title_key(str(divergence.title)),
        )
        if key in seen:
            continue
        seen.add(key)
        actions.append(
            RepairActionPreview(
                id=f"preview-{len(actions)}",
                action_type=action_type,
                title=f"Repair: {divergence.title}",
                reason=divergence.reason,
                severity=divergence.severity,
                target_day_plan_entry_id=divergence.day_plan_entry_id,
                target_calendar_entry_id=divergence.calendar_entry_id,
                target_item_id=divergence.item_id,
                before=divergence.title,
                after=_after_label(request.change_type),
            )
        )
        if len(actions) >= 24:
            break
    return actions


def _repair_title_key(title: str) -> str:
    normalized = title.strip().lower()
    while normalized.startswith("repair:"):
        normalized = normalized.removeprefix("repair:").strip()
    return " ".join(normalized.split())


def _generic_repair_action(request: RepairPreviewRequest) -> RepairActionPreview:
    return RepairActionPreview(
        id="preview-0",
        action_type=request.change_type,
        title="Make today smaller",
        reason=request.note or "User requested a lower-load repair preview.",
        before="Current day plan",
        after="Protected essentials, flexible work moved later, tomorrow bridge prepared",
    )


def _action_type_for_divergence(divergence_type: str, change_type: str) -> str:
    if change_type and change_type != "make_smaller":
        return change_type
    if "skipped" in divergence_type or "blocked" in divergence_type:
        return "move_to_tomorrow"
    if "stale" in divergence_type:
        return "confirm_or_drop"
    return "defer_nonessential"


def _after_label(change_type: str) -> str:
    labels = {
        "make_smaller": "Smaller version kept; nonessential work deferred",
        "move_to_tomorrow": "Moved into tomorrow bridge",
        "add_buffer": "Transition buffer added",
    }
    return labels.get(change_type, "Previewed repair change")


def _preview_indexes(ids: list[str]) -> set[int]:
    indexes: set[int] = set()
    for value in ids:
        if value.startswith("preview-"):
            try:
                indexes.add(int(value.split("-", 1)[1]))
            except ValueError:
                pass
    return indexes


def _memory_title(body: str, note: Any) -> str:
    first = next((line.strip("# ").strip() for line in body.splitlines() if line.strip()), "")
    return first[:80] or f"{note.memory_type} memory"


def _preview(body: str) -> str:
    text = " ".join(line.strip() for line in body.splitlines() if line.strip())
    return text[:260]


def _certainty(tags: list[str], body: str) -> str:
    haystack = " ".join([*tags, body[:500]]).lower()
    if "correction" in haystack or "corrected" in haystack:
        return "correction"
    if "stale" in haystack or "outdated" in haystack:
        return "stale"
    if "guess" in haystack or "inferred" in haystack or "uncertain" in haystack:
        return "guess"
    if "confirmed" in haystack or "user_confirmed" in haystack:
        return "confirmed"
    return "unknown"


def _memory_provenance(note: Any) -> list[str]:
    provenance = ["kora_memory_root"]
    if note.source_path:
        provenance.append(note.source_path)
    if note.memory_type == "user_model":
        provenance.append("user_model")
    return provenance


def _iso_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _new_calendar_event(request: CalendarEditRequest) -> CalendarEventView:
    return CalendarEventView(
        id=request.event_id or "preview-new",
        title=request.title or "New event",
        kind="event",
        starts_at=request.starts_at or _now(),
        ends_at=request.ends_at,
        all_day=False,
        source="kora",
        status="planned",
        layer_ids=["events"],
        provenance=["kora"],
        metadata={"preview": True},
    )


def _calendar_preview_summary(
    request: CalendarEditRequest,
    before: CalendarEventView | None,
    after: CalendarEventView | None,
) -> str:
    if request.operation == "cancel":
        return f"Cancel '{before.title}' (preview only — apply to confirm)." if before else "Cancel event."
    if request.operation == "create":
        return f"Create '{after.title if after else 'event'}' (preview only — apply to confirm)."
    if request.operation == "move":
        when = after.starts_at.isoformat() if after and after.starts_at else "new time"
        return f"Move '{(before or after).title}' to {when} (preview only — apply to confirm)."
    if request.operation == "resize":
        return f"Resize '{(before or after).title}' (preview only — apply to confirm)."
    return "Preview only — apply to confirm."


def _dose_view(raw: Any) -> MedicationDose:
    return MedicationDose(
        id=str(getattr(raw, "id", "")),
        medication_id=str(getattr(raw, "medication_id", "")),
        name=str(getattr(raw, "name", "Medication")),
        dose_label=str(getattr(raw, "dose_label", "—")),
        scheduled_at=getattr(raw, "scheduled_at", None),
        window_start=getattr(raw, "window_start", None),
        window_end=getattr(raw, "window_end", None),
        status=str(getattr(raw, "status", "pending")),  # type: ignore[arg-type]
        pair_with=list(getattr(raw, "pair_with", []) or []),
        notes=getattr(raw, "notes", None),
    )


def _medication_preview_summary(request: MedicationLogRequest) -> str:
    note = f" ({request.note})" if request.note else ""
    return f"Mark dose as {request.status}{note}. Preview only — apply to confirm."


def _routine_run_view(raw: Any) -> RoutineRunView:
    steps_raw = list(getattr(raw, "steps", []) or [])
    steps = [
        RoutineStepView(
            index=int(getattr(step, "index", index)),
            title=str(getattr(step, "title", "Step")),
            description=str(getattr(step, "description", "") or ""),
            estimated_minutes=int(getattr(step, "estimated_minutes", 5) or 5),
            energy_required=str(getattr(step, "energy_required", "medium")),  # type: ignore[arg-type]
            cue=str(getattr(step, "cue", "") or ""),
            completed=bool(getattr(step, "completed", False)),
        )
        for index, step in enumerate(steps_raw)
    ]
    next_index: int | None = next(
        (step.index for step in steps if not step.completed),
        None,
    )
    return RoutineRunView(
        id=str(getattr(raw, "id", "")),
        routine_id=str(getattr(raw, "routine_id", "")),
        name=str(getattr(raw, "name", "Routine")),
        description=str(getattr(raw, "description", "") or ""),
        variant=str(getattr(raw, "variant", "standard")),  # type: ignore[arg-type]
        status=str(getattr(raw, "status", "pending")),  # type: ignore[arg-type]
        started_at=getattr(raw, "started_at", None),
        estimated_total_minutes=int(getattr(raw, "estimated_total_minutes", 0) or 0),
        steps=steps,
        next_step_index=next_index,
    )


def _correction_summary(request: VaultCorrectionRequest) -> str:
    if request.operation == "delete":
        return "Delete this memory entry (preview only — apply to confirm)."
    if request.operation == "merge":
        target = request.merge_target_id or "another entry"
        return f"Merge into {target} (preview only — apply to confirm)."
    if request.operation == "confirm":
        return "Mark as user-confirmed (preview only — apply to confirm)."
    if request.operation == "mark_stale":
        return "Mark as stale (preview only — apply to confirm)."
    return "Update memory entry (preview only — apply to confirm)."


def _pipeline_view(pipeline: Any) -> AutonomousPlanView:
    completed = int(getattr(pipeline, "completed_steps", 0) or 0)
    total = int(getattr(pipeline, "total_steps", 0) or 0)
    progress = (completed / total) if total else 0.0
    checkpoints_raw = list(getattr(pipeline, "checkpoints", []) or [])
    checkpoints = [
        AutonomousCheckpointView(
            id=str(getattr(cp, "id", index)),
            label=str(getattr(cp, "label", f"Checkpoint {index + 1}")),
            status=str(getattr(cp, "status", "pending")),  # type: ignore[arg-type]
            occurred_at=getattr(cp, "occurred_at", None),
            summary=getattr(cp, "summary", None),
        )
        for index, cp in enumerate(checkpoints_raw)
    ]
    return AutonomousPlanView(
        id=str(getattr(pipeline, "id", "")),
        pipeline_id=str(getattr(pipeline, "pipeline_id", getattr(pipeline, "id", ""))),
        title=str(getattr(pipeline, "title", "Autonomous plan")),
        goal=str(getattr(pipeline, "goal", "") or ""),
        status=str(getattr(pipeline, "status", "running")),  # type: ignore[arg-type]
        started_at=getattr(pipeline, "started_at", None),
        progress=progress,
        completed_steps=completed,
        total_steps=total,
        current_step=getattr(pipeline, "current_step", None),
        checkpoints=checkpoints,
        open_decisions=[],
        last_activity_at=getattr(pipeline, "last_activity_at", None),
    )


def _pipeline_instance_view(row: Any, tasks: list[Any]) -> AutonomousPlanView:
    raw_status = str(_row_value(row, "status", "running")).lower()
    status_map = {
        "pending": "queued",
        "queued": "queued",
        "running": "running",
        "active": "running",
        "paused": "paused",
        "waiting": "paused",
        "completed": "completed",
        "succeeded": "completed",
        "failed": "failed",
        "error": "failed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
    }
    status = status_map.get(raw_status, "running")
    total_steps = max(len(tasks), int(_row_value(row, "total_steps", 0) or 0))
    completed_steps = sum(
        1
        for task in tasks
        if str(_row_value(task, "state", "")).lower() in {"completed", "succeeded"}
    )
    if not total_steps and status in {"completed", "failed", "cancelled"}:
        total_steps = 1
        completed_steps = 1 if status == "completed" else 0
    current_step = next(
        (
            str(_row_value(task, "stage_name", ""))
            for task in tasks
            if str(_row_value(task, "state", "")).lower()
            not in {"completed", "succeeded", "failed", "cancelled", "canceled"}
            and _row_value(task, "stage_name")
        ),
        None,
    )
    pipeline_name = str(_row_value(row, "pipeline_name", "autonomous_plan"))
    title = " ".join(part for part in pipeline_name.replace("_", " ").split() if part).title()
    last_activity_candidates = [
        _iso_datetime(_row_value(row, key))
        for key in ("updated_at", "completed_at", "started_at", "created_at")
    ]
    for task in tasks:
        last_activity_candidates.extend(
            _iso_datetime(_row_value(task, key))
            for key in ("last_step_at", "completed_at")
        )
    last_activity = max((value for value in last_activity_candidates if value is not None), default=None)
    progress = (completed_steps / total_steps) if total_steps else 0.0
    return AutonomousPlanView(
        id=str(_row_value(row, "id", "")),
        pipeline_id=str(_row_value(row, "pipeline_name", _row_value(row, "id", ""))),
        title=title or "Autonomous plan",
        goal=str(_row_value(row, "goal", "")),
        status=status,  # type: ignore[arg-type]
        started_at=_iso_datetime(_row_value(row, "started_at", _row_value(row, "created_at"))),
        progress=progress,
        completed_steps=completed_steps,
        total_steps=total_steps,
        current_step=current_step,
        checkpoints=[],
        open_decisions=[],
        last_activity_at=last_activity,
    )


def _decision_view(decision: Any) -> AutonomousDecisionView:
    return AutonomousDecisionView(
        id=str(getattr(decision, "id", "")),
        prompt=str(getattr(decision, "prompt", "Decision required")),
        options=list(getattr(decision, "options", []) or []),
        deadline_at=getattr(decision, "deadline_at", None),
        pipeline_id=getattr(decision, "pipeline_id", None),
    )
