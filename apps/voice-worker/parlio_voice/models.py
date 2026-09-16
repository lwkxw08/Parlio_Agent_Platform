"""Tenant/assistant configuration and call-event models shared with the Core API."""

from __future__ import annotations

import re
from datetime import UTC, datetime, time
from enum import StrEnum
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field


class RegionProfile(StrEnum):
    STANDARD = "standard"
    SOVEREIGN_UK = "sovereign-uk"
    SOVEREIGN_UK_STRICT = "sovereign-uk-strict"


class STTProvider(StrEnum):
    DEEPGRAM = "deepgram"
    DEEPGRAM_EU = "deepgram-eu"


class LLMProvider(StrEnum):
    OPENAI = "openai"
    AZURE_OPENAI = "azure-openai"
    ANTHROPIC = "anthropic"
    GROQ = "groq"


class TTSProvider(StrEnum):
    CARTESIA = "cartesia"
    ELEVENLABS = "elevenlabs"


class VoiceConfig(BaseModel):
    provider: TTSProvider = TTSProvider.CARTESIA
    voice_id: str = "c46cf1f6-49a1-4d67-9a57-ff859a4046d3"  # Cartesia "Cora" (British)
    speed: float | None = None


class ProviderChain(BaseModel):
    """Primary provider plus ordered fallbacks; resolved per region profile."""

    stt: list[STTProvider] = Field(default_factory=lambda: [STTProvider.DEEPGRAM])
    llm: list[LLMProvider] = Field(default_factory=lambda: [LLMProvider.OPENAI])
    tts: list[TTSProvider] = Field(default_factory=lambda: [TTSProvider.CARTESIA])


class TurnTuning(BaseModel):
    min_endpointing_delay: float = 0.2
    max_endpointing_delay: float = 2.0
    allow_interruptions: bool = True
    min_interruption_duration: float = 0.4
    preemptive_generation: bool = True


class RecordingConfig(BaseModel):
    enabled: bool = True
    # Warm transfers only: the bridged-in human gets their own track file and the caller
    # track runs on until the call ends.
    record_transfers: bool = False
    consent_announcement: dict[str, str] = Field(
        default_factory=lambda: {
            "en": "This call may be recorded for quality and training purposes.",
        }
    )


class DayHours(BaseModel):
    """Opening window for one weekday, local time. `open >= close` means closed."""

    open: time = time(9, 0)
    close: time = time(17, 30)

    def contains(self, t: time) -> bool:
        return self.open <= t < self.close


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class Schedule(BaseModel):
    """Weekly availability, keyed by weekday. Missing day = unavailable. Empty = always."""

    timezone: str = "Europe/London"
    hours: dict[str, DayHours] = Field(
        default_factory=lambda: {d: DayHours() for d in WEEKDAYS[:5]}
    )
    always: bool = False

    def is_open(self, now: datetime | None = None) -> bool:
        if self.always:
            return True
        local = (now or datetime.now(UTC)).astimezone(ZoneInfo(self.timezone))
        day = self.hours.get(WEEKDAYS[local.weekday()])
        return day is not None and day.contains(local.time().replace(tzinfo=None))


class DestinationKind(StrEnum):
    PSTN = "pstn"  # dial out via carrier, e.g. tel:+447700900000
    SIP = "sip"  # full SIP URI on the customer's PBX/trunk
    EXTENSION = "extension"  # extension on the customer's BYO trunk (Phase 5b)


class Destination(BaseModel):
    """A human (or hunt group) the assistant can hand a caller to."""

    id: str
    name: str
    department: str = "general"
    kind: DestinationKind = DestinationKind.PSTN
    address: str  # E.164 number, sip:user@host, or extension digits
    priority: int = 0  # lower first within a department
    schedule: Schedule = Field(default_factory=Schedule)
    fallback_id: str | None = None
    on_call: bool = False  # eligible for urgent/emergency escalation

    def is_available(self, now: datetime | None = None) -> bool:
        return self.schedule.is_open(now)


class TransferMode(StrEnum):
    WARM = "warm"  # AI briefs the human, then bridges
    COLD = "cold"  # blind SIP REFER


class AfterHoursBehaviour(StrEnum):
    TICKET = "ticket"
    VOICEMAIL = "voicemail"
    BOTH = "both"


class IntakeField(BaseModel):
    name: str
    prompt: str
    required: bool = True


DEFAULT_INTAKE = [
    IntakeField(name="caller_name", prompt="the caller's full name"),
    IntakeField(name="callback_number", prompt="the best number to call back on"),
    IntakeField(name="reason", prompt="a one-sentence reason for the call"),
    IntakeField(name="urgency", prompt="how urgent it is: low, normal, high or urgent"),
    IntakeField(name="callback_window", prompt="when they would like a callback", required=False),
]


