import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from livekit import rtc

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
    asks_for_callback,
    asks_for_transfer,
    booking_first_instruction,
    build_tools,
    caller_id_instruction,
    declines_transfer,
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
    briefing = rec.said[1]
    assert briefing.startswith("Hi, this is") and "Hi Office" not in briefing
    assert "leaking tap" in briefing and "0 7 7 0 0" in briefing
    assert briefing.endswith("Press any key to take the call.")
    assert rec.said[-1].startswith("Thank you, putting them through")
    assert tools.transfer_attempted
    types = [t for t, _ in rec.events]
    assert types == [CallEventType.TRANSFER_STARTED, CallEventType.TRANSFER_COMPLETED]
    assert rec.events[1][1]["outcome"] == TransferOutcome.ANSWERED


async def test_voicemail_answer_without_keypress_is_not_a_transfer() -> None:
    bridge = SimulatedBridge({"office": TransferOutcome.ANSWERED}, accepts={"office": False})
    tools, rec = make_tools(cfg([office()]), bridge, MONDAY_10)
    res = await tools.transfer(None, "boiler")
    assert not res.succeeded and res.outcome == TransferOutcome.VOICEMAIL
    assert res.connected is None and not tools.transferred and not bridge.left
    assert len(bridge.dropped) == 1 and bridge.dropped[0].startswith("human-office-")
    assert res.attempts[0].detail == "no keypress after answer"
    assert not any(s.startswith("Thank you") for s in rec.said)
    # Caller was on hold for the briefing + voicemail greeting, then released.
    assert bridge.holds == [True, False]
    outcomes = [p["outcome"] for t, p in rec.events if t == CallEventType.TRANSFER_COMPLETED]
    assert outcomes == [TransferOutcome.VOICEMAIL]


async def test_voicemail_on_first_destination_rings_the_fallback() -> None:
    bridge = SimulatedBridge(accepts={"office": False})
    tools, _ = make_tools(cfg([office(fallback_id="oncall"), oncall()]), bridge, MONDAY_10)
    res = await tools.transfer("general", "boiler")
    assert res.succeeded and res.connected and res.connected.id == "oncall"
    assert bridge.dialed == ["office", "oncall"] and len(bridge.dropped) == 1


async def test_accept_key_off_bridges_on_answer() -> None:
    bridge = SimulatedBridge(accepts={"office": False})
    tools, rec = make_tools(cfg([office()], accept_key=False), bridge, MONDAY_10)
    res = await tools.transfer(None, "boiler")
    assert res.succeeded and bridge.left and not bridge.dropped
    assert rec.said[-1].endswith("Putting them through now.")
    assert bridge.holds == []


async def test_caller_hangup_during_transfer_drops_the_human_leg() -> None:
    bridge = SimulatedBridge()
    tools, _ = make_tools(cfg([office()]), bridge, MONDAY_10)
    tools.caller_gone = True
    res = await tools.transfer(None, "boiler")
    assert not res.succeeded and res.outcome == TransferOutcome.NO_ANSWER
    assert not tools.transferred and not bridge.left
    assert len(bridge.dropped) == 1 and bridge.dropped[0].startswith("human-office-")


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
    # Asking about a transfer is not promising one.
    assert not mentions_connecting(
        "Could you let me know the reason so I can transfer you to the right department?"
    )
    assert not mentions_connecting("Would you like me to put you through to accounts?")
    assert not mentions_connecting("Before I connect you, can I take your name?")
    assert mentions_connecting("Thanks Keith. Connecting you to accounts now, okay?")
    assert guess_department("connecting you to accounts", ["general", "accounts"]) == "accounts"
    assert guess_department("connecting you now", ["general", "accounts"]) is None


def test_caller_intent_phrases() -> None:
    assert asks_for_callback("Hi. Can I book a plumber callback, please?")
    assert asks_for_callback("could someone call me back this afternoon")
    assert not asks_for_callback("I'd just like to speak to a plumber for some recommendation.")
    assert asks_for_transfer("can you put me through to accounts")
    assert asks_for_transfer("I want to speak to someone now")
    assert not asks_for_transfer("I'd just like to speak to a plumber for some recommendation.")
    assert declines_transfer("No. Please call back.")
    assert declines_transfer("don't transfer me, just call back")
    assert not declines_transfer("yes please put me through")


