"""Analytics exports & scheduled reports (Phase 21b leftovers).

* ``insights_csv`` flattens any section of the ``InsightsReport`` into a spreadsheet-friendly
  CSV (one file per section, or ``summary`` for the headline measures).
* ``ReportService`` emails a plain-English weekly/monthly analytics report through the tenant's
  notification rules (event ``analytics.report``), on a schedule the owner sets.
"""

from __future__ import annotations

import asyncio
import csv
import logging
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from parlio_api.insights import InsightsReport, build_insights, load_inputs
from parlio_api.notifications import NotificationEvent, NotificationService, NotifyEvent
from parlio_api.store import CallStore, TenantDoc
from parlio_api.value import ValueService

log = logging.getLogger("parlio.reports")

REPORT_SETTINGS_KIND = "report_schedule"
REPORT_LOG_KIND = "report_sent"
REPORT_TZ = ZoneInfo("Europe/London")

SECTIONS = (
    "summary",
    "demand",
    "forecast",
    "resolution",
    "sla",
    "transfers",
    "intents",
    "gaps",
    "revenue",
    "cx",
    "workforce",
    "attribution",
    "cost",
    "trends",
)
_HOURS = [f"{h:02d}:00" for h in range(24)]
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _pct(x: float | None) -> str:
    return "" if x is None else f"{round(x * 100, 1)}"


def _f(x: float | None, nd: int = 1) -> str:
    return "" if x is None else f"{round(x, nd)}"


