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
from parlio_api.billing import (
    BillingProvider,
    BillingService,
    SimulatedBilling,
    SimulatedNumbers,
    StripeBilling,
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
from parlio_api.db.engine import make_engine, migrate
from parlio_api.db.postgres import PostgresStore
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
from parlio_api.outbound import (
    Dialer,
    LiveKitDialer,
    OutboundLoop,
    OutboundService,
    SimulatedDialer,
)
from parlio_api.postcall import Analyser, HeuristicAnalyser, OpenAIAnalyser, PostCallProcessor
from parlio_api.routes import (
    account,
    connectors,
    dashboard,
    integrations,
    platform,
    worker,
)
from parlio_api.routes import (
    inbox as inbox_routes,
)
from parlio_api.routes import (
    live as live_routes,
)
from parlio_api.routes import (
    outbound as outbound_routes,
)
from parlio_api.settings import Settings, get_settings
from parlio_api.sip import SimulatedProvisioner, SimulatedRegistrar, SipProvisioner, SipService
from parlio_api.sip_livekit import LiveKitProvisioner
from parlio_api.store import CallStore, MemoryStore
from parlio_api.telephony.base import TelephonyProvider
from parlio_api.telephony.telnyx import TelnyxProvider
from parlio_api.tickets import Notifier, SlaMonitor, TicketService
from parlio_api.vault import LocalVault
from parlio_voice.config_client import DEMO_CONFIG
from parlio_voice.models import CallEvent, CallEventType

log = logging.getLogger("parlio.api")


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
        return LiveKitDialer(settings.outbound_trunk_id)
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


def build_sip_provisioner(settings: Settings) -> SipProvisioner:
    if settings.sip_provisioner == "livekit" and settings.livekit_url:
        return LiveKitProvisioner(
            api.LiveKitAPI(
                settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
            )
        )
    return SimulatedProvisioner()


def build_analyser(settings: Settings) -> Analyser:
    if settings.postcall_analyser == "openai" and settings.openai_api_key:
        return OpenAIAnalyser(settings.openai_api_key, model=settings.openai_model)
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
        await store.upsert_assistant(DEMO_CONFIG, [settings.demo_number])

    vault = LocalVault(settings.vault_key)
    app.state.vault = vault
    if settings.env != "dev" and settings.vault_key == "dev-only-change-me":
        log.error("PARLIO_VAULT_KEY is the dev default in env=%s; set a real key", settings.env)
    sms = MessageService(store, build_sms_provider(settings), settings.sms_from_number)
    app.state.sms = sms
    notifications = NotificationService(
        store, build_email(settings), sms, fallback_webhook_url=settings.notify_webhook_url
    )
    app.state.notifications = notifications
    calendar = CalendarService(store, vault, build_calendar_backends(settings, vault))
    app.state.calendar = calendar
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
    )
    app.state.billing = billing
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
            settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
        )
    else:
        control = SimulatedRoomControl()
    app.state.room_control = control
    app.state.supervisor = SupervisorService(live, control)
    app.state.approvals = ApprovalService(store, live, notifications, settings.dashboard_url)
    hub = IntegrationHub(
        store, sms, notifications, sip, telemetry, compliance, connectors, outbound, live
    )
    app.state.hub = hub
    calendar.on_booked = hub.on_booking

    postcall = PostCallProcessor(
        store, build_analyser(settings), settings.postcall_concurrency, on_done=hub.on_postcall
    )
    app.state.postcall = postcall
    notifier: Notifier = RuleNotifier(notifications)
    tickets = TicketService(store, notifier, on_created=hub.on_ticket)
    app.state.tickets = tickets
    outbound.on_ticket = tickets.create_from_intake
    sla = SlaMonitor(tickets, settings.sla_check_interval_s)
    sla.start()

    async def booking_url(tenant_id: str) -> str | None:
        conn = await calendar.primary(tenant_id)
        return conn.booking_url if conn else None

    rules = RuleTextAgent(booking_url)
    text_agent: TextAgent = rules
    if settings.postcall_analyser == "openai" and settings.openai_api_key:
        text_agent = OpenAITextAgent(settings.openai_api_key, settings.openai_model, fallback=rules)
    senders: dict[Channel, ChannelSender] = {
        Channel.SMS: SmsSender(sms),
        Channel.WEBCHAT: StoreOnlySender(),
    }
    inbox = InboxService(
        store,
        text_agent,
        senders,
        notifications=notifications,
        live=live,
        on_ticket=tickets.create_from_intake,
        sla_minutes=settings.inbox_sla_minutes,
    )
    senders[Channel.WHATSAPP] = WhatsAppSender(inbox.whatsapp)
    app.state.inbox = inbox
    hub.inbox = inbox
    inbox_sla = InboxSlaLoop(inbox, settings.inbox_sweep_interval_s)
    inbox_sla.start()

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
        await compliance.stop()
        await retry_loop.stop()
        await outbound_loop.stop()
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
        allow_origins=sorted({*s.cors_origins, s.dashboard_url}),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(worker.router)
    app.include_router(dashboard.router)
    app.include_router(account.router)
    app.include_router(account.public)
    app.include_router(integrations.router)
    app.include_router(integrations.public)
    app.include_router(integrations.worker)
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
    app.include_router(platform.router)
    app.include_router(platform.public)
    app.include_router(platform.ops)

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
