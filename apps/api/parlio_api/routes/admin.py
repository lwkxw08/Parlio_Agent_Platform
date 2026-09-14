"""Phase 16b: platform-owner admin console routes (``/v1/admin``).

Every route runs through ``staff`` which requires an active platform-staff membership, a verified
2FA session outside dev mode, an allow-listed IP (when configured) and blocks writes for the
read-only role. Mutations are audited into the affected tenant's log with ``platform_staff=True``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from parlio_api.admin import (
    FEATURE_FLAGS,
    PLATFORM_TENANT,
    STAFF_ROLES,
    AdminOverview,
    FeatureFlags,
    PlatformAnalytics,
    PlatformStatus,
    StaffRole,
    StaffSettings,
    SupportNote,
    TenantDetail,
    TenantSummary,
    ViewAsGrant,
)
from parlio_api.auth import Principal, UserDep
from parlio_api.billing import (
    ENTITLEMENTS,
    Coupon,
    Credit,
    Invoice,
    Plan,
    Refund,
    Subscription,
    SubscriptionStatus,
    TenantLimits,
)
from parlio_api.compliance import ErasureResult, SubjectExport
from parlio_api.deps import (
    AdminDep,
    AuditDep,
    BillingDep,
    ComplianceDep,
    SecurityDep,
    SettingsDep,
    StoreDep,
)
from parlio_api.observability import AuditEntry
from parlio_api.store import Member

router = APIRouter(prefix="/v1/admin", tags=["admin"])
public = APIRouter(prefix="/v1/public", tags=["public"])


async def staff(
    request: Request, user: UserDep, settings: SettingsDep, admin: AdminDep
) -> Principal:
    role = user.require_staff()
    if settings.auth_mode != "dev" and not user.mfa_verified:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "two-factor verification required for platform staff",
            headers={"X-Parlio-MFA-Required": "1"},
        )
    ip = request.client.host if request.client else None
    if not await admin.ip_allowed(ip):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "IP address not on the staff allow-list")
    if role == "readonly" and request.method not in ("GET", "HEAD", "OPTIONS"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "read-only staff role")
    return user


StaffDep = Annotated[Principal, Depends(staff)]


async def _audit(
    audit: AuditDep,
    request: Request,
    user: Principal,
    tenant_id: str,
    action: str,
    target: str | None = None,
    meta: Mapping[str, object] | None = None,
) -> AuditEntry:
    return await audit.record(
        AuditEntry(
            tenant_id=tenant_id,
            actor=user.email,
            action=action,
            target=target,
            method=request.method,
            path=request.url.path,
            ip=request.client.host if request.client else None,
            meta={"platform_staff": True, "staff_role": user.staff_role, **dict(meta or {})},
        )
    )


def _fail(e: ValueError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# -- overview / analytics ------------------------------------------------------------------------


@router.get("/overview", response_model=AdminOverview)
async def overview(user: StaffDep, admin: AdminDep, days: int = 30) -> AdminOverview:
    return AdminOverview(
        status=await admin.status(),
        analytics=await admin.analytics(days),
        staff=len(await admin.staff()),
        recent_audit=await admin.staff_activity(limit=15),
    )


@router.get("/analytics", response_model=PlatformAnalytics)
async def analytics(user: StaffDep, admin: AdminDep, days: int = 30) -> PlatformAnalytics:
    return await admin.analytics(days)


@router.get("/export/{what}.csv")
async def export_csv(what: str, user: StaffDep, admin: AdminDep, days: int = 30) -> Response:
    if what == "tenants":
        body = await admin.tenants_csv()
    elif what == "analytics":
        body = await admin.analytics_csv(days)
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown export")
    return Response(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="parlio-{what}.csv"'},
    )


@router.get("/activity", response_model=list[AuditEntry])
async def activity(user: StaffDep, admin: AdminDep, limit: int = 200) -> list[AuditEntry]:
    return await admin.staff_activity(min(max(limit, 1), 1000))


# -- tenants ---------------------------------------------------------------------------------------


@router.get("/tenants", response_model=list[TenantSummary])
async def tenants(
    user: StaffDep,
    admin: AdminDep,
    q: str | None = None,
    sub_status: str | None = None,
    plan_id: str | None = None,
) -> list[TenantSummary]:
    return await admin.tenants(q=q, status=sub_status, plan_id=plan_id)


@router.get("/tenants/{tenant_id}", response_model=TenantDetail)
async def tenant(tenant_id: str, user: StaffDep, admin: AdminDep) -> TenantDetail:
    if tenant_id == PLATFORM_TENANT or tenant_id not in await admin.tenant_ids():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")
    return await admin.tenant_detail(tenant_id)


class FlagsUpdate(BaseModel):
    flags: dict[str, bool]


@router.get("/feature-flags", response_model=dict[str, str])
async def feature_flag_catalogue(user: StaffDep) -> dict[str, str]:
    return FEATURE_FLAGS


@router.get("/entitlements", response_model=dict[str, str])
async def entitlement_catalogue(user: StaffDep) -> dict[str, str]:
    return ENTITLEMENTS


@router.put("/tenants/{tenant_id}/flags", response_model=FeatureFlags)
async def set_flags(
    tenant_id: str,
    body: FlagsUpdate,
    request: Request,
    user: StaffDep,
    admin: AdminDep,
    audit: AuditDep,
) -> FeatureFlags:
    user.require_staff("support")
    try:
        out = await admin.set_flags(tenant_id, body.flags, user.email)
    except ValueError as e:
        raise _fail(e) from e
    await _audit(audit, request, user, tenant_id, "admin.flags.set", meta={"flags": body.flags})
    return out


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    pinned: bool = False


@router.post("/tenants/{tenant_id}/notes", response_model=SupportNote, status_code=201)
async def add_note(
    tenant_id: str, body: NoteIn, request: Request, user: StaffDep, admin: AdminDep, audit: AuditDep
) -> SupportNote:
    user.require_staff("support", "finance")
    n = await admin.add_note(tenant_id, user.email, body.text, body.pinned)
    await _audit(audit, request, user, tenant_id, "admin.note.add", target=n.id)
    return n


@router.delete("/tenants/{tenant_id}/notes/{note_id}", status_code=204)
async def delete_note(
    tenant_id: str, note_id: str, request: Request, user: StaffDep, admin: AdminDep, audit: AuditDep
) -> Response:
    user.require_staff("support", "finance")
    if not await admin.delete_note(tenant_id, note_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "note not found")
    await _audit(audit, request, user, tenant_id, "admin.note.delete", target=note_id)
    return Response(status_code=204)


@router.post("/tenants/{tenant_id}/view-as", response_model=ViewAsGrant)
async def view_as(
    tenant_id: str, request: Request, user: StaffDep, admin: AdminDep, audit: AuditDep
) -> ViewAsGrant:
    """Read-only support login: returns a short-lived grant sent as ``X-Parlio-View-As``."""
    user.require_staff("support", "finance", "readonly")
    if tenant_id not in await admin.tenant_ids():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")
    grant = await admin.issue_view_as(tenant_id, user.email)
    await _audit(
        audit,
        request,
        user,
        tenant_id,
        "admin.view_as",
        meta={"expires_at": grant.expires_at.isoformat(), "read_only": True},
    )
    return grant


@router.post("/tenants/{tenant_id}/members/{user_id}/reset-2fa", status_code=204)
async def reset_2fa(
    tenant_id: str,
    user_id: str,
    request: Request,
    user: StaffDep,
    admin: AdminDep,
    store: StoreDep,
    security: SecurityDep,
    audit: AuditDep,
) -> Response:
    user.require_staff("support")
    if not any(m.user_id == user_id for m in await store.list_members(tenant_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    await security.reset_by_staff(user_id)
    await _audit(audit, request, user, tenant_id, "admin.member.reset_2fa", target=user_id)
    return Response(status_code=204)


@router.post("/tenants/{tenant_id}/members/{user_id}/resend-invite", response_model=Member)
async def resend_invite(
    tenant_id: str,
    user_id: str,
    request: Request,
    user: StaffDep,
    store: StoreDep,
    audit: AuditDep,
) -> Member:
    user.require_staff("support")
    m = next((m for m in await store.list_members(tenant_id) if m.user_id == user_id), None)
    if m is None or m.status != "invited":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no pending invite for that member")
    out = await store.upsert_member(m.model_copy(update={"invited_at": datetime.now(UTC)}))
    await _audit(audit, request, user, tenant_id, "admin.member.resend_invite", target=m.email)
    return out


@router.post("/tenants/{tenant_id}/numbers/{number_id}/release", status_code=204)
async def force_release_number(
    tenant_id: str,
    number_id: str,
    request: Request,
    user: StaffDep,
    billing: BillingDep,
    audit: AuditDep,
) -> Response:
    user.require_staff("support")
    if not await billing.release_number(tenant_id, number_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "number not found")
    await _audit(audit, request, user, tenant_id, "admin.number.release", target=number_id)
    return Response(status_code=204)


class SubjectIn(BaseModel):
    e164: str = Field(min_length=6, max_length=20)


@router.post("/tenants/{tenant_id}/gdpr/export", response_model=SubjectExport)
async def gdpr_export(
    tenant_id: str,
    body: SubjectIn,
    request: Request,
    user: StaffDep,
    compliance: ComplianceDep,
    audit: AuditDep,
) -> SubjectExport:
    user.require_staff("support")
    out = await compliance.export_subject(tenant_id, body.e164)
    await _audit(audit, request, user, tenant_id, "admin.gdpr.export", target=body.e164)
    return out


@router.post("/tenants/{tenant_id}/gdpr/erase", response_model=ErasureResult)
async def gdpr_erase(
    tenant_id: str,
    body: SubjectIn,
    request: Request,
    user: StaffDep,
    compliance: ComplianceDep,
    audit: AuditDep,
) -> ErasureResult:
    user.require_staff("support")
    out = await compliance.erase_subject(tenant_id, body.e164)
    await _audit(audit, request, user, tenant_id, "admin.gdpr.erase", target=body.e164)
    return out


# -- subscriptions --------------------------------------------------------------------------------


class PlanChange(BaseModel):
    plan_id: str
    coupon_code: str | None = None


@router.post("/tenants/{tenant_id}/subscription/plan", response_model=Subscription)
async def set_plan(
    tenant_id: str,
    body: PlanChange,
    request: Request,
    user: StaffDep,
    billing: BillingDep,
    audit: AuditDep,
) -> Subscription:
    user.require_staff("finance")
    try:
        sub = await billing.change_plan(tenant_id, body.plan_id, body.coupon_code, by_staff=True)
    except ValueError as e:
        raise _fail(e) from e
    await _audit(
        audit,
        request,
        user,
        tenant_id,
        "admin.subscription.plan",
        target=body.plan_id,
        meta={"coupon": body.coupon_code},
    )
    return sub


class StatusChange(BaseModel):
    status: SubscriptionStatus
    reason: str | None = Field(default=None, max_length=500)


@router.post("/tenants/{tenant_id}/subscription/status", response_model=Subscription)
async def set_subscription_status(
    tenant_id: str,
    body: StatusChange,
    request: Request,
    user: StaffDep,
    billing: BillingDep,
    audit: AuditDep,
) -> Subscription:
    """pause / suspend / reactivate (active) / cancel."""
    user.require_staff("finance", "support")
    if body.status == SubscriptionStatus.TRIALING:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "use /trial/extend to re-open a trial")
    try:
        sub = await billing.set_status(tenant_id, body.status, body.reason)
    except Exception as e:  # provider errors surface as 502
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"billing provider: {e}") from e
    await _audit(
        audit,
        request,
        user,
        tenant_id,
        f"admin.subscription.{body.status.value}",
        meta={"reason": body.reason},
    )
    return sub


class TrialExtend(BaseModel):
    days: int = Field(ge=1, le=365)


@router.post("/tenants/{tenant_id}/subscription/trial/extend", response_model=Subscription)
async def extend_trial(
    tenant_id: str,
    body: TrialExtend,
    request: Request,
    user: StaffDep,
    billing: BillingDep,
    audit: AuditDep,
) -> Subscription:
    user.require_staff("finance", "support")
    sub = await billing.extend_trial(tenant_id, body.days)
    await _audit(
        audit, request, user, tenant_id, "admin.subscription.trial_extend", meta={"days": body.days}
    )
    return sub


@router.post("/tenants/{tenant_id}/subscription/trial/convert", response_model=Subscription)
async def convert_trial(
    tenant_id: str, request: Request, user: StaffDep, billing: BillingDep, audit: AuditDep
) -> Subscription:
    user.require_staff("finance")
    sub = await billing.convert_trial(tenant_id)
    await _audit(audit, request, user, tenant_id, "admin.subscription.trial_convert")
    return sub


class CreditIn(BaseModel):
    pence: int = Field(gt=0, le=10_000_000)
    reason: str = Field(min_length=1, max_length=500)


@router.post("/tenants/{tenant_id}/credits", response_model=Credit, status_code=201)
async def grant_credit(
    tenant_id: str,
    body: CreditIn,
    request: Request,
    user: StaffDep,
    billing: BillingDep,
    audit: AuditDep,
) -> Credit:
    user.require_staff("finance")
    c = await billing.grant_credit(tenant_id, body.pence, body.reason, user.email)
    await _audit(
        audit,
        request,
        user,
        tenant_id,
        "admin.credit.grant",
        target=c.id,
        meta={"pence": body.pence, "reason": body.reason},
    )
    return c


class RefundIn(BaseModel):
    pence: int = Field(gt=0, le=10_000_000)
    reason: str = Field(min_length=1, max_length=500)
    invoice_id: str | None = None


@router.post("/tenants/{tenant_id}/refunds", response_model=Refund, status_code=201)
async def refund(
    tenant_id: str,
    body: RefundIn,
    request: Request,
    user: StaffDep,
    billing: BillingDep,
    audit: AuditDep,
) -> Refund:
    user.require_staff("finance")
    try:
        rf = await billing.refund(tenant_id, body.pence, body.reason, user.email, body.invoice_id)
    except ValueError as e:
        raise _fail(e) from e
    await _audit(
        audit,
        request,
        user,
        tenant_id,
        "admin.refund",
        target=rf.id,
        meta={"pence": body.pence, "reason": body.reason, "invoice_id": body.invoice_id},
    )
    return rf


@router.get("/tenants/{tenant_id}/invoices", response_model=list[Invoice])
async def invoices(tenant_id: str, user: StaffDep, billing: BillingDep) -> list[Invoice]:
    return await billing.invoices(tenant_id)


class LimitsIn(BaseModel):
    max_concurrent_calls: int | None = Field(default=None, ge=1, le=500)
    minutes_cap: int | None = Field(default=None, ge=0)
    rate_limit_per_minute: int | None = Field(default=None, ge=10, le=100_000)
    note: str | None = Field(default=None, max_length=300)


@router.put("/tenants/{tenant_id}/limits", response_model=TenantLimits)
async def set_limits(
    tenant_id: str,
    body: LimitsIn,
    request: Request,
    user: StaffDep,
    admin: AdminDep,
    audit: AuditDep,
) -> TenantLimits:
    user.require_staff("finance", "support")
    out = await admin.set_limits(TenantLimits(tenant_id=tenant_id, **body.model_dump()))
    await _audit(audit, request, user, tenant_id, "admin.limits.set", meta=body.model_dump())
    return out


# -- plan & coupon catalogue --------------------------------------------------------------------


@router.get("/plans", response_model=list[Plan])
async def plans(user: StaffDep, admin: AdminDep) -> list[Plan]:
    return admin.plans()


class PlanDefaults(BaseModel):
    trial_days: int


@router.get("/plans/defaults", response_model=PlanDefaults)
async def plan_defaults(user: StaffDep, billing: BillingDep) -> PlanDefaults:
    return PlanDefaults(trial_days=billing.trial_days)


@router.put("/plans/{plan_id}", response_model=Plan)
async def save_plan(
    plan_id: str, body: Plan, request: Request, user: StaffDep, admin: AdminDep, audit: AuditDep
) -> Plan:
    user.require_staff("finance")
    if body.id != plan_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "plan id mismatch")
    try:
        out = await admin.save_plan(body)
    except ValueError as e:
        raise _fail(e) from e
    await _audit(
        audit,
        request,
        user,
        PLATFORM_TENANT,
        "admin.plan.save",
        target=plan_id,
        meta=body.model_dump(),
    )
    return out


@router.delete("/plans/{plan_id}", response_model=Plan | None)
async def delete_plan(
    plan_id: str, request: Request, user: StaffDep, admin: AdminDep, audit: AuditDep
) -> Plan | None:
    user.require_staff("finance")
    try:
        out = await admin.delete_plan(plan_id)
    except ValueError as e:
        raise _fail(e) from e
    await _audit(audit, request, user, PLATFORM_TENANT, "admin.plan.delete", target=plan_id)
    return out


@router.get("/coupons", response_model=list[Coupon])
async def coupons(user: StaffDep, admin: AdminDep) -> list[Coupon]:
    return admin.coupons()


@router.put("/coupons/{code}", response_model=Coupon)
async def save_coupon(
    code: str, body: Coupon, request: Request, user: StaffDep, admin: AdminDep, audit: AuditDep
) -> Coupon:
    user.require_staff("finance")
    if body.code.upper() != code.upper():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "coupon code mismatch")
    out = await admin.save_coupon(body)
    await _audit(
        audit,
        request,
        user,
        PLATFORM_TENANT,
        "admin.coupon.save",
        target=out.code,
        meta=body.model_dump(mode="json"),
    )
    return out


@router.delete("/coupons/{code}", status_code=204)
async def delete_coupon(
    code: str, request: Request, user: StaffDep, admin: AdminDep, audit: AuditDep
) -> Response:
    user.require_staff("finance")
    if not await admin.delete_coupon(code):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "coupon not found")
    await _audit(audit, request, user, PLATFORM_TENANT, "admin.coupon.delete", target=code)
    return Response(status_code=204)


# -- staff -----------------------------------------------------------------------------------------


class StaffInvite(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str | None = None
    role: StaffRole = "support"


class StaffRoleIn(BaseModel):
    role: StaffRole


@router.get("/staff", response_model=list[Member])
async def list_staff(user: StaffDep, admin: AdminDep) -> list[Member]:
    return await admin.staff()


@router.get("/staff/roles", response_model=list[str])
async def staff_roles(user: StaffDep) -> list[str]:
    return list(STAFF_ROLES)


@router.post("/staff", response_model=Member, status_code=201)
async def invite_staff(
    body: StaffInvite, request: Request, user: StaffDep, admin: AdminDep, audit: AuditDep
) -> Member:
    if user.staff_role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only platform owners manage staff")
    m = await admin.invite_staff(body.email.lower(), body.role, body.name)
    await _audit(
        audit,
        request,
        user,
        PLATFORM_TENANT,
        "admin.staff.invite",
        target=m.email,
        meta={"role": body.role},
    )
    return m


@router.patch("/staff/{user_id}", response_model=Member)
async def set_staff_role(
    user_id: str,
    body: StaffRoleIn,
    request: Request,
    user: StaffDep,
    admin: AdminDep,
    audit: AuditDep,
) -> Member:
    if user.staff_role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only platform owners manage staff")
    if user_id == user.user_id and body.role != "owner":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "you cannot demote yourself")
    m = await admin.set_staff_role(user_id, body.role)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "staff member not found")
    await _audit(
        audit,
        request,
        user,
        PLATFORM_TENANT,
        "admin.staff.role",
        target=m.email,
        meta={"role": body.role},
    )
    return m


@router.delete("/staff/{user_id}", status_code=204)
async def remove_staff(
    user_id: str, request: Request, user: StaffDep, admin: AdminDep, audit: AuditDep
) -> Response:
    if user.staff_role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only platform owners manage staff")
    if user_id == user.user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "you cannot remove yourself")
    if not await admin.remove_staff(user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "staff member not found")
    await _audit(audit, request, user, PLATFORM_TENANT, "admin.staff.remove", target=user_id)
    return Response(status_code=204)


@router.get("/staff/settings", response_model=StaffSettings)
async def get_staff_settings(user: StaffDep, admin: AdminDep) -> StaffSettings:
    return await admin.staff_settings()


@router.put("/staff/settings", response_model=StaffSettings)
async def put_staff_settings(
    body: StaffSettings, request: Request, user: StaffDep, admin: AdminDep, audit: AuditDep
) -> StaffSettings:
    if user.staff_role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only platform owners manage staff")
    ip = request.client.host if request.client else None
    try:
        saved = await admin.save_staff_settings(body, user.email)
    except ValueError as e:
        raise _fail(e) from e
    if not await admin.ip_allowed(ip):
        # never lock the owner out with the request that set the list
        await admin.save_staff_settings(
            saved.model_copy(update={"ip_allowlist": [*saved.ip_allowlist, f"{ip}/32"]}), user.email
        )
        saved = await admin.staff_settings()
    await _audit(
        audit,
        request,
        user,
        PLATFORM_TENANT,
        "admin.staff.settings",
        meta=body.model_dump(mode="json"),
    )
    return saved


# -- platform status ------------------------------------------------------------------------------


@router.get("/status", response_model=PlatformStatus)
async def get_status(user: StaffDep, admin: AdminDep) -> PlatformStatus:
    return await admin.status()


@router.put("/status", response_model=PlatformStatus)
async def put_status(
    body: PlatformStatus, request: Request, user: StaffDep, admin: AdminDep, audit: AuditDep
) -> PlatformStatus:
    user.require_staff("support")
    out = await admin.set_status(body, user.email)
    await _audit(
        audit,
        request,
        user,
        PLATFORM_TENANT,
        "admin.status.set",
        meta={"level": body.level, "title": body.title},
    )
    return out


class PublicStatus(BaseModel):
    level: str
    title: str
    message: str
    link: str | None
    active: bool


@public.get("/status", response_model=PublicStatus)
async def public_status(admin: AdminDep) -> PublicStatus:
    """Incident banner for the dashboard and widget (no auth)."""
    s = await admin.status()
    on = s.active()
    return PublicStatus(
        level=s.level if on else "ok",
        title=s.title if on else "",
        message=s.message if on else "",
        link=s.link if on else None,
        active=on,
    )
