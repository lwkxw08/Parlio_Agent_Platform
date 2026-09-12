"""Segmented analytics + natural-language ("Ask AI") query parsing.

A `Segment` is a date range with optional hour/weekday filters ("last month after hours",
"weekends in November"). `compute_segment` produces the Overview read model for one segment;
`compare` runs two. `parse_question` turns free text into a `Question` deterministically (dates,
months, quarters, weekend/weekday, business/after hours, "X vs Y"); when an LLM key is
configured the API tries it first and falls back to the rules.
"""

from __future__ import annotations

import calendar
import logging
import re
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field

from parlio_api.analytics import DailyPoint, PeriodSummary, _pct_change, summarise
from parlio_api.store import CallRecord, Contact
from parlio_voice.models import WEEKDAYS, Schedule

log = logging.getLogger("parlio.analytics")

Hours = Literal["all", "business", "after"]
DayFilter = Literal["all", "weekdays", "weekends"]


class Segment(BaseModel):
    start: date
    end: date  # inclusive
    hours: Hours = "all"
    days: DayFilter = "all"
    label: str | None = None

    def describe(self) -> str:
        span = f"{self.start:%-d %b} to {self.end:%-d %b %Y}"
        parts = [span]
        if self.days != "all":
            parts.append(self.days)
        if self.hours != "all":
            parts.append("business hours" if self.hours == "business" else "after hours")
        return ", ".join(parts)


class Question(BaseModel):
    period: Segment
    compare: Segment | None = None
    interpretation: str = ""
    source: Literal["rules", "llm"] = "rules"


class Breakdown(BaseModel):
    name: str
    count: int


class SegmentAnalytics(BaseModel):
    segment: Segment
    summary: PeriodSummary
    business_hours_calls: int = 0
    after_hours_calls: int = 0
    by_hour: list[int] = Field(default_factory=lambda: [0] * 24)
    by_weekday: list[int] = Field(default_factory=lambda: [0] * 7)
    daily: list[DailyPoint] = Field(default_factory=list)
    by_department: list[Breakdown] = Field(default_factory=list)
    by_outcome: list[Breakdown] = Field(default_factory=list)
    first_time_callers: int = 0
    returning_callers: int = 0
    transfers_total: int = 0
    transfers_answered: int = 0
    tickets: int = 0


class ComparisonAnalytics(BaseModel):
    timezone: str
    question: Question | None = None
    current: SegmentAnalytics
    compare: SegmentAnalytics | None = None
    change: dict[str, float | None] = Field(default_factory=dict)


# -- segment computation -----------------------------------------------------------------------


def _in_hours(local: datetime, schedule: Schedule) -> bool:
    day = schedule.hours.get(WEEKDAYS[local.weekday()])
    return schedule.always or (day is not None and day.contains(local.time().replace(tzinfo=None)))


def _matches(local: datetime, seg: Segment, schedule: Schedule) -> bool:
    if seg.days == "weekdays" and local.weekday() >= 5:
        return False
    if seg.days == "weekends" and local.weekday() < 5:
        return False
    return seg.hours == "all" or _in_hours(local, schedule) == (seg.hours == "business")


def compute_segment(
    calls: list[CallRecord],
    contacts: list[Contact],
    seg: Segment,
    *,
    schedule: Schedule | None = None,
    timezone: str = "Europe/London",
) -> SegmentAnalytics:
    tz = ZoneInfo(timezone)
    schedule = schedule or Schedule()
    start = datetime.combine(seg.start, time.min, tz)
    end = datetime.combine(seg.end + timedelta(days=1), time.min, tz)

    sel: list[tuple[CallRecord, datetime]] = []
    for c in calls:
        if not (start <= c.started_at < end):
            continue
        local = c.started_at.astimezone(tz)
        if _matches(local, seg, schedule):
            sel.append((c, local))

    out = SegmentAnalytics(segment=seg, summary=summarise([c for c, _ in sel], start, end))
    daily: dict[str, DailyPoint] = {}
    d = seg.start
    while d <= seg.end:
        daily[d.isoformat()] = DailyPoint(day=d.isoformat())
        d += timedelta(days=1)
    depts: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    for c, local in sel:
        out.by_hour[local.hour] += 1
        out.by_weekday[local.weekday()] += 1
        if _in_hours(local, schedule):
            out.business_hours_calls += 1
        else:
            out.after_hours_calls += 1
        p = daily.setdefault(local.date().isoformat(), DailyPoint(day=local.date().isoformat()))
        p.calls += 1
        if c.kind == "missed":
            p.missed += 1
        elif c.kind in ("answered", "transferred", "ticketed"):
            p.answered += 1
        outcomes[c.kind] += 1
        for t in c.transfers:
            out.transfers_total += 1
            if t.get("outcome") in ("answered", "completed", "bridged"):
                out.transfers_answered += 1
            depts[str(t.get("department") or "general")] += 1
        if not c.transfers and (dep := c.extracted.get("department")):
            depts[str(dep)] += 1
        out.tickets += len(c.ticket_ids)
        if c.caller_type == "new":
            out.first_time_callers += 1
        elif c.caller_type == "returning":
            out.returning_callers += 1

    out.daily = sorted(daily.values(), key=lambda p: p.day)
    out.by_department = [Breakdown(name=k, count=v) for k, v in depts.most_common()]
    out.by_outcome = [Breakdown(name=k, count=v) for k, v in outcomes.most_common()]
    return out


