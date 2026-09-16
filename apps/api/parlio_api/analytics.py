"""Dashboard analytics read model, computed from call/contact records.

Works identically on the memory and Postgres stores (both hand back `CallRecord`s); when volume
outgrows this, the same shapes are produced from the `analytics_*` views / ClickHouse.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from parlio_api.store import CallRecord, Contact, TenantDoc, TicketStats, TransferStats


class PeriodSummary(BaseModel):
    start: datetime
    end: datetime
    total_calls: int = 0
    answered: int = 0
    missed: int = 0
    transferred: int = 0
    ticketed: int = 0
    blocked: int = 0
    escalated: int = 0
    answer_rate: float | None = None
    avg_duration_s: float | None = None
    avg_answer_latency_s: float | None = None
    total_minutes: float = 0.0
    unique_callers: int = 0
    new_callers: int = 0
    avg_calls_per_caller: float | None = None


class DailyPoint(BaseModel):
    day: str  # YYYY-MM-DD
    calls: int = 0
    answered: int = 0
    missed: int = 0


class ProspectInsights(BaseModel):
    contacts: int = 0
    prospects: int = 0
    customers: int = 0
    vip: int = 0
    returning_callers: int = 0
    returning_rate: float | None = None
    top_callers: list[dict[str, str | int]] = Field(default_factory=list)


class UsageSummary(BaseModel):
    month: str  # YYYY-MM
    calls: int = 0
    minutes: float = 0.0
    tickets: int = 0
    transfers: int = 0
    sms: int = 0
    whatsapp: int = 0
    web_chats: int = 0
    emails: int = 0
    bookings: int = 0


class OverviewAnalytics(BaseModel):
    timezone: str
    current: PeriodSummary
    previous: PeriodSummary
    change: dict[str, float | None] = Field(default_factory=dict)
    by_hour: list[int] = Field(default_factory=lambda: [0] * 24)
    by_weekday: list[int] = Field(default_factory=lambda: [0] * 7)  # Mon..Sun
    daily: list[DailyPoint] = Field(default_factory=list)
    top_missed_fields: list[dict[str, str | int]] = Field(default_factory=list)
    feedback_by_type: dict[str, int] = Field(default_factory=dict)
    transfers: TransferStats = Field(default_factory=TransferStats)
    tickets: TicketStats = Field(default_factory=TicketStats)
    prospects: ProspectInsights = Field(default_factory=ProspectInsights)
    usage: UsageSummary


def _pct_change(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev == 0:
        return None
    return round((cur - prev) / prev * 100, 1)


def summarise(calls: list[CallRecord], start: datetime, end: datetime) -> PeriodSummary:
    s = PeriodSummary(start=start, end=end, total_calls=len(calls))
    kinds = Counter(c.kind for c in calls)
    s.answered = kinds["answered"] + kinds["transferred"] + kinds["ticketed"]
    s.missed = kinds["missed"]
    s.transferred = kinds["transferred"]
    s.ticketed = sum(1 for c in calls if c.ticket_ids)
    s.blocked = kinds["blocked"]
    s.escalated = sum(1 for c in calls if c.escalated)
    handled = s.answered + s.missed
    s.answer_rate = round(s.answered / handled, 3) if handled else None
    durs = [c.duration_s for c in calls if c.duration_s]
    s.avg_duration_s = round(sum(durs) / len(durs), 1) if durs else None
    s.total_minutes = round(sum(durs) / 60, 1)
    lats = [c.answer_latency_s for c in calls if c.answer_latency_s is not None]
    s.avg_answer_latency_s = round(sum(lats) / len(lats), 2) if lats else None
    callers = Counter(c.caller for c in calls if c.caller)
    s.unique_callers = len(callers)
    s.new_callers = sum(1 for c in calls if c.caller_type == "new")
    s.avg_calls_per_caller = round(len(calls) / len(callers), 2) if callers else None
    return s


def channel_usage(
    usage: UsageSummary,
    *,
    messages: list[TenantDoc],
    inbox_messages: list[TenantDoc],
    notifications: list[TenantDoc],
    bookings: list[TenantDoc],
    month_start: datetime,
) -> UsageSummary:
    """Add this month's non-call usage (texts, chats, emails, bookings) to `usage`."""

    def this_month(docs: list[TenantDoc]) -> list[dict[str, Any]]:
        return [d.data for d in docs if d.created_at >= month_start]

    usage.sms = sum(1 for m in this_month(messages) if m.get("status") == "sent")
    for m in this_month(inbox_messages):
        if m.get("direction") != "out" or m.get("status") not in ("sent", None):
            continue
        if m.get("channel") == "whatsapp":
            usage.whatsapp += 1
        elif m.get("channel") == "webchat":
            usage.web_chats += 1
    usage.emails = sum(
        1
        for n in this_month(notifications)
        if n.get("channel") == "email" and n.get("status") == "sent"
    )
    usage.bookings = sum(1 for b in this_month(bookings) if b.get("status") != "cancelled")
    return usage


