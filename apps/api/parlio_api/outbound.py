"""Phase 9: compliant outbound calling & speed-to-lead.

Model
-----
* ``Lead``           - a person who asked to be contacted (web form, webhook, dashboard); has the
                        consent flag that makes a follow-up call lawful (PECR: solicited call).
* ``OutboundCall``   - one scheduled dial with a purpose (lead follow-up, ticket callback, reminder,
                        confirmation, no-show follow-up, review request). Retried within policy.
* ``OutboundPolicy`` - per-tenant rules: enabled purposes, calling window (UK-friendly default
                        08:00-20:00 Mon-Sat, no Sunday), max attempts & gaps, daily cap per number,
                        speed-to-lead target, caller ID, consent requirement.
* ``Suppression``    - tenant do-not-call list (opt-out by keyword/SMS/dashboard; blocked numbers).

Everything is stored as ``TenantDoc`` so no migration is needed; the dialer is a protocol with a
simulated implementation (tests, pending Telnyx approval) and a LiveKit implementation that
dispatches the voice worker with the job in the dispatch metadata — the worker then dials the
SIP participant itself so the AI is already in the room when the callee answers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, computed_field

from parlio_api.store import CallRecord, CallStore, TenantDoc, Ticket
from parlio_voice.models import WEEKDAYS, AssistantConfig, TicketIntake, TicketPriority

log = logging.getLogger(__name__)

LEAD_KIND = "lead"
CALL_KIND = "outbound_call"
POLICY_KIND = "outbound_policy"
SUPPRESSION_KIND = "suppression"

# UK jurisdiction defaults. Ofcom persistent-misuse guidance / TPS practice: no unsolicited
# calls before 08:00 or after 21:00; we default tighter (20:00) and skip Sundays. Marketing
# calls additionally require TPS screening; Parlio only places *solicited* calls (lead asked to
# be called, existing customer relationship) so TPS is a safety check, not a gate.
JURISDICTIONS: dict[str, dict[str, Any]] = {
    "GB": {
        "prefix": "+44",
        "timezone": "Europe/London",
        "start": "08:00",
        "end": "20:00",
        "days": list(WEEKDAYS[:6]),
        "max_attempts": 3,
        "note": "Ofcom/PECR: solicited calls only",
    },
    "IE": {
        "prefix": "+353",
        "timezone": "Europe/Dublin",
        "start": "09:00",
        "end": "20:00",
        "days": list(WEEKDAYS[:6]),
        "max_attempts": 3,
        "note": "ComReg",
    },
    "US": {
        "prefix": "+1",
        "timezone": "America/New_York",
        "start": "08:00",
        "end": "21:00",
        "days": list(WEEKDAYS),
        "max_attempts": 3,
        "note": "TCPA: 8am-9pm callee local time",
    },
}


class Purpose(StrEnum):
    LEAD_FOLLOWUP = "lead_followup"
    TICKET_CALLBACK = "ticket_callback"
    REMINDER = "reminder"
    CONFIRMATION = "confirmation"
    NO_SHOW = "no_show"
    REVIEW_REQUEST = "review_request"


class OutboundStatus(StrEnum):
    SCHEDULED = "scheduled"
    DIALING = "dialing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    RETRY = "retry"  # waiting for next attempt
    EXHAUSTED = "exhausted"  # attempts used up without contact
    SUPPRESSED = "suppressed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Outcome(StrEnum):
    """What the AI achieved on an answered call (set by the worker's record_outcome tool)."""

    QUALIFIED = "qualified"
    BOOKED = "booked"
    TICKETED = "ticketed"
    CONFIRMED = "confirmed"
    RESCHEDULED = "rescheduled"
    NOT_INTERESTED = "not_interested"
    CALLBACK_LATER = "callback_later"
    WRONG_NUMBER = "wrong_number"
    OPT_OUT = "opt_out"
    VOICEMAIL = "voicemail"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    FAILED = "failed"
    UNKNOWN = "unknown"


NO_CONTACT = {Outcome.VOICEMAIL, Outcome.NO_ANSWER, Outcome.BUSY, Outcome.FAILED}
TERMINAL_CONTACT = {
    Outcome.QUALIFIED,
    Outcome.BOOKED,
    Outcome.TICKETED,
    Outcome.CONFIRMED,
    Outcome.RESCHEDULED,
    Outcome.NOT_INTERESTED,
    Outcome.WRONG_NUMBER,
    Outcome.OPT_OUT,
}


class LeadStatus(StrEnum):
    NEW = "new"
    CALLING = "calling"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    BOOKED = "booked"
    TICKETED = "ticketed"
    LOST = "lost"
    OPTED_OUT = "opted_out"
    UNREACHABLE = "unreachable"


class Lead(BaseModel):
    id: str = Field(default_factory=lambda: f"ld-{uuid4().hex[:8]}")
    tenant_id: str
    company_id: str
    name: str = ""
    phone: str
    email: str | None = None
    source: str = "web_form"  # web_form | webhook | dashboard | api | missed_call
    interest: str | None = None  # what they enquired about
    notes: str | None = None
    consent: bool = True  # they asked to be called back
    consent_text: str | None = None  # the checkbox/legal text they agreed to
    status: LeadStatus = LeadStatus.NEW
    outbound_call_ids: list[str] = Field(default_factory=list)
    contact_id: str | None = None
    custom: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    first_call_at: datetime | None = None  # speed-to-lead measurement

    @computed_field  # type: ignore[prop-decorator]
    @property
    def speed_to_lead_s(self) -> float | None:
        if self.first_call_at is None:
            return None
        return (self.first_call_at - self.created_at).total_seconds()

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=LEAD_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class Attempt(BaseModel):
    n: int
    at: datetime
    call_id: str | None = None
    outcome: Outcome = Outcome.UNKNOWN
    detail: str | None = None


class OutboundCall(BaseModel):
    id: str = Field(default_factory=lambda: f"ob-{uuid4().hex[:8]}")
    tenant_id: str
    company_id: str
    assistant_id: str
    purpose: Purpose
    to: str
    from_number: str | None = None
    name: str | None = None
    lead_id: str | None = None
    ticket_id: str | None = None
    booking_id: str | None = None
    context: dict[str, str] = Field(default_factory=dict)  # script variables
    scheduled_at: datetime
    not_after: datetime | None = None  # e.g. reminder is pointless after the appointment
    status: OutboundStatus = OutboundStatus.SCHEDULED
    attempts: list[Attempt] = Field(default_factory=list)
    max_attempts: int = 3
    call_id: str | None = None  # live/last call record
    outcome: Outcome | None = None
    outcome_detail: str | None = None
    reason: str | None = None  # why suppressed/cancelled/failed
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=CALL_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class Suppression(BaseModel):
    id: str
    tenant_id: str
    phone: str
    reason: str = "opt_out"  # opt_out | blocked | wrong_number | complaint | tps
    source: str = "dashboard"  # dashboard | call | sms | api
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=SUPPRESSION_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


class OutboundPolicy(BaseModel):
    tenant_id: str
    enabled: bool = True
    purposes: list[Purpose] = Field(
        default_factory=lambda: [
            Purpose.LEAD_FOLLOWUP,
            Purpose.TICKET_CALLBACK,
            Purpose.REMINDER,
            Purpose.CONFIRMATION,
            Purpose.NO_SHOW,
        ]
    )
    jurisdiction: str = "GB"
    timezone: str = "Europe/London"
    window_start: time = time(8, 0)
    window_end: time = time(20, 0)
    days: list[str] = Field(default_factory=lambda: list(WEEKDAYS[:6]))
    max_attempts: int = 3
    retry_gap_min: int = 90
    daily_cap_per_number: int = 2
    speed_to_lead_target_s: int = 60
    require_consent: bool = True
    caller_id: str | None = None  # E.164 presented to the callee
    form_token: str = Field(default_factory=lambda: f"lf_{uuid4().hex}")  # public web-form auth
    reminder_hours_before: int = 24
    review_request_delay_h: int = 3
    leave_voicemail: bool = True
    opt_out_keywords: list[str] = Field(
        default_factory=lambda: [
            "stop calling",
            "do not call",
            "don't call",
            "remove my number",
            "unsubscribe",
            "take me off",
        ]
    )
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=POLICY_KIND,
            id=self.tenant_id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            updated_at=self.updated_at,
        )

    def allows(self, purpose: Purpose) -> bool:
        return self.enabled and purpose in self.purposes

    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def in_window(self, at: datetime) -> bool:
        local = at.astimezone(ZoneInfo(self.timezone))
        day = WEEKDAYS[local.weekday()]
        return day in self.days and self.window_start <= local.time() < self.window_end

    def next_window(self, at: datetime) -> datetime:
        """Earliest instant >= `at` inside the calling window (`at` itself if already inside)."""
        if self.in_window(at):
            return at
        tz = ZoneInfo(self.timezone)
        local = at.astimezone(tz)
        for offset in range(0, 8):
            day = local.date() + timedelta(days=offset)
            if WEEKDAYS[day.weekday()] not in self.days:
                continue
            start = datetime.combine(day, self.window_start, tzinfo=tz)
            if start >= local:
                return start.astimezone(UTC)
        raise ValueError("calling window has no allowed days")


