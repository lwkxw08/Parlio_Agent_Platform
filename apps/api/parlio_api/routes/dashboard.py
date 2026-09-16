"""Dashboard-facing endpoints.

Every route requires a dashboard principal (see `parlio_api.auth`: dev mode = seeded demo owner,
supabase mode = bearer JWT). `tenant_id` is still an explicit query parameter; callers may only
name organisations they belong to, and the Postgres store applies RLS on top.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from parlio_api.analytics import OverviewAnalytics, channel_usage, compute_overview
from parlio_api.analytics_query import (
    ComparisonAnalytics,
    LlmQuestionParser,
    Question,
    Segment,
    compare,
    parse_question,
)
from parlio_api.auth import UserDep, current_user
from parlio_api.deps import (
    AdminDep,
    BillingDep,
    DrafterDep,
    SettingsDep,
    StoreDep,
    TicketsDep,
    ValueDep,
    VoicePreviewDep,
)
from parlio_api.drafting import Draft, DraftRequest
from parlio_api.insights import InsightInputs, InsightsReport, build_insights
from parlio_api.onboarding import suggest_faqs
from parlio_api.recordings import RecordingStorage, content_type_for
from parlio_api.store import (
    AssistantVersion,
    CallFeedback,
    CallFilter,
    CallRecord,
    RequiredField,
    Ticket,
    TicketEvent,
    TicketStats,
    TicketStatus,
    TicketUpdate,
    TransferRecord,
    TransferStats,
)
from parlio_api.voices import (
    CATALOGUE,
    MARKETS,
    Preview,
    PreviewRequest,
    PreviewUnavailable,
    Voice,
    default_voice,
)
from parlio_voice.models import (
    AssistantConfig,
    Destination,
    Faq,
    TicketIntake,
    TransferConfig,
    TransferMode,
    TTSProvider,
)

router = APIRouter(prefix="/v1", tags=["dashboard"], dependencies=[Depends(current_user)])


class VersionSummary(BaseModel):
    version: int
    created_at: datetime
    created_by: str | None = None
    note: str | None = None
    name: str
    greeting: str
    faq_count: int
    rule_count: int

    @classmethod
    def of(cls, v: AssistantVersion) -> VersionSummary:
        return cls(
            version=v.version,
            created_at=v.created_at,
            created_by=v.created_by,
            note=v.note,
            name=v.config.name,
            greeting=v.config.greeting,
            faq_count=len(v.config.faqs),
            rule_count=len(v.config.rules),
        )


class ShareLink(BaseModel):
    token: str
    url: str


class AssistantUpsert(BaseModel):
    config: AssistantConfig
    numbers: list[str] = Field(
        default_factory=list, description="E.164 numbers routed to this assistant"
    )


class WorkerKeyCreate(BaseModel):
    tenant_id: str | None = None
    name: str = "worker"


class WorkerKeyCreated(BaseModel):
    key: str = Field(description="Shown once; only a hash is stored")


@router.get("/assistants", response_model=list[AssistantConfig])
async def list_assistants(store: StoreDep, tenant_id: str | None = None) -> list[AssistantConfig]:
    return await store.list_assistants(tenant_id)


class AssistantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    business_name: str = Field(min_length=1, max_length=120)
    tenant_id: str | None = None
    copy_from: str | None = Field(default=None, description="assistant_id to clone settings from")


@router.post("/assistants", response_model=AssistantConfig, status_code=status.HTTP_201_CREATED)
async def create_assistant(
    body: AssistantCreate, store: StoreDep, billing: BillingDep, user: UserDep, admin: AdminDep
) -> AssistantConfig:
    """Add another assistant to the organisation (plan-limited via ``Plan.max_assistants``)."""
    tenants = user.tenant_ids
    tenant_id = body.tenant_id or (tenants[0] if tenants else None)
    if tenant_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no organisation")
    user.require_admin(tenant_id)
    existing = await store.list_assistants(tenant_id)
    plan = (await billing.subscription(tenant_id)).plan
    if len(existing) >= plan.max_assistants:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{plan.name} allows {plan.max_assistants} assistant(s); upgrade your plan to add more",
        )
    aid = f"{tenant_id}-{uuid4().hex[:8]}"
    src = await store.get_assistant(body.copy_from) if body.copy_from else None
    if src is not None and src.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot copy from another organisation")
    if src is not None:
        cfg = src.model_copy(
            update={
                "assistant_id": aid,
                "assistant_version": 1,
                "name": body.name,
                "business_name": body.business_name,
            },
            deep=True,
        )
    else:
        company = existing[0].company_id if existing else f"{tenant_id}-main"
        cfg = AssistantConfig(
            tenant_id=tenant_id,
            company_id=company,
            assistant_id=aid,
            name=body.name,
            business_name=body.business_name,
        )
    cfg = await _apply_platform_voice(cfg, admin)
    await store.upsert_assistant(cfg, [])
    return cfg


async def _apply_platform_voice(cfg: AssistantConfig, admin: AdminDep) -> AssistantConfig:
    """The TTS provider is a platform decision: pin it and swap the voice if it doesn't fit."""
    market = (await admin.locale(cfg.tenant_id)).market
    provider = (await admin.voice_settings()).provider_for(market)
    if cfg.voice.provider == provider:
        return cfg
    fallback = default_voice(provider, market)
    voice = cfg.voice.model_copy(
        update={"provider": provider, "voice_id": fallback.id if fallback else cfg.voice.voice_id}
    )
    return cfg.model_copy(update={"voice": voice})