class TransferConfig(BaseModel):
    enabled: bool = True
    mode: TransferMode = TransferMode.WARM
    ring_timeout_s: int = 25
    destinations: list[Destination] = Field(default_factory=list)
    department_notes: dict[str, str] = Field(default_factory=dict)
    urgent_keywords: list[str] = Field(
        default_factory=lambda: [
            "emergency",
            "gas leak",
            "flooding",
            "fire",
            "chest pain",
            "not breathing",
            "burst pipe",
        ]
    )
    after_hours: AfterHoursBehaviour = AfterHoursBehaviour.TICKET
    intake: list[IntakeField] = Field(default_factory=lambda: list(DEFAULT_INTAKE))
    sla_minutes: dict[str, int] = Field(
        default_factory=lambda: {"urgent": 15, "high": 60, "normal": 240, "low": 1440}
    )

    def departments(self) -> list[str]:
        seen: dict[str, None] = {}
        for d in self.destinations:
            seen.setdefault(d.department, None)
        return list(seen)

    def describe_departments(self) -> str:
        """One line per department, with its routing description when set."""
        return "; ".join(
            f"{d} ({self.department_notes[d]})" if self.department_notes.get(d) else d
            for d in self.departments()
        )

    def candidates(
        self, department: str | None = None, now: datetime | None = None, urgent: bool = False
    ) -> list[Destination]:
        """Available destinations in ring order; urgent calls may go to on-call staff 24/7."""
        pool = [
            d
            for d in self.destinations
            if department is None or d.department.lower() == department.lower()
        ]
        avail = [d for d in pool if d.is_available(now) or (urgent and d.on_call)]
        return sorted(avail, key=lambda d: (not (urgent and d.on_call), d.priority))

    def by_id(self, dest_id: str) -> Destination | None:
        return next((d for d in self.destinations if d.id == dest_id), None)

    def matches_urgent(self, text: str) -> str | None:
        """First urgent keyword in `text`, ignoring negated mentions ("it's not an emergency")."""
        low = text.lower()
        for k in self.urgent_keywords:
            for m in re.finditer(re.escape(k), low):
                before = low[max(0, m.start() - 24) : m.start()]
                if not _NEGATION.search(before):
                    return k
        return None


_NEGATION = re.compile(r"\b(not|no|isn't|isnt|wasn't|never|nothing|without)\b[^.!?,;]*$")