def compare(
    calls: list[CallRecord],
    contacts: list[Contact],
    q: Question,
    *,
    schedule: Schedule | None = None,
    timezone: str = "Europe/London",
) -> ComparisonAnalytics:
    cur = compute_segment(calls, contacts, q.period, schedule=schedule, timezone=timezone)
    cmp = (
        compute_segment(calls, contacts, q.compare, schedule=schedule, timezone=timezone)
        if q.compare
        else None
    )
    change: dict[str, float | None] = {}
    if cmp:
        a, b = cur.summary, cmp.summary
        change = {
            "total_calls": _pct_change(a.total_calls, b.total_calls),
            "avg_duration_s": _pct_change(a.avg_duration_s, b.avg_duration_s),
            "answer_rate": _pct_change(a.answer_rate, b.answer_rate),
            "unique_callers": _pct_change(a.unique_callers, b.unique_callers),
            "new_callers": _pct_change(a.new_callers, b.new_callers),
            "answered": _pct_change(a.answered, b.answered),
            "missed": _pct_change(a.missed, b.missed),
            "total_minutes": _pct_change(a.total_minutes, b.total_minutes),
            "avg_calls_per_caller": _pct_change(a.avg_calls_per_caller, b.avg_calls_per_caller),
            "first_time_callers": _pct_change(cur.first_time_callers, cmp.first_time_callers),
            "returning_callers": _pct_change(cur.returning_callers, cmp.returning_callers),
            "transfers_total": _pct_change(cur.transfers_total, cmp.transfers_total),
            "transfers_answered": _pct_change(cur.transfers_answered, cmp.transfers_answered),
            "tickets": _pct_change(cur.tickets, cmp.tickets),
            "business_hours_calls": _pct_change(cur.business_hours_calls, cmp.business_hours_calls),
            "after_hours_calls": _pct_change(cur.after_hours_calls, cmp.after_hours_calls),
        }
    return ComparisonAnalytics(
        timezone=timezone, question=q, current=cur, compare=cmp, change=change
    )


# -- natural-language parsing -------------------------------------------------------------------

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _quarter_range(year: int, q: int) -> tuple[date, date]:
    m = (q - 1) * 3 + 1
    return date(year, m, 1), _month_range(year, m + 2)[1]


def _filters(text: str) -> tuple[Hours, DayFilter]:
    hours: Hours = "all"
    days: DayFilter = "all"
    if re.search(r"after[- ]hours|out of hours|out-of-hours|evenings?|nights?|overnight", text):
        hours = "after"
    elif re.search(r"business hours|office hours|working hours|during the day|daytime", text):
        hours = "business"
    if re.search(r"weekends?|saturdays?|sundays?", text):
        days = "weekends"
    elif re.search(r"weekdays?|mon(day)?[- ]to[- ]fri(day)?|working days", text):
        days = "weekdays"
    return hours, days


