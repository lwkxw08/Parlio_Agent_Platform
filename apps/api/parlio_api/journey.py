"""Phase 19 — guided self-serve journey, setup checklist, explainability and trust centre.

* ``Questionnaire`` -> ``recommend_plan``: a few sign-up questions (monthly call volume, what the
  assistant should do, team size, channels) map onto the smallest plan whose included minutes and
  entitlements cover the answers.
* ``VERTICALS``: starter playbooks (greeting, FAQs, rules, required fields) applied at onboarding.
* ``SetupChecklist``: computed live from real state (assistant published, number/forwarding, first
  call, calendar, alerts, team, billing) — nothing is stored, so it can never go stale.
* ``explain_call``: "why did the AI say this?" — for every assistant turn, the FAQ / rule / business
  fact from the *pinned* assistant version whose wording best overlaps the reply.
* ``TrustCentre``: public sub-processors, residency, policies; ``CheckInLoop`` sends the day-7 /
  day-30 onboarding check-ins for every tenant exactly once.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from parlio_api.billing import ENTITLEMENTS, BillingService, Plan, SubscriptionStatus
from parlio_api.calendar import CalendarService
from parlio_api.notifications import NotificationEvent, NotificationService, NotifyEvent
from parlio_api.sip import SipService
from parlio_api.store import CallRecord, CallStore, TenantDoc
from parlio_api.whitelabel import SUB_PROCESSORS
from parlio_voice.models import AssistantConfig, BusinessRule, Faq

log = logging.getLogger("parlio.journey")

QUESTIONNAIRE_KIND = "onboarding_questionnaire"
CHECKIN_KIND = "onboarding_checkin"

CallVolume = Literal["0-50", "50-200", "200-500", "500+"]
Channel = Literal["phone", "sms", "whatsapp", "webchat"]
Task = Literal[
    "faqs", "book", "transfer", "messages", "info", "qualify", "payments", "outbound", "webchat"
]
Vertical = Literal[
    "trades",
    "salon",
    "hospitality",
    "professional",
    "dental",
    "legal",
    "property",
    "general",
]

# Midpoint call volume x a conservative 2.5 min average handle time.
_VOLUME_MINUTES: dict[str, int] = {"0-50": 75, "50-200": 320, "200-500": 900, "500+": 2000}
_TASK_ENTITLEMENTS: dict[str, list[str]] = {
    "book": ["calendar_booking"],
    "transfer": ["warm_transfers", "departments"],
    "payments": ["payments"],
    "outbound": ["outbound"],
    "webchat": ["browser_voice"],
    "qualify": ["value_reports"],
}
_CHANNEL_ENTITLEMENTS: dict[str, list[str]] = {"whatsapp": ["whatsapp"], "sms": ["sms_scenarios"]}


DEFAULT_TASKS: tuple[Task, ...] = ("faqs", "messages")
DEFAULT_CHANNELS: tuple[Channel, ...] = ("phone",)


class Questionnaire(BaseModel):
    monthly_calls: CallVolume = "50-200"
    tasks: list[Task] = Field(default_factory=lambda: list(DEFAULT_TASKS))
    team_size: int = Field(default=1, ge=1, le=10000)
    channels: list[Channel] = Field(default_factory=lambda: list(DEFAULT_CHANNELS))
    languages: list[str] = Field(default_factory=lambda: ["en"])
    integrations: list[str] = Field(default_factory=list)  # e.g. "hubspot", "zapier", "pbx"
    vertical: Vertical = "general"
    sovereign_uk: bool = False
    locations: int = Field(default=1, ge=1, le=1000)


class PlanOption(BaseModel):
    plan_id: str
    name: str
    monthly_pence: int
    included_minutes: int
    fits: bool
    missing: list[str] = Field(default_factory=list)
    estimated_monthly_pence: int


class PlanRecommendation(BaseModel):
    plan_id: str
    reasons: list[str]
    needed_entitlements: list[str]
    estimated_minutes: int
    options: list[PlanOption]
    trial_days: int


def needed_entitlements(q: Questionnaire) -> list[str]:
    need: list[str] = []
    for t in q.tasks:
        need.extend(_TASK_ENTITLEMENTS.get(t, []))
    for c in q.channels:
        need.extend(_CHANNEL_ENTITLEMENTS.get(c, []))
    if [lang for lang in q.languages if lang != "en"]:
        need.append("languages")
    if "pbx" in q.integrations or "sip" in q.integrations:
        need.append("byo_sip")
    if any(i not in ("pbx", "sip") for i in q.integrations):
        need.append("connectors")
    if q.sovereign_uk:
        need.append("sovereign_uk")
    if q.team_size >= 5 and "transfer" in q.tasks:
        need.append("departments")
    return sorted({k for k in need if k in ENTITLEMENTS})


def _estimate(plan: Plan, minutes: int) -> int:
    over = max(0, minutes - plan.included_minutes) if not plan.enterprise else 0
    return plan.monthly_pence + over * plan.overage_pence_per_minute


def recommend_plan(q: Questionnaire, plans: list[Plan], trial_days: int) -> PlanRecommendation:
    need = needed_entitlements(q)
    minutes = _VOLUME_MINUTES[q.monthly_calls]
    options: list[PlanOption] = []
    for p in sorted(plans, key=lambda p: (p.enterprise, p.monthly_pence)):
        missing = [k for k in need if k not in p.entitlements]
        enough_minutes = p.enterprise or p.included_minutes * 1.25 >= minutes
        enough_seats = p.enterprise or p.max_assistants >= q.locations
        options.append(
            PlanOption(
                plan_id=p.id,
                name=p.name,
                monthly_pence=p.monthly_pence,
                included_minutes=p.included_minutes,
                fits=not missing and enough_minutes and enough_seats,
                missing=missing
                + ([] if enough_minutes else ["minutes"])
                + ([] if enough_seats else ["assistants"]),
                estimated_monthly_pence=_estimate(p, minutes),
            )
        )
    fit = [o for o in options if o.fits]
    pick = fit[0] if fit else options[-1]
    plan = next(p for p in plans if p.id == pick.plan_id)
    reasons = [f"~{minutes} minutes/month expected from {q.monthly_calls} calls"]
    if plan.enterprise:
        reasons.append("Sovereign / high-volume needs are quoted individually")
    else:
        reasons.append(
            f"{plan.included_minutes} minutes included, {len(plan.entitlements)} features"
        )
    for k in need:
        reasons.append(f"Includes {ENTITLEMENTS[k].split(' (')[0].lower()}")
    return PlanRecommendation(
        plan_id=plan.id,
        reasons=reasons[:6],
        needed_entitlements=need,
        estimated_minutes=minutes,
        options=options,
        trial_days=plan.trial_days if plan.trial_days is not None else trial_days,
    )


# -- vertical playbooks ---------------------------------------------------------------------------


class VerticalPlaybook(BaseModel):
    id: Vertical
    name: str
    tagline: str
    greeting: str
    faqs: list[Faq]
    rules: list[BusinessRule]
    required_fields: list[str]
    suggested_tasks: list[Task]


def _faq(q: str, a: str) -> Faq:
    return Faq(category="general", question=q, answer=a, source="playbook")


def _rule(name: str, instruction: str) -> BusinessRule:
    return BusinessRule(name=name, instruction=instruction)


VERTICALS: list[VerticalPlaybook] = [
    VerticalPlaybook(
        id="trades",
        name="Trades & home services",
        tagline="Plumbers, electricians, builders, locksmiths",
        greeting=(
            "Hi, thanks for calling {business_name}. Are you calling about a new job or an "
            "existing one?"
        ),
        faqs=[
            _faq(
                "Do you do emergency call-outs?",
                "Yes — tell me what's happening and I'll get someone to call you straight back.",
            ),
            _faq(
                "Do you give free quotes?",
                "We'll take the details and arrange a free, no-obligation quote.",
            ),
            _faq(
                "Which areas do you cover?",
                "We cover the local area — give me your postcode and I'll check.",
            ),
        ],
        rules=[
            _rule(
                "Never quote prices",
                "Never quote prices or timescales; take details and promise a call back.",
            ),
            _rule(
                "Emergencies",
                "If the caller mentions a leak, no heating, no power or being locked out, "
                "treat it as urgent.",
            ),
        ],
        required_fields=["name", "phone", "postcode", "job description"],
        suggested_tasks=["messages", "qualify", "transfer", "faqs"],
    ),
    VerticalPlaybook(
        id="salon",
        name="Salons, clinics & studios",
        tagline="Hair, beauty, barbers, aesthetics, fitness",
        greeting=(
            "Hi, thanks for calling {business_name}. Would you like to book, change or ask "
            "about an appointment?"
        ),
        faqs=[
            _faq("How do I cancel?", "Let us know at least 24 hours ahead and there's no charge."),
            _faq("Do you take walk-ins?", "We do when there's space, but booking ahead is best."),
        ],
        rules=[_rule("Booking first", "Offer to book an appointment before taking a message.")],
        required_fields=["name", "phone", "service", "preferred time"],
        suggested_tasks=["book", "faqs", "messages"],
    ),
    VerticalPlaybook(
        id="hospitality",
        name="Restaurants, pubs & venues",
        tagline="Bookings, opening hours, menus, events",
        greeting=(
            "Hi, thanks for calling {business_name}. Is it a table booking or something else "
            "I can help with?"
        ),
        faqs=[
            _faq(
                "Do you cater for allergies?",
                "Yes — tell us when booking and the kitchen will advise.",
            ),
            _faq(
                "Is there parking?",
                "Please check our website for parking details, or I can send them by text.",
            ),
        ],
        rules=[
            _rule(
                "Large parties",
                "For parties of 8 or more, take details and transfer or promise a call back.",
            )
        ],
        required_fields=["name", "phone", "party size", "date and time"],
        suggested_tasks=["book", "faqs", "info"],
    ),
    VerticalPlaybook(
        id="professional",
        name="Professional services",
        tagline="Accountants, consultants, agencies, IT",
        greeting="Good day, you're through to {business_name}. How can I help?",
        faqs=[
            _faq(
                "Do you offer a free consultation?",
                "Yes — I can take your details and arrange one.",
            )
        ],
        rules=[
            _rule(
                "Existing clients",
                "Ask whether the caller is an existing client and note their account manager "
                "if given.",
            )
        ],
        required_fields=["name", "company", "phone", "email", "reason for call"],
        suggested_tasks=["transfer", "messages", "qualify", "faqs"],
    ),
    VerticalPlaybook(
        id="dental",
        name="Dental & healthcare practices",
        tagline="Appointments, emergencies, new-patient enquiries",
        greeting=(
            "Hello, thanks for calling {business_name}. Is this about an appointment, or is "
            "it urgent?"
        ),
        faqs=[
            _faq(
                "Are you taking new patients?",
                "Please leave your details and the practice will confirm availability.",
            ),
            _faq(
                "What do I do in a dental emergency?",
                "If you're in severe pain or bleeding, say 'emergency' and I'll prioritise you.",
            ),
        ],
        rules=[
            _rule(
                "No clinical advice",
                "Never give clinical or medical advice; take details for the clinical team.",
            ),
            _rule("Urgent", "Severe pain, swelling or bleeding is urgent — escalate immediately."),
        ],
        required_fields=["name", "date of birth", "phone", "reason"],
        suggested_tasks=["book", "messages", "transfer"],
    ),
    VerticalPlaybook(
        id="legal",
        name="Legal practices",
        tagline="Solicitors, conveyancing, family, wills",
        greeting=(
            "Good day, {business_name}. May I take your name and the matter you're calling about?"
        ),
        faqs=[
            _faq(
                "Do you offer fixed fees?",
                "Fees depend on the matter — a solicitor will explain before any work starts.",
            )
        ],
        rules=[
            _rule(
                "No legal advice",
                "Never give legal advice or opinions; capture the matter type and arrange "
                "a call back.",
            ),
            _rule("Confidentiality", "Do not disclose whether someone is a client."),
        ],
        required_fields=["name", "phone", "email", "matter type"],
        suggested_tasks=["messages", "transfer", "qualify"],
    ),
    VerticalPlaybook(
        id="property",
        name="Estate & letting agents",
        tagline="Viewings, valuations, maintenance",
        greeting=(
            "Hi, thanks for calling {business_name}. Are you calling about buying, selling, "
            "renting or a repair?"
        ),
        faqs=[
            _faq(
                "How do I report a repair?",
                "Tell me the property address and the problem and I'll log it for the "
                "property manager.",
            )
        ],
        rules=[
            _rule(
                "Repairs",
                "Log repairs as tickets with the address; treat gas smells, floods or "
                "no heating as urgent.",
            )
        ],
        required_fields=["name", "phone", "property address", "reason"],
        suggested_tasks=["book", "messages", "transfer", "faqs"],
    ),
    VerticalPlaybook(
        id="general",
        name="Other business",
        tagline="A sensible default for any small business",
        greeting="Hi, thanks for calling {business_name}. How can I help you today?",
        faqs=[],
        rules=[],
        required_fields=["name", "phone", "reason for call"],
        suggested_tasks=["faqs", "messages"],
    ),
]
VERTICAL_BY_ID = {v.id: v for v in VERTICALS}


def apply_playbook(cfg: AssistantConfig, vertical: Vertical) -> AssistantConfig:
    pb = VERTICAL_BY_ID[vertical]
    seen = {f.question.lower() for f in cfg.faqs}
    faqs = cfg.faqs + [f for f in pb.faqs if f.question.lower() not in seen]
    upd: dict[str, Any] = {"faqs": faqs, "rules": cfg.rules + pb.rules}
    if cfg.greeting == AssistantConfig.model_fields["greeting"].default:
        upd["greeting"] = pb.greeting
    return cfg.model_copy(update=upd)


# -- setup checklist -----------------------------------------------------------------------------


class ChecklistItem(BaseModel):
    key: str
    title: str
    detail: str
    done: bool
    href: str
    optional: bool = False


class SetupChecklist(BaseModel):
    tenant_id: str
    items: list[ChecklistItem]
    completed: int
    total: int
    live: bool  # a real call has been handled
    trial_ends_at: datetime | None
    plan_id: str
    next_step: ChecklistItem | None


async def setup_checklist(
    tenant_id: str,
    store: CallStore,
    billing: BillingService,
    sip: SipService,
    calendar: CalendarService,
    notifications: NotificationService,
) -> SetupChecklist:
    cfgs = await store.list_assistants(tenant_id)
    cfg = cfgs[0] if cfgs else None
    calls = await store.list_calls(tenant_id, limit=20)
    real = [c for c in calls if c.answered_at is not None]
    synthetic = await store.list_docs("synthetic_run", tenant_id, limit=1)
    sub = await billing.subscription(tenant_id)
    trunks = await sip.trunks(tenant_id)
    numbers = await billing.list_numbers(tenant_id)
    members = await store.list_members(tenant_id)
    rules = await notifications.rules(tenant_id)
    cal = await calendar.connections(tenant_id)
    has_route = bool(trunks) or bool(numbers)
    items = [
        ChecklistItem(
            key="assistant",
            title="Assistant published",
            detail="Business details, opening hours and FAQs are set",
            done=cfg is not None and (bool(cfg.faqs) or bool(cfg.business.description)),
            href="/assistant",
        ),
        ChecklistItem(
            key="number",
            title="Phone number connected",
            detail="Get a ParlioTec number, forward your line to it, or connect your PBX",
            done=has_route,
            href="/telephony",
        ),
        ChecklistItem(
            key="test_call",
            title="Make a test call",
            detail="Run a simulated test call now, then ring the number once it is connected",
            done=bool(real) or bool(synthetic),
            href="/setup#test",
        ),
        ChecklistItem(
            key="alerts",
            title="Alerts to your team",
            detail="Email, SMS or Slack when a message, urgent call or lead comes in",
            done=bool(rules),
            href="/integrations",
        ),
        ChecklistItem(
            key="calendar",
            title="Calendar connected",
            detail="Google / Microsoft calendar or a booking link so callers can book",
            done=bool(cal),
            href="/integrations",
            optional=True,
        ),
        ChecklistItem(
            key="team",
            title="Team invited",
            detail="Colleagues can see calls, tickets and the inbox",
            done=len(members) > 1,
            href="/team",
            optional=True,
        ),
        ChecklistItem(
            key="billing",
            title="Plan confirmed",
            detail="Add payment details before the trial ends so calls keep being answered",
            done=sub.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE)
            and sub.customer_ref is not None,
            href="/billing",
        ),
    ]
    required = [i for i in items if not i.optional]
    return SetupChecklist(
        tenant_id=tenant_id,
        items=items,
        completed=sum(1 for i in required if i.done),
        total=len(required),
        live=bool(real),
        trial_ends_at=sub.trial_ends_at,
        plan_id=sub.plan_id,
        next_step=next((i for i in items if not i.done), None),
    )


# -- explainability ------------------------------------------------------------------------------


class Evidence(BaseModel):
    kind: Literal["faq", "rule", "business", "hours", "greeting", "instructions", "none"]
    label: str
    text: str
    score: float


class ExplainedTurn(BaseModel):
    index: int
    text: str
    evidence: list[Evidence]


class CallExplanation(BaseModel):
    call_id: str
    assistant_id: str
    assistant_version: int | None
    turns: list[ExplainedTurn]
    note: str


_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "is",
    "are",
    "you",
    "your",
    "we",
    "our",
    "i",
    "it",
    "for",
    "on",
    "in",
    "at",
    "with",
    "can",
    "do",
    "be",
    "that",
    "this",
    "please",
    "thanks",
    "thank",
    "how",
    "what",
    "help",
    "yes",
    "no",
    "ok",
    "okay",
    "sure",
    "just",
    "so",
    "if",
    "me",
    "my",
}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", s.lower()) if w not in _STOP and len(w) > 2}


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def explain_call(call: CallRecord, cfg: AssistantConfig | None) -> CallExplanation:
    turns: list[ExplainedTurn] = []
    if cfg is None:
        return CallExplanation(
            call_id=call.call_id,
            assistant_id=call.assistant_id,
            assistant_version=None,
            turns=[],
            note="Assistant configuration no longer available for this call.",
        )
    candidates: list[tuple[Evidence, set[str]]] = []
    for f in cfg.faqs:
        if f.enabled:
            ev = Evidence(kind="faq", label=f"FAQ: {f.question}", text=f.answer, score=0)
            candidates.append((ev, _words(f.question + " " + f.answer)))
    for r in cfg.rules:
        if r.enabled:
            ev = Evidence(kind="rule", label=f"Rule: {r.name}", text=r.instruction, score=0)
            candidates.append((ev, _words(r.instruction)))
    b = cfg.business
    facts = [
        ("Business description", b.description),
        ("Address", b.address or ""),
        ("Services", ", ".join(b.services)),
        ("Email", b.email or ""),
        ("Website", b.website or ""),
    ]
    for label, text in facts:
        if text:
            candidates.append(
                (Evidence(kind="business", label=label, text=text, score=0), _words(text))
            )
    hours = [s for s in cfg.knowledge_sections() if s.startswith("Opening hours")]
    for h in hours:
        candidates.append(
            (
                Evidence(kind="hours", label="Opening hours", text=h, score=0),
                _words(h) | {"open", "close", "closed", "hours", "opening"},
            )
        )
    greeting = cfg.rendered_greeting()
    prior_user = ""
    for i, t in enumerate(call.transcript):
        if t.get("role") != "assistant":
            prior_user = str(t.get("text") or "")
            continue
        text = str(t.get("text") or "")
        words = _words(text) | _words(prior_user)
        if i == 0 or _overlap(_words(text), _words(greeting)) > 0.5:
            turns.append(
                ExplainedTurn(
                    index=i,
                    text=text,
                    evidence=[
                        Evidence(kind="greeting", label="Greeting", text=greeting, score=1.0)
                    ],
                )
            )
            continue
        scored = sorted(
            (
                ev.model_copy(update={"score": round(_overlap(words, cw), 2)})
                for ev, cw in candidates
            ),
            key=lambda e: e.score,
            reverse=True,
        )
        top = [e for e in scored[:3] if e.score >= 0.08]
        if not top:
            top = [
                Evidence(
                    kind="instructions",
                    label="General instructions",
                    text=cfg.rendered_instructions()[:280],
                    score=0,
                )
            ]
        turns.append(ExplainedTurn(index=i, text=text, evidence=top))
    return CallExplanation(
        call_id=call.call_id,
        assistant_id=cfg.assistant_id,
        assistant_version=cfg.assistant_version,
        turns=turns,
        note="Evidence is the FAQ, rule or business fact from the assistant version live during "
        "the "
        "call whose wording best matches each reply. Add an FAQ or rule from Quality → Insights if "
        "a reply had no good source.",
    )


# -- trust centre --------------------------------------------------------------------------------


class TrustSection(BaseModel):
    id: str
    title: str
    body: str


class TrustCentre(BaseModel):
    updated_at: datetime
    data_residency: str
    sub_processors: list[dict[str, str]]
    sections: list[TrustSection]
    status_url: str = "/status"


def trust_centre(dashboard_url: str) -> TrustCentre:
    subs = [SUB_PROCESSORS[k] for k in SUB_PROCESSORS]
    sections = [
        TrustSection(
            id="residency",
            title="Data residency",
            body=(
                "Call records, transcripts, recordings and the database are hosted in the UK "
                "(DigitalOcean London). Speech, language and voice vendors are listed below with "
                "their processing region; UK-region and self-hosted profiles are available for "
                "customers who require no data to leave the UK."
            ),
        ),
        TrustSection(
            id="dpa",
            title="Data processing agreement",
            body=(
                "ParlioTec acts as processor for our customers (controllers). Our DPA incorporates "
                "the UK GDPR Article 28 terms and the UK Addendum to the EU SCCs for any "
                "transfer to sub-processors outside the UK. Request a signed copy from support."
            ),
        ),
        TrustSection(
            id="consent",
            title="Call recording & consent",
            body=(
                "Assistants announce recording at the start of every call where recording is "
                "enabled. Recording, transcription and retention are configurable per assistant; "
                "default retention is 90 days with automatic purge and PII redaction options."
            ),
        ),
        TrustSection(
            id="security",
            title="Security",
            body=(
                "TLS everywhere, encrypted credential vault for PBX/SIP secrets, per-tenant "
                "row-level isolation in PostgreSQL, TOTP two-factor authentication with "
                "per-tenant enforcement, SSO/SCIM for Enterprise, audit log of every staff and "
                "admin action, dependency and container scanning in CI."
            ),
        ),
        TrustSection(
            id="incident",
            title="Incident response & breach notification",
            body=(
                "24x7 on-call for P1 incidents with a 15-minute acknowledgement target, public "
                "status page updates within 30 minutes, root-cause analysis within 48 hours for "
                "Enterprise customers. Personal-data breaches are notified to affected customers "
                "without undue delay and within 72 hours as required by UK GDPR."
            ),
        ),
        TrustSection(
            id="telephony",
            title="Telephony demarcation",
            body=(
                "ParlioTec is responsible for the SIP edge, media and AI platform. Customers "
                "remain "
                "responsible for their own phone line, call forwarding and PBX configuration; "
                "our Health page classifies faults as customer, carrier or ParlioTec with an "
                "evidence pack to share with your provider."
            ),
        ),
        TrustSection(
            id="rights",
            title="Data subject rights",
            body=(
                "Export and erasure of a caller's data is available in-product (Compliance → "
                "GDPR) and honoured across recordings, transcripts, contacts and tickets."
            ),
        ),
        TrustSection(
            id="certs",
            title="Certifications",
            body=(
                "Cyber Essentials in progress; ISO 27001 and SOC 2 Type II on the roadmap. "
                "Sub-processors hold SOC 2 / ISO 27001 as noted in their DPAs."
            ),
        ),
    ]
    return TrustCentre(
        updated_at=datetime.now(UTC),
        data_residency="UK (London)",
        sub_processors=subs,
        sections=sections,
        status_url=f"{dashboard_url.rstrip('/')}/status",
    )


# -- onboarding check-ins ------------------------------------------------------------------------


CHECKINS: dict[int, tuple[str, str]] = {
    7: (
        "Your first week with {name}",
        "It's been a week since you set up your ParlioTec assistant. Setup is {completed}/{total} "
        "complete{next_hint}. Reply to this email or open Support in the dashboard if you'd like "
        "a config review or help with forwarding / SIP.",
    ),
    30: (
        "One month in — how is {name} doing?",
        "Your assistant has now been live for a month. Check Value in the dashboard for calls "
        "answered, leads captured and revenue attributed, and Quality for suggested FAQs from "
        "unanswered questions. Growth and Scale customers can book a free tuning session with us.",
    ),
}


class CheckInLoop:
    def __init__(
        self,
        store: CallStore,
        billing: BillingService,
        sip: SipService,
        calendar: CalendarService,
        notifications: NotificationService,
        interval_s: float = 3600,
        enrich: Callable[[str, int], Awaitable[str]] | None = None,
    ) -> None:
        self.store, self.billing, self.sip = store, billing, sip
        self.calendar, self.notifications = calendar, notifications
        self.interval_s = interval_s
        self.enrich = enrich
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while True:
            try:
                await self.sweep()
            except Exception:
                log.exception("check-in sweep failed")
            await asyncio.sleep(self.interval_s)

    async def sweep(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(UTC)
        sent: list[str] = []
        for doc in await self.store.list_docs(QUESTIONNAIRE_KIND, limit=10000):
            raw = doc.data.get("signed_up_at")
            start = datetime.fromisoformat(raw) if isinstance(raw, str) else doc.created_at
            age = (now - start).days
            for day, (subject, body) in CHECKINS.items():
                if age < day:
                    continue
                key = f"{doc.tenant_id}:{day}"
                if await self.store.get_doc(CHECKIN_KIND, key):
                    continue
                cl = await setup_checklist(
                    doc.tenant_id,
                    self.store,
                    self.billing,
                    self.sip,
                    self.calendar,
                    self.notifications,
                )
                cfgs = await self.store.list_assistants(doc.tenant_id)
                name = cfgs[0].name if cfgs else "your assistant"
                hint = f" — next: {cl.next_step.title.lower()}" if cl.next_step else ""
                text = body.format(
                    name=name, completed=cl.completed, total=cl.total, next_hint=hint
                )
                if self.enrich is not None:
                    try:
                        extra = await self.enrich(doc.tenant_id, day)
                    except Exception:
                        log.exception("check-in enrichment failed")
                        extra = ""
                    if extra:
                        text = f"{extra}\n\n{text}"
                await self.notifications.dispatch(
                    NotificationEvent(
                        tenant_id=doc.tenant_id,
                        company_id=cfgs[0].company_id if cfgs else None,
                        event=NotifyEvent.OWNER_DIGEST,
                        title=subject.format(name=name),
                        body=text,
                        context={"checkin_day": day},
                    )
                )
                await self.store.put_doc(
                    TenantDoc(
                        kind=CHECKIN_KIND,
                        id=key,
                        tenant_id=doc.tenant_id,
                        data={"day": day, "sent_at": now.isoformat()},
                    )
                )
                sent.append(key)
        return sent
