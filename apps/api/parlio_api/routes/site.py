"""Public endpoints for the marketing website (apps/marketing).

* ``GET  /v1/public/site``             plans (Platform admin -> Plans), trial length, demo info.
* ``POST /v1/public/site/demo/voice``  "Hear it for yourself": browser click-to-talk against the
                                       demo tenant, capped per month and per visitor IP.
* ``POST /v1/public/site/contact``     contact / sales form -> platform owners by email + stored.

Everything here is unauthenticated, so nothing tenant-specific beyond the demo tenant is exposed.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from parlio_api.admin import PLATFORM_TENANT
from parlio_api.billing import PLANS, Plan, billable_minutes, is_browser_call
from parlio_api.browser_voice import WebVoiceSession
from parlio_api.deps import BillingDep, BrowserVoiceDep, NotificationsDep, SettingsDep, StoreDep
from parlio_api.store import CallFilter, TenantDoc

log = logging.getLogger(__name__)

public = APIRouter(prefix="/v1/public/site", tags=["public"])

CONTACT_KIND = "site_contact"


class PublicPlan(BaseModel):
    id: str
    name: str
    monthly_pence: int
    included_minutes: int
    overage_pence_per_minute: int
    included_sms: int
    sms_overage_pence: int
    included_numbers: int
    max_assistants: int
    max_concurrent_calls: int
    max_resources: int
    max_sites: int
    max_members: int
    features: list[str]
    entitlements: list[str]
    enterprise: bool
    trial_days: int

    @classmethod
    def of(cls, p: Plan, default_trial: int) -> PublicPlan:
        return cls(
            id=p.id,
            name=p.name,
            monthly_pence=p.monthly_pence,
            included_minutes=p.included_minutes,
            overage_pence_per_minute=p.overage_pence_per_minute,
            included_sms=p.included_sms,
            sms_overage_pence=p.sms_overage_pence,
            included_numbers=p.included_numbers,
            max_assistants=p.max_assistants,
            max_concurrent_calls=p.max_concurrent_calls,
            max_resources=p.max_resources,
            max_sites=p.max_sites,
            max_members=p.max_members,
            features=list(p.features),
            entitlements=list(p.entitlements),
            enterprise=p.enterprise,
            trial_days=p.trial_days if p.trial_days is not None else default_trial,
        )


class DemoInfo(BaseModel):
    voice_available: bool
    phone: str | None
    minutes_remaining: int


class SiteInfo(BaseModel):
    plans: list[PublicPlan]
    trial_days: int
    demo: DemoInfo
    dashboard_url: str


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _demo_minutes_used(store: StoreDep, tenant_id: str) -> float:
    now = datetime.now(UTC)
    calls = await store.filter_calls(
        CallFilter(tenant_id=tenant_id, since=_month_start(now), until=now, limit=100000)
    )
    return sum(billable_minutes(c) for c in calls if is_browser_call(c))


async def _demo_info(store: StoreDep, settings: SettingsDep) -> DemoInfo:
    cap = settings.site_demo_monthly_minutes
    used = await _demo_minutes_used(store, settings.site_demo_tenant_id)
    remaining = max(0, int(cap - used)) if cap else 10_000
    has_assistant = bool(await store.list_assistants(settings.site_demo_tenant_id))
    return DemoInfo(
        voice_available=has_assistant and remaining > 0,
        phone=settings.site_demo_phone,
        minutes_remaining=remaining,
    )


@public.get("", response_model=SiteInfo)
async def site_info(store: StoreDep, billing: BillingDep, settings: SettingsDep) -> SiteInfo:
    return SiteInfo(
        plans=[PublicPlan.of(p, billing.trial_days) for p in PLANS],
        trial_days=billing.trial_days,
        demo=await _demo_info(store, settings),
        dashboard_url=settings.dashboard_url,
    )


class _IpWindow:
    """Sliding one-hour window of demo starts per client IP (in-process, best-effort)."""

    def __init__(self) -> None:
        self.hits: dict[str, deque[datetime]] = defaultdict(deque)

    def allow(self, ip: str, limit: int, now: datetime) -> bool:
        q = self.hits[ip]
        cutoff = now - timedelta(hours=1)
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def _window(request: Request) -> _IpWindow:
    try:
        w: _IpWindow = request.app.state.site_demo_window
    except AttributeError:
        w = request.app.state.site_demo_window = _IpWindow()
    return w


class DemoVoiceStart(BaseModel):
    visitor: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str | None = Field(default=None, max_length=80)
    page_url: str | None = Field(default=None, max_length=500)


@public.post("/demo/voice", response_model=WebVoiceSession)
async def demo_voice(
    request: Request,
    body: DemoVoiceStart,
    store: StoreDep,
    voice: BrowserVoiceDep,
    settings: SettingsDep,
) -> WebVoiceSession:
    ip = request.client.host if request.client else "?"
    if not _window(request).allow(ip, settings.site_demo_starts_per_ip_per_hour, datetime.now(UTC)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "demo limit reached for now; please try again later"
        )
    info = await _demo_info(store, settings)
    if not info.voice_available:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "the live demo is unavailable right now"
        )
    session = await voice.start(
        settings.site_demo_tenant_id, body.visitor, body.name, body.page_url or settings.site_url
    )
    if session is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no demo assistant configured")
    return session


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    company: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=40)
    interest: str | None = Field(default=None, max_length=80)  # e.g. plan id, "demo", "partner"
    message: str = Field(min_length=1, max_length=4000)
    website: str | None = Field(
        default=None, max_length=200
    )  # honeypot: bots fill it, humans don't


class ContactOut(BaseModel):
    id: str
    received: bool


@public.post("/contact", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
async def contact(
    body: ContactIn, store: StoreDep, notifications: NotificationsDep, settings: SettingsDep
) -> ContactOut:
    doc_id = f"sc-{uuid4().hex[:10]}"
    if body.website:
        return ContactOut(id=doc_id, received=True)
    data = body.model_dump(exclude={"website"})
    await store.put_doc(
        TenantDoc(kind=CONTACT_KIND, id=doc_id, tenant_id=PLATFORM_TENANT, data=data)
    )
    subject = f"Website enquiry from {body.name}" + (f" ({body.company})" if body.company else "")
    text = "\n".join(
        [
            f"Name: {body.name}",
            f"Email: {body.email}",
            f"Company: {body.company or '-'}",
            f"Phone: {body.phone or '-'}",
            f"Interest: {body.interest or '-'}",
            "",
            body.message,
        ]
    )
    for owner in settings.platform_owner_emails:
        try:
            await notifications.email.send(owner, subject, text)
        except Exception as e:  # never fail the visitor because of email delivery
            log.warning("site contact email to %s failed: %s", owner, e)
    return ContactOut(id=doc_id, received=True)
