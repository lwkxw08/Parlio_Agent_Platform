from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import cast

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from livekit import api
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy.ext.asyncio import AsyncEngine

from parlio_api import __version__
from parlio_api.admin import AdminService
from parlio_api.adoption import (
    AnnouncementService,
    WhiteGloveService,
    first_week_report,
    render_first_week,
)
from parlio_api.advisor import AdvisorService, Reworder
from parlio_api.billing import (
    BillingProvider,
    BillingService,
    SimulatedBilling,
    SimulatedNumbers,
    StripeBilling,
)
from parlio_api.browser_voice import (
    AgentDispatcher,
    BrowserVoiceService,
    LiveKitDispatcher,
    SimulatedDispatcher,
)
from parlio_api.calendar import (
    CalendarBackend,
    CalendarProvider,
    CalendarService,
    GoogleCalendarBackend,
    MicrosoftCalendarBackend,
    SimulatedBackend,
)
from parlio_api.compliance import ComplianceService
from parlio_api.connectors import ConnectorService, RetryLoop, build_backends
from parlio_api.contacts import ContactIntelligence
from parlio_api.db.engine import make_engine, migrate
from parlio_api.db.postgres import PostgresStore
from parlio_api.drafting import Drafter
from parlio_api.help import Guide, HelpAssistant
from parlio_api.improve import ImproveService
from parlio_api.inbox import (
    Channel,
    ChannelSender,
    InboxService,
    InboxSlaLoop,
    OpenAITextAgent,
    RuleTextAgent,
    SmsSender,
    StoreOnlySender,
    TextAgent,
    WhatsAppSender,
)
from parlio_api.integrations import IntegrationHub
from parlio_api.journey import CheckInLoop
from parlio_api.live import (
    ApprovalService,
    LiveCallHub,
    LiveKitRoomControl,
    RoomControl,
    SimulatedRoomControl,
    SupervisorService,
)
from parlio_api.messaging import CarrierSmsProvider, LogSmsProvider, MessageService, SmsProvider
from parlio_api.notifications import (
    EmailSender,
    LogEmailSender,
    NotificationService,
    ResendEmailSender,
    RuleNotifier,
)
from parlio_api.observability import AuditLog, RateLimiter, Telemetry, build_tracer
from parlio_api.ops import HttpPager, OpsLoop, OpsService
from parlio_api.outbound import (
    Dialer,
    LiveKitDialer,
    OutboundLoop,
    OutboundService,
    SimulatedDialer,
)
from parlio_api.payments import (
    PaymentProvider,
    PaymentService,
    SimulatedPayments,
    StripePayments,
)
from parlio_api.postcall import Analyser, HeuristicAnalyser, OpenAIAnalyser, PostCallProcessor
from parlio_api.qa import (
    HeuristicScorer,
    OpenAIScorer,
    QAScorer,
    QAService,
    SimulatedCloneProvider,
    SimulationService,
    VoiceCloneService,
)
from parlio_api.recordings import RecordingStorage
from parlio_api.reminders import ReminderLoop, ReminderService
from parlio_api.reports import ReportService
from parlio_api.routes import (
    account,
    connectors,
    dashboard,
    integrations,
    platform,
    worker,
)
from parlio_api.routes import admin as admin_routes
from parlio_api.routes import adoption as adoption_routes
from parlio_api.routes import advisor as advisor_routes
from parlio_api.routes import help as help_routes
from parlio_api.routes import (
    inbox as inbox_routes,
)
from parlio_api.routes import journey as journey_routes
from parlio_api.routes import (
    live as live_routes,
)
from parlio_api.routes import nav as nav_routes
from parlio_api.routes import ops as ops_routes
from parlio_api.routes import (
    outbound as outbound_routes,
)
from parlio_api.routes import (
    payments as payment_routes,
)
from parlio_api.routes import (
    quality as quality_routes,
)
from parlio_api.routes import (
    reminders as reminders_routes,
)
from parlio_api.routes import (
    screening as screening_routes,
)
from parlio_api.routes import site as site_routes
from parlio_api.routes import (
    team as team_routes,
)
from parlio_api.routes import (
    value as value_routes,
)
from parlio_api.schedule import ScheduleService
from parlio_api.scheduling import SchedulingService
from parlio_api.screening import ScreeningService
from parlio_api.security import SecurityService
from parlio_api.settings import Settings, get_settings
from parlio_api.sip import SimulatedProvisioner, SimulatedRegistrar, SipProvisioner, SipService
from parlio_api.sip_livekit import LiveKitInboundEdge, LiveKitProvisioner
from parlio_api.store import CallStore, MemoryStore, RequiredField, Ticket
from parlio_api.support import (
    SUPPORT_TENANT,
    LinearIssueTracker,
    LogIssueTracker,
    SupportDesk,
    SupportTextAgent,
    SupportTicketIn,
    support_assistant,
)
from parlio_api.telephony.base import InboundEdge, TelephonyProvider
from parlio_api.telephony.telnyx import TelnyxProvider
from parlio_api.tickets import Notifier, SlaMonitor, TicketService
from parlio_api.value import DigestService, ValueService
from parlio_api.vault import LocalVault
from parlio_api.voices import VoicePreviewer
from parlio_api.whitelabel import WhiteLabelService
from parlio_voice.config_client import DEMO_CONFIG
from parlio_voice.models import CallEvent, CallEventType

