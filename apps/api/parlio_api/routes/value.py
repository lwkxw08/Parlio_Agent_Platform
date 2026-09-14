"""Phase 14 routes: business value (lead scores, attribution, missed revenue, digest, tracking
numbers), white-label / agency, compliance pack, account security (2FA, sessions, SSO, SCIM)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from parlio_api.auth import UserDep
from parlio_api.deps import (
    AuditDep,
    ComplianceDep,
    DigestDep,
    SecurityDep,
    StoreDep,
    ValueDep,
    WhiteLabelDep,
)
from parlio_api.observability import AuditEntry
from parlio_api.security import (
    EnrolmentStart,
    MfaToken,
    RecoveryCodes,
    ScimConfig,
    SecurityPolicy,
    Session,
    SsoConfig,
    TwoFactorStatus,
)
from parlio_api.store import Member
from parlio_api.value import (
    DigestRecord,
    LeadScore,
    TrackingNumber,
    ValueReport,
    ValueSettings,
)
from parlio_api.whitelabel import (
    Branding,
    ClientSummary,
    CompliancePack,
    DomainInstructions,
    PublicBranding,
    TenantLink,
    audit_csv,
    compliance_pack,
)

router = APIRouter(prefix="/v1", tags=["value"])
public = APIRouter(prefix="/v1/public", tags=["public"])
scim = APIRouter(prefix="/scim/v2", tags=["scim"])


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


# -- value -----------------------------------------------------------------------------------------


class ValueOverview(BaseModel):
    settings: ValueSettings
    report: ValueReport
    tracking_numbers: list[TrackingNumber]
    digests: list[DigestRecord]


@router.get("/value", response_model=ValueOverview)
async def value_overview(
    user: UserDep, value: ValueDep, digest: DigestDep, tenant_id: str, days: int = 7
) -> ValueOverview:
    user.require_tenant(tenant_id)
    days = min(max(days, 1), 365)
    return ValueOverview(
        settings=await value.settings(tenant_id),
        report=await value.attribute(tenant_id, days),
        tracking_numbers=await value.tracking_numbers(tenant_id),
        digests=await digest.history(tenant_id, 6),
    )


class ValueSettingsInput(BaseModel):
    currency: str = Field("GBP", pattern=r"^[A-Z]{3}$")
    avg_job_value_pence: int = Field(ge=0)
    booking_value_pence: int | None = Field(None, ge=0)
    lead_to_sale_rate: float = Field(ge=0, le=1)
    missed_call_lead_rate: float = Field(ge=0, le=1)
    digest_enabled: bool = True
    digest_weekday: int = Field(0, ge=0, le=6)
    digest_hour: int = Field(8, ge=0, le=23)


@router.put("/value/settings", response_model=ValueSettings)
async def save_value_settings(
    body: ValueSettingsInput, user: UserDep, value: ValueDep, tenant_id: str
) -> ValueSettings:
    user.require_admin(tenant_id)
    return await value.save_settings(ValueSettings(tenant_id=tenant_id, **body.model_dump()))


@router.get("/value/calls/{call_id}", response_model=LeadScore)
async def call_lead_score(
    user: UserDep, value: ValueDep, tenant_id: str, call_id: str
) -> LeadScore:
    user.require_tenant(tenant_id)
    ls = await value.score_call(tenant_id, call_id)
    if ls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")
    return ls


class TrackingInput(BaseModel):
    id: str | None = None
    e164: str = Field(pattern=r"^\+\d{7,15}$")
    channel: str = Field(min_length=1, max_length=60)
    campaign: str | None = None
    monthly_cost_pence: int = Field(0, ge=0)


@router.put("/value/tracking-numbers", response_model=TrackingNumber)
async def save_tracking(
    body: TrackingInput, user: UserDep, value: ValueDep, tenant_id: str
) -> TrackingNumber:
    user.require_admin(tenant_id)
    data = body.model_dump(exclude={"id"})
    if body.id:
        if body.id not in {t.id for t in await value.tracking_numbers(tenant_id)}:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "tracking number not found")
        tn = TrackingNumber(id=body.id, tenant_id=tenant_id, **data)
    else:
        tn = TrackingNumber(tenant_id=tenant_id, **data)
    try:
        return await value.save_tracking_number(tn)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e


@router.delete("/value/tracking-numbers/{tid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tracking(tid: str, user: UserDep, value: ValueDep, tenant_id: str) -> None:
    user.require_admin(tenant_id)
    if not await value.delete_tracking_number(tenant_id, tid):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tracking number not found")


@router.post("/value/digest/send", response_model=DigestRecord)
async def send_digest(user: UserDep, digest: DigestDep, tenant_id: str) -> DigestRecord:
    user.require_admin(tenant_id)
    return await digest.send(tenant_id)


# -- white-label -----------------------------------------------------------------------------------


class BrandingView(BaseModel):
    branding: Branding
    domain: DomainInstructions | None
    is_agency: bool
    parent: TenantLink | None


@router.get("/whitelabel", response_model=BrandingView)
async def whitelabel(user: UserDep, wl: WhiteLabelDep, tenant_id: str) -> BrandingView:
    user.require_tenant(tenant_id)
    return BrandingView(
        branding=await wl.branding(tenant_id),
        domain=await wl.domain_instructions(tenant_id),
        is_agency=bool(await wl.clients(tenant_id)),
        parent=await wl.parent_of(tenant_id),
    )


class BrandingInput(BaseModel):
    brand_name: str = Field(min_length=1, max_length=60)
    logo_url: str | None = None
    icon_url: str | None = None
    primary_colour: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    accent_colour: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    support_email: str | None = None
    support_url: str | None = None
    hide_powered_by: bool = False
    custom_domain: str | None = None


@router.put("/whitelabel", response_model=Branding)
async def save_branding(
    body: BrandingInput,
    request: Request,
    user: UserDep,
    wl: WhiteLabelDep,
    audit: AuditDep,
    tenant_id: str,
) -> Branding:
    user.require_admin(tenant_id)
    try:
        b = await wl.save_branding(Branding(tenant_id=tenant_id, **body.model_dump()))
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    await audit.record(_audit(request, user, tenant_id, "whitelabel.update", tenant_id))
    return b


class VerifyDomainInput(BaseModel):
    txt_records: list[str] = Field(
        default_factory=list,
        description="TXT values currently published at _parlio.<domain>",
    )


@router.post("/whitelabel/verify-domain", response_model=Branding)
async def verify_domain(
    body: VerifyDomainInput, user: UserDep, wl: WhiteLabelDep, tenant_id: str
) -> Branding:
    user.require_admin(tenant_id)
    if not body.txt_records:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "paste the TXT value(s) currently published at the _parlio record (e.g. from "
            "`dig TXT _parlio.<domain>` or your DNS provider)",
        )
    try:
        return await wl.verify_domain(tenant_id, body.txt_records)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.get("/whitelabel/clients", response_model=list[ClientSummary])
async def clients(user: UserDep, wl: WhiteLabelDep, tenant_id: str) -> list[ClientSummary]:
    user.require_admin(tenant_id)
    return await wl.client_summaries(tenant_id)


class ClientInput(BaseModel):
    client_name: str = Field(min_length=1, max_length=80)
    business_name: str = Field(min_length=1, max_length=120)
    inherit_branding: bool = True


@router.post("/whitelabel/clients", response_model=TenantLink, status_code=status.HTTP_201_CREATED)
async def create_client(
    body: ClientInput,
    request: Request,
    user: UserDep,
    wl: WhiteLabelDep,
    store: StoreDep,
    audit: AuditDep,
    tenant_id: str,
) -> TenantLink:
    user.require_admin(tenant_id)
    admins = [m for m in await store.list_members(tenant_id) if m.role in ("owner", "admin")]
    try:
        link = await wl.create_client(
            tenant_id,
            client_name=body.client_name,
            business_name=body.business_name,
            admins=admins,
            inherit_branding=body.inherit_branding,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    await audit.record(
        _audit(request, user, tenant_id, "whitelabel.client.create", link.child_tenant_id)
    )
    return link


class ClientUpdate(BaseModel):
    client_name: str | None = Field(None, min_length=1, max_length=80)
    inherit_branding: bool | None = None
    notes: str | None = None


@router.patch("/whitelabel/clients/{link_id}", response_model=TenantLink)
async def update_client(
    link_id: str, body: ClientUpdate, user: UserDep, wl: WhiteLabelDep, tenant_id: str
) -> TenantLink:
    user.require_admin(tenant_id)
    link = await wl.update_client(tenant_id, link_id, **body.model_dump(exclude_none=True))
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    return link


@public.get("/branding", response_model=PublicBranding)
async def public_branding(wl: WhiteLabelDep, host: str | None = None) -> PublicBranding:
    return await wl.public_branding(host)


# -- compliance pack -------------------------------------------------------------------------------


@router.get("/compliance/pack", response_model=CompliancePack)
async def get_pack(
    user: UserDep,
    store: StoreDep,
    compliance: ComplianceDep,
    security: SecurityDep,
    audit: AuditDep,
    tenant_id: str,
) -> CompliancePack:
    user.require_tenant(tenant_id)
    return await compliance_pack(tenant_id, store, compliance, security, audit)


@router.get("/compliance/audit.csv")
async def export_audit(
    request: Request, user: UserDep, audit: AuditDep, tenant_id: str, limit: int = 5000
) -> Response:
    user.require_admin(tenant_id)
    entries = await audit.recent(tenant_id, min(max(limit, 1), 20000))
    await audit.record(_audit(request, user, tenant_id, "compliance.audit.export", tenant_id))
    return Response(
        content=audit_csv(entries),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="parlio-audit-{tenant_id}.csv"'},
    )


# -- account security: 2FA -------------------------------------------------------------------------


@router.get("/account/2fa", response_model=TwoFactorStatus)
async def two_factor_status(user: UserDep, security: SecurityDep) -> TwoFactorStatus:
    return await security.status(user.user_id, user.mfa_verified)


@router.post("/account/2fa/enrol", response_model=EnrolmentStart)
async def enrol(user: UserDep, security: SecurityDep) -> EnrolmentStart:
    try:
        return await security.start_enrolment(user.user_id, user.email)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


class CodeInput(BaseModel):
    code: str = Field(min_length=6, max_length=20)


@router.post("/account/2fa/confirm", response_model=RecoveryCodes)
async def confirm(body: CodeInput, user: UserDep, security: SecurityDep) -> RecoveryCodes:
    try:
        return await security.confirm_enrolment(user.user_id, body.code)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.post("/account/2fa/verify", response_model=MfaToken)
async def verify(
    body: CodeInput,
    request: Request,
    user: UserDep,
    security: SecurityDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> MfaToken:
    hours = 12
    for t in user.tenant_ids:
        hours = min(hours, (await security.policy(t)).session_hours)
    try:
        return await security.verify(
            user.user_id,
            body.code,
            user_agent=user_agent,
            ip=request.client.host if request.client else None,
            session_hours=hours,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e


@router.post("/account/2fa/recovery-codes", response_model=RecoveryCodes)
async def regenerate(body: CodeInput, user: UserDep, security: SecurityDep) -> RecoveryCodes:
    try:
        return await security.regenerate_recovery(user.user_id, body.code)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.post("/account/2fa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable(body: CodeInput, user: UserDep, security: SecurityDep) -> None:
    try:
        await security.disable(user.user_id, body.code)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.get("/account/sessions", response_model=list[Session])
async def sessions(user: UserDep, security: SecurityDep) -> list[Session]:
    return await security.sessions(user.user_id, user.mfa_session_id)


@router.delete("/account/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(session_id: str, user: UserDep, security: SecurityDep) -> None:
    if not await security.revoke(user.user_id, session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")


@router.post("/account/sessions/revoke-others", response_model=dict[str, int])
async def revoke_others(user: UserDep, security: SecurityDep) -> dict[str, int]:
    return {"revoked": await security.revoke_all(user.user_id, user.mfa_session_id)}


# -- org security policy / SSO / SCIM -------------------------------------------------------------


class SecurityView(BaseModel):
    policy: SecurityPolicy
    sso_status: str
    scim_endpoint: str
    members_without_2fa: list[str]


@router.get("/security", response_model=SecurityView)
async def security_view(
    request: Request, user: UserDep, security: SecurityDep, store: StoreDep, tenant_id: str
) -> SecurityView:
    user.require_admin(tenant_id)
    p = await security.policy(tenant_id)
    missing: list[str] = []
    for m in await store.list_members(tenant_id):
        e = await security.enrolment(m.user_id)
        if m.status == "active" and not (e and e.confirmed):
            missing.append(m.email)
    return SecurityView(
        policy=p,
        sso_status=p.sso.status,
        scim_endpoint=str(request.base_url).rstrip("/") + "/scim/v2",
        members_without_2fa=missing,
    )


class PolicyInput(BaseModel):
    require_2fa: str = Field("off", pattern=r"^(off|admins|all)$")
    session_hours: int = Field(12, ge=1, le=24 * 30)
    sso: SsoConfig = Field(default_factory=SsoConfig)


@router.put("/security", response_model=SecurityPolicy)
async def save_policy(
    body: PolicyInput,
    request: Request,
    user: UserDep,
    security: SecurityDep,
    audit: AuditDep,
    tenant_id: str,
) -> SecurityPolicy:
    user.require_admin(tenant_id)
    current = await security.policy(tenant_id)
    if body.require_2fa != "off":
        e = await security.enrolment(user.user_id)
        if not (e and e.confirmed):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "enable 2FA on your own account before enforcing it"
            )
    sso = body.sso.model_copy(update={"supabase_provider_id": current.sso.supabase_provider_id})
    p = current.model_copy(
        update={"require_2fa": body.require_2fa, "session_hours": body.session_hours, "sso": sso}
    )
    p = await security.save_policy(SecurityPolicy.model_validate(p.model_dump()))
    await audit.record(_audit(request, user, tenant_id, "security.policy.update", tenant_id))
    return p


class ScimToken(BaseModel):
    token: str
    config: ScimConfig


@router.post("/security/scim/token", response_model=ScimToken)
async def scim_token(
    request: Request, user: UserDep, security: SecurityDep, audit: AuditDep, tenant_id: str
) -> ScimToken:
    user.require_admin(tenant_id)
    token = await security.issue_scim_token(tenant_id)
    await audit.record(_audit(request, user, tenant_id, "security.scim.token", tenant_id))
    return ScimToken(token=token, config=(await security.policy(tenant_id)).scim)


@router.delete("/security/scim/token", status_code=status.HTTP_204_NO_CONTENT)
async def scim_revoke(user: UserDep, security: SecurityDep, tenant_id: str) -> None:
    user.require_admin(tenant_id)
    await security.revoke_scim_token(tenant_id)


# SCIM v2 (IdP-facing, bearer token)


async def _scim_tenant(security: SecurityDep, authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer token required")
    tenant = await security.tenant_for_scim_token(authorization[7:].strip())
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid SCIM token")
    return tenant


def _scim_user(m: Member) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": m.user_id,
        "userName": m.email,
        "externalId": m.user_id,
        "displayName": m.name,
        "active": m.status != "removed",
        "meta": {"resourceType": "User"},
    }


@scim.get("/Users")
async def scim_list(
    security: SecurityDep,
    store: StoreDep,
    authorization: Annotated[str | None, Header()] = None,
    filter: str | None = None,
) -> dict[str, Any]:
    tenant = await _scim_tenant(security, authorization)
    members = await store.list_members(tenant)
    if filter and "userName eq" in filter:
        wanted = filter.split("eq", 1)[1].strip().strip('"').lower()
        members = [m for m in members if m.email.lower() == wanted]
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": len(members),
        "startIndex": 1,
        "itemsPerPage": len(members),
        "Resources": [_scim_user(m) for m in members],
    }


class ScimUserInput(BaseModel):
    userName: str
    externalId: str | None = None
    displayName: str | None = None
    active: bool = True
    name: dict[str, str] | None = None
    emails: list[dict[str, Any]] = Field(default_factory=list)


def _email_of(u: ScimUserInput) -> str:
    primary = next((e for e in u.emails if e.get("primary")), None) or (
        u.emails[0] if u.emails else None
    )
    return str(primary["value"]) if primary and primary.get("value") else u.userName


def _name_of(u: ScimUserInput) -> str | None:
    if u.displayName:
        return u.displayName
    if u.name:
        return " ".join(x for x in (u.name.get("givenName"), u.name.get("familyName")) if x) or None
    return None


@scim.post("/Users", status_code=status.HTTP_201_CREATED)
async def scim_create(
    body: ScimUserInput,
    security: SecurityDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    tenant = await _scim_tenant(security, authorization)
    m = await security.scim_upsert_user(
        tenant,
        email=_email_of(body),
        name=_name_of(body),
        active=body.active,
        external_id=body.externalId,
    )
    return _scim_user(m)


@scim.put("/Users/{user_id}")
async def scim_replace(
    user_id: str,
    body: ScimUserInput,
    security: SecurityDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    tenant = await _scim_tenant(security, authorization)
    m = await security.scim_upsert_user(
        tenant, email=_email_of(body), name=_name_of(body), active=body.active, external_id=user_id
    )
    return _scim_user(m)


class ScimPatch(BaseModel):
    Operations: list[dict[str, Any]] = Field(default_factory=list)


@scim.patch("/Users/{user_id}")
async def scim_patch(
    user_id: str,
    body: ScimPatch,
    security: SecurityDep,
    store: StoreDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    tenant = await _scim_tenant(security, authorization)
    member = next((m for m in await store.list_members(tenant) if m.user_id == user_id), None)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    active = member.status != "removed"
    for op in body.Operations:
        val = op.get("value")
        if isinstance(val, dict) and "active" in val:
            active = bool(val["active"])
        elif str(op.get("path", "")).lower() == "active":
            active = str(val).lower() in ("true", "1")
    m = await security.scim_upsert_user(
        tenant, email=member.email, name=member.name, active=active, external_id=user_id
    )
    return _scim_user(m)


@scim.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def scim_delete(
    user_id: str,
    security: SecurityDep,
    store: StoreDep,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    tenant = await _scim_tenant(security, authorization)
    member = next((m for m in await store.list_members(tenant) if m.user_id == user_id), None)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    await security.scim_upsert_user(
        tenant, email=member.email, name=member.name, active=False, external_id=user_id
    )
