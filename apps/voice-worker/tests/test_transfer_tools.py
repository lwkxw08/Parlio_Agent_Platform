from datetime import UTC, datetime
from typing import Any

import pytest

from parlio_voice.config_client import DEMO_CONFIG
from parlio_voice.models import (
    AssistantConfig,
    CallEventType,
    Destination,
    Schedule,
    TransferConfig,
    TransferMode,
    TransferOutcome,
)
from parlio_voice.tools import (
    ReceptionistTools,
    after_hours_instruction,
    booking_first_instruction,
    caller_id_instruction,
    guess_department,
    mentions_connecting,
    normalise_number,
    spoken_number,
)
from parlio_voice.transfer import SimulatedBridge, TransferEngine

MONDAY_10 = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)  # 10:00 BST
SUNDAY_10 = datetime(2026, 9, 13, 9, 0, tzinfo=UTC)


def cfg(destinations: list[Destination], **kw: Any) -> AssistantConfig:
    return AssistantConfig(
        assistant_id="a",
        tenant_id="t",
        company_id="c",
        business_name="Acme Plumbing",
        transfer=TransferConfig(destinations=destinations, **kw),
    )


def office(**kw: Any) -> Destination:
    return Destination(id="office", name="Office", address="+441onnnn", **kw)


def oncall(**kw: Any) -> Destination:
    return Destination(
        id="oncall",
        name="Dave",
        department="emergencies",
        address="+447onnnn",
        schedule=Schedule(always=True),
        on_call=True,
        **kw,
    )


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[CallEventType, dict[str, Any]]] = []
        self.said: list[str] = []

    def emit(self, t: CallEventType, p: dict[str, Any]) -> None:
        self.events.append((t, p))

    async def say(self, text: str) -> None:
        self.said.append(text)


def make_tools(
    c: AssistantConfig, bridge: SimulatedBridge, now: datetime
) -> tuple[ReceptionistTools, Recorder]:
    rec = Recorder()
    engine = TransferEngine(c.transfer, bridge, now=now)
    return ReceptionistTools(c, "call-1", "+447700900000", engine, None, rec.emit, rec.say), rec


def test_schedule_default_is_uk_office_hours() -> None:
    s = Schedule()
    assert s.is_open(MONDAY_10)
    assert not s.is_open(SUNDAY_10)
    assert Schedule(always=True).is_open(SUNDAY_10)


def test_candidates_respect_schedule_and_urgent_on_call() -> None:
    t = cfg([office(), oncall()]).transfer
    assert [d.id for d in t.candidates(now=MONDAY_10)] == ["office", "oncall"]
    assert [d.id for d in t.candidates(now=SUNDAY_10)] == ["oncall"]
    assert [d.id for d in t.candidates(now=SUNDAY_10, urgent=True)] == ["oncall"]
    assert t.candidates(department="sales", now=MONDAY_10) == []
    assert t.departments() == ["general", "emergencies"]


async def test_warm_transfer_answered_briefs_and_leaves() -> None:
    bridge = SimulatedBridge({"office": TransferOutcome.ANSWERED})
    tools, rec = make_tools(cfg([office()]), bridge, MONDAY_10)
    res = await tools.transfer(None, "a leaking tap")
    assert res.succeeded and res.connected and res.connected.id == "office"
    assert bridge.dialed == ["office"] and bridge.left
    assert "Connecting you" in rec.said[0]
    briefing = rec.said[-1]
    assert briefing.startswith("Hi, this is") and "Hi Office" not in briefing
    assert "leaking tap" in briefing and "0 7 7 0 0" in briefing
    assert tools.transfer_attempted
    types = [t for t, _ in rec.events]
    assert types == [CallEventType.TRANSFER_STARTED, CallEventType.TRANSFER_COMPLETED]
    assert rec.events[1][1]["outcome"] == TransferOutcome.ANSWERED