@router.put("/assistants/{assistant_id}", response_model=AssistantConfig)
async def upsert_assistant(
    assistant_id: str, body: AssistantUpsert, store: StoreDep, admin: AdminDep
) -> AssistantConfig:
    if body.config.assistant_id != assistant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "assistant_id mismatch")
    cfg = await _apply_platform_voice(body.config, admin)
    await store.upsert_assistant(cfg, body.numbers)
    return cfg


@router.get("/assistants/{assistant_id}/versions", response_model=list[VersionSummary])
async def list_versions(assistant_id: str, store: StoreDep) -> list[VersionSummary]:
    return [VersionSummary.of(v) for v in await store.list_assistant_versions(assistant_id)]


@router.get("/assistants/{assistant_id}/versions/{version}", response_model=AssistantConfig)
async def get_version(assistant_id: str, version: int, store: StoreDep) -> AssistantConfig:
    cfg = await store.get_assistant_version(assistant_id, version)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version not found")
    return cfg


@router.post("/assistants/{assistant_id}/rollback/{version}", response_model=AssistantConfig)
async def rollback_version(assistant_id: str, version: int, store: StoreDep) -> AssistantConfig:
    """Re-publish an earlier version as a new version (history is never rewritten)."""
    cfg = await store.get_assistant_version(assistant_id, version)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version not found")
    await store.upsert_assistant(cfg, [])
    return cfg


