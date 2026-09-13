from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from parlio_api.billing import BillingService
from parlio_api.calendar import CalendarService
from parlio_api.compliance import ComplianceService
from parlio_api.connectors import ConnectorService, TenantApiKey
from parlio_api.integrations import IntegrationHub
from parlio_api.messaging import MessageService
from parlio_api.notifications import NotificationService
from parlio_api.observability import AuditLog, Telemetry
from parlio_api.postcall import PostCallProcessor
from parlio_api.settings import Settings, get_settings
from parlio_api.sip import SipService
from parlio_api.store import CallStore
from parlio_api.tickets import TicketService


def get_store(request: Request) -> CallStore:
    store: CallStore = request.app.state.store
    return store


def get_postcall(request: Request) -> PostCallProcessor:
    proc: PostCallProcessor = request.app.state.postcall
    return proc


def get_tickets(request: Request) -> TicketService:
    svc: TicketService = request.app.state.tickets
    return svc


def get_sms(request: Request) -> MessageService:
    svc: MessageService = request.app.state.sms
    return svc


def get_notifications(request: Request) -> NotificationService:
    svc: NotificationService = request.app.state.notifications
    return svc


def get_calendar(request: Request) -> CalendarService:
    svc: CalendarService = request.app.state.calendar
    return svc


def get_sip(request: Request) -> SipService:
    svc: SipService = request.app.state.sip
    return svc


def get_hub(request: Request) -> IntegrationHub:
    hub: IntegrationHub = request.app.state.hub
    return hub


def get_billing(request: Request) -> BillingService:
    svc: BillingService = request.app.state.billing
    return svc


def get_telemetry(request: Request) -> Telemetry:
    t: Telemetry = request.app.state.telemetry
    return t


def get_audit(request: Request) -> AuditLog:
    a: AuditLog = request.app.state.audit
    return a


def get_compliance(request: Request) -> ComplianceService:
    c: ComplianceService = request.app.state.compliance
    return c


def get_connectors(request: Request) -> ConnectorService:
    c: ConnectorService = request.app.state.connectors
    return c


StoreDep = Annotated[CallStore, Depends(get_store)]
BillingDep = Annotated[BillingService, Depends(get_billing)]
TelemetryDep = Annotated[Telemetry, Depends(get_telemetry)]
AuditDep = Annotated[AuditLog, Depends(get_audit)]
ComplianceDep = Annotated[ComplianceService, Depends(get_compliance)]
ConnectorsDep = Annotated[ConnectorService, Depends(get_connectors)]
SmsDep = Annotated[MessageService, Depends(get_sms)]
NotificationsDep = Annotated[NotificationService, Depends(get_notifications)]
CalendarDep = Annotated[CalendarService, Depends(get_calendar)]
SipDep = Annotated[SipService, Depends(get_sip)]
HubDep = Annotated[IntegrationHub, Depends(get_hub)]
TicketsDep = Annotated[TicketService, Depends(get_tickets)]
PostCallDep = Annotated[PostCallProcessor, Depends(get_postcall)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def require_worker_key(
    store: StoreDep,
    settings: SettingsDep,
    x_worker_key: Annotated[str | None, Header()] = None,
) -> None:
    """Accept the bootstrap env key or any non-revoked key issued via the store (hash compare)."""
    if not x_worker_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing worker key")
    if secrets.compare_digest(x_worker_key, settings.worker_api_key):
        return
    if await store.verify_worker_key(x_worker_key):
        return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid worker key")


async def require_tenant_api_key(
    connectors: ConnectorsDep,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> TenantApiKey:
    """Customer-facing inbound API: `Authorization: Bearer pk_...` or `X-Api-Key`."""
    raw = x_api_key
    if not raw and authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing API key")
    key = await connectors.resolve_api_key(raw)
    if key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
    return key


ApiKeyDep = Annotated[TenantApiKey, Depends(require_tenant_api_key)]