async def test_no_answer_falls_back_to_fallback_destination() -> None:
    bridge = SimulatedBridge(
        {"office": TransferOutcome.NO_ANSWER, "oncall": TransferOutcome.ANSWERED}
    )
    tools, rec = make_tools(cfg([office(fallback_id="oncall"), oncall()]), bridge, MONDAY_10)
    res = await tools.transfer("general", "boiler")
    assert res.succeeded and res.connected and res.connected.id == "oncall"
    assert bridge.dialed == ["office", "oncall"]
    outcomes = [p["outcome"] for t, p in rec.events if t == CallEventType.TRANSFER_COMPLETED]
    assert outcomes == [TransferOutcome.NO_ANSWER, TransferOutcome.ANSWERED]


async def test_cold_transfer_uses_refer() -> None:
    bridge = SimulatedBridge({"office": TransferOutcome.ANSWERED})
    tools, rec = make_tools(cfg([office()], mode=TransferMode.COLD), bridge, MONDAY_10)
    res = await tools.transfer(None, "invoice")
    assert res.succeeded
    assert not bridge.left  # cold: caller was REFERred away, agent needn't hang around
    assert len(rec.said) == 1 and rec.said[0].startswith("Connecting you")  # no briefing


async def test_out_of_hours_is_unavailable_and_prompts_ticket() -> None:
    c = cfg([office()])
    tools, _ = make_tools(c, SimulatedBridge(), SUNDAY_10)
    assert tools.availability()["someone_available"] is False
    res = await tools.transfer(None, "quote")
    assert res.outcome == TransferOutcome.UNAVAILABLE and not res.attempts
    assert not tools.transfer_attempted
    assert "ticket" in after_hours_instruction(c, False).lower()
    assert after_hours_instruction(c, True) == ""


def test_booking_first_instruction_offers_choice_not_unasked_transfer() -> None:
    text = booking_first_instruction(cfg([office(), oncall()]))
    assert "check_calendar" in text and "book_appointment" in text
    assert "the emergencies team now or the earliest appointment" in text
    assert booking_first_instruction(cfg([])) == ""
    assert "the general team" in booking_first_instruction(cfg([office()]))


def test_demo_profile_books_and_only_flags_real_emergencies() -> None:
    t = DEMO_CONFIG.transfer
    assert t.matches_urgent("the boiler's packed in and I've got no hot water") is None
    assert t.matches_urgent("there's a small leak under the sink") is None
    assert t.matches_urgent("I can smell gas in the kitchen") == "smell gas"
    assert t.matches_urgent("a pipe has burst upstairs") == "burst"
    assert "check_calendar" in DEMO_CONFIG.instructions


async def test_urgent_keyword_escalates_and_forces_urgent_ticket() -> None:
    bridge = SimulatedBridge({"oncall": TransferOutcome.ANSWERED})
    tools, rec = make_tools(cfg([office(), oncall()]), bridge, SUNDAY_10)
    assert tools.observe_user_text("There's a gas leak in the kitchen") == "gas leak"
    assert tools.observe_user_text("gas leak again") is None
    assert rec.events[0][0] == CallEventType.ESCALATION
    assert tools.availability()["available"] == ["Dave"]

    ticket = await tools.create_ticket("gas smell", "Sam", None, "low", None, None)
    assert ticket["status"] == "unsent"
    ev = rec.events[-1]
    assert ev[0] == CallEventType.TICKET_CREATED
    assert ev[1]["ticket_id"] is None
    assert ev[1]["intake"]["priority"] == "urgent"
    assert ev[1]["intake"]["caller_number"] == "+447700900000"


@pytest.mark.parametrize(
    ("text", "hit"),
    [
        ("hello there", None),
        ("a BURST PIPE upstairs", "burst pipe"),
        ("chest pain", "chest pain"),
        ("No, it's not an emergency, I'd like accounts", None),
        ("there's no flooding but there is a gas leak", "gas leak"),
        ("not sure. It's an emergency", "emergency"),
    ],
)
def test_urgent_keyword_matching(text: str, hit: str | None) -> None:
    assert TransferConfig().matches_urgent(text) == hit


