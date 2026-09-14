"""Resolve the AssistantConfig for an inbound call (Redis cache -> Core API -> demo)."""

from __future__ import annotations

import logging
from datetime import time

import httpx
from redis.asyncio import Redis

from parlio_voice.models import (
    AfterHoursBehaviour,
    AssistantConfig,
    BusinessInfo,
    BusinessRule,
    DayHours,
    Destination,
    Faq,
    IntakeField,
    Persona,
    Schedule,
    SmsScenario,
    SmsTrigger,
    TransferConfig,
)
from parlio_voice.settings import Settings

log = logging.getLogger("parlio.config")

_DEMO_DAY = {"open": time(8, 0), "close": time(18, 0)}
DEMO_CONFIG = AssistantConfig(
    tenant_id="demo",
    company_id="demo",
    assistant_id="demo",
    name="Parlio",
    business_name="Parlio Demo Plumbing",
    business=BusinessInfo(
        description=(
            "Parlio Demo Plumbing is a family-run, Gas Safe registered plumbing and heating "
            "company based in Didsbury, serving homes and small businesses across South "
            "Manchester, Stockport and Trafford. Established in 2009, the team of six engineers "
            "handles everything from dripping taps to full boiler installations, with a 24/7 "
            "emergency call-out service for burst pipes, leaks and no-heating situations. "
            "All work is guaranteed for 12 months and every engineer is DBS checked."
        ),
        website="https://www.parliodemoplumbing.co.uk",
        address="Unit 4, Burton Road Trading Estate, Didsbury, Manchester M20 2LW",
        phone="0161 496 0000",
        email="hello@parliodemoplumbing.co.uk",
        services=[
            "Boiler repairs and annual servicing",
            "New boiler installation (Worcester Bosch and Vaillant accredited)",
            "Central heating repairs, power flushing and radiator replacement",
            "Burst pipes, leaks and emergency call-outs",
            "Blocked drains, sinks and toilets",
            "Bathroom and kitchen plumbing",
            "Landlord gas safety certificates (CP12)",
            "Unvented cylinders and hot water systems",
        ],
    ),
    hours=Schedule(
        timezone="Europe/London",
        hours={
            "mon": DayHours(**_DEMO_DAY),
            "tue": DayHours(**_DEMO_DAY),
            "wed": DayHours(**_DEMO_DAY),
            "thu": DayHours(**_DEMO_DAY),
            "fri": DayHours(**_DEMO_DAY),
            "sat": DayHours(open=time(9, 0), close=time(13, 0)),
        },
    ),
    persona=Persona(
        tone="warm, calm and reassuring",
        formality="conversational",
        pace="normal",
        extra=(
            "Callers are often stressed about a leak or having no heating; acknowledge that "
            "first, then get the details. Use plain English, no jargon."
        ),
    ),
    greeting=(
        "Hi, thanks for calling {business_name}, this is {name}. Is this an emergency, "
        "or can I help you book a plumber or answer a question?"
    ),
    instructions=(
        "You are {name}, the phone receptionist for {business_name}, a Gas Safe registered "
        "plumbing and heating company in Didsbury, South Manchester. Keep replies to one or two "
        "short sentences. For any job, collect the caller's full name, best callback number, the "
        "property address and postcode, a short description of the problem and how urgent it is. "
        "Confirm the details back before ending. If it is an emergency (burst pipe, major leak, "
        "flooding, no heating or hot water for a vulnerable person) offer to transfer to the "
        "on-call engineer straight away. Otherwise tell them an engineer will call back within "
        "the hour during opening hours, or first thing next working day if after hours."
    ),
    rules=[
        BusinessRule(
            name="No fixed prices",
            instruction=(
                "Never quote a fixed price. You may say the standard call-out is from a set "
                "hourly rate and that the engineer confirms the cost before starting work."
            ),
        ),
        BusinessRule(
            name="Gas smell safety",
            instruction=(
                "If a caller smells gas, tell them to open windows, not use switches or naked "
                "flames, leave the property and call the National Gas Emergency line on "
                "0800 111 999 immediately, before anything else."
            ),
        ),
        BusinessRule(
            name="Stop the water",
            instruction=(
                "For a burst pipe or active leak, tell the caller to turn off the mains stop tap "
                "(usually under the kitchen sink) while they wait."
            ),
        ),
        BusinessRule(
            name="Coverage area",
            instruction=(
                "We cover South Manchester, Stockport and Trafford (M, SK and WA postcodes). "
                "For anywhere else, apologise and say we can't help this time."
            ),
        ),
        BusinessRule(
            name="Confirm details",
            instruction="Always read back the phone number and postcode to confirm them.",
        ),
    ],
    faqs=[
        Faq(
            category="pricing",
            question="How much do you charge?",
            answer=(
                "Our standard call-out is charged at an hourly rate with no hidden fees, and the "
                "engineer always confirms the cost before starting. Boiler services are a fixed "
                "price. I can't quote exact figures, but the engineer will when they call back."
            ),
        ),
        Faq(
            category="emergency",
            question="Do you offer 24-hour emergency call-outs?",
            answer=(
                "Yes, we have an engineer on call 24/7 for burst pipes, major leaks and no "
                "heating. Out-of-hours call-outs carry an emergency rate."
            ),
        ),
        Faq(
            category="coverage",
            question="Which areas do you cover?",
            answer=(
                "South Manchester, Stockport and Trafford, including Didsbury, Chorlton, "
                "Withington, Cheadle, Sale and Altrincham."
            ),
        ),
        Faq(
            category="booking",
            question="How quickly can someone come out?",
            answer=(
                "Emergencies are usually attended within two hours. For routine work we can "
                "normally offer a slot within two to three working days."
            ),
        ),
        Faq(
            category="qualifications",
            question="Are you Gas Safe registered?",
            answer=(
                "Yes, all our heating engineers are Gas Safe registered and we're Worcester Bosch "
                "and Vaillant accredited installers."
            ),
        ),
        Faq(
            category="payment",
            question="How can I pay?",
            answer=(
                "Card, bank transfer or cash on completion. We also offer 0% finance over 12 "
                "months on new boiler installations."
            ),
        ),
        Faq(
            category="guarantee",
            question="Is your work guaranteed?",
            answer=(
                "All our work comes with a 12-month guarantee, and new boilers carry the "
                "manufacturer's warranty of up to 10 years."
            ),
        ),
        Faq(
            category="landlords",
            question="Do you do landlord gas safety certificates?",
            answer=(
                "Yes, we issue CP12 landlord gas safety certificates and can set up annual "
                "reminders for your properties."
            ),
        ),
    ],
    sms_scenarios=[
        SmsScenario(
            trigger=SmsTrigger.TICKET_CONFIRMATION,
            name="Callback confirmation",
            template=(
                "Hi {caller_name}, thanks for calling {business_name}. We've logged your request "
                "(ref {ticket_id}) and an engineer will call you back shortly."
            ),
        ),
        SmsScenario(
            trigger=SmsTrigger.MISSED_CALL,
            name="Missed call",
            template=(
                "Sorry we missed you! This is {business_name}. Reply here or call us back on "
                "0161 496 0000 and we'll get an engineer to you."
            ),
        ),
        SmsScenario(
            trigger=SmsTrigger.BOOKING_LINK,
            name="Book online",
            template=(
                "Hi {caller_name}, you can book your boiler service with {business_name} here: "
                "https://www.parliodemoplumbing.co.uk/book"
            ),
        ),
        SmsScenario(
            trigger=SmsTrigger.ADDRESS,
            name="Our address",
            template=(
                "{business_name}: Unit 4, Burton Road Trading Estate, Didsbury, Manchester "
                "M20 2LW. Open Mon-Fri 8am-6pm, Sat 9am-1pm."
            ),
        ),
    ],
    transfer=TransferConfig(
        department_notes={
            "general": "anything else, new enquiries, general questions",
            "bookings": "booking, moving or cancelling an appointment; engineer ETAs",
            "accounts": "invoices, payments, quotes, refunds, statements",
            "emergencies": "gas leaks, burst pipes, flooding, no heating or hot water",
        },
        urgent_keywords=[
            "emergency",
            "burst",
            "flooding",
            "flood",
            "gas",
            "leak",
            "no heating",
            "no hot water",
        ],
        after_hours=AfterHoursBehaviour.TICKET,
        intake=[
            IntakeField(name="caller_name", prompt="the caller's full name"),
            IntakeField(name="callback_number", prompt="the best number to call back on"),
            IntakeField(name="address", prompt="the property address and postcode"),
            IntakeField(name="reason", prompt="a one-sentence description of the problem"),
            IntakeField(name="urgency", prompt="how urgent it is: low, normal, high or urgent"),
            IntakeField(
                name="callback_window", prompt="when they would like a callback", required=False
            ),
        ],
        destinations=[
            Destination(
                id="office",
                name="the office",
                department="general",
                address="+441614960000",
                fallback_id="oncall",
            ),
            Destination(
                id="bookings",
                name="Sarah in bookings",
                department="bookings",
                address="+441614960001",
                fallback_id="office",
            ),
            Destination(
                id="accounts",
                name="accounts",
                department="accounts",
                address="+441614960002",
                schedule=Schedule(
                    hours={
                        d: DayHours(open=time(9, 0), close=time(17, 0))
                        for d in ("mon", "tue", "wed", "thu", "fri")
                    }
                ),
                fallback_id="office",
            ),
            Destination(
                id="oncall",
                name="the on-call engineer",
                department="emergencies",
                address="+447700900000",
                on_call=True,
                schedule=Schedule(always=True),
            ),
        ],
    ),
)


