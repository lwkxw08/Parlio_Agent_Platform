"""Dashboard-facing endpoints. Tenant auth (Supabase JWT -> tenant_id) lands in Phase 3; until
then `tenant_id` is an explicit query parameter and is applied via RLS on the Postgres store."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from parlio_api.deps import StoreDep
from parlio_api.store import CallRecord, RequiredField
from parlio_voice.models import AssistantConfig

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


@router.post("/worker-keys", response_model=WorkerKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_worker_key(body: WorkerKeyCreate, store: StoreDep) -> WorkerKeyCreated:
    return WorkerKeyCreated(key=await store.create_worker_key(body.tenant_id, body.name))