log = logging.getLogger("parlio.api")

DEMO_REQUIRED_FIELDS = [
    RequiredField(name="name", description="caller's full name"),
    RequiredField(name="phone", description="best contact number"),
    RequiredField(name="address", description="property address or postcode for the job"),
    RequiredField(name="issue", description="what the caller needs (fault, service, quote)"),
    RequiredField(name="email", description="caller's email address", required=False),
]


async def consume_events(
    redis: Redis,
    store: CallStore,
    stream: str,
    group: str,
    stop: asyncio.Event,
    postcall: PostCallProcessor | None = None,
    tickets: TicketService | None = None,
    hub: IntegrationHub | None = None,
) -> None:
    """Redis Streams consumer: folds worker call events into the store.

    Swappable for a NATS/Kafka consumer at enterprise scale; the store interface is unchanged.
    """
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    consumer = "api-1"
    while not stop.is_set():
        try:
            batches = await redis.xreadgroup(group, consumer, {stream: ">"}, count=100, block=1000)
        except Exception:
            log.warning("event consumer read failed; retrying", exc_info=True)
            await asyncio.sleep(1)
            continue
        typed = cast("list[tuple[str, list[tuple[str, dict[str, str]]]]]", batches or [])
        for _stream, messages in typed:
            for msg_id, fields in messages:
                raw = fields.get("body") or fields.get("event")
                if raw:
                    try:
                        ev = CallEvent.model_validate_json(raw)
                        applied = await store.apply_event(ev)
                        if applied and postcall and ev.type == CallEventType.CALL_ENDED:
                            postcall.enqueue(ev.call_id)
                        if applied and tickets and ev.type == CallEventType.TICKET_CREATED:
                            await tickets.rebuild_from_event(ev)
                        if applied and hub is not None:
                            await hub.on_event(ev)
                    except Exception:
                        log.exception("bad event %s", msg_id)
                await redis.xack(stream, group, msg_id)


def build_billing_provider(settings: Settings) -> BillingProvider:
    if settings.billing_provider == "stripe" and settings.stripe_secret_key:
        return StripeBilling(settings.stripe_secret_key, settings.stripe_webhook_secret)
    if settings.billing_provider == "stripe":
        log.warning("PARLIO_BILLING_PROVIDER=stripe but no secret key; using simulated billing")
    return SimulatedBilling()


