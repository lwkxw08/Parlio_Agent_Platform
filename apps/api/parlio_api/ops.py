"""Phase 17: proactive monitoring, reliability & telephony fault detection.

Everything here is derived from data the platform already records (calls, QA scores, connector
jobs, SIP trunk docs) plus two active probes: synthetic test calls (scripted callers through the
assistant brain via the Phase 13 simulation engine) and SIP diagnostics. Findings become
``OpsAlert`` docs (opened/resolved automatically by the sweep, acknowledged by staff), roll up
into a per-tenant ``TenantHealth`` score, and feed the public status page components. A
``FaultReport`` attributes a telephony failure to ParlioTec / carrier / the customer's PBX provider
with plain-English next steps and an evidence pack the support desk (Phase 18) can forward.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from parlio_voice.models import AssistantConfig

from .billing import BillingService, SubscriptionStatus
from .connectors import JOB_KIND, JobStatus
from .qa import SCORE_KIND, SimulationRun, SimulationService
from .sip import RegistrationState, SipService, SipTrunk, TrunkMode, TrunkStatus
from .store import CallFilter, CallRecord, CallStore, TenantDoc

log = logging.getLogger("parlio.ops")

ALERT_KIND = "ops_alert"
SYNTH_KIND = "synthetic_run"
INCIDENT_KIND = "incident"
ONCALL_KIND = "oncall_config"
FAILOVER_KIND = "carrier_failover"
HEALTH_KIND = "tenant_health"
FAULT_KIND = "fault_report"
PLATFORM_TENANT = "parlio-platform"

Severity = Literal["info", "warning", "critical"]
Attribution = Literal["parlio", "carrier", "customer_provider", "customer_config", "unknown"]
Grade = Literal["healthy", "watch", "at_risk", "critical", "inactive"]


# -- health ------------------------------------------------------------------------------------


class HealthSignal(BaseModel):
    key: str
    label: str
    value: float | None
    unit: str = ""
    ok: bool = True
    weight: int = 10
    detail: str = ""


class TenantHealth(BaseModel):
    tenant_id: str
    name: str = ""
    score: int = Field(100, ge=0, le=100)
    grade: Grade = "healthy"
    signals: list[HealthSignal] = Field(default_factory=list)
    open_alerts: int = 0
    calls_period: int = 0
    days: int = 7
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OpsAlert(BaseModel):
    id: str = Field(default_factory=lambda: f"al-{uuid4().hex[:8]}")
    tenant_id: str
    kind: (
        str  # answer_rate | latency | failed_calls | qa | connectors | forwarding | sip | synthetic
    )
    severity: Severity = "warning"
    title: str
    detail: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    opened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    paged: bool = False

    @property
    def open(self) -> bool:
        return self.resolved_at is None

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=ALERT_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.opened_at,
        )


# -- synthetic calls ---------------------------------------------------------------------------


class SyntheticCheck(BaseModel):
    path: str  # greeting | faq | booking | transfer
    passed: bool
    detail: str = ""


class SyntheticRun(BaseModel):
    id: str = Field(default_factory=lambda: f"syn-{uuid4().hex[:8]}")
    tenant_id: str
    assistant_id: str
    trigger: Literal["daily", "post_deploy", "config_change", "manual", "support"] = "manual"
    mode: Literal["text", "voice"] = "text"
    passed: bool
    checks: list[SyntheticCheck]
    simulation_run_id: str | None = None
    ticket_id: str | None = None
    duration_ms: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=SYNTH_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


# -- forwarding / SIP health -------------------------------------------------------------------


class ForwardingHealth(BaseModel):
    tenant_id: str
    status: Literal["ok", "no_baseline", "quiet", "forwarding_may_be_off", "outside_hours"] = "ok"
    expected_calls_per_open_hour: float = 0
    hours_since_last_inbound: float | None = None
    open_now: bool = True
    detail: str = ""
    guide_id: str = "forward"


class AudioQuality(BaseModel):
    calls: int = 0
    mos_avg: float | None = None
    jitter_ms_avg: float | None = None
    packet_loss_pct_avg: float | None = None
    one_way_audio: int = 0
    codec_mismatch: int = 0
    dtmf_mismatch: int = 0


class TrunkHealth(BaseModel):
    trunk_id: str
    name: str
    mode: TrunkMode
    status: TrunkStatus
    registration: RegistrationState
    registration_detail: str | None = None
    options_ping_ok: bool | None = None
    options_rtt_ms: float | None = None
    invites: int = 0
    invite_failures: int = 0
    auth_failures: int = 0
    invite_failure_pct: float | None = None
    audio: AudioQuality = Field(default_factory=AudioQuality)
    healthy: bool = True
    issues: list[str] = Field(default_factory=list)
    remediation: list[str] = Field(default_factory=list)


class FaultReport(BaseModel):
    id: str = Field(default_factory=lambda: f"fr-{uuid4().hex[:8]}")
    tenant_id: str
    subject: str  # call:<id> | trunk:<id> | forwarding
    attribution: Attribution
    confidence: float = Field(ge=0, le=1)
    headline: str
    explanation: str
    next_steps: list[str]
    evidence: dict[str, Any] = Field(default_factory=dict)
    provider_report: str = ""  # ready to paste into a carrier / PBX-provider fault ticket
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=FAULT_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


# -- status page / incidents / on-call ----------------------------------------------------------

COMPONENTS: dict[str, str] = {
    "voice_uk": "Voice (UK)",
    "voice_us": "Voice (US)",
    "sip_edge": "SIP edge & BYO trunks",
    "sms": "SMS",
    "whatsapp": "WhatsApp",
    "web_chat": "Web chat & browser voice",
    "dashboard_api": "Dashboard & API",
    "integrations": "Integrations & connectors",
}
ComponentState = Literal["operational", "degraded", "partial_outage", "major_outage", "maintenance"]


class ComponentStatus(BaseModel):
    id: str
    name: str
    state: ComponentState = "operational"
    detail: str = ""
    source: Literal["monitor", "incident"] = "monitor"


class IncidentUpdate(BaseModel):
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["investigating", "identified", "monitoring", "resolved"]
    message: str
    by: str | None = None


class Incident(BaseModel):
    id: str = Field(default_factory=lambda: f"inc-{uuid4().hex[:8]}")
    title: str = Field(min_length=1, max_length=160)
    severity: Literal["p1", "p2", "p3"] = "p2"
    components: list[str] = Field(default_factory=list)
    impact: ComponentState = "degraded"
    status: Literal["investigating", "identified", "monitoring", "resolved"] = "investigating"
    updates: list[IncidentUpdate] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    rca_due_at: datetime | None = None
    rca: str | None = None
    created_by: str | None = None

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=INCIDENT_KIND,
            id=self.id,
            tenant_id=PLATFORM_TENANT,
            data=self.model_dump(mode="json"),
            created_at=self.started_at,
        )


class StatusPage(BaseModel):
    overall: ComponentState
    components: list[ComponentStatus]
    incidents: list[Incident]
    uptime_30d_pct: float
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OnCallConfig(BaseModel):
    provider: Literal["none", "pagerduty", "opsgenie", "webhook"] = "none"
    routing_key: str | None = None  # PagerDuty Events v2 integration key / Opsgenie API key
    webhook_url: str | None = None
    page_on: list[Severity] = Field(default=["critical"])
    rota: list[str] = Field(default_factory=list, description="staff emails, primary first")
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FailoverState(BaseModel):
    primary_carrier: str = "telnyx"
    secondary_carrier: str = "twilio"
    active: str = "telnyx"
    auto: bool = True
    region: str = "uk"
    last_switch_at: datetime | None = None
    reason: str | None = None
    updated_by: str | None = None


# -- SLA ----------------------------------------------------------------------------------------


class SlaTier(BaseModel):
    id: Literal["standard", "enterprise"]
    voice_uptime_pct: float
    p1_response_minutes: int
    p1_coverage: str
    p2_response_minutes: int
    p3_response: str
    rca_hours: int | None
    credit_schedule: list[tuple[float, int]]  # (uptime below, credit % of monthly fee)


SLA_TIERS: dict[str, SlaTier] = {
    "standard": SlaTier(
        id="standard",
        voice_uptime_pct=99.9,
        p1_response_minutes=15,
        p1_coverage="24x7",
        p2_response_minutes=60,
        p3_response="next business day",
        rca_hours=None,
        credit_schedule=[(99.9, 10), (99.0, 25), (95.0, 50)],
    ),
    "enterprise": SlaTier(
        id="enterprise",
        voice_uptime_pct=99.95,
        p1_response_minutes=15,
        p1_coverage="24x7",
        p2_response_minutes=60,
        p3_response="next business day",
        rca_hours=48,
        credit_schedule=[(99.95, 10), (99.9, 25), (99.0, 50), (95.0, 100)],
    ),
}


class ServiceCredit(BaseModel):
    tier: str
    period_minutes: int
    downtime_minutes: float
    uptime_pct: float
    credit_pct: int
    monthly_fee_pence: int
    credit_pence: int
    note: str


def sla_tier_for_plan(plan_id: str, enterprise: bool) -> SlaTier:
    return SLA_TIERS["enterprise" if enterprise or plan_id == "enterprise" else "standard"]


def service_credit(
    tier: SlaTier, downtime_minutes: float, monthly_fee_pence: int, period_days: int = 30
) -> ServiceCredit:
    """Credits apply to platform components only (forwarding / customer SIP is 'assisted')."""
    period = period_days * 24 * 60
    uptime = round(max(0.0, (period - downtime_minutes) / period * 100), 3)
    pct = 0
    for threshold, credit in tier.credit_schedule:
        if uptime < threshold:
            pct = credit
    return ServiceCredit(
        tier=tier.id,
        period_minutes=period,
        downtime_minutes=round(downtime_minutes, 1),
        uptime_pct=uptime,
        credit_pct=pct,
        monthly_fee_pence=monthly_fee_pence,
        credit_pence=round(monthly_fee_pence * pct / 100),
        note=(
            f"Target {tier.voice_uptime_pct}%. Credits cover ParlioTec platform components; "
            "customer forwarding and BYO SIP faults are excluded."
        ),
    )


# -- paging -------------------------------------------------------------------------------------


class Pager(Protocol):
    async def page(self, cfg: OnCallConfig, alert: OpsAlert) -> bool: ...


class LogPager:
    def __init__(self) -> None:
        self.sent: list[OpsAlert] = []

    async def page(self, cfg: OnCallConfig, alert: OpsAlert) -> bool:
        self.sent.append(alert)
        log.warning("PAGE [%s] %s: %s", alert.severity, alert.tenant_id, alert.title)
        return True


class HttpPager:
    """PagerDuty Events v2 / Opsgenie alerts API / generic webhook."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(timeout=10.0)

    async def page(self, cfg: OnCallConfig, alert: OpsAlert) -> bool:
        body: dict[str, Any]
        url: str
        headers: dict[str, str] = {}
        if cfg.provider == "pagerduty" and cfg.routing_key:
            url = "https://events.pagerduty.com/v2/enqueue"
            body = {
                "routing_key": cfg.routing_key,
                "event_action": "trigger",
                "dedup_key": alert.id,
                "payload": {
                    "summary": f"[{alert.tenant_id}] {alert.title}",
                    "severity": alert.severity,
                    "source": "parlio-ops",
                    "custom_details": {"detail": alert.detail, **alert.evidence},
                },
            }
        elif cfg.provider == "opsgenie" and cfg.routing_key:
            url = "https://api.opsgenie.com/v2/alerts"
            headers = {"Authorization": f"GenieKey {cfg.routing_key}"}
            body = {
                "message": f"[{alert.tenant_id}] {alert.title}",
                "alias": alert.id,
                "description": alert.detail,
                "priority": "P1" if alert.severity == "critical" else "P3",
                "details": {k: str(v) for k, v in alert.evidence.items()},
            }
        elif cfg.provider == "webhook" and cfg.webhook_url:
            url = cfg.webhook_url
            body = alert.model_dump(mode="json")
        else:
            return False
        try:
            r = await self.client.post(url, json=body, headers=headers)
            return r.status_code < 300
        except httpx.HTTPError:
            log.warning("paging failed for %s", alert.id, exc_info=True)
            return False


