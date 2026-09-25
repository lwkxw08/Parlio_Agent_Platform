"""Account, organisation membership, onboarding and public share endpoints."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from parlio_api.admin import PLATFORM_TENANT
from parlio_api.auth import UserDep
from parlio_api.billing import PLAN_BY_ID
from parlio_api.contacts import ContactRules
from parlio_api.deps import (
    AuditDep,
    BillingDep,
    ContactsDep,
    SettingsDep,
    StoreDep,
    ensure_cap,
)
from parlio_api.journey import QUESTIONNAIRE_KIND, Questionnaire, Vertical, apply_playbook
from parlio_api.observability import AuditEntry
from parlio_api.onboarding import (
    PlaceResult,
    WebsiteAnalysis,
    analyse_website,
    config_patch_from_analysis,
    search_places,
)
from parlio_api.store import CallStore, Contact, ContactUpdate, Member, TenantDoc
from parlio_voice.models import AssistantConfig, BusinessInfo, Faq, Schedule, VoiceConfig

router = APIRouter(prefix="/v1", tags=["account"])
public = APIRouter(prefix="/v1/public", tags=["public"])

ROLES = ("owner", "admin", "member", "viewer")
PREFS_KIND = "user_prefs"
THEMES = ("light", "dark")


class Me(BaseModel):
    user_id: str
    email: str
    name: str | None
    mode: str
    memberships: list[Member]
    auth: dict[str, Any]
    staff_role: str | None = None
    view_as: str | None = None
    mfa_verified: bool = False
    organisations: dict[str, str] = {}
    theme: str | None = None


class Preferences(BaseModel):
    theme: str | None = None


async def _prefs(store: CallStore, user_id: str) -> Preferences:
    doc = await store.get_doc(PREFS_KIND, user_id)
    return Preferences.model_validate(doc.data) if doc else Preferences()


@router.get("/me", response_model=Me)
async def me(user: UserDep, settings: SettingsDep, store: StoreDep) -> Me:
    prefs = await _prefs(store, user.user_id)
    names: dict[str, str] = {}
    for m in user.tenant_memberships:
        if m.tenant_id in names:
            continue
        assistants = await store.list_assistants(m.tenant_id)
        names[m.tenant_id] = assistants[0].business_name if assistants else m.tenant_id
    return Me(
        user_id=user.user_id,
        email=user.email,
        name=user.name,
        mode=user.mode,
        memberships=user.tenant_memberships,
        auth={"mode": settings.auth_mode, "supabase_url": settings.supabase_url},
        staff_role=user.staff_role,
        view_as=user.view_as,
        mfa_verified=user.mfa_verified,
        organisations=names,
        theme=prefs.theme,
    )


@router.patch("/me/preferences", response_model=Preferences)
async def update_preferences(body: Preferences, user: UserDep, store: StoreDep) -> Preferences:
    """Per-user dashboard preferences (theme) that follow the account across browsers."""
    if body.theme is not None and body.theme not in THEMES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid theme")
    prefs = await _prefs(store, user.user_id)
    merged = prefs.model_copy(update=body.model_dump(exclude_unset=True))
    await store.put_doc(
        TenantDoc(
            kind=PREFS_KIND, id=user.user_id, tenant_id=PLATFORM_TENANT, data=merged.model_dump()
        )
    )
    return merged


# -- members -----------------------------------------------------------------------------------


class Invite(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str | None = None
    role: str = "member"


class RoleChange(BaseModel):
    role: str


@router.get("/organisations/{tenant_id}/members", response_model=list[Member])
async def list_members(tenant_id: str, user: UserDep, store: StoreDep) -> list[Member]:
    user.require_tenant(tenant_id)
    return await store.list_members(tenant_id)


@router.post(
    "/organisations/{tenant_id}/members", response_model=Member, status_code=status.HTTP_201_CREATED
)
async def invite_member(
    tenant_id: str, body: Invite, user: UserDep, store: StoreDep, billing: BillingDep
) -> Member:
    """Create a pending membership; it activates on the invitee's first sign-in."""
    user.require_admin(tenant_id)
    if body.role not in ROLES or body.role == "owner":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid role")
    members = await store.list_members(tenant_id)
    await ensure_cap(billing, tenant_id, "max_members", len(members))
    return await store.upsert_member(
        Member(
            tenant_id=tenant_id,
            user_id=f"u_{uuid4().hex[:12]}",
            email=str(body.email),
            name=body.name,
            role=body.role,
            status="invited",
            invited_at=datetime.now(UTC),
        )
    )


