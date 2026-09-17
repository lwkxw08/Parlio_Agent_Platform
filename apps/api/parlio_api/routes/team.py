"""Phase 22 endpoints: bookable resources (engineers), team assignment settings, scheduling-tool
configuration + inbound events, schedule read model, and owner booking actions.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from parlio_api.auth import UserDep
from parlio_api.calendar import Booking
from parlio_api.deps import CalendarDep, ScheduleDep, SchedulingDep, StoreDep
from parlio_api.resources import (
    Resource,
    ResourceInput,
    ResourceService,
    TeamSettings,
    TeamSettingsInput,
)
from parlio_api.schedule import ScheduleView
from parlio_api.scheduling import ExternalResource, SchedulerInput

router = APIRouter(prefix="/v1/team", tags=["team"])
public = APIRouter(prefix="/v1/public/scheduler", tags=["public"])


def _resources(cal: CalendarDep) -> ResourceService:
    return cal.resources


# -- resources ---------------------------------------------------------------------------------


@router.get("/resources", response_model=list[Resource])
async def list_resources(
    cal: CalendarDep, user: UserDep, tenant_id: str, include_inactive: bool = True
) -> list[Resource]:
    user.require_tenant(tenant_id)
    return await _resources(cal).all(tenant_id, include_inactive=include_inactive)


@router.post("/resources", response_model=Resource, status_code=status.HTTP_201_CREATED)
async def create_resource(
    cal: CalendarDep, user: UserDep, tenant_id: str, body: ResourceInput
) -> Resource:
    user.require_tenant(tenant_id)
    return await _resources(cal).put(Resource(tenant_id=tenant_id, **body.model_dump()))


@router.put("/resources/{rid}", response_model=Resource)
async def update_resource(
    rid: str, cal: CalendarDep, user: UserDep, tenant_id: str, body: ResourceInput
) -> Resource:
    user.require_tenant(tenant_id)
    svc = _resources(cal)
    cur = await svc.get(tenant_id, rid)
    if cur is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "resource not found")
    return await svc.put(cur.model_copy(update=body.model_dump()))


@router.delete("/resources/{rid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resource(rid: str, cal: CalendarDep, user: UserDep, tenant_id: str) -> None:
    user.require_tenant(tenant_id)
    if not await _resources(cal).delete(tenant_id, rid):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "resource not found")


@router.get("/settings", response_model=TeamSettings)
async def get_settings(cal: CalendarDep, user: UserDep, tenant_id: str) -> TeamSettings:
    user.require_tenant(tenant_id)
    return await _resources(cal).settings(tenant_id)


@router.put("/settings", response_model=TeamSettings)
async def put_settings(
    cal: CalendarDep, user: UserDep, tenant_id: str, body: TeamSettingsInput
) -> TeamSettings:
    user.require_tenant(tenant_id)
    svc = _resources(cal)
    cur = await svc.settings(tenant_id)
    return await svc.put_settings(cur.model_copy(update=body.model_dump()))


# -- scheduling tool ---------------------------------------------------------------------------


@router.get("/scheduler")
async def get_scheduler(
    sched: SchedulingDep, user: UserDep, tenant_id: str
) -> dict[str, Any] | None:
    user.require_tenant(tenant_id)
    cfg = await sched.config(tenant_id)
    return cfg.public() if cfg else None


@router.put("/scheduler")
async def put_scheduler(
    sched: SchedulingDep, user: UserDep, tenant_id: str, body: SchedulerInput
) -> dict[str, Any]:
    user.require_tenant(tenant_id)
    return (await sched.put(tenant_id, body)).public()


@router.delete("/scheduler", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scheduler(sched: SchedulingDep, user: UserDep, tenant_id: str) -> None:
    user.require_tenant(tenant_id)
    await sched.delete(tenant_id)


class SchedulerTestResult(BaseModel):
    ok: bool
    staff: list[ExternalResource]
    imported: int = 0
    error: str | None = None


@router.post("/scheduler/test", response_model=SchedulerTestResult)
async def test_scheduler(
    sched: SchedulingDep,
    cal: CalendarDep,
    user: UserDep,
    tenant_id: str,
    import_staff: bool = False,
) -> SchedulerTestResult:
    user.require_tenant(tenant_id)
    cfg = await sched.config(tenant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no scheduling tool configured")
    try:
        staff = await sched.test(cfg)
    except Exception as e:
        return SchedulerTestResult(ok=False, staff=[], error=str(e)[:300])
    imported = 0
    if import_staff:
        imported = len(
            await _resources(cal).sync_external(
                tenant_id, [s.model_dump(mode="json") for s in staff]
            )
        )
    return SchedulerTestResult(ok=True, staff=staff, imported=imported)


class SchedulerEvent(BaseModel):
    """Inbound event from a scheduling tool (generic webhook contract)."""

    event: str  # job.updated | job.cancelled | job.rescheduled
    booking_id: str | None = None
    ref: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    resource_id: str | None = None
    resource_name: str | None = None


@public.post("/events/{tenant_id}", status_code=status.HTTP_202_ACCEPTED)
async def scheduler_event(
    tenant_id: str, request: Request, sched: SchedulingDep, cal: CalendarDep, store: StoreDep
) -> dict[str, Any]:
    cfg = await sched.config(tenant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no scheduling tool configured")
    body = await request.body()
    if not sched.verify_event(cfg, dict(request.headers), body):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad signature")
    ev = SchedulerEvent.model_validate_json(body)
    booking = await _find_booking(cal, tenant_id, ev)
    if booking is None:
        return {"ok": True, "matched": False}
    if ev.event == "job.cancelled":
        booking.status = "cancelled"
    elif ev.event in ("job.rescheduled", "job.updated"):
        if ev.start and ev.end:
            booking.start, booking.end = ev.start, ev.end
        if ev.resource_id is not None:
            booking.resource_id = ev.resource_id
        if ev.resource_name is not None:
            booking.resource_name = ev.resource_name
    booking.updated_at = datetime.now(tz=booking.start.tzinfo)
    await store.put_doc(booking.to_doc())
    return {"ok": True, "matched": True, "booking_id": booking.id}


async def _find_booking(cal: CalendarDep, tenant_id: str, ev: SchedulerEvent) -> Booking | None:
    if ev.booking_id:
        return await cal.booking(tenant_id, ev.booking_id)
    if ev.ref:
        for b in await cal.bookings(tenant_id, limit=500):
            if b.provider_ref == ev.ref:
                return b
    return None


# -- schedule view + booking actions -----------------------------------------------------------


@router.get("/schedule", response_model=ScheduleView)
async def schedule(
    view: ScheduleDep,
    user: UserDep,
    tenant_id: str,
    start: date,
    days: int = 1,
    site_id: str | None = None,
    service_id: str | None = None,
    resource_id: str | None = None,
    refresh: bool = False,
) -> ScheduleView:
    user.require_tenant(tenant_id)
    return await view.view(
        tenant_id,
        start=start,
        days=days,
        site_id=site_id,
        service_id=service_id,
        resource_id=resource_id,
        refresh=refresh,
    )


class ReassignBody(BaseModel):
    resource_id: str


class RescheduleBody(BaseModel):
    start: datetime


def _mutation_error(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    if isinstance(e, PermissionError):
        return HTTPException(status.HTTP_423_LOCKED, str(e))
    return HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.get("/bookings/{booking_id}", response_model=Booking)
async def get_booking(booking_id: str, cal: CalendarDep, user: UserDep, tenant_id: str) -> Booking:
    user.require_tenant(tenant_id)
    b = await cal.booking(tenant_id, booking_id)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "booking not found")
    return b


@router.post("/bookings/{booking_id}/reassign", response_model=Booking)
async def reassign(
    booking_id: str,
    cal: CalendarDep,
    view: ScheduleDep,
    user: UserDep,
    tenant_id: str,
    body: ReassignBody,
) -> Booking:
    user.require_tenant(tenant_id)
    try:
        b = await cal.reassign(tenant_id, booking_id, body.resource_id)
    except (LookupError, PermissionError, ValueError) as e:
        raise _mutation_error(e) from e
    view.invalidate(tenant_id)
    return b


@router.post("/bookings/{booking_id}/reschedule", response_model=Booking)
async def reschedule(
    booking_id: str,
    cal: CalendarDep,
    view: ScheduleDep,
    user: UserDep,
    tenant_id: str,
    body: RescheduleBody,
) -> Booking:
    user.require_tenant(tenant_id)
    try:
        b = await cal.reschedule(tenant_id, booking_id, body.start)
    except (LookupError, PermissionError, ValueError) as e:
        raise _mutation_error(e) from e
    view.invalidate(tenant_id)
    return b


@router.post("/bookings/{booking_id}/cancel", response_model=Booking)
async def cancel(
    booking_id: str, cal: CalendarDep, view: ScheduleDep, user: UserDep, tenant_id: str
) -> Booking:
    user.require_tenant(tenant_id)
    try:
        b = await cal.cancel(tenant_id, booking_id)
    except (LookupError, PermissionError, ValueError) as e:
        raise _mutation_error(e) from e
    view.invalidate(tenant_id)
    return b
