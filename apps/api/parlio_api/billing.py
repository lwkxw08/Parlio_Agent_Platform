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

INBOX_MESSAGE_KIND = "inbox_message"  # parlio_api.inbox.MESSAGE_KIND (avoids an import cycle)
CHAT_CHANNELS = {"webchat", "whatsapp"}
WEB_CALLER_PREFIX = "web:"
WEB_DIALED = "web"


def is_browser_call(call: CallRecord) -> bool:
    """Browser voice sessions (Phase 11b): caller ``web:<visitor>`` on the ``web`` line."""
    return call.dialed == WEB_DIALED or bool(
        call.caller and call.caller.startswith(WEB_CALLER_PREFIX)
    )


# -- catalogue --------------------------------------------------------------------------------


FLAGS_KIND = "feature_flags"

# Functional entitlements a plan can switch on; enforced via ``BillingService.entitled`` and the
# ``require_feature`` route dependency. Per-tenant feature flags (admin console) override these.
ENTITLEMENTS: dict[str, str] = {
    "calendar_booking": "Calendar booking (Google / Microsoft / booking links)",
    "warm_transfers": "Warm (announced) transfers",
    "departments": "Departments & on-call routing",
    "ask_ai": "Analytics Ask AI natural-language queries",
    "languages": "Additional languages beyond English",
    "sms_scenarios": "SMS follow-ups & scenarios",
    "whatsapp": "WhatsApp channel in the Inbox",
    "browser_voice": "Click-to-talk browser voice",
    "outbound": "Outbound dialer & speed-to-lead",
    "live_takeover": "Live listen / whisper / take over",
    "approvals": "Human-in-the-loop approvals",
    "payments": "Mid-call payment links",
    "connectors": "CRM / Zapier / webhook connectors",
    "byo_sip": "BYO SIP trunk / PBX",
    "qa_insights": "QA scoring & insight engine",
    "simulation": "Simulation sandbox & prompt A/B",
    "value_reports": "Lead scoring & value attribution",
    "white_label": "White-label branding & agency accounts",
    "sso": "SSO / SCIM",
    "priority_support": "Priority support (P1 24x7)",
    "sovereign_uk": "UK-sovereign deployment",
}
_ENT_STARTER = ["sms_scenarios", "browser_voice", "approvals", "qa_insights"]
_ENT_GROWTH = [
    *_ENT_STARTER,
    "calendar_booking",
    "warm_transfers",
    "departments",
    "ask_ai",
    "languages",
    "whatsapp",
    "live_takeover",
    "connectors",
    "value_reports",
    "priority_support",
]
_ENT_SCALE = [*_ENT_GROWTH, "outbound", "payments", "byo_sip", "simulation", "white_label"]
_ENT_ENTERPRISE = list(ENTITLEMENTS)


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
    entitlements: list[str] = Field(default_factory=list)
    enterprise: bool = False
    # Channel bundle (Phase 11b): web chat / WhatsApp inbound messages; browser-voice minutes
    # draw from ``included_minutes`` like phone calls (no telephony cost -> higher margin).
    included_chat_messages: int = 500
    chat_overage_pence: int = 2
    channels: list[str] = Field(
        default_factory=lambda: ["phone", "sms", "webchat", "browser_voice"]
    )


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
        features=[
            "1 assistant",
            "Tickets & callbacks",
            "Email/SMS alerts",
            "Web chat + browser voice",
        ],
        included_chat_messages=300,
        entitlements=_ENT_STARTER,
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
        entitlements=_ENT_GROWTH,
        included_chat_messages=1500,
        channels=["phone", "sms", "webchat", "browser_voice", "whatsapp"],
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
        entitlements=_ENT_SCALE,
        included_chat_messages=5000,
        chat_overage_pence=1,
        channels=["phone", "sms", "webchat", "browser_voice", "whatsapp"],
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
        entitlements=_ENT_ENTERPRISE,
        enterprise=True,
        included_chat_messages=0,
        chat_overage_pence=1,
        channels=["phone", "sms", "webchat", "browser_voice", "whatsapp"],
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
    PAUSED = "paused"  # customer-requested hold: no charges, calls declined politely
    SUSPENDED = "suspended"  # platform-enforced (non-payment / abuse)
    CANCELLED = "cancelled"


SERVING_STATUSES = {SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE}


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
    trial_ends_at: datetime | None = None
    status_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def plan(self) -> Plan:
        return PLAN_BY_ID.get(self.plan_id, PLANS[0])