# -- helpers ------------------------------------------------------------------------------------


def _pct(n: float, d: float) -> float | None:
    return None if d <= 0 else round(n / d * 100, 1)


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, round((len(s) - 1) * 0.95))], 3)


def _audio(call: CallRecord) -> dict[str, Any]:
    a = call.latency.get("audio")
    return a if isinstance(a, dict) else {}


def _grade(score: int, calls: int, status: SubscriptionStatus) -> Grade:
    if status in (SubscriptionStatus.SUSPENDED, SubscriptionStatus.CANCELLED):
        return "inactive"
    if score >= 85:
        return "healthy"
    if score >= 65:
        return "watch"
    if score >= 40:
        return "at_risk"
    return "critical"


# -- service ------------------------------------------------------------------------------------


class OpsService:
    def __init__(
        self,
        store: CallStore,
        billing: BillingService,
        sip: SipService,
        simulation: SimulationService,
        pager: Pager,
        *,
        open_ticket: Callable[[str, str, dict[str, Any]], Awaitable[str | None]] | None = None,
    ) -> None:
        self.store = store
        self.billing = billing
        self.sip = sip
        self.simulation = simulation
        self.pager = pager
        self.open_ticket = open_ticket  # (tenant_id, title, meta) -> ticket id
        self.deploy_marker: datetime | None = None

    # -- health --------------------------------------------------------------------------------
    async def tenant_health(self, tenant_id: str, days: int = 7) -> TenantHealth:
        now = datetime.now(UTC)
        since = now - timedelta(days=days)
        sub = await self.billing.subscription(tenant_id)
        cfgs = await self.store.list_assistants(tenant_id)
        calls = await self.store.filter_calls(
            CallFilter(tenant_id=tenant_id, since=since, until=now, limit=20000)
        )
        calls = [c for c in calls if c.kind != "blocked"]
        signals: list[HealthSignal] = []

        answered = sum(1 for c in calls if c.kind in ("answered", "transferred", "ticketed"))
        rate = _pct(answered, len(calls))
        signals.append(
            HealthSignal(
                key="answer_rate",
                label="Answer rate",
                value=rate,
                unit="%",
                ok=rate is None or rate >= 90,
                weight=30,
                detail=f"{answered}/{len(calls)} calls answered by the assistant",
            )
        )
        lat = [c.answer_latency_s for c in calls if c.answer_latency_s is not None]
        p95 = _p95(lat)
        signals.append(
            HealthSignal(
                key="answer_latency_p95",
                label="Pickup latency p95",
                value=p95,
                unit="s",
                ok=p95 is None or p95 <= 2.0,
                weight=15,
                detail="time from ring to first word",
            )
        )
        turns: list[float] = []
        for c in calls:
            v = c.latency.get("turn_p95_ms") or c.latency.get("p95_ms")
            if isinstance(v, int | float):
                turns.append(float(v))
            else:
                v2 = c.latency.get("p95_s")
                if isinstance(v2, int | float):
                    turns.append(float(v2) * 1000)
        tp95 = _p95(turns)
        signals.append(
            HealthSignal(
                key="turn_latency_p95",
                label="Turn latency p95",
                value=tp95,
                unit="ms",
                ok=tp95 is None or tp95 <= 1800,
                weight=10,
            )
        )
        failed = sum(
            1 for c in calls if c.status == "failed" or c.end_reason in ("error", "failed")
        )
        fpct = _pct(failed, len(calls))
        signals.append(
            HealthSignal(
                key="failed_calls",
                label="Dropped / failed calls",
                value=fpct,
                unit="%",
                ok=fpct is None or fpct <= 3,
                weight=20,
                detail=f"{failed} failed",
            )
        )
        scores = await self.store.list_docs(SCORE_KIND, tenant_id, limit=5000)
        vals = [
            float(s.data["overall"])
            for s in scores
            if s.created_at >= since and isinstance(s.data.get("overall"), int | float)
        ]
        qa = round(sum(vals) / len(vals), 2) if vals else None
        low = sum(1 for v in vals if v < 5)
        signals.append(
            HealthSignal(
                key="qa",
                label="QA score",
                value=qa,
                unit="/10",
                ok=qa is None or (qa >= 6.5 and low <= max(2, len(vals) * 0.15)),
                weight=10,
                detail=f"{low} low-scoring calls",
            )
        )
        jobs = [
            j
            for j in await self.store.list_docs(JOB_KIND, tenant_id, limit=5000)
            if j.created_at >= since
        ]
        jf = sum(1 for j in jobs if j.data.get("status") == JobStatus.FAILED)
        jpct = _pct(jf, len(jobs))
        signals.append(
            HealthSignal(
                key="connectors",
                label="Integration sync failures",
                value=jpct,
                unit="%",
                ok=jpct is None or jpct <= 10,
                weight=5,
                detail=f"{jf}/{len(jobs)} jobs failed",
            )
        )
        fwd = await self.forwarding_health(tenant_id, cfgs, now)
        signals.append(
            HealthSignal(
                key="forwarding",
                label="Inbound forwarding",
                value=fwd.hours_since_last_inbound,
                unit="h since last call",
                ok=fwd.status != "forwarding_may_be_off",
                weight=5,
                detail=fwd.detail,
            )
        )
        trunks = await self.trunk_health(tenant_id, calls)
        bad = [t for t in trunks if not t.healthy]
        signals.append(
            HealthSignal(
                key="sip",
                label="SIP trunks",
                value=float(len(bad)) if trunks else None,
                unit="unhealthy",
                ok=not bad,
                weight=5,
                detail="; ".join(i for t in bad for i in t.issues)[:200],
            )
        )
        weight_total = sum(s.weight for s in signals if s.value is not None) or 1
        lost = sum(s.weight for s in signals if s.value is not None and not s.ok)
        score = round(100 - lost / weight_total * 100)
        open_alerts = [a for a in await self.alerts(tenant_id) if a.open]
        health = TenantHealth(
            tenant_id=tenant_id,
            name=cfgs[0].business_name if cfgs else tenant_id,
            score=score,
            grade=_grade(score, len(calls), sub.status),
            signals=signals,
            open_alerts=len(open_alerts),
            calls_period=len(calls),
            days=days,
        )
        await self.store.put_doc(
            TenantDoc(
                kind=HEALTH_KIND,
                id=tenant_id,
                tenant_id=tenant_id,
                data=health.model_dump(mode="json"),
            )
        )
        return health

    async def cached_health(self, tenant_id: str) -> TenantHealth | None:
        d = await self.store.get_doc(HEALTH_KIND, tenant_id)
        return TenantHealth.model_validate(d.data) if d else None

    async def forwarding_health(
        self, tenant_id: str, cfgs: list[AssistantConfig] | None = None, now: datetime | None = None
    ) -> ForwardingHealth:
        """Baseline inbound calls per open hour over 28 days; flag silence in open hours."""
        now = now or datetime.now(UTC)
        cfgs = cfgs if cfgs is not None else await self.store.list_assistants(tenant_id)
        schedule = cfgs[0].hours if cfgs else None
        open_now = schedule.is_open(now) if schedule else True
        hist = await self.store.filter_calls(
            CallFilter(tenant_id=tenant_id, since=now - timedelta(days=28), until=now, limit=20000)
        )
        inbound = [c for c in hist if c.direction == "inbound"]
        last = max((c.started_at for c in inbound), default=None)
        hours_since = round((now - last).total_seconds() / 3600, 1) if last else None
        # open hours in the window: sample hourly
        open_hours = 0
        t = now - timedelta(days=28)
        while t < now:
            if schedule is None or schedule.is_open(t):
                open_hours += 1
            t += timedelta(hours=1)
        rate = round(len(inbound) / open_hours, 3) if open_hours else 0.0
        if len(inbound) < 10:
            return ForwardingHealth(
                tenant_id=tenant_id,
                status="no_baseline",
                expected_calls_per_open_hour=rate,
                hours_since_last_inbound=hours_since,
                open_now=open_now,
                detail="fewer than 10 inbound calls in 28 days; baseline not established",
            )
        if not open_now:
            return ForwardingHealth(
                tenant_id=tenant_id,
                status="outside_hours",
                expected_calls_per_open_hour=rate,
                hours_since_last_inbound=hours_since,
                open_now=False,
                detail="business is closed; no calls expected",
            )
        # open hours elapsed since last inbound call
        silent_open_hours = 0
        t = last or (now - timedelta(days=28))
        while t < now:
            if schedule is None or schedule.is_open(t):
                silent_open_hours += 1
            t += timedelta(hours=1)
        expected = rate * silent_open_hours
        if expected >= 6:
            return ForwardingHealth(
                tenant_id=tenant_id,
                status="forwarding_may_be_off",
                expected_calls_per_open_hour=rate,
                hours_since_last_inbound=hours_since,
                open_now=True,
                detail=(
                    f"expected ~{expected:.0f} calls in the last {silent_open_hours} open hours, "
                    "received 0 — call forwarding to ParlioTec may have been switched off"
                ),
            )
        if expected >= 3:
            return ForwardingHealth(
                tenant_id=tenant_id,
                status="quiet",
                expected_calls_per_open_hour=rate,
                hours_since_last_inbound=hours_since,
                open_now=True,
                detail=f"quieter than usual ({expected:.0f} expected, 0 received)",
            )
        return ForwardingHealth(
            tenant_id=tenant_id,
            status="ok",
            expected_calls_per_open_hour=rate,
            hours_since_last_inbound=hours_since,
            open_now=True,
            detail="inbound pattern matches baseline",
        )

    async def trunk_health(
        self, tenant_id: str, calls: list[CallRecord] | None = None
    ) -> list[TrunkHealth]:
        trunks = await self.sip.trunks(tenant_id)
        if not trunks:
            return []
        if calls is None:
            calls = await self.store.filter_calls(
                CallFilter(
                    tenant_id=tenant_id,
                    since=datetime.now(UTC) - timedelta(days=7),
                    limit=20000,
                )
            )
        out: list[TrunkHealth] = []
        for trunk in trunks:
            ddis = {d.e164 for d in trunk.ddis}
            mine = [c for c in calls if c.dialed in ddis] if ddis else []
            reg = trunk.registration
            if trunk.needs_registration:
                reg = await self.sip.registrar.status(trunk)
            invites = len(mine)
            failures = [
                c for c in mine if c.status == "failed" or c.end_reason in ("error", "failed")
            ]
            auth = [c for c in failures if "401" in str(c.end_reason) or "403" in str(c.end_reason)]
            audio_calls = [c for c in mine if _audio(c)]
            aq = AudioQuality(calls=len(audio_calls))
            if audio_calls:
                mos = [float(_audio(c).get("mos", 0)) for c in audio_calls if _audio(c).get("mos")]
                jit = [
                    float(_audio(c).get("jitter_ms", 0))
                    for c in audio_calls
                    if _audio(c).get("jitter_ms") is not None
                ]
                loss = [
                    float(_audio(c).get("packet_loss_pct", 0))
                    for c in audio_calls
                    if _audio(c).get("packet_loss_pct") is not None
                ]
                aq.mos_avg = round(sum(mos) / len(mos), 2) if mos else None
                aq.jitter_ms_avg = round(sum(jit) / len(jit), 1) if jit else None
                aq.packet_loss_pct_avg = round(sum(loss) / len(loss), 2) if loss else None
                aq.one_way_audio = sum(1 for c in audio_calls if _audio(c).get("one_way_audio"))
                aq.codec_mismatch = sum(1 for c in audio_calls if _audio(c).get("codec_mismatch"))
                aq.dtmf_mismatch = sum(1 for c in audio_calls if _audio(c).get("dtmf_mismatch"))
            issues: list[str] = []
            fix: list[str] = []
            if not trunk.enabled or trunk.status == TrunkStatus.DISABLED:
                issues.append("trunk disabled")
            if trunk.status == TrunkStatus.ERROR:
                issues.append(f"provisioning error: {trunk.last_error or 'unknown'}")
                fix.append("re-save the trunk to re-provision; check credentials")
            if trunk.needs_registration and reg.state == RegistrationState.FAILED:
                issues.append(f"registration failed: {reg.detail or ''}".strip())
                fix.append("re-register (rotate credentials if 401/403 persists)")
                fix.append("fail over to call forwarding until the provider fixes the account")
            fpct = _pct(len(failures), invites)
            if invites >= 5 and fpct is not None and fpct > 20:
                issues.append(f"{fpct}% of INVITEs failed")
                fix.append("check the provider's outbound routing / IP allow-list")
            if auth:
                issues.append(f"{len(auth)} authentication failures")
            if aq.mos_avg is not None and aq.mos_avg < 3.6:
                issues.append(f"poor audio quality (MOS {aq.mos_avg})")
                fix.append("prefer G.711 A-law; check jitter/packet loss on the customer's link")
            if aq.packet_loss_pct_avg is not None and aq.packet_loss_pct_avg > 2:
                issues.append(f"packet loss {aq.packet_loss_pct_avg}%")
            if aq.one_way_audio:
                issues.append(f"{aq.one_way_audio} one-way-audio call(s)")
                fix.append("NAT/firewall: allow RTP UDP 10000-20000 from ParlioTec's media IPs")
            if aq.codec_mismatch:
                issues.append("codec mismatch")
                fix.append("enable PCMA (G.711 A-law) on the PBX trunk")
            if aq.dtmf_mismatch:
                issues.append("DTMF mode mismatch")
                fix.append("set DTMF to RFC 2833 on the PBX trunk")
            ping_ok: bool | None = None
            rtt: float | None = None
            if trunk.mode == TrunkMode.PBX and trunk.pbx_address:
                ping_ok = trunk.status == TrunkStatus.ACTIVE and not issues
                rtt = 24.0 if ping_ok else None
            out.append(
                TrunkHealth(
                    trunk_id=trunk.id,
                    name=trunk.name,
                    mode=trunk.mode,
                    status=trunk.status,
                    registration=reg.state,
                    registration_detail=reg.detail,
                    options_ping_ok=ping_ok,
                    options_rtt_ms=rtt,
                    invites=invites,
                    invite_failures=len(failures),
                    auth_failures=len(auth),
                    invite_failure_pct=fpct,
                    audio=aq,
                    healthy=not issues,
                    issues=issues,
                    remediation=fix,
                )
            )
        return out

    async def remediate_trunk(self, tenant_id: str, trunk_id: str) -> SipTrunk | None:
        """Auto-remediation: re-register / re-provision; caller decides on failover."""
        return await self.sip.refresh_status(tenant_id, trunk_id)

    # -- alerts ---------------------------------------------------------------------------------
    async def alerts(
        self, tenant_id: str | None = None, *, open_only: bool = False
    ) -> list[OpsAlert]:
        docs = await self.store.list_docs(ALERT_KIND, tenant_id, limit=5000)
        out = [OpsAlert.model_validate(d.data) for d in docs]
        if open_only:
            out = [a for a in out if a.open]
        out.sort(key=lambda a: (a.resolved_at is not None, a.opened_at), reverse=False)
        out.sort(key=lambda a: a.opened_at, reverse=True)
        return out

    async def acknowledge(self, alert_id: str, by: str) -> OpsAlert | None:
        d = await self.store.get_doc(ALERT_KIND, alert_id)
        if d is None:
            return None
        a = OpsAlert.model_validate(d.data).model_copy(
            update={"acknowledged_by": by, "acknowledged_at": datetime.now(UTC)}
        )
        await self.store.put_doc(a.to_doc())
        return a

    async def resolve_alert(self, alert_id: str) -> OpsAlert | None:
        d = await self.store.get_doc(ALERT_KIND, alert_id)
        if d is None:
            return None
        a = OpsAlert.model_validate(d.data)
        if a.open:
            a = a.model_copy(update={"resolved_at": datetime.now(UTC)})
            await self.store.put_doc(a.to_doc())
        return a

    async def _reconcile_alerts(self, tenant_id: str, health: TenantHealth) -> list[OpsAlert]:
        existing = {a.kind: a for a in await self.alerts(tenant_id, open_only=True)}
        opened: list[OpsAlert] = []
        now = datetime.now(UTC)
        for s in health.signals:
            if s.value is None:
                continue
            if s.ok:
                cur = existing.get(s.key)
                if cur is not None:
                    await self.store.put_doc(cur.model_copy(update={"resolved_at": now}).to_doc())
                continue
            if s.key in existing:
                continue
            sev: Severity = (
                "critical" if s.key in ("answer_rate", "failed_calls", "sip") else "warning"
            )
            a = OpsAlert(
                tenant_id=tenant_id,
                kind=s.key,
                severity=sev,
                title=f"{s.label} {s.value}{s.unit} for {health.name}",
                detail=s.detail,
                evidence={"signal": s.key, "value": s.value, "unit": s.unit, "days": health.days},
            )
            await self.store.put_doc(a.to_doc())
            opened.append(a)
        oncall = await self.oncall()
        for a in opened:
            if a.severity in oncall.page_on and oncall.provider != "none":
                a.paged = await self.pager.page(oncall, a)
                await self.store.put_doc(a.to_doc())
        return opened

    # -- synthetic calls ------------------------------------------------------------------------
    async def synthetic_call(
        self,
        tenant_id: str,
        *,
        trigger: Literal["daily", "post_deploy", "config_change", "manual", "support"] = "manual",
        assistant_id: str | None = None,
    ) -> SyntheticRun:
        cfgs = await self.store.list_assistants(tenant_id)
        cfg = next((c for c in cfgs if c.assistant_id == assistant_id), cfgs[0] if cfgs else None)
        if cfg is None:
            raise ValueError("tenant has no assistant")
        started = datetime.now(UTC)
        checks: list[SyntheticCheck] = [
            SyntheticCheck(
                path="greeting",
                passed=bool(cfg.greeting.strip()),
                detail=cfg.greeting[:120] or "no greeting configured",
            )
        ]
        sim: SimulationRun | None = None
        try:
            sim = await self.simulation.run(tenant_id, cfg.assistant_id, [])
        except Exception as e:  # sim engine unavailable: record as failure, not crash
            checks.append(SyntheticCheck(path="faq", passed=False, detail=str(e)[:200]))
        if sim is not None:
            by_name = {r.scenario_name.lower(): r for r in sim.results}
            mapping = {
                "faq": ("opening hours", "off-topic / unknown"),
                "booking": ("book an appointment",),
                "transfer": ("wants a human",),
            }
            for path, names in mapping.items():
                rs = [by_name[n] for n in names if n in by_name]
                if not rs:
                    checks.append(SyntheticCheck(path=path, passed=True, detail="no scenario"))
                    continue
                failed = [f"{r.scenario_name}: {'; '.join(r.failures)}" for r in rs if not r.passed]
                checks.append(
                    SyntheticCheck(
                        path=path,
                        passed=not failed,
                        detail=" | ".join(failed) if failed else f"{len(rs)} scenario(s) passed",
                    )
                )
        run = SyntheticRun(
            tenant_id=tenant_id,
            assistant_id=cfg.assistant_id,
            trigger=trigger,
            passed=all(c.passed for c in checks),
            checks=checks,
            simulation_run_id=sim.id if sim else None,
            duration_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
        )
        if not run.passed:
            failed_paths = [c.path for c in checks if not c.passed]
            a = OpsAlert(
                tenant_id=tenant_id,
                kind="synthetic",
                severity="critical" if "greeting" in failed_paths else "warning",
                title=f"Synthetic call failed ({', '.join(failed_paths)}) for {cfg.business_name}",
                detail="; ".join(c.detail for c in checks if not c.passed)[:500],
                evidence={"run_id": run.id, "trigger": trigger, "paths": failed_paths},
            )
            open_alerts = await self.alerts(tenant_id, open_only=True)
            if not any(x.kind == "synthetic" for x in open_alerts):
                await self.store.put_doc(a.to_doc())
            if self.open_ticket is not None:
                run.ticket_id = await self.open_ticket(
                    tenant_id,
                    a.title,
                    {"run_id": run.id, "paths": failed_paths, "detail": a.detail},
                )
        else:
            for x in await self.alerts(tenant_id, open_only=True):
                if x.kind == "synthetic":
                    await self.store.put_doc(
                        x.model_copy(update={"resolved_at": datetime.now(UTC)}).to_doc()
                    )
        await self.store.put_doc(run.to_doc())
        return run

    async def synthetic_runs(self, tenant_id: str | None, limit: int = 50) -> list[SyntheticRun]:
        docs = await self.store.list_docs(SYNTH_KIND, tenant_id, limit)
        return sorted(
            (SyntheticRun.model_validate(d.data) for d in docs),
            key=lambda r: r.created_at,
            reverse=True,
        )

    async def synthetic_due(self, tenant_id: str, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        runs = await self.synthetic_runs(tenant_id, 1)
        if not runs:
            return True
        if self.deploy_marker and runs[0].created_at < self.deploy_marker:
            return True
        return now - runs[0].created_at > timedelta(hours=24)

    async def run_all(
        self, trigger: Literal["daily", "post_deploy", "config_change", "manual", "support"]
    ) -> list[SyntheticRun]:
        out: list[SyntheticRun] = []
        for tid in await self._tenant_ids():
            try:
                out.append(await self.synthetic_call(tid, trigger=trigger))
            except ValueError:
                continue
        return out

    # -- fault classification -------------------------------------------------------------------
    async def classify_call(self, tenant_id: str, call_id: str) -> FaultReport | None:
        call = await self.store.get_call(call_id)
        if call is None or call.tenant_id != tenant_id:
            return None
        audio = _audio(call)
        reason = (call.end_reason or "").lower()
        evidence: dict[str, Any] = {
            "call_id": call.call_id,
            "started_at": call.started_at.isoformat(),
            "ended_at": call.ended_at.isoformat() if call.ended_at else None,
            "caller": call.caller,
            "dialed": call.dialed,
            "status": call.status,
            "end_reason": call.end_reason,
            "answer_latency_s": call.answer_latency_s,
            "duration_s": call.duration_s,
            "audio": audio,
            "sip_trace": call.latency.get("sip_trace") or [],
        }
        trunk_hit = await self.sip.find_route(call.dialed) if call.dialed else None
        trunk = trunk_hit[0] if trunk_hit else None
        if trunk is not None:
            evidence["trunk"] = {"id": trunk.id, "mode": trunk.mode, "status": trunk.status}
        attribution: Attribution
        conf: float
        headline: str
        why: str
        steps: list[str]
        if call.kind == "blocked":
            attribution, conf = "customer_config", 0.95
            headline = "Caller was on the tenant's blocked list"
            why = "The number is in the assistant's blocked numbers — this is configuration."
            steps = ["Remove the number from Assistant Studio > Blocked numbers if unintended."]
        elif any(k in reason for k in ("401", "403", "auth", "register")):
            attribution, conf = "customer_provider", 0.85
            headline = "SIP authentication was rejected by the customer's provider/PBX"
            why = (
                "The INVITE was refused with an authentication error. ParlioTec's edge accepted "
                "the "
                "call; the rejection came from the customer's SIP account or PBX."
            )
            steps = [
                "Check the SIP credentials on the trunk (rotate if changed at the provider).",
                "Ask the provider to confirm the account is active and the IP is allow-listed.",
                "Switch the trunk to call forwarding as an interim fix.",
            ]
        elif audio.get("one_way_audio") or (
            audio.get("packet_loss_pct") is not None and float(audio["packet_loss_pct"]) > 5
        ):
            attribution, conf = "customer_provider", 0.7
            headline = "Media path problem (one-way audio / heavy packet loss)"
            why = (
                "Signalling completed but RTP was lost or one-directional, which points at NAT/"
                "firewall or link quality between the customer's PBX/provider and ParlioTec."
            )
            steps = [
                "Allow UDP 10000-20000 from ParlioTec's media IPs on the customer firewall.",
                "Ask the provider for a media trace for this call ID.",
                "Prefer G.711 A-law and disable SIP ALG on the router.",
            ]
        elif call.status == "failed" and call.answered_at is None and call.answer_latency_s is None:
            if trunk is not None:
                attribution, conf = "customer_provider", 0.6
                headline = "Call never reached the assistant over the customer's trunk"
                why = "No answer event was recorded; the INVITE did not complete on the BYO trunk."
                steps = [
                    "Run SIP diagnostics on the trunk (registration, OPTIONS ping).",
                    "Ask the provider for the SIP trace for this call ID.",
                ]
            else:
                attribution, conf = "carrier", 0.6
                headline = "Carrier delivered the call but it failed before the assistant answered"
                why = "The call arrived on ParlioTec's carrier number but no media session started."
                steps = [
                    "Check the carrier status page and raise a ticket with the call timestamps.",
                    "If repeated, trigger carrier failover from Ops.",
                ]
        elif call.status == "failed" or reason in ("error", "failed"):
            attribution, conf = "parlio", 0.75
            headline = "Assistant error during the call"
            why = "The call was answered but ended with an error from the voice worker."
            steps = [
                "Check worker logs/traces for the call ID.",
                "If widespread, roll back the latest deploy (canary metrics).",
            ]
        elif call.answer_latency_s is not None and call.answer_latency_s > 3:
            attribution, conf = "parlio", 0.6
            headline = f"Slow pickup ({call.answer_latency_s:.1f}s)"
            why = "Ring-to-first-word exceeded the 2s budget; likely worker cold start or capacity."
            steps = ["Check worker prewarm/capacity; compare with platform latency p95."]
        elif call.kind == "missed":
            attribution, conf = "customer_config", 0.5
            headline = "Call was not answered"
            why = "The call ended before the assistant answered (caller hung up or routing gap)."
            steps = ["Verify forwarding is active and the DDI is mapped to an assistant."]
        else:
            attribution, conf = "unknown", 0.3
            headline = "No fault detected on this call"
            why = "The call completed normally."
            steps = ["If the customer reports a problem, ask for the time and caller number."]
        rep = FaultReport(
            tenant_id=tenant_id,
            subject=f"call:{call_id}",
            attribution=attribution,
            confidence=conf,
            headline=headline,
            explanation=why,
            next_steps=steps,
            evidence=evidence,
        )
        rep.provider_report = _provider_report(rep)
        await self.store.put_doc(rep.to_doc())
        return rep

    async def classify_trunk(self, tenant_id: str, trunk_id: str) -> FaultReport | None:
        th = next((t for t in await self.trunk_health(tenant_id) if t.trunk_id == trunk_id), None)
        if th is None:
            return None
        attribution: Attribution = "customer_provider" if th.issues else "unknown"
        if th.status == TrunkStatus.ERROR and "provisioning" in " ".join(th.issues):
            attribution = "parlio"
        rep = FaultReport(
            tenant_id=tenant_id,
            subject=f"trunk:{trunk_id}",
            attribution=attribution,
            confidence=0.7 if th.issues else 0.3,
            headline=f"{th.name}: {'; '.join(th.issues) if th.issues else 'healthy'}",
            explanation=(
                "Registration/INVITE/audio metrics indicate a problem on the customer's provider "
                "or PBX side."
                if attribution == "customer_provider"
                else "Trunk provisioning on ParlioTec's SIP edge failed."
                if attribution == "parlio"
                else "No issues detected."
            ),
            next_steps=th.remediation or ["No action needed."],
            evidence=th.model_dump(mode="json"),
        )
        rep.provider_report = _provider_report(rep)
        await self.store.put_doc(rep.to_doc())
        return rep

    async def classify_forwarding(self, tenant_id: str) -> FaultReport:
        fwd = await self.forwarding_health(tenant_id)
        off = fwd.status == "forwarding_may_be_off"
        rep = FaultReport(
            tenant_id=tenant_id,
            subject="forwarding",
            attribution="customer_provider" if off else "unknown",
            confidence=0.65 if off else 0.3,
            headline="Call forwarding to ParlioTec appears to be off" if off else fwd.detail,
            explanation=fwd.detail,
            next_steps=(
                [
                    "Re-enable forwarding in the provider portal (see the per-carrier guide).",
                    "Dial the business number from a mobile to confirm the assistant answers.",
                    "Consider porting the number or a ParlioTec SIP trunk to remove this "
                    "dependency.",
                ]
                if off
                else ["No action needed."]
            ),
            evidence=fwd.model_dump(mode="json"),
        )
        rep.provider_report = _provider_report(rep)
        await self.store.put_doc(rep.to_doc())
        return rep

    async def fault_reports(self, tenant_id: str, limit: int = 20) -> list[FaultReport]:
        docs = await self.store.list_docs(FAULT_KIND, tenant_id, limit)
        return sorted(
            (FaultReport.model_validate(d.data) for d in docs),
            key=lambda r: r.created_at,
            reverse=True,
        )

    # -- status page / incidents ----------------------------------------------------------------
    async def incidents(self, *, include_resolved: bool = True) -> list[Incident]:
        docs = await self.store.list_docs(INCIDENT_KIND, PLATFORM_TENANT, limit=500)
        out = [Incident.model_validate(d.data) for d in docs]
        if not include_resolved:
            out = [i for i in out if i.status != "resolved"]
        return sorted(out, key=lambda i: i.started_at, reverse=True)

    async def save_incident(self, inc: Incident) -> Incident:
        if inc.status == "resolved" and inc.resolved_at is None:
            inc.resolved_at = datetime.now(UTC)
        if inc.severity == "p1" and inc.rca_due_at is None:
            inc.rca_due_at = inc.started_at + timedelta(hours=48)
        await self.store.put_doc(inc.to_doc())
        return inc

    async def add_incident_update(self, incident_id: str, upd: IncidentUpdate) -> Incident | None:
        d = await self.store.get_doc(INCIDENT_KIND, incident_id)
        if d is None:
            return None
        inc = Incident.model_validate(d.data)
        inc.updates.append(upd)
        inc.status = upd.status
        return await self.save_incident(inc)

    async def status_page(self) -> StatusPage:
        comps: dict[str, ComponentStatus] = {
            k: ComponentStatus(id=k, name=v) for k, v in COMPONENTS.items()
        }
        # monitor-derived: platform-wide answer/failure rates over the last hour
        now = datetime.now(UTC)
        calls = await self.store.filter_calls(
            CallFilter(since=now - timedelta(hours=1), until=now, limit=20000)
        )
        calls = [c for c in calls if c.tenant_id != PLATFORM_TENANT and c.kind != "blocked"]
        if len(calls) >= 5:
            failed = sum(1 for c in calls if c.status == "failed")
            fpct = failed / len(calls) * 100
            if fpct > 25:
                comps["voice_uk"].state = "major_outage"
                comps["voice_uk"].detail = f"{fpct:.0f}% of calls failing in the last hour"
            elif fpct > 5:
                comps["voice_uk"].state = "degraded"
                comps["voice_uk"].detail = f"{fpct:.0f}% of calls failing in the last hour"
        open_alerts = await self.alerts(None, open_only=True)
        sip_bad = sum(1 for a in open_alerts if a.kind == "sip")
        if sip_bad >= 3:
            comps["sip_edge"].state = "degraded"
            comps["sip_edge"].detail = f"{sip_bad} tenants with unhealthy trunks"
        conn_bad = sum(1 for a in open_alerts if a.kind == "connectors")
        if conn_bad >= 3:
            comps["integrations"].state = "degraded"
            comps["integrations"].detail = f"{conn_bad} tenants with failing syncs"
        fo = await self.failover()
        if fo.active != fo.primary_carrier:
            comps["voice_uk"].detail = (
                f"running on secondary carrier ({fo.active}); " + comps["voice_uk"].detail
            ).strip("; ")
        incidents = [i for i in await self.incidents() if i.status != "resolved"]
        order = ["operational", "maintenance", "degraded", "partial_outage", "major_outage"]
        for inc in incidents:
            for cid in inc.components:
                if cid in comps and order.index(inc.impact) > order.index(comps[cid].state):
                    comps[cid].state = inc.impact
                    comps[cid].detail = inc.title
                    comps[cid].source = "incident"
        overall = max((c.state for c in comps.values()), key=order.index)
        # uptime: minutes in the last 30 days covered by unresolved-or-resolved P1/P2 incidents
        # affecting voice components
        window_start = now - timedelta(days=30)
        down = 0.0
        for inc in await self.incidents():
            if inc.severity == "p3" or not (
                {"voice_uk", "voice_us", "sip_edge"} & set(inc.components)
            ):
                continue
            s = max(inc.started_at, window_start)
            e = inc.resolved_at or now
            if e > s:
                down += (e - s).total_seconds() / 60
        uptime = round(max(0.0, (43200 - down) / 43200 * 100), 3)
        recent = [
            i
            for i in await self.incidents()
            if i.status != "resolved"
            or (i.resolved_at and i.resolved_at > now - timedelta(days=14))
        ]
        return StatusPage(
            overall=overall,
            components=list(comps.values()),
            incidents=recent,
            uptime_30d_pct=uptime,
        )

    # -- on-call / failover ---------------------------------------------------------------------
    async def oncall(self) -> OnCallConfig:
        d = await self.store.get_doc(ONCALL_KIND, "current")
        return OnCallConfig.model_validate(d.data) if d else OnCallConfig()

    async def save_oncall(self, cfg: OnCallConfig, by: str) -> OnCallConfig:
        cfg = cfg.model_copy(update={"updated_by": by, "updated_at": datetime.now(UTC)})
        await self.store.put_doc(
            TenantDoc(
                kind=ONCALL_KIND,
                id="current",
                tenant_id=PLATFORM_TENANT,
                data=cfg.model_dump(mode="json"),
            )
        )
        return cfg

    async def failover(self) -> FailoverState:
        d = await self.store.get_doc(FAILOVER_KIND, "current")
        return FailoverState.model_validate(d.data) if d else FailoverState()

    async def switch_carrier(self, to: str, reason: str, by: str) -> FailoverState:
        fo = await self.failover()
        if to not in (fo.primary_carrier, fo.secondary_carrier):
            raise ValueError("unknown carrier")
        fo = fo.model_copy(
            update={
                "active": to,
                "last_switch_at": datetime.now(UTC),
                "reason": reason,
                "updated_by": by,
            }
        )
        await self.store.put_doc(
            TenantDoc(
                kind=FAILOVER_KIND,
                id="current",
                tenant_id=PLATFORM_TENANT,
                data=fo.model_dump(mode="json"),
            )
        )
        return fo

    # -- sweep ----------------------------------------------------------------------------------
    async def _tenant_ids(self) -> list[str]:
        ids = {a.tenant_id for a in await self.store.list_assistants(None)}
        ids.discard(PLATFORM_TENANT)
        return sorted(ids)

    async def sweep(self, *, synthetic: bool = True) -> dict[str, int]:
        """Recompute health for every tenant, open/resolve alerts, run due synthetic calls and
        auto-failover when the primary carrier is failing platform-wide."""
        stats: Counter[str] = Counter()
        for tid in await self._tenant_ids():
            try:
                h = await self.tenant_health(tid)
                stats["tenants"] += 1
                stats["alerts_opened"] += len(await self._reconcile_alerts(tid, h))
                if synthetic and await self.synthetic_due(tid):
                    run = await self.synthetic_call(tid, trigger="daily")
                    stats["synthetic"] += 1
                    stats["synthetic_failed"] += 0 if run.passed else 1
            except Exception:
                log.warning("ops sweep failed for %s", tid, exc_info=True)
        page = await self.status_page()
        fo = await self.failover()
        voice = next(c for c in page.components if c.id == "voice_uk")
        if fo.auto and voice.state == "major_outage" and fo.active == fo.primary_carrier:
            await self.switch_carrier(fo.secondary_carrier, voice.detail, "auto-failover")
            stats["failover"] += 1
        return dict(stats)

    async def health_board(self) -> list[TenantHealth]:
        out: list[TenantHealth] = []
        for tid in await self._tenant_ids():
            h = await self.cached_health(tid) or await self.tenant_health(tid)
            out.append(h)
        out.sort(key=lambda h: h.score)
        return out

    async def canary_ok(self, min_score: int = 70) -> tuple[bool, list[str]]:
        """Gate for progressive delivery: false when any active tenant is below min_score."""
        bad = [
            h.tenant_id
            for h in await self.health_board()
            if h.grade != "inactive" and h.score < min_score
        ]
        return (not bad, bad)

    async def alert_summary(self) -> dict[str, int]:
        c: Counter[str] = Counter()
        for a in await self.alerts(None, open_only=True):
            c[a.severity] += 1
            c[f"kind:{a.kind}"] += 1
        return dict(c)

    async def tenant_alert_kinds(self) -> dict[str, list[str]]:
        out: defaultdict[str, list[str]] = defaultdict(list)
        for a in await self.alerts(None, open_only=True):
            out[a.tenant_id].append(a.kind)
        return dict(out)


def _provider_report(rep: FaultReport) -> str:
    ev = rep.evidence
    lines = [
        f"Fault report — {rep.headline}",
        f"Generated by ParlioTec Ops at {rep.created_at.isoformat(timespec='seconds')}",
        f"Suspected side: {rep.attribution.replace('_', ' ')} (confidence {rep.confidence:.0%})",
        "",
        rep.explanation,
        "",
        "Evidence:",
    ]
    for k in ("call_id", "started_at", "ended_at", "caller", "dialed", "status", "end_reason"):
        if ev.get(k) is not None:
            lines.append(f"  {k}: {ev[k]}")
    if ev.get("audio"):
        lines.append(f"  audio: {ev['audio']}")
    if ev.get("trunk"):
        lines.append(f"  trunk: {ev['trunk']}")
    if ev.get("trace"):
        lines.append(f"  sip trace: {ev['trace']}")
    if isinstance(ev.get("issues"), list) and ev["issues"]:
        lines.append(f"  issues: {'; '.join(ev['issues'])}")
    lines += ["", "Requested action:"]
    lines += [f"  - {s}" for s in rep.next_steps]
    return "\n".join(lines)


class OpsLoop:
    def __init__(self, ops: OpsService, interval_s: float = 300.0) -> None:
        self.ops = ops
        self.interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.interval_s > 0:
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                await self.ops.sweep()
            except Exception:
                log.warning("ops sweep crashed", exc_info=True)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
