"""Phase 9: outbound calling & speed-to-lead.

* ``/v1/outbound/*``        dashboard: leads, queue/history, policy, suppression, manual scheduling.
* ``/v1/public/leads/{id}`` unauthenticated web-form endpoint (per-tenant form token) so a website
                            form can post a lead and the AI calls within the speed-to-lead target.
* ``/v1/inbound/leads``     customer API (inbound API key) for CRMs/Zapier to push leads.
* ``/v1/worker/outbound/*`` voice worker: fetch job context, record the outcome mid-call.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from parlio_api.auth import UserDep
from parlio_api.deps import (
    ApiKeyDep,
    AuditDep,
    OutboundDep,
    StoreDep,
    require_feature,
    require_worker_key,
)
from parlio_api.observability import AuditEntry
from parlio_api.outbound import (
    JURISDICTIONS,
    Lead,
    OutboundCall,
    OutboundPolicy,
    OutboundStatus,
    Outcome,
    Purpose,
    Suppression,
    normalise_phone,
)
from parlio_api.store import CallStore

router = APIRouter(prefix="/v1/outbound", tags=["outbound"])
public = APIRouter(prefix="/v1/public", tags=["public"])
inbound = APIRouter(prefix="/v1/inbound", tags=["inbound"])
worker = APIRouter(
    prefix="/v1/worker/outbound", tags=["worker"], dependencies=[Depends(require_worker_key)]
)


async def _company(store: CallStore, tenant_id: str) -> str:
    cfgs = await store.list_assistants(tenant_id)
    return cfgs[0].company_id if cfgs else f"{tenant_id}-main"


def _audit(request: Request, user: UserDep, tenant_id: str, action: str, target: str) -> AuditEntry:
    return AuditEntry(
        tenant_id=tenant_id,
        actor=user.email,
        action=action,
        target=target,
        method=request.method,
        path=request.url.path,
        ip=request.client.host if request.client else None,
    )


class LeadInput(BaseModel):
    name: str = ""
    phone: str = Field(min_length=6)
    email: str | None = None
    interest: str | None = None
    notes: str | None = None
    source: str = "web_form"
    consent: bool = True
    consent_text: str | None = None
    custom: dict[str, str] = Field(default_factory=dict)
    call_now: bool = True


class ScheduleInput(BaseModel):
    assistant_id: str | None = None
    purpose: Purpose
    to: str
    name: str | None = None
    when: datetime | None = None
    ticket_id: str | None = None
    context: dict[str, str] = Field(default_factory=dict)
    # Ticket callbacks must carry something for the customer: an update to relay, a person
    # who is ready to take the call, or a booking to make.
    resolution_kind: Literal["answer", "transfer", "booking"] | None = None
    resolution: str | None = None
    transfer_to: str | None = None


class SuppressInput(BaseModel):
    phone: str
    reason: str = "opt_out"


class OutcomeInput(BaseModel):
    outcome: Outcome
    detail: str | None = None


# -- dashboard ---------------------------------------------------------------------------------


@router.get("/summary")
async def summary(user: UserDep, svc: OutboundDep, tenant_id: str) -> dict[str, Any]:
    user.require_tenant(tenant_id)
    return await svc.stats(tenant_id)


@router.get("/jurisdictions")
async def jurisdictions(user: UserDep) -> dict[str, dict[str, Any]]:
    return JURISDICTIONS


@router.get("/policy", response_model=OutboundPolicy)
async def get_policy(user: UserDep, svc: OutboundDep, tenant_id: str) -> OutboundPolicy:
    user.require_tenant(tenant_id)
    return await svc.policy(tenant_id)


@router.put("/policy", response_model=OutboundPolicy)
async def put_policy(
    request: Request,
    user: UserDep,
    svc: OutboundDep,
    audit: AuditDep,
    tenant_id: str,
    body: OutboundPolicy,
) -> OutboundPolicy:
    user.require_admin(tenant_id)
    if body.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "tenant mismatch")
    if body.window_start >= body.window_end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "calling window start must precede end")
    if not body.days:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "at least one calling day required")
    if body.jurisdiction not in JURISDICTIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown jurisdiction")
    if body.caller_id:
        body.caller_id = normalise_phone(body.caller_id)
    await audit.record(_audit(request, user, tenant_id, "outbound.policy", tenant_id))
    return await svc.save_policy(body)


@router.get("/leads", response_model=list[Lead])
async def list_leads(
    user: UserDep, svc: OutboundDep, tenant_id: str, limit: int = 200
) -> list[Lead]:
    user.require_tenant(tenant_id)
    return await svc.leads(tenant_id, limit)


@router.post(
    "/leads",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature("outbound"))],
)
async def create_lead(
    request: Request,
    user: UserDep,
    svc: OutboundDep,
    store: StoreDep,
    audit: AuditDep,
    tenant_id: str,
    body: LeadInput,
) -> dict[str, Any]:
    user.require_tenant(tenant_id)
    lead, job = await svc.capture_lead(
        Lead(
            tenant_id=tenant_id,
            company_id=await _company(store, tenant_id),
            source="dashboard",
            **body.model_dump(exclude={"call_now", "source"}),
        ),
        call_now=body.call_now,
    )
    await audit.record(_audit(request, user, tenant_id, "outbound.lead", lead.id))
    return {
        "lead": lead.model_dump(mode="json"),
        "call": job.model_dump(mode="json") if job else None,
    }


@router.get("/calls", response_model=list[OutboundCall])
async def list_calls(
    user: UserDep,
    svc: OutboundDep,
    tenant_id: str,
    status_filter: OutboundStatus | None = None,
    limit: int = 200,
) -> list[OutboundCall]:
    user.require_tenant(tenant_id)
    jobs = await svc.list_calls(tenant_id, limit)
    return [j for j in jobs if status_filter is None or j.status == status_filter]


@router.post(
    "/calls",
    response_model=OutboundCall,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature("outbound"))],
)
async def schedule_call(
    request: Request,
    user: UserDep,
    svc: OutboundDep,
    store: StoreDep,
    audit: AuditDep,
    tenant_id: str,
    body: ScheduleInput,
) -> OutboundCall:
    user.require_tenant(tenant_id)
    aid = body.assistant_id
    if aid is None:
        cfgs = await store.list_assistants(tenant_id)
        if not cfgs:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "no assistant configured")
        aid = cfgs[0].assistant_id
    cfg = await store.get_assistant(aid)
    if cfg is None or cfg.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    ctx = {k: v for k, v in body.context.items() if v}
    if body.purpose == Purpose.TICKET_CALLBACK and body.ticket_id:
        kind = body.resolution_kind or "answer"
        if kind == "answer" and not (body.resolution or "").strip():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "say what the assistant should tell the customer, or choose transfer/booking",
            )
        if kind == "transfer" and not (body.transfer_to or "").strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "say who will take the call")
        ctx["resolution_kind"] = kind
        if body.resolution:
            ctx["resolution"] = body.resolution.strip()
        if body.transfer_to:
            ctx["transfer_to"] = body.transfer_to.strip()
    job = await svc.schedule(
        tenant_id=tenant_id,
        assistant_id=aid,
        purpose=body.purpose,
        to=body.to,
        name=body.name,
        when=body.when,
        ticket_id=body.ticket_id,
        context=ctx,
    )
    await audit.record(_audit(request, user, tenant_id, "outbound.schedule", job.id))
    return job


@router.post("/calls/{job_id}/cancel", response_model=OutboundCall)
async def cancel_call(
    request: Request,
    user: UserDep,
    svc: OutboundDep,
    audit: AuditDep,
    tenant_id: str,
    job_id: str,
) -> OutboundCall:
    user.require_tenant(tenant_id)
    job = await svc.cancel(tenant_id, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    await audit.record(_audit(request, user, tenant_id, "outbound.cancel", job_id))
    return job


@router.post("/calls/{job_id}/dial-now", response_model=OutboundCall)
async def dial_now(
    request: Request,
    user: UserDep,
    svc: OutboundDep,
    audit: AuditDep,
    tenant_id: str,
    job_id: str,
) -> OutboundCall:
    """Dial immediately: overrides the calling window and daily cap, not do-not-call."""
    user.require_tenant(tenant_id)
    job = await svc.get(tenant_id, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    if job.status not in (OutboundStatus.SCHEDULED, OutboundStatus.RETRY):
        raise HTTPException(status.HTTP_409_CONFLICT, f"job is {job.status}")
    await audit.record(_audit(request, user, tenant_id, "outbound.dial_now", job_id))
    return await svc.dispatch(job, manual=True)


@router.get("/suppressions", response_model=list[Suppression])
async def list_suppressions(user: UserDep, svc: OutboundDep, tenant_id: str) -> list[Suppression]:
    user.require_tenant(tenant_id)
    return await svc.suppressions(tenant_id)


@router.post("/suppressions", response_model=Suppression, status_code=status.HTTP_201_CREATED)
async def add_suppression(
    request: Request,
    user: UserDep,
    svc: OutboundDep,
    audit: AuditDep,
    tenant_id: str,
    body: SuppressInput,
) -> Suppression:
    user.require_tenant(tenant_id)
    s = await svc.suppress(tenant_id, body.phone, body.reason, source="dashboard")
    await audit.record(_audit(request, user, tenant_id, "outbound.suppress", s.phone))
    return s


@router.delete("/suppressions", status_code=status.HTTP_204_NO_CONTENT)
async def remove_suppression(
    request: Request,
    user: UserDep,
    svc: OutboundDep,
    audit: AuditDep,
    tenant_id: str,
    phone: str,
) -> None:
    user.require_admin(tenant_id)
    if not await svc.unsuppress(tenant_id, phone):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not suppressed")
    await audit.record(_audit(request, user, tenant_id, "outbound.unsuppress", phone))


# -- lead intake ---------------------------------------------------------------------------------


class PublicLead(LeadInput):
    token: str = Field(description="Tenant form token (Outbound page)")


@public.post("/leads/{tenant_id}", status_code=status.HTTP_201_CREATED)
async def public_lead(
    tenant_id: str, body: PublicLead, svc: OutboundDep, store: StoreDep
) -> dict[str, Any]:
    """Web form target: ``<form action=.../v1/public/leads/{tenant}>`` with the form token."""
    policy = await svc.policy(tenant_id)
    if not policy.form_token or not hmac.compare_digest(policy.form_token, body.token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid form token")
    lead, job = await svc.capture_lead(
        Lead(
            tenant_id=tenant_id,
            company_id=await _company(store, tenant_id),
            **body.model_dump(exclude={"call_now", "token"}),
        ),
        call_now=body.call_now,
    )
    return {
        "lead_id": lead.id,
        "call_scheduled": job is not None and job.status == OutboundStatus.SCHEDULED,
    }


@inbound.post("/leads", status_code=status.HTTP_201_CREATED)
async def inbound_lead(
    key: ApiKeyDep, svc: OutboundDep, store: StoreDep, body: LeadInput
) -> dict[str, Any]:
    lead, job = await svc.capture_lead(
        Lead(
            tenant_id=key.tenant_id,
            company_id=await _company(store, key.tenant_id),
            **body.model_dump(exclude={"call_now", "source"}),
            source=body.source if body.source != "web_form" else "api",
        ),
        call_now=body.call_now,
    )
    return {
        "lead": lead.model_dump(mode="json"),
        "call": job.model_dump(mode="json") if job else None,
    }


# -- worker --------------------------------------------------------------------------------------


@worker.get("/{job_id}", response_model=OutboundCall)
async def worker_job(job_id: str, svc: OutboundDep, store: StoreDep) -> OutboundCall:
    d = await store.get_doc("outbound_call", job_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return OutboundCall.model_validate(d.data)


@worker.post("/{job_id}/outcome", response_model=OutboundCall)
async def worker_outcome(
    job_id: str, body: OutcomeInput, svc: OutboundDep, store: StoreDep
) -> OutboundCall:
    """Mid-call outcome from the assistant's record_outcome tool; call.ended finalises retries."""
    d = await store.get_doc("outbound_call", job_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    job = OutboundCall.model_validate(d.data)
    job.outcome, job.outcome_detail = body.outcome, body.detail
    if job.attempts:
        job.attempts[-1].outcome, job.attempts[-1].detail = body.outcome, body.detail
    await store.put_doc(job.to_doc())
    if body.outcome == Outcome.OPT_OUT:
        await svc.suppress(job.tenant_id, job.to, "opt_out", source="call")
    return job
