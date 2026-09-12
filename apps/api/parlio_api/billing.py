"""Phase 6 billing: plans, coupons, subscriptions, minute metering, overage, number provisioning.

Money is integer pence. The provider seam (`BillingProvider`) is Stripe-shaped (customer,
checkout session, metered usage record, webhook) but everything works offline with
`SimulatedBilling`, which is also what tests and demo mode use. Usage is derived from the call /
message stores rather than a separate counter so it can never drift from what the customer sees.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from parlio_api.messaging import MessageService, MessageStatus
from parlio_api.store import CallFilter, CallRecord, CallStore, TenantDoc
from parlio_api.telephony.base import CarrierHealth, CarrierStatus, PhoneNumber, TelephonyProvider

log = logging.getLogger("parlio.billing")


# -- catalogue --------------------------------------------------------------------------------


class Plan(BaseModel):
    id: str
    name: str
    monthly_pence: int
    included_minutes: int
    overage_pence_per_minute: int
    included_numbers: int
    included_sms: int
    sms_overage_pence: int
    max_assistants: int
    max_concurrent_calls: int
    features: list[str] = Field(default_factory=list)
    enterprise: bool = False


PLANS: list[Plan] = [
    Plan(
        id="starter",
        name="Starter",
        monthly_pence=4900,
        included_minutes=300,
        overage_pence_per_minute=15,
        included_numbers=1,
        included_sms=100,
        sms_overage_pence=6,
        max_assistants=1,
        max_concurrent_calls=2,
        features=["1 assistant", "Tickets & callbacks", "Email/SMS alerts"],
    ),
    Plan(
        id="growth",
        name="Growth",
        monthly_pence=14900,
        included_minutes=1200,
        overage_pence_per_minute=12,
        included_numbers=3,
        included_sms=500,
        sms_overage_pence=5,
        max_assistants=3,
        max_concurrent_calls=5,
        features=["3 assistants", "Calendar booking", "Warm transfers", "Analytics Ask AI"],
    ),
    Plan(
        id="scale",
        name="Scale",
        monthly_pence=39900,
        included_minutes=4000,
        overage_pence_per_minute=10,
        included_numbers=10,
        included_sms=2000,
        sms_overage_pence=4,
        max_assistants=10,
        max_concurrent_calls=15,
        features=["10 assistants", "BYO SIP / PBX", "Slack & webhooks", "Priority support"],
    ),
    Plan(
        id="enterprise",
        name="Enterprise",
        monthly_pence=0,
        included_minutes=0,
        overage_pence_per_minute=8,
        included_numbers=50,
        included_sms=10000,
        sms_overage_pence=3,
        max_assistants=100,
        max_concurrent_calls=100,
        features=["UK-sovereign deployment", "SSO", "Custom SLAs", "Dedicated capacity"],
        enterprise=True,
    ),
]
PLAN_BY_ID = {p.id: p for p in PLANS}


class Coupon(BaseModel):
    code: str
    percent_off: int | None = None
    amount_off_pence: int | None = None
    months: int | None = None  # None = forever
    expires_at: datetime | None = None
    plans: list[str] = Field(default_factory=list)  # empty = any plan

    def valid_for(self, plan_id: str, now: datetime) -> bool:
        if self.expires_at and now >= self.expires_at:
            return False
        return not self.plans or plan_id in self.plans

    def apply(self, pence: int) -> int:
        if self.percent_off:
            pence -= pence * self.percent_off // 100
        if self.amount_off_pence:
            pence -= self.amount_off_pence
        return max(0, pence)


COUPONS: dict[str, Coupon] = {
    "LAUNCH50": Coupon(code="LAUNCH50", percent_off=50, months=3),
    "FOUNDER": Coupon(code="FOUNDER", percent_off=20),
}


class SubscriptionStatus(StrEnum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"


class Subscription(BaseModel):
    tenant_id: str
    plan_id: str = "starter"
    status: SubscriptionStatus = SubscriptionStatus.TRIALING
    period_start: datetime
    period_end: datetime
    coupon: str | None = None
    coupon_months_left: int | None = None
    provider: str = "simulated"
    customer_ref: str | None = None
    subscription_ref: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def plan(self) -> Plan:
        return PLAN_BY_ID.get(self.plan_id, PLANS[0])


# -- usage / cost metering ------------------------------------------------------------------------


class CostRates(BaseModel):
    """Per-minute vendor costs used for margin tracking (pence). Overridable via settings."""

    stt_pence_per_min: float = 0.6  # Deepgram Nova streaming
    tts_pence_per_min: float = 1.2  # Cartesia
    llm_pence_per_min: float = 0.4  # gpt-4o-mini class at ~400 tok/turn
    telephony_pence_per_min: float = 0.8  # Telnyx UK inbound + SIP
    sms_pence: float = 3.5


class CallCost(BaseModel):
    call_id: str
    minutes: float
    vendor_pence: float
    billable_pence: int


class UsageSummary(BaseModel):
    tenant_id: str
    plan: Plan
    status: SubscriptionStatus
    period_start: datetime
    period_end: datetime
    calls: int
    minutes_used: float
    minutes_included: int
    minutes_overage: float
    overage_pence: int
    sms_used: int
    sms_included: int
    sms_overage_pence: int
    numbers_used: int
    numbers_included: int
    base_pence: int
    discount_pence: int
    estimated_total_pence: int
    vendor_cost_pence: float
    gross_margin_pct: float | None
    per_day_minutes: dict[str, float]
    top_calls: list[CallCost]


def billable_minutes(call: CallRecord) -> float:
    """Answered seconds rounded up to the next 6-second block, in minutes (UK carrier norm)."""
    if call.answered_at is None or not call.duration_s or call.duration_s <= 0:
        return 0.0
    blocks = int((call.duration_s + 5.999) // 6)
    return round(blocks * 6 / 60, 2)


# -- provider seam ------------------------------------------------------------------------------


class CheckoutSession(BaseModel):
    url: str
    provider: str
    session_ref: str


class BillingProvider(Protocol):
    name: str

    async def ensure_customer(self, tenant_id: str, email: str | None) -> str: ...
    async def checkout(
        self, customer_ref: str, plan: Plan, success_url: str, cancel_url: str
    ) -> CheckoutSession: ...
    async def report_usage(self, subscription_ref: str, overage_minutes: float) -> None: ...
    def verify_webhook(self, payload: bytes, signature: str | None) -> dict[str, Any]: ...


class SimulatedBilling:
    """Offline provider: checkout 'succeeds' immediately via a dashboard return URL."""

    name = "simulated"

    def __init__(self) -> None:
        self.usage_reports: list[tuple[str, float]] = []

    async def ensure_customer(self, tenant_id: str, email: str | None) -> str:
        return f"cus_sim_{tenant_id}"

    async def checkout(
        self, customer_ref: str, plan: Plan, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        return CheckoutSession(
            url=f"{success_url}?simulated=1&plan={plan.id}",
            provider=self.name,
            session_ref=f"cs_sim_{uuid4().hex[:10]}",
        )

    async def report_usage(self, subscription_ref: str, overage_minutes: float) -> None:
        self.usage_reports.append((subscription_ref, overage_minutes))

    def verify_webhook(self, payload: bytes, signature: str | None) -> dict[str, Any]:
        return dict(json.loads(payload or b"{}"))


class StripeBilling:
    """Stripe via REST (no SDK). Prices are looked up by `lookup_key = parlio_<plan_id>`."""

    name = "stripe"

    def __init__(
        self,
        secret_key: str,
        webhook_secret: str | None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._webhook_secret = webhook_secret
        self._http = client or httpx.AsyncClient(
            base_url="https://api.stripe.com/v1",
            auth=(secret_key, ""),
            timeout=15,
        )

    async def ensure_customer(self, tenant_id: str, email: str | None) -> str:
        r = await self._http.post(
            "/customers",
            data={"metadata[tenant_id]": tenant_id, **({"email": email} if email else {})},
        )
        r.raise_for_status()
        return str(r.json()["id"])

    async def checkout(
        self, customer_ref: str, plan: Plan, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        r = await self._http.post(
            "/checkout/sessions",
            data={
                "mode": "subscription",
                "customer": customer_ref,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "line_items[0][price_data][currency]": "gbp",
                "line_items[0][price_data][unit_amount]": str(plan.monthly_pence),
                "line_items[0][price_data][recurring][interval]": "month",
                "line_items[0][price_data][product_data][name]": f"Parlio {plan.name}",
                "line_items[0][quantity]": "1",
                "allow_promotion_codes": "true",
                "metadata[plan_id]": plan.id,
                "subscription_data[metadata][plan_id]": plan.id,
            },
        )
        r.raise_for_status()
        body = r.json()
        return CheckoutSession(url=str(body["url"]), provider=self.name, session_ref=body["id"])

    async def report_usage(self, subscription_ref: str, overage_minutes: float) -> None:
        # Overage is invoiced as a one-off invoice item at period close (simple, auditable).
        if overage_minutes <= 0:
            return
        r = await self._http.get(f"/subscriptions/{subscription_ref}")
        r.raise_for_status()
        sub = r.json()
        plan = PLAN_BY_ID.get(sub.get("metadata", {}).get("plan_id", ""), PLANS[0])
        pence = round(overage_minutes * plan.overage_pence_per_minute)
        r = await self._http.post(
            "/invoiceitems",
            data={
                "customer": sub["customer"],
                "subscription": subscription_ref,
                "currency": "gbp",
                "amount": str(pence),
                "description": f"Parlio overage: {overage_minutes:.0f} min",
            },
        )
        r.raise_for_status()

    def verify_webhook(self, payload: bytes, signature: str | None) -> dict[str, Any]:
        if not self._webhook_secret:
            raise ValueError("webhook secret not configured")
        if not signature:
            raise ValueError("missing Stripe-Signature")
        parts = dict(p.split("=", 1) for p in signature.split(",") if "=" in p)
        ts = parts.get("t")
        v1 = parts.get("v1")
        if not ts or not v1:
            raise ValueError("malformed Stripe-Signature")
        if abs(time.time() - int(ts)) > 300:
            raise ValueError("stale webhook")
        expected = hmac.new(
            self._webhook_secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, v1):
            raise ValueError("bad webhook signature")
        return dict(json.loads(payload))


# -- numbers ------------------------------------------------------------------------------------


class TenantNumber(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    tenant_id: str
    company_id: str
    e164: str
    country: str = "GB"
    provider: str
    provider_ref: str | None = None
    assistant_id: str
    label: str | None = None
    monthly_pence: int = 100
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SimulatedNumbers(TelephonyProvider):
    """Hands out fake UK 020 numbers so provisioning is testable without carrier credit."""

    name = "simulated"

    def __init__(self) -> None:
        self._n = 0

    async def search_numbers(self, country: str = "GB", limit: int = 5) -> list[PhoneNumber]:
        out = []
        for i in range(limit):
            self._n += 1
            out.append(
                PhoneNumber(
                    provider=self.name,
                    e164=f"+4420{7000000 + self._n * 37 + i:07d}",
                    country=country,
                )
            )
        return out

    async def purchase_number(self, e164: str) -> PhoneNumber:
        return PhoneNumber(provider=self.name, e164=e164, country="GB", provider_ref=f"sim_{e164}")

    async def route_number_to_trunk(self, number: PhoneNumber, sip_uri: str) -> PhoneNumber:
        return number.model_copy(update={"sip_trunk_ref": sip_uri})

    async def release_number(self, number: PhoneNumber) -> None:
        return None

    async def send_sms(self, from_e164: str, to_e164: str, body: str) -> str:
        return f"sim-{uuid4().hex[:8]}"

    async def health(self) -> CarrierHealth:
        return CarrierHealth(provider=self.name, status=CarrierStatus.HEALTHY, detail="simulated")


# -- service -------------------------------------------------------------------------------------


def _period(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt


class BillingService:
    KIND = "subscription"
    NUMBER_KIND = "number"

    def __init__(
        self,
        store: CallStore,
        sms: MessageService,
        provider: BillingProvider,
        numbers: TelephonyProvider,
        rates: CostRates | None = None,
        sip_uri: str = "sip:parlio.local",
        trial_days: int = 14,
    ) -> None:
        self.store = store
        self.sms = sms
        self.provider = provider
        self.numbers = numbers
        self.rates = rates or CostRates()
        self.sip_uri = sip_uri
        self.trial_days = trial_days

    # subscriptions
    async def subscription(self, tenant_id: str) -> Subscription:
        doc = await self.store.get_doc(self.KIND, tenant_id)
        if doc is not None:
            sub = Subscription.model_validate(doc.data)
            if datetime.now(UTC) >= sub.period_end:
                sub = await self._roll_period(sub)
            return sub
        now = datetime.now(UTC)
        start, end = _period(now)
        sub = Subscription(
            tenant_id=tenant_id,
            period_start=start,
            period_end=max(end, now + timedelta(days=self.trial_days)),
            provider=self.provider.name,
        )
        await self._save(sub)
        return sub

    async def _save(self, sub: Subscription) -> Subscription:
        await self.store.put_doc(
            TenantDoc(
                kind=self.KIND,
                id=sub.tenant_id,
                tenant_id=sub.tenant_id,
                data=sub.model_dump(mode="json"),
            )
        )
        return sub

    async def _roll_period(self, sub: Subscription) -> Subscription:
        """Close the period: report overage to the provider, then advance the window."""
        usage = await self._usage_for(sub)
        if sub.subscription_ref and usage.minutes_overage > 0:
            try:
                await self.provider.report_usage(sub.subscription_ref, usage.minutes_overage)
            except Exception:
                log.warning("overage report failed for %s", sub.tenant_id, exc_info=True)
        start, end = _period(datetime.now(UTC))
        left = sub.coupon_months_left
        if left is not None:
            left -= 1
        coupon = sub.coupon if left is None or left > 0 else None
        status = sub.status
        if status == SubscriptionStatus.TRIALING:
            status = SubscriptionStatus.PAST_DUE if not sub.subscription_ref else status
        return await self._save(
            sub.model_copy(
                update={
                    "period_start": start,
                    "period_end": end,
                    "coupon": coupon,
                    "coupon_months_left": left if coupon else None,
                    "status": status,
                }
            )
        )

    async def change_plan(
        self, tenant_id: str, plan_id: str, coupon_code: str | None = None
    ) -> Subscription:
        if plan_id not in PLAN_BY_ID:
            raise ValueError(f"unknown plan {plan_id}")
        plan = PLAN_BY_ID[plan_id]
        if plan.enterprise:
            raise ValueError("enterprise plans are set up by Parlio; contact sales")
        assistants = await self.store.list_assistants(tenant_id)
        if len(assistants) > plan.max_assistants:
            raise ValueError(
                f"{plan.name} allows {plan.max_assistants} assistant(s); you have {len(assistants)}"
            )
        numbers = await self.list_numbers(tenant_id)
        if len(numbers) > plan.included_numbers:
            raise ValueError(
                f"{plan.name} includes {plan.included_numbers} number(s); release some first"
            )
        sub = await self.subscription(tenant_id)
        upd: dict[str, Any] = {"plan_id": plan_id}
        if coupon_code is not None:
            c = self.coupon(coupon_code, plan_id)
            if c is None:
                raise ValueError("invalid or expired coupon")
            upd["coupon"] = c.code
            upd["coupon_months_left"] = c.months
        return await self._save(sub.model_copy(update=upd))

    def coupon(self, code: str, plan_id: str) -> Coupon | None:
        c = COUPONS.get(code.strip().upper())
        if c is None or not c.valid_for(plan_id, datetime.now(UTC)):
            return None
        return c

    async def checkout(
        self, tenant_id: str, plan_id: str, email: str | None, return_url: str
    ) -> CheckoutSession:
        sub = await self.change_plan(tenant_id, plan_id)
        customer = sub.customer_ref or await self.provider.ensure_customer(tenant_id, email)
        session = await self.provider.checkout(
            customer, sub.plan, f"{return_url}?checkout=success", f"{return_url}?checkout=cancel"
        )
        upd: dict[str, Any] = {"customer_ref": customer, "provider": self.provider.name}
        if self.provider.name == "simulated":
            upd["status"] = SubscriptionStatus.ACTIVE
            upd["subscription_ref"] = session.session_ref
        await self._save(sub.model_copy(update=upd))
        return session

    async def handle_webhook(self, payload: bytes, signature: str | None) -> str:
        """Apply a provider event; returns the event type handled (or 'ignored')."""
        event = self.provider.verify_webhook(payload, signature)
        etype = str(event.get("type", ""))
        obj = event.get("data", {}).get("object", {}) if isinstance(event.get("data"), dict) else {}
        customer = obj.get("customer")
        if not customer:
            return "ignored"
        sub = await self._by_customer(str(customer))
        if sub is None:
            return "ignored"
        if etype == "checkout.session.completed":
            upd = {
                "status": SubscriptionStatus.ACTIVE,
                "subscription_ref": obj.get("subscription"),
            }
            plan_id = (obj.get("metadata") or {}).get("plan_id")
            if plan_id in PLAN_BY_ID:
                upd["plan_id"] = plan_id
            await self._save(sub.model_copy(update=upd))
        elif etype in ("invoice.paid", "invoice.payment_succeeded"):
            await self._save(sub.model_copy(update={"status": SubscriptionStatus.ACTIVE}))
        elif etype == "invoice.payment_failed":
            await self._save(sub.model_copy(update={"status": SubscriptionStatus.PAST_DUE}))
        elif etype == "customer.subscription.deleted":
            await self._save(sub.model_copy(update={"status": SubscriptionStatus.CANCELLED}))
        else:
            return "ignored"
        return etype

    async def _by_customer(self, customer_ref: str) -> Subscription | None:
        for doc in await self.store.list_docs(self.KIND, None, limit=10000):
            if doc.data.get("customer_ref") == customer_ref:
                return Subscription.model_validate(doc.data)
        return None

    # usage
    async def usage(self, tenant_id: str) -> UsageSummary:
        return await self._usage_for(await self.subscription(tenant_id))

    async def _usage_for(self, sub: Subscription) -> UsageSummary:
        plan = sub.plan
        calls = await self.store.filter_calls(
            CallFilter(
                tenant_id=sub.tenant_id, since=sub.period_start, until=sub.period_end, limit=100000
            )
        )
        costs: list[CallCost] = []
        per_day: dict[str, float] = {}
        minutes = 0.0
        vendor = 0.0
        for c in calls:
            m = billable_minutes(c)
            if m <= 0:
                continue
            minutes += m
            v = m * (
                self.rates.stt_pence_per_min
                + self.rates.tts_pence_per_min
                + self.rates.llm_pence_per_min
                + self.rates.telephony_pence_per_min
            )
            vendor += v
            day = c.started_at.date().isoformat()
            per_day[day] = round(per_day.get(day, 0.0) + m, 2)
            costs.append(
                CallCost(
                    call_id=c.call_id,
                    minutes=m,
                    vendor_pence=round(v, 2),
                    billable_pence=round(m * plan.overage_pence_per_minute),
                )
            )
        msgs = await self.sms.recent(sub.tenant_id, limit=100000)
        sms_used = sum(
            1
            for m in msgs
            if m.status == MessageStatus.SENT and sub.period_start <= m.created_at < sub.period_end
        )
        vendor += sms_used * self.rates.sms_pence
        numbers = await self.list_numbers(sub.tenant_id)

        overage_min = max(0.0, minutes - plan.included_minutes) if not plan.enterprise else minutes
        overage_pence = round(overage_min * plan.overage_pence_per_minute)
        sms_over = max(0, sms_used - plan.included_sms)
        sms_over_pence = sms_over * plan.sms_overage_pence
        extra_numbers = max(0, len(numbers) - plan.included_numbers)
        base = plan.monthly_pence + extra_numbers * 100
        discount = 0
        if sub.coupon and (cp := COUPONS.get(sub.coupon)):
            discount = base - cp.apply(base)
        total = base - discount + overage_pence + sms_over_pence
        margin = None if total <= 0 else round((total - vendor) / total * 100, 1)
        costs.sort(key=lambda x: x.minutes, reverse=True)
        return UsageSummary(
            tenant_id=sub.tenant_id,
            plan=plan,
            status=sub.status,
            period_start=sub.period_start,
            period_end=sub.period_end,
            calls=len(costs),
            minutes_used=round(minutes, 2),
            minutes_included=plan.included_minutes,
            minutes_overage=round(overage_min, 2),
            overage_pence=overage_pence,
            sms_used=sms_used,
            sms_included=plan.included_sms,
            sms_overage_pence=sms_over_pence,
            numbers_used=len(numbers),
            numbers_included=plan.included_numbers,
            base_pence=base,
            discount_pence=discount,
            estimated_total_pence=total,
            vendor_cost_pence=round(vendor, 2),
            gross_margin_pct=margin,
            per_day_minutes=dict(sorted(per_day.items())),
            top_calls=costs[:10],
        )

    async def concurrent_limit(self, tenant_id: str) -> int:
        return (await self.subscription(tenant_id)).plan.max_concurrent_calls

    # numbers
    async def list_numbers(self, tenant_id: str) -> list[TenantNumber]:
        docs = await self.store.list_docs(self.NUMBER_KIND, tenant_id, limit=1000)
        return sorted((TenantNumber.model_validate(d.data) for d in docs), key=lambda n: n.e164)

    async def search_numbers(self, country: str = "GB", limit: int = 5) -> list[PhoneNumber]:
        return await self.numbers.search_numbers(country, limit)

    async def provision_number(
        self,
        tenant_id: str,
        company_id: str,
        assistant_id: str,
        e164: str,
        label: str | None = None,
    ) -> TenantNumber:
        sub = await self.subscription(tenant_id)
        owned = await self.list_numbers(tenant_id)
        if any(n.e164 == e164 for n in owned):
            raise ValueError("number already on this account")
        if len(owned) >= sub.plan.included_numbers and sub.plan.id == "starter":
            raise ValueError("Starter includes 1 number; upgrade to add more")
        if await self.store.get_assistant(assistant_id) is None:
            raise ValueError("unknown assistant")
        bought = await self.numbers.purchase_number(e164)
        bought = await self.numbers.route_number_to_trunk(bought, self.sip_uri)
        num = TenantNumber(
            tenant_id=tenant_id,
            company_id=company_id,
            e164=bought.e164,
            country=bought.country,
            provider=bought.provider,
            provider_ref=bought.provider_ref,
            assistant_id=assistant_id,
            label=label,
        )
        await self.store.put_doc(
            TenantDoc(
                kind=self.NUMBER_KIND,
                id=num.id,
                tenant_id=tenant_id,
                data=num.model_dump(mode="json"),
            )
        )
        await self.store.assign_number(tenant_id, company_id, num.e164, assistant_id)
        return num

    async def release_number(self, tenant_id: str, number_id: str) -> bool:
        doc = await self.store.get_doc(self.NUMBER_KIND, number_id)
        if doc is None or doc.tenant_id != tenant_id:
            return False
        num = TenantNumber.model_validate(doc.data)
        await self.numbers.release_number(
            PhoneNumber(
                provider=num.provider,
                e164=num.e164,
                country=num.country,
                provider_ref=num.provider_ref,
            )
        )
        await self.store.unassign_number(num.e164)
        return await self.store.delete_doc(self.NUMBER_KIND, number_id)
