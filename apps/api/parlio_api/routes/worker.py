"""Endpoints used by voice workers (authenticated with X-Worker-Key)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from parlio_api.deps import PostCallDep, StoreDep, TicketsDep, require_worker_key
from parlio_api.store import Ticket
from parlio_voice.models import AssistantConfig, CallEvent, CallEventType, TicketIntake

router = APIRouter(prefix="/v1/worker", tags=["worker"], dependencies=[Depends(require_worker_key)])


@router.get("/assistants/resolve", response_model=AssistantConfig)
async def resolve_assistant(store: StoreDep, number: str = Query(min_length=3)) -> AssistantConfig:
    cfg = await store.resolve_number(number)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no assistant for number {number}")
    return cfg


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_event(
    ev: CallEvent, store: StoreDep, postcall: PostCallDep, tickets: TicketsDep
) -> dict[str, bool]:
    applied = await store.apply_event(ev)
    if applied and ev.type == CallEventType.CALL_ENDED:
        postcall.enqueue(ev.call_id)
    if applied and ev.type == CallEventType.TICKET_CREATED:
        await tickets.rebuild_from_event(ev)
    return {"applied": applied}


@router.post("/tickets", response_model=Ticket, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    intake: TicketIntake, tickets: TicketsDep, tenant_id: str, company_id: str
) -> Ticket:
    """AI intake from the voice worker; classification, SLA and alerts happen here."""
    return await tickets.create_from_intake(tenant_id, company_id, intake)
