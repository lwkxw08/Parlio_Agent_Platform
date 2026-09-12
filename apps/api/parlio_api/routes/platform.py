"""Phase 6 dashboard routes: billing & numbers, observability, compliance (GDPR), audit log.

Every tenant-scoped endpoint takes an explicit `tenant_id` and checks membership; mutating and
sensitive operations require an admin role and write an audit entry.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from parlio_api.auth import UserDep
from parlio_api.billing import (
    PLANS,
    CheckoutSession,
    Coupon,
    Plan,
    Subscription,
    TenantNumber,
    UsageSummary,
)
from parlio_api.compliance import (
    ErasureResult,
    RetentionPolicy,
    RetentionRun,
    SubjectExport,
)
from parlio_api.deps import (
    AuditDep,
    BillingDep,
    ComplianceDep,
    SettingsDep,
    StoreDep,
    TelemetryDep,
)
from parlio_api.observability import AuditEntry, LatencyReport
from parlio_api.store import CallStore
from parlio_api.telephony.base import PhoneNumber

router = APIRouter(prefix="/v1", tags=["platform"])
public = APIRouter(prefix="/v1/public", tags=["public"])
ops = APIRouter(tags=["ops"])


async def _company(store: CallStore, tenant_id: str) -> str:
    cfgs = await store.list_assistants(tenant_id)
    return cfgs[0].company_id if cfgs else f"{tenant_id}-main"


def _audit(
    request: Request,
    user: UserDep,
    tenant_id: str,
    action: str,
    target: str | None = None,
    meta: Mapping[str, object] | None = None,
) -> AuditEntry:
    return AuditEntry(
        tenant_id=tenant_id,
        actor=user.email,
        action=action,
        target=target,
        method=request.method,
        path=request.url.path,
        ip=request.client.host if request.client else None,
        meta=dict(meta or {}),
    )


# -- Billing ---------------------------------------------------------------------------------


@router.get("/billing/plans", response_model=list[Plan])
async def list_plans() -> list[Plan]:
    return PLANS


@router.get("/billing/subscription", response_model=Subscription)
async def get_subscription(user: UserDep, billing: BillingDep, tenant_id: str) -> Subscription:
    user.require_tenant(tenant_id)
    return await billing.subscription(tenant_id)


class PlanChange(BaseModel):
    plan_id: str
    coupon: str | None = None


@router.post("/billing/subscription", response_model=Subscription)
async def change_plan(
    request: Request,
    user: UserDep,
    billing: BillingDep,
    audit: AuditDep,
    tenant_id: str,
    body: PlanChange,
) -> Subscription:
    user.require_admin(tenant_id)
    try:
        sub = await billing.change_plan(tenant_id, body.plan_id, body.coupon)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    await audit.record(
        _audit(
            request, user, tenant_id, "billing.plan.change", body.plan_id, {"coupon": body.coupon}
        )
    )
    return sub


class CouponCheck(BaseModel):
    code: str
    plan_id: str


@router.post("/billing/coupon", response_model=Coupon)
async def check_coupon(
    user: UserDep, billing: BillingDep, tenant_id: str, body: CouponCheck
) -> Coupon:
    user.require_tenant(tenant_id)
    c = billing.coupon(body.code, body.plan_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invalid or expired coupon")
    return c


class CheckoutInput(BaseModel):
    plan_id: str
    return_url: str


@router.post("/billing/checkout", response_model=CheckoutSession)
async def checkout(
    request: Request,
    user: UserDep,
    billing: BillingDep,
    audit: AuditDep,
    tenant_id: str,
    body: CheckoutInput,
) -> CheckoutSession:
    user.require_admin(tenant_id)
    try:
        session = await billing.checkout(tenant_id, body.plan_id, user.email, body.return_url)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    await audit.record(
        _audit(
            request,
            user,
            tenant_id,
            "billing.checkout",
            body.plan_id,
            {"provider": session.provider},
        )
    )
    return session


@router.get("/billing/usage", response_model=UsageSummary)
async def usage(user: UserDep, billing: BillingDep, tenant_id: str) -> UsageSummary:
    user.require_tenant(tenant_id)
    return await billing.usage(tenant_id)


@public.post("/billing/webhook")
async def billing_webhook(
    request: Request,
    billing: BillingDep,
    stripe_signature: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    payload = await request.body()
    try:
        handled = await billing.handle_webhook(payload, stripe_signature)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"handled": handled}


# -- Numbers ---------------------------------------------------------------------------------


@router.get("/numbers", response_model=list[TenantNumber])
async def list_numbers(user: UserDep, billing: BillingDep, tenant_id: str) -> list[TenantNumber]:
    user.require_tenant(tenant_id)
    return await billing.list_numbers(tenant_id)


@router.get("/numbers/search", response_model=list[PhoneNumber])
async def search_numbers(
    user: UserDep, billing: BillingDep, tenant_id: str, country: str = "GB", limit: int = 5
) -> list[PhoneNumber]:
    user.require_tenant(tenant_id)
    return await billing.search_numbers(country, min(max(limit, 1), 20))


class ProvisionInput(BaseModel):
    e164: str
    assistant_id: str
    label: str | None = None


@router.post("/numbers", response_model=TenantNumber, status_code=status.HTTP_201_CREATED)
async def provision_number(
    request: Request,
    user: UserDep,
    billing: BillingDep,
    store: StoreDep,
    audit: AuditDep,
    tenant_id: str,
    body: ProvisionInput,
) -> TenantNumber:
    user.require_admin(tenant_id)
    try:
        num = await billing.provision_number(
            tenant_id, await _company(store, tenant_id), body.assistant_id, body.e164, body.label
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    await audit.record(
        _audit(request, user, tenant_id, "numbers.provision", num.e164, {"provider": num.provider})
    )
    return num


@router.delete("/numbers/{number_id}", status_code=status.HTTP_204_NO_CONTENT)
async def release_number(
    request: Request,
    user: UserDep,
    billing: BillingDep,
    audit: AuditDep,
    tenant_id: str,
    number_id: str,
) -> Response:
    user.require_admin(tenant_id)
    if not await billing.release_number(tenant_id, number_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "number not found")
    await audit.record(_audit(request, user, tenant_id, "numbers.release", number_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- Observability ---------------------------------------------------------------------------


@router.get("/observability/latency", response_model=LatencyReport)
async def latency(
    user: UserDep, telemetry: TelemetryDep, store: StoreDep, tenant_id: str, days: int = 7
) -> LatencyReport:
    user.require_tenant(tenant_id)
    return await telemetry.latency_report(store, tenant_id, min(max(days, 1), 90))


@ops.get("/metrics", include_in_schema=False)
async def metrics(
    telemetry: TelemetryDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """Prometheus scrape endpoint. Protected by PARLIO_METRICS_TOKEN when set (always in prod)."""
    if settings.metrics_token or settings.env != "dev":
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not settings.metrics_token or not secrets.compare_digest(
            supplied, settings.metrics_token
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "metrics token required")
    return Response(telemetry.metrics_text(), media_type="text/plain; version=0.0.4")


# -- Audit -----------------------------------------------------------------------------------


@router.get("/audit", response_model=list[AuditEntry])
async def audit_log(
    user: UserDep, audit: AuditDep, tenant_id: str, limit: int = 200
) -> list[AuditEntry]:
    user.require_admin(tenant_id)
    return await audit.recent(tenant_id, min(max(limit, 1), 1000))


# -- Compliance ------------------------------------------------------------------------------


class RetentionView(BaseModel):
    policy: RetentionPolicy
    last_run: RetentionRun | None = None


@router.get("/compliance/retention", response_model=RetentionView)
async def get_retention(user: UserDep, compliance: ComplianceDep, tenant_id: str) -> RetentionView:
    user.require_tenant(tenant_id)
    return RetentionView(
        policy=await compliance.policy(tenant_id), last_run=await compliance.last_run(tenant_id)
    )


class RetentionInput(BaseModel):
    transcript_days: int = Field(90, ge=1, le=3650)
    recording_days: int = Field(30, ge=1, le=3650)
    call_days: int = Field(365, ge=1, le=3650)
    redact_on_write: bool = False
    redact_caller_number: bool = False


@router.put("/compliance/retention", response_model=RetentionPolicy)
async def set_retention(
    request: Request,
    user: UserDep,
    compliance: ComplianceDep,
    audit: AuditDep,
    tenant_id: str,
    body: RetentionInput,
) -> RetentionPolicy:
    user.require_admin(tenant_id)
    try:
        pol = await compliance.set_policy(RetentionPolicy(tenant_id=tenant_id, **body.model_dump()))
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    await audit.record(
        _audit(request, user, tenant_id, "compliance.retention.update", None, body.model_dump())
    )
    return pol


@router.post("/compliance/retention/run", response_model=RetentionRun)
async def run_retention(
    request: Request, user: UserDep, compliance: ComplianceDep, audit: AuditDep, tenant_id: str
) -> RetentionRun:
    user.require_admin(tenant_id)
    run = await compliance.sweep_tenant(tenant_id)
    await audit.record(
        _audit(
            request,
            user,
            tenant_id,
            "compliance.retention.run",
            None,
            run.model_dump(mode="json", exclude={"tenant_id"}),
        )
    )
    return run


class SubjectInput(BaseModel):
    e164: str = Field(pattern=r"^\+[1-9]\d{6,14}$")


@router.post("/compliance/export", response_model=SubjectExport)
async def gdpr_export(
    request: Request,
    user: UserDep,
    compliance: ComplianceDep,
    audit: AuditDep,
    tenant_id: str,
    body: SubjectInput,
) -> SubjectExport:
    user.require_admin(tenant_id)
    out = await compliance.export_subject(tenant_id, body.e164)
    await audit.record(
        _audit(
            request,
            user,
            tenant_id,
            "compliance.subject.export",
            body.e164,
            {"calls": len(out.calls), "tickets": len(out.tickets)},
        )
    )
    return out


@router.post("/compliance/erase", response_model=ErasureResult)
async def gdpr_erase(
    request: Request,
    user: UserDep,
    compliance: ComplianceDep,
    audit: AuditDep,
    tenant_id: str,
    body: SubjectInput,
) -> ErasureResult:
    user.require_admin(tenant_id)
    res = await compliance.erase_subject(tenant_id, body.e164)
    await audit.record(
        _audit(
            request,
            user,
            tenant_id,
            "compliance.subject.erase",
            body.e164,
            res.model_dump(exclude={"tenant_id", "subject_e164"}),
        )
    )
    return res