@router.patch("/organisations/{tenant_id}/members/{user_id}", response_model=Member)
async def change_role(
    tenant_id: str, user_id: str, body: RoleChange, user: UserDep, store: StoreDep
) -> Member:
    user.require_admin(tenant_id)
    if body.role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid role")
    m = next((m for m in await store.list_members(tenant_id) if m.user_id == user_id), None)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    if m.role == "owner" and body.role != "owner":
        owners = [x for x in await store.list_members(tenant_id) if x.role == "owner"]
        if len(owners) <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "organisation needs one owner")
    return await store.upsert_member(m.model_copy(update={"role": body.role}))


@router.delete("/organisations/{tenant_id}/members/{user_id}", status_code=204)
async def remove_member(tenant_id: str, user_id: str, user: UserDep, store: StoreDep) -> None:
    user.require_admin(tenant_id)
    m = next((m for m in await store.list_members(tenant_id) if m.user_id == user_id), None)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    if m.role == "owner":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "transfer ownership first")
    await store.remove_member(tenant_id, user_id)


# -- contacts / prospects ----------------------------------------------------------------------


@router.get("/contacts", response_model=list[Contact])
async def list_contacts(
    user: UserDep, store: StoreDep, tenant_id: str | None = None, q: str | None = None
) -> list[Contact]:
    return await store.list_contacts(user.scope(tenant_id), q)


@router.get("/contacts/rules", response_model=ContactRules)
async def get_contact_rules(
    user: UserDep, contacts: ContactsDep, tenant_id: str | None = None
) -> ContactRules:
    tid = tenant_id or (user.tenant_ids[0] if user.tenant_ids else None)
    if tid is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no organisation")
    user.require_tenant(tid)
    return await contacts.rules(tid)


@router.put("/contacts/rules", response_model=ContactRules)
async def put_contact_rules(
    body: ContactRules,
    request: Request,
    user: UserDep,
    contacts: ContactsDep,
    audit: AuditDep,
    tenant_id: str | None = None,
) -> ContactRules:
    tid = tenant_id or (user.tenant_ids[0] if user.tenant_ids else None)
    if tid is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no organisation")
    user.require_tenant(tid)
    saved = await contacts.save_rules(tid, body)
    await audit.record(
        AuditEntry(
            tenant_id=tid,
            actor=user.email,
            action="contacts.rules_updated",
            method=request.method,
            path=request.url.path,
            ip=request.client.host if request.client else None,
            meta=saved.model_dump(mode="json"),
        )
    )
    return saved


async def _owned_contact(contact_id: str, user: UserDep, store: StoreDep) -> Contact:
    c = await store.get_contact(contact_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
    user.require_tenant(c.tenant_id)
    return c


@router.get("/contacts/{contact_id}", response_model=Contact)
async def get_contact(contact_id: str, user: UserDep, store: StoreDep) -> Contact:
    return await _owned_contact(contact_id, user, store)


@router.patch("/contacts/{contact_id}", response_model=Contact)
async def update_contact(
    contact_id: str,
    body: ContactUpdate,
    request: Request,
    user: UserDep,
    store: StoreDep,
    contacts: ContactsDep,
    audit: AuditDep,
) -> Contact:
    before = await _owned_contact(contact_id, user, store)
    try:
        c = await contacts.set_manual(before, body, actor=user.email)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
    if body.status is not None or body.vip is not None or body.status_pinned is not None:
        await audit.record(
            AuditEntry(
                tenant_id=c.tenant_id,
                actor=user.email,
                action="contact.status_changed",
                target=c.id,
                method=request.method,
                path=request.url.path,
                ip=request.client.host if request.client else None,
                meta={
                    "from": {"status": before.status, "vip": before.vip},
                    "to": {"status": c.status, "vip": c.vip},
                    "pinned": c.status_pinned,
                },
            )
        )
    return c


# -- onboarding --------------------------------------------------------------------------------


class AnalyseRequest(BaseModel):
    url: str


class AnalyseResponse(BaseModel):
    analysis: WebsiteAnalysis
    config_patch: dict[str, Any]


@router.post("/onboarding/analyse-website", response_model=AnalyseResponse)
async def analyse(body: AnalyseRequest) -> AnalyseResponse:
    a = await analyse_website(body.url)
    return AnalyseResponse(analysis=a, config_patch=config_patch_from_analysis(a))


@router.get("/onboarding/places", response_model=list[PlaceResult])
async def places(query: str, settings: SettingsDep) -> list[PlaceResult]:
    if not settings.google_places_api_key:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Google Places not configured")
    return await search_places(query, settings.google_places_api_key)


class OnboardingRequest(BaseModel):
    organisation_name: str = Field(min_length=1)
    assistant_name: str = "ParlioTec"
    business: BusinessInfo = Field(default_factory=BusinessInfo)
    hours: Schedule = Field(default_factory=Schedule)
    faqs: list[Faq] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=lambda: ["en"])
    greeting: str | None = None
    voice: VoiceConfig | None = None
    numbers: list[str] = Field(default_factory=list)
    vertical: Vertical = "general"
    questionnaire: Questionnaire | None = None
    plan_id: str | None = None  # optional; without it the trial starts on the default plan