def normalise_phone(raw: str, default_cc: str = "+44") -> str:
    digits = re.sub(r"[^\d+]", "", raw.strip())
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("0") and not digits.startswith("+"):
        digits = default_cc + digits[1:]
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits


def jurisdiction_for(number: str) -> str | None:
    for code, j in JURISDICTIONS.items():
        if number.startswith(str(j["prefix"])):
            return code
    return None


# -- scripts ------------------------------------------------------------------------------------


def build_script(job: OutboundCall, cfg: AssistantConfig) -> dict[str, str]:
    """Opening line + goal for the worker. Always identifies the business and offers opt-out."""
    biz = cfg.business_name
    who = f", is that {job.name}?" if job.name else "."
    c = job.context
    common = (
        f"This is an OUTBOUND call you placed on behalf of {biz}. Confirm you are speaking to the "
        f"right person before discussing details. If they say it is a bad time, offer to call back "
        f"and use record_outcome('callback_later'). If they ask not to be called again, apologise, "
        f"confirm you will remove their number and call record_outcome('opt_out'). If you reach "
        f"voicemail or an answering service, leave a short message with the business name and "
        f"call record_outcome('voicemail'). Keep it brief and courteous; end the call politely "
        f"when done."
    )
    match job.purpose:
        case Purpose.LEAD_FOLLOWUP:
            opening = (
                f"Hi{who} It's {cfg.name} calling from {biz} — you asked us to get in touch"
                + (f" about {c['interest']}" if c.get("interest") else "")
                + ". Is now a good time?"
            )
            goal = (
                "Goal: qualify the lead — understand what they need, when, and any budget or "
                "location details; answer questions from the FAQs. If suitable, book an "
                "appointment "
                "(check_calendar / book_appointment) and call record_outcome('booked'); otherwise "
                "raise a ticket for the team (create_ticket) and call record_outcome('ticketed'), "
                "or "
                "record_outcome('qualified'/'not_interested') as appropriate."
            )
        case Purpose.TICKET_CALLBACK:
            opening = (
                f"Hi{who} It's {cfg.name} from {biz}, returning your call"
                + (f" about {c['reason']}" if c.get("reason") else "")
                + ". Is now a good time?"
            )
            goal = (
                "Goal: resolve or progress their enquiry using the FAQs and business rules; if a "
                "human is needed, transfer_to_human, otherwise update the ticket via create_ticket "
                "with the new information. Finish with record_outcome('ticketed' or 'qualified')."
            )
        case Purpose.REMINDER:
            opening = (
                f"Hi{who} It's {cfg.name} from {biz} with a quick reminder of your appointment "
                f"{c.get('when', 'soon')}. Does that still work for you?"
            )
            goal = (
                "Goal: confirm attendance (record_outcome('confirmed')) or reschedule using "
                "check_calendar/book_appointment (record_outcome('rescheduled'))."
            )
        case Purpose.CONFIRMATION:
            opening = (
                f"Hi{who} It's {cfg.name} from {biz}. I'm calling to confirm your booking "
                f"{c.get('when', '')}. Can you confirm that's right?"
            )
            goal = (
                "Goal: confirm details (record_outcome('confirmed')) or amend "
                "(record_outcome('rescheduled'))."
            )
        case Purpose.NO_SHOW:
            opening = (
                f"Hi{who} It's {cfg.name} from {biz}. We missed you at your appointment "
                f"{c.get('when', 'earlier')} — I hope everything's okay. Would you like to rebook?"
            )
            goal = (
                "Goal: be understanding, offer to rebook (check_calendar/book_appointment, "
                "record_outcome('rescheduled')) or record_outcome('not_interested')."
            )
        case Purpose.REVIEW_REQUEST:
            opening = (
                f"Hi{who} It's {cfg.name} from {biz}. Thanks for visiting us "
                f"{c.get('when', 'recently')}. "
                f"I just wanted to check everything went well?"
            )
            goal = (
                "Goal: ask for feedback; if positive, offer to text a review link (send_sms) and "
                "record_outcome('confirmed'); if negative, apologise, create_ticket for a manager "
                "and record_outcome('ticketed')."
            )
    return {"opening": opening, "instructions": f"{common}\n\n{goal}"}