@pytest.mark.parametrize(
    ("number", "said"),
    [
        ("+447930934098", "0 7 9 3 0, 9 3 4, 0 9 8"),
        ("+442046206823", "0 2 0, 4 6 2 0, 6 8 2 3"),
        ("01614960000", "0 1 6 1 4, 9 6 0, 0 0 0"),
        ("unknown", None),
        ("anonymous", None),
    ],
)
def test_spoken_number(number: str, said: str | None) -> None:
    assert spoken_number(number) == said


@pytest.mark.parametrize(
    ("spoken", "caller", "stored"),
    [
        ("07930, 934, 098", "+447930934098", "+447930934098"),
        ("0 7 9 3 0, 9 3 4, 0 9 8", "+447930934098", "+447930934098"),
        ("07881 311506", "+447930934098", "+447881311506"),
        ("+44 161 496 0000", None, "+441614960000"),
        ("the same one", "+447930934098", "+447930934098"),
        (None, None, None),
        ("(310) 555-0199", "+12125550100", "+13105550199"),
        ("212 555 0100", "+12125550100", "+12125550100"),
        ("011 44 161 496 0000", "+12125550100", "+441614960000"),
        ("087 123 4567", "+35387000000", "+353871234567"),
    ],
)
def test_normalise_number(spoken: str | None, caller: str | None, stored: str | None) -> None:
    assert normalise_number(spoken, caller) == stored


def test_normalise_number_withheld_caller_uses_tenant_country() -> None:
    assert normalise_number("(310) 555-0199", None, "1") == "+13105550199"
    assert normalise_number("07930 934098", None) == "+447930934098"


def test_caller_id_instruction_offers_own_number_or_asks() -> None:
    assert "0 7 9 3 0, 9 3 4, 0 9 8" in caller_id_instruction("+447930934098")
    assert "withheld" in caller_id_instruction(None)
    assert "withheld" in caller_id_instruction("unknown")


def test_promised_transfer_detection() -> None:
    assert mentions_connecting("I'll connect you to the accounts team now.")
    assert mentions_connecting("Let me put you through to Dave")
    assert not mentions_connecting("We're open until six today.")
    assert guess_department("connecting you to accounts", ["general", "accounts"]) == "accounts"
    assert guess_department("connecting you now", ["general", "accounts"]) is None


class FakeApi:
    """Stands in for CoreApiClient; records calls and returns canned API responses."""

    def __init__(self, fail_booking: bool = False) -> None:
        self.sms: list[dict[str, Any]] = []
        self.bookings: list[dict[str, Any]] = []
        self.fail_booking = fail_booking

    async def create(self, cfg: AssistantConfig, intake: Any) -> dict[str, Any]:
        return {"id": "tk-1", "status": "open"}

    async def send_sms(
        self,
        cfg: AssistantConfig,
        to: str,
        call_id: str,
        trigger: Any,
        context: dict[str, Any],
        body: str | None = None,
    ) -> dict[str, Any] | None:
        self.sms.append({"to": to, "trigger": trigger, "context": context})
        if trigger == "payment_link":
            return None
        return {"status": "sent"}

    services: list[dict[str, Any]] = []

    async def availability(
        self, cfg: AssistantConfig, days: int = 7, service_id: str | None = None
    ) -> dict[str, Any]:
        self.service_asked = service_id
        return {
            "slots": [{"start": "2026-09-14T09:00:00Z"}, {"start": "2026-09-14T09:30:00Z"}],
            "services": self.services,
            "service_id": service_id,
            "slot_minutes": 60,
        }

    async def book(self, cfg: AssistantConfig, req: dict[str, Any]) -> dict[str, Any]:
        if self.fail_booking:
            raise RuntimeError("down")
        self.bookings.append(req)
        return {"id": "bk-1", "start": req["start"]}

    async def admit(self, number: str, call_id: str) -> dict[str, Any]:
        return {"allowed": True}

    async def release(self, call_id: str) -> None:
        return None