def _rows(section: str, r: InsightsReport) -> tuple[list[str], list[list[Any]]]:
    if section == "summary":
        total = sum(sum(row) for row in r.demand.heatmap)
        return ["measure", "value"], [
            ["period_start", r.start.date().isoformat()],
            ["period_end", r.end.date().isoformat()],
            ["days", r.days],
            ["calls", total],
            ["after_hours_calls", r.demand.after_hours_calls],
            ["would_have_missed", r.demand.would_have_missed_total],
            ["peak_concurrency", r.demand.peak_concurrency],
            ["forecast_next_week", round(r.demand.forecast_total)],
            ["forecast_vs_last_week_pct", _f(r.demand.forecast_vs_last_week_pct)],
            ["resolved_by_ai", r.resolution.resolved_by_ai],
            ["transferred", r.resolution.transferred],
            ["ticketed", r.resolution.ticketed],
            ["first_contact_resolution_pct", _pct(r.resolution.first_contact_resolution_rate)],
            ["leakage_pct", _pct(r.resolution.leakage_rate)],
            ["tickets", r.sla.tickets],
            ["sla_breach_pct", _pct(r.sla.breach_rate)],
            ["median_time_to_claim_h", _f(r.sla.median_time_to_claim_h)],
            ["transfer_answer_pct", _pct(r.transfers.answer_rate)],
            ["human_minutes", _f(r.transfers.human_minutes)],
            ["unanswered_questions", r.intents.unanswered_questions],
            ["leads", r.revenue.leads],
            ["bookings", r.revenue.bookings],
            ["attributed_pence", r.revenue.attributed_pence],
            ["missed_pence", r.revenue.missed_pence],
            ["unresolved_pence", r.revenue.unresolved_pence],
            ["avg_qa", _f(r.cx.avg_qa)],
            ["frustration_pct", _pct(r.cx.frustration_rate)],
            ["repeat_within_7d_pct", _pct(r.cx.repeat_rate)],
            ["ai_minutes", _f(r.cost.ai_minutes)],
            ["hours_saved", _f(r.cost.hours_saved)],
            ["cost_per_resolved_pence", r.cost.cost_per_resolved_pence or ""],
        ]
    if section == "demand":
        rows = [
            [_WEEKDAYS[d], _HOURS[h], r.demand.heatmap[d][h], r.demand.would_have_missed_by_hour[h]]
            for d in range(7)
            for h in range(24)
        ]
        return ["weekday", "hour", "calls", "would_have_missed"], rows
    if section == "forecast":
        return ["day", "weekday", "expected_calls", "low", "high", "busiest_hours"], [
            [
                d.day,
                d.weekday,
                _f(d.expected_calls),
                _f(d.low),
                _f(d.high),
                " ".join(map(str, d.busiest_hours)),
            ]
            for d in r.demand.forecast
        ]
    if section == "resolution":
        return ["step", "count", "share_pct"], [
            [s.label, s.count, _pct(s.share)] for s in r.resolution.funnel
        ] + [["by_intent:" + s.label, s.count, _pct(s.share)] for s in r.resolution.by_intent]
    if section == "sla":
        return ["department", "open", "oldest_h", "avg_age_h"], [
            [b.department, b.open, _f(b.oldest_h), _f(b.avg_age_h)] for b in r.sla.backlog
        ] + [["breach_trend:" + s.label, s.count, _f(s.share), ""] for s in r.sla.breach_trend]
    if section == "transfers":
        hdr = [
            "group",
            "label",
            "attempts",
            "answered",
            "answer_pct",
            "abandoned",
            "avg_human_s",
            "human_minutes",
        ]
        rows = [
            [
                g,
                t.label,
                t.attempts,
                t.answered,
                _pct(t.answer_rate),
                t.abandoned,
                _f(t.avg_human_s),
                _f(t.human_minutes),
            ]
            for g, rows_ in (
                ("department", r.transfers.by_department),
                ("destination", r.transfers.by_destination),
            )
            for t in rows_
        ]
        return hdr, rows
    if section == "intents":
        return ["group", "intent", "count", "value"], (
            [["top", s.label, s.count, _f(s.value)] for s in r.intents.top]
            + [["rising", s.label, s.count, _f(s.value)] for s in r.intents.rising]
            + [["falling", s.label, s.count, _f(s.value)] for s in r.intents.falling]
        )
    if section == "gaps":
        return ["question", "count", "status", "est_value_pence"], [
            [g.question, g.count, g.status, g.est_value_pence] for g in r.intents.gaps
        ]
    if section == "revenue":
        return ["group", "label", "count", "value_pence"], (
            [["intent", s.label, s.count, _f(s.value, 0)] for s in r.revenue.by_intent]
            + [["source", s.label, s.count, _f(s.value, 0)] for s in r.revenue.by_source]
            + [["customer", s.label, s.count, _f(s.value, 0)] for s in r.revenue.top_customers]
        )
    if section == "cx":
        return ["group", "label", "count", "avg_qa"], (
            [["week", s.label, s.count, _f(s.value)] for s in r.cx.qa_trend]
            + [["intent", s.label, s.count, _f(s.value)] for s in r.cx.qa_by_intent]
            + [["version", s.label, s.count, _f(s.value)] for s in r.cx.qa_by_version]
        )
    if section == "workforce":
        return [
            "member",
            "claimed",
            "resolved",
            "callbacks",
            "notes",
            "transfers_answered",
            "transfers_missed",
            "avg_resolution_h",
            "active_days",
            "vs_team_pct",
        ], [
            [
                m.label,
                m.claimed,
                m.resolved,
                m.callbacks,
                m.notes,
                m.transfers_answered,
                m.transfers_missed,
                _f(m.avg_resolution_h),
                m.active_days,
                _f(m.vs_team_pct),
            ]
            for m in r.workforce.members
        ]
    if section == "attribution":
        return ["group", "channel", "count", "value"], (
            [["calls", s.label, s.count, _f(s.value, 0)] for s in r.attribution.channels]
            + [["leads", s.label, s.count, ""] for s in r.attribution.leads_by_channel]
            + [["bookings", s.label, s.count, ""] for s in r.attribution.bookings_by_channel]
            + [["untracked", "untracked", r.attribution.untracked_calls, ""]]
        )
    if section == "cost":
        c = r.cost
        return ["measure", "value"], [
            ["ai_minutes", _f(c.ai_minutes)],
            ["human_minutes", _f(c.human_minutes)],
            ["ai_share_pct", _pct(c.ai_share)],
            ["ai_handled_calls", c.ai_handled_calls],
            ["hours_saved", _f(c.hours_saved)],
            ["est_human_cost_pence", c.est_human_cost_pence],
            ["est_ai_cost_pence", c.est_ai_cost_pence],
            ["cost_per_resolved_pence", c.cost_per_resolved_pence or ""],
        ]
    if section == "trends":
        hdr = [
            "granularity",
            "period",
            "calls",
            "answered",
            "missed",
            "transfers",
            "tickets",
            "bookings",
            "leads",
            "answer_pct",
            "est_value_pence",
        ]
        rows = [
            [
                g,
                p.label,
                p.calls,
                p.answered,
                p.missed,
                p.transfers,
                p.tickets,
                p.bookings,
                p.leads,
                _pct(p.answer_rate),
                p.est_value_pence,
            ]
            for g, pts in (
                ("month", r.trends.monthly),
                ("quarter", r.trends.quarterly),
                ("year", r.trends.yearly),
            )
            for p in pts
        ]
        return hdr, rows
    raise ValueError(f"unknown section '{section}'")