def test_callback_in_progress_blocks_transfer_safety_net_and_tool() -> None:
    bridge = SimulatedBridge({"office": TransferOutcome.ANSWERED})
    tools, _ = make_tools(cfg([office()]), bridge, MONDAY_10)
    # Nothing asked for yet: the safety net must not dial on the assistant's wording alone.
    assert not tools.transfer_allowed_without_tool_call()
    tools.observe_user_text("Hi. Can I book a plumber callback, please?")
    tools.observe_user_text("Keith Wilson.")
    tools.observe_user_text("I'd just like to speak to a plumber for some recommendation.")
    assert tools.callback_requested and not tools.transfer_requested
    assert not tools.transfer_allowed_without_tool_call()
    tools.observe_user_text("No. Please call back.")
    assert tools.callback_requested and not tools.transfer_allowed_without_tool_call()


async def test_transfer_tool_refuses_during_callback_intake() -> None:
    bridge = SimulatedBridge({"office": TransferOutcome.ANSWERED})
    tools, rec = make_tools(cfg([office()]), bridge, MONDAY_10)
    tools.observe_user_text("can someone call me back please")
    fn = next(x for x in build_tools(tools) if x.info.name == "transfer_to_human")
    out = await fn(reason="wants advice")
    assert out["outcome"] == "not_transferred" and "create_ticket" in out["note"]
    assert bridge.dialed == [] and not tools.transfer_attempted and rec.events == []
    # The caller changes their mind and asks to be put through: now it goes ahead.
    tools.observe_user_text("actually, can you put me through to someone now")
    assert tools.transfer_requested and not tools.callback_requested
    assert tools.transfer_allowed_without_tool_call()
    out = await fn(reason="wants advice")
    assert out["outcome"] == TransferOutcome.ANSWERED and bridge.dialed == ["office"]


def test_explicit_transfer_request_still_allows_safety_net() -> None:
    bridge = SimulatedBridge({"office": TransferOutcome.ANSWERED})
    tools, _ = make_tools(cfg([office()]), bridge, MONDAY_10)
    tools.observe_user_text("put me through to the office please")
    assert tools.transfer_allowed_without_tool_call()
    tools.observe_user_text("no, don't transfer me")
    assert not tools.transfer_allowed_without_tool_call()


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


class _FakeRoom:
    """Minimal rtc.Room stand-in: on/off registration + manual emit."""

    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}

    def on(self, event: str, fn: Any) -> None:
        self.handlers.setdefault(event, []).append(fn)

    def off(self, event: str, fn: Any) -> None:
        self.handlers[event].remove(fn)

    def emit(self, event: str, *args: Any) -> None:
        for fn in list(self.handlers.get(event, [])):
            fn(*args)


class _P:
    def __init__(self, identity: str) -> None:
        self.identity = identity


async def test_livekit_confirm_human_keypress_hangup_and_timeout() -> None:
    from parlio_voice.transfer import LiveKitSipBridge

    room = _FakeRoom()
    bridge = LiveKitSipBridge(None, "room", "caller", "trunk", room=room)  # type: ignore[arg-type]

    async def press(identity: str, digit: str) -> None:
        await asyncio.sleep(0)
        dtmf = rtc.SipDTMF(code=1, digit=digit, participant=_P(identity))  # type: ignore[arg-type]
        room.emit("sip_dtmf_received", dtmf)

    # the caller pressing a key does not count; the human pressing one does
    task = asyncio.create_task(bridge.confirm_human("human-a-1", 1))
    await press("caller", "1")
    await press("human-a-1", "5")
    assert await task is True
    # the human hanging up (voicemail cut off / declined) is a no
    task = asyncio.create_task(bridge.confirm_human("human-a-2", 1))
    await asyncio.sleep(0)
    room.emit("participant_disconnected", _P("human-a-2"))
    assert await task is False
    # silence (a voicemail greeting) times out
    assert await bridge.confirm_human("human-a-3", 0) is False
    assert room.handlers["sip_dtmf_received"] == []
