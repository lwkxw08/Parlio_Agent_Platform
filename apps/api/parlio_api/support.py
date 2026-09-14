"""Phase 18: AI-powered support desk incl. telephony fault assist.

Parlio runs its own support on Parlio: a reserved *support tenant* whose assistant answers the
support line, in-app chat, WhatsApp and email 24/7 (dogfooding). ``SupportTools`` are the tool
surface the agent (text agent here, voice worker via ``/v1/worker/support``) calls: KB search,
tenant status (identity-verified), forwarding/SIP walkthroughs, synthetic call, SIP diagnostics,
ticket creation and P1 escalation. ``SupportDesk`` is the helpdesk: SLA timers per Phase 17
tiers, engineering escalation seam (Linear), CSAT after every ticket, tag review, and
customer/provider-side fault tickets with a provider-ready report the customer can consent to
have emailed to their provider.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field

from parlio_voice.models import (
    AssistantConfig,
    DayHours,
    Faq,
    Schedule,
)

from .billing import BillingService
from .inbox import AgentTurn, Channel, Direction, InboxMessage, TextAgent, Thread
from .notifications import EmailSender
from .ops import (
    FaultReport,
    ForwardingHealth,
    OpsService,
    SyntheticRun,
    TenantHealth,
    TrunkHealth,
    sla_tier_for_plan,
)
from .sip import PROVIDER_GUIDES, SipService
from .store import CallFilter, CallStore, TenantDoc

log = logging.getLogger("parlio.support")

SUPPORT_TENANT = "parlio-support"
TICKET_KIND = "support_ticket"
UK = ZoneInfo("Europe/London")
HUMAN_HOURS = Schedule(
    timezone="Europe/London",
    hours={
        d: DayHours(open=time(8, 0), close=time(20, 0))
        for d in ("mon", "tue", "wed", "thu", "fri", "sat")
    },
)

Priority = Literal["p1", "p2", "p3", "p4"]
SupportStatus = Literal[
    "open", "in_progress", "waiting_customer", "waiting_provider", "resolved", "closed"
]

SLA_MINUTES: dict[Priority, int] = {"p1": 15, "p2": 60, "p3": 24 * 60, "p4": 3 * 24 * 60}


# -- knowledge base ----------------------------------------------------------------------------


class KbArticle(BaseModel):
    id: str
    title: str
    body: str
    tags: list[str] = Field(default_factory=list)
    url: str | None = None


KB: list[KbArticle] = [
    KbArticle(
        id="forwarding",
        title="Setting up call forwarding to Parlio",
        body=(
            "Forward your existing business number to your Parlio number in your provider's "
            "portal or with a dial code (BT: *21*<number># to enable, #21# to disable; "
            "Vodafone/EE/O2/Three mobiles: **21*<number># enable, ##21# disable). Use "
            "'no answer' forwarding for overflow. Test by calling your number from a mobile."
        ),
        tags=["forwarding", "setup", "telephony"],
    ),
    KbArticle(
        id="forwarding-off",
        title="Calls stopped reaching Parlio (forwarding switched off)",
        body=(
            "Providers sometimes clear forwarding after a line fault, a PBX reboot or a plan "
            "change. Re-enable with the dial code or portal, then run a test call from the "
            "dashboard Health page. Consider porting your number or a Parlio SIP trunk so "
            "there is nothing to switch off."
        ),
        tags=["forwarding", "fault"],
    ),
    KbArticle(
        id="sip",
        title="Connecting a SIP trunk or PBX",
        body=(
            "Telephony page: choose Forwarding, PBX trunk (Parlio issues credentials / IP auth) or "
            "BYO registration (Parlio registers to your provider). Use G.711 A-law and RFC 2833 "
            "DTMF. Allow UDP 5060 and RTP 10000-20000 from Parlio's edge."
        ),
        tags=["sip", "pbx", "setup"],
    ),
    KbArticle(
        id="one-way-audio",
        title="One-way audio or robotic sound on SIP calls",
        body=(
            "Almost always NAT/firewall: disable SIP ALG on the router, allow RTP UDP "
            "10000-20000, and make sure the PBX advertises its public IP. Packet loss above 2% "
            "or jitter above 30ms degrades MOS below 3.6."
        ),
        tags=["sip", "audio", "fault"],
    ),
    KbArticle(
        id="latency",
        title="Assistant takes too long to answer or reply",
        body=(
            "Parlio targets under 2 seconds ring-to-first-word and ~1s per turn. Check the Health "
            "page for pickup latency p95; sustained slowness is Parlio-side and we investigate."
        ),
        tags=["latency", "quality"],
    ),
    KbArticle(
        id="billing",
        title="Plans, minutes and invoices",
        body=(
            "Billing page shows the plan, included minutes, overage and invoices. Upgrade or "
            "downgrade any time; changes are prorated. Credits are applied before Stripe charges."
        ),
        tags=["billing"],
    ),
    KbArticle(
        id="sla",
        title="Support hours and SLAs",
        body=(
            "Human support 8am-8pm UK Monday-Saturday via ticket queue and shared inbox; the AI "
            "assistant answers 24/7. P1 (service down) 15 minute response 24x7 on paid SLAs, "
            "P2 1 business hour, P3 next business day. Enterprise/Sovereign get Slack Connect and "
            "a named CSM. Voice uptime SLA 99.9% (99.95% Enterprise/Sovereign) on platform "
            "components; forwarding and customer SIP are 'assisted'."
        ),
        tags=["sla", "support"],
    ),
    KbArticle(
        id="porting",
        title="Porting your number to Parlio",
        body=(
            "Porting removes the forwarding dependency. Send a letter of authority and a recent "
            "bill; UK ports take 7-10 working days for single lines. We route the number to your "
            "assistant on port day with no downtime."
        ),
        tags=["porting", "telephony"],
    ),
]

FORWARDING_GUIDES: dict[str, list[str]] = {
    "bt": [
        "Lift the handset and dial *21*<your Parlio number># to divert all calls.",
        "Dial #21# to cancel. For divert-on-no-answer use *61*<number>#.",
        "BT Cloud Voice / Business: Portal > Users > Call forwarding > Always.",
    ],
    "virgin": [
        "Dial *21*<your Parlio number># (divert all) or *61*<number># (no answer).",
        "Cancel with #21# / #61#.",
    ],
    "vodafone": ["Dial **21*<number># to enable, ##21# to disable (also on Vodafone Business)."],
    "ee": ["Dial **21*<number># to enable, ##21# to disable."],
    "o2": ["Dial **21*<number># to enable, ##21# to disable."],
    "three": ["Dial **21*<number># to enable, ##21# to disable."],
    "teams": [
        "Teams admin center > Users > Calling > Call answering rules > Forward to external number.",
        "Or the user: Settings > Calls > Forward my calls > New number or contact.",
    ],
    "generic": [
        "Log in to your provider portal and find Call forwarding / Divert.",
        "Set 'Always' (or 'No answer' for overflow) to your Parlio number.",
        "Call your number from a mobile: the assistant should answer within one ring.",
    ],
}


def kb_search(query: str, limit: int = 3) -> list[KbArticle]:
    q = {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2}
    scored: list[tuple[int, KbArticle]] = []
    for a in KB:
        text = f"{a.title} {a.body} {' '.join(a.tags)}".lower()
        words = set(re.findall(r"[a-z0-9]+", text))
        s = len(q & words) + 2 * sum(1 for t in a.tags if t in q)
        if s:
            scored.append((s, a))
    scored.sort(key=lambda x: -x[0])
    return [a for _, a in scored[:limit]]


def human_support_open(now: datetime | None = None) -> bool:
    return bool(HUMAN_HOURS.is_open(now))


def next_human_window(now: datetime | None = None) -> str:
    now = (now or datetime.now(UTC)).astimezone(UK)
    if human_support_open(now):
        return "now"
    nxt = now
    for _ in range(8):
        nxt = (nxt + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        if HUMAN_HOURS.is_open(nxt):
            return nxt.strftime("%A 8am UK")
    return "next business day"


# -- support tenant ------------------------------------------------------------------------------


def support_assistant(number: str | None) -> AssistantConfig:
    faqs = [Faq(id=a.id, category="support", question=a.title, answer=a.body) for a in KB]
    return AssistantConfig(
        tenant_id=SUPPORT_TENANT,
        company_id=SUPPORT_TENANT,
        assistant_id="parlio-support",
        name="Parlio Support",
        business_name="Parlio",
        hours=Schedule(always=True),
        faqs=faqs,
        greeting=(
            "Hi, you've reached Parlio support. I can check your service status, walk you "
            "through forwarding or SIP setup, run a test call, or raise a ticket. How can I help?"
        ),
        instructions=(
            "You are {name}, the 24/7 support assistant for Parlio, an AI phone receptionist "
            "platform. Verify the caller's identity (account email or business number) before "
            "sharing account details. Use tools to check tenant status, run synthetic calls and "
            "SIP diagnostics, and raise tickets. Human support is 8am-8pm UK Mon-Sat; P1 outages "
            "are escalated to the on-call engineer immediately."
        ),
    )


# -- helpdesk models -----------------------------------------------------------------------------


class SupportEvent(BaseModel):
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    type: str  # created | reply | note | status | escalated | provider_emailed | verified | csat
    by: str
    text: str = ""
    public: bool = True


class SupportTicket(BaseModel):
    id: str = Field(default_factory=lambda: f"sup-{uuid4().hex[:8]}")
    tenant_id: str
    requester: str  # email / e164 / "agent"
    channel: Literal["phone", "chat", "whatsapp", "email", "dashboard", "system"] = "dashboard"
    subject: str = Field(min_length=1, max_length=200)
    body: str = ""
    priority: Priority = "p3"
    status: SupportStatus = "open"
    tags: list[str] = Field(default_factory=list)
    assignee: str | None = None
    sla_due_at: datetime | None = None
    sla_breached: bool = False
    first_response_at: datetime | None = None
    fault: FaultReport | None = None
    provider_email: str | None = None
    provider_consent: bool = False
    provider_emailed_at: datetime | None = None
    verified_healthy_at: datetime | None = None
    engineering_ref: str | None = None
    csat_score: int | None = Field(default=None, ge=1, le=5)
    csat_comment: str | None = None
    csat_requested_at: datetime | None = None
    events: list[SupportEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None

    def customer_view(self) -> SupportTicket:
        """Copy without internal notes / engineering references (what the tenant sees)."""
        return self.model_copy(
            update={"events": [e for e in self.events if e.public], "engineering_ref": None}
        )

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=TICKET_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


class SupportTicketIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = ""
    priority: Priority = "p3"
    tags: list[str] = Field(default_factory=list)
    channel: Literal["phone", "chat", "whatsapp", "email", "dashboard", "system"] = "dashboard"


class TenantStatus(BaseModel):
    tenant_id: str
    business_name: str
    plan_id: str
    subscription_status: str
    sla_tier: str
    health: TenantHealth
    forwarding: ForwardingHealth
    trunks: list[TrunkHealth]
    recent_calls: int
    last_call_at: datetime | None
    open_tickets: int


class TagReview(BaseModel):
    days: int
    tickets: int
    resolved: int
    csat_avg: float | None
    csat_responses: int
    sla_breaches: int
    median_first_response_min: float | None
    by_tag: dict[str, int]
    by_priority: dict[str, int]
    by_channel: dict[str, int]
    backlog_suggestions: list[str]


# -- engineering escalation seam ----------------------------------------------------------------


class IssueTracker(Protocol):
    async def create(self, ticket: SupportTicket, summary: str) -> str: ...


class LogIssueTracker:
    def __init__(self) -> None:
        self.created: list[str] = []

    async def create(self, ticket: SupportTicket, summary: str) -> str:
        ref = f"ENG-{len(self.created) + 1}"
        self.created.append(ref)
        return ref


class LinearIssueTracker:
    def __init__(self, api_key: str, team_id: str, client: httpx.AsyncClient | None = None) -> None:
        self.api_key = api_key
        self.team_id = team_id
        self.client = client or httpx.AsyncClient(timeout=10.0)

    async def create(self, ticket: SupportTicket, summary: str) -> str:
        q = (
            "mutation($input: IssueCreateInput!) { issueCreate(input: $input) "
            "{ issue { identifier url } } }"
        )
        body = {
            "query": q,
            "variables": {
                "input": {
                    "teamId": self.team_id,
                    "title": f"[{ticket.priority.upper()}] {ticket.subject}",
                    "description": summary,
                    "priority": {"p1": 1, "p2": 2, "p3": 3, "p4": 4}[ticket.priority],
                }
            },
        }
        r = await self.client.post(
            "https://api.linear.app/graphql", json=body, headers={"Authorization": self.api_key}
        )
        r.raise_for_status()
        data = r.json()["data"]["issueCreate"]["issue"]
        return str(data["identifier"])


# -- desk ------------------------------------------------------------------------------------------


class SupportDesk:
    def __init__(
        self,
        store: CallStore,
        ops: OpsService,
        sip: SipService,
        billing: BillingService,
        email: EmailSender,
        tracker: IssueTracker,
        *,
        dashboard_url: str = "",
        page_p1: Callable[[SupportTicket], Awaitable[bool]] | None = None,
    ) -> None:
        self.store = store
        self.ops = ops
        self.sip = sip
        self.billing = billing
        self.email = email
        self.tracker = tracker
        self.dashboard_url = dashboard_url
        self.page_p1 = page_p1

    # -- identity -------------------------------------------------------------------------------
    async def verify_identity(self, tenant_id: str, proof: str) -> bool:
        """Proof is a member email or a number owned by the tenant (DDI / Parlio number)."""
        p = proof.strip().lower()
        if not p or tenant_id == SUPPORT_TENANT:
            return False
        for m in await self.store.list_members(tenant_id):
            if m.email.lower() == p:
                return True
        digits = re.sub(r"\D", "", p)
        if len(digits) >= 9:
            for cfg in await self.store.list_assistants(tenant_id):
                if any(
                    digits[-9:] == re.sub(r"\D", "", n)[-9:]
                    for n in (cfg.business.phone or "",)
                    if n
                ):
                    return True
            for t in await self.sip.trunks(tenant_id):
                if any(digits[-9:] == re.sub(r"\D", "", d.e164)[-9:] for d in t.ddis):
                    return True
            nums = await self.billing.list_numbers(tenant_id)
            if any(digits[-9:] == re.sub(r"\D", "", n.e164)[-9:] for n in nums):
                return True
        return False

    async def find_tenant(self, proof: str) -> str | None:
        p = proof.strip().lower()
        if "@" in p:
            ms = await self.store.memberships_for_email(p)
            ids = {m.tenant_id for m in ms if m.tenant_id != SUPPORT_TENANT}
            return sorted(ids)[0] if ids else None
        digits = re.sub(r"\D", "", p)
        if len(digits) < 9:
            return None
        for cfg in await self.store.list_assistants(None):
            if cfg.business.phone and re.sub(r"\D", "", cfg.business.phone)[-9:] == digits[-9:]:
                return str(cfg.tenant_id)
        return None

    # -- tools ----------------------------------------------------------------------------------
    async def tenant_status(self, tenant_id: str) -> TenantStatus:
        sub = await self.billing.subscription(tenant_id)
        cfgs = await self.store.list_assistants(tenant_id)
        health = await self.ops.tenant_health(tenant_id)
        now = datetime.now(UTC)
        calls = await self.store.filter_calls(
            CallFilter(tenant_id=tenant_id, since=now - timedelta(days=7), until=now, limit=5000)
        )
        tier = sla_tier_for_plan(sub.plan_id, sub.plan_id in ("enterprise", "sovereign"))
        open_t = [
            t for t in await self.tickets(tenant_id) if t.status not in ("resolved", "closed")
        ]
        return TenantStatus(
            tenant_id=tenant_id,
            business_name=cfgs[0].business_name if cfgs else tenant_id,
            plan_id=sub.plan_id,
            subscription_status=str(sub.status),
            sla_tier=tier.id,
            health=health,
            forwarding=await self.ops.forwarding_health(tenant_id, cfgs, now),
            trunks=await self.ops.trunk_health(tenant_id, calls),
            recent_calls=len(calls),
            last_call_at=max((c.started_at for c in calls), default=None),
            open_tickets=len(open_t),
        )

    def walkthrough_forwarding(self, carrier: str | None) -> dict[str, Any]:
        key = (carrier or "generic").lower()
        for k in FORWARDING_GUIDES:
            if k in key:
                key = k
                break
        else:
            key = "generic"
        return {
            "carrier": key,
            "steps": FORWARDING_GUIDES[key],
            "test": (
                "Call your business number from a mobile; the assistant should answer within "
                "one ring. Then run a synthetic call from Health."
            ),
        }

    def walkthrough_sip(self, provider: str | None) -> dict[str, Any]:
        p = (provider or "").lower()
        guide = next((g for g in PROVIDER_GUIDES if g.id in p or p in g.name.lower()), None)
        if guide is None:
            guide = next(g for g in PROVIDER_GUIDES if g.id == "forward")
        return {
            "provider": guide.id,
            "name": guide.name,
            "mode": guide.mode,
            "steps": guide.steps,
            "quirks": guide.quirks,
            "defaults": guide.defaults,
        }

    async def run_synthetic_call(self, tenant_id: str) -> SyntheticRun:
        return await self.ops.synthetic_call(tenant_id, trigger="support")

    async def run_sip_diagnostics(
        self, tenant_id: str, trunk_id: str | None = None
    ) -> list[TrunkHealth]:
        trunks = await self.ops.trunk_health(tenant_id)
        return [t for t in trunks if trunk_id is None or t.trunk_id == trunk_id]

    # -- tickets ----------------------------------------------------------------------------------
    async def _plan_p1_24x7(self, tenant_id: str) -> bool:
        return await self.billing.entitled(tenant_id, "priority_support")

    async def create_ticket(
        self,
        tenant_id: str,
        requester: str,
        data: SupportTicketIn,
        *,
        fault: FaultReport | None = None,
    ) -> SupportTicket:
        now = datetime.now(UTC)
        t = SupportTicket(
            tenant_id=tenant_id,
            requester=requester,
            channel=data.channel,
            subject=data.subject,
            body=data.body,
            priority=data.priority,
            tags=sorted(
                set(data.tags) | ({"telephony-fault", fault.attribution} if fault else set())
            ),
            fault=fault,
        )
        t.sla_due_at = self._sla_due(t.priority, now, await self._plan_p1_24x7(tenant_id))
        if fault is not None and fault.attribution in ("customer_provider", "carrier"):
            t.status = "waiting_provider"
        t.events.append(SupportEvent(type="created", by=requester, text=data.body[:500]))
        if t.priority == "p1":
            paged = self.page_p1 is not None and await self.page_p1(t)
            t.events.append(
                SupportEvent(
                    type="escalated",
                    by="system",
                    text="P1: on-call engineer paged" if paged else "P1: on-call not configured",
                    public=False,
                )
            )
        await self.store.put_doc(t.to_doc())
        return t

    def _sla_due(self, priority: Priority, now: datetime, p1_24x7: bool) -> datetime:
        minutes = SLA_MINUTES[priority]
        if priority == "p1" and p1_24x7:
            return now + timedelta(minutes=minutes)
        # business-hours clock: advance through human support windows
        remaining = timedelta(minutes=minutes)
        t = now
        step = timedelta(minutes=15)
        for _ in range(4 * 24 * 14):
            if remaining <= timedelta(0):
                break
            if human_support_open(t):
                remaining -= step
            t += step
        return t

    async def tickets(
        self, tenant_id: str | None = None, *, open_only: bool = False
    ) -> list[SupportTicket]:
        docs = await self.store.list_docs(TICKET_KIND, tenant_id, limit=5000)
        out = [SupportTicket.model_validate(d.data) for d in docs]
        if open_only:
            out = [t for t in out if t.status not in ("resolved", "closed")]
        out.sort(key=lambda t: t.created_at, reverse=True)
        return out

    async def get(self, ticket_id: str, tenant_id: str | None = None) -> SupportTicket | None:
        d = await self.store.get_doc(TICKET_KIND, ticket_id)
        if d is None or (tenant_id is not None and d.tenant_id != tenant_id):
            return None
        return SupportTicket.model_validate(d.data)

    async def _save(self, t: SupportTicket) -> SupportTicket:
        t.updated_at = datetime.now(UTC)
        await self.store.put_doc(t.to_doc())
        return t

    async def reply(
        self, t: SupportTicket, by: str, text: str, *, staff: bool, public: bool = True
    ) -> SupportTicket:
        t.events.append(
            SupportEvent(type="reply" if public else "note", by=by, text=text, public=public)
        )
        if staff and public and t.first_response_at is None:
            t.first_response_at = datetime.now(UTC)
        if staff and public and t.status == "open":
            t.status = "in_progress"
        if not staff and t.status == "waiting_customer":
            t.status = "in_progress"
        return await self._save(t)

    async def set_status(
        self,
        t: SupportTicket,
        status: SupportStatus,
        by: str,
        *,
        assignee: str | None = None,
        priority: Priority | None = None,
        tags: list[str] | None = None,
    ) -> SupportTicket:
        if assignee is not None:
            t.assignee = assignee
        if priority is not None and priority != t.priority:
            t.priority = priority
            t.sla_due_at = self._sla_due(
                priority, datetime.now(UTC), await self._plan_p1_24x7(t.tenant_id)
            )
        if tags is not None:
            t.tags = sorted(set(tags))
        if status != t.status:
            t.status = status
            t.events.append(SupportEvent(type="status", by=by, text=status))
        if status in ("resolved", "closed") and t.resolved_at is None:
            t.resolved_at = datetime.now(UTC)
            t.csat_requested_at = t.resolved_at
            t.events.append(
                SupportEvent(
                    type="csat",
                    by="system",
                    text="How did we do? Rate this ticket 1-5 on your Support page.",
                )
            )
        return await self._save(t)

    async def csat(self, t: SupportTicket, score: int, comment: str | None) -> SupportTicket:
        t.csat_score = score
        t.csat_comment = comment
        t.events.append(
            SupportEvent(type="csat", by=t.requester, text=f"{score}/5 {comment or ''}".strip())
        )
        return await self._save(t)

    async def escalate_engineering(self, t: SupportTicket, by: str, summary: str) -> SupportTicket:
        report = f"{summary}\n\nTenant: {t.tenant_id}\nTicket: {t.id}\n\n{t.body}"
        if t.fault:
            report += "\n\n" + t.fault.provider_report
        t.engineering_ref = await self.tracker.create(t, report)
        t.events.append(
            SupportEvent(
                type="escalated", by=by, text=f"engineering {t.engineering_ref}", public=False
            )
        )
        return await self._save(t)

    async def sweep_sla(self, now: datetime | None = None) -> list[SupportTicket]:
        now = now or datetime.now(UTC)
        out: list[SupportTicket] = []
        for t in await self.tickets(open_only=True):
            if (
                t.sla_due_at
                and t.first_response_at is None
                and not t.sla_breached
                and t.sla_due_at < now
            ):
                t.sla_breached = True
                t.events.append(
                    SupportEvent(type="status", by="system", text="SLA breached", public=False)
                )
                await self._save(t)
                out.append(t)
            if (
                t.status == "waiting_provider"
                and t.fault is not None
                and await self._fault_cleared(t)
            ):
                t.verified_healthy_at = now
                t.events.append(
                    SupportEvent(
                        type="verified",
                        by="system",
                        text="Telephony verified healthy; closing loop.",
                    )
                )
                await self.set_status(t, "resolved", "system")
                out.append(t)
        return out

    async def _fault_cleared(self, t: SupportTicket) -> bool:
        f = t.fault
        if f is None:
            return False
        if f.subject == "forwarding":
            fwd = await self.ops.forwarding_health(t.tenant_id)
            return fwd.status in ("ok", "no_baseline", "quiet")
        if f.subject.startswith("trunk:"):
            th = await self.run_sip_diagnostics(t.tenant_id, f.subject.split(":", 1)[1])
            return bool(th) and th[0].healthy
        return False

    # -- fault logging on the customer's behalf ------------------------------------------------
    async def open_fault_ticket(
        self,
        tenant_id: str,
        requester: str,
        fault: FaultReport,
        *,
        channel: Literal["phone", "chat", "whatsapp", "email", "dashboard", "system"] = "system",
    ) -> SupportTicket:
        side = {
            "customer_provider": "your phone provider / PBX",
            "carrier": "our carrier",
            "parlio": "Parlio",
            "customer_config": "your Parlio configuration",
            "unknown": "unknown",
        }[fault.attribution]
        pri: Priority = "p2" if fault.attribution in ("parlio", "carrier") else "p3"
        t = await self.create_ticket(
            tenant_id,
            requester,
            SupportTicketIn(
                subject=fault.headline,
                body=(
                    f"Suspected side: {side}.\n{fault.explanation}\n\nNext steps:\n- "
                    + "\n- ".join(fault.next_steps)
                    + "\n\nPermanent fix: port your number to Parlio or move to a Parlio SIP "
                    "trunk so forwarding cannot be switched off."
                ),
                priority=pri,
                tags=["telephony-fault"],
                channel=channel,
            ),
            fault=fault,
        )
        return t

    async def email_provider(
        self, t: SupportTicket, provider_email: str, by: str, consent: bool
    ) -> SupportTicket:
        if not consent:
            raise ValueError("customer consent is required before contacting their provider")
        if t.fault is None:
            raise ValueError("ticket has no fault report")
        subject = f"Fault report: {t.fault.headline} (Parlio ref {t.id})"
        body = (
            f"Hello,\n\nOn behalf of our mutual customer (Parlio account {t.tenant_id}) we are "
            f"reporting the following fault.\n\n{t.fault.provider_report}\n\nPlease reply to this "
            f"address quoting {t.id}.\n\nParlio Support"
        )
        await self.email.send(provider_email, subject, body)
        t.provider_email = provider_email
        t.provider_consent = True
        t.provider_emailed_at = datetime.now(UTC)
        t.status = "waiting_provider"
        t.events.append(
            SupportEvent(type="provider_emailed", by=by, text=f"sent to {provider_email}")
        )
        return await self._save(t)

    # -- review ---------------------------------------------------------------------------------
    async def tag_review(self, days: int = 7) -> TagReview:
        since = datetime.now(UTC) - timedelta(days=days)
        ts = [t for t in await self.tickets() if t.created_at >= since]
        tags: Counter[str] = Counter()
        pri: Counter[str] = Counter()
        chan: Counter[str] = Counter()
        frt: list[float] = []
        for t in ts:
            tags.update(t.tags or ["untagged"])
            pri[t.priority] += 1
            chan[t.channel] += 1
            if t.first_response_at:
                frt.append((t.first_response_at - t.created_at).total_seconds() / 60)
        cs = [t.csat_score for t in ts if t.csat_score is not None]
        frt.sort()
        med = round(frt[len(frt) // 2], 1) if frt else None
        suggestions = [
            f"{tag}: {n} tickets this week — candidate for a product fix or self-serve doc"
            for tag, n in tags.most_common(3)
            if n >= 3 and tag != "untagged"
        ]
        return TagReview(
            days=days,
            tickets=len(ts),
            resolved=sum(1 for t in ts if t.status in ("resolved", "closed")),
            csat_avg=round(sum(cs) / len(cs), 2) if cs else None,
            csat_responses=len(cs),
            sla_breaches=sum(1 for t in ts if t.sla_breached),
            median_first_response_min=med,
            by_tag=dict(tags),
            by_priority=dict(pri),
            by_channel=dict(chan),
            backlog_suggestions=suggestions,
        )


# -- text agent for the support tenant ---------------------------------------------------------

_STATUS = ("status", "down", "not working", "stopped", "no calls", "not answering", "outage")
_FWD = ("forward", "divert", "forwarding")
_SIP = ("sip", "pbx", "trunk", "register", "3cx", "teams")
_TEST = ("test call", "synthetic", "run a test")
_HUMAN = ("human", "person", "someone", "agent", "call me", "urgent", "emergency")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(\+?\d[\d\s]{8,}\d)")


class SupportTextAgent:
    """Answers the support tenant's chat/WhatsApp/SMS threads with the desk's tools; delegates
    every other tenant to the wrapped agent."""

    name = "support"

    def __init__(self, desk: SupportDesk, fallback: TextAgent) -> None:
        self.desk = desk
        self.fallback = fallback

    async def respond(
        self, cfg: AssistantConfig, thread: Thread, history: list[InboxMessage]
    ) -> AgentTurn:
        if cfg.tenant_id != SUPPORT_TENANT:
            return await self.fallback.respond(cfg, thread, history)
        last = next((m for m in reversed(history) if m.direction == Direction.IN), None)
        text = (last.text if last else "").strip()
        low = text.lower()
        verified = thread.tags and any(t.startswith("verified:") for t in thread.tags)
        tenant_id = next(
            (t.split(":", 1)[1] for t in thread.tags if t.startswith("verified:")), None
        )

        if any(k in low for k in _HUMAN):
            pri: Priority = (
                "p1" if ("down" in low or "urgent" in low or "emergency" in low) else "p3"
            )
            t = await self.desk.create_ticket(
                tenant_id or SUPPORT_TENANT,
                thread.identity,
                SupportTicketIn(
                    subject=f"{thread.channel}: caller asked for a person",
                    body=text[:500],
                    priority=pri,
                    channel=_chan(thread.channel),
                    tags=["human-requested"],
                ),
            )
            when = (
                "now — a P1 has been raised and the on-call engineer paged"
                if pri == "p1"
                else (
                    "now"
                    if human_support_open()
                    else f"at {next_human_window()} (human support is 8am-8pm UK Mon-Sat)"
                )
            )
            return AgentTurn(
                reply=f"I've raised ticket {t.id}; a person will pick this up {when}.", handoff=True
            )

        if not verified:
            proof = _EMAIL.search(text) or _PHONE.search(text)
            if proof:
                tid = await self.desk.find_tenant(proof.group(0))
                if tid and await self.desk.verify_identity(tid, proof.group(0)):
                    thread.tags = [*thread.tags, f"verified:{tid}"]
                    await self.desk.store.put_doc(thread.to_doc())
                    st = await self.desk.tenant_status(tid)
                    return AgentTurn(
                        reply=f"Thanks — verified {st.business_name}. {_status_line(st)} What "
                        "would you like to do: forwarding help, SIP diagnostics, a test call, "
                        "or raise a ticket?"
                    )
                return AgentTurn(
                    reply="I couldn't match that to an account. Please send the email address "
                    "you log in with, or your business phone number."
                )
            if any(k in low for k in _STATUS + _TEST + _SIP):
                return AgentTurn(
                    reply="Happy to check that. First, to verify you, what email do you log in "
                    "with (or your business phone number)?"
                )
        elif tenant_id:
            if any(k in low for k in _TEST):
                run = await self.desk.run_synthetic_call(tenant_id)
                bad = [c.path for c in run.checks if not c.passed]
                return AgentTurn(
                    reply=(
                        "Test call passed: greeting, FAQ, booking and transfer paths all OK."
                        if run.passed
                        else f"Test call failed on {', '.join(bad)} — I've logged this "
                        f"({run.ticket_id or run.id}) for the team."
                    )
                )
            if any(k in low for k in _SIP):
                th = await self.desk.run_sip_diagnostics(tenant_id)
                if not th:
                    g = self.desk.walkthrough_sip(text)
                    return AgentTurn(
                        reply=f"You don't have a SIP trunk yet. {g['name']}: "
                        + " ".join(g["steps"])
                    )
                unhealthy = [x for x in th if not x.healthy]
                if unhealthy:
                    fr = await self.desk.ops.classify_trunk(tenant_id, unhealthy[0].trunk_id)
                    if fr:
                        tk = await self.desk.open_fault_ticket(
                            tenant_id, thread.identity, fr, channel=_chan(thread.channel)
                        )
                        return AgentTurn(
                            reply=f"{fr.headline}. {fr.explanation} Next: {fr.next_steps[0]} "
                            f"I've opened fault ticket {tk.id} with the evidence; say 'email my "
                            "provider' and I'll send them the report with your consent."
                        )
                return AgentTurn(
                    reply="SIP diagnostics: registration OK, no failed INVITEs, audio quality "
                    "within limits."
                )
            if any(k in low for k in _STATUS + _FWD):
                st = await self.desk.tenant_status(tenant_id)
                if st.forwarding.status == "forwarding_may_be_off" or any(k in low for k in _FWD):
                    g = self.desk.walkthrough_forwarding(text)
                    fr = await self.desk.ops.classify_forwarding(tenant_id)
                    extra = ""
                    if fr.attribution == "customer_provider":
                        tk = await self.desk.open_fault_ticket(
                            tenant_id, thread.identity, fr, channel=_chan(thread.channel)
                        )
                        extra = (
                            f" I've opened {tk.id} and will keep checking until calls flow again."
                        )
                    return AgentTurn(
                        reply=f"{_status_line(st)} To re-enable forwarding ({g['carrier']}): "
                        + " ".join(g["steps"])
                        + extra
                    )
                return AgentTurn(reply=_status_line(st))
        arts = kb_search(text)
        if arts:
            return AgentTurn(reply=f"{arts[0].title}: {arts[0].body}")
        return await self.fallback.respond(cfg, thread, history)


def _chan(c: Channel) -> Literal["phone", "chat", "whatsapp", "email", "dashboard", "system"]:
    if c == Channel.WHATSAPP:
        return "whatsapp"
    if c == Channel.WEBCHAT:
        return "chat"
    return "phone"


def _status_line(st: TenantStatus) -> str:
    fwd = {
        "ok": "inbound calls look normal",
        "no_baseline": "not enough call history yet to judge forwarding",
        "quiet": "quieter than usual",
        "forwarding_may_be_off": "no calls during open hours — forwarding may be off",
        "outside_hours": "closed right now",
    }[st.forwarding.status]
    trunks = (
        f", {sum(1 for t in st.trunks if not t.healthy)} of {len(st.trunks)} trunks unhealthy"
        if st.trunks
        else ""
    )
    return (
        f"Health score {st.health.score}/100 ({st.health.grade}); {st.recent_calls} calls in "
        f"7 days; {fwd}{trunks}. Plan {st.plan_id}, SLA {st.sla_tier}."
    )
