from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from parlio_api.admin import AdminService
from parlio_api.billing import BillingService
from parlio_api.browser_voice import BrowserVoiceService
from parlio_api.calendar import CalendarService
from parlio_api.compliance import ComplianceService
from parlio_api.connectors import ConnectorService, TenantApiKey
from parlio_api.inbox import InboxService
from parlio_api.integrations import IntegrationHub
from parlio_api.live import ApprovalService, LiveCallHub, SupervisorService
from parlio_api.messaging import MessageService
from parlio_api.notifications import NotificationService
from parlio_api.observability import AuditLog, Telemetry
from parlio_api.outbound import OutboundService
from parlio_api.payments import PaymentService
from parlio_api.postcall import PostCallProcessor
from parlio_api.qa import QAService, SimulationService, VoiceCloneService
from parlio_api.security import SecurityService
from parlio_api.settings import Settings, get_settings
from parlio_api.sip import SipService
from parlio_api.store import CallStore
from parlio_api.tickets import TicketService
from parlio_api.value import DigestService, ValueService
from parlio_api.whitelabel import WhiteLabelService


def get_store(request: Request) -> CallStore:
    store: CallStore = request.app.state.store
    return store


def get_admin(request: Request) -> AdminService:
    svc: AdminService = request.app.state.admin
    return svc


AdminDep = Annotated[AdminService, Depends(get_admin)]


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


def get_qa(request: Request) -> QAService:
    svc: QAService = request.app.state.qa
    return svc


def get_simulation(request: Request) -> SimulationService:
    svc: SimulationService = request.app.state.simulation
    return svc


def get_voice_clones(request: Request) -> VoiceCloneService:
    svc: VoiceCloneService = request.app.state.voice_clones
    return svc


def get_security(request: Request) -> SecurityService:
    svc: SecurityService = request.app.state.security
    return svc


def get_value(request: Request) -> ValueService:
    svc: ValueService = request.app.state.value
    return svc


def get_digest(request: Request) -> DigestService:
    svc: DigestService = request.app.state.digest
    return svc


def get_whitelabel(request: Request) -> WhiteLabelService:
    svc: WhiteLabelService = request.app.state.whitelabel
    return svc


def get_compliance(request: Request) -> ComplianceService:
    c: ComplianceService = request.app.state.compliance
    return c


def get_connectors(request: Request) -> ConnectorService:
    c: ConnectorService = request.app.state.connectors
    return c


def get_outbound(request: Request) -> OutboundService:
    o: OutboundService = request.app.state.outbound
    return o


def get_live(request: Request) -> LiveCallHub:
    h: LiveCallHub = request.app.state.live
    return h


def get_supervisor(request: Request) -> SupervisorService:
    s: SupervisorService = request.app.state.supervisor
    return s


def get_approvals(request: Request) -> ApprovalService:
    a: ApprovalService = request.app.state.approvals
    return a


def get_inbox(request: Request) -> InboxService:
    i: InboxService = request.app.state.inbox
    return i


def get_browser_voice(request: Request) -> BrowserVoiceService:
    b: BrowserVoiceService = request.app.state.browser_voice
    return b


def get_payments(request: Request) -> PaymentService:
    p: PaymentService = request.app.state.payments
    return p


StoreDep = Annotated[CallStore, Depends(get_store)]
InboxDep = Annotated[InboxService, Depends(get_inbox)]
BrowserVoiceDep = Annotated[BrowserVoiceService, Depends(get_browser_voice)]
PaymentsDep = Annotated[PaymentService, Depends(get_payments)]
BillingDep = Annotated[BillingService, Depends(get_billing)]
TelemetryDep = Annotated[Telemetry, Depends(get_telemetry)]
AuditDep = Annotated[AuditLog, Depends(get_audit)]
ComplianceDep = Annotated[ComplianceService, Depends(get_compliance)]
ConnectorsDep = Annotated[ConnectorService, Depends(get_connectors)]
OutboundDep = Annotated[OutboundService, Depends(get_outbound)]
LiveDep = Annotated[LiveCallHub, Depends(get_live)]
SupervisorDep = Annotated[SupervisorService, Depends(get_supervisor)]
ApprovalsDep = Annotated[ApprovalService, Depends(get_approvals)]
SmsDep = Annotated[MessageService, Depends(get_sms)]
NotificationsDep = Annotated[NotificationService, Depends(get_notifications)]
CalendarDep = Annotated[CalendarService, Depends(get_calendar)]
SipDep = Annotated[SipService, Depends(get_sip)]
HubDep = Annotated[IntegrationHub, Depends(get_hub)]
TicketsDep = Annotated[TicketService, Depends(get_tickets)]
PostCallDep = Annotated[PostCallProcessor, Depends(get_postcall)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
QADep = Annotated[QAService, Depends(get_qa)]
SimulationDep = Annotated[SimulationService, Depends(get_simulation)]
VoiceCloneDep = Annotated[VoiceCloneService, Depends(get_voice_clones)]
SecurityDep = Annotated[SecurityService, Depends(get_security)]
ValueDep = Annotated[ValueService, Depends(get_value)]
DigestDep = Annotated[DigestService, Depends(get_digest)]
WhiteLabelDep = Annotated[WhiteLabelService, Depends(get_whitelabel)]


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
