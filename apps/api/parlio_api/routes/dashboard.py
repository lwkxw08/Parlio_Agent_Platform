"""Dashboard-facing endpoints. Tenant auth (Supabase JWT) lands in Phase 2."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from parlio_api.deps import StoreDep
from parlio_api.store import CallRecord
from parlio_voice.models import AssistantConfig

router = APIRouter(prefix="/v1", tags=["dashboard"])


class AssistantUpsert(BaseModel):
    config: AssistantConfig
    numbers: list[str] = Field(
        default_factory=list, description="E.164 numbers routed to this assistant"
    )


@router.get("/assistants", response_model=list[AssistantConfig])
async def list_assistants(store: StoreDep) -> list[AssistantConfig]:
    return store.list_assistants()


@router.put("/assistants/{assistant_id}", response_model=AssistantConfig)
async def upsert_assistant(
    assistant_id: str, body: AssistantUpsert, store: StoreDep
) -> AssistantConfig:
    if body.config.assistant_id != assistant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "assistant_id mismatch")
    await store.upsert_assistant(body.config, body.numbers)
    return body.config


@router.get("/calls", response_model=list[CallRecord])
async def list_calls(
    store: StoreDep, tenant_id: str | None = None, limit: int = 50
) -> list[CallRecord]:
    return store.list_calls(tenant_id, limit)


@router.get("/calls/{call_id}", response_model=CallRecord)
async def get_call(call_id: str, store: StoreDep) -> CallRecord:
    call = store.get_call(call_id)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")
    return call
