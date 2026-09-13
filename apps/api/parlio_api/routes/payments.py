"""Phase 12 routes: payment links (worker + dashboard + provider webhook) and caller verification.

Verification answers arrive in request bodies only, are hashed in memory and are never logged or
persisted; responses carry outcomes, not values.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from parlio_api.auth import UserDep
from parlio_api.deps import AuditDep, PaymentsDep, StoreDep, require_worker_key
from parlio_api.observability import AuditEntry
from parlio_api.payments import (
    PaymentError,
    PaymentRequest,
    VerificationAttempt,
    VerificationResult,
)
from parlio_voice.models import VerificationField

router = APIRouter(prefix="/v1/payments", tags=["payments"])
worker = APIRouter(prefix="/v1/worker", tags=["worker"], dependencies=[Depends(require_worker_key)])
public = APIRouter(prefix="/v1/public/payments", tags=["public"])


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


# -- worker -----------------------------------------------------------------------------------


class WorkerPaymentLink(BaseModel):
    assistant_id: str
    call_id: str
    to: str = Field(min_length=6, max_length=20)
    amount_pence: int = Field(gt=0)
    description: str = Field(min_length=1, max_length=200)
    consent: bool
    contact_id: str | None = None
    verified_caller: bool = False


class PaymentLinkOut(BaseModel):
    id: str
    status: str
    amount_display: str
    description: str
    expires_at: str | None
    sms_status: str


def _link_out(p: PaymentRequest) -> PaymentLinkOut:
    return PaymentLinkOut(
        id=p.id,
        status=p.status,
        amount_display=p.amount_display,
        description=p.description,
        expires_at=p.expires_at.isoformat() if p.expires_at else None,
        sms_status=p.sms_status or "not_sent",
    )


@worker.post("/payments/link", response_model=PaymentLinkOut)
async def worker_payment_link(
    req: WorkerPaymentLink, payments: PaymentsDep, store: StoreDep
) -> PaymentLinkOut:
    cfg = await store.get_assistant(req.assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    if not cfg.payments.enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "payments not enabled for this assistant")
    needs_verification = cfg.verification.enabled and cfg.verification.required_for_payments
    if needs_verification and not req.verified_caller:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "caller must be verified first")
    try:
        p = await payments.request_link(
            cfg.tenant_id,
            cfg.company_id,
            to=req.to,
            amount_pence=req.amount_pence,
            description=req.description,
            consent=req.consent,
            call_id=req.call_id,
            assistant_id=cfg.assistant_id,
            contact_id=req.contact_id,
            currency=cfg.payments.currency,
            max_pence=cfg.payments.max_pence,
            ttl_minutes=cfg.payments.link_ttl_minutes,
            verified_caller=req.verified_caller,
        )
    except PaymentError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return _link_out(p)


class WorkerVerify(BaseModel):
    assistant_id: str
    call_id: str
    contact_id: str | None = None
    caller: str | None = None  # E.164; used to find the contact when contact_id is unknown
    answers: dict[VerificationField, str] = Field(min_length=1)


@worker.post("/verification/check", response_model=VerificationResult)
async def worker_verify(
    req: WorkerVerify, payments: PaymentsDep, store: StoreDep
) -> VerificationResult:
    cfg = await store.get_assistant(req.assistant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    if not cfg.verification.enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "verification not enabled")
    contact_id = req.contact_id
    if contact_id is None and req.caller and not req.caller.startswith("web:"):
        contact_id, _ = await store.touch_contact(cfg.tenant_id, cfg.company_id, req.caller)
    allowed = set(cfg.verification.fields)
    answers = {f: v for f, v in req.answers.items() if f in allowed}
    if not answers:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no permitted verification fields")
    return await payments.verify(
        cfg.tenant_id,
        req.call_id,
        contact_id,
        answers,
        required_matches=cfg.verification.required_matches,
        max_attempts=cfg.verification.max_attempts,
    )


# -- dashboard --------------------------------------------------------------------------------


@router.get("", response_model=list[PaymentRequest])
async def list_payments(
    user: UserDep, payments: PaymentsDep, tenant_id: str, call_id: str | None = None
) -> list[PaymentRequest]:
    user.require_tenant(tenant_id)
    if call_id:
        return await payments.for_call(tenant_id, call_id)
    return await payments.list_requests(tenant_id)


class ManualLink(BaseModel):
    to: str = Field(min_length=6, max_length=20)
    amount_pence: int = Field(gt=0)
    description: str = Field(min_length=1, max_length=200)
    call_id: str | None = None
    contact_id: str | None = None
    consent: bool = True


@router.post("", response_model=PaymentRequest, status_code=status.HTTP_201_CREATED)
async def create_payment(
    request: Request,
    user: UserDep,
    payments: PaymentsDep,
    store: StoreDep,
    audit: AuditDep,
    tenant_id: str,
    body: ManualLink,
) -> PaymentRequest:
    user.require_tenant(tenant_id)
    cfgs = await store.list_assistants(tenant_id)
    if not cfgs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no assistant for tenant")
    cfg = cfgs[0]
    try:
        p = await payments.request_link(
            tenant_id,
            cfg.company_id,
            to=body.to,
            amount_pence=body.amount_pence,
            description=body.description,
            consent=body.consent,
            call_id=body.call_id,
            assistant_id=cfg.assistant_id,
            contact_id=body.contact_id,
            currency=cfg.payments.currency,
            ttl_minutes=cfg.payments.link_ttl_minutes,
            requested_by=user.email,
        )
    except PaymentError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    await audit.record(_audit(request, user, tenant_id, "payment.link", p.id))
    return p


@router.post("/{payment_id}/cancel", response_model=PaymentRequest)
async def cancel_payment(
    request: Request,
    user: UserDep,
    payments: PaymentsDep,
    audit: AuditDep,
    payment_id: str,
    tenant_id: str,
) -> PaymentRequest:
    user.require_tenant(tenant_id)
    p = await payments.cancel(tenant_id, payment_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "payment not found")
    await audit.record(_audit(request, user, tenant_id, "payment.cancel", payment_id))
    return p


class RefundIn(BaseModel):
    amount_pence: int | None = Field(default=None, gt=0)


@router.post("/{payment_id}/refund", response_model=PaymentRequest)
async def refund_payment(
    request: Request,
    user: UserDep,
    payments: PaymentsDep,
    audit: AuditDep,
    payment_id: str,
    tenant_id: str,
    body: RefundIn,
) -> PaymentRequest:
    user.require_admin(tenant_id)
    try:
        p = await payments.refund(tenant_id, payment_id, body.amount_pence)
    except PaymentError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    await audit.record(_audit(request, user, tenant_id, "payment.refund", payment_id))
    return p


class IdentityIn(BaseModel):
    """Raw answers are hashed on receipt; an empty string clears that field."""

    answers: dict[VerificationField, str] = Field(min_length=1)


class IdentityOut(BaseModel):
    contact_id: str
    fields: list[VerificationField]
    updated_at: str | None


@router.get("/verification/contacts/{contact_id}", response_model=IdentityOut)
async def get_identity(
    user: UserDep, payments: PaymentsDep, contact_id: str, tenant_id: str
) -> IdentityOut:
    user.require_tenant(tenant_id)
    ident = await payments.identity(tenant_id, contact_id)
    return IdentityOut(
        contact_id=contact_id,
        fields=ident.fields if ident else [],
        updated_at=ident.updated_at.isoformat() if ident else None,
    )


@router.put("/verification/contacts/{contact_id}", response_model=IdentityOut)
async def put_identity(
    request: Request,
    user: UserDep,
    payments: PaymentsDep,
    store: StoreDep,
    audit: AuditDep,
    contact_id: str,
    tenant_id: str,
    body: IdentityIn,
) -> IdentityOut:
    user.require_tenant(tenant_id)
    c = await store.get_contact(contact_id)
    if c is None or c.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
    ident = await payments.set_identity(tenant_id, contact_id, body.answers)
    await audit.record(_audit(request, user, tenant_id, "verification.identity.set", contact_id))
    return IdentityOut(
        contact_id=contact_id, fields=ident.fields, updated_at=ident.updated_at.isoformat()
    )


@router.delete("/verification/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity(
    request: Request,
    user: UserDep,
    payments: PaymentsDep,
    audit: AuditDep,
    contact_id: str,
    tenant_id: str,
) -> None:
    user.require_tenant(tenant_id)
    if not await payments.clear_identity(tenant_id, contact_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no verification data")
    await audit.record(_audit(request, user, tenant_id, "verification.identity.clear", contact_id))


@router.get("/verification/calls/{call_id}", response_model=list[VerificationAttempt])
async def call_verification(
    user: UserDep, payments: PaymentsDep, call_id: str, tenant_id: str
) -> list[VerificationAttempt]:
    user.require_tenant(tenant_id)
    return await payments.attempts_for_call(tenant_id, call_id)


# -- provider webhook -------------------------------------------------------------------------


@public.post("/webhook")
async def payments_webhook(
    request: Request,
    payments: PaymentsDep,
    stripe_signature: Annotated[str | None, Header()] = None,
) -> dict[str, str | None]:
    payload = await request.body()
    try:
        p = await payments.handle_webhook(payload, stripe_signature)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"payment_id": p.id if p else None, "status": p.status if p else None}
