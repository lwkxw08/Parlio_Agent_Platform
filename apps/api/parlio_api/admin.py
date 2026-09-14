"""Phase 16b: platform-owner admin console.

Platform staff are memberships of the reserved ``PLATFORM_TENANT`` organisation, so they live in
the same users/memberships tables as tenant users but are never treated as tenant members
(``Principal.tenant_ids`` excludes the platform tenant). Access to anything cross-tenant goes
through ``require_staff`` (2FA outside dev mode, optional IP allow-list, role checks) and every
mutation is written to the audit log with ``meta.platform_staff = True`` so tenants can see what
Parlio staff did to their account.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import ipaddress
import logging
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .billing import (
    COUPONS,
    PLAN_BY_ID,
    PLANS,
    BillingService,
    Coupon,
    Credit,
    Invoice,
    Plan,
    Refund,
    Subscription,
    SubscriptionStatus,
    TenantLimits,
    UsageSummary,
    is_browser_call,
)
from .connectors import CONN_KIND, JOB_KIND, JobStatus
from .inbox import THREAD_KIND
from .observability import AuditEntry, RateLimiter, Telemetry
from .outbound import CALL_KIND as OUTBOUND_KIND
from .qa import SCORE_KIND
from .sip import KIND as TRUNK_KIND
from .store import CallFilter, CallRecord, CallStore, Member, TenantDoc

log = logging.getLogger("parlio.admin")

PLATFORM_TENANT = "parlio-platform"
StaffRole = Literal["owner", "support", "finance", "readonly"]
STAFF_ROLES: tuple[StaffRole, ...] = ("owner", "support", "finance", "readonly")

PLAN_KIND = "plan_override"
COUPON_KIND = "coupon"
FLAGS_KIND = "feature_flags"
NOTE_KIND = "support_note"
STATUS_KIND = "platform_status"
STAFF_SETTINGS_KIND = "staff_settings"

BUILTIN_PLANS: dict[str, Plan] = {p.id: p.model_copy(deep=True) for p in PLANS}
BUILTIN_COUPONS: dict[str, Coupon] = {c.code: c.model_copy(deep=True) for c in COUPONS.values()}

FEATURE_FLAGS: dict[str, str] = {
    "browser_voice": "Click-to-talk on the chat widget",
    "outbound": "Outbound dialer & speed-to-lead",
    "live_takeover": "Live listen / whisper / take over",
    "payments": "Mid-call payment links",
    "voice_clone": "Voice cloning (consent-gated)",
    "white_label": "White-label branding & agency accounts",
    "sovereign_uk": "UK-region model providers (Phase 15)",
    "beta_insights": "Beta insight engine features",
}


# -- models ----------------------------------------------------------------------------------------


class StaffSettings(BaseModel):
    ip_allowlist: list[str] = Field(
        default_factory=list, description="CIDRs allowed to use /admin; empty = any"
    )
    view_as_ttl_minutes: int = 60
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FeatureFlags(BaseModel):
    tenant_id: str
    flags: dict[str, bool] = Field(default_factory=dict)
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SupportNote(BaseModel):
    id: str = Field(default_factory=lambda: f"sn-{uuid4().hex[:8]}")
    tenant_id: str
    author: str
    text: str = Field(min_length=1, max_length=4000)
    pinned: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlatformStatus(BaseModel):
    level: Literal["ok", "degraded", "incident", "maintenance"] = "ok"
    title: str = ""
    message: str = ""
    link: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def active(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        if self.level == "ok":
            return False
        if self.starts_at and now < self.starts_at:
            return False
        return not (self.ends_at and now > self.ends_at)


class ViewAsGrant(BaseModel):
    token: str
    tenant_id: str
    staff_email: str
    expires_at: datetime
    read_only: bool = True


class TenantSummary(BaseModel):
    tenant_id: str
    name: str
    created_at: datetime
    plan_id: str
    plan_name: str
    status: SubscriptionStatus
    trial_ends_at: datetime | None
    assistants: int
    members: int
    numbers: int
    calls_period: int
    minutes_period: float
    minutes_included: int
    estimated_total_pence: int
    credit_balance_pence: int
    last_call_at: datetime | None
    flags: list[str] = Field(default_factory=list)
    health: Literal["healthy", "watch", "at_risk", "inactive"] = "healthy"


class AssistantBrief(BaseModel):
    id: str
    name: str
    business_name: str
    version: int | None = None
    updated_at: datetime | None = None


class TenantDetail(BaseModel):
    summary: TenantSummary
    subscription: Subscription
    usage: UsageSummary
    limits: TenantLimits
    credits: list[Credit]
    invoices: list[Invoice]
    refunds: list[Refund]
    assistants: list[AssistantBrief]
    members: list[Member]
    numbers: list[dict[str, Any]]
    trunks: list[dict[str, Any]]
    connectors: list[dict[str, Any]]
    flags: FeatureFlags
    notes: list[SupportNote]
    audit: list[AuditEntry]


class SeriesPoint(BaseModel):
    key: str
    value: float
    extra: dict[str, float] = Field(default_factory=dict)


class BusinessAnalytics(BaseModel):
    tenants: int
    signups_period: int
    trialing: int
    active: int
    past_due: int
    paused: int
    suspended: int
    cancelled: int
    conversions_period: int
    churned_period: int
    trial_conversion_pct: float | None
    plan_mix: dict[str, int]
    mrr_pence: int
    arr_pence: int
    overage_pence_period: int
    credit_outstanding_pence: int
    top_accounts: list[dict[str, Any]]
    signups_by_day: list[SeriesPoint]
    cohorts: list[dict[str, Any]]


class DemandAnalytics(BaseModel):
    calls: int
    minutes: float
    inbound: int
    outbound: int
    answered: int
    missed: int
    failed: int
    transferred: int
    ticketed: int
    channel_mix: dict[str, int]
    calls_by_day: list[SeriesPoint]
    calls_by_hour: list[SeriesPoint]
    peak_concurrency: int
    capacity_concurrent: int
    growth_pct: float | None
    forecast_calls_next_period: int


class QualityCostAnalytics(BaseModel):
    answer_latency_p50_s: float | None
    answer_latency_p95_s: float | None
    turn_latency_p50_ms: float | None
    turn_latency_p95_ms: float | None
    vendor_cost_pence: float
    revenue_pence: int
    gross_margin_pct: float | None
    cost_per_call_pence: float | None
    qa_overall_avg: float | None
    qa_by_day: list[SeriesPoint]
    low_score_calls: int
    connector_jobs: int
    connector_failed: int
    connector_failure_pct: float | None
    connectors_by_provider: dict[str, int]
    tenant_health: dict[str, int]


class PlatformAnalytics(BaseModel):
    generated_at: datetime
    days: int
    business: BusinessAnalytics
    demand: DemandAnalytics
    quality: QualityCostAnalytics


class AdminOverview(BaseModel):
    status: PlatformStatus
    analytics: PlatformAnalytics
    staff: int
    recent_audit: list[AuditEntry]


def _pct(n: float, d: float) -> float | None:
    return None if d <= 0 else round(n / d * 100, 1)


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round((len(s) - 1) * p)))
    return round(s[idx], 3)


def _health(
    summary_minutes: float, calls: int, status: SubscriptionStatus, last: datetime | None
) -> str:
    if status in (SubscriptionStatus.SUSPENDED, SubscriptionStatus.CANCELLED):
        return "inactive"
    if status == SubscriptionStatus.PAST_DUE:
        return "at_risk"
    if last is None or last < datetime.now(UTC) - timedelta(days=14):
        return "watch" if status == SubscriptionStatus.TRIALING else "at_risk"
    return "healthy"


class AdminService:
    def __init__(
        self,
        store: CallStore,
        billing: BillingService,
        telemetry: Telemetry,
        limiter: RateLimiter,
        signing_key: str,
    ) -> None:
        self.store = store
        self.billing = billing
        self.telemetry = telemetry
        self.limiter = limiter
        self._key = signing_key.encode()

    # -- startup --------------------------------------------------------------------------------
    async def load(self) -> None:
        """Apply persisted plan/coupon edits and rate-limit overrides to the in-process tables."""
        for d in await self.store.list_docs(PLAN_KIND, PLATFORM_TENANT, limit=500):
            _apply_plan(Plan.model_validate(d.data))
        for d in await self.store.list_docs(COUPON_KIND, PLATFORM_TENANT, limit=500):
            COUPONS[d.id] = Coupon.model_validate(d.data)
        for d in await self.store.list_docs(BillingService.LIMITS_KIND, None, limit=10000):
            lim = TenantLimits.model_validate(d.data)
            if lim.rate_limit_per_minute:
                self.limiter.overrides[f"tenant:{lim.tenant_id}"] = lim.rate_limit_per_minute

    async def ensure_owner(self, email: str, user_id: str, name: str | None) -> Member:
        """Bootstrap: emails in PARLIO_PLATFORM_OWNER_EMAILS become platform owners on sign-in."""
        for m in await self.store.list_members(PLATFORM_TENANT):
            if m.email.lower() == email.lower():
                if m.status != "active" or m.user_id != user_id:
                    return await self.store.upsert_member(
                        m.model_copy(update={"status": "active", "user_id": user_id})
                    )
                return m
        return await self.store.upsert_member(
            Member(
                tenant_id=PLATFORM_TENANT,
                user_id=user_id,
                email=email,
                name=name,
                role="owner",
                status="active",
            )
        )

    # -- staff ----------------------------------------------------------------------------------
    async def staff(self) -> list[Member]:
        return await self.store.list_members(PLATFORM_TENANT)

    async def invite_staff(self, email: str, role: StaffRole, name: str | None) -> Member:
        for m in await self.staff():
            if m.email.lower() == email.lower():
                return await self.store.upsert_member(m.model_copy(update={"role": role}))
        return await self.store.upsert_member(
            Member(
                tenant_id=PLATFORM_TENANT,
                user_id=f"invite:{hashlib.sha256(email.lower().encode()).hexdigest()[:16]}",
                email=email,
                name=name,
                role=role,
                status="invited",
                invited_at=datetime.now(UTC),
            )
        )

    async def set_staff_role(self, user_id: str, role: StaffRole) -> Member | None:
        for m in await self.staff():
            if m.user_id == user_id:
                return await self.store.upsert_member(m.model_copy(update={"role": role}))
        return None

    async def remove_staff(self, user_id: str) -> bool:
        return await self.store.remove_member(PLATFORM_TENANT, user_id)

    async def staff_settings(self) -> StaffSettings:
        doc = await self.store.get_doc(STAFF_SETTINGS_KIND, "current")
        return StaffSettings.model_validate(doc.data) if doc else StaffSettings()

    async def save_staff_settings(self, s: StaffSettings, by: str) -> StaffSettings:
        for cidr in s.ip_allowlist:
            ipaddress.ip_network(cidr, strict=False)  # raises ValueError on bad input
        s = s.model_copy(update={"updated_by": by, "updated_at": datetime.now(UTC)})
        await self._put(STAFF_SETTINGS_KIND, "current", PLATFORM_TENANT, s)
        return s

    async def ip_allowed(self, ip: str | None) -> bool:
        allow = (await self.staff_settings()).ip_allowlist
        if not allow:
            return True
        if not ip:
            return False
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in ipaddress.ip_network(c, strict=False) for c in allow)

    # -- view-as-tenant -------------------------------------------------------------------------
    async def issue_view_as(self, tenant_id: str, staff_email: str) -> ViewAsGrant:
        ttl = (await self.staff_settings()).view_as_ttl_minutes
        exp = int(time.time()) + ttl * 60
        body = f"{tenant_id}|{staff_email}|{exp}"
        sig = hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()[:32]
        return ViewAsGrant(
            token=f"{body}|{sig}",
            tenant_id=tenant_id,
            staff_email=staff_email,
            expires_at=datetime.fromtimestamp(exp, UTC),
        )

    def verify_view_as(self, token: str | None) -> ViewAsGrant | None:
        if not token or token.count("|") != 3:
            return None
        tenant_id, email, exp_s, sig = token.split("|")
        body = f"{tenant_id}|{email}|{exp_s}"
        want = hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(want, sig):
            return None
        try:
            exp = int(exp_s)
        except ValueError:
            return None
        if exp < time.time():
            return None
        return ViewAsGrant(
            token=token,
            tenant_id=tenant_id,
            staff_email=email,
            expires_at=datetime.fromtimestamp(exp, UTC),
        )

    # -- tenant directory -----------------------------------------------------------------------
    async def tenant_ids(self) -> list[str]:
        ids: set[str] = set()
        for a in await self.store.list_assistants(None):
            ids.add(a.tenant_id)
        for s in await self.billing.all_subscriptions():
            ids.add(s.tenant_id)
        ids.discard(PLATFORM_TENANT)
        return sorted(ids)

    async def tenant_summary(self, tenant_id: str) -> TenantSummary:
        assistants = await self.store.list_assistants(tenant_id)
        sub = await self.billing.subscription(tenant_id)
        usage = await self.billing.usage(tenant_id)
        members = await self.store.list_members(tenant_id)
        numbers = await self.billing.list_numbers(tenant_id)
        recent = await self.store.filter_calls(CallFilter(tenant_id=tenant_id, limit=1))
        last = recent[0].started_at if recent else None
        flags = await self.flags(tenant_id)
        name = assistants[0].business_name if assistants else tenant_id
        return TenantSummary(
            tenant_id=tenant_id,
            name=name,
            created_at=sub.created_at,
            plan_id=sub.plan_id,
            plan_name=sub.plan.name,
            status=sub.status,
            trial_ends_at=sub.trial_ends_at,
            assistants=len(assistants),
            members=len(members),
            numbers=len(numbers),
            calls_period=usage.calls,
            minutes_period=round(usage.minutes_used, 1),
            minutes_included=usage.minutes_included,
            estimated_total_pence=usage.estimated_total_pence,
            credit_balance_pence=usage.credit_balance_pence,
            last_call_at=last,
            flags=sorted(k for k, v in flags.flags.items() if v),
            health=_health(usage.minutes_used, usage.calls, sub.status, last),
        )

    async def tenants(
        self, q: str | None = None, status: str | None = None, plan_id: str | None = None
    ) -> list[TenantSummary]:
        out: list[TenantSummary] = []
        ql = (q or "").lower().strip()
        for tid in await self.tenant_ids():
            s = await self.tenant_summary(tid)
            if ql and ql not in tid.lower() and ql not in s.name.lower():
                continue
            if status and s.status != status:
                continue
            if plan_id and s.plan_id != plan_id:
                continue
            out.append(s)
        out.sort(key=lambda s: s.created_at, reverse=True)
        return out

    async def tenant_detail(self, tenant_id: str) -> TenantDetail:
        summary = await self.tenant_summary(tenant_id)
        assistants = await self.store.list_assistants(tenant_id)
        trunks = await self.store.list_docs(TRUNK_KIND, tenant_id, limit=50)
        conns = await self.store.list_docs(CONN_KIND, tenant_id, limit=100)
        audit = await self.store.list_docs("audit", tenant_id, limit=1000)
        return TenantDetail(
            summary=summary,
            subscription=await self.billing.subscription(tenant_id),
            usage=await self.billing.usage(tenant_id),
            limits=await self.billing.limits(tenant_id),
            credits=await self.billing.credits(tenant_id),
            invoices=await self.billing.invoices(tenant_id),
            refunds=await self.billing.refunds(tenant_id),
            assistants=[
                AssistantBrief(
                    id=a.assistant_id,
                    name=a.name,
                    business_name=a.business_name,
                    version=a.assistant_version,
                )
                for a in assistants
            ],
            members=await self.store.list_members(tenant_id),
            numbers=[n.model_dump(mode="json") for n in await self.billing.list_numbers(tenant_id)],
            trunks=[
                {k: d.data.get(k) for k in ("id", "name", "mode", "status", "enabled")}
                for d in trunks
            ],
            connectors=[
                {k: d.data.get(k) for k in ("id", "name", "provider", "enabled")} for d in conns
            ],
            flags=await self.flags(tenant_id),
            notes=await self.notes(tenant_id),
            audit=sorted(
                (AuditEntry.model_validate(d.data) for d in audit),
                key=lambda e: e.at,
                reverse=True,
            )[:25],
        )

    # -- flags / notes / limits -----------------------------------------------------------------
    async def flags(self, tenant_id: str) -> FeatureFlags:
        doc = await self.store.get_doc(FLAGS_KIND, tenant_id)
        return FeatureFlags.model_validate(doc.data) if doc else FeatureFlags(tenant_id=tenant_id)

    async def set_flags(self, tenant_id: str, flags: dict[str, bool], by: str) -> FeatureFlags:
        unknown = sorted(set(flags) - set(FEATURE_FLAGS))
        if unknown:
            raise ValueError(f"unknown feature flag(s): {', '.join(unknown)}")
        cur = await self.flags(tenant_id)
        cur = cur.model_copy(
            update={
                "flags": {**cur.flags, **flags},
                "updated_by": by,
                "updated_at": datetime.now(UTC),
            }
        )
        await self._put(FLAGS_KIND, tenant_id, tenant_id, cur)
        return cur

    async def notes(self, tenant_id: str) -> list[SupportNote]:
        docs = await self.store.list_docs(NOTE_KIND, tenant_id, limit=500)
        notes = [SupportNote.model_validate(d.data) for d in docs]
        notes.sort(key=lambda n: (not n.pinned, -n.created_at.timestamp()))
        return notes

    async def add_note(self, tenant_id: str, author: str, text: str, pinned: bool) -> SupportNote:
        n = SupportNote(tenant_id=tenant_id, author=author, text=text, pinned=pinned)
        await self._put(NOTE_KIND, n.id, tenant_id, n)
        return n

    async def delete_note(self, tenant_id: str, note_id: str) -> bool:
        doc = await self.store.get_doc(NOTE_KIND, note_id)
        if doc is None or doc.tenant_id != tenant_id:
            return False
        return await self.store.delete_doc(NOTE_KIND, note_id)

    async def set_limits(self, limits: TenantLimits) -> TenantLimits:
        saved = await self.billing.set_limits(limits)
        key = f"tenant:{limits.tenant_id}"
        if limits.rate_limit_per_minute:
            self.limiter.overrides[key] = limits.rate_limit_per_minute
        else:
            self.limiter.overrides.pop(key, None)
        return saved

    # -- plan / coupon catalogue ----------------------------------------------------------------
    def plans(self) -> list[Plan]:
        return list(PLANS)

    async def save_plan(self, plan: Plan) -> Plan:
        _apply_plan(plan)
        await self._put(PLAN_KIND, plan.id, PLATFORM_TENANT, plan)
        return plan

    async def delete_plan(self, plan_id: str) -> Plan | None:
        """Custom plans are removed; built-ins revert to their shipped definition."""
        in_use = [s for s in await self.billing.all_subscriptions() if s.plan_id == plan_id]
        if in_use:
            raise ValueError(f"{len(in_use)} subscription(s) still on {plan_id}")
        await self.store.delete_doc(PLAN_KIND, plan_id)
        if plan_id in BUILTIN_PLANS:
            builtin = BUILTIN_PLANS[plan_id].model_copy(deep=True)
            _apply_plan(builtin)
            return builtin
        PLAN_BY_ID.pop(plan_id, None)
        PLANS[:] = [p for p in PLANS if p.id != plan_id]
        return None

    def coupons(self) -> list[Coupon]:
        return sorted(COUPONS.values(), key=lambda c: c.code)

    async def save_coupon(self, c: Coupon) -> Coupon:
        c = c.model_copy(update={"code": c.code.upper()})
        COUPONS[c.code] = c
        await self._put(COUPON_KIND, c.code, PLATFORM_TENANT, c)
        return c

    async def delete_coupon(self, code: str) -> bool:
        code = code.upper()
        await self.store.delete_doc(COUPON_KIND, code)
        if code in BUILTIN_COUPONS:
            COUPONS[code] = BUILTIN_COUPONS[code].model_copy(deep=True)
            return True
        return COUPONS.pop(code, None) is not None

    # -- platform status ------------------------------------------------------------------------
    async def status(self) -> PlatformStatus:
        doc = await self.store.get_doc(STATUS_KIND, "current")
        return PlatformStatus.model_validate(doc.data) if doc else PlatformStatus()

    async def set_status(self, s: PlatformStatus, by: str) -> PlatformStatus:
        s = s.model_copy(update={"updated_by": by, "updated_at": datetime.now(UTC)})
        await self._put(STATUS_KIND, "current", PLATFORM_TENANT, s)
        return s

    # -- audit ----------------------------------------------------------------------------------
    async def staff_activity(self, limit: int = 200) -> list[AuditEntry]:
        docs = await self.store.list_docs("audit", None, limit=20000)
        entries = [
            e
            for e in (AuditEntry.model_validate(d.data) for d in docs)
            if e.meta.get("platform_staff") or e.tenant_id == PLATFORM_TENANT
        ]
        entries.sort(key=lambda e: e.at, reverse=True)
        return entries[:limit]

    # -- analytics ------------------------------------------------------------------------------
    async def analytics(self, days: int = 30) -> PlatformAnalytics:
        days = min(max(days, 1), 365)
        now = datetime.now(UTC)
        since = now - timedelta(days=days)
        prev_since = since - timedelta(days=days)
        subs = [s for s in await self.billing.all_subscriptions() if s.tenant_id != PLATFORM_TENANT]
        tenant_ids = await self.tenant_ids()
        sub_by_tenant = {s.tenant_id: s for s in subs}
        usages: dict[str, UsageSummary] = {}
        for tid in tenant_ids:
            usages[tid] = await self.billing.usage(tid)
            if tid not in sub_by_tenant:
                sub_by_tenant[tid] = await self.billing.subscription(tid)
        subs = list(sub_by_tenant.values())

        calls = await self.store.filter_calls(
            CallFilter(tenant_id=None, since=prev_since, until=now, limit=200000)
        )
        calls = [c for c in calls if c.tenant_id != PLATFORM_TENANT]
        cur_calls = [c for c in calls if c.started_at >= since]
        prev_calls = [c for c in calls if c.started_at < since]

        business = self._business(subs, usages, cur_calls, since, now, days)
        demand = await self._demand(cur_calls, prev_calls, subs, since, days)
        quality = await self._quality(cur_calls, usages, since, tenant_ids)
        return PlatformAnalytics(
            generated_at=now, days=days, business=business, demand=demand, quality=quality
        )

    def _business(
        self,
        subs: list[Subscription],
        usages: dict[str, UsageSummary],
        calls: list[CallRecord],
        since: datetime,
        now: datetime,
        days: int,
    ) -> BusinessAnalytics:
        by_status = Counter(s.status for s in subs)
        plan_mix = Counter(s.plan_id for s in subs)
        mrr = 0
        for s in subs:
            if s.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE):
                u = usages.get(s.tenant_id)
                mrr += (u.base_pence - u.discount_pence) if u else s.plan.monthly_pence
        overage = sum(
            u.overage_pence + u.sms_overage_pence + u.chat_overage_pence for u in usages.values()
        )
        credit_out = sum(u.credit_balance_pence for u in usages.values())
        signups = [s for s in subs if s.created_at >= since]
        conversions = [
            s
            for s in subs
            if s.status == SubscriptionStatus.ACTIVE
            and s.trial_ends_at is None
            and s.created_at >= since - timedelta(days=90)
        ]
        churned = [
            s for s in subs if s.status == SubscriptionStatus.CANCELLED and s.period_start >= since
        ]
        trials_started = [s for s in subs if s.created_at >= since - timedelta(days=90)]
        top = sorted(
            (
                {
                    "tenant_id": tid,
                    "plan_id": u.plan.id,
                    "minutes": round(u.minutes_used, 1),
                    "estimated_total_pence": u.estimated_total_pence,
                    "status": u.status,
                }
                for tid, u in usages.items()
            ),
            key=lambda r: int(str(r["estimated_total_pence"])),
            reverse=True,
        )[:10]
        by_day: Counter[str] = Counter()
        for s in signups:
            by_day[s.created_at.strftime("%Y-%m-%d")] += 1
        series = [
            SeriesPoint(key=(since + timedelta(days=i)).strftime("%Y-%m-%d"), value=0)
            for i in range(days + 1)
        ]
        for pt in series:
            pt.value = by_day.get(pt.key, 0)
        cohorts: list[dict[str, Any]] = []
        by_month: dict[str, list[Subscription]] = defaultdict(list)
        for s in subs:
            by_month[s.created_at.strftime("%Y-%m")].append(s)
        for month in sorted(by_month)[-12:]:
            group = by_month[month]
            retained = [
                s
                for s in group
                if s.status
                in (
                    SubscriptionStatus.ACTIVE,
                    SubscriptionStatus.TRIALING,
                    SubscriptionStatus.PAUSED,
                )
            ]
            cohorts.append(
                {
                    "cohort": month,
                    "signups": len(group),
                    "retained": len(retained),
                    "retention_pct": _pct(len(retained), len(group)),
                }
            )
        return BusinessAnalytics(
            tenants=len(subs),
            signups_period=len(signups),
            trialing=by_status[SubscriptionStatus.TRIALING],
            active=by_status[SubscriptionStatus.ACTIVE],
            past_due=by_status[SubscriptionStatus.PAST_DUE],
            paused=by_status[SubscriptionStatus.PAUSED],
            suspended=by_status[SubscriptionStatus.SUSPENDED],
            cancelled=by_status[SubscriptionStatus.CANCELLED],
            conversions_period=len(conversions),
            churned_period=len(churned),
            trial_conversion_pct=_pct(len(conversions), len(trials_started)),
            plan_mix=dict(plan_mix),
            mrr_pence=mrr,
            arr_pence=mrr * 12,
            overage_pence_period=overage,
            credit_outstanding_pence=credit_out,
            top_accounts=top,
            signups_by_day=series,
            cohorts=cohorts,
        )

    async def _demand(
        self,
        calls: list[CallRecord],
        prev: list[CallRecord],
        subs: list[Subscription],
        since: datetime,
        days: int,
    ) -> DemandAnalytics:
        outbound_docs = await self.store.list_docs(OUTBOUND_KIND, None, limit=50000)
        outbound = sum(
            1
            for d in outbound_docs
            if d.created_at >= since and d.data.get("status") == "completed"
        )
        threads = await self.store.list_docs(THREAD_KIND, None, limit=50000)
        channel: Counter[str] = Counter()
        for c in calls:
            channel["browser_voice" if is_browser_call(c) else "phone"] += 1
        for t in threads:
            if t.created_at >= since:
                channel[str(t.data.get("channel", "chat"))] += 1
        kinds = Counter(c.kind for c in calls)
        failed = sum(1 for c in calls if c.end_reason in ("error", "failed"))
        by_day: Counter[str] = Counter()
        mins_day: defaultdict[str, float] = defaultdict(float)
        by_hour: Counter[int] = Counter()
        for c in calls:
            k = c.started_at.strftime("%Y-%m-%d")
            by_day[k] += 1
            mins_day[k] += (c.duration_s or 0) / 60
            by_hour[c.started_at.hour] += 1
        calls_by_day = [
            SeriesPoint(
                key=(since + timedelta(days=i)).strftime("%Y-%m-%d"),
                value=by_day.get((since + timedelta(days=i)).strftime("%Y-%m-%d"), 0),
                extra={
                    "minutes": round(
                        mins_day.get((since + timedelta(days=i)).strftime("%Y-%m-%d"), 0), 1
                    )
                },
            )
            for i in range(days + 1)
        ]
        calls_by_hour = [SeriesPoint(key=f"{h:02d}", value=by_hour.get(h, 0)) for h in range(24)]
        # peak concurrency: sweep call intervals
        events: list[tuple[datetime, int]] = []
        for c in calls:
            end = c.ended_at or (c.started_at + timedelta(seconds=c.duration_s or 0))
            events.append((c.started_at, 1))
            events.append((end, -1))
        events.sort()
        peak = cur = 0
        for _, delta in events:
            cur += delta
            peak = max(peak, cur)
        capacity = 0
        for s in subs:
            if s.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING):
                lim = await self.billing.limits(s.tenant_id)
                capacity += lim.max_concurrent_calls or s.plan.max_concurrent_calls
        growth = _pct(len(calls) - len(prev), len(prev)) if prev else None
        recent = sum(
            by_day.get((since + timedelta(days=i)).strftime("%Y-%m-%d"), 0)
            for i in range(max(0, days - 6), days + 1)
        )
        forecast = round(recent / min(7, days) * days) if calls else 0
        return DemandAnalytics(
            calls=len(calls),
            minutes=round(sum((c.duration_s or 0) for c in calls) / 60, 1),
            inbound=sum(1 for c in calls if c.direction == "inbound"),
            outbound=sum(1 for c in calls if c.direction != "inbound") + outbound,
            answered=kinds.get("answered", 0)
            + kinds.get("transferred", 0)
            + kinds.get("ticketed", 0),
            missed=kinds.get("missed", 0),
            failed=failed,
            transferred=kinds.get("transferred", 0),
            ticketed=kinds.get("ticketed", 0),
            channel_mix=dict(channel),
            calls_by_day=calls_by_day,
            calls_by_hour=calls_by_hour,
            peak_concurrency=peak,
            capacity_concurrent=capacity,
            growth_pct=growth,
            forecast_calls_next_period=forecast,
        )

    async def _quality(
        self,
        calls: list[CallRecord],
        usages: dict[str, UsageSummary],
        since: datetime,
        tenant_ids: list[str],
    ) -> QualityCostAnalytics:
        answer = [c.answer_latency_s for c in calls if c.answer_latency_s is not None]
        turns: list[float] = []
        for c in calls:
            v = c.latency.get("turn_p50_ms") or c.latency.get("p50_ms")
            if isinstance(v, int | float):
                turns.append(float(v))
        vendor = sum(u.vendor_cost_pence for u in usages.values())
        revenue = sum(u.estimated_total_pence for u in usages.values())
        scores = await self.store.list_docs(SCORE_KIND, None, limit=50000)
        scores = [s for s in scores if s.created_at >= since]
        qa_day: dict[str, list[float]] = defaultdict(list)
        overall: list[float] = []
        low = 0
        for s in scores:
            v = s.data.get("overall")
            if isinstance(v, int | float):
                overall.append(float(v))
                qa_day[s.created_at.strftime("%Y-%m-%d")].append(float(v))
                if v < 5:
                    low += 1
        qa_series = [
            SeriesPoint(key=k, value=round(sum(v) / len(v), 2), extra={"scored": len(v)})
            for k, v in sorted(qa_day.items())
        ]
        jobs = await self.store.list_docs(JOB_KIND, None, limit=50000)
        jobs = [j for j in jobs if j.created_at >= since]
        failed_jobs = sum(1 for j in jobs if j.data.get("status") == JobStatus.FAILED)
        by_provider: Counter[str] = Counter(str(j.data.get("provider", "?")) for j in jobs)
        health: Counter[str] = Counter()
        for tid in tenant_ids:
            health[(await self.tenant_summary(tid)).health] += 1
        return QualityCostAnalytics(
            answer_latency_p50_s=_percentile(answer, 0.5),
            answer_latency_p95_s=_percentile(answer, 0.95),
            turn_latency_p50_ms=_percentile(turns, 0.5),
            turn_latency_p95_ms=_percentile(turns, 0.95),
            vendor_cost_pence=round(vendor, 1),
            revenue_pence=revenue,
            gross_margin_pct=None if revenue <= 0 else round((revenue - vendor) / revenue * 100, 1),
            cost_per_call_pence=None if not calls else round(vendor / len(calls), 2),
            qa_overall_avg=None if not overall else round(sum(overall) / len(overall), 2),
            qa_by_day=qa_series,
            low_score_calls=low,
            connector_jobs=len(jobs),
            connector_failed=failed_jobs,
            connector_failure_pct=_pct(failed_jobs, len(jobs)),
            connectors_by_provider=dict(by_provider),
            tenant_health=dict(health),
        )

    # -- CSV ------------------------------------------------------------------------------------
    async def tenants_csv(self) -> str:
        rows = await self.tenants()
        buf = io.StringIO()
        w = csv.writer(buf)
        cols = list(TenantSummary.model_fields)
        w.writerow(cols)
        for r in rows:
            d = r.model_dump(mode="json")
            w.writerow([",".join(d[c]) if isinstance(d[c], list) else d[c] for c in cols])
        return buf.getvalue()

    async def analytics_csv(self, days: int) -> str:
        a = await self.analytics(days)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["day", "signups", "calls", "minutes", "qa_avg"])
        qa = {p.key: p.value for p in a.quality.qa_by_day}
        signups = {p.key: p.value for p in a.business.signups_by_day}
        for p in a.demand.calls_by_day:
            w.writerow(
                [
                    p.key,
                    int(signups.get(p.key, 0)),
                    int(p.value),
                    p.extra.get("minutes", 0),
                    qa.get(p.key, ""),
                ]
            )
        return buf.getvalue()

    # -- helpers --------------------------------------------------------------------------------
    async def _put(self, kind: str, doc_id: str, tenant_id: str, model: BaseModel) -> None:
        await self.store.put_doc(
            TenantDoc(kind=kind, id=doc_id, tenant_id=tenant_id, data=model.model_dump(mode="json"))
        )


def _apply_plan(plan: Plan) -> None:
    PLAN_BY_ID[plan.id] = plan
    for i, p in enumerate(PLANS):
        if p.id == plan.id:
            PLANS[i] = plan
            return
    PLANS.append(plan)