class OnboardingResult(BaseModel):
    tenant_id: str
    assistant: AssistantConfig
    member: Member
    plan_id: str | None = None
    trial_ends_at: datetime | None = None


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "org"
    return f"{base}-{secrets.token_hex(2)}"


@router.post("/onboarding", response_model=OnboardingResult, status_code=status.HTTP_201_CREATED)
async def complete_onboarding(
    body: OnboardingRequest, user: UserDep, store: StoreDep, billing: BillingDep
) -> OnboardingResult:
    """Create the organisation, make the signer its owner and publish assistant v1."""
    tenant_id = _slug(body.organisation_name)
    member = await store.upsert_member(
        Member(
            tenant_id=tenant_id,
            user_id=user.user_id,
            email=user.email,
            name=user.name,
            role="owner",
        )
    )
    cfg = AssistantConfig(
        tenant_id=tenant_id,
        company_id=f"{tenant_id}-main",
        assistant_id=f"{tenant_id}-assistant",
        name=body.assistant_name,
        business_name=body.organisation_name,
        business=body.business,
        hours=body.hours,
        faqs=body.faqs,
        languages=body.languages or ["en"],
        language=(body.languages or ["en"])[0],
    )
    if body.greeting:
        cfg.greeting = body.greeting
    if body.voice:
        cfg.voice = body.voice
    cfg = apply_playbook(cfg, body.vertical)
    await store.upsert_assistant(cfg, body.numbers)
    if body.plan_id and body.plan_id in PLAN_BY_ID and not PLAN_BY_ID[body.plan_id].enterprise:
        sub = await billing.change_plan(tenant_id, body.plan_id)
    else:
        sub = await billing.subscription(tenant_id)
    plan_id, trial_ends = sub.plan_id, sub.trial_ends_at
    q = body.questionnaire
    await store.put_doc(
        TenantDoc(
            kind=QUESTIONNAIRE_KIND,
            id=tenant_id,
            tenant_id=tenant_id,
            data={
                "questionnaire": q.model_dump(mode="json") if q else None,
                "recommended_plan_id": body.plan_id,
                "vertical": body.vertical,
                "signed_up_at": datetime.now(UTC).isoformat(),
            },
        )
    )
    return OnboardingResult(
        tenant_id=tenant_id,
        assistant=cfg,
        member=member,
        plan_id=plan_id,
        trial_ends_at=trial_ends,
    )


# -- public share links ------------------------------------------------------------------------


class SharedCall(BaseModel):
    call_id: str
    business_name: str | None
    started_at: datetime
    duration_s: float | None
    caller: str | None
    summary: str | None
    extracted: dict[str, Any]
    transcript: list[dict[str, Any]]


@public.get("/share/{token}", response_model=SharedCall)
async def shared_call(token: str, store: StoreDep) -> SharedCall:
    call = await store.get_call_by_share_token(token)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "link not found")
    cfg = await store.get_assistant(call.assistant_id)
    return SharedCall(
        call_id=call.call_id,
        business_name=cfg.business_name if cfg else None,
        started_at=call.started_at,
        duration_s=call.duration_s,
        caller=(call.caller[:-4] + "****") if call.caller and len(call.caller) > 6 else None,
        summary=call.summary,
        extracted=call.extracted,
        transcript=call.transcript,
    )
