from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from parlio_api.admin import AdminService
from parlio_api.adoption import AnnouncementService, WhiteGloveService
from parlio_api.advisor import AdvisorService
from parlio_api.billing import ENTITLEMENTS, BillingService
from parlio_api.browser_voice import BrowserVoiceService
from parlio_api.calendar import CalendarService
from parlio_api.compliance import ComplianceService
from parlio_api.connectors import ConnectorService, TenantApiKey
from parlio_api.contacts import ContactIntelligence
from parlio_api.drafting import Drafter
from parlio_api.help import HelpAssistant
from parlio_api.improve import ImproveService
from parlio_api.inbox import InboxService
from parlio_api.integrations import IntegrationHub
from parlio_api.live import ApprovalService, LiveCallHub, SupervisorService
from parlio_api.messaging import MessageService
from parlio_api.notifications import NotificationService
from parlio_api.observability import AuditLog, Telemetry
from parlio_api.ops import OpsService
from parlio_api.outbound import OutboundService
from parlio_api.payments import PaymentService
from parlio_api.postcall import PostCallProcessor
from parlio_api.qa import QAService, SimulationService, VoiceCloneService
from parlio_api.reminders import ReminderService
from parlio_api.reports import ReportService
from parlio_api.schedule import ScheduleService
from parlio_api.scheduling import SchedulingService
from parlio_api.screening import ScreeningService
from parlio_api.security import SecurityService
from parlio_api.settings import Settings, get_settings
from parlio_api.sip import SipService
from parlio_api.store import CallStore
from parlio_api.support import SupportDesk
from parlio_api.tickets import TicketService
from parlio_api.value import DigestService, ValueService
from parlio_api.voices import VoicePreviewer
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


def get_drafter(request: Request) -> Drafter:
    d: Drafter = request.app.state.drafter
    return d


def get_help(request: Request) -> HelpAssistant:
    h: HelpAssistant = request.app.state.help
    return h


def get_voice_previewer(request: Request) -> VoicePreviewer:
    p: VoicePreviewer = request.app.state.voice_previewer
    return p


def get_sms(request: Request) -> MessageService:
    svc: MessageService = request.app.state.sms
    return svc


def get_notifications(request: Request) -> NotificationService:
    svc: NotificationService = request.app.state.notifications
    return svc


def get_calendar(request: Request) -> CalendarService:
    svc: CalendarService = request.app.state.calendar
    return svc


def get_scheduling(request: Request) -> SchedulingService:
    svc: SchedulingService = request.app.state.scheduling
    return svc


def get_schedule(request: Request) -> ScheduleService:
    svc: ScheduleService = request.app.state.schedule
    return svc


def get_sip(request: Request) -> SipService:
    svc: SipService = request.app.state.sip
    return svc


def get_reminders(request: Request) -> ReminderService:
    svc: ReminderService = request.app.state.reminders
    return svc


def get_screening(request: Request) -> ScreeningService:
    svc: ScreeningService = request.app.state.screening
    return svc


def get_contacts(request: Request) -> ContactIntelligence:
    svc: ContactIntelligence = request.app.state.contacts
    return svc


def get_advisor(request: Request) -> AdvisorService:
    svc: AdvisorService = request.app.state.advisor
    return svc


def get_reports(request: Request) -> ReportService:
    svc: ReportService = request.app.state.reports
    return svc


def get_hub(request: Request) -> IntegrationHub:
    hub: IntegrationHub = request.app.state.hub
    return hub


def get_ops(request: Request) -> OpsService:
    svc: OpsService = request.app.state.ops
    return svc


def get_support(request: Request) -> SupportDesk:
    svc: SupportDesk = request.app.state.support
    return svc


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


def get_improve(request: Request) -> ImproveService:
    svc: ImproveService = request.app.state.improve
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


def get_whiteglove(request: Request) -> WhiteGloveService:
    svc: WhiteGloveService = request.app.state.whiteglove
    return svc


def get_announcements(request: Request) -> AnnouncementService:
    svc: AnnouncementService = request.app.state.announcements
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
OpsDep = Annotated[OpsService, Depends(get_ops)]
SupportDep = Annotated[SupportDesk, Depends(get_support)]


def require_feature(key: str) -> Callable[[str, BillingService], Awaitable[None]]:
    """Route dependency: 403 unless the tenant's plan (or a flag override) includes ``key``."""

    async def _dep(tenant_id: str, billing: BillingDep) -> None:
        if not await billing.entitled(tenant_id, key):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"'{ENTITLEMENTS.get(key, key)}' is not included in your plan",
            )

    return _dep


async def ensure_feature(billing: BillingService, tenant_id: str, key: str) -> None:
    """Imperative form of ``require_feature`` for routes that decide the key from the body."""
    if not await billing.entitled(tenant_id, key):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"'{ENTITLEMENTS.get(key, key)}' is not included in your plan",
        )


async def ensure_cap(billing: BillingService, tenant_id: str, key: str, current: int) -> None:
    """403 when adding one more of ``key`` would exceed the tenant's effective plan cap."""
    try:
        await billing.check_cap(tenant_id, key, current)
    except ValueError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e


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
SchedulingDep = Annotated[SchedulingService, Depends(get_scheduling)]
ScheduleDep = Annotated[ScheduleService, Depends(get_schedule)]
ScreeningDep = Annotated[ScreeningService, Depends(get_screening)]
ContactsDep = Annotated[ContactIntelligence, Depends(get_contacts)]
AdvisorDep = Annotated[AdvisorService, Depends(get_advisor)]
ReportsDep = Annotated[ReportService, Depends(get_reports)]
RemindersDep = Annotated[ReminderService, Depends(get_reminders)]
SipDep = Annotated[SipService, Depends(get_sip)]
HubDep = Annotated[IntegrationHub, Depends(get_hub)]
TicketsDep = Annotated[TicketService, Depends(get_tickets)]
DrafterDep = Annotated[Drafter, Depends(get_drafter)]
HelpDep = Annotated[HelpAssistant, Depends(get_help)]
VoicePreviewDep = Annotated[VoicePreviewer, Depends(get_voice_previewer)]
PostCallDep = Annotated[PostCallProcessor, Depends(get_postcall)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
QADep = Annotated[QAService, Depends(get_qa)]
SimulationDep = Annotated[SimulationService, Depends(get_simulation)]
ImproveDep = Annotated[ImproveService, Depends(get_improve)]
VoiceCloneDep = Annotated[VoiceCloneService, Depends(get_voice_clones)]
SecurityDep = Annotated[SecurityService, Depends(get_security)]
ValueDep = Annotated[ValueService, Depends(get_value)]
DigestDep = Annotated[DigestService, Depends(get_digest)]
WhiteLabelDep = Annotated[WhiteLabelService, Depends(get_whitelabel)]
WhiteGloveDep = Annotated[WhiteGloveService, Depends(get_whiteglove)]
AnnouncementsDep = Annotated[AnnouncementService, Depends(get_announcements)]


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
