"""Authenticated desktop view-model routes for the Electron app."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from kora_v2.daemon.server import verify_token
from kora_v2.desktop.models import (
    CalendarEditRequest,
    MedicationLogRequest,
    RepairApplyRequest,
    RepairPreviewRequest,
    RoutineActionRequest,
    VaultCorrectionRequest,
)
from kora_v2.desktop.service import DesktopViewService


def build_desktop_router(container_getter: Any) -> APIRouter:
    """Create the /desktop router.

    ``container_getter`` is a callable so the router can share the daemon
    module's live container without importing mutable globals at definition
    time.
    """

    router = APIRouter(
        prefix="/desktop",
        dependencies=[Depends(verify_token)],
        tags=["desktop"],
    )

    def service() -> DesktopViewService:
        container = container_getter()
        if container is None:
            raise HTTPException(status_code=503, detail="Container not initialized")
        return DesktopViewService(container)

    @router.get("/status")
    async def desktop_status() -> dict[str, Any]:
        return (await service().status()).model_dump(mode="json")

    @router.get("/today")
    async def desktop_today(date: date) -> dict[str, Any]:
        return (await service().today(date)).model_dump(mode="json")

    @router.get("/calendar")
    async def desktop_calendar(
        start: datetime,
        end: datetime,
        view: str | None = None,
    ) -> dict[str, Any]:
        if end <= start:
            raise HTTPException(status_code=400, detail="end must be after start")
        result = await service().calendar(start, end)
        if view in {"day", "week", "month", "agenda"}:
            result = result.model_copy(update={"default_view": view})
        return result.model_dump(mode="json")

    @router.post("/calendar/preview")
    async def desktop_calendar_preview(body: CalendarEditRequest) -> dict[str, Any]:
        return (await service().calendar_preview(body)).model_dump(mode="json")

    @router.post("/calendar/apply")
    async def desktop_calendar_apply(body: CalendarEditRequest) -> dict[str, Any]:
        return (await service().calendar_apply(body)).model_dump(mode="json")

    @router.get("/medication")
    async def desktop_medication(date: date) -> dict[str, Any]:
        return (await service().medication(date)).model_dump(mode="json")

    @router.post("/medication/preview")
    async def desktop_medication_preview(body: MedicationLogRequest) -> dict[str, Any]:
        return (await service().medication_preview(body)).model_dump(mode="json")

    @router.post("/medication/apply")
    async def desktop_medication_apply(body: MedicationLogRequest) -> dict[str, Any]:
        return (await service().medication_apply(body)).model_dump(mode="json")

    @router.get("/routines")
    async def desktop_routines(date: date) -> dict[str, Any]:
        return (await service().routines(date)).model_dump(mode="json")

    @router.post("/routines/apply")
    async def desktop_routines_apply(body: RoutineActionRequest) -> dict[str, Any]:
        return (await service().routines_apply(body)).model_dump(mode="json")

    @router.post("/vault/correction/preview")
    async def desktop_vault_correction_preview(
        body: VaultCorrectionRequest,
    ) -> dict[str, Any]:
        try:
            return (await service().vault_correction_preview(body)).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/vault/correction/apply")
    async def desktop_vault_correction_apply(
        body: VaultCorrectionRequest,
    ) -> dict[str, Any]:
        return (await service().vault_correction_apply(body)).model_dump(mode="json")

    @router.get("/autonomous")
    async def desktop_autonomous() -> dict[str, Any]:
        return (await service().autonomous()).model_dump(mode="json")

    @router.get("/integrations")
    async def desktop_integrations() -> dict[str, Any]:
        return (await service().integrations()).model_dump(mode="json")

    @router.post("/settings/validate")
    async def desktop_settings_validate(body: dict[str, Any]) -> dict[str, Any]:
        return (await service().validate_settings(body)).model_dump(mode="json")

    @router.get("/repair/state")
    async def desktop_repair_state(date: date) -> dict[str, Any]:
        return (await service().repair_state(date)).model_dump(mode="json")

    @router.post("/repair/preview")
    async def desktop_repair_preview(body: RepairPreviewRequest) -> dict[str, Any]:
        return (await service().repair_preview(body)).model_dump(mode="json")

    @router.post("/repair/apply")
    async def desktop_repair_apply(body: RepairApplyRequest) -> dict[str, Any]:
        return (await service().repair_apply(body)).model_dump(mode="json")

    @router.get("/vault/search")
    async def desktop_vault_search(q: str = "") -> dict[str, Any]:
        return (await service().vault_search(q)).model_dump(mode="json")

    @router.get("/vault/context")
    async def desktop_vault_context() -> dict[str, Any]:
        return (await service().vault_context()).model_dump(mode="json")

    @router.get("/settings")
    async def desktop_settings() -> dict[str, Any]:
        return (await service().get_settings()).model_dump(mode="json")

    @router.patch("/settings")
    async def desktop_settings_patch(body: dict[str, Any]) -> dict[str, Any]:
        return (await service().patch_settings(body)).model_dump(mode="json")

    return router