# -- dialer -------------------------------------------------------------------------------------


class Dialer(Protocol):
    name: str

    async def dial(self, job: OutboundCall, cfg: AssistantConfig, script: dict[str, str]) -> str:
        """Start the call; returns the call_id the worker will report events under."""
        ...


class SimulatedDialer:
    """Records dials; tests drive outcomes through `OutboundService.on_call_ended`."""

    name = "simulated"

    def __init__(self) -> None:
        self.dials: list[tuple[OutboundCall, dict[str, str]]] = []

    async def dial(self, job: OutboundCall, cfg: AssistantConfig, script: dict[str, str]) -> str:
        self.dials.append((job, script))
        return f"sim-{job.id}-{len(job.attempts) + 1}"


class LiveKitDialer:
    """Dispatch the voice worker into a fresh room with the job as metadata; the worker dials."""

    name = "livekit"

    def __init__(self, trunk_id: str, *, agent_name: str = "parlio-voice") -> None:
        from livekit import api

        self._lk = api.LiveKitAPI()
        self._api = api
        self._trunk_id = trunk_id
        self._agent = agent_name

    async def dial(self, job: OutboundCall, cfg: AssistantConfig, script: dict[str, str]) -> str:
        call_id = f"out-{job.id}-{len(job.attempts) + 1}"
        meta = {
            "outbound": {
                "call_id": call_id,
                "job_id": job.id,
                "tenant_id": job.tenant_id,
                "assistant_id": job.assistant_id,
                "purpose": job.purpose,
                "to": job.to,
                "from": job.from_number,
                "name": job.name,
                "trunk_id": self._trunk_id,
                "script": script,
                "context": job.context,
            }
        }
        await self._lk.agent_dispatch.create_dispatch(
            self._api.CreateAgentDispatchRequest(
                agent_name=self._agent, room=call_id, metadata=json.dumps(meta)
            )
        )
        return call_id