def sms_cfg() -> AssistantConfig:
    from parlio_voice.models import SmsScenario, SmsTrigger

    c = cfg([office()])
    c.sms_scenarios = [
        SmsScenario(trigger=SmsTrigger.BOOKING_LINK, name="Link", template="x"),
        SmsScenario(trigger=SmsTrigger.AFTER_CALL, name="Thanks", template="y"),
        SmsScenario(trigger=SmsTrigger.ADDRESS, name="Addr", template="z", enabled=False),
    ]
    return c


async def test_send_sms_tool_uses_caller_and_reports_skips() -> None:
    from parlio_voice.tools import build_tools

    api = FakeApi()
    rec = Recorder()
    c = sms_cfg()
    tools = ReceptionistTools(
        c,
        "call-1",
        "+447700900000",
        TransferEngine(c.transfer, SimulatedBridge({})),
        api,
        rec.emit,
        rec.say,
    )
    assert tools.sms_triggers() == ["booking_link"]  # after_call is automatic, address disabled
    assert {t.info.name for t in build_tools(tools)} >= {
        "send_sms",
        "check_calendar",
        "book_appointment",
    }

    res = await tools.send_sms("booking_link", caller_name="Sam")
    assert res["status"] == "sent" and api.sms[-1]["to"] == "+447700900000"
    assert tools.sms_sent == ["booking_link"]
    assert (await tools.send_sms("payment_link"))["status"] == "skipped"
    assert (await tools.send_sms("nonsense"))["status"] == "failed"
    tools.caller = "anonymous"
    assert (await tools.send_sms("booking_link"))["status"] == "failed"


async def test_calendar_tools_book_and_degrade_gracefully() -> None:
    api = FakeApi()
    rec = Recorder()
    c = cfg([office()])
    engine = TransferEngine(c.transfer, SimulatedBridge({}))
    tools = ReceptionistTools(c, "call-1", "+447700900000", engine, api, rec.emit, rec.say)
    avail = await tools.calendar_availability()
    assert avail["slots"] == ["2026-09-14T09:00:00Z", "2026-09-14T09:30:00Z"]
    res = await tools.book_appointment("2026-09-14T09:00:00Z", "Sam")
    assert res["status"] == "booked" and tools.booking_id == "bk-1"
    assert api.bookings[0]["phone"] == "+447700900000" and api.bookings[0]["call_id"] == "call-1"

    broken = ReceptionistTools(
        c, "call-2", None, engine, FakeApi(fail_booking=True), rec.emit, rec.say
    )
    assert (await broken.book_appointment("2026-09-14T09:00:00Z", "Sam"))["status"] == "failed"
    offline = ReceptionistTools(c, "call-3", None, engine, None, rec.emit, rec.say)
    assert (await offline.calendar_availability())["slots"] == []


async def test_calendar_tools_ask_for_service_and_pass_it_through() -> None:
    api = FakeApi()
    api.services = [
        {"id": "svc-1", "name": "Repair", "minutes": 60, "description": None, "emergency": False},
        {"id": "svc-2", "name": "Service", "minutes": 90, "description": None, "emergency": False},
    ]
    rec = Recorder()
    c = cfg([office()])
    tools = ReceptionistTools(
        c, "call-1", None, TransferEngine(c.transfer, SimulatedBridge({})), api, rec.emit, rec.say
    )
    avail = await tools.calendar_availability()
    assert [s["name"] for s in avail["services"]] == ["Repair", "Service"]
    assert "Ask the caller which one" in avail["hint"]
    chosen = await tools.calendar_availability(service="svc-2")
    assert api.service_asked == "svc-2" and chosen["service_id"] == "svc-2"
    assert "hint" not in chosen
    await tools.book_appointment("2026-09-14T09:00:00Z", "Sam", service="svc-2")
    assert api.bookings[-1]["service_id"] == "svc-2"