def insights_csv(report: InsightsReport, section: str = "summary") -> str:
    header, rows = _rows(section, report)
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


# -- scheduled report ----------------------------------------------------------------------------


class ReportSchedule(BaseModel):
    tenant_id: str
    enabled: bool = False
    cadence: str = Field("weekly", pattern=r"^(weekly|monthly)$")
    weekday: int = Field(0, ge=0, le=6)  # weekly: Monday
    day_of_month: int = Field(1, ge=1, le=28)  # monthly
    hour: int = Field(8, ge=0, le=23)
    sections: list[str] = Field(
        default_factory=lambda: ["demand", "resolution", "sla", "revenue", "cx"]
    )
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReportSent(BaseModel):
    id: str
    tenant_id: str
    title: str
    body: str
    days: int
    sent_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _gbp(pence: int) -> str:
    return f"£{pence / 100:,.0f}"


def render_report(r: InsightsReport, business: str, sections: list[str]) -> tuple[str, str]:
    total = sum(sum(row) for row in r.demand.heatmap)
    title = f"{business or 'Your business'}: analytics for the last {r.days} days"
    out = [f"{r.start:%d %b} - {r.end:%d %b %Y}", ""]
    if "demand" in sections:
        busiest = r.demand.busiest_hours[0].label if r.demand.busiest_hours else "n/a"
        out += [
            "Demand",
            f"- {total} calls, {r.demand.after_hours_calls} outside opening hours;"
            f" busiest slot {busiest}",
            f"- Next week: about {round(r.demand.forecast_total)} calls expected"
            + (
                f" ({'+' if (r.demand.forecast_vs_last_week_pct or 0) >= 0 else ''}"
                f"{round(r.demand.forecast_vs_last_week_pct or 0)}% vs last week)"
                if r.demand.forecast_vs_last_week_pct is not None
                else ""
            ),
            "",
        ]
    if "resolution" in sections:
        fcr = r.resolution.first_contact_resolution_rate
        out += [
            "Resolution",
            f"- Resolved by the assistant: {r.resolution.resolved_by_ai}; transferred:"
            f" {r.resolution.transferred}; ticketed: {r.resolution.ticketed}",
            f"- First-contact resolution: {'' if fcr is None else f'{round(fcr * 100)}%'}",
            "",
        ]
    if "sla" in sections:
        br = r.sla.breach_rate
        out += [
            "Callbacks",
            f"- {r.sla.tickets} tickets, {r.sla.breached} breached SLA"
            + (f" ({round(br * 100)}%)" if br is not None else ""),
            "- Median time to claim: "
            + (
                ""
                if r.sla.median_time_to_claim_h is None
                else f"{r.sla.median_time_to_claim_h:.1f} h"
            ),
            "",
        ]
    if "transfers" in sections:
        ar = r.transfers.answer_rate
        out += [
            "Transfers",
            f"- {r.transfers.attempts} attempted, {r.transfers.answered} answered"
            + (f" ({round(ar * 100)}%)" if ar is not None else ""),
            f"- Team talk time: {r.transfers.human_minutes:.0f} min",
            "",
        ]
    if "revenue" in sections:
        out += [
            "Revenue",
            f"- {r.revenue.leads} leads, {r.revenue.bookings} bookings; attributed"
            f" {_gbp(r.revenue.attributed_pence)}, missed {_gbp(r.revenue.missed_pence)},"
            f" unresolved {_gbp(r.revenue.unresolved_pence)}",
            "",
        ]
    if "cx" in sections:
        out += [
            "Customer experience",
            f"- Average QA {'' if r.cx.avg_qa is None else f'{r.cx.avg_qa:.1f}'} across"
            f" {r.cx.qa_scored} scored calls; frustration"
            f" {'' if r.cx.frustration_rate is None else f'{round(r.cx.frustration_rate * 100)}%'}",
            "",
        ]
    if "workforce" in sections and r.workforce.members:
        top = max(r.workforce.members, key=lambda m: m.resolved)
        out += [
            "Team",
            f"- {len(r.workforce.members)} active; most resolved: {top.label} ({top.resolved});"
            f" unassigned open tickets: {r.workforce.unassigned_open}",
            "",
        ]
    if "cost" in sections:
        out += [
            "Efficiency",
            f"- Assistant handled {r.cost.ai_minutes:.0f} min, saving about"
            f" {r.cost.hours_saved:.1f} hours of staff time",
            "",
        ]
    out.append("Full charts and CSV exports: Analytics > Insights in your dashboard.")
    return title, "\n".join(out)


