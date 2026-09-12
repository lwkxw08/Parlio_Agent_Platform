"""Endpoints used by voice workers (authenticated with X-Worker-Key)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from parlio_api.deps import StoreDep, require_worker_key
from parlio_voice.models import AssistantConfig, CallEvent

router = APIRouter(prefix="/v1/worker", tags=["worker"], dependencies=[Depends(require_worker_key)])


@router.get("/assistants/resolve", response_model=AssistantConfig)
async def resolve_assistant(store: StoreDep, number: str = Query(min_length=3)) -> AssistantConfig:
    cfg = await store.resolve_number(number)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no assistant for number {number}")
    return cfg


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_event(ev: CallEvent, store: StoreDep) -> dict[str, bool]:
    applied = await store.apply_event(ev)
    return {"applied": applied}
