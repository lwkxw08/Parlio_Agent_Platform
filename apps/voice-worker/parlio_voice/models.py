"""Tenant/assistant configuration and call-event models shared with the Core API."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
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
    max_endpointing_delay: float = 1.2
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


class Holiday(BaseModel):
    """A dated override of the weekly hours: closed all day, or open for a shorter window."""

    day: date
    name: str = "Holiday"
    closed: bool = True
    hours: DayHours | None = None


class Schedule(BaseModel):
    """Weekly availability, keyed by weekday. Missing day = unavailable. Empty = always."""

    timezone: str = "Europe/London"
    hours: dict[str, DayHours] = Field(
        default_factory=lambda: {d: DayHours() for d in WEEKDAYS[:5]}
    )
    always: bool = False
    holidays: list[Holiday] = Field(default_factory=list)

    def local(self, now: datetime | None = None) -> datetime:
        return (now or datetime.now(UTC)).astimezone(ZoneInfo(self.timezone))

    def holiday_on(self, now: datetime | None = None) -> Holiday | None:
        d = self.local(now).date()
        return next((h for h in self.holidays if h.day == d), None)

    def is_open(self, now: datetime | None = None) -> bool:
        local = self.local(now)
        hol = self.holiday_on(now)
        if hol is not None:
            if hol.closed or hol.hours is None:
                return False
            return hol.hours.contains(local.time().replace(tzinfo=None))
        if self.always:
            return True
        day = self.hours.get(WEEKDAYS[local.weekday()])
        return day is not None and day.contains(local.time().replace(tzinfo=None))

    def window(self, now: datetime | None = None) -> str:
        """Which persona window applies right now: `open`, `closed` or `holiday`."""
        if self.is_open(now):
            return "open"
        return "holiday" if self.holiday_on(now) is not None else "closed"


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
    site_id: str | None = None  # None = shared across every site

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
    # Warm transfers: the person who answers presses a key to take the call. Voicemail can't,
    # so an unanswered prompt counts as no answer and the caller is offered a callback.
    accept_key: bool = True
    accept_timeout_s: int = 10
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
        self,
        department: str | None = None,
        now: datetime | None = None,
        urgent: bool = False,
        site_id: str | None = None,
    ) -> list[Destination]:
        """Available destinations in ring order; urgent calls may go to on-call staff 24/7.

        With a `site_id`, that site's own people ring first and other sites' people are left
        out; shared (unsited) destinations are always eligible.
        """
        pool = [
            d
            for d in self.destinations
            if (department is None or d.department.lower() == department.lower())
            and (site_id is None or d.site_id in (None, site_id))
        ]
        avail = [d for d in pool if d.is_available(now) or (urgent and d.on_call)]
        return sorted(
            avail,
            key=lambda d: (
                not (urgent and d.on_call),
                site_id is not None and d.site_id != site_id,
                d.priority,
            ),
        )

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
    BOOKING_CONFIRMATION = "booking_confirmation"
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
            "turn; do not just say you will connect them and then wait. But once the caller "
            "has asked for a callback, stay with the callback: collect the details and log it "
            "with create_ticket. Their description of the problem (even 'I want some advice' or "
            "'I need to speak to someone about X') is the reason for the callback, "
            "not a request to be transferred. If they say no to a transfer, never try again."
        )
        return "Speaking style on the phone:\n" + "\n".join(f"- {line}" for line in lines)


class GlossaryTerm(BaseModel):
    """A word the assistant must say (and hear) correctly: a brand, product, place or person."""

    term: str
    say_as: str = ""  # how to pronounce it, e.g. "Loo-shum" for Lewisham; empty = as written
    meaning: str = ""  # optional one-line explanation the assistant can use

    def prompt(self) -> str:
        bits = [f"'{self.term}'"]
        if self.say_as:
            bits.append(f"pronounced '{self.say_as}'")
        if self.meaning:
            bits.append(self.meaning)
        return " - ".join(bits) if len(bits) > 1 else bits[0]


class WebsiteSearchConfig(BaseModel):
    """Live look-ups on the business's own website mid-call (domain-locked)."""

    enabled: bool = False
    extra_urls: list[str] = Field(default_factory=list)  # deeper pages worth indexing
    max_pages: int = 12


class TransferWhenClosed(StrEnum):
    NORMAL = "normal"  # whoever is on their own schedule
    ON_CALL_ONLY = "on_call_only"  # only urgent calls to on-call staff
    NEVER = "never"  # take a message, never ring anyone


class AfterHoursPersona(BaseModel):
    """How the assistant behaves outside opening hours (Studio -> After hours).

    Everything here layers on top of the daytime set-up: an empty field means "same as
    during the day". Holidays use `holiday_greeting` when set, otherwise `greeting`.
    """

    enabled: bool = False
    greeting: str = (
        "Hi, thanks for calling {business_name}. We're closed at the moment, but I can still "
        "help - how can I help you today?"
    )
    holiday_greeting: str = ""
    tone: str = ""
    instructions: str = ""
    intake_only: bool = False  # take details for a callback rather than answer at length
    transfer: TransferWhenClosed = TransferWhenClosed.ON_CALL_ONLY
    quote_next_opening: bool = True

    def prompt(self, window: str, next_open: str | None) -> str:
        lines = [
            "It is currently outside opening hours"
            + (" (a holiday)" if window == "holiday" else "")
            + ". Tell callers we are closed if they ask, without apologising repeatedly."
        ]
        if self.quote_next_opening and next_open:
            lines.append(f"We reopen {next_open}; mention this when it is useful.")
        if self.tone:
            lines.append(f"Tone for this window: {self.tone}.")
        if self.intake_only:
            lines.append(
                "Do not try to resolve the enquiry in detail: take the caller's name, number "
                "and reason with create_ticket and promise a callback when we reopen. Answer "
                "only simple factual questions from the FAQs."
            )
        match self.transfer:
            case TransferWhenClosed.NEVER:
                lines.append("Never offer or attempt a transfer during this window.")
            case TransferWhenClosed.ON_CALL_ONLY:
                lines.append(
                    "Only transfer genuine emergencies to the on-call person; everyone else "
                    "gets a callback ticket."
                )
            case TransferWhenClosed.NORMAL:
                pass
        if self.instructions.strip():
            lines.append(self.instructions.strip())
        return "After-hours behaviour:\n" + "\n".join(f"- {line}" for line in lines)