def build_payment_provider(settings: Settings) -> PaymentProvider:
    if settings.payments_provider == "stripe" and settings.stripe_secret_key:
        return StripePayments(
            settings.stripe_secret_key,
            settings.stripe_payments_webhook_secret or settings.stripe_webhook_secret,
        )
    if settings.payments_provider == "stripe":
        log.warning("PARLIO_PAYMENTS_PROVIDER=stripe but no secret key; using simulated payments")
    return SimulatedPayments(settings.dashboard_url)


def build_number_provider(settings: Settings) -> TelephonyProvider:
    if settings.number_provider == "telnyx" and settings.telnyx_api_key:
        return TelnyxProvider(
            settings.telnyx_api_key,
            connection_id=settings.telnyx_connection_id,
            messaging_profile_id=settings.telnyx_messaging_profile_id,
        )
    return SimulatedNumbers()


def build_sms_provider(settings: Settings) -> SmsProvider:
    if settings.sms_provider == "telnyx" and settings.telnyx_api_key:
        return CarrierSmsProvider(
            TelnyxProvider(
                settings.telnyx_api_key,
                messaging_profile_id=settings.telnyx_messaging_profile_id,
            )
        )
    return LogSmsProvider()


def build_dialer(settings: Settings) -> Dialer:
    if settings.outbound_dialer == "livekit" and settings.outbound_trunk_id:
        return LiveKitDialer(
            settings.outbound_trunk_id,
            url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
            agent_name=settings.agent_name,
        )
    if settings.outbound_dialer == "livekit":
        log.warning("PARLIO_OUTBOUND_DIALER=livekit but no trunk id; using simulated dialer")
    return SimulatedDialer()


def build_email(settings: Settings) -> EmailSender:
    if settings.resend_api_key:
        return ResendEmailSender(settings.resend_api_key, settings.email_from)
    return LogEmailSender()


def build_calendar_backends(
    settings: Settings, vault: LocalVault
) -> dict[CalendarProvider, CalendarBackend]:
    backends: dict[CalendarProvider, CalendarBackend] = {
        CalendarProvider.SIMULATED: SimulatedBackend()
    }
    if settings.google_client_id and settings.google_client_secret:
        backends[CalendarProvider.GOOGLE] = GoogleCalendarBackend(
            settings.google_client_id, settings.google_client_secret, vault
        )
    if settings.microsoft_client_id and settings.microsoft_client_secret:
        backends[CalendarProvider.MICROSOFT] = MicrosoftCalendarBackend(
            settings.microsoft_client_id, settings.microsoft_client_secret, vault
        )
    return backends


def build_inbound_edge(settings: Settings) -> InboundEdge | None:
    if settings.inbound_trunk_id and settings.livekit_url:
        return LiveKitInboundEdge(
            api.LiveKitAPI(
                settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
            ),
            settings.inbound_trunk_id,
        )
    if settings.inbound_trunk_id:
        log.warning("PARLIO_INBOUND_TRUNK_ID set but no LiveKit URL; bought numbers not routed")
    return None


def build_sip_provisioner(settings: Settings) -> SipProvisioner:
    if settings.sip_provisioner == "livekit" and settings.livekit_url:
        return LiveKitProvisioner(
            api.LiveKitAPI(
                settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
            )
        )
    return SimulatedProvisioner()


def build_analyser(settings: Settings) -> Analyser:
    if (key := settings.llm_key) is not None:
        return OpenAIAnalyser(key, model=settings.openai_model)
    return HeuristicAnalyser()