@router.get("/assistants/{assistant_id}/faqs/suggest", response_model=list[Faq])
async def suggest_assistant_faqs(assistant_id: str, store: StoreDep) -> list[Faq]:
    cfg = await store.get_assistant(assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    calls = await store.list_calls(cfg.tenant_id, limit=200)
    return suggest_faqs(calls, cfg.faqs)


@router.post("/assistants/{assistant_id}/draft", response_model=Draft)
async def draft_field(
    assistant_id: str, body: DraftRequest, store: StoreDep, drafter: DrafterDep
) -> Draft:
    """Ask AI to draft: brief (+ optional website) -> optimised wording for a Studio field."""
    cfg = await store.get_assistant(assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    return await drafter.draft(body, cfg)


class VoiceCatalogue(BaseModel):
    provider: TTSProvider
    market: str
    accents: list[str]
    voices: list[Voice]
    preview_available: bool


@router.get("/voices", response_model=VoiceCatalogue)
async def list_voices(
    previewer: VoicePreviewDep, admin: AdminDep, user: UserDep, tenant_id: str | None = None
) -> VoiceCatalogue:
    """Voices for the tenant's platform-assigned TTS provider, tagged by accent; `accents` are
    the ones to lead with for the tenant's market (UK -> British/Irish, US -> American...)."""
    tenants = user.tenant_ids
    tid = tenant_id or (tenants[0] if tenants else None)
    if tid is not None:
        user.require_tenant(tid)
    settings = await admin.voice_settings()
    market = (await admin.locale(tid)).market if tid else settings.default_market
    provider = settings.provider_for(market)
    return VoiceCatalogue(
        provider=provider,
        market=market,
        accents=MARKETS.get(market, MARKETS["GB"]).accents,
        voices=[v for v in CATALOGUE if v.provider == provider],
        preview_available=previewer.available(provider),
    )


@router.post("/voices/preview", response_model=Preview)
async def preview_voice(body: PreviewRequest, previewer: VoicePreviewDep) -> Preview:
    """Synthesise a short sample so the user can hear a voice before saving."""
    try:
        return await previewer.preview(body)
    except PreviewUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e


@router.get("/assistants/{assistant_id}/required-fields", response_model=list[RequiredField])
async def get_required_fields(assistant_id: str, store: StoreDep) -> list[RequiredField]:
    return await store.required_fields(assistant_id)


@router.put("/assistants/{assistant_id}/required-fields", response_model=list[RequiredField])
async def put_required_fields(
    assistant_id: str, fields: list[RequiredField], store: StoreDep
) -> list[RequiredField]:
    if await store.get_assistant(assistant_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    await store.set_required_fields(assistant_id, fields)
    return fields


@router.get("/calls", response_model=list[CallRecord])
async def list_calls(
    store: StoreDep,
    tenant_id: str | None = None,
    limit: int = 50,
    kind: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    hour: Annotated[int | None, Query(ge=0, le=23)] = None,
    q: str | None = None,
) -> list[CallRecord]:
    if kind is None and since is None and until is None and hour is None and q is None:
        return await store.list_calls(tenant_id, limit)
    return await store.filter_calls(
        CallFilter(
            tenant_id=tenant_id, kind=kind, since=since, until=until, hour=hour, q=q, limit=limit
        )
    )


@router.get("/calls/{call_id}", response_model=CallRecord)
async def get_call(call_id: str, store: StoreDep) -> CallRecord:
    call = await store.get_call(call_id)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")
    return call


@router.post("/calls/{call_id}/read", response_model=CallRecord)
async def mark_read(call_id: str, store: StoreDep, read: bool = True) -> CallRecord:
    call = await store.mark_call_read(call_id, read)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")
    return call


@router.post("/calls/{call_id}/feedback", response_model=CallRecord)
async def call_feedback(
    call_id: str, body: CallFeedback, store: StoreDep, user: UserDep
) -> CallRecord:
    body.actor = body.actor or user.email
    call = await store.add_call_feedback(call_id, body)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")
    return call


@router.get("/calls/{call_id}/recordings/{index}")
async def call_recording(
    call_id: str,
    index: int,
    request: Request,
    store: StoreDep,
    user: UserDep,
    range_header: Annotated[str | None, Header(alias="range")] = None,
) -> Response:
    """Stream one leg of the call recording (index into `CallRecord.recordings`)."""
    call = await store.get_call(call_id)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")
    user.require_tenant(call.tenant_id)
    if not 0 <= index < len(call.recordings):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such recording")
    storage: RecordingStorage | None = request.app.state.recordings
    if storage is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "recording storage not configured")
    key = call.recordings[index]
    upstream = await storage.fetch(key, range_header)
    if upstream.status_code == 404:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "recording not available yet")
    if upstream.status_code >= 400:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "recording storage error")
    headers = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() in {"content-length", "content-range", "accept-ranges", "etag"}
    }
    headers["content-disposition"] = f'inline; filename="{call_id}-{key.rsplit("/", 1)[-1]}"'
    return Response(
        upstream.content,
        status_code=upstream.status_code,
        media_type=content_type_for(key),
        headers=headers,
    )