class ConfigClient:
    def __init__(self, settings: Settings, redis: Redis | None) -> None:
        self._s = settings
        self._redis = redis
        self._http = httpx.AsyncClient(
            base_url=settings.api_url,
            headers={"X-Worker-Key": settings.worker_api_key},
            timeout=httpx.Timeout(2.0, connect=1.0),
        )

    @property
    def http(self) -> httpx.AsyncClient:
        return self._http

    async def aclose(self) -> None:
        await self._http.aclose()

    def _cache_key(self, dialed_number: str) -> str:
        return f"parlio:assistant_config:{dialed_number}"

    async def resolve(self, dialed_number: str) -> AssistantConfig:
        if self._redis is not None:
            try:
                raw = await self._redis.get(self._cache_key(dialed_number))
                if raw:
                    return AssistantConfig.model_validate_json(raw)
            except Exception:
                log.warning("redis cache read failed", exc_info=True)

        cfg = await self._fetch(dialed_number)
        if cfg is None:
            if not self._s.demo_mode:
                raise LookupError(f"no assistant configured for {dialed_number}")
            log.warning("no assistant for %s, using demo config", dialed_number)
            return DEMO_CONFIG

        if self._redis is not None:
            try:
                await self._redis.set(
                    self._cache_key(dialed_number),
                    cfg.model_dump_json(),
                    ex=self._s.config_cache_ttl_s,
                )
            except Exception:
                log.warning("redis cache write failed", exc_info=True)
        return cfg

    async def get(self, assistant_id: str) -> AssistantConfig:
        """Config by id (outbound jobs know their assistant; no number lookup involved)."""
        r = await self._http.get(f"/v1/worker/assistants/{assistant_id}")
        if r.status_code == 404:
            raise LookupError(f"assistant {assistant_id} not found")
        r.raise_for_status()
        return AssistantConfig.model_validate(r.json())

    async def _fetch(self, dialed_number: str) -> AssistantConfig | None:
        try:
            r = await self._http.get(
                "/v1/worker/assistants/resolve", params={"number": dialed_number}
            )
        except httpx.HTTPError:
            log.warning("core api unreachable", exc_info=True)
            return None
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return AssistantConfig.model_validate(r.json())