async def build_store(
    settings: Settings, redis: Redis | None
) -> tuple[CallStore, AsyncEngine | None]:
    if settings.store_backend == "memory":
        return MemoryStore(redis), None
    engine = make_engine(settings.database_url, settings.db_pool_size)
    if settings.db_auto_migrate:
        await migrate(engine)
    store = PostgresStore(engine, redis)
    await store.ensure_partitions()
    return store, engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    redis: Redis | None = None
    if settings.redis_url:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.ping()
        except Exception:
            log.warning("redis unavailable at %s; running without it", settings.redis_url)
            await redis.aclose()
            redis = None

    store, engine = await build_store(settings, redis)
    app.state.store = store
    if settings.seed_demo_assistant:
        existing = await store.get_assistant(DEMO_CONFIG.assistant_id)
        if existing is None or not existing.business.description:
            await store.upsert_assistant(DEMO_CONFIG.model_copy(deep=True), [settings.demo_number])
        if not await store.required_fields(DEMO_CONFIG.assistant_id):
            await store.set_required_fields(DEMO_CONFIG.assistant_id, DEMO_REQUIRED_FIELDS)

    vault = LocalVault(settings.vault_key)
    app.state.security = SecurityService(store, vault, settings.vault_key)
    app.state.vault = vault
    if settings.env != "dev" and settings.vault_key == "dev-only-change-me":
        log.error("PARLIO_VAULT_KEY is the dev default in env=%s; set a real key", settings.env)
    sms = MessageService(store, build_sms_provider(settings), settings.sms_from_number)
    app.state.sms = sms
    email = build_email(settings)
    notifications = NotificationService(
        store, email, sms, fallback_webhook_url=settings.notify_webhook_url
    )
    app.state.notifications = notifications
    scheduling = SchedulingService(store, vault)
    app.state.scheduling = scheduling
    calendar = CalendarService(
        store,
        vault,
        build_calendar_backends(settings, vault),
        dashboard_url=settings.dashboard_url,
        scheduler=scheduling,
    )
    app.state.calendar = calendar
    app.state.schedule = ScheduleService(calendar)
    app.state.screening = ScreeningService(store)
    sip = SipService(
        store,
        vault,
        build_sip_provisioner(settings),
        SimulatedRegistrar(),
        sip_domain=settings.sip_domain,
    )
    app.state.sip = sip
    telemetry = Telemetry(
        build_tracer("parlio-api", settings.env, settings.otlp_endpoint),
        target_turn_s=settings.target_turn_latency_s,
    )
    app.state.telemetry = telemetry
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute, telemetry)
    app.state.audit = AuditLog(store)
    compliance = ComplianceService(store, settings.retention_sweep_interval_s)
    app.state.compliance = compliance
    compliance.start()
    billing = BillingService(
        store,
        sms,
        build_billing_provider(settings),
        build_number_provider(settings),
        sip_uri=settings.telnyx_sip_uri or f"sip:{settings.sip_domain}",
        trial_days=settings.trial_days,
        edge=build_inbound_edge(settings),
    )
    app.state.billing = billing
    admin = AdminService(store, billing, telemetry, app.state.rate_limiter, settings.vault_key)
    await admin.load()
    app.state.admin = admin
    connectors_http = httpx.AsyncClient(timeout=15.0)
    connectors = ConnectorService(
        store,
        vault,
        build_backends(
            connectors_http,
            google_client_id=settings.google_client_id,
            google_client_secret=settings.google_client_secret,
        ),
        public_url=settings.public_api_url,
    )
    app.state.connectors = connectors
    retry_loop = RetryLoop(connectors, settings.connector_retry_interval_s)
    retry_loop.start()
    outbound = OutboundService(
        store, build_dialer(settings), default_caller_id=settings.outbound_caller_id
    )
    app.state.outbound = outbound
    outbound_loop = OutboundLoop(outbound, settings.outbound_sweep_interval_s)
    outbound_loop.start()
    app.state.outbound_loop = outbound_loop
    live = LiveCallHub()
    app.state.live = live
    control: RoomControl
    if settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret:
        control = LiveKitRoomControl(
            settings.livekit_url,
            settings.livekit_api_key,
            settings.livekit_api_secret,
            ws_url=settings.livekit_public_url,
        )
    else:
        control = SimulatedRoomControl()
    app.state.room_control = control
    dispatcher: AgentDispatcher
    if settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret:
        dispatcher = LiveKitDispatcher(
            settings.livekit_url,
            settings.livekit_api_key,
            settings.livekit_api_secret,
            agent_name=settings.agent_name,
        )
    else:
        dispatcher = SimulatedDispatcher()
    app.state.browser_voice = BrowserVoiceService(store, control, dispatcher)
    app.state.payments = PaymentService(
        store, build_payment_provider(settings), sms, dashboard_url=settings.dashboard_url
    )
    supervisor = SupervisorService(live, control)
    app.state.supervisor = supervisor
    app.state.approvals = ApprovalService(store, live, notifications, settings.dashboard_url)
    hub = IntegrationHub(
        store, sms, notifications, sip, telemetry, compliance, connectors, outbound, live
    )
    app.state.hub = hub

    async def close_ghost_call(call_id: str, tenant_id: str, by: str) -> bool:
        rec = await store.get_call(call_id)
        if (
            rec is None
            or rec.tenant_id != tenant_id
            or rec.status not in ("ringing", "in_progress")
        ):
            return False
        ev = CallEvent(
            type=CallEventType.CALL_ENDED,
            call_id=call_id,
            tenant_id=rec.tenant_id,
            company_id=rec.company_id,
            assistant_id=rec.assistant_id,
            payload={"reason": "supervisor_hangup", "by": by, "transcript": []},
        )
        if await store.apply_event(ev):
            await hub.on_event(ev)
        return True

    supervisor.on_ghost_hangup = close_ghost_call
    calendar.on_booked = hub.on_booking
    contacts = ContactIntelligence(store)
    app.state.contacts = contacts
    hub.contacts = contacts
    app.state.payments.on_paid = contacts.on_payment_paid

    postcall = PostCallProcessor(
        store, build_analyser(settings), settings.postcall_concurrency, on_done=hub.on_postcall
    )
    app.state.postcall = postcall
    notifier: Notifier = RuleNotifier(notifications)
    tickets = TicketService(store, notifier, on_created=hub.on_ticket)
    app.state.tickets = tickets
    outbound.on_ticket = tickets.create_from_intake

    async def _ticket_resolved(t: Ticket) -> None:
        await contacts.on_ticket_resolved(t.tenant_id, t.caller_number, name=t.caller_name)

    tickets.on_resolved = _ticket_resolved
    sla = SlaMonitor(tickets, settings.sla_check_interval_s)
    sla.start()

    async def booking_url(tenant_id: str) -> str | None:
        conn = await calendar.primary(tenant_id)
        return conn.booking_url if conn else None

    rules = RuleTextAgent(booking_url)
    text_agent: TextAgent = rules
    if (key := settings.llm_key) is not None:
        text_agent = OpenAITextAgent(key, settings.openai_model, fallback=rules)
    senders: dict[Channel, ChannelSender] = {
        Channel.SMS: SmsSender(sms),
        Channel.WEBCHAT: StoreOnlySender(),
    }
    scorer: QAScorer = HeuristicScorer()
    if (key := settings.llm_key) is not None:
        scorer = OpenAIScorer(key, settings.openai_model)
    pager = HttpPager(email=email, sms=sms.provider, sms_from=sms.from_number)
    ops = OpsService(store, billing, sip, SimulationService(store, text_agent, scorer), pager)
    app.state.ops = ops
    tracker = (
        LinearIssueTracker(settings.linear_api_key, settings.linear_team_id)
        if settings.linear_api_key and settings.linear_team_id
        else LogIssueTracker()
    )
    support = SupportDesk(
        store,
        ops,
        sip,
        billing,
        build_email(settings),
        tracker,
        dashboard_url=settings.dashboard_url,
    )
    app.state.support = support

    async def _synthetic_ticket(tenant_id: str, title: str, meta: dict[str, object]) -> str | None:
        t = await support.create_ticket(
            tenant_id,
            "system",
            SupportTicketIn(subject=title, body=str(meta.get("detail", "")), priority="p2"),
        )
        return t.id

    ops.open_ticket = _synthetic_ticket
    if not await store.list_assistants(SUPPORT_TENANT):
        await store.upsert_assistant(
            support_assistant(settings.support_number),
            [settings.support_number] if settings.support_number else [],
        )
    inbox = InboxService(
        store,
        SupportTextAgent(support, text_agent),
        senders,
        notifications=notifications,
        live=live,
        on_ticket=tickets.create_from_intake,
        on_ticket_update=tickets.update,
        sla_minutes=settings.inbox_sla_minutes,
    )
    senders[Channel.WHATSAPP] = WhatsAppSender(inbox.whatsapp)
    app.state.inbox = inbox
    hub.inbox = inbox
    tickets.inbox = inbox
    reminders = ReminderService(
        store, sms, business_name=hub.business_name_for, on_ticket=tickets.create_from_intake
    )
    app.state.reminders = reminders
    hub.reminders = reminders

    async def _reminder_reply(tenant_id: str, phone: str, text: str) -> str | None:
        r = await reminders.handle_reply(tenant_id, phone, text)
        return r.text if r else None

    inbox.on_sms_reply = _reminder_reply
    reminder_loop = ReminderLoop(reminders, settings.reminder_sweep_interval_s)
    reminder_loop.start()

    qa = QAService(store, scorer, notifications, settings.dashboard_url)
    app.state.qa = qa
    hub.qa = qa
    app.state.simulation = ops.simulation
    app.state.voice_clones = VoiceCloneService(store, SimulatedCloneProvider())
    value = ValueService(store)
    app.state.value = value
    digest = DigestService(store, value, notifications)
    app.state.digest = digest
    advisor = AdvisorService(
        store,
        value,
        billing,
        notifications,
        Reworder(settings.openai_api_key, model=settings.openai_model),
    )
    app.state.advisor = advisor
    reports = ReportService(store, value, notifications)
    app.state.reports = reports
    app.state.whiteglove = WhiteGloveService(store, billing, notifications)
    app.state.announcements = AnnouncementService(store)
    app.state.drafter = Drafter(settings.openai_api_key, model=settings.openai_model)
    app.state.help = HelpAssistant(Guide(), settings.openai_api_key, model=settings.openai_model)
    app.state.improve = ImproveService(store, ops.simulation, app.state.drafter)
    app.state.voice_previewer = VoicePreviewer(
        settings.cartesia_api_key, settings.elevenlabs_api_key
    )
    app.state.recordings = (
        RecordingStorage(
            settings.recording_s3_endpoint,
            settings.recording_bucket,
            settings.recording_s3_access_key,
            settings.recording_s3_secret_key,
            region=settings.recording_s3_region,
        )
        if settings.recording_s3_endpoint
        and settings.recording_bucket
        and settings.recording_s3_access_key
        and settings.recording_s3_secret_key
        else None
    )
    digest.start()
    advisor.start()
    reports.start()
    app.state.whitelabel = WhiteLabelService(
        store,
        billing,
        dashboard_host=settings.dashboard_url.split("://", 1)[-1].split("/")[0],
        verify_salt=settings.vault_key,
    )
    inbox_sla = InboxSlaLoop(inbox, settings.inbox_sweep_interval_s)
    inbox_sla.start()
    ops_loop = OpsLoop(ops, settings.ops_sweep_interval_s)
    ops_loop.start()

    async def _checkin_digest(tenant_id: str, day: int) -> str:
        if day != 7:
            return ""
        rep = await first_week_report(
            tenant_id, store, value, billing, sip, calendar, notifications
        )
        cfgs = await store.list_assistants(tenant_id)
        return render_first_week(rep, cfgs[0].business_name if cfgs else "your business")

    checkins = CheckInLoop(store, billing, sip, calendar, notifications, enrich=_checkin_digest)
    checkins.start()

    stop = asyncio.Event()
    consumer: asyncio.Task[None] | None = None
    if redis is not None:
        consumer = asyncio.create_task(
            consume_events(
                redis,
                store,
                settings.events_stream,
                settings.events_consumer_group,
                stop,
                postcall,
                tickets,
                hub,
            )
        )
    try:
        yield
    finally:
        stop.set()
        if consumer is not None:
            consumer.cancel()
            with suppress(asyncio.CancelledError):
                await consumer
        await postcall.close()
        await sla.aclose()
        await inbox_sla.aclose()
        await ops_loop.stop()
        await checkins.stop()
        await digest.stop()
        await advisor.stop()
        await reports.stop()
        await compliance.stop()
        await retry_loop.stop()
        await outbound_loop.stop()
        await reminder_loop.stop()
        await connectors_http.aclose()
        await notifications.aclose()
        if isinstance(control, LiveKitRoomControl):
            await control.aclose()
        if redis is not None:
            await redis.aclose()
        if engine is not None:
            await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Parlio Core API", version=__version__, lifespan=lifespan)
    s = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted({*s.cors_origins, s.dashboard_url, s.site_url}),
        allow_origin_regex=s.site_preview_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(worker.router)
    app.include_router(dashboard.router)
    app.include_router(account.router)
    app.include_router(account.public)
    app.include_router(journey_routes.router)
    app.include_router(journey_routes.public)
    app.include_router(site_routes.public)
    app.include_router(adoption_routes.router)
    app.include_router(help_routes.router)
    app.include_router(adoption_routes.admin)
    app.include_router(adoption_routes.public)
    app.include_router(quality_routes.router)
    app.include_router(value_routes.router)
    app.include_router(value_routes.public)
    app.include_router(nav_routes.router)
    app.include_router(value_routes.scim)
    app.include_router(integrations.router)
    app.include_router(integrations.public)
    app.include_router(integrations.worker)
    app.include_router(screening_routes.router)
    app.include_router(advisor_routes.router)
    app.include_router(screening_routes.worker)
    app.include_router(reminders_routes.router)
    app.include_router(team_routes.router)
    app.include_router(team_routes.public)
    app.include_router(connectors.router)
    app.include_router(connectors.public)
    app.include_router(connectors.inbound)
    app.include_router(outbound_routes.router)
    app.include_router(outbound_routes.public)
    app.include_router(outbound_routes.inbound)
    app.include_router(outbound_routes.worker)
    app.include_router(live_routes.router)
    app.include_router(live_routes.approvals)
    app.include_router(live_routes.worker)
    app.include_router(live_routes.public)
    app.include_router(inbox_routes.router)
    app.include_router(inbox_routes.inbound)
    app.include_router(inbox_routes.public)
    app.include_router(payment_routes.router)
    app.include_router(payment_routes.worker)
    app.include_router(payment_routes.public)
    app.include_router(ops_routes.router)
    app.include_router(ops_routes.admin)
    app.include_router(ops_routes.public)
    app.include_router(platform.router)
    app.include_router(platform.public)
    app.include_router(platform.ops)
    app.include_router(admin_routes.router)
    app.include_router(admin_routes.public)

    @app.middleware("http")
    async def tenant_rate_limit(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if not path.startswith("/v1") or path.startswith(("/v1/worker", "/v1/public")):
            return await call_next(request)
        limiter: RateLimiter = request.app.state.rate_limiter
        tenant = request.query_params.get("tenant_id")
        key = (
            f"tenant:{tenant}" if tenant else f"ip:{request.client.host if request.client else '?'}"
        )
        allowed, remaining = limiter.check(key)
        if not allowed:
            return JSONResponse(
                {"detail": "rate limit exceeded; retry shortly"},
                status_code=429,
                headers={"Retry-After": "60", "X-RateLimit-Remaining": "0"},
            )
        resp = await call_next(request)
        resp.headers["X-RateLimit-Remaining"] = str(remaining)
        return resp

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        s = get_settings()
        return {"status": "ok", "version": __version__, "env": s.env, "store": s.store_backend}

    return app


app = create_app()


def run() -> None:
    uvicorn.run("parlio_api.main:app", host="0.0.0.0", port=8000, reload=False)