@router.post("/calls/{call_id}/share", response_model=ShareLink)
async def share_call(call_id: str, store: StoreDep, settings: SettingsDep) -> ShareLink:
    token = await store.ensure_share_token(call_id)
    if token is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")
    return ShareLink(token=token, url=f"{settings.dashboard_url.rstrip('/')}/share/{token}")


# -- analytics ---------------------------------------------------------------------------------


@router.get("/analytics/insights", response_model=InsightsReport)
async def insights_analytics(
    store: StoreDep,
    value: ValueDep,
    user: UserDep,
    tenant_id: str | None = None,
    days: Annotated[int, Query(ge=7, le=730)] = 30,
    timezone: str = "Europe/London",
) -> InsightsReport:
    """Phase 21a deep analytics read model (demand, resolution, SLA, transfers, revenue, CX…)."""
    tid = tenant_id or (user.tenant_ids[0] if user.tenant_ids else None)
    if tid is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no organisation")
    tickets = await store.list_tickets(tid, limit=1000)
    events = {t.id: await store.ticket_events(t.id) for t in tickets}
    assistants = await store.list_assistants(tid)
    inp = InsightInputs(
        tenant_id=tid,
        calls=await store.filter_calls(CallFilter(tenant_id=tid, limit=20000)),
        contacts=await store.list_contacts(tid, limit=5000),
        tickets=tickets,
        ticket_events=events,
        transfers=await store.list_transfers(tid, limit=5000),
        members=await store.list_members(tid),
        bookings=await store.list_docs("booking", tid, limit=5000),
        outbound=await store.list_docs("outbound_call", tid, limit=5000),
        qa_scores=await store.list_docs("qa_score", tid, limit=5000),
        faq_insights=await store.list_docs("insight", tid, limit=500),
        tracking_numbers=await value.tracking_numbers(tid),
        value=await value.settings(tid),
        schedule=assistants[0].hours if assistants else None,
    )
    return build_insights(inp, days=days, timezone=timezone, now=datetime.now(UTC))


@router.get("/analytics/overview", response_model=OverviewAnalytics)
async def overview_analytics(
    store: StoreDep,
    tenant_id: str | None = None,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    timezone: str = "Europe/London",
) -> OverviewAnalytics:
    calls = await store.filter_calls(CallFilter(tenant_id=tenant_id, limit=5000))
    contacts = await store.list_contacts(tenant_id, limit=5000)
    out = compute_overview(
        calls,
        contacts,
        await store.transfer_stats(tenant_id),
        await store.ticket_stats(tenant_id),
        days=days,
        timezone=timezone,
        now=datetime.now(UTC),
    )
    month_start = datetime.fromisoformat(out.usage.month + "-01").replace(tzinfo=ZoneInfo(timezone))
    channel_usage(
        out.usage,
        messages=await store.list_docs("message", tenant_id, limit=5000),
        inbox_messages=await store.list_docs("inbox_message", tenant_id, limit=5000),
        notifications=await store.list_docs("notification", tenant_id, limit=5000),
        bookings=await store.list_docs("booking", tenant_id, limit=5000),
        month_start=month_start,
    )
    return out


class AnalyticsQuery(BaseModel):
    """Either a free-text `question` (Ask AI) or explicit segments."""

    question: str | None = None
    period: Segment | None = None
    compare: Segment | None = None
    tenant_id: str | None = None
    timezone: str = "Europe/London"