# -- service ------------------------------------------------------------------------------------


class OutboundService:
    def __init__(
        self,
        store: CallStore,
        dialer: Dialer,
        *,
        default_caller_id: str | None = None,
        on_ticket: Callable[[str, str, TicketIntake], Awaitable[Ticket]] | None = None,
    ) -> None:
        self.store = store
        self.dialer = dialer
        self.default_caller_id = default_caller_id
        self.on_ticket = on_ticket

    # policy
    async def policy(self, tenant_id: str) -> OutboundPolicy:
        d = await self.store.get_doc(POLICY_KIND, tenant_id)
        return OutboundPolicy.model_validate(d.data) if d else OutboundPolicy(tenant_id=tenant_id)

    async def save_policy(self, p: OutboundPolicy) -> OutboundPolicy:
        p.updated_at = datetime.now(UTC)
        await self.store.put_doc(p.to_doc())
        return p

    # suppression
    @staticmethod
    def _sid(tenant_id: str, phone: str) -> str:
        return f"{tenant_id}:{normalise_phone(phone)}"

    async def suppress(
        self, tenant_id: str, phone: str, reason: str = "opt_out", source: str = "dashboard"
    ) -> Suppression:
        phone = normalise_phone(phone)
        s = Suppression(
            id=self._sid(tenant_id, phone),
            tenant_id=tenant_id,
            phone=phone,
            reason=reason,
            source=source,
        )
        await self.store.put_doc(s.to_doc())
        for job in await self.list_calls(tenant_id):
            if job.to == phone and job.status in (OutboundStatus.SCHEDULED, OutboundStatus.RETRY):
                job.status, job.reason = OutboundStatus.SUPPRESSED, f"opted out ({reason})"
                await self._save(job)
        for lead in await self.leads(tenant_id):
            if lead.phone == phone and lead.status not in (LeadStatus.BOOKED, LeadStatus.TICKETED):
                lead.status = LeadStatus.OPTED_OUT
                await self._save_lead(lead)
        return s

    async def unsuppress(self, tenant_id: str, phone: str) -> bool:
        return await self.store.delete_doc(SUPPRESSION_KIND, self._sid(tenant_id, phone))

    async def is_suppressed(self, tenant_id: str, phone: str) -> bool:
        return await self.store.get_doc(SUPPRESSION_KIND, self._sid(tenant_id, phone)) is not None

    async def suppressions(self, tenant_id: str) -> list[Suppression]:
        docs = await self.store.list_docs(SUPPRESSION_KIND, tenant_id, limit=5000)
        return sorted(
            (Suppression.model_validate(d.data) for d in docs), key=lambda s: s.created_at
        )

    # leads
    async def leads(self, tenant_id: str, limit: int = 500) -> list[Lead]:
        docs = await self.store.list_docs(LEAD_KIND, tenant_id, limit=limit)
        return sorted(
            (Lead.model_validate(d.data) for d in docs), key=lambda x: x.created_at, reverse=True
        )

    async def get_lead(self, tenant_id: str, lead_id: str) -> Lead | None:
        d = await self.store.get_doc(LEAD_KIND, lead_id)
        if d is None or d.tenant_id != tenant_id:
            return None
        return Lead.model_validate(d.data)

    async def _save_lead(self, lead: Lead) -> Lead:
        lead.updated_at = datetime.now(UTC)
        await self.store.put_doc(lead.to_doc())
        return lead

    async def capture_lead(
        self, lead: Lead, *, call_now: bool = True
    ) -> tuple[Lead, OutboundCall | None]:
        """Speed-to-lead entrypoint: store the lead and (if lawful) schedule an immediate call."""
        lead.phone = normalise_phone(lead.phone)
        lead.contact_id, _ = await self.store.touch_contact(
            lead.tenant_id, lead.company_id, lead.phone
        )
        if lead.name or lead.email:
            from parlio_api.store import ContactUpdate

            await self.store.update_contact(
                lead.contact_id, ContactUpdate(name=lead.name or None, email=lead.email)
            )
        await self._save_lead(lead)
        job: OutboundCall | None = None
        if call_now:
            cfgs = await self.store.list_assistants(lead.tenant_id)
            if cfgs:
                ctx = {
                    k: v for k, v in {"interest": lead.interest, "notes": lead.notes}.items() if v
                }
                job = await self.schedule(
                    tenant_id=lead.tenant_id,
                    assistant_id=cfgs[0].assistant_id,
                    purpose=Purpose.LEAD_FOLLOWUP,
                    to=lead.phone,
                    name=lead.name or None,
                    lead_id=lead.id,
                    context=ctx | lead.custom,
                    consent=lead.consent,
                )
                lead.outbound_call_ids.append(job.id)
                if job.status == OutboundStatus.SUPPRESSED:
                    lead.status = LeadStatus.OPTED_OUT
                await self._save_lead(lead)
        return lead, job

    # jobs
    async def list_calls(self, tenant_id: str, limit: int = 500) -> list[OutboundCall]:
        docs = await self.store.list_docs(CALL_KIND, tenant_id, limit=limit)
        return sorted(
            (OutboundCall.model_validate(d.data) for d in docs),
            key=lambda j: j.scheduled_at,
            reverse=True,
        )

    async def get(self, tenant_id: str, job_id: str) -> OutboundCall | None:
        d = await self.store.get_doc(CALL_KIND, job_id)
        if d is None or d.tenant_id != tenant_id:
            return None
        return OutboundCall.model_validate(d.data)

    async def _save(self, job: OutboundCall) -> OutboundCall:
        job.updated_at = datetime.now(UTC)
        await self.store.put_doc(job.to_doc())
        return job

    async def schedule(
        self,
        *,
        tenant_id: str,
        assistant_id: str,
        purpose: Purpose,
        to: str,
        name: str | None = None,
        when: datetime | None = None,
        not_after: datetime | None = None,
        lead_id: str | None = None,
        ticket_id: str | None = None,
        booking_id: str | None = None,
        context: dict[str, str] | None = None,
        consent: bool = True,
    ) -> OutboundCall:
        """Apply compliance gates then queue. Policy failures never raise; the job records why."""
        policy = await self.policy(tenant_id)
        cfg = await self.store.get_assistant(assistant_id)
        if cfg is None:
            raise LookupError(f"assistant {assistant_id} not found")
        to = normalise_phone(to)
        now = datetime.now(UTC)
        job = OutboundCall(
            tenant_id=tenant_id,
            company_id=cfg.company_id,
            assistant_id=assistant_id,
            purpose=purpose,
            to=to,
            from_number=policy.caller_id or self.default_caller_id,
            name=name,
            lead_id=lead_id,
            ticket_id=ticket_id,
            booking_id=booking_id,
            context=context or {},
            scheduled_at=when or now,
            not_after=not_after,
            max_attempts=policy.max_attempts,
        )
        if not policy.allows(purpose):
            job.status, job.reason = (
                OutboundStatus.CANCELLED,
                f"{purpose} disabled in outbound policy",
            )
        elif await self.is_suppressed(tenant_id, to) or cfg.is_blocked(to):
            job.status, job.reason = OutboundStatus.SUPPRESSED, "number on do-not-call list"
        elif policy.require_consent and not consent and purpose == Purpose.LEAD_FOLLOWUP:
            job.status, job.reason = OutboundStatus.CANCELLED, "no consent to call recorded"
        elif jurisdiction_for(to) is None:
            job.status, job.reason = (
                OutboundStatus.CANCELLED,
                "destination country not enabled for outbound",
            )
        else:
            job.scheduled_at = policy.next_window(job.scheduled_at)
            if job.not_after and job.scheduled_at > job.not_after:
                job.status, job.reason = (
                    OutboundStatus.CANCELLED,
                    "no calling window before deadline",
                )
        return await self._save(job)

    async def cancel(
        self, tenant_id: str, job_id: str, reason: str = "cancelled by user"
    ) -> OutboundCall | None:
        job = await self.get(tenant_id, job_id)
        if job is None:
            return None
        if job.status in (OutboundStatus.SCHEDULED, OutboundStatus.RETRY):
            job.status, job.reason = OutboundStatus.CANCELLED, reason
            await self._save(job)
        return job

    async def _daily_count(self, tenant_id: str, to: str, now: datetime) -> int:
        cutoff = now - timedelta(hours=24)
        return sum(
            1
            for j in await self.list_calls(tenant_id)
            if j.to == to
            for a in j.attempts
            if a.at >= cutoff
        )

    async def due(
        self, now: datetime | None = None, tenant_id: str | None = None
    ) -> list[OutboundCall]:
        now = now or datetime.now(UTC)
        docs = await self.store.list_docs(CALL_KIND, tenant_id, limit=5000)
        jobs = [OutboundCall.model_validate(d.data) for d in docs]
        return sorted(
            (
                j
                for j in jobs
                if j.status in (OutboundStatus.SCHEDULED, OutboundStatus.RETRY)
                and j.scheduled_at <= now
            ),
            key=lambda j: j.scheduled_at,
        )

    async def dispatch(self, job: OutboundCall, now: datetime | None = None) -> OutboundCall:
        """Final gates at dial time (window, cap, suppression, deadline) then hand to the dialer."""
        now = now or datetime.now(UTC)
        policy = await self.policy(job.tenant_id)
        if job.not_after and now > job.not_after:
            job.status, job.reason = OutboundStatus.CANCELLED, "deadline passed"
            return await self._save(job)
        if await self.is_suppressed(job.tenant_id, job.to):
            job.status, job.reason = OutboundStatus.SUPPRESSED, "number on do-not-call list"
            return await self._save(job)
        if not policy.enabled:
            job.scheduled_at = now + timedelta(minutes=policy.retry_gap_min)
            return await self._save(job)
        if not policy.in_window(now):
            job.scheduled_at = policy.next_window(now)
            return await self._save(job)
        if await self._daily_count(job.tenant_id, job.to, now) >= policy.daily_cap_per_number:
            job.scheduled_at = policy.next_window(now + timedelta(hours=24))
            job.reason = "daily contact cap reached; deferred"
            return await self._save(job)
        cfg = await self.store.get_assistant(job.assistant_id)
        if cfg is None:
            job.status, job.reason = OutboundStatus.FAILED, "assistant missing"
            return await self._save(job)
        script = build_script(job, cfg)
        n = len(job.attempts) + 1
        try:
            call_id = await self.dialer.dial(job, cfg, script)
        except Exception as e:
            log.warning("outbound dial failed for %s: %s", job.id, e)
            job.attempts.append(Attempt(n=n, at=now, outcome=Outcome.FAILED, detail=str(e)[:300]))
            return await self._after_attempt(job, Outcome.FAILED, policy, now)
        job.attempts.append(Attempt(n=n, at=now, call_id=call_id))
        job.call_id, job.status, job.reason = call_id, OutboundStatus.DIALING, None
        await self._save(job)
        if job.lead_id:
            lead = await self.get_lead(job.tenant_id, job.lead_id)
            if lead is not None:
                if lead.first_call_at is None:
                    lead.first_call_at = now
                lead.status = LeadStatus.CALLING
                await self._save_lead(lead)
        return job

    async def _after_attempt(
        self, job: OutboundCall, outcome: Outcome, policy: OutboundPolicy, now: datetime
    ) -> OutboundCall:
        job.outcome = outcome
        if outcome in NO_CONTACT and len(job.attempts) < job.max_attempts:
            job.status = OutboundStatus.RETRY
            job.scheduled_at = policy.next_window(now + timedelta(minutes=policy.retry_gap_min))
            if job.not_after and job.scheduled_at > job.not_after:
                job.status, job.reason = (
                    OutboundStatus.EXHAUSTED,
                    "no further window before deadline",
                )
                job.completed_at = now
        elif outcome in NO_CONTACT:
            job.status, job.completed_at = OutboundStatus.EXHAUSTED, now
        else:
            job.status, job.completed_at = OutboundStatus.COMPLETED, now
        await self._save(job)
        await self._update_lead(job, outcome)
        if outcome == Outcome.OPT_OUT:
            await self.suppress(job.tenant_id, job.to, "opt_out", source="call")
        if outcome == Outcome.WRONG_NUMBER:
            await self.suppress(job.tenant_id, job.to, "wrong_number", source="call")
        if (
            job.status == OutboundStatus.EXHAUSTED
            and job.purpose == Purpose.LEAD_FOLLOWUP
            and self.on_ticket
        ):
            with contextlib.suppress(Exception):
                await self.on_ticket(
                    job.tenant_id,
                    job.company_id,
                    TicketIntake(
                        caller_name=job.name,
                        caller_number=job.to,
                        reason=f"Lead follow-up: {job.max_attempts} call attempts unanswered"
                        + (
                            f" — enquiry: {job.context['interest']}"
                            if job.context.get("interest")
                            else ""
                        ),
                        priority=TicketPriority.NORMAL,
                        category="lead",
                        source="outbound_unreachable",
                    ),
                )
        return job

    async def _update_lead(self, job: OutboundCall, outcome: Outcome) -> None:
        if not job.lead_id:
            return
        lead = await self.get_lead(job.tenant_id, job.lead_id)
        if lead is None:
            return
        mapping: dict[Outcome, LeadStatus] = {
            Outcome.QUALIFIED: LeadStatus.QUALIFIED,
            Outcome.BOOKED: LeadStatus.BOOKED,
            Outcome.TICKETED: LeadStatus.TICKETED,
            Outcome.NOT_INTERESTED: LeadStatus.LOST,
            Outcome.WRONG_NUMBER: LeadStatus.LOST,
            Outcome.OPT_OUT: LeadStatus.OPTED_OUT,
            Outcome.CALLBACK_LATER: LeadStatus.CONTACTED,
            Outcome.CONFIRMED: LeadStatus.CONTACTED,
            Outcome.RESCHEDULED: LeadStatus.CONTACTED,
        }
        if outcome in mapping:
            lead.status = mapping[outcome]
        elif job.status == OutboundStatus.EXHAUSTED:
            lead.status = LeadStatus.UNREACHABLE
        elif job.status == OutboundStatus.RETRY:
            lead.status = LeadStatus.NEW
        await self._save_lead(lead)

    async def on_call_started(self, call_id: str) -> None:
        job = await self._by_call(call_id)
        if job and job.status == OutboundStatus.DIALING:
            job.status = OutboundStatus.IN_PROGRESS
            await self._save(job)

    async def on_call_ended(self, call: CallRecord) -> OutboundCall | None:
        """Fold the worker's call.ended into the job: outcome, retries, lead status, suppression."""
        job = await self._by_call(call.call_id)
        if job is None or job.status not in (OutboundStatus.DIALING, OutboundStatus.IN_PROGRESS):
            return job
        recorded = job.attempts[-1].outcome if job.attempts else Outcome.UNKNOWN
        if recorded != Outcome.UNKNOWN:
            outcome: Outcome = recorded  # set mid-call via the worker outcome endpoint
        else:
            raw = call.extracted.get("outbound_outcome") or call.end_reason or ""
            outcome = _outcome_from(str(raw), answered=call.answered_at is not None)
        if not job.outcome_detail:
            job.outcome_detail = call.summary
        if job.attempts:
            job.attempts[-1].outcome = outcome
            job.attempts[-1].detail = job.outcome_detail
        if call.ticket_ids and outcome not in TERMINAL_CONTACT:
            outcome = Outcome.TICKETED
        policy = await self.policy(job.tenant_id)
        return await self._after_attempt(job, outcome, policy, call.ended_at or datetime.now(UTC))

    async def _by_call(self, call_id: str) -> OutboundCall | None:
        # call ids are "out-<job>-<n>" / "sim-<job>-<n>"; fall back to a scan for other dialers
        m = re.match(r"^(?:out|sim)-(ob-[0-9a-f]+)-\d+$", call_id)
        if m:
            d = await self.store.get_doc(CALL_KIND, m.group(1))
            return OutboundCall.model_validate(d.data) if d else None
        for d in await self.store.list_docs(CALL_KIND, None, limit=5000):
            if d.data.get("call_id") == call_id:
                return OutboundCall.model_validate(d.data)
        return None

    # automations
    async def on_ticket_created(self, ticket: Ticket) -> OutboundCall | None:
        """Ticket callbacks: when the caller asked for a call back and a window is known/ASAP."""
        if not ticket.caller_number or ticket.source in ("outbound_unreachable", "manual"):
            return None
        policy = await self.policy(ticket.tenant_id)
        if not policy.allows(Purpose.TICKET_CALLBACK) or not ticket.callback_window:
            return None
        cfgs = await self.store.list_assistants(ticket.tenant_id)
        if not cfgs:
            return None
        when = parse_callback_window(ticket.callback_window, policy)
        return await self.schedule(
            tenant_id=ticket.tenant_id,
            assistant_id=cfgs[0].assistant_id,
            purpose=Purpose.TICKET_CALLBACK,
            to=ticket.caller_number,
            name=ticket.caller_name,
            when=when,
            ticket_id=ticket.id,
            context={"reason": ticket.reason},
        )

    async def on_booking(
        self,
        *,
        tenant_id: str,
        booking_id: str,
        phone: str | None,
        name: str,
        start: datetime,
        assistant_id: str | None = None,
    ) -> list[OutboundCall]:
        """Reminder before the appointment (and a review request after, if enabled)."""
        if not phone:
            return []
        policy = await self.policy(tenant_id)
        cfgs = await self.store.list_assistants(tenant_id)
        if not cfgs:
            return []
        aid = assistant_id or cfgs[0].assistant_id
        when_txt = start.astimezone(ZoneInfo(policy.timezone)).strftime("on %A %-d %B at %H:%M")
        out: list[OutboundCall] = []
        if policy.allows(Purpose.REMINDER):
            at = start - timedelta(hours=policy.reminder_hours_before)
            if at > datetime.now(UTC):
                out.append(
                    await self.schedule(
                        tenant_id=tenant_id,
                        assistant_id=aid,
                        purpose=Purpose.REMINDER,
                        to=phone,
                        name=name,
                        when=at,
                        not_after=start - timedelta(hours=1),
                        booking_id=booking_id,
                        context={"when": when_txt},
                    )
                )
        if policy.allows(Purpose.REVIEW_REQUEST):
            out.append(
                await self.schedule(
                    tenant_id=tenant_id,
                    assistant_id=aid,
                    purpose=Purpose.REVIEW_REQUEST,
                    to=phone,
                    name=name,
                    when=start + timedelta(hours=policy.review_request_delay_h),
                    booking_id=booking_id,
                    context={"when": when_txt},
                )
            )
        return out

    async def on_no_show(
        self, *, tenant_id: str, booking_id: str, phone: str, name: str, start: datetime
    ) -> OutboundCall | None:
        policy = await self.policy(tenant_id)
        cfgs = await self.store.list_assistants(tenant_id)
        if not cfgs or not policy.allows(Purpose.NO_SHOW):
            return None
        for j in await self.list_calls(tenant_id):
            if j.booking_id == booking_id and j.status in (
                OutboundStatus.SCHEDULED,
                OutboundStatus.RETRY,
            ):
                await self.cancel(tenant_id, j.id, "booking marked no-show")
        when_txt = start.astimezone(ZoneInfo(policy.timezone)).strftime("on %A at %H:%M")
        return await self.schedule(
            tenant_id=tenant_id,
            assistant_id=cfgs[0].assistant_id,
            purpose=Purpose.NO_SHOW,
            to=phone,
            name=name,
            booking_id=booking_id,
            context={"when": when_txt},
        )

    async def check_opt_out(self, tenant_id: str, phone: str, text: str) -> bool:
        """Inbound SMS/call text opt-out ("STOP", "do not call")."""
        policy = await self.policy(tenant_id)
        t = text.strip().lower()
        if t == "stop" or any(k in t for k in policy.opt_out_keywords):
            await self.suppress(tenant_id, phone, "opt_out", source="sms")
            return True
        return False

    async def stats(self, tenant_id: str) -> dict[str, Any]:
        jobs = await self.list_calls(tenant_id, limit=5000)
        leads = await self.leads(tenant_id, limit=5000)
        s2l = [x for x in (ld.speed_to_lead_s for ld in leads) if x is not None]
        policy = await self.policy(tenant_id)
        by_status: dict[str, int] = {}
        by_purpose: dict[str, int] = {}
        by_outcome: dict[str, int] = {}
        for j in jobs:
            by_status[j.status] = by_status.get(j.status, 0) + 1
            by_purpose[j.purpose] = by_purpose.get(j.purpose, 0) + 1
            if j.outcome:
                by_outcome[j.outcome] = by_outcome.get(j.outcome, 0) + 1
        contacted = sum(1 for j in jobs if j.outcome in TERMINAL_CONTACT)
        attempted = sum(1 for j in jobs if j.attempts)
        return {
            "jobs": len(jobs),
            "leads": len(leads),
            "queued": by_status.get(OutboundStatus.SCHEDULED, 0)
            + by_status.get(OutboundStatus.RETRY, 0),
            "contact_rate": round(contacted / attempted, 3) if attempted else None,
            "speed_to_lead_median_s": sorted(s2l)[len(s2l) // 2] if s2l else None,
            "speed_to_lead_within_target": (
                round(sum(1 for x in s2l if x <= policy.speed_to_lead_target_s) / len(s2l), 3)
                if s2l
                else None
            ),
            "leads_by_status": {
                k: sum(1 for ld in leads if ld.status == k)
                for k in LeadStatus
                if any(ld.status == k for ld in leads)
            },
            "by_status": by_status,
            "by_purpose": by_purpose,
            "by_outcome": by_outcome,
            "suppressed": len(await self.suppressions(tenant_id)),
        }


def _outcome_from(raw: str, *, answered: bool) -> Outcome:
    try:
        return Outcome(raw)
    except ValueError:
        pass
    r = raw.lower()
    if "voicemail" in r or "machine" in r:
        return Outcome.VOICEMAIL
    if "busy" in r:
        return Outcome.BUSY
    if "no_answer" in r or "no answer" in r or "timeout" in r or "declined" in r:
        return Outcome.NO_ANSWER
    if "fail" in r or "error" in r:
        return Outcome.FAILED
    return Outcome.UNKNOWN if answered else Outcome.NO_ANSWER


def parse_callback_window(
    text: str, policy: OutboundPolicy, now: datetime | None = None
) -> datetime:
    """'asap' / 'this afternoon' / 'tomorrow morning' / 'after 5pm' -> UTC instant (best effort)."""
    now = now or datetime.now(UTC)
    tz = ZoneInfo(policy.timezone)
    local = now.astimezone(tz)
    t = text.lower()
    day = local.date()
    if "tomorrow" in t:
        day += timedelta(days=1)
    hour: int | None = None
    if "morning" in t:
        hour = 9
    elif "lunch" in t:
        hour = 12
    elif "afternoon" in t:
        hour = 14
    elif "evening" in t or "after work" in t:
        hour = 17
    m = re.search(r"after\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    if m:
        hour = int(m.group(1)) + (12 if m.group(3) == "pm" and int(m.group(1)) < 12 else 0)
    if hour is None:
        return now
    target = datetime.combine(day, time(hour, 0), tzinfo=tz)
    if target < local and "tomorrow" not in t:
        target += timedelta(days=1)
    return target.astimezone(UTC)


class OutboundLoop:
    """Ticks every few seconds so a fresh lead is dialled well inside the 60s target."""

    def __init__(self, svc: OutboundService, interval_s: float = 5.0) -> None:
        self.svc, self.interval_s = svc, interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def tick(self) -> int:
        n = 0
        for job in await self.svc.due():
            await self.svc.dispatch(job)
            n += 1
        return n

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                await self.tick()
            except Exception:
                log.warning("outbound sweep failed", exc_info=True)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
