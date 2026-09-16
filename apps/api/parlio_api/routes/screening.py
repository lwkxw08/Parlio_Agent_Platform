"""Phase 20d: call screening & spam filtering endpoints.

The worker asks for a verdict at call start; the dashboard manages the tenant's spam list (and
platform staff the shared one).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from parlio_api.auth import UserDep
from parlio_api.contacts import CallerContext, caller_context_instruction
from parlio_api.deps import ContactsDep, ScreeningDep, StoreDep, require_worker_key
from parlio_api.screening import PLATFORM_TENANT, ScreeningVerdict, SpamNumber

router = APIRouter(prefix="/v1/screening", tags=["screening"])
worker = APIRouter(prefix="/v1/worker", tags=["worker"], dependencies=[Depends(require_worker_key)])


@worker.get("/screening", response_model=ScreeningVerdict)
async def worker_screen(
    screening: ScreeningDep, store: StoreDep, assistant_id: str, caller: str | None = None
) -> ScreeningVerdict:
    cfg = await store.get_assistant(assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    return await screening.assess(cfg, caller)


class CallerContextOut(CallerContext):
    instruction: str = ""


@worker.get("/caller-context", response_model=CallerContextOut)
async def worker_caller_context(
    contacts: ContactsDep, store: StoreDep, assistant_id: str, caller: str | None = None
) -> CallerContextOut:
    """Who is calling (Phase 21e): name, status, VIP, history and the tenant's handling rules."""
    cfg = await store.get_assistant(assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    ctx = await contacts.caller_context(cfg.tenant_id, caller)
    return CallerContextOut(**ctx.model_dump(), instruction=caller_context_instruction(ctx) or "")


class SpamReport(BaseModel):
    e164: str = Field(min_length=3)
    reason: str = "reported"
    call_id: str | None = None


@router.get("/spam", response_model=list[SpamNumber])
async def list_spam(user: UserDep, screening: ScreeningDep, tenant_id: str) -> list[SpamNumber]:
    user.require_tenant(tenant_id)
    return await screening.spam_list(tenant_id)


@router.post("/spam", response_model=SpamNumber, status_code=status.HTTP_201_CREATED)
async def report_spam(
    user: UserDep, screening: ScreeningDep, tenant_id: str, req: SpamReport
) -> SpamNumber:
    user.require_tenant(tenant_id)
    try:
        return await screening.report_spam(
            tenant_id, req.e164, reason=req.reason, by=user.email, call_id=req.call_id
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e


@router.delete("/spam", status_code=status.HTTP_204_NO_CONTENT)
async def forgive_spam(user: UserDep, screening: ScreeningDep, tenant_id: str, e164: str) -> None:
    user.require_tenant(tenant_id)
    if not await screening.forgive(tenant_id, e164):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "number not on the spam list")


@router.get("/platform-spam", response_model=list[SpamNumber])
async def list_platform_spam(user: UserDep, screening: ScreeningDep) -> list[SpamNumber]:
    user.require_staff()
    return await screening.spam_list(PLATFORM_TENANT)


@router.post("/platform-spam", response_model=SpamNumber, status_code=status.HTTP_201_CREATED)
async def report_platform_spam(
    user: UserDep, screening: ScreeningDep, req: SpamReport
) -> SpamNumber:
    user.require_staff("owner", "support")
    try:
        return await screening.report_spam(
            PLATFORM_TENANT, req.e164, reason=req.reason, by=user.email, call_id=req.call_id
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
