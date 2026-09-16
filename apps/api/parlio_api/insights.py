"""Phase 21a: deep analytics read models ("Insights").

Everything here is a pure function over tenant-scoped records already collected by the platform
(calls, tickets + events, transfers, bookings, outbound jobs, QA scores, insights, contacts,
members, tracking numbers, value settings). No PII leaves the aggregation: the report only carries
counts, rates, durations, money estimates and human labels (departments, channels, intents, team
member names). The AI business advisor (21c) is meant to read this report, never raw records.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from parlio_api.notifications import is_qualified_lead
from parlio_api.store import (
    CallRecord,
    Contact,
    Member,
    TenantDoc,
    Ticket,
    TicketEvent,
    TicketStatus,
    TransferRecord,
)
from parlio_api.value import TrackingNumber, ValueSettings, detect_intent
from parlio_voice.models import Schedule

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# -- models --------------------------------------------------------------------------------------


class Slice(BaseModel):
    """A generic labelled measure used by the breakdown / donut / bar components."""

    label: str
    count: int = 0
    share: float | None = None
    value: float | None = None  # secondary measure (minutes, pence, rate…) depending on context


class ForecastDay(BaseModel):
    day: str  # YYYY-MM-DD (tenant local)
    weekday: str
    expected_calls: float
    low: float
    high: float
    busiest_hours: list[int] = Field(default_factory=list)


class Demand(BaseModel):
    heatmap: list[list[int]] = Field(default_factory=lambda: [[0] * 24 for _ in range(7)])
    avg_by_hour: list[float] = Field(default_factory=lambda: [0.0] * 24)  # per active day
    avg_by_weekday: list[float] = Field(default_factory=lambda: [0.0] * 7)  # per occurrence
    forecast: list[ForecastDay] = Field(default_factory=list)
    forecast_total: float = 0.0
    forecast_vs_last_week_pct: float | None = None
    would_have_missed_by_hour: list[int] = Field(default_factory=lambda: [0] * 24)
    would_have_missed_total: int = 0
    after_hours_calls: int = 0
    overlapping_calls: int = 0  # answered while another call was already in progress
    peak_concurrency: int = 0
    peak_concurrency_at: datetime | None = None
    busiest_hours: list[Slice] = Field(default_factory=list)
    quietest_hours: list[Slice] = Field(default_factory=list)


class FunnelStep(BaseModel):
    key: str
    label: str
    count: int
    share: float | None = None  # of the first step


class Resolution(BaseModel):
    funnel: list[FunnelStep] = Field(default_factory=list)
    resolved_by_ai: int = 0
    transferred: int = 0
    transfer_answered: int = 0
    ticketed: int = 0
    ai_callbacks: int = 0
    ai_callbacks_resolved: int = 0
    resolved_by_human: int = 0
    first_contact_resolution_rate: float | None = None
    leakage_rate: float | None = None  # share of answered calls needing a human afterwards
    median_time_to_resolve_h: float | None = None
    by_intent: list[Slice] = Field(default_factory=list)  # AI-resolved share per intent


class Backlog(BaseModel):
    department: str
    open: int
    oldest_h: float | None = None
    avg_age_h: float | None = None


class Sla(BaseModel):
    tickets: int = 0
    median_time_to_claim_h: float | None = None
    median_time_to_first_callback_h: float | None = None
    median_time_to_resolve_h: float | None = None
    breach_rate: float | None = None
    breached: int = 0
    reopened: int = 0
    reopen_rate: float | None = None
    callback_first_attempt_rate: float | None = None
    backlog: list[Backlog] = Field(default_factory=list)
    breach_trend: list[Slice] = Field(default_factory=list)  # per week: label, breached, share


class TransferRow(BaseModel):
    label: str
    attempts: int
    answered: int
    answer_rate: float | None = None
    abandoned: int = 0
    avg_human_s: float | None = None
    human_minutes: float = 0.0


class TransferQuality(BaseModel):
    attempts: int = 0
    answered: int = 0
    answer_rate: float | None = None
    abandoned: int = 0
    avg_human_s: float | None = None
    human_minutes: float = 0.0
    recorded: int = 0
    by_department: list[TransferRow] = Field(default_factory=list)
    by_destination: list[TransferRow] = Field(default_factory=list)
    by_hour: list[int] = Field(default_factory=lambda: [0] * 24)
    est_transfer_cost_pence: int = 0
    est_ai_cost_pence: int = 0


class Gap(BaseModel):
    question: str
    count: int
    status: str
    est_value_pence: int = 0


class Intents(BaseModel):
    top: list[Slice] = Field(default_factory=list)  # count + value (=est pence)
    rising: list[Slice] = Field(default_factory=list)  # value = % change vs previous period
    falling: list[Slice] = Field(default_factory=list)
    unanswered_questions: int = 0
    gaps: list[Gap] = Field(default_factory=list)
    est_gap_value_pence: int = 0


class Revenue(BaseModel):
    currency: str = "GBP"
    calls: int = 0
    leads: int = 0
    bookings: int = 0
    lead_rate: float | None = None
    booking_rate: float | None = None
    attributed_pence: int = 0
    missed_pence: int = 0
    unresolved_pence: int = 0
    avg_job_value_pence: int = 0
    median_lead_to_booking_h: float | None = None
    repeat_caller_share: float | None = None
    by_intent: list[Slice] = Field(default_factory=list)  # count=leads, value=pence
    by_source: list[Slice] = Field(default_factory=list)
    by_hour: list[float] = Field(default_factory=lambda: [0.0] * 24)  # lead rate per hour
    top_customers: list[Slice] = Field(default_factory=list)  # label=name/number, value=pence


class CustomerExperience(BaseModel):
    qa_scored: int = 0
    avg_qa: float | None = None
    avg_tone: float | None = None
    avg_resolution: float | None = None
    frustration_rate: float | None = None
    frustrated: int = 0
    repeat_within_7d: int = 0
    repeat_rate: float | None = None
    positive_feedback: int = 0
    negative_feedback: int = 0
    qa_trend: list[Slice] = Field(default_factory=list)  # per week; value = avg QA
    qa_by_intent: list[Slice] = Field(default_factory=list)
    qa_by_version: list[Slice] = Field(default_factory=list)


class MemberRow(BaseModel):
    label: str
    claimed: int = 0
    resolved: int = 0
    callbacks: int = 0
    notes: int = 0
    transfers_answered: int = 0
    transfers_missed: int = 0
    avg_resolution_h: float | None = None
    active_days: int = 0
    vs_team_pct: float | None = None  # resolved vs team average


class Workforce(BaseModel):
    members: list[MemberRow] = Field(default_factory=list)
    team_avg_resolved: float | None = None
    team_avg_resolution_h: float | None = None
    unassigned_open: int = 0


class Attribution(BaseModel):
    channels: list[Slice] = Field(default_factory=list)  # count=calls, value=attributed pence
    leads_by_channel: list[Slice] = Field(default_factory=list)
    bookings_by_channel: list[Slice] = Field(default_factory=list)
    untracked_calls: int = 0


class Cost(BaseModel):
    ai_minutes: float = 0.0
    human_minutes: float = 0.0
    ai_share: float | None = None
    ai_handled_calls: int = 0
    hours_saved: float = 0.0
    est_human_cost_pence: int = 0
    est_ai_cost_pence: int = 0
    cost_per_resolved_pence: int | None = None


class TrendPoint(BaseModel):
    period: str  # e.g. 2026-03, 2026-Q1, 2026
    label: str
    calls: int = 0
    answered: int = 0
    missed: int = 0
    transfers: int = 0
    tickets: int = 0
    bookings: int = 0
    leads: int = 0
    answer_rate: float | None = None
    est_value_pence: int = 0


class Trends(BaseModel):
    window_days: int
    avg_by_hour: list[float] = Field(default_factory=lambda: [0.0] * 24)
    avg_by_weekday: list[float] = Field(default_factory=lambda: [0.0] * 7)
    monthly: list[TrendPoint] = Field(default_factory=list)
    quarterly: list[TrendPoint] = Field(default_factory=list)
    yearly: list[TrendPoint] = Field(default_factory=list)
    intent_monthly: list[Slice] = Field(default_factory=list)  # label "intent|YYYY-MM"
    busiest_days: list[Slice] = Field(default_factory=list)
    busiest_months: list[Slice] = Field(default_factory=list)
    same_period_last_year: TrendPoint | None = None
    this_period: TrendPoint | None = None
    yoy_calls_pct: float | None = None


class InsightsReport(BaseModel):
    tenant_id: str
    timezone: str
    start: datetime
    end: datetime
    days: int
    generated_at: datetime
    demand: Demand
    resolution: Resolution
    sla: Sla
    transfers: TransferQuality
    intents: Intents
    revenue: Revenue
    cx: CustomerExperience
    workforce: Workforce
    attribution: Attribution
    cost: Cost
    trends: Trends


# -- inputs ------------------------------------------------------------------------------------


class InsightInputs(BaseModel):
    """Everything `build_insights` needs; assembled by the route, easy to fake in tests."""

    tenant_id: str
    calls: list[CallRecord] = Field(default_factory=list)  # all history available (for trends)
    contacts: list[Contact] = Field(default_factory=list)
    tickets: list[Ticket] = Field(default_factory=list)
    ticket_events: dict[str, list[TicketEvent]] = Field(default_factory=dict)
    transfers: list[TransferRecord] = Field(default_factory=list)
    members: list[Member] = Field(default_factory=list)
    bookings: list[TenantDoc] = Field(default_factory=list)
    outbound: list[TenantDoc] = Field(default_factory=list)
    qa_scores: list[TenantDoc] = Field(default_factory=list)
    faq_insights: list[TenantDoc] = Field(default_factory=list)
    tracking_numbers: list[TrackingNumber] = Field(default_factory=list)
    value: ValueSettings | None = None
    schedule: Schedule | None = None
    human_hourly_cost_pence: int = 1_800  # £18/h fully loaded office cost, used for estimates
    ai_minute_cost_pence: int = 15  # blended platform minute price used for AI-cost estimates
    human_handle_minutes: float = 6.0  # what an answered call costs a person on average


# -- helpers -----------------------------------------------------------------------------------


def _rate(n: int | float, d: int | float) -> float | None:
    return round(n / d, 3) if d else None


def _pct(cur: float, prev: float) -> float | None:
    return round((cur - prev) / prev * 100, 1) if prev else None


def _hours(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds() / 3600, 2)


def _median(xs: list[float]) -> float | None:
    return round(median(xs), 2) if xs else None


def _parse_dt(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _in_hours(local: datetime, schedule: Schedule | None) -> bool:
    if schedule is None:
        return 8 <= local.hour < 18 and local.weekday() < 5
    if schedule.always:
        return True
    day = schedule.hours.get(WEEKDAYS[local.weekday()])
    return day is not None and day.contains(local.time().replace(tzinfo=None))


def _humanize(s: str) -> str:
    return s.replace("_", " ").replace("-", " ").strip().capitalize() or "Other"


def _answered(c: CallRecord) -> bool:
    return c.kind in ("answered", "transferred", "ticketed")


def _slices(counter: Counter[str], total: int | None = None, top: int = 8) -> list[Slice]:
    total = total if total is not None else sum(counter.values())
    return [
        Slice(label=_humanize(k), count=v, share=_rate(v, total))
        for k, v in counter.most_common(top)
    ]


# -- sections ----------------------------------------------------------------------------------


def _demand(
    cur: list[CallRecord],
    prev_week: list[CallRecord],
    tz: ZoneInfo,
    now: datetime,
    inp: InsightInputs,
) -> Demand:
    d = Demand()
    days_seen: set[str] = set()
    weekday_occurrences: Counter[int] = Counter()
    for c in cur:
        local = c.started_at.astimezone(tz)
        d.heatmap[local.weekday()][local.hour] += 1
        days_seen.add(local.strftime("%Y-%m-%d"))
        if not _in_hours(local, inp.schedule):
            d.after_hours_calls += 1
            if _answered(c):
                d.would_have_missed_by_hour[local.hour] += 1
    # weekday occurrences inside the window (for per-occurrence averages)
    if cur:
        first = min(c.started_at for c in cur).astimezone(tz).date()
        last = now.astimezone(tz).date()
        day = first
        while day <= last:
            weekday_occurrences[day.weekday()] += 1
            day += timedelta(days=1)
    by_hour = [sum(row[h] for row in d.heatmap) for h in range(24)]
    n_days = max(len(days_seen), 1)
    d.avg_by_hour = [round(v / n_days, 2) for v in by_hour]
    d.avg_by_weekday = [
        round(sum(d.heatmap[w]) / weekday_occurrences[w], 2) if weekday_occurrences[w] else 0.0
        for w in range(7)
    ]

    # concurrency: sweep over answered call intervals
    events: list[tuple[datetime, int]] = []
    for c in cur:
        if not _answered(c):
            continue
        end = c.ended_at or (c.started_at + timedelta(seconds=c.duration_s or 0))
        events.append((c.started_at, 1))
        events.append((end, -1))
    events.sort(key=lambda e: (e[0], e[1]))
    live = 0
    for at, delta in events:
        live += delta
        if delta > 0 and live > 1:
            d.overlapping_calls += 1
            d.would_have_missed_by_hour[at.astimezone(tz).hour] += 1
        if live > d.peak_concurrency:
            d.peak_concurrency, d.peak_concurrency_at = live, at
    d.would_have_missed_total = sum(d.would_have_missed_by_hour)

    # forecast: per-weekday mean of the last 4 weeks, nudged by the 2-week trend, ±1 sd
    weeks: dict[int, list[int]] = defaultdict(list)
    week_totals: list[int] = []
    for w in range(4):
        w_end = now - timedelta(days=7 * w)
        w_start = w_end - timedelta(days=7)
        chunk = [c for c in cur if w_start <= c.started_at < w_end]
        week_totals.append(len(chunk))
        per_day: Counter[int] = Counter(c.started_at.astimezone(tz).weekday() for c in chunk)
        for wd in range(7):
            weeks[wd].append(per_day[wd])
    recent, older = sum(week_totals[:2]), sum(week_totals[2:])
    trend = min(max((recent / older) if older else 1.0, 0.7), 1.3)
    hour_profile: dict[int, Counter[int]] = defaultdict(Counter)
    for c in cur:
        local = c.started_at.astimezone(tz)
        hour_profile[local.weekday()][local.hour] += 1
    today = now.astimezone(tz).date()
    for i in range(1, 8):
        day = today + timedelta(days=i)
        xs = weeks[day.weekday()]
        mean = sum(xs) / len(xs) if xs else 0.0
        var = sum((x - mean) ** 2 for x in xs) / len(xs) if xs else 0.0
        sd = var**0.5
        expected = round(mean * trend, 1)
        d.forecast.append(
            ForecastDay(
                day=day.strftime("%Y-%m-%d"),
                weekday=WEEKDAYS[day.weekday()].capitalize(),
                expected_calls=expected,
                low=round(max(expected - sd, 0), 1),
                high=round(expected + sd, 1),
                busiest_hours=[h for h, _ in hour_profile[day.weekday()].most_common(3)],
            )
        )
    d.forecast_total = round(sum(f.expected_calls for f in d.forecast), 1)
    d.forecast_vs_last_week_pct = _pct(d.forecast_total, len(prev_week))

    ranked = sorted(range(24), key=lambda h: -by_hour[h])
    total = sum(by_hour)
    d.busiest_hours = [
        Slice(label=f"{h:02d}:00", count=by_hour[h], share=_rate(by_hour[h], total))
        for h in ranked[:5]
        if by_hour[h]
    ]
    active = [h for h in range(24) if 7 <= h <= 20]
    d.quietest_hours = [
        Slice(label=f"{h:02d}:00", count=by_hour[h], share=_rate(by_hour[h], total))
        for h in sorted(active, key=lambda h: by_hour[h])[:3]
    ]
    return d


def _resolution(
    cur: list[CallRecord],
    tickets: list[Ticket],
    transfers: list[TransferRecord],
    outbound: list[dict[str, Any]],
) -> Resolution:
    r = Resolution()
    answered = [c for c in cur if _answered(c)]
    call_ids = {c.call_id for c in cur}
    r.transferred = sum(1 for c in answered if c.transfers)
    r.transfer_answered = sum(
        1 for t in transfers if t.call_id in call_ids and t.outcome in ("answered", "completed")
    )
    r.ticketed = sum(1 for c in answered if c.ticket_ids)
    r.resolved_by_ai = sum(
        1 for c in answered if not c.transfers and not c.ticket_ids and not c.escalated
    )
    cb = [o for o in outbound if o.get("purpose") == "ticket_callback" and o.get("attempts")]
    r.ai_callbacks = len(cb)
    r.ai_callbacks_resolved = sum(1 for o in cb if o.get("outcome") in ("resolved", "booked"))
    resolved_tickets = [t for t in tickets if t.resolved_at is not None]
    r.resolved_by_human = len(resolved_tickets)
    r.first_contact_resolution_rate = _rate(r.resolved_by_ai + r.transfer_answered, len(answered))
    r.leakage_rate = _rate(r.ticketed + (r.transferred - r.transfer_answered), len(answered))
    r.median_time_to_resolve_h = _median(
        [_hours(t.created_at, t.resolved_at) for t in resolved_tickets if t.resolved_at]
    )
    n = len(answered)
    steps = [
        ("answered", "Answered", n),
        ("ai", "Resolved by AI", r.resolved_by_ai),
        ("transferred", "Transferred", r.transferred),
        ("transfer_answered", "Transfer answered", r.transfer_answered),
        ("ticketed", "Ticket / callback", r.ticketed),
        ("ai_callback", "AI call back", r.ai_callbacks),
        ("human", "Resolved by team", r.resolved_by_human),
    ]
    r.funnel = [FunnelStep(key=k, label=lbl, count=v, share=_rate(v, n)) for k, lbl, v in steps]
    per_intent: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for c in answered:
        key = detect_intent(c) or "other"
        per_intent[key][1] += 1
        if not c.transfers and not c.ticket_ids and not c.escalated:
            per_intent[key][0] += 1
    r.by_intent = sorted(
        (
            Slice(label=_humanize(k), count=tot, share=_rate(ai, tot), value=float(ai))
            for k, (ai, tot) in per_intent.items()
        ),
        key=lambda s: -s.count,
    )[:8]
    return r


def _sla(
    tickets: list[Ticket],
    events: dict[str, list[TicketEvent]],
    outbound: list[dict[str, Any]],
    now: datetime,
    tz: ZoneInfo,
) -> Sla:
    s = Sla(tickets=len(tickets))
    claims: list[float] = []
    callbacks: list[float] = []
    resolves: list[float] = []
    for t in tickets:
        evs = sorted(events.get(t.id, []), key=lambda e: e.at)
        claimed = next((e.at for e in evs if e.type in ("claimed", "assigned")), None)
        if claimed:
            claims.append(_hours(t.created_at, claimed))
        cb = next((e.at for e in evs if e.type == "callback"), None)
        if cb:
            callbacks.append(_hours(t.created_at, cb))
        if t.resolved_at:
            resolves.append(_hours(t.created_at, t.resolved_at))
        if any(e.type == "reopened" for e in evs):
            s.reopened += 1
        if t.sla_breached:
            s.breached += 1
    s.median_time_to_claim_h = _median(claims)
    s.median_time_to_first_callback_h = _median(callbacks)
    s.median_time_to_resolve_h = _median(resolves)
    s.breach_rate = _rate(s.breached, len(tickets))
    s.reopen_rate = _rate(s.reopened, len(tickets))
    cb_jobs = [
        o
        for o in outbound
        if o.get("purpose") == "ticket_callback" and o.get("status") in ("completed", "exhausted")
    ]
    first = sum(
        1
        for o in cb_jobs
        if o.get("outcome") in ("resolved", "booked", "confirmed")
        and len(o.get("attempts", [])) == 1
    )
    s.callback_first_attempt_rate = _rate(first, len(cb_jobs))
    open_by_dept: dict[str, list[float]] = defaultdict(list)
    for t in tickets:
        if t.status in (TicketStatus.OPEN, TicketStatus.CLAIMED):
            open_by_dept[t.department or "unassigned"].append(_hours(t.created_at, now))
    s.backlog = sorted(
        (
            Backlog(
                department=_humanize(k),
                open=len(v),
                oldest_h=round(max(v), 1),
                avg_age_h=round(sum(v) / len(v), 1),
            )
            for k, v in open_by_dept.items()
        ),
        key=lambda b: -b.open,
    )
    weekly: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for t in tickets:
        wk = t.created_at.astimezone(tz).strftime("%G-W%V")
        weekly[wk][1] += 1
        if t.sla_breached:
            weekly[wk][0] += 1
    s.breach_trend = [
        Slice(label=wk, count=b, share=_rate(b, n)) for wk, (b, n) in sorted(weekly.items())[-8:]
    ]
    return s


def _transfers(
    transfers: list[TransferRecord], tz: ZoneInfo, inp: InsightInputs
) -> TransferQuality:
    q = TransferQuality(attempts=len(transfers))
    ok = ("answered", "completed")
    by_dept: dict[str, list[TransferRecord]] = defaultdict(list)
    by_dest: dict[str, list[TransferRecord]] = defaultdict(list)
    human_s: list[float] = []
    for t in transfers:
        by_dept[t.department or "unassigned"].append(t)
        by_dest[t.destination].append(t)
        q.by_hour[t.started_at.astimezone(tz).hour] += 1
        if t.outcome in ok:
            q.answered += 1
            if t.human_duration_s:
                human_s.append(t.human_duration_s)
        elif t.outcome in ("no_answer", "busy", "failed", "abandoned", "cancelled"):
            q.abandoned += 1
        if t.recorded:
            q.recorded += 1
    q.answer_rate = _rate(q.answered, q.attempts)
    q.avg_human_s = round(sum(human_s) / len(human_s), 1) if human_s else None
    q.human_minutes = round(sum(human_s) / 60, 1)

    def row(label: str, ts: list[TransferRecord]) -> TransferRow:
        ans = [t for t in ts if t.outcome in ok]
        hs = [t.human_duration_s for t in ans if t.human_duration_s]
        return TransferRow(
            label=_humanize(label),
            attempts=len(ts),
            answered=len(ans),
            answer_rate=_rate(len(ans), len(ts)),
            abandoned=sum(
                1
                for t in ts
                if t.outcome in ("no_answer", "busy", "failed", "abandoned", "cancelled")
            ),
            avg_human_s=round(sum(hs) / len(hs), 1) if hs else None,
            human_minutes=round(sum(hs) / 60, 1),
        )

    q.by_department = sorted((row(k, v) for k, v in by_dept.items()), key=lambda r: -r.attempts)
    q.by_destination = sorted((row(k, v) for k, v in by_dest.items()), key=lambda r: -r.attempts)[
        :10
    ]
    # a transfer costs the human's time (or a default handle time when we didn't record it)
    per_transfer_min = (q.avg_human_s or inp.human_handle_minutes * 60) / 60
    q.est_transfer_cost_pence = int(
        q.answered * per_transfer_min / 60 * inp.human_hourly_cost_pence
    )
    q.est_ai_cost_pence = int(q.answered * inp.human_handle_minutes * inp.ai_minute_cost_pence)
    return q


def _intents(
    cur: list[CallRecord],
    prev: list[CallRecord],
    qa: list[dict[str, Any]],
    insights: list[dict[str, Any]],
    value: ValueSettings,
) -> Intents:
    out = Intents()
    cur_c: Counter[str] = Counter(detect_intent(c) or "other" for c in cur)
    prev_c: Counter[str] = Counter(detect_intent(c) or "other" for c in prev)
    per_intent_value = value.avg_job_value_pence * value.lead_to_sale_rate
    total = sum(cur_c.values())
    out.top = [
        Slice(
            label=_humanize(k),
            count=v,
            share=_rate(v, total),
            value=float(int(v * per_intent_value)),
        )
        for k, v in cur_c.most_common(10)
    ]
    moves: list[Slice] = []
    for k in set(cur_c) | set(prev_c):
        if cur_c[k] + prev_c[k] < 3:
            continue
        change = _pct(cur_c[k], prev_c[k])
        if change is None and cur_c[k]:
            change = 100.0
        moves.append(Slice(label=_humanize(k), count=cur_c[k], value=change))
    moves = [m for m in moves if m.value is not None]
    out.rising = sorted((m for m in moves if (m.value or 0) > 0), key=lambda m: -(m.value or 0))[:5]
    out.falling = sorted((m for m in moves if (m.value or 0) < 0), key=lambda m: m.value or 0)[:5]
    call_ids = {c.call_id for c in cur}
    out.unanswered_questions = sum(
        len(q.get("unanswered", [])) for q in qa if q.get("call_id") in call_ids
    )
    gaps: list[Gap] = []
    for i in insights:
        if i.get("status") in ("dismissed", "applied"):
            continue
        n = int(i.get("count", 1))
        gaps.append(
            Gap(
                question=str(i.get("question", "")),
                count=n,
                status=str(i.get("status", "open")),
                est_value_pence=int(n * per_intent_value),
            )
        )
    out.gaps = sorted(gaps, key=lambda g: -g.count)[:10]
    out.est_gap_value_pence = sum(g.est_value_pence for g in gaps)
    return out


def _revenue(
    cur: list[CallRecord],
    contacts: list[Contact],
    bookings: list[dict[str, Any]],
    tracking: list[TrackingNumber],
    value: ValueSettings,
    tz: ZoneInfo,
) -> Revenue:
    r = Revenue(
        currency=value.currency, calls=len(cur), avg_job_value_pence=value.avg_job_value_pence
    )
    booked_calls = {b.get("call_id") for b in bookings if b.get("call_id")}
    leads = [c for c in cur if is_qualified_lead(c)]
    r.leads = len(leads)
    r.bookings = sum(1 for b in bookings if b.get("status") != "cancelled")
    r.lead_rate = _rate(r.leads, len(cur))
    r.booking_rate = _rate(r.bookings, r.leads or len(cur))
    booking_value = value.booking_value_pence or value.avg_job_value_pence
    r.attributed_pence = int(
        r.bookings * booking_value
        + sum(1 for c in leads if c.call_id not in booked_calls)
        * value.avg_job_value_pence
        * value.lead_to_sale_rate
    )
    missed = [c for c in cur if c.kind == "missed"]
    r.missed_pence = int(
        len(missed)
        * value.missed_call_lead_rate
        * value.lead_to_sale_rate
        * value.avg_job_value_pence
    )
    unresolved = [c for c in leads if c.ticket_ids and c.call_id not in booked_calls]
    r.unresolved_pence = int(len(unresolved) * value.avg_job_value_pence * value.lead_to_sale_rate)
    by_call = {c.call_id: c for c in cur}
    l2b: list[float] = []
    for b in bookings:
        c = by_call.get(str(b.get("call_id")))
        created = _parse_dt(b.get("created_at"))
        if c and created and created >= c.started_at:
            l2b.append(_hours(c.started_at, created))
    r.median_lead_to_booking_h = _median(l2b)
    callers = Counter(c.party for c in cur if c.party)
    r.repeat_caller_share = _rate(sum(1 for _, n in callers.items() if n > 1), len(callers))
    per_intent: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for c in leads:
        key = detect_intent(c) or "other"
        per_intent[key][0] += 1
        per_intent[key][1] += (
            booking_value
            if c.call_id in booked_calls
            else int(value.avg_job_value_pence * value.lead_to_sale_rate)
        )
    r.by_intent = sorted(
        (Slice(label=_humanize(k), count=n, value=float(v)) for k, (n, v) in per_intent.items()),
        key=lambda s: -(s.value or 0),
    )[:8]
    channel_of = {t.e164: t.channel for t in tracking}
    per_src: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for c in leads:
        src = channel_of.get(c.dialed or "", "Direct") if c.direction == "inbound" else "Call back"
        per_src[src][0] += 1
        per_src[src][1] += int(value.avg_job_value_pence * value.lead_to_sale_rate)
    r.by_source = sorted(
        (Slice(label=k, count=n, value=float(v)) for k, (n, v) in per_src.items()),
        key=lambda s: -s.count,
    )
    hour_calls, hour_leads = [0] * 24, [0] * 24
    for c in cur:
        h = c.started_at.astimezone(tz).hour
        hour_calls[h] += 1
        if is_qualified_lead(c):
            hour_leads[h] += 1
    r.by_hour = [
        round(hour_leads[h] / hour_calls[h], 3) if hour_calls[h] else 0.0 for h in range(24)
    ]
    top = sorted(contacts, key=lambda c: -c.call_count)[:5]
    r.top_customers = [
        Slice(
            label=c.name or c.e164,
            count=c.call_count,
            value=float(c.call_count * value.avg_job_value_pence * value.lead_to_sale_rate),
        )
        for c in top
    ]
    return r


def _cx(cur: list[CallRecord], qa: list[dict[str, Any]], tz: ZoneInfo) -> CustomerExperience:
    cx = CustomerExperience()
    by_call = {c.call_id: c for c in cur}
    scored = [q for q in qa if q.get("call_id") in by_call]
    cx.qa_scored = len(scored)
    if scored:
        cx.avg_qa = round(sum(int(q.get("overall", 0)) for q in scored) / len(scored), 1)
        cx.avg_tone = round(sum(int(q.get("tone", 0)) for q in scored) / len(scored), 1)
        cx.avg_resolution = round(sum(int(q.get("resolution", 0)) for q in scored) / len(scored), 1)
    frustrated_ids: set[str] = set()
    for q in scored:
        flags = set(q.get("flags", []))
        if flags & {"tone", "unresolved", "abrupt_end", "escalated"} or int(q.get("tone", 10)) <= 4:
            frustrated_ids.add(str(q.get("call_id")))
    for c in cur:
        if c.escalated or detect_intent(c) == "complaint":
            frustrated_ids.add(c.call_id)
    cx.frustrated = len(frustrated_ids)
    cx.frustration_rate = _rate(cx.frustrated, len(cur))
    by_party: dict[str, list[datetime]] = defaultdict(list)
    for c in cur:
        if c.party:
            by_party[c.party].append(c.started_at)
    for times in by_party.values():
        times.sort()
        for a, b in pairwise(times):
            if b - a <= timedelta(days=7):
                cx.repeat_within_7d += 1
    cx.repeat_rate = _rate(cx.repeat_within_7d, len(cur))
    for c in cur:
        for f in c.feedback:
            kind = str(f.get("type", ""))
            if kind in ("positive", "thumbs_up", "good"):
                cx.positive_feedback += 1
            elif kind in ("negative", "thumbs_down", "bad", "complaint"):
                cx.negative_feedback += 1
    weekly: dict[str, list[int]] = defaultdict(list)
    per_intent: dict[str, list[int]] = defaultdict(list)
    per_version: dict[str, list[int]] = defaultdict(list)
    for q in scored:
        c = by_call[str(q.get("call_id"))]
        weekly[c.started_at.astimezone(tz).strftime("%G-W%V")].append(int(q.get("overall", 0)))
        per_intent[detect_intent(c) or "other"].append(int(q.get("overall", 0)))
        per_version[str(q.get("scorer", "heuristic"))].append(int(q.get("overall", 0)))
    cx.qa_trend = [
        Slice(label=wk, count=len(v), value=round(sum(v) / len(v), 1))
        for wk, v in sorted(weekly.items())[-8:]
    ]
    cx.qa_by_intent = sorted(
        (
            Slice(label=_humanize(k), count=len(v), value=round(sum(v) / len(v), 1))
            for k, v in per_intent.items()
        ),
        key=lambda s: -s.count,
    )[:8]
    cx.qa_by_version = [
        Slice(label=_humanize(k), count=len(v), value=round(sum(v) / len(v), 1))
        for k, v in sorted(per_version.items())
    ]
    return cx


def _workforce(
    tickets: list[Ticket],
    events: dict[str, list[TicketEvent]],
    transfers: list[TransferRecord],
    members: list[Member],
    tz: ZoneInfo,
) -> Workforce:
    w = Workforce()
    label_of: dict[str, str] = {}
    for m in members:
        for key in (m.user_id, m.email):
            if key:
                label_of[key] = m.name or m.email or m.user_id
    rows: dict[str, MemberRow] = {}
    res_h: dict[str, list[float]] = defaultdict(list)
    days: dict[str, set[str]] = defaultdict(set)

    def row(actor: str) -> MemberRow:
        key = label_of.get(actor, actor)
        return rows.setdefault(key, MemberRow(label=key))

    for t in tickets:
        if t.status in (TicketStatus.OPEN, TicketStatus.CLAIMED) and not t.assigned_to:
            w.unassigned_open += 1
        for e in events.get(t.id, []):
            if not e.actor or e.actor in ("system", "assistant", "ai"):
                continue
            r = row(e.actor)
            days[r.label].add(e.at.astimezone(tz).strftime("%Y-%m-%d"))
            if e.type in ("claimed", "assigned"):
                r.claimed += 1
            elif e.type == "resolved":
                r.resolved += 1
                res_h[r.label].append(_hours(t.created_at, e.at))
            elif e.type == "callback":
                r.callbacks += 1
            elif e.type == "note":
                r.notes += 1
    for tr in transfers:
        r = row(tr.destination_id or tr.destination)
        if tr.outcome in ("answered", "completed"):
            r.transfers_answered += 1
        else:
            r.transfers_missed += 1
        days[r.label].add(tr.started_at.astimezone(tz).strftime("%Y-%m-%d"))
    for label, r in rows.items():
        r.active_days = len(days[label])
        hs = res_h[label]
        r.avg_resolution_h = round(sum(hs) / len(hs), 1) if hs else None
    if rows:
        w.team_avg_resolved = round(sum(r.resolved for r in rows.values()) / len(rows), 1)
        all_h = [h for hs in res_h.values() for h in hs]
        w.team_avg_resolution_h = round(sum(all_h) / len(all_h), 1) if all_h else None
        for r in rows.values():
            r.vs_team_pct = _pct(r.resolved, w.team_avg_resolved or 0)
    w.members = sorted(
        rows.values(), key=lambda r: -(r.resolved + r.claimed + r.transfers_answered)
    )
    return w


def _attribution(
    cur: list[CallRecord],
    bookings: list[dict[str, Any]],
    tracking: list[TrackingNumber],
    value: ValueSettings,
) -> Attribution:
    a = Attribution()
    channel_of = {t.e164: t.channel for t in tracking}
    calls_c: Counter[str] = Counter()
    leads_c: Counter[str] = Counter()
    book_c: Counter[str] = Counter()
    booked = {b.get("call_id") for b in bookings if b.get("call_id")}
    for c in cur:
        if c.direction != "inbound":
            continue
        ch = channel_of.get(c.dialed or "")
        if ch is None:
            a.untracked_calls += 1
            ch = "Direct / untracked"
        calls_c[ch] += 1
        if is_qualified_lead(c):
            leads_c[ch] += 1
        if c.call_id in booked:
            book_c[ch] += 1
    total = sum(calls_c.values())
    booking_value = value.booking_value_pence or value.avg_job_value_pence
    a.channels = [
        Slice(
            label=ch,
            count=n,
            share=_rate(n, total),
            value=float(
                book_c[ch] * booking_value
                + (leads_c[ch] - book_c[ch]) * value.avg_job_value_pence * value.lead_to_sale_rate
            ),
        )
        for ch, n in calls_c.most_common()
    ]
    a.leads_by_channel = [Slice(label=ch, count=n) for ch, n in leads_c.most_common()]
    a.bookings_by_channel = [Slice(label=ch, count=n) for ch, n in book_c.most_common()]
    return a


def _cost(
    cur: list[CallRecord],
    transfers: list[TransferRecord],
    resolution: Resolution,
    inp: InsightInputs,
) -> Cost:
    c = Cost()
    answered = [x for x in cur if _answered(x)]
    c.ai_minutes = round(sum(x.duration_s or 0 for x in answered) / 60, 1)
    c.human_minutes = round(sum(t.human_duration_s or 0 for t in transfers) / 60, 1)
    c.ai_share = _rate(c.ai_minutes, c.ai_minutes + c.human_minutes)
    c.ai_handled_calls = resolution.resolved_by_ai
    c.hours_saved = round(len(answered) * inp.human_handle_minutes / 60, 1)
    c.est_human_cost_pence = int(c.hours_saved * inp.human_hourly_cost_pence)
    c.est_ai_cost_pence = int(c.ai_minutes * inp.ai_minute_cost_pence)
    resolved = (
        resolution.resolved_by_ai + resolution.transfer_answered + resolution.resolved_by_human
    )
    c.cost_per_resolved_pence = int(c.est_ai_cost_pence / resolved) if resolved else None
    return c


def _trend_point(
    period: str,
    label: str,
    calls: list[CallRecord],
    bookings: int,
    value: ValueSettings,
) -> TrendPoint:
    answered = sum(1 for c in calls if _answered(c))
    leads = sum(1 for c in calls if is_qualified_lead(c))
    return TrendPoint(
        period=period,
        label=label,
        calls=len(calls),
        answered=answered,
        missed=sum(1 for c in calls if c.kind == "missed"),
        transfers=sum(1 for c in calls if c.transfers),
        tickets=sum(len(c.ticket_ids) for c in calls),
        bookings=bookings,
        leads=leads,
        answer_rate=_rate(answered, len(calls)),
        est_value_pence=int(
            bookings * (value.booking_value_pence or value.avg_job_value_pence)
            + max(leads - bookings, 0) * value.avg_job_value_pence * value.lead_to_sale_rate
        ),
    )


def _trends(
    all_calls: list[CallRecord],
    cur: list[CallRecord],
    bookings: list[dict[str, Any]],
    value: ValueSettings,
    tz: ZoneInfo,
    now: datetime,
    days: int,
) -> Trends:
    t = Trends(window_days=days)
    window = [c for c in all_calls if c.started_at >= now - timedelta(days=days)]
    active_days = {c.started_at.astimezone(tz).strftime("%Y-%m-%d") for c in window}
    by_hour = [0] * 24
    by_wd = [0] * 7
    wd_occ: Counter[int] = Counter()
    for c in window:
        local = c.started_at.astimezone(tz)
        by_hour[local.hour] += 1
        by_wd[local.weekday()] += 1
    if window:
        day = (now - timedelta(days=days)).astimezone(tz).date()
        end = now.astimezone(tz).date()
        while day <= end:
            wd_occ[day.weekday()] += 1
            day += timedelta(days=1)
    n_days = max(len(active_days), 1)
    t.avg_by_hour = [round(v / n_days, 2) for v in by_hour]
    t.avg_by_weekday = [round(by_wd[w] / wd_occ[w], 2) if wd_occ[w] else 0.0 for w in range(7)]

    booking_month: Counter[str] = Counter()
    for b in bookings:
        created = _parse_dt(b.get("created_at"))
        if created and b.get("status") != "cancelled":
            booking_month[created.astimezone(tz).strftime("%Y-%m")] += 1
    months: dict[str, list[CallRecord]] = defaultdict(list)
    for c in all_calls:
        months[c.started_at.astimezone(tz).strftime("%Y-%m")].append(c)
    keys = sorted(months)
    if keys:
        # fill gaps so the line is continuous
        first = datetime.strptime(keys[0], "%Y-%m")
        last = now.astimezone(tz).replace(tzinfo=None)
        y, m = first.year, first.month
        while (y, m) <= (last.year, last.month):
            months.setdefault(f"{y:04d}-{m:02d}", [])
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for k in sorted(months)[-24:]:
        label = datetime.strptime(k, "%Y-%m").strftime("%b %Y")
        t.monthly.append(_trend_point(k, label, months[k], booking_month[k], value))
    quarters: dict[str, list[CallRecord]] = defaultdict(list)
    q_book: Counter[str] = Counter()
    years: dict[str, list[CallRecord]] = defaultdict(list)
    y_book: Counter[str] = Counter()
    for k, cs in months.items():
        yr, mo = k.split("-")
        qk = f"{yr}-Q{(int(mo) - 1) // 3 + 1}"
        quarters[qk].extend(cs)
        q_book[qk] += booking_month[k]
        years[yr].extend(cs)
        y_book[yr] += booking_month[k]
    t.quarterly = [
        _trend_point(k, k.replace("-", " "), quarters[k], q_book[k], value)
        for k in sorted(quarters)
    ][-8:]
    t.yearly = [_trend_point(k, k, years[k], y_book[k], value) for k in sorted(years)][-5:]

    intent_month: Counter[tuple[str, str]] = Counter()
    for k in sorted(months)[-12:]:
        for c in months[k]:
            intent_month[(detect_intent(c) or "other", k)] += 1
    t.intent_monthly = [
        Slice(label=f"{_humanize(i)}|{k}", count=n) for (i, k), n in sorted(intent_month.items())
    ]
    day_counts: Counter[str] = Counter(
        c.started_at.astimezone(tz).strftime("%Y-%m-%d") for c in window
    )
    t.busiest_days = [Slice(label=d, count=n) for d, n in day_counts.most_common(5)]
    month_counts = [(k, len(v)) for k, v in months.items() if v]
    t.busiest_months = [
        Slice(label=datetime.strptime(k, "%Y-%m").strftime("%b %Y"), count=n)
        for k, n in sorted(month_counts, key=lambda kv: -kv[1])[:3]
    ]
    t.this_period = _trend_point("current", "This period", cur, 0, value)
    ly_end = now - timedelta(days=365)
    ly_start = ly_end - timedelta(days=days)
    last_year = [c for c in all_calls if ly_start <= c.started_at < ly_end]
    if last_year:
        t.same_period_last_year = _trend_point(
            "last_year", "Same period last year", last_year, 0, value
        )
        t.yoy_calls_pct = _pct(len(cur), len(last_year))
    return t


# -- entry point -------------------------------------------------------------------------------


def build_insights(
    inp: InsightInputs,
    *,
    days: int = 30,
    timezone: str = "Europe/London",
    now: datetime | None = None,
) -> InsightsReport:
    tz = ZoneInfo(timezone)
    now = now or datetime.now(UTC)
    start = now - timedelta(days=days)
    prev_start = start - timedelta(days=days)
    value = inp.value or ValueSettings(tenant_id=inp.tenant_id)

    calls = [c for c in inp.calls if c.tenant_id == inp.tenant_id]
    cur = [c for c in calls if start <= c.started_at < now]
    prev = [c for c in calls if prev_start <= c.started_at < start]
    prev_week = [c for c in calls if now - timedelta(days=7) <= c.started_at < now]
    tickets = [t for t in inp.tickets if t.tenant_id == inp.tenant_id and t.created_at >= start]
    transfers = [t for t in inp.transfers if t.tenant_id == inp.tenant_id and t.started_at >= start]

    def docs(items: list[TenantDoc], since: datetime | None = start) -> list[dict[str, Any]]:
        return [
            d.data
            for d in items
            if d.tenant_id == inp.tenant_id and (since is None or d.created_at >= since)
        ]

    bookings = docs(inp.bookings)
    outbound = docs(inp.outbound)
    qa = docs(inp.qa_scores)
    faq_insights = docs(inp.faq_insights, since=None)
    contacts = [c for c in inp.contacts if c.tenant_id == inp.tenant_id]
    members = [m for m in inp.members if m.tenant_id == inp.tenant_id]

    resolution = _resolution(cur, tickets, transfers, outbound)
    return InsightsReport(
        tenant_id=inp.tenant_id,
        timezone=timezone,
        start=start,
        end=now,
        days=days,
        generated_at=now,
        demand=_demand(cur, prev_week, tz, now, inp),
        resolution=resolution,
        sla=_sla(tickets, inp.ticket_events, outbound, now, tz),
        transfers=_transfers(transfers, tz, inp),
        intents=_intents(cur, prev, qa, faq_insights, value),
        revenue=_revenue(cur, contacts, bookings, inp.tracking_numbers, value, tz),
        cx=_cx(cur, qa, tz),
        workforce=_workforce(tickets, inp.ticket_events, transfers, members, tz),
        attribution=_attribution(cur, docs(inp.bookings), inp.tracking_numbers, value),
        cost=_cost(cur, transfers, resolution, inp),
        trends=_trends(calls, cur, docs(inp.bookings, since=None), value, tz, now, days),
    )