class Faq(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    category: str = "general"
    question: str
    answer: str
    enabled: bool = True
    source: str = "manual"  # manual | website | suggested


class BusinessRule(BaseModel):
    """Plain-English rule injected into the system prompt, e.g. 'Never quote prices'."""

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    name: str
    instruction: str
    enabled: bool = True


class SmsTrigger(StrEnum):
    AFTER_CALL = "after_call"
    MISSED_CALL = "missed_call"
    BOOKING_LINK = "booking_link"
    ADDRESS = "address"
    PAYMENT_LINK = "payment_link"
    TICKET_CONFIRMATION = "ticket_confirmation"
    APPOINTMENT_REMINDER = "appointment_reminder"
    CUSTOM = "custom"


class SmsScenario(BaseModel):
    """Text the AI may send mid/after call (delivery arrives with the SMS provider in Phase 5)."""

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    trigger: SmsTrigger = SmsTrigger.CUSTOM
    name: str
    template: str  # may use {business_name}, {caller_name}, {ticket_id}
    enabled: bool = True


class Persona(BaseModel):
    tone: str = "friendly and professional"
    formality: str = "conversational"  # conversational | formal | casual
    pace: str = "normal"  # slower | normal | brisk
    extra: str = ""


class SpeakingStyle(BaseModel):
    """How details are read back on the phone (Studio → Speaking style)."""

    one_detail_at_a_time: bool = True
    digits_individually: bool = True
    spell_postcodes: bool = True
    summary_per_line: bool = True
    confirm_phrase: str = "is that right?"
    final_confirm_phrase: str = "Is all of that correct?"
    extra_rules: list[str] = Field(default_factory=list)

    def prompt(self) -> str:
        lines: list[str] = []
        if self.one_detail_at_a_time:
            lines.append(
                "Confirm one detail at a time, never several in one sentence. After each, ask "
                f"'{self.confirm_phrase}' and wait for the answer before moving on."
            )
        if self.digits_individually:
            lines.append(
                "Read phone numbers back as single digits with a space between each and a pause "
                "between groups, e.g. '0 7 9 3 0, 9 3 4, 0 9 8' - never as whole numbers like 934. "
                "Say house or flat numbers digit by digit ('flat 1 2')."
            )
        if self.spell_postcodes:
            lines.append(
                "Read postcodes one character at a time with spaces, e.g. 'M 2 1, 2 D F'. Say "
                "addresses slowly, one line at a time: house number and street, then town, then "
                f"postcode, then ask '{self.confirm_phrase}'. If a postcode or name is unclear, "
                "ask the caller to spell it and repeat it back letter by letter before saving it."
            )
        if self.summary_per_line:
            number = "0 7 9 3 0, 9 3 4, 0 9 8" if self.digits_individually else "07930 934098"
            postcode = "M 2 1, 2 D F" if self.spell_postcodes else "M21 2DF"
            lines.append(
                "Never repeat the caller's name, address and number back together in one go. If a "
                "final summary is needed, use one short sentence per detail on its own line "
                f"('Your name is Keith Wilson.' / 'Your callback number is {number}.' / "
                f"'Your address is 1 High Street, Manchester, {postcode}.') and finish with "
                f"'{self.final_confirm_phrase}'."
            )
        lines.extend(r.strip() for r in self.extra_rules if r.strip())
        lines.append(
            "When the caller wants a person, call transfer_to_human straight away in that same "
            "turn; do not just say you will connect them and then wait."
        )
        return "Speaking style on the phone:\n" + "\n".join(f"- {line}" for line in lines)


class BusinessInfo(BaseModel):
    description: str = ""
    website: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    services: list[str] = Field(default_factory=list)


class VerificationField(StrEnum):
    DOB = "dob"
    POSTCODE = "postcode"
    REFERENCE = "reference"


class VerificationConfig(BaseModel):
    """Caller identity checks against the contact record (Phase 12).

    Answers are compared server-side and never written to transcripts or logs.
    """

    enabled: bool = False
    fields: list[VerificationField] = Field(
        default_factory=lambda: [VerificationField.POSTCODE, VerificationField.DOB]
    )
    required_matches: int = 1
    max_attempts: int = 3
    required_for_payments: bool = True
    required_for_account_details: bool = True


class PaymentsConfig(BaseModel):
    """Mid-call payments: a hosted pay link is texted to the caller; Parlio never sees card data."""

    enabled: bool = False
    currency: str = "gbp"
    max_pence: int = 50_000
    default_description: str = "Deposit for {business_name}"
    link_ttl_minutes: int = 30
    # PCI-compliant card-by-phone (DTMF) capture via a provider that keeps digits off our media
    # path; only offered when a capture provider is configured platform-side.
    card_by_phone: bool = False


class ScreeningMode(StrEnum):
    OFF = "off"
    UNKNOWN = "unknown"  # callers we have no contact record for
    ALL = "all"


class ScreeningConfig(BaseModel):
    """Call screening & spam filtering (Phase 20d).

    Screened callers are asked who they are and why they are calling before the assistant helps;
    sales/robocalls are ended early. Rejected callers (withheld IDs, spam-listed numbers) never
    reach the assistant at all, so they cost no minutes.
    """

    mode: ScreeningMode = ScreeningMode.OFF
    block_withheld: bool = False
    block_spam: bool = True
    allow_numbers: list[str] = Field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return self.mode != ScreeningMode.OFF or self.block_withheld or self.block_spam


class AssistantConfig(BaseModel):
    tenant_id: str
    company_id: str
    assistant_id: str
    assistant_version: int = 1
    name: str = "Parlio"
    business_name: str = "the business"
    language: str = "en"
    languages: list[str] = Field(default_factory=lambda: ["en"])
    business: BusinessInfo = Field(default_factory=BusinessInfo)
    hours: Schedule = Field(default_factory=Schedule)
    persona: Persona = Field(default_factory=Persona)
    speaking: SpeakingStyle = Field(default_factory=SpeakingStyle)
    rules: list[BusinessRule] = Field(default_factory=list)
    faqs: list[Faq] = Field(default_factory=list)
    sms_scenarios: list[SmsScenario] = Field(default_factory=list)
    blocked_numbers: list[str] = Field(default_factory=list)
    greeting: str = "Hi, thanks for calling {business_name}. How can I help you today?"
    instructions: str = (
        "You are {name}, the friendly and efficient phone receptionist for {business_name}. "
        "Keep answers short (one or two sentences), speak naturally, never invent facts. "
        "If you do not know something, offer to take a message."
    )
    region_profile: RegionProfile = RegionProfile.STANDARD
    providers: ProviderChain = Field(default_factory=ProviderChain)
    llm_model: str | None = None
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    turn: TurnTuning = Field(default_factory=TurnTuning)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    transfer: TransferConfig = Field(default_factory=TransferConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    payments: PaymentsConfig = Field(default_factory=PaymentsConfig)
    screening: ScreeningConfig = Field(default_factory=ScreeningConfig)

    def rendered_greeting(self) -> str:
        return self.greeting.format(name=self.name, business_name=self.business_name)

    def rendered_instructions(self) -> str:
        base = self.instructions.format(name=self.name, business_name=self.business_name)
        return "\n\n".join([base, *self.knowledge_sections(), self.speaking.prompt()])

    def knowledge_sections(self) -> list[str]:
        """Studio-managed prompt sections: persona, business, hours, rules, FAQs, languages."""
        out: list[str] = []
        p = self.persona
        out.append(
            f"Tone: {p.tone}; style: {p.formality}; pace: {p.pace}."
            + (f" {p.extra}" if p.extra else "")
        )
        b = self.business
        facts = [
            f"About {self.business_name}: {b.description}" if b.description else "",
            f"Address: {b.address}" if b.address else "",
            f"Website: {b.website}" if b.website else "",
            f"Email: {b.email}" if b.email else "",
            f"Services: {', '.join(b.services)}" if b.services else "",
        ]
        if any(facts):
            out.append("\n".join(f for f in facts if f))
        if self.hours.always:
            out.append("Opening hours: open 24 hours.")
        else:
            days = ", ".join(
                f"{d.title()} {h.open:%H:%M}-{h.close:%H:%M}"
                for d, h in self.hours.hours.items()
                if h.open < h.close
            )
            out.append(f"Opening hours ({self.hours.timezone}): {days or 'not set'}.")
        rules = [r.instruction for r in self.rules if r.enabled]
        if rules:
            out.append("Business rules you must follow:\n" + "\n".join(f"- {r}" for r in rules))
        faqs = [f for f in self.faqs if f.enabled]
        if faqs:
            out.append(
                "Frequently asked questions (answer from these when relevant):\n"
                + "\n".join(f"Q: {f.question}\nA: {f.answer}" for f in faqs)
            )
        if len(self.languages) > 1:
            out.append(
                f"You can speak {', '.join(self.languages)}; reply in the caller's language."
            )
        return out

    def is_blocked(self, number: str | None) -> bool:
        if not number:
            return False
        digits = number.lstrip("+")
        return any(digits == b.lstrip("+") for b in self.blocked_numbers)

    def is_open(self, now: datetime | None = None) -> bool:
        return self.hours.is_open(now)

    def consent_text(self) -> str | None:
        if not self.recording.enabled:
            return None
        ann = self.recording.consent_announcement
        return ann.get(self.language) or ann.get("en")


CONVERSATION_STYLE = SpeakingStyle().prompt()


class CallDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallEventType(StrEnum):
    CALL_STARTED = "call.started"
    CALL_ANSWERED = "call.answered"
    TURN_COMPLETED = "call.turn_completed"
    TRANSCRIPT_ITEM = "call.transcript_item"
    RECORDING_STARTED = "call.recording_started"
    CALL_ENDED = "call.ended"
    CALL_FAILED = "call.failed"
    TRANSFER_STARTED = "call.transfer_started"
    TRANSFER_COMPLETED = "call.transfer_completed"
    TICKET_CREATED = "call.ticket_created"
    ESCALATION = "call.escalation"
    SUPERVISOR = "call.supervisor"
    APPROVAL_REQUESTED = "call.approval_requested"
    PAYMENT_REQUESTED = "call.payment_requested"
    CALLER_VERIFIED = "call.caller_verified"


class TransferOutcome(StrEnum):
    ANSWERED = "answered"
    NO_ANSWER = "no_answer"
    VOICEMAIL = "voicemail"
    REJECTED = "rejected"
    TICKETED = "ticketed"
    UNAVAILABLE = "unavailable"


class TicketPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TicketIntake(BaseModel):
    """What the assistant collects before raising a ticket (worker -> API)."""

    call_id: str | None = None
    caller_name: str | None = None
    caller_number: str | None = None
    reason: str
    priority: TicketPriority = TicketPriority.NORMAL
    category: str | None = None
    department: str | None = None
    callback_window: str | None = None
    source: str = "ai_intake"  # ai_intake | no_answer | after_hours | escalation | manual
    thread_id: str | None = None  # inbox conversation this ticket belongs to (kept in sync)


class CallEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    type: CallEventType
    call_id: str
    tenant_id: str
    company_id: str
    assistant_id: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)


class TurnLatency(BaseModel):
    """Latency budget for one agent turn (all seconds)."""

    speech_id: str
    end_of_utterance_delay: float | None = None
    transcription_delay: float | None = None
    llm_ttft: float | None = None
    tts_ttfb: float | None = None

    @property
    def total(self) -> float | None:
        parts = [self.end_of_utterance_delay, self.llm_ttft, self.tts_ttfb]
        if any(p is None for p in parts):
            return None
        return sum(p for p in parts if p is not None)
