"""Phase 14: business value — lead scoring, revenue attribution, missed-revenue, owner digest,
tracking numbers per marketing channel.

Value is estimated from tenant-supplied assumptions (`ValueSettings`: average job value, quote to
sale conversion) plus real outcomes (bookings created by the assistant, qualified leads, missed
calls). `attribute()` builds a `ValueReport` for a period; `TrackingNumber`s map the dialled
number to a marketing channel so the report can be split by source. `DigestService` composes the
weekly owner digest and sends it through the notification rules (email / WhatsApp via SMS
channel) as `NotifyEvent.OWNER_DIGEST`.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from parlio_api.calendar import BOOKING_KIND
from parlio_api.notifications import (
    NotificationEvent,
    NotificationService,
    NotifyEvent,
    is_qualified_lead,
)
from parlio_api.store import CallFilter, CallRecord, CallStore, TenantDoc

log = logging.getLogger("parlio.api.value")

SETTINGS_KIND = "value_settings"
TRACKING_KIND = "tracking_number"
DIGEST_KIND = "owner_digest"
DIGEST_TZ = ZoneInfo("Europe/London")

_INTENT = {
    "emergency": ("urgent", "emergency", "asap", "right now", "leak", "flood", "broken"),
    "complaint": ("complain", "unhappy", "refund", "wrong"),
    "booking": ("book", "appointment", "schedule", "slot", "reserve"),
    "quote": ("quote", "price", "how much", "cost", "estimate"),
    "info": ("open", "hours", "where", "address", "do you"),
}


# -- lead scoring ----------------------------------------------------------------------------------


class LeadScore(BaseModel):
    call_id: str
    score: int = Field(ge=0, le=100)
    grade: str  # hot | warm | cold
    intent: str | None = None
    reasons: list[str] = Field(default_factory=list)


def detect_intent(call: CallRecord) -> str | None:
    text = " ".join(str(t.get("text") or "") for t in call.transcript if t.get("role") == "user")
    low = text.lower()
    for intent, keys in _INTENT.items():
        if any(k in low for k in keys):
            return intent
    return None


def lead_score(call: CallRecord, booked: bool = False) -> LeadScore:
    score = 10
    reasons: list[str] = []
    intent = detect_intent(call)
    if call.answered_at is None or call.status == "failed":
        reasons.append("missed call")
        score = 15 if call.caller and call.caller != "unknown" else 5
        return LeadScore(
            call_id=call.call_id, score=score, grade="cold", intent=intent, reasons=reasons
        )
    if call.caller_type == "new":
        score += 15
        reasons.append("new caller")
    details = [k for k, v in call.extracted.items() if v]
    if details:
        score += min(25, 8 * len(details))
        reasons.append(f"details captured: {', '.join(details[:4])}")
    if intent in ("booking", "quote"):
        score += 20
        reasons.append(f"{intent} intent")
    elif intent == "emergency":
        score += 25
        reasons.append("urgent need")
    elif intent == "complaint":
        score -= 10
        reasons.append("complaint")
    dur = call.duration_s or 0
    if dur >= 120:
        score += 10
        reasons.append("engaged (2+ min)")
    elif dur >= 45:
        score += 5
    if booked:
        score += 25
        reasons.append("booked")
    if call.ticket_ids:
        score += 10
        reasons.append("callback requested")
    if call.missed_fields:
        score -= 5 * len(call.missed_fields)
    score = max(0, min(100, score))
    grade = "hot" if score >= 70 else "warm" if score >= 40 else "cold"
    return LeadScore(call_id=call.call_id, score=score, grade=grade, intent=intent, reasons=reasons)


# -- settings & tracking numbers -----------------------------------------------------------------


class ValueSettings(BaseModel):
    tenant_id: str
    currency: str = "GBP"
    avg_job_value_pence: int = Field(15_000, ge=0, description="typical value of one won job")
    booking_value_pence: int | None = Field(
        None, ge=0, description="value of a booked appointment; defaults to avg job value"
    )
    lead_to_sale_rate: float = Field(0.3, ge=0, le=1, description="share of qualified leads won")
    missed_call_lead_rate: float = Field(
        0.4, ge=0, le=1, description="share of missed calls that were real leads"
    )
    digest_enabled: bool = True
    digest_weekday: int = Field(0, ge=0, le=6, description="0 = Monday")
    digest_hour: int = Field(8, ge=0, le=23)


class TrackingNumber(BaseModel):
    id: str = Field(default_factory=lambda: f"tn-{uuid4().hex[:8]}")
    tenant_id: str
    e164: str = Field(pattern=r"^\+\d{7,15}$")
    channel: str = Field(min_length=1, max_length=60, description="Google Ads, Website, Van…")
    campaign: str | None = None
    monthly_cost_pence: int = Field(0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=TRACKING_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


# -- reports ---------------------------------------------------------------------------------------


class ChannelValue(BaseModel):
    channel: str
    calls: int = 0
    leads: int = 0
    bookings: int = 0
    attributed_pence: int = 0
    cost_pence: int = 0
    cost_per_lead_pence: int | None = None


class ValueReport(BaseModel):
    tenant_id: str
    period_start: datetime
    period_end: datetime
    currency: str
    calls: int = 0
    answered: int = 0
    missed: int = 0
    qualified_leads: int = 0
    hot_leads: int = 0
    bookings: int = 0
    attributed_pence: int = 0
    pipeline_pence: int = 0
    missed_revenue_pence: int = 0
    recovered_by_ai_pence: int = 0
    intents: dict[str, int] = Field(default_factory=dict)
    channels: list[ChannelValue] = Field(default_factory=list)
    top_leads: list[LeadScore] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ValueService:
    def __init__(self, store: CallStore) -> None:
        self.store = store

    async def settings(self, tenant_id: str) -> ValueSettings:
        doc = await self.store.get_doc(SETTINGS_KIND, tenant_id)
        return ValueSettings.model_validate(doc.data) if doc else ValueSettings(tenant_id=tenant_id)

    async def save_settings(self, s: ValueSettings) -> ValueSettings:
        await self.store.put_doc(
            TenantDoc(
                kind=SETTINGS_KIND, id=s.tenant_id, tenant_id=s.tenant_id, data=s.model_dump()
            )
        )
        return s

    async def tracking_numbers(self, tenant_id: str) -> list[TrackingNumber]:
        docs = await self.store.list_docs(TRACKING_KIND, tenant_id, 200)
        return sorted(
            (TrackingNumber.model_validate(d.data) for d in docs), key=lambda t: t.channel
        )

    async def save_tracking_number(self, t: TrackingNumber) -> TrackingNumber:
        for other in await self.tracking_numbers(t.tenant_id):
            if other.e164 == t.e164 and other.id != t.id:
                raise ValueError("that number is already assigned to a channel")
        await self.store.put_doc(t.to_doc())
        return t

    async def delete_tracking_number(self, tenant_id: str, tid: str) -> bool:
        doc = await self.store.get_doc(TRACKING_KIND, tid)
        if doc is None or doc.tenant_id != tenant_id:
            return False
        return await self.store.delete_doc(TRACKING_KIND, tid)

    async def _bookings_by_call(self, tenant_id: str, since: datetime) -> dict[str, int]:
        docs = await self.store.list_docs(BOOKING_KIND, tenant_id, 2000)
        out: Counter[str] = Counter()
        for d in docs:
            if d.created_at < since or d.data.get("status") == "cancelled":
                continue
            out[str(d.data.get("call_id") or f"nocall:{d.id}")] += 1
        return dict(out)

    async def attribute(self, tenant_id: str, days: int = 7) -> ValueReport:
        s = await self.settings(tenant_id)
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        calls = await self.store.filter_calls(
            CallFilter(tenant_id=tenant_id, since=start, until=end, limit=5000)
        )
        calls = [c for c in calls if c.kind != "blocked" and c.direction == "inbound"]
        bookings = await self._bookings_by_call(tenant_id, start)
        tracking = {t.e164: t for t in await self.tracking_numbers(tenant_id)}
        booking_value = s.booking_value_pence or s.avg_job_value_pence

        rep = ValueReport(
            tenant_id=tenant_id, period_start=start, period_end=end, currency=s.currency
        )
        intents: Counter[str] = Counter()
        chans: dict[str, ChannelValue] = {}
        scores: list[LeadScore] = []
        for c in calls:
            rep.calls += 1
            booked = bookings.get(c.call_id, 0)
            missed = c.kind == "missed"
            qualified = is_qualified_lead(c)
            ls = lead_score(c, booked=bool(booked))
            scores.append(ls)
            if ls.intent:
                intents[ls.intent] += 1
            tn = tracking.get(c.dialed or "")
            ch = chans.setdefault(
                tn.channel if tn else "Direct / untracked",
                ChannelValue(channel=tn.channel if tn else "Direct / untracked"),
            )
            ch.calls += 1
            if missed:
                rep.missed += 1
                rep.missed_revenue_pence += int(
                    s.avg_job_value_pence * s.missed_call_lead_rate * s.lead_to_sale_rate
                )
                continue
            rep.answered += 1
            if qualified:
                rep.qualified_leads += 1
                ch.leads += 1
                rep.pipeline_pence += int(s.avg_job_value_pence * s.lead_to_sale_rate)
            if ls.grade == "hot":
                rep.hot_leads += 1
            if booked:
                rep.bookings += booked
                ch.bookings += booked
                val = booked * booking_value
                rep.attributed_pence += val
                ch.attributed_pence += val
            elif qualified:
                ch.attributed_pence += int(s.avg_job_value_pence * s.lead_to_sale_rate)
        rep.bookings += sum(v for k, v in bookings.items() if k.startswith("nocall:"))
        # calls the AI answered outside hours or while lines were busy would have been missed
        after_hours = [c for c in calls if c.kind != "missed" and c.latency.get("after_hours")]
        rep.recovered_by_ai_pence = int(
            len(after_hours) * s.avg_job_value_pence * s.missed_call_lead_rate * s.lead_to_sale_rate
        )
        for t in tracking.values():
            ch = chans.setdefault(t.channel, ChannelValue(channel=t.channel))
            ch.cost_pence += int(t.monthly_cost_pence * days / 30)
        for ch in chans.values():
            if ch.leads and ch.cost_pence:
                ch.cost_per_lead_pence = ch.cost_pence // ch.leads
        rep.intents = dict(intents.most_common())
        rep.channels = sorted(chans.values(), key=lambda c: -c.attributed_pence)
        rep.top_leads = sorted(scores, key=lambda x: -x.score)[:10]
        return rep

    async def score_call(self, tenant_id: str, call_id: str) -> LeadScore | None:
        call = await self.store.get_call(call_id)
        if call is None or call.tenant_id != tenant_id:
            return None
        bookings = await self._bookings_by_call(tenant_id, call.started_at - timedelta(days=1))
        return lead_score(call, booked=call_id in bookings)


# -- weekly owner digest ---------------------------------------------------------------------------


def money(pence: int, currency: str = "GBP") -> str:
    sym = {"GBP": "£", "EUR": "€", "USD": "$"}.get(currency, currency + " ")
    return f"{sym}{pence / 100:,.0f}"


def render_digest(rep: ValueReport, business_name: str) -> tuple[str, str]:
    cur = rep.currency
    title = f"{business_name or 'Your business'}: weekly call report"
    lines = [
        f"Week to {rep.period_end:%a %d %b}: {rep.calls} calls, {rep.answered} answered by your "
        f"assistant, {rep.missed} missed.",
        f"Qualified leads: {rep.qualified_leads} ({rep.hot_leads} hot). Bookings: {rep.bookings}.",
        f"Attributed revenue: {money(rep.attributed_pence, cur)}; pipeline "
        f"{money(rep.pipeline_pence, cur)}.",
    ]
    if rep.missed_revenue_pence:
        lines.append(
            "Estimated missed revenue from unanswered calls: "
            f"{money(rep.missed_revenue_pence, cur)}."
        )
    if rep.intents:
        top = ", ".join(f"{k} {v}" for k, v in list(rep.intents.items())[:3])
        lines.append(f"Top reasons for calling: {top}.")
    tracked = [c for c in rep.channels if c.channel != "Direct / untracked" and c.calls]
    if tracked:
        lines.append(
            "By channel: "
            + "; ".join(
                f"{c.channel} {c.calls} calls / {c.leads} leads"
                + (f" (CPL {money(c.cost_per_lead_pence, cur)})" if c.cost_per_lead_pence else "")
                for c in tracked[:4]
            )
            + "."
        )
    return title, "\n".join(lines)


class DigestRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"dg-{uuid4().hex[:8]}")
    tenant_id: str
    title: str
    body: str
    report: dict[str, Any]
    sent_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DigestService:
    def __init__(
        self,
        store: CallStore,
        value: ValueService,
        notifications: NotificationService,
        interval_s: float = 900.0,
    ) -> None:
        self.store = store
        self.value = value
        self.notifications = notifications
        self.interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    async def send(self, tenant_id: str, *, days: int = 7) -> DigestRecord:
        rep = await self.value.attribute(tenant_id, days)
        cfgs = await self.store.list_assistants(tenant_id)
        name = cfgs[0].business_name if cfgs else ""
        title, body = render_digest(rep, name)
        await self.notifications.dispatch(
            NotificationEvent(
                tenant_id=tenant_id,
                company_id=cfgs[0].company_id if cfgs else None,
                event=NotifyEvent.OWNER_DIGEST,
                title=title,
                body=body,
                context={"period_days": days},
            )
        )
        rec = DigestRecord(
            tenant_id=tenant_id, title=title, body=body, report=rep.model_dump(mode="json")
        )
        await self.store.put_doc(
            TenantDoc(
                kind=DIGEST_KIND,
                id=rec.id,
                tenant_id=tenant_id,
                data=rec.model_dump(mode="json"),
                created_at=rec.sent_at,
            )
        )
        return rec

    async def history(self, tenant_id: str, limit: int = 12) -> list[DigestRecord]:
        docs = await self.store.list_docs(DIGEST_KIND, tenant_id, limit)
        return sorted(
            (DigestRecord.model_validate(d.data) for d in docs),
            key=lambda d: d.sent_at,
            reverse=True,
        )

    async def due(self, tenant_id: str, now: datetime | None = None) -> bool:
        s = await self.value.settings(tenant_id)
        if not s.digest_enabled:
            return False
        now = now or datetime.now(UTC)
        local = now.astimezone(DIGEST_TZ)
        if local.weekday() != s.digest_weekday or local.hour != s.digest_hour:
            return False
        last = await self.history(tenant_id, 1)
        return not last or (now - last[0].sent_at) > timedelta(days=6)

    async def sweep(self, now: datetime | None = None) -> int:
        sent = 0
        seen: set[str] = set()
        for cfg in await self.store.list_assistants():
            if cfg.tenant_id in seen:
                continue
            seen.add(cfg.tenant_id)
            try:
                if await self.due(cfg.tenant_id, now):
                    await self.send(cfg.tenant_id)
                    sent += 1
            except Exception:
                log.warning("digest failed for %s", cfg.tenant_id, exc_info=True)
        return sent

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            await self.sweep()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