class Credit(BaseModel):
    """Account credit (pence) applied against invoices until used up."""

    id: str = Field(default_factory=lambda: f"cr-{uuid4().hex[:8]}")
    tenant_id: str
    pence: int
    remaining_pence: int
    reason: str = ""
    granted_by: str = "system"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TenantLimits(BaseModel):
    """Platform-staff overrides of the plan's caps (None = use plan / global default)."""

    tenant_id: str
    max_concurrent_calls: int | None = None
    minutes_cap: int | None = None
    rate_limit_per_minute: int | None = None
    note: str | None = None


class Invoice(BaseModel):
    id: str
    tenant_id: str
    period_start: datetime
    period_end: datetime
    total_pence: int
    status: str  # draft | open | paid | void | uncollectible | refunded
    provider: str
    url: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Refund(BaseModel):
    id: str = Field(default_factory=lambda: f"rf-{uuid4().hex[:8]}")
    tenant_id: str
    pence: int
    reason: str
    invoice_id: str | None = None
    provider: str
    provider_ref: str | None = None
    issued_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# -- usage / cost metering ------------------------------------------------------------------------


class CostRates(BaseModel):
    """Per-minute vendor costs used for margin tracking (pence). Overridable via settings."""

    stt_pence_per_min: float = 0.6  # Deepgram Nova streaming
    tts_pence_per_min: float = 1.2  # Cartesia
    llm_pence_per_min: float = 0.4  # gpt-4o-mini class at ~400 tok/turn
    telephony_pence_per_min: float = 0.8  # Telnyx UK inbound + SIP
    sms_pence: float = 3.5
    chat_message_pence: float = 0.05  # LLM tokens per text reply


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
    # per-channel breakdown (Phase 11b)
    phone_minutes: float = 0.0
    browser_voice_minutes: float = 0.0
    browser_voice_calls: int = 0
    chat_messages_used: int = 0
    chat_messages_included: int = 0
    chat_overage_pence: int = 0
    chat_by_channel: dict[str, int] = Field(default_factory=dict)
    numbers_used: int
    numbers_included: int
    base_pence: int
    discount_pence: int
    credit_pence: int = 0
    credit_balance_pence: int = 0
    minutes_cap: int | None = None
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
    async def list_invoices(self, customer_ref: str) -> list[dict[str, Any]]: ...
    async def refund(self, customer_ref: str, pence: int, reason: str) -> str | None: ...
    async def set_paused(self, subscription_ref: str, paused: bool) -> None: ...
    async def cancel(self, subscription_ref: str) -> None: ...


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

    async def list_invoices(self, customer_ref: str) -> list[dict[str, Any]]:
        return []  # BillingService synthesises period invoices from usage

    async def refund(self, customer_ref: str, pence: int, reason: str) -> str | None:
        return f"re_sim_{uuid4().hex[:10]}"

    async def set_paused(self, subscription_ref: str, paused: bool) -> None:
        return None

    async def cancel(self, subscription_ref: str) -> None:
        return None


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
        return verify_stripe_webhook(self._webhook_secret, payload, signature)

    async def list_invoices(self, customer_ref: str) -> list[dict[str, Any]]:
        r = await self._http.get("/invoices", params={"customer": customer_ref, "limit": 24})
        r.raise_for_status()
        return [
            {
                "id": inv["id"],
                "period_start": inv.get("period_start"),
                "period_end": inv.get("period_end"),
                "total_pence": inv.get("total", 0),
                "status": inv.get("status", "open"),
                "url": inv.get("hosted_invoice_url"),
                "created": inv.get("created"),
            }
            for inv in r.json().get("data", [])
        ]

    async def refund(self, customer_ref: str, pence: int, reason: str) -> str | None:
        # Refund against the customer's most recent successful charge.
        r = await self._http.get("/charges", params={"customer": customer_ref, "limit": 1})
        r.raise_for_status()
        charges = r.json().get("data", [])
        if not charges:
            raise ValueError("no charge to refund")
        r = await self._http.post(
            "/refunds",
            data={
                "charge": charges[0]["id"],
                "amount": str(pence),
                "metadata[reason]": reason[:200],
            },
        )
        r.raise_for_status()
        return str(r.json()["id"])

    async def set_paused(self, subscription_ref: str, paused: bool) -> None:
        data = {"pause_collection[behavior]": "void"} if paused else {"pause_collection": ""}
        r = await self._http.post(f"/subscriptions/{subscription_ref}", data=data)
        r.raise_for_status()

    async def cancel(self, subscription_ref: str) -> None:
        r = await self._http.delete(f"/subscriptions/{subscription_ref}")
        r.raise_for_status()