@router.post("/analytics/query", response_model=ComparisonAnalytics)
async def query_analytics(
    body: AnalyticsQuery, store: StoreDep, settings: SettingsDep, billing: BillingDep
) -> ComparisonAnalytics:
    today = datetime.now(ZoneInfo(body.timezone)).date()
    if body.question and body.tenant_id and not await billing.entitled(body.tenant_id, "ask_ai"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Ask AI is not included in your plan")
    if body.period is not None:
        q = Question(period=body.period, compare=body.compare)
        q.interpretation = q.period.describe() + (
            f" vs {q.compare.describe()}" if q.compare else ""
        )
    elif body.question:
        if (key := settings.llm_key) is not None:
            parser = LlmQuestionParser(key, model=settings.openai_model)
            q = await parser.parse(body.question, today)
        else:
            q = parse_question(body.question, today)
    else:
        raise HTTPException(422, "question or period required")

    calls = await store.filter_calls(CallFilter(tenant_id=body.tenant_id, limit=5000))
    contacts = await store.list_contacts(body.tenant_id, limit=5000)
    assistants = await store.list_assistants(body.tenant_id)
    schedule = assistants[0].hours if assistants else None
    return compare(calls, contacts, q, schedule=schedule, timezone=body.timezone)


# -- transfers ---------------------------------------------------------------------------------


@router.get("/assistants/{assistant_id}/transfer", response_model=TransferConfig)
async def get_transfer_config(assistant_id: str, store: StoreDep) -> TransferConfig:
    cfg = await store.get_assistant(assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    return cfg.transfer


@router.put("/assistants/{assistant_id}/transfer", response_model=TransferConfig)
async def put_transfer_config(
    assistant_id: str, body: TransferConfig, store: StoreDep, billing: BillingDep
) -> TransferConfig:
    """Destinations, departments, schedules, urgent keywords, after-hours behaviour, SLAs."""
    cfg = await store.get_assistant(assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    ent = await billing.entitlements(cfg.tenant_id)
    if body.mode == TransferMode.WARM and not ent["warm_transfers"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Warm transfers are not in your plan")
    if body.departments() and not ent["departments"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Departments are not in your plan")
    ids = [d.id for d in body.destinations]
    if len(ids) != len(set(ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "duplicate destination id")
    for d in body.destinations:
        if d.fallback_id and d.fallback_id not in ids:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown fallback {d.fallback_id}")
    await store.upsert_assistant(cfg.model_copy(update={"transfer": body}), [])
    return body


@router.get("/assistants/{assistant_id}/destinations", response_model=list[Destination])
async def list_destinations(assistant_id: str, store: StoreDep) -> list[Destination]:
    cfg = await store.get_assistant(assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    return cfg.transfer.destinations


class DestinationAvailability(BaseModel):
    destination: Destination
    available_now: bool


@router.get("/assistants/{assistant_id}/availability", response_model=list[DestinationAvailability])
async def destination_availability(
    assistant_id: str, store: StoreDep
) -> list[DestinationAvailability]:
    cfg = await store.get_assistant(assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    return [
        DestinationAvailability(destination=d, available_now=d.is_available())
        for d in cfg.transfer.destinations
    ]


@router.get("/transfers", response_model=list[TransferRecord])
async def list_transfers(
    store: StoreDep, tenant_id: str | None = None, limit: int = 100
) -> list[TransferRecord]:
    return await store.list_transfers(tenant_id, limit)


# -- tickets -----------------------------------------------------------------------------------


class TicketCreate(BaseModel):
    tenant_id: str
    company_id: str
    intake: TicketIntake


class TicketDetail(BaseModel):
    ticket: Ticket
    events: list[TicketEvent]
    sla_remaining_s: float | None


@router.get("/tickets", response_model=list[Ticket])
async def list_tickets(
    store: StoreDep,
    tenant_id: str | None = None,
    status_: Annotated[TicketStatus | None, Query(alias="status")] = None,
    limit: int = 100,
) -> list[Ticket]:
    return await store.list_tickets(tenant_id, status_, limit)


@router.post("/tickets", response_model=Ticket, status_code=status.HTTP_201_CREATED)
async def create_ticket_manual(body: TicketCreate, tickets: TicketsDep) -> Ticket:
    intake = body.intake.model_copy(update={"source": "manual"})
    return await tickets.create_from_intake(body.tenant_id, body.company_id, intake)


@router.get("/tickets/{ticket_id}", response_model=TicketDetail)
async def get_ticket(ticket_id: str, store: StoreDep) -> TicketDetail:
    t = await store.get_ticket(ticket_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return TicketDetail(
        ticket=t, events=await store.ticket_events(ticket_id), sla_remaining_s=t.sla_remaining_s
    )


@router.patch("/tickets/{ticket_id}", response_model=Ticket)
async def update_ticket(ticket_id: str, upd: TicketUpdate, tickets: TicketsDep) -> Ticket:
    t = await tickets.update(ticket_id, upd)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return t


class Actor(BaseModel):
    actor: str
    note: str | None = None


@router.post("/tickets/{ticket_id}/claim", response_model=Ticket)
async def claim_ticket(ticket_id: str, body: Actor, tickets: TicketsDep) -> Ticket:
    t = await tickets.update(
        ticket_id,
        TicketUpdate(status=TicketStatus.CLAIMED, assigned_to=body.actor, actor=body.actor),
    )
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return t


@router.post("/tickets/{ticket_id}/resolve", response_model=Ticket)
async def resolve_ticket(ticket_id: str, body: Actor, tickets: TicketsDep) -> Ticket:
    t = await tickets.update(
        ticket_id, TicketUpdate(status=TicketStatus.RESOLVED, actor=body.actor, note=body.note)
    )
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return t


@router.post("/tickets/{ticket_id}/reopen", response_model=Ticket)
async def reopen_ticket(ticket_id: str, body: Actor, tickets: TicketsDep) -> Ticket:
    t = await tickets.update(
        ticket_id, TicketUpdate(status=TicketStatus.OPEN, actor=body.actor, note=body.note)
    )
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return t


@router.post("/tickets/{ticket_id}/notes", response_model=list[TicketEvent])
async def add_ticket_note(ticket_id: str, body: Actor, store: StoreDep) -> list[TicketEvent]:
    if not body.note:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "note required")
    t = await store.update_ticket(ticket_id, TicketUpdate(actor=body.actor, note=body.note))
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return await store.ticket_events(ticket_id)


class ClickToCall(BaseModel):
    tel_uri: str
    ticket_id: str


@router.post("/tickets/{ticket_id}/callback", response_model=ClickToCall)
async def request_callback(
    ticket_id: str, body: Actor, store: StoreDep, tickets: TicketsDep
) -> ClickToCall:
    """Click-to-call: records the callback attempt and returns a tel: link for the agent's
    softphone. Automatic bridge-dialling via LiveKit SIP lands in the second Phase 3 session."""
    t = await store.get_ticket(ticket_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    if not t.caller_number:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ticket has no callback number")
    await store.add_ticket_event(
        TicketEvent(ticket_id=ticket_id, type="callback", actor=body.actor, note=t.caller_number)
    )
    if t.status == TicketStatus.OPEN:
        await tickets.update(ticket_id, TicketUpdate(status=TicketStatus.CLAIMED, actor=body.actor))
    return ClickToCall(tel_uri=f"tel:{t.caller_number}", ticket_id=ticket_id)


# -- analytics ---------------------------------------------------------------------------------


class HandoffAnalytics(BaseModel):
    transfers: TransferStats
    tickets: TicketStats


@router.get("/analytics/handoff", response_model=HandoffAnalytics)
async def handoff_analytics(store: StoreDep, tenant_id: str | None = None) -> HandoffAnalytics:
    return HandoffAnalytics(
        transfers=await store.transfer_stats(tenant_id), tickets=await store.ticket_stats(tenant_id)
    )


@router.post("/worker-keys", response_model=WorkerKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_worker_key(body: WorkerKeyCreate, store: StoreDep) -> WorkerKeyCreated:
    return WorkerKeyCreated(key=await store.create_worker_key(body.tenant_id, body.name))