def _parse_range(text: str, today: date) -> tuple[date, date] | None:
    t = text.strip().lower()
    if m := re.search(r"last (\d+) days?", t):
        n = int(m.group(1))
        return today - timedelta(days=n - 1), today
    if re.search(r"\b(today)\b", t):
        return today, today
    if re.search(r"\byesterday\b", t):
        return today - timedelta(days=1), today - timedelta(days=1)
    if re.search(r"this week", t):
        return today - timedelta(days=today.weekday()), today
    if re.search(r"last week|previous week", t):
        s = today - timedelta(days=today.weekday() + 7)
        return s, s + timedelta(days=6)
    if re.search(r"this month|month to date|mtd", t):
        return today.replace(day=1), today
    if re.search(r"last month|previous month", t):
        first = today.replace(day=1)
        prev = first - timedelta(days=1)
        return _month_range(prev.year, prev.month)
    if re.search(r"this quarter", t):
        return _quarter_range(today.year, (today.month - 1) // 3 + 1)
    if re.search(r"last quarter|previous quarter", t):
        q = (today.month - 1) // 3 + 1
        return _quarter_range(today.year - 1, 4) if q == 1 else _quarter_range(today.year, q - 1)
    if m := re.search(r"\bq([1-4])\s*(\d{4})?", t):
        return _quarter_range(int(m.group(2) or today.year), int(m.group(1)))
    if re.search(r"this year|year to date|ytd", t):
        return date(today.year, 1, 1), today
    if re.search(r"last year|previous year", t):
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if m := re.search(rf"\b({_MONTH_RE})\b\s*(\d{{4}})?", t):
        month = MONTHS[m.group(1)]
        year = int(m.group(2)) if m.group(2) else today.year
        if not m.group(2) and (month > today.month):
            year -= 1
        return _month_range(year, month)
    if m := re.search(r"\b(\d{4})\b", t):
        y = int(m.group(1))
        return date(y, 1, 1), date(y, 12, 31)
    if m := re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:to|-|until)\s*(\d{4}-\d{2}-\d{2})", t):
        return date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
    return None


def _segment(text: str, today: date, default_days: int = 30) -> Segment:
    rng = _parse_range(text, today)
    if rng is None:
        rng = (today - timedelta(days=default_days - 1), today)
    hours, days = _filters(text.lower())
    return Segment(start=rng[0], end=rng[1], hours=hours, days=days, label=text.strip() or None)


def parse_question(text: str, today: date | None = None) -> Question:
    """Deterministic parser. Splits on 'vs'/'versus'/'compared to'; filters on either side
    apply to both when only one side names them ("weekends: Oct vs Sep")."""
    today = today or datetime.now(UTC).date()
    parts = re.split(r"\s+(?:vs\.?|versus|compared (?:to|with)|against)\s+", text, maxsplit=1)
    period = _segment(parts[0], today)
    compare: Segment | None = None
    if len(parts) == 2:
        compare = _segment(parts[1], today)
        h, d = _filters(text.lower())
        if compare.hours == "all" and h != "all":
            compare.hours = h
        if period.hours == "all" and h != "all":
            period.hours = h
        if compare.days == "all" and d != "all":
            compare.days = d
        if period.days == "all" and d != "all":
            period.days = d
        # a side with only a filter ("business hours vs after hours this month") shares
        # the other side's date range
        if _parse_range(parts[1], today) is None:
            compare.start, compare.end = period.start, period.end
        elif _parse_range(parts[0], today) is None:
            period.start, period.end = compare.start, compare.end
    interp = period.describe() + (f" vs {compare.describe()}" if compare else "")
    return Question(period=period, compare=compare, interpretation=interp, source="rules")


class LlmQuestionParser:
    """OpenAI-compatible JSON-mode parser; falls back to `parse_question` on any error."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=20
        )
        self._model = model

    async def parse(self, text: str, today: date | None = None) -> Question:
        today = today or datetime.now(UTC).date()
        prompt = (
            f"Today is {today.isoformat()} (UK). Convert the analytics question into JSON with "
            'keys "period" and optional "compare", each {"start": "YYYY-MM-DD", "end": '
            '"YYYY-MM-DD", "hours": "all"|"business"|"after", "days": "all"|"weekdays"|'
            '"weekends", "label": <short label>}, plus "interpretation" (one plain-English '
            "sentence). Ranges are inclusive. Use compare only when two periods are contrasted.\n"
            f"Question: {text}"
        )
        try:
            r = await self._client.post(
                "/chat/completions",
                json={
                    "model": self._model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            r.raise_for_status()
            q = Question.model_validate_json(r.json()["choices"][0]["message"]["content"])
            q.source = "llm"
            if not q.interpretation:
                q.interpretation = q.period.describe() + (
                    f" vs {q.compare.describe()}" if q.compare else ""
                )
            return q
        except Exception:
            log.warning("LLM question parse failed; using rules", exc_info=True)
            return parse_question(text, today)