def verify_stripe_webhook(
    webhook_secret: str | None, payload: bytes, signature: str | None
) -> dict[str, Any]:
    if not webhook_secret:
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
        webhook_secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256
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


def _ts(v: Any) -> datetime:
    if isinstance(v, int | float):
        return datetime.fromtimestamp(v, UTC)
    if isinstance(v, str):
        return datetime.fromisoformat(v)
    return datetime.now(UTC)


def _period(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt


class BillingService:
    KIND = "subscription"
    NUMBER_KIND = "number"
    CREDIT_KIND = "billing_credit"
    LIMITS_KIND = "tenant_limits"
    INVOICE_KIND = "invoice"
    REFUND_KIND = "refund"

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
        trial_end = now + timedelta(days=self.trial_days)
        sub = Subscription(
            tenant_id=tenant_id,
            period_start=start,
            period_end=max(end, trial_end),
            provider=self.provider.name,
            trial_ends_at=trial_end,
        )
        await self._save(sub)
        return sub

    async def entitlements(self, tenant_id: str) -> dict[str, bool]:
        """Plan entitlements with per-tenant feature-flag overrides (flag true/false wins).

        Trials unlock every entitlement so prospects can evaluate the whole product; the plan's
        own set applies once the subscription converts.
        """
        sub = await self.subscription(tenant_id)
        trial = sub.status == SubscriptionStatus.TRIALING
        out = {k: trial or k in sub.plan.entitlements for k in ENTITLEMENTS}
        doc = await self.store.get_doc(FLAGS_KIND, tenant_id)
        if doc is not None:
            flags = doc.data.get("flags", {})
            for k, v in flags.items():
                if k in out and isinstance(v, bool):
                    out[k] = v
        return out

    async def entitled(self, tenant_id: str, key: str) -> bool:
        return (await self.entitlements(tenant_id)).get(key, False)

    async def all_subscriptions(self) -> list[Subscription]:
        docs = await self.store.list_docs(self.KIND, None, limit=100000)
        return [Subscription.model_validate(d.data) for d in docs]

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
        await self._consume_credits(sub.tenant_id, usage.credit_pence)
        if sub.status in SERVING_STATUSES:
            await self._record_invoice(sub, usage)
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
        self,
        tenant_id: str,
        plan_id: str,
        coupon_code: str | None = None,
        *,
        by_staff: bool = False,
    ) -> Subscription:
        if plan_id not in PLAN_BY_ID:
            raise ValueError(f"unknown plan {plan_id}")
        plan = PLAN_BY_ID[plan_id]
        if plan.enterprise and not by_staff:
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

    # -- platform-staff operations (Phase 16b) --------------------------------------------------
    async def set_status(
        self, tenant_id: str, status: SubscriptionStatus, reason: str | None = None
    ) -> Subscription:
        sub = await self.subscription(tenant_id)
        if sub.subscription_ref and self.provider.name != "simulated":
            if status == SubscriptionStatus.CANCELLED:
                await self.provider.cancel(sub.subscription_ref)
            elif status in (SubscriptionStatus.PAUSED, SubscriptionStatus.SUSPENDED):
                await self.provider.set_paused(sub.subscription_ref, True)
            elif sub.status in (SubscriptionStatus.PAUSED, SubscriptionStatus.SUSPENDED):
                await self.provider.set_paused(sub.subscription_ref, False)
        return await self._save(sub.model_copy(update={"status": status, "status_reason": reason}))

    async def extend_trial(self, tenant_id: str, days: int) -> Subscription:
        sub = await self.subscription(tenant_id)
        base = max(sub.trial_ends_at or sub.period_end, datetime.now(UTC))
        new_end = base + timedelta(days=days)
        upd: dict[str, Any] = {
            "trial_ends_at": new_end,
            "period_end": max(sub.period_end, new_end),
        }
        if sub.status in (SubscriptionStatus.PAST_DUE, SubscriptionStatus.TRIALING):
            upd["status"] = SubscriptionStatus.TRIALING
        return await self._save(sub.model_copy(update=upd))

    async def convert_trial(self, tenant_id: str) -> Subscription:
        """Activate without a card (invoiced / enterprise-style) - staff only."""
        sub = await self.subscription(tenant_id)
        return await self._save(
            sub.model_copy(update={"status": SubscriptionStatus.ACTIVE, "trial_ends_at": None})
        )

    async def grant_credit(
        self, tenant_id: str, pence: int, reason: str, granted_by: str
    ) -> Credit:
        if pence <= 0:
            raise ValueError("credit must be positive")
        c = Credit(
            tenant_id=tenant_id,
            pence=pence,
            remaining_pence=pence,
            reason=reason,
            granted_by=granted_by,
        )
        await self.store.put_doc(
            TenantDoc(
                kind=self.CREDIT_KIND, id=c.id, tenant_id=tenant_id, data=c.model_dump(mode="json")
            )
        )
        return c

    async def credits(self, tenant_id: str) -> list[Credit]:
        docs = await self.store.list_docs(self.CREDIT_KIND, tenant_id, limit=1000)
        return sorted((Credit.model_validate(d.data) for d in docs), key=lambda c: c.created_at)

    async def credit_balance(self, tenant_id: str) -> int:
        return sum(c.remaining_pence for c in await self.credits(tenant_id))

    async def _consume_credits(self, tenant_id: str, pence: int) -> None:
        for c in await self.credits(tenant_id):
            if pence <= 0:
                break
            take = min(c.remaining_pence, pence)
            if take <= 0:
                continue
            pence -= take
            await self.store.put_doc(
                TenantDoc(
                    kind=self.CREDIT_KIND,
                    id=c.id,
                    tenant_id=tenant_id,
                    data=c.model_copy(
                        update={"remaining_pence": c.remaining_pence - take}
                    ).model_dump(mode="json"),
                )
            )

    async def limits(self, tenant_id: str) -> TenantLimits:
        doc = await self.store.get_doc(self.LIMITS_KIND, tenant_id)
        return TenantLimits.model_validate(doc.data) if doc else TenantLimits(tenant_id=tenant_id)

    async def set_limits(self, limits: TenantLimits) -> TenantLimits:
        await self.store.put_doc(
            TenantDoc(
                kind=self.LIMITS_KIND,
                id=limits.tenant_id,
                tenant_id=limits.tenant_id,
                data=limits.model_dump(mode="json"),
            )
        )
        return limits

    async def _record_invoice(self, sub: Subscription, usage: UsageSummary) -> Invoice:
        inv = Invoice(
            id=f"inv-{sub.tenant_id}-{sub.period_start:%Y%m}",
            tenant_id=sub.tenant_id,
            period_start=sub.period_start,
            period_end=sub.period_end,
            total_pence=usage.estimated_total_pence,
            status="paid" if sub.status == SubscriptionStatus.ACTIVE else "open",
            provider=self.provider.name,
        )
        await self.store.put_doc(
            TenantDoc(
                kind=self.INVOICE_KIND,
                id=inv.id,
                tenant_id=sub.tenant_id,
                data=inv.model_dump(mode="json"),
            )
        )
        return inv

    async def invoices(self, tenant_id: str) -> list[Invoice]:
        """Provider invoices when a customer ref exists, else the period invoices we recorded."""
        sub = await self.subscription(tenant_id)
        out: list[Invoice] = []
        if sub.customer_ref and self.provider.name != "simulated":
            try:
                for raw in await self.provider.list_invoices(sub.customer_ref):
                    out.append(
                        Invoice(
                            id=str(raw["id"]),
                            tenant_id=tenant_id,
                            period_start=_ts(raw.get("period_start")),
                            period_end=_ts(raw.get("period_end")),
                            total_pence=int(raw.get("total_pence") or 0),
                            status=str(raw.get("status") or "open"),
                            provider=self.provider.name,
                            url=raw.get("url"),
                            created_at=_ts(raw.get("created")),
                        )
                    )
            except Exception:
                log.warning("invoice fetch failed for %s", tenant_id, exc_info=True)
        if not out:
            docs = await self.store.list_docs(self.INVOICE_KIND, tenant_id, limit=100)
            out = [Invoice.model_validate(d.data) for d in docs]
        usage = await self._usage_for(sub)
        out.append(
            Invoice(
                id=f"upcoming-{tenant_id}",
                tenant_id=tenant_id,
                period_start=sub.period_start,
                period_end=sub.period_end,
                total_pence=usage.estimated_total_pence,
                status="draft",
                provider=self.provider.name,
            )
        )
        out.sort(key=lambda i: i.period_start, reverse=True)
        return out

    async def refund(
        self, tenant_id: str, pence: int, reason: str, issued_by: str, invoice_id: str | None = None
    ) -> Refund:
        if pence <= 0:
            raise ValueError("refund must be positive")
        sub = await self.subscription(tenant_id)
        ref = None
        if sub.customer_ref and self.provider.name != "simulated":
            ref = await self.provider.refund(sub.customer_ref, pence, reason)
        else:
            ref = await self.provider.refund(
                sub.customer_ref or f"cus_sim_{tenant_id}", pence, reason
            )
        rf = Refund(
            tenant_id=tenant_id,
            pence=pence,
            reason=reason,
            invoice_id=invoice_id,
            provider=self.provider.name,
            provider_ref=ref,
            issued_by=issued_by,
        )
        await self.store.put_doc(
            TenantDoc(
                kind=self.REFUND_KIND,
                id=rf.id,
                tenant_id=tenant_id,
                data=rf.model_dump(mode="json"),
            )
        )
        if invoice_id:
            doc = await self.store.get_doc(self.INVOICE_KIND, invoice_id)
            if doc is not None and doc.tenant_id == tenant_id:
                doc.data["status"] = "refunded"
                await self.store.put_doc(doc)
        return rf

    async def refunds(self, tenant_id: str) -> list[Refund]:
        docs = await self.store.list_docs(self.REFUND_KIND, tenant_id, limit=200)
        return sorted(
            (Refund.model_validate(d.data) for d in docs), key=lambda r: r.created_at, reverse=True
        )

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
        browser_minutes = 0.0
        browser_calls = 0
        vendor = 0.0
        for c in calls:
            m = billable_minutes(c)
            if m <= 0:
                continue
            minutes += m
            browser = is_browser_call(c)
            if browser:
                browser_minutes += m
                browser_calls += 1
            v = m * (
                self.rates.stt_pence_per_min
                + self.rates.tts_pence_per_min
                + self.rates.llm_pence_per_min
                + (0.0 if browser else self.rates.telephony_pence_per_min)
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
        chat_by_channel = await self._chat_messages(sub)
        chat_used = sum(chat_by_channel.values())
        vendor += chat_used * self.rates.chat_message_pence
        numbers = await self.list_numbers(sub.tenant_id)

        overage_min = max(0.0, minutes - plan.included_minutes) if not plan.enterprise else minutes
        overage_pence = round(overage_min * plan.overage_pence_per_minute)
        sms_over = max(0, sms_used - plan.included_sms)
        sms_over_pence = sms_over * plan.sms_overage_pence
        chat_over = (
            chat_used if plan.enterprise else max(0, chat_used - plan.included_chat_messages)
        )
        chat_over_pence = chat_over * plan.chat_overage_pence
        extra_numbers = max(0, len(numbers) - plan.included_numbers)
        base = plan.monthly_pence + extra_numbers * 100
        discount = 0
        if sub.coupon and (cp := COUPONS.get(sub.coupon)):
            discount = base - cp.apply(base)
        gross = base - discount + overage_pence + sms_over_pence + chat_over_pence
        balance = await self.credit_balance(sub.tenant_id)
        credit = min(balance, gross)
        total = gross - credit
        limits = await self.limits(sub.tenant_id)
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
            phone_minutes=round(minutes - browser_minutes, 2),
            browser_voice_minutes=round(browser_minutes, 2),
            browser_voice_calls=browser_calls,
            chat_messages_used=chat_used,
            chat_messages_included=plan.included_chat_messages,
            chat_overage_pence=chat_over_pence,
            chat_by_channel=chat_by_channel,
            numbers_used=len(numbers),
            numbers_included=plan.included_numbers,
            base_pence=base,
            discount_pence=discount,
            credit_pence=credit,
            credit_balance_pence=balance,
            minutes_cap=limits.minutes_cap,
            estimated_total_pence=total,
            vendor_cost_pence=round(vendor, 2),
            gross_margin_pct=margin,
            per_day_minutes=dict(sorted(per_day.items())),
            top_calls=costs[:10],
        )

    async def _chat_messages(self, sub: Subscription) -> dict[str, int]:
        """Inbound web-chat / WhatsApp messages this period (what the AI had to answer)."""
        out: dict[str, int] = {}
        for d in await self.store.list_docs(INBOX_MESSAGE_KIND, sub.tenant_id, 100000):
            ch = str(d.data.get("channel"))
            if ch not in CHAT_CHANNELS or d.data.get("direction") != "in":
                continue
            if not (sub.period_start <= d.created_at < sub.period_end):
                continue
            out[ch] = out.get(ch, 0) + 1
        return out

    async def concurrent_limit(self, tenant_id: str) -> int:
        sub = await self.subscription(tenant_id)
        if sub.status not in SERVING_STATUSES:
            return 0
        limits = await self.limits(tenant_id)
        return limits.max_concurrent_calls or sub.plan.max_concurrent_calls

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