class ReportService:
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

    async def schedule(self, tenant_id: str) -> ReportSchedule:
        doc = await self.store.get_doc(REPORT_SETTINGS_KIND, tenant_id)
        return (
            ReportSchedule.model_validate(doc.data) if doc else ReportSchedule(tenant_id=tenant_id)
        )

    async def save_schedule(self, tenant_id: str, s: ReportSchedule) -> ReportSchedule:
        bad = [x for x in s.sections if x not in SECTIONS]
        if bad:
            raise ValueError(f"unknown section(s): {', '.join(bad)}")
        s = s.model_copy(update={"tenant_id": tenant_id, "updated_at": datetime.now(UTC)})
        await self.store.put_doc(
            TenantDoc(
                kind=REPORT_SETTINGS_KIND,
                id=tenant_id,
                tenant_id=tenant_id,
                data=s.model_dump(mode="json"),
            )
        )
        return s

    async def report(self, tenant_id: str, days: int) -> InsightsReport:
        inp = await load_inputs(self.store, self.value, tenant_id)
        return build_insights(inp, days=days, timezone=str(REPORT_TZ), now=datetime.now(UTC))

    async def send(self, tenant_id: str, *, days: int | None = None) -> ReportSent:
        s = await self.schedule(tenant_id)
        days = days or (7 if s.cadence == "weekly" else 30)
        rep = await self.report(tenant_id, days)
        cfgs = await self.store.list_assistants(tenant_id)
        title, body = render_report(rep, cfgs[0].business_name if cfgs else "", s.sections)
        await self.notifications.dispatch(
            NotificationEvent(
                tenant_id=tenant_id,
                company_id=cfgs[0].company_id if cfgs else None,
                event=NotifyEvent.ANALYTICS_REPORT,
                title=title,
                body=body,
                context={"period_days": days},
            )
        )
        rec = ReportSent(
            id=f"rep-{uuid4().hex[:10]}",
            tenant_id=tenant_id,
            title=title,
            body=body,
            days=days,
        )
        await self.store.put_doc(
            TenantDoc(
                kind=REPORT_LOG_KIND,
                id=rec.id,
                tenant_id=tenant_id,
                data=rec.model_dump(mode="json"),
                created_at=rec.sent_at,
            )
        )
        return rec

    async def history(self, tenant_id: str, limit: int = 12) -> list[ReportSent]:
        docs = await self.store.list_docs(REPORT_LOG_KIND, tenant_id, limit)
        return sorted(
            (ReportSent.model_validate(d.data) for d in docs), key=lambda r: r.sent_at, reverse=True
        )

    async def due(self, tenant_id: str, now: datetime | None = None) -> bool:
        s = await self.schedule(tenant_id)
        if not s.enabled:
            return False
        now = now or datetime.now(UTC)
        local = now.astimezone(REPORT_TZ)
        if local.hour != s.hour:
            return False
        if s.cadence == "weekly" and local.weekday() != s.weekday:
            return False
        if s.cadence == "monthly" and local.day != s.day_of_month:
            return False
        last = await self.history(tenant_id, 1)
        gap = timedelta(days=6) if s.cadence == "weekly" else timedelta(days=27)
        return not last or (now - last[0].sent_at) > gap

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
                log.warning("scheduled report failed for %s", cfg.tenant_id, exc_info=True)
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
