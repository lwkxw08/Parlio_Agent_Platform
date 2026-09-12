"""Dashboard-facing endpoints. Tenant auth (Supabase JWT -> tenant_id) lands in Phase 3; until
then `tenant_id` is an explicit query parameter and is applied via RLS on the Postgres store."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from parlio_api.deps import StoreDep, TicketsDep
from parlio_api.store import (
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
from parlio_voice.models import AssistantConfig, Destination, TicketIntake, TransferConfig

router = APIRouter(prefix="/v1", tags=["dashboard"])


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


@router.put("/assistants/{assistant_id}", response_model=AssistantConfig)
async def upsert_assistant(
    assistant_id: str, body: AssistantUpsert, store: StoreDep
) -> AssistantConfig:
    if body.config.assistant_id != assistant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "assistant_id mismatch")
    await store.upsert_assistant(body.config, body.numbers)
    return body.config


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
    store: StoreDep, tenant_id: str | None = None, limit: int = 50
) -> list[CallRecord]:
    return await store.list_calls(tenant_id, limit)


@router.get("/calls/{call_id}", response_model=CallRecord)
async def get_call(call_id: str, store: StoreDep) -> CallRecord:
    call = await store.get_call(call_id)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")
    return call


# -- transfers ---------------------------------------------------------------------------------


@router.get("/assistants/{assistant_id}/transfer", response_model=TransferConfig)
async def get_transfer_config(assistant_id: str, store: StoreDep) -> TransferConfig:
    cfg = await store.get_assistant(assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    return cfg.transfer


@router.put("/assistants/{assistant_id}/transfer", response_model=TransferConfig)
async def put_transfer_config(
    assistant_id: str, body: TransferConfig, store: StoreDep
) -> TransferConfig:
    """Destinations, departments, schedules, urgent keywords, after-hours behaviour, SLAs."""
    cfg = await store.get_assistant(assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
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
async def update_ticket(ticket_id: str, upd: TicketUpdate, store: StoreDep) -> Ticket:
    t = await store.update_ticket(ticket_id, upd)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return t


class Actor(BaseModel):
    actor: str
    note: str | None = None


@router.post("/tickets/{ticket_id}/claim", response_model=Ticket)
async def claim_ticket(ticket_id: str, body: Actor, store: StoreDep) -> Ticket:
    t = await store.update_ticket(
        ticket_id,
        TicketUpdate(status=TicketStatus.CLAIMED, assigned_to=body.actor, actor=body.actor),
    )
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return t


@router.post("/tickets/{ticket_id}/resolve", response_model=Ticket)
async def resolve_ticket(ticket_id: str, body: Actor, store: StoreDep) -> Ticket:
    t = await store.update_ticket(
        ticket_id, TicketUpdate(status=TicketStatus.RESOLVED, actor=body.actor, note=body.note)
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
async def request_callback(ticket_id: str, body: Actor, store: StoreDep) -> ClickToCall:
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
        await store.update_ticket(
            ticket_id, TicketUpdate(status=TicketStatus.CLAIMED, actor=body.actor)
        )
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
