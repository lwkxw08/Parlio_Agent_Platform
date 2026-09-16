"""AI business advisor (Phase 21c).

Weekly and on-demand recommendations for the business owner, built on the Phase 21a
``InsightsReport``. Deterministic pattern detectors are the source of truth: each one turns a
measurable signal (transfer answer rate, SLA breaches, FAQ gaps, forecast spike…) into a
``Recommendation`` with evidence, expected impact, confidence and concrete actions. An LLM may
optionally *reword* the summary from the aggregate evidence only - never raw calls, names or
numbers - and its output is discarded unless every number it states appears in the evidence and
the tone check passes. Spend on that LLM is capped per tenant per month.

Lifecycle: new -> applied | dismissed | snoozed. Applied recommendations remember the metric
they targeted so the next run can report whether it moved.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field

from parlio_api.billing import BillingService
from parlio_api.insights import InsightsReport, build_insights, load_inputs
from parlio_api.notifications import NotificationEvent, NotificationService, NotifyEvent
from parlio_api.store import CallStore, TenantDoc
from parlio_api.value import ValueService
from parlio_voice.models import BusinessRule, Faq

log = logging.getLogger("parlio.advisor")

ADVICE_KIND = "advice"
ADVISOR_SETTINGS_KIND = "advisor_settings"
ADVISOR_RUN_KIND = "advisor_run"
ENTITLEMENT = "advisor"
ADVISOR_TZ = ZoneInfo("Europe/London")

# gpt-4o-mini list price, pence per 1k tokens (in / out).
LLM_IN_PENCE_PER_1K = 0.012
LLM_OUT_PENCE_PER_1K = 0.048

# Words that over-promise or blame; a reworded summary containing any of these is rejected.
BANNED_PHRASES = (
    "guarantee",
    "guaranteed",
    "will definitely",
    "certainly will",
    "always",
    "never",
    "you must",
    "you failed",
    "lazy",
    "incompetent",
    "useless",
    "obviously",
    "!",
)
_NUMBER_RE = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)")
_PHONE_RE = re.compile(r"\+?\d[\d\s]{8,}\d")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


class AdviceStatus(StrEnum):
    NEW = "new"
    APPLIED = "applied"
    DISMISSED = "dismissed"
    SNOOZED = "snoozed"


class Evidence(BaseModel):
    label: str
    value: str  # human formatted, e.g. "38%" or "26 h"
    metric: str | None = None  # dotted path into InsightsReport, for outcome tracking


class Action(BaseModel):
    kind: str  # faq | rule | manual
    label: str
    question: str | None = None  # faq
    text: str | None = None  # faq answer / rule instruction (may be blank -> owner fills in)


class Outcome(BaseModel):
    metric: str
    before: float
    after: float
    delta_pct: float | None = None
    improved: bool | None = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Recommendation(BaseModel):
    id: str = Field(default_factory=lambda: f"adv-{uuid4().hex[:8]}")
    tenant_id: str
    run_id: str
    rule: str  # detector key, e.g. transfer_hotspot:accounts
    area: str  # demand | resolution | sla | transfers | faq | revenue | cx | workforce | marketing
    title: str
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    expected_impact: str
    confidence: float = Field(ge=0.0, le=1.0)
    priority: int = Field(1, ge=1, le=3)  # 1 = act this week
    actions: list[Action] = Field(default_factory=list)
    wording_source: str = "rules"  # rules | llm
    metric: str | None = None
    metric_before: float | None = None
    lower_is_better: bool = True
    status: AdviceStatus = AdviceStatus.NEW
    snoozed_until: datetime | None = None
    applied_at: datetime | None = None
    applied_by: str | None = None
    outcome: Outcome | None = None
    period_days: int = 28
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=ADVICE_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )

    def open(self, now: datetime) -> bool:
        if self.status == AdviceStatus.NEW:
            return True
        return (
            self.status == AdviceStatus.SNOOZED
            and self.snoozed_until is not None
            and self.snoozed_until > now
        )


class AdvisorSettings(BaseModel):
    tenant_id: str
    enabled: bool = True
    weekly_digest: bool = True
    digest_weekday: int = Field(0, ge=0, le=6)  # Monday
    digest_hour: int = Field(8, ge=0, le=23)
    use_llm_wording: bool = True
    llm_monthly_cap_pence: int = Field(50, ge=0, le=5000)
    lookback_days: int = Field(28, ge=7, le=365)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AdvisorRun(BaseModel):
    id: str = Field(default_factory=lambda: f"run-{uuid4().hex[:8]}")
    tenant_id: str
    trigger: str  # weekly | manual
    generated: int = 0
    refreshed: int = 0
    llm_pence: float = 0.0
    llm_calls: int = 0
    llm_rejected: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AdvisorOverview(BaseModel):
    settings: AdvisorSettings
    recommendations: list[Recommendation]
    last_run: AdvisorRun | None = None
    llm_spent_this_month_pence: float = 0.0


# -- formatting helpers --------------------------------------------------------------------------


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{round(x * 100)}%"


def hours(x: float | None) -> str:
    return "n/a" if x is None else (f"{round(x)} h" if x >= 10 else f"{x:.1f} h")


def pounds(pence: int) -> str:
    return f"£{pence / 100:,.0f}"


def numbers_in(text: str) -> set[str]:
    """Every numeric token in ``text`` normalised (thousands separators removed)."""
    return {m.group(1).replace(",", "").rstrip(".") for m in _NUMBER_RE.finditer(text)}


def validate_claims(text: str, evidence: list[Evidence]) -> list[str]:
    """Return the numbers in ``text`` that are not backed by any evidence value."""
    allowed: set[str] = set()
    for e in evidence:
        allowed |= numbers_in(e.value) | numbers_in(e.label)
    # allow the integer form of a decimal ("3.5" -> "3") and vice versa is not allowed
    allowed |= {a.split(".")[0] for a in allowed}
    return sorted(n for n in numbers_in(text) if n not in allowed)


def validate_tone(text: str) -> list[str]:
    low = text.lower()
    problems = [p for p in BANNED_PHRASES if p in low]
    if _PHONE_RE.search(text):
        problems.append("phone number")
    if _EMAIL_RE.search(text):
        problems.append("email address")
    return problems


def metric_value(report: InsightsReport, path: str) -> float | None:
    """Resolve ``sla.breach_rate`` or ``transfers.by_department[Accounts].answer_rate``."""
    cur: Any = report
    for part in path.split("."):
        m = re.fullmatch(r"(\w+)\[(.+)\]", part)
        if m:
            seq = getattr_model(cur, m.group(1))
            if not isinstance(seq, list):
                return None
            cur = next((row for row in seq if row_label(row) == m.group(2)), None)
        else:
            cur = getattr_model(cur, part)
        if cur is None:
            return None
    return float(cur) if isinstance(cur, int | float) else None


def getattr_model(obj: Any, name: str) -> Any:
    if isinstance(obj, BaseModel) and name in type(obj).model_fields:
        return obj.__dict__.get(name)
    return None


def row_label(row: Any) -> str | None:
    if isinstance(row, BaseModel):
        for key in ("label", "department", "question"):
            if key in type(row).model_fields:
                return str(row.__dict__.get(key))
    return None


# -- deterministic detectors ---------------------------------------------------------------------


def _rec(
    tenant_id: str,
    run_id: str,
    *,
    rule: str,
    area: str,
    title: str,
    summary: str,
    evidence: list[Evidence],
    impact: str,
    confidence: float,
    priority: int,
    actions: list[Action],
    metric: str | None,
    before: float | None,
    lower_is_better: bool = True,
    days: int,
) -> Recommendation:
    return Recommendation(
        tenant_id=tenant_id,
        run_id=run_id,
        rule=rule,
        area=area,
        title=title,
        summary=summary,
        evidence=evidence,
        expected_impact=impact,
        confidence=confidence,
        priority=priority,
        actions=actions,
        metric=metric,
        metric_before=before,
        lower_is_better=lower_is_better,
        period_days=days,
    )


def detect(report: InsightsReport, *, run_id: str) -> list[Recommendation]:
    """Turn the aggregate report into evidence-backed recommendations (no LLM involved)."""
    tid = report.tenant_id
    days = report.days
    out: list[Recommendation] = []
    total_calls = sum(sum(row) for row in report.demand.heatmap)

    # 1. Demand spike ahead
    fc = report.demand.forecast_vs_last_week_pct
    if fc is not None and fc >= 25 and report.demand.forecast_total >= 20:
        peak = report.demand.busiest_hours[0] if report.demand.busiest_hours else None
        ev = [
            Evidence(
                label="Forecast vs last week",
                value=f"+{round(fc)}%",
                metric="demand.forecast_vs_last_week_pct",
            ),
            Evidence(
                label="Calls expected next week", value=f"{round(report.demand.forecast_total)}"
            ),
        ]
        if peak:
            ev.append(Evidence(label="Busiest hour", value=peak.label))
        out.append(
            _rec(
                tid,
                run_id,
                rule="demand_spike",
                area="demand",
                title="Busier week ahead - plan cover for the peak hours",
                summary=(
                    f"Next week's forecast is {round(fc)}% above last week"
                    f" (about {round(report.demand.forecast_total)} calls)."
                    + (f" The busiest slot is {peak.label}." if peak else "")
                    + " Make sure someone is free for transfers in that window, or let the"
                    " assistant take full details and promise a call back."
                ),
                evidence=ev,
                impact="Fewer unanswered transfers and shorter waits during the peak.",
                confidence=0.6,
                priority=2,
                actions=[Action(kind="manual", label="Check the rota for the busiest hours")],
                metric="demand.forecast_vs_last_week_pct",
                before=fc,
                days=days,
            )
        )

    # 2. After-hours demand
    if total_calls >= 20 and report.demand.after_hours_calls / total_calls >= 0.2:
        share = report.demand.after_hours_calls / total_calls
        out.append(
            _rec(
                tid,
                run_id,
                rule="after_hours_demand",
                area="demand",
                title="A large share of calls arrive outside business hours",
                summary=(
                    f"{pct(share)} of calls ({report.demand.after_hours_calls} of {total_calls})"
                    f" came in outside your opening hours in the last {days} days. The"
                    " assistant answers them, but check it can book, take payment details and"
                    " raise urgent tickets when nobody is in."
                ),
                evidence=[
                    Evidence(label="After-hours share", value=pct(share)),
                    Evidence(label="After-hours calls", value=str(report.demand.after_hours_calls)),
                    Evidence(label="All calls", value=str(total_calls)),
                    Evidence(label="Window", value=f"{days} days"),
                ],
                impact="Captures enquiries that would otherwise go to voicemail or a competitor.",
                confidence=0.7,
                priority=2,
                actions=[
                    Action(
                        kind="rule",
                        label="Add an after-hours instruction",
                        text=(
                            "Outside opening hours, offer to book an appointment or take a"
                            " detailed message with the best time to call back; for emergencies"
                            " follow the emergency department rules."
                        ),
                    )
                ],
                metric="demand.after_hours_calls",
                before=float(report.demand.after_hours_calls),
                lower_is_better=False,
                days=days,
            )
        )

    # 3. Transfer hotspots (department misses transfers)
    for row in report.transfers.by_department:
        if row.attempts >= 5 and row.answer_rate is not None and row.answer_rate < 0.6:
            missed = row.attempts - row.answered
            out.append(
                _rec(
                    tid,
                    run_id,
                    rule=f"transfer_hotspot:{row.label}",
                    area="transfers",
                    title=f"{row.label} misses most of its transfers",
                    summary=(
                        f"Only {pct(row.answer_rate)} of the {row.attempts} transfers to"
                        f" {row.label} were answered in the last {days} days ({missed} missed)."
                        " Either add cover for that team or let the assistant handle the"
                        " common questions and take a message for the rest."
                    ),
                    evidence=[
                        Evidence(
                            label=f"{row.label} answer rate",
                            value=pct(row.answer_rate),
                            metric=f"transfers.by_department[{row.label}].answer_rate",
                        ),
                        Evidence(label="Transfers attempted", value=str(row.attempts)),
                        Evidence(label="Missed", value=str(missed)),
                        Evidence(label="Window", value=f"{days} days"),
                    ],
                    impact="Callers get an answer first time instead of a ring-out and a ticket.",
                    confidence=0.8,
                    priority=1,
                    actions=[
                        Action(
                            kind="rule",
                            label=f"Let the assistant screen {row.label} calls",
                            text=(
                                f"Before transferring to {row.label}, find out exactly what the"
                                " caller needs; answer it from the FAQs where you can, and only"
                                " transfer if it needs a person."
                            ),
                        ),
                        Action(kind="manual", label=f"Add cover for {row.label}"),
                    ],
                    metric=f"transfers.by_department[{row.label}].answer_rate",
                    before=row.answer_rate,
                    lower_is_better=False,
                    days=days,
                )
            )

    # 4. SLA drift
    sla = report.sla
    if sla.tickets >= 5 and sla.breach_rate is not None and sla.breach_rate >= 0.2:
        worst = max(sla.backlog, key=lambda b: b.oldest_h or 0.0, default=None)
        ev = [
            Evidence(
                label="Tickets breaching SLA", value=pct(sla.breach_rate), metric="sla.breach_rate"
            ),
            Evidence(label="Breached", value=str(sla.breached)),
            Evidence(label="Tickets", value=str(sla.tickets)),
            Evidence(label="Median time to claim", value=hours(sla.median_time_to_claim_h)),
        ]
        if worst and worst.oldest_h:
            ev.append(
                Evidence(label=f"Oldest open in {worst.department}", value=hours(worst.oldest_h))
            )
        out.append(
            _rec(
                tid,
                run_id,
                rule="sla_drift",
                area="sla",
                title="Callbacks are breaching their SLA",
                summary=(
                    f"{pct(sla.breach_rate)} of tickets ({sla.breached} of {sla.tickets}) went"
                    f" past their SLA; the median time to claim one is"
                    f" {hours(sla.median_time_to_claim_h)}."
                    + (
                        f" The oldest open ticket in {worst.department} is"
                        f" {hours(worst.oldest_h)} old."
                        if worst and worst.oldest_h
                        else ""
                    )
                    + " Assign an owner for the queue each morning or shorten the claim window."
                ),
                evidence=ev,
                impact="Fewer repeat calls chasing a callback and fewer lost leads.",
                confidence=0.85,
                priority=1,
                actions=[Action(kind="manual", label="Assign a daily ticket-queue owner")],
                metric="sla.breach_rate",
                before=sla.breach_rate,
                days=days,
            )
        )

    # 5. Cold leads
    rev = report.revenue
    if rev.unresolved_pence >= 20_000 and (sla.median_time_to_claim_h or 0) > 24:
        out.append(
            _rec(
                tid,
                run_id,
                rule="cold_leads",
                area="revenue",
                title="Leads are going cold waiting for a call back",
                summary=(
                    f"About {pounds(rev.unresolved_pence)} of enquiries are still unresolved"
                    f" and tickets wait a median {hours(sla.median_time_to_claim_h)} to be"
                    " claimed. Speed-to-lead matters most in the first hour: send routine"
                    " quote requests to AI call back with a price range, and keep people for"
                    " the complex ones."
                ),
                evidence=[
                    Evidence(
                        label="Unresolved enquiry value",
                        value=pounds(rev.unresolved_pence),
                        metric="revenue.unresolved_pence",
                    ),
                    Evidence(label="Median time to claim", value=hours(sla.median_time_to_claim_h)),
                    Evidence(label="Window", value=f"{days} days"),
                ],
                impact="Recovers part of the unresolved value and shortens lead-to-booking time.",
                confidence=0.65,
                priority=1,
                actions=[Action(kind="manual", label="Use AI call back for routine quotes")],
                metric="revenue.unresolved_pence",
                before=float(rev.unresolved_pence),
                days=days,
            )
        )

    # 6. FAQ gaps
    gaps = [g for g in report.intents.gaps if g.count >= 3 and g.status == "open"][:3]
    for g in gaps:
        out.append(
            _rec(
                tid,
                run_id,
                rule=f"faq_gap:{g.question[:60].lower()}",
                area="faq",
                title="Callers keep asking something the assistant can't answer",
                summary=(
                    f'"{g.question}" came up {g.count} times without an answer'
                    + (f" (worth about {pounds(g.est_value_pence)})" if g.est_value_pence else "")
                    + ". Add it as an FAQ so the assistant resolves it on the call."
                ),
                evidence=[
                    Evidence(label="Times asked", value=str(g.count)),
                    Evidence(label="Question", value=g.question),
                ]
                + (
                    [Evidence(label="Estimated value", value=pounds(g.est_value_pence))]
                    if g.est_value_pence
                    else []
                ),
                impact="Moves these calls from ticket/transfer to resolved-by-AI.",
                confidence=0.9,
                priority=2,
                actions=[Action(kind="faq", label="Add this FAQ", question=g.question, text="")],
                metric="intents.unanswered_questions",
                before=float(report.intents.unanswered_questions),
                days=days,
            )
        )

    # 7. Low-QA segment
    cx = report.cx
    if cx.avg_qa is not None and cx.qa_scored >= 10:
        for seg in cx.qa_by_intent:
            if seg.count >= 5 and seg.value is not None and seg.value <= cx.avg_qa - 1.0:
                out.append(
                    _rec(
                        tid,
                        run_id,
                        rule=f"low_qa:{seg.label}",
                        area="cx",
                        title=f"{seg.label} calls score below your average",
                        summary=(
                            f"{seg.label} calls average a QA score of {seg.value:.1f} against"
                            f" {cx.avg_qa:.1f} overall, across {seg.count} scored calls."
                            " Review a few transcripts for that intent and tighten the FAQs or"
                            " rules the assistant uses there."
                        ),
                        evidence=[
                            Evidence(
                                label=f"{seg.label} QA",
                                value=f"{seg.value:.1f}",
                                metric=f"cx.qa_by_intent[{seg.label}].value",
                            ),
                            Evidence(label="Overall QA", value=f"{cx.avg_qa:.1f}"),
                            Evidence(label="Scored calls", value=str(seg.count)),
                        ],
                        impact="Lifts QA and resolution for that intent.",
                        confidence=0.7,
                        priority=2,
                        actions=[Action(kind="manual", label=f"Review {seg.label} transcripts")],
                        metric=f"cx.qa_by_intent[{seg.label}].value",
                        before=seg.value,
                        lower_is_better=False,
                        days=days,
                    )
                )

    # 8. Frustration / repeat calls
    if cx.qa_scored >= 10 and cx.frustration_rate is not None and cx.frustration_rate >= 0.15:
        out.append(
            _rec(
                tid,
                run_id,
                rule="frustration",
                area="cx",
                title="Callers are getting frustrated more often than they should",
                summary=(
                    f"{pct(cx.frustration_rate)} of scored calls ({cx.frustrated} of"
                    f" {cx.qa_scored}) showed frustration. Common causes are being asked for"
                    " details twice, long confirmations and transfers that ring out."
                ),
                evidence=[
                    Evidence(
                        label="Frustration rate",
                        value=pct(cx.frustration_rate),
                        metric="cx.frustration_rate",
                    ),
                    Evidence(label="Frustrated calls", value=str(cx.frustrated)),
                    Evidence(label="Scored calls", value=str(cx.qa_scored)),
                ],
                impact="Better tone scores and fewer repeat calls.",
                confidence=0.7,
                priority=2,
                actions=[
                    Action(
                        kind="rule",
                        label="Confirm one detail at a time",
                        text=(
                            "Confirm one detail at a time and never ask for something the"
                            " caller has already given you."
                        ),
                    )
                ],
                metric="cx.frustration_rate",
                before=cx.frustration_rate,
                days=days,
            )
        )
    if total_calls >= 30 and cx.repeat_rate is not None and cx.repeat_rate >= 0.15:
        out.append(
            _rec(
                tid,
                run_id,
                rule="repeat_calls",
                area="cx",
                title="Many callers ring back within a week",
                summary=(
                    f"{pct(cx.repeat_rate)} of callers ({cx.repeat_within_7d}) rang again within"
                    " 7 days - usually chasing a callback or an unanswered question. Faster"
                    " ticket claims and a post-call SMS confirmation cut this sharply."
                ),
                evidence=[
                    Evidence(
                        label="Repeat within 7 days",
                        value=pct(cx.repeat_rate),
                        metric="cx.repeat_rate",
                    ),
                    Evidence(label="Repeat callers", value=str(cx.repeat_within_7d)),
                    Evidence(label="Days", value="7"),
                ],
                impact="Fewer repeat calls, less time on the phone for the team.",
                confidence=0.65,
                priority=2,
                actions=[Action(kind="manual", label="Switch on ticket confirmation texts")],
                metric="cx.repeat_rate",
                before=cx.repeat_rate,
                days=days,
            )
        )

    # 9. Workforce imbalance
    wf = report.workforce
    if len(wf.members) >= 3 and wf.team_avg_resolved and wf.team_avg_resolved >= 5:
        for m in wf.members:
            if m.vs_team_pct is not None and m.vs_team_pct <= -50 and m.active_days >= 5:
                out.append(
                    _rec(
                        tid,
                        run_id,
                        rule=f"workforce:{m.label}",
                        area="workforce",
                        title="Ticket work is unevenly spread across the team",
                        summary=(
                            f"One team member resolved {m.resolved} tickets over {m.active_days}"
                            f" active days, {abs(round(m.vs_team_pct))}% below the team average"
                            f" of {wf.team_avg_resolved:.0f}. Check whether the queue is being"
                            " routed fairly or whether they need support on the tooling."
                        ),
                        evidence=[
                            Evidence(label="Resolved", value=str(m.resolved)),
                            Evidence(label="Active days", value=str(m.active_days)),
                            Evidence(label="Vs team", value=f"{round(m.vs_team_pct)}%"),
                            Evidence(label="Team average", value=f"{wf.team_avg_resolved:.0f}"),
                        ],
                        impact="Evens out resolution time across the queue.",
                        confidence=0.5,
                        priority=3,
                        actions=[Action(kind="manual", label="Review queue routing")],
                        metric=None,
                        before=None,
                        days=days,
                    )
                )
                break

    # 10. Untracked marketing
    att = report.attribution
    tracked = sum(c.count for c in att.channels)
    if (
        tracked + att.untracked_calls >= 30
        and att.untracked_calls / (tracked + att.untracked_calls) >= 0.5
    ):
        share = att.untracked_calls / (tracked + att.untracked_calls)
        out.append(
            _rec(
                tid,
                run_id,
                rule="untracked_marketing",
                area="marketing",
                title="Most calls can't be tied to a marketing channel",
                summary=(
                    f"{pct(share)} of calls ({att.untracked_calls}) arrived on numbers with no"
                    " channel tag, so you can't see which advertising pays for itself. Add"
                    " tracking numbers for your website, Google Business Profile and any print"
                    " ads."
                ),
                evidence=[
                    Evidence(label="Untracked share", value=pct(share)),
                    Evidence(
                        label="Untracked calls",
                        value=str(att.untracked_calls),
                        metric="attribution.untracked_calls",
                    ),
                ],
                impact="Lets you cut spend on channels that don't ring the phone.",
                confidence=0.75,
                priority=3,
                actions=[Action(kind="manual", label="Add tracking numbers per channel")],
                metric="attribution.untracked_calls",
                before=float(att.untracked_calls),
                days=days,
            )
        )

    out.sort(key=lambda r: (r.priority, -r.confidence))
    return out


# -- LLM rewording (aggregate evidence only) ----------------------------------------------------


class Reworder:
    """Optional OpenAI-backed rewording of a recommendation summary from its evidence."""

    def __init__(
        self,
        api_key: str | None,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client: httpx.AsyncClient | None = None
        if api_key:
            self._client = client or httpx.AsyncClient(
                base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=20
            )
        self._model = model

    @property
    def available(self) -> bool:
        return self._client is not None

    @staticmethod
    def prompt(rec: Recommendation) -> tuple[str, str]:
        system = (
            "You are a calm, plain-English business adviser writing one short paragraph"
            " (max 60 words) for a small-business owner. Use only the numbers given in the"
            " evidence, exactly as written; do not invent figures, names or phone numbers;"
            " no exclamation marks, no promises or guarantees, no blame. Return the paragraph"
            " only."
        )
        lines = [f"Topic: {rec.title}", "Evidence:"]
        lines += [f"- {e.label}: {e.value}" for e in rec.evidence]
        lines.append(f"Suggested action: {rec.actions[0].label if rec.actions else 'review'}")
        return system, "\n".join(lines)

    async def reword(self, rec: Recommendation) -> tuple[str | None, float]:
        """Return (text or None if rejected/unavailable, cost in pence)."""
        if self._client is None:
            return None, 0.0
        system, user = self.prompt(rec)
        try:
            r = await self._client.post(
                "/chat/completions",
                json={
                    "model": self._model,
                    "temperature": 0.3,
                    "max_tokens": 160,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            r.raise_for_status()
            body = r.json()
            text = str(body["choices"][0]["message"]["content"]).strip().strip('"')
            usage = body.get("usage") or {}
            cost = (
                float(usage.get("prompt_tokens", 400)) / 1000 * LLM_IN_PENCE_PER_1K
                + float(usage.get("completion_tokens", 120)) / 1000 * LLM_OUT_PENCE_PER_1K
            )
        except Exception:
            log.warning("advisor rewording failed", exc_info=True)
            return None, 0.0
        if validate_claims(text, rec.evidence) or validate_tone(text):
            return None, cost
        return text, cost


# -- service --------------------------------------------------------------------------------------


class AdvisorService:
    def __init__(
        self,
        store: CallStore,
        value: ValueService,
        billing: BillingService,
        notifications: NotificationService,
        reworder: Reworder | None = None,
        interval_s: float = 900.0,
    ) -> None:
        self.store = store
        self.value = value
        self.billing = billing
        self.notifications = notifications
        self.reworder = reworder or Reworder(None)
        self.interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    # -- settings ----------------------------------------------------------------------------
    async def settings(self, tenant_id: str) -> AdvisorSettings:
        doc = await self.store.get_doc(ADVISOR_SETTINGS_KIND, tenant_id)
        return (
            AdvisorSettings.model_validate(doc.data)
            if doc
            else AdvisorSettings(tenant_id=tenant_id)
        )

    async def save_settings(self, tenant_id: str, s: AdvisorSettings) -> AdvisorSettings:
        s = s.model_copy(update={"tenant_id": tenant_id, "updated_at": datetime.now(UTC)})
        await self.store.put_doc(
            TenantDoc(
                kind=ADVISOR_SETTINGS_KIND,
                id=tenant_id,
                tenant_id=tenant_id,
                data=s.model_dump(mode="json"),
            )
        )
        return s

    async def entitled(self, tenant_id: str) -> bool:
        return await self.billing.entitled(tenant_id, ENTITLEMENT)

    # -- persistence ---------------------------------------------------------------------------
    async def recommendations(
        self, tenant_id: str, *, include_closed: bool = True
    ) -> list[Recommendation]:
        docs = await self.store.list_docs(ADVICE_KIND, tenant_id, limit=500)
        recs = [Recommendation.model_validate(d.data) for d in docs]
        now = datetime.now(UTC)
        for r in recs:
            if r.status == AdviceStatus.SNOOZED and r.snoozed_until and r.snoozed_until <= now:
                r.status = AdviceStatus.NEW
        if not include_closed:
            recs = [r for r in recs if r.open(now)]
        recs.sort(
            key=lambda r: (r.status != AdviceStatus.NEW, r.priority, -r.created_at.timestamp())
        )
        return recs

    async def get(self, tenant_id: str, rec_id: str) -> Recommendation | None:
        doc = await self.store.get_doc(ADVICE_KIND, rec_id)
        if doc is None or doc.tenant_id != tenant_id:
            return None
        return Recommendation.model_validate(doc.data)

    async def _save(self, rec: Recommendation) -> Recommendation:
        rec.updated_at = datetime.now(UTC)
        await self.store.put_doc(rec.to_doc())
        return rec

    async def runs(self, tenant_id: str, limit: int = 12) -> list[AdvisorRun]:
        docs = await self.store.list_docs(ADVISOR_RUN_KIND, tenant_id, limit=limit)
        return sorted(
            (AdvisorRun.model_validate(d.data) for d in docs),
            key=lambda r: r.created_at,
            reverse=True,
        )

    async def llm_spent_this_month(self, tenant_id: str, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return round(
            sum(r.llm_pence for r in await self.runs(tenant_id, 200) if r.created_at >= start), 3
        )

    async def overview(self, tenant_id: str) -> AdvisorOverview:
        runs = await self.runs(tenant_id, 1)
        return AdvisorOverview(
            settings=await self.settings(tenant_id),
            recommendations=await self.recommendations(tenant_id),
            last_run=runs[0] if runs else None,
            llm_spent_this_month_pence=await self.llm_spent_this_month(tenant_id),
        )

    # -- generation ------------------------------------------------------------------------------
    async def report(self, tenant_id: str, days: int) -> InsightsReport:
        inp = await load_inputs(self.store, self.value, tenant_id)
        return build_insights(inp, days=days, timezone=str(ADVISOR_TZ), now=datetime.now(UTC))

    async def run(
        self, tenant_id: str, *, trigger: str = "manual", report: InsightsReport | None = None
    ) -> AdvisorRun:
        s = await self.settings(tenant_id)
        rep = report or await self.report(tenant_id, s.lookback_days)
        run = AdvisorRun(tenant_id=tenant_id, trigger=trigger)
        now = datetime.now(UTC)
        existing = await self.recommendations(tenant_id)
        by_rule: dict[str, list[Recommendation]] = {}
        for r in existing:
            by_rule.setdefault(r.rule, []).append(r)

        # outcomes for applied recommendations (>= 7 days after applying)
        for r in existing:
            if (
                r.status == AdviceStatus.APPLIED
                and r.metric
                and r.metric_before is not None
                and r.applied_at
                and now - r.applied_at >= timedelta(days=7)
            ):
                after = metric_value(rep, r.metric)
                if after is not None:
                    delta = (
                        None
                        if r.metric_before == 0
                        else (after - r.metric_before) / abs(r.metric_before) * 100
                    )
                    improved = (
                        after < r.metric_before if r.lower_is_better else after > r.metric_before
                    )
                    r.outcome = Outcome(
                        metric=r.metric,
                        before=r.metric_before,
                        after=after,
                        delta_pct=delta,
                        improved=improved,
                    )
                    await self._save(r)
                    run.refreshed += 1

        spent = await self.llm_spent_this_month(tenant_id, now)
        budget = max(0.0, s.llm_monthly_cap_pence - spent) if s.use_llm_wording else 0.0
        for cand in detect(rep, run_id=run.id):
            prior = by_rule.get(cand.rule, [])
            if any(p.open(now) for p in prior):
                continue  # already on the list
            recent = [p for p in prior if now - p.updated_at < timedelta(days=30)]
            if any(p.status in (AdviceStatus.DISMISSED, AdviceStatus.APPLIED) for p in recent):
                continue  # respect the owner's decision for a month
            if budget > 0 and self.reworder.available:
                text, cost = await self.reworder.reword(cand)
                run.llm_calls += 1
                run.llm_pence += cost
                budget -= cost
                if text:
                    cand.summary = text
                    cand.wording_source = "llm"
                else:
                    run.llm_rejected += 1
            await self._save(cand)
            run.generated += 1
        await self.store.put_doc(
            TenantDoc(
                kind=ADVISOR_RUN_KIND,
                id=run.id,
                tenant_id=tenant_id,
                data=run.model_dump(mode="json"),
                created_at=run.created_at,
            )
        )
        return run

    # -- lifecycle -----------------------------------------------------------------------------
    async def apply(
        self,
        tenant_id: str,
        rec_id: str,
        *,
        actor: str,
        action_index: int = 0,
        text: str | None = None,
    ) -> Recommendation | None:
        rec = await self.get(tenant_id, rec_id)
        if rec is None:
            return None
        if not rec.actions:
            raise ValueError("this recommendation has no action to apply")
        if action_index < 0 or action_index >= len(rec.actions):
            raise ValueError("unknown action")
        act = rec.actions[action_index]
        if act.kind in ("faq", "rule"):
            body = (text or act.text or "").strip()
            if not body:
                raise ValueError("an answer or instruction is required")
            cfgs = await self.store.list_assistants(tenant_id)
            if not cfgs:
                raise ValueError("no assistant to apply the change to")
            cfg = cfgs[0].model_copy(deep=True)
            if act.kind == "faq":
                cfg.faqs.append(
                    Faq(question=(act.question or rec.title).strip(), answer=body, source="advisor")
                )
            else:
                cfg.rules.append(BusinessRule(name=act.label[:60], instruction=body))
            await self.store.upsert_assistant(cfg, [])
            act.text = body
        rec.status = AdviceStatus.APPLIED
        rec.applied_at = datetime.now(UTC)
        rec.applied_by = actor
        rec.snoozed_until = None
        return await self._save(rec)

    async def dismiss(self, tenant_id: str, rec_id: str) -> Recommendation | None:
        rec = await self.get(tenant_id, rec_id)
        if rec is None:
            return None
        rec.status = AdviceStatus.DISMISSED
        rec.snoozed_until = None
        return await self._save(rec)

    async def snooze(self, tenant_id: str, rec_id: str, *, days: int = 14) -> Recommendation | None:
        rec = await self.get(tenant_id, rec_id)
        if rec is None:
            return None
        rec.status = AdviceStatus.SNOOZED
        rec.snoozed_until = datetime.now(UTC) + timedelta(days=max(1, min(days, 90)))
        return await self._save(rec)

    # -- weekly digest ---------------------------------------------------------------------------
    def render_digest(self, recs: list[Recommendation], business: str) -> tuple[str, str]:
        plural = "s" if len(recs) != 1 else ""
        title = (
            f"Parlio advisor: {len(recs)} recommendation{plural} for {business or 'your business'}"
        )
        if not recs:
            return (
                title,
                "Nothing needs your attention this week - the assistant is handling calls well.",
            )
        lines = ["This week's recommendations, most important first:", ""]
        for i, r in enumerate(recs[:5], 1):
            lines.append(f"{i}. {r.title}")
            lines.append(f"   {r.summary}")
            lines.append(
                "   Evidence: " + "; ".join(f"{e.label} {e.value}" for e in r.evidence[:3])
            )
            lines.append(f"   Expected impact: {r.expected_impact}")
            lines.append("")
        lines.append("Open the Advisor tab on Analytics to apply, snooze or dismiss each one.")
        return title, "\n".join(lines)

    async def send_digest(self, tenant_id: str) -> int:
        recs = await self.recommendations(tenant_id, include_closed=False)
        cfgs = await self.store.list_assistants(tenant_id)
        title, body = self.render_digest(recs, cfgs[0].business_name if cfgs else "")
        await self.notifications.dispatch(
            NotificationEvent(
                tenant_id=tenant_id,
                company_id=cfgs[0].company_id if cfgs else None,
                event=NotifyEvent.ADVISOR_DIGEST,
                title=title,
                body=body,
                context={"recommendations": len(recs)},
            )
        )
        return len(recs)

    async def due(self, tenant_id: str, now: datetime | None = None) -> bool:
        s = await self.settings(tenant_id)
        if not (s.enabled and s.weekly_digest):
            return False
        if not await self.entitled(tenant_id):
            return False
        now = now or datetime.now(UTC)
        local = now.astimezone(ADVISOR_TZ)
        if local.weekday() != s.digest_weekday or local.hour != s.digest_hour:
            return False
        last = [r for r in await self.runs(tenant_id, 5) if r.trigger == "weekly"]
        return not last or (now - last[0].created_at) > timedelta(days=6)

    async def sweep(self, now: datetime | None = None) -> int:
        sent = 0
        seen: set[str] = set()
        for cfg in await self.store.list_assistants():
            if cfg.tenant_id in seen:
                continue
            seen.add(cfg.tenant_id)
            try:
                if await self.due(cfg.tenant_id, now):
                    await self.run(cfg.tenant_id, trigger="weekly")
                    await self.send_digest(cfg.tenant_id)
                    sent += 1
            except Exception:
                log.warning("advisor weekly run failed for %s", cfg.tenant_id, exc_info=True)
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
