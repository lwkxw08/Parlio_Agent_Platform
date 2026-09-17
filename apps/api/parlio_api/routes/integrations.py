"""Phase 5/5b endpoints: SMS log, notification rules, calendar/booking, BYO SIP trunks.

Dashboard routes take `tenant_id` explicitly and check membership via the principal (same
convention as `routes/dashboard.py`). Worker routes are keyed by X-Worker-Key and resolve the
tenant from the call / assistant they act on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from parlio_api.auth import UserDep
from parlio_api.calendar import (
    AvailabilityResult,
    Booking,
    BookingRequest,
    BookingRules,
    CalendarConnection,
    CalendarProvider,
    ConnectionStatus,
    SyncLogEntry,
)
from parlio_api.deps import (
    CalendarDep,
    HubDep,
    NotificationsDep,
    SettingsDep,
    SipDep,
    SmsDep,
    StoreDep,
    require_feature,
    require_worker_key,
)
from parlio_api.messaging import Message, SendSmsRequest
from parlio_api.notifications import (
    Notification,
    NotificationEvent,
    NotificationRule,
    NotifyEvent,
)
from parlio_api.sip import (
    PROVIDER_GUIDES,
    AdmitResult,
    IssuedCredentials,
    ProviderGuide,
    TestCallResult,
    TrunkInput,
)
from parlio_api.store import CallStore
from parlio_voice.models import Schedule

router = APIRouter(prefix="/v1", tags=["integrations"])
public = APIRouter(prefix="/v1/public", tags=["public"])
worker = APIRouter(prefix="/v1/worker", tags=["worker"], dependencies=[Depends(require_worker_key)])


async def _company(store: CallStore, tenant_id: str) -> str:
    cfgs = await store.list_assistants(tenant_id)
    return cfgs[0].company_id if cfgs else f"{tenant_id}-main"


# -- SMS -------------------------------------------------------------------------------------


@router.get("/messages", response_model=list[Message])
async def list_messages(
    user: UserDep, sms: SmsDep, tenant_id: str, call_id: str | None = None, limit: int = 100
) -> list[Message]:
    user.require_tenant(tenant_id)
    if call_id:
        return await sms.for_call(tenant_id, call_id)
    return await sms.recent(tenant_id, limit)


class DashboardSms(SendSmsRequest):
    assistant_id: str


@router.post("/messages", response_model=Message, status_code=status.HTTP_201_CREATED)
async def send_message(
    user: UserDep, sms: SmsDep, store: StoreDep, tenant_id: str, req: DashboardSms
) -> Message:
    user.require_tenant(tenant_id)
    cfg = await store.get_assistant(req.assistant_id)
    if cfg is None or cfg.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    msg = await sms.handle_request(cfg, req)
    if msg is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "scenario disabled or already sent")
    return msg


class WorkerSms(SendSmsRequest):
    assistant_id: str


@worker.post("/sms", response_model=Message | None)
async def worker_sms(req: WorkerSms, sms: SmsDep, store: StoreDep) -> Message | None:
    cfg = await store.get_assistant(req.assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    return await sms.handle_request(cfg, req)


# -- notifications ---------------------------------------------------------------------------


class RuleInput(BaseModel):
    name: str = "Team alerts"
    channel: str
    target: str = Field(min_length=3)
    events: list[NotifyEvent] = Field(default_factory=lambda: [NotifyEvent.TICKET_URGENT])
    enabled: bool = True
    qualified_only: bool = False
    departments: list[str] = Field(default_factory=list)


@router.get("/notifications/rules", response_model=list[NotificationRule])
async def list_rules(
    user: UserDep, svc: NotificationsDep, tenant_id: str
) -> list[NotificationRule]:
    user.require_tenant(tenant_id)
    return await svc.rules(tenant_id)


@router.post(
    "/notifications/rules", response_model=NotificationRule, status_code=status.HTTP_201_CREATED
)
async def create_rule(
    user: UserDep, svc: NotificationsDep, store: StoreDep, tenant_id: str, body: RuleInput
) -> NotificationRule:
    user.require_admin(tenant_id)
    rule = NotificationRule(
        tenant_id=tenant_id, company_id=await _company(store, tenant_id), **body.model_dump()
    )
    return await svc.put_rule(rule)


@router.put("/notifications/rules/{rule_id}", response_model=NotificationRule)
async def update_rule(
    user: UserDep, svc: NotificationsDep, tenant_id: str, rule_id: str, body: RuleInput
) -> NotificationRule:
    user.require_admin(tenant_id)
    prev = await svc.get_rule(tenant_id, rule_id)
    if prev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    return await svc.put_rule(prev.model_copy(update=body.model_dump()))


@router.delete("/notifications/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(user: UserDep, svc: NotificationsDep, tenant_id: str, rule_id: str) -> None:
    user.require_admin(tenant_id)
    if not await svc.delete_rule(tenant_id, rule_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")


@router.post("/notifications/rules/{rule_id}/test", response_model=Notification)
async def test_rule(
    user: UserDep, svc: NotificationsDep, tenant_id: str, rule_id: str
) -> Notification:
    user.require_admin(tenant_id)
    rule = await svc.get_rule(tenant_id, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    return await svc.test_rule(rule)


@router.get("/notifications/log", response_model=list[Notification])
async def notification_log(
    user: UserDep, svc: NotificationsDep, tenant_id: str, limit: int = 100
) -> list[Notification]:
    user.require_tenant(tenant_id)
    return await svc.log(tenant_id, limit)


@worker.post("/notify", status_code=status.HTTP_202_ACCEPTED)
async def worker_notify(ev: NotificationEvent, hub: HubDep) -> dict[str, bool]:
    await hub.notify(ev)
    return {"queued": True}


# -- calendar / booking ----------------------------------------------------------------------


class ConnectionInput(BaseModel):
    provider: CalendarProvider
    name: str = "Bookings"
    booking_url: str | None = None
    booking_vendor: str | None = None
    calendar_id: str = "primary"
    slot_minutes: int = Field(default=30, ge=5, le=480)
    buffer_minutes: int = Field(default=0, ge=0, le=120)


@router.get("/calendar/connections")
async def list_connections(user: UserDep, cal: CalendarDep, tenant_id: str) -> list[dict[str, Any]]:
    user.require_tenant(tenant_id)
    return [c.public() for c in await cal.connections(tenant_id)]


@router.post(
    "/calendar/connections",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature("calendar_booking"))],
)
async def create_connection(
    user: UserDep, cal: CalendarDep, store: StoreDep, tenant_id: str, body: ConnectionInput
) -> dict[str, Any]:
    user.require_admin(tenant_id)
    if body.provider in (CalendarProvider.GOOGLE, CalendarProvider.MICROSOFT):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "use /calendar/oauth/start for Google/Microsoft"
        )
    if body.provider == CalendarProvider.BOOKING_LINK and not body.booking_url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "booking_url required")
    conn = CalendarConnection(
        tenant_id=tenant_id,
        company_id=await _company(store, tenant_id),
        status=ConnectionStatus.CONNECTED,
        **body.model_dump(),
    )
    return (await cal.put(conn)).public()


class BookingRulesInput(BaseModel):
    """Editable booking rules for one connection (see BookingRules for field meanings)."""

    slot_minutes: int = Field(ge=5, le=480, description="Standard appointment length")
    buffer_minutes: int = Field(ge=0, le=120, description="Gap either side, e.g. travel")
    hours: Schedule | None = Field(
        default=None, description="Own hours when not using business hours"
    )
    rules: BookingRules


@router.put("/calendar/connections/{conn_id}/rules")
async def update_booking_rules(
    user: UserDep, cal: CalendarDep, tenant_id: str, conn_id: str, body: BookingRulesInput
) -> dict[str, Any]:
    user.require_admin(tenant_id)
    conn = await cal.get(tenant_id, conn_id)
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connection not found")
    names = [s.name.strip().lower() for s in body.rules.services]
    if len(set(names)) != len(names):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "service names must be unique")
    conn.slot_minutes = body.slot_minutes
    conn.buffer_minutes = body.buffer_minutes
    if body.hours is not None:
        conn.hours = body.hours
    conn.rules = body.rules
    return (await cal.put(conn)).public()


@router.delete("/calendar/connections/{conn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(user: UserDep, cal: CalendarDep, tenant_id: str, conn_id: str) -> None:
    user.require_admin(tenant_id)
    if not await cal.delete(tenant_id, conn_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connection not found")


@router.get("/calendar/oauth/start")
async def oauth_start(
    user: UserDep,
    cal: CalendarDep,
    store: StoreDep,
    settings: SettingsDep,
    tenant_id: str,
    provider: CalendarProvider,
) -> dict[str, str]:
    user.require_admin(tenant_id)
    redirect = f"{settings.public_api_url.rstrip('/')}/v1/public/calendar/oauth/callback"
    try:
        url = await cal.oauth_start(tenant_id, await _company(store, tenant_id), provider, redirect)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"url": url}


@public.get("/calendar/oauth/callback")
async def oauth_callback(
    cal: CalendarDep,
    settings: SettingsDep,
    state: str,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    dest = f"{settings.dashboard_url.rstrip('/')}/integrations"
    if error or not code:
        return RedirectResponse(f"{dest}?calendar=error&reason={error or 'no_code'}")
    try:
        await cal.oauth_callback(state, code)
    except ValueError as e:
        return RedirectResponse(f"{dest}?calendar=error&reason={e}")
    return RedirectResponse(f"{dest}?calendar=connected")


@router.get("/calendar/availability", response_model=AvailabilityResult)
async def availability(
    user: UserDep,
    cal: CalendarDep,
    tenant_id: str,
    connection_id: str | None = None,
    start: datetime | None = None,
    days: int = Query(default=7, ge=1, le=60),
    duration_minutes: int | None = None,
    service_id: str | None = None,
) -> AvailabilityResult:
    user.require_tenant(tenant_id)
    return await cal.availability(
        tenant_id,
        connection_id=connection_id,
        start=start,
        days=days,
        duration_minutes=duration_minutes,
        service_id=service_id,
    )


@router.post(
    "/calendar/bookings",
    response_model=Booking,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature("calendar_booking"))],
)
async def create_booking(
    user: UserDep, cal: CalendarDep, tenant_id: str, req: BookingRequest
) -> Booking:
    user.require_tenant(tenant_id)
    try:
        return await cal.book(tenant_id, req)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e


@router.get("/calendar/bookings", response_model=list[Booking])
async def list_bookings(
    user: UserDep, cal: CalendarDep, tenant_id: str, limit: int = 100
) -> list[Booking]:
    user.require_tenant(tenant_id)
    return await cal.bookings(tenant_id, limit)


@router.get("/calendar/sync-log", response_model=list[SyncLogEntry])
async def sync_log(
    user: UserDep, cal: CalendarDep, tenant_id: str, limit: int = 100
) -> list[SyncLogEntry]:
    user.require_tenant(tenant_id)
    return await cal.sync_log(tenant_id, limit)


@worker.get("/calendar/availability", response_model=AvailabilityResult)
async def worker_availability(
    cal: CalendarDep,
    tenant_id: str,
    days: int = Query(default=7, ge=1, le=30),
    duration_minutes: int | None = None,
    service_id: str | None = None,
    connection_id: str | None = None,
) -> AvailabilityResult:
    return await cal.availability(
        tenant_id,
        connection_id=connection_id,
        days=days,
        duration_minutes=duration_minutes,
        service_id=service_id,
    )


@worker.post("/calendar/bookings", response_model=Booking, status_code=status.HTTP_201_CREATED)
async def worker_book(
    cal: CalendarDep, hub: HubDep, tenant_id: str, req: BookingRequest
) -> Booking:
    try:
        booking = await cal.book(tenant_id, req)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    await hub.notify(
        NotificationEvent(
            tenant_id=tenant_id,
            company_id=booking.company_id,
            event=NotifyEvent.BOOKING_CREATED,
            title=(
                f"Booking: {booking.name}"
                f"{f' - {booking.service_name}' if booking.service_name else ''}"
                f" at {booking.start:%a %d %b %H:%M}"
            ),
            body=(booking.notes or "Booked by the AI assistant during a call.")
            + (f" Assigned to {booking.resource_name}." if booking.resource_name else ""),
            call_id=booking.call_id,
        )
    )
    return booking


# -- BYO SIP ---------------------------------------------------------------------------------


class TrunkView(BaseModel):
    trunk: dict[str, Any]
    credentials: IssuedCredentials | None = None


@router.get("/telephony/guides", response_model=list[ProviderGuide])
async def provider_guides() -> list[ProviderGuide]:
    return PROVIDER_GUIDES


@router.get("/telephony/trunks")
async def list_trunks(user: UserDep, sip: SipDep, tenant_id: str) -> list[dict[str, Any]]:
    user.require_tenant(tenant_id)
    return [t.public() for t in await sip.trunks(tenant_id)]


@router.post(
    "/telephony/trunks",
    response_model=TrunkView,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature("byo_sip"))],
)
async def create_trunk(
    user: UserDep, sip: SipDep, store: StoreDep, tenant_id: str, body: TrunkInput
) -> TrunkView:
    user.require_admin(tenant_id)
    try:
        trunk, creds = await sip.create(tenant_id, await _company(store, tenant_id), body)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return TrunkView(trunk=trunk.public(), credentials=creds)


@router.get("/telephony/trunks/{trunk_id}")
async def get_trunk(user: UserDep, sip: SipDep, tenant_id: str, trunk_id: str) -> dict[str, Any]:
    user.require_tenant(tenant_id)
    t = await sip.get(tenant_id, trunk_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trunk not found")
    return t.public()


@router.put("/telephony/trunks/{trunk_id}", response_model=TrunkView)
async def update_trunk(
    user: UserDep, sip: SipDep, tenant_id: str, trunk_id: str, body: TrunkInput
) -> TrunkView:
    user.require_admin(tenant_id)
    try:
        res = await sip.update(tenant_id, trunk_id, body)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    if res is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trunk not found")
    return TrunkView(trunk=res[0].public(), credentials=res[1])


@router.delete("/telephony/trunks/{trunk_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trunk(user: UserDep, sip: SipDep, tenant_id: str, trunk_id: str) -> None:
    user.require_admin(tenant_id)
    if not await sip.delete(tenant_id, trunk_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trunk not found")


@router.post("/telephony/trunks/{trunk_id}/rotate", response_model=IssuedCredentials)
async def rotate_trunk(
    user: UserDep, sip: SipDep, tenant_id: str, trunk_id: str
) -> IssuedCredentials:
    user.require_admin(tenant_id)
    creds = await sip.rotate_credentials(tenant_id, trunk_id)
    if creds is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trunk not found or not in PBX mode")
    return creds


@router.post("/telephony/trunks/{trunk_id}/refresh")
async def refresh_trunk(
    user: UserDep, sip: SipDep, tenant_id: str, trunk_id: str
) -> dict[str, Any]:
    user.require_tenant(tenant_id)
    t = await sip.refresh_status(tenant_id, trunk_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trunk not found")
    return t.public()


class TestCallInput(BaseModel):
    to: str | None = None


@router.post("/telephony/trunks/{trunk_id}/test-call", response_model=TestCallResult)
async def test_call(
    user: UserDep, sip: SipDep, tenant_id: str, trunk_id: str, body: TestCallInput
) -> TestCallResult:
    user.require_admin(tenant_id)
    res = await sip.test_call(tenant_id, trunk_id, body.to)
    if res is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trunk not found")
    return res


@worker.post("/telephony/admit", response_model=AdmitResult)
async def admit_call(sip: SipDep, number: str, call_id: str) -> AdmitResult:
    return await sip.admit(number, call_id)


@worker.post("/telephony/release", status_code=status.HTTP_204_NO_CONTENT)
async def release_call(sip: SipDep, call_id: str) -> None:
    sip.release(call_id)