def compute_overview(
    calls: list[CallRecord],
    contacts: list[Contact],
    transfers: TransferStats,
    tickets: TicketStats,
    *,
    days: int = 30,
    timezone: str = "Europe/London",
    now: datetime | None = None,
) -> OverviewAnalytics:
    tz = ZoneInfo(timezone)
    now = now or datetime.now(UTC)
    end = now
    start = end - timedelta(days=days)
    prev_start = start - timedelta(days=days)

    cur = [c for c in calls if start <= c.started_at < end]
    prev = [c for c in calls if prev_start <= c.started_at < start]
    cs, ps = summarise(cur, start, end), summarise(prev, prev_start, start)

    by_hour = [0] * 24
    by_weekday = [0] * 7
    daily: dict[str, DailyPoint] = {}
    for d in range(days):
        key = (start + timedelta(days=d + 1)).astimezone(tz).strftime("%Y-%m-%d")
        daily[key] = DailyPoint(day=key)
    for c in cur:
        local = c.started_at.astimezone(tz)
        by_hour[local.hour] += 1
        by_weekday[local.weekday()] += 1
        p = daily.setdefault(local.strftime("%Y-%m-%d"), DailyPoint(day=local.strftime("%Y-%m-%d")))
        p.calls += 1
        if c.kind == "missed":
            p.missed += 1
        elif c.kind in ("answered", "transferred", "ticketed"):
            p.answered += 1

    missed_fields = Counter(f for c in cur for f in c.missed_fields)
    feedback = Counter(str(f.get("type", "other")) for c in cur for f in c.feedback)

    returning = sum(1 for c in contacts if c.call_count > 1)
    prospects = ProspectInsights(
        contacts=len(contacts),
        prospects=sum(1 for c in contacts if c.status == "prospect"),
        customers=sum(1 for c in contacts if c.status == "customer"),
        vip=sum(1 for c in contacts if c.vip),
        returning_callers=returning,
        returning_rate=round(returning / len(contacts), 3) if contacts else None,
        top_callers=[
            {"e164": c.e164, "name": c.name or "", "calls": c.call_count}
            for c in sorted(contacts, key=lambda c: -c.call_count)[:5]
        ],
    )

    month_start = now.astimezone(tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_calls = [c for c in calls if c.started_at >= month_start]
    usage = UsageSummary(
        month=month_start.strftime("%Y-%m"),
        calls=len(month_calls),
        minutes=round(sum(c.duration_s or 0 for c in month_calls) / 60, 1),
        tickets=sum(len(c.ticket_ids) for c in month_calls),
        transfers=sum(len(c.transfers) for c in month_calls),
    )

    return OverviewAnalytics(
        timezone=timezone,
        current=cs,
        previous=ps,
        change={
            "total_calls": _pct_change(cs.total_calls, ps.total_calls),
            "answer_rate": _pct_change(cs.answer_rate, ps.answer_rate),
            "avg_duration_s": _pct_change(cs.avg_duration_s, ps.avg_duration_s),
            "unique_callers": _pct_change(cs.unique_callers, ps.unique_callers),
            "new_callers": _pct_change(cs.new_callers, ps.new_callers),
            "missed": _pct_change(cs.missed, ps.missed),
        },
        by_hour=by_hour,
        by_weekday=by_weekday,
        daily=sorted(daily.values(), key=lambda p: p.day),
        top_missed_fields=[{"field": k, "count": v} for k, v in missed_fields.most_common(5)],
        feedback_by_type=dict(feedback),
        transfers=transfers,
        tickets=tickets,
        prospects=prospects,
        usage=usage,
    )