class SiteRef(BaseModel):
    """A location/brand the assistant answers for (Phase 20g); numbers pick the site."""

    id: str
    name: str
    brand_name: str = ""
    numbers: list[str] = Field(default_factory=list)
    address: str = ""
    timezone: str | None = None

    def owns(self, number: str | None) -> bool:
        if not number:
            return False
        digits = number.lstrip("+")
        return any(digits == n.lstrip("+") for n in self.numbers)


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
    name: str = "ParlioTec"
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
    glossary: list[GlossaryTerm] = Field(default_factory=list)
    website_search: WebsiteSearchConfig = Field(default_factory=WebsiteSearchConfig)
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
    after_hours: AfterHoursPersona = Field(default_factory=AfterHoursPersona)
    sites: list[SiteRef] = Field(default_factory=list)

    def site_for(self, dialed: str | None) -> SiteRef | None:
        return next((s for s in self.sites if s.owns(dialed)), None)

    def brand_for(self, site: SiteRef | None) -> str:
        return (site.brand_name if site and site.brand_name else None) or self.business_name

    def window(self, now: datetime | None = None) -> str:
        return self.hours.window(now)

    def after_hours_active(self, now: datetime | None = None) -> bool:
        return self.after_hours.enabled and not self.is_open(now)

    def next_opening(self, now: datetime | None = None) -> str | None:
        """Plain-English next opening time, e.g. 'tomorrow at 09:00' / 'on Monday at 09:00'."""
        if self.hours.always and not self.hours.holidays:
            return None
        local = self.hours.local(now)
        for offset in range(0, 15):
            day = local.date() + timedelta(days=offset)
            hol = next((h for h in self.hours.holidays if h.day == day), None)
            dh: DayHours | None
            if hol is not None:
                dh = None if hol.closed else (hol.hours or DayHours())
            elif self.hours.always:
                dh = DayHours(open=time(0, 0), close=time(23, 59))
            else:
                dh = self.hours.hours.get(WEEKDAYS[day.weekday()])
            if dh is None or dh.open >= dh.close:
                continue
            if offset == 0 and local.time().replace(tzinfo=None) >= dh.open:
                continue
            when = "today" if offset == 0 else "tomorrow" if offset == 1 else f"on {day:%A}"
            return f"{when} at {dh.open:%H:%M}"
        return None

    def rendered_greeting(self, now: datetime | None = None, site: SiteRef | None = None) -> str:
        brand = self.brand_for(site)
        template = self.greeting
        if self.after_hours_active(now):
            ah = self.after_hours
            template = (
                ah.holiday_greeting
                if self.window(now) == "holiday" and ah.holiday_greeting.strip()
                else ah.greeting
            ) or self.greeting
        return template.format(name=self.name, business_name=brand)

    def rendered_instructions(
        self, now: datetime | None = None, site: SiteRef | None = None
    ) -> str:
        brand = self.brand_for(site)
        base = self.instructions.format(name=self.name, business_name=brand)
        parts = [base, *self.knowledge_sections()]
        if site is not None:
            facts = [f"This call came in on the number for {site.name}"]
            if site.brand_name:
                facts.append(f"trading as {site.brand_name}")
            if site.address:
                facts.append(f"at {site.address}")
            parts.append(", ".join(facts) + ". Use that location's details when they differ.")
        if self.after_hours_active(now):
            parts.append(self.after_hours.prompt(self.window(now), self.next_opening(now)))
        parts.append(self.speaking.prompt())
        return "\n\n".join(parts)

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
        if self.glossary:
            out.append(
                "Glossary - names and terms to recognise and say exactly like this (spell them "
                "as written if asked):\n"
                + "\n".join(f"- {g.prompt()}" for g in self.glossary if g.term.strip())
            )
        if self.hours.always:
            out.append("Opening hours: open 24 hours.")
        else:
            days = ", ".join(
                f"{d.title()} {h.open:%H:%M}-{h.close:%H:%M}"
                for d, h in self.hours.hours.items()
                if h.open < h.close
            )
            out.append(f"Opening hours ({self.hours.timezone}): {days or 'not set'}.")
        hols = [
            f"{h.day:%-d %B}: {h.name}"
            + (
                ""
                if h.closed or h.hours is None
                else f" ({h.hours.open:%H:%M}-{h.hours.close:%H:%M})"
            )
            for h in sorted(self.hours.holidays, key=lambda h: h.day)
            if h.day >= datetime.now(UTC).date()
        ][:8]
        if hols:
            out.append("Upcoming holidays/closures: " + "; ".join(hols) + ".")
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
