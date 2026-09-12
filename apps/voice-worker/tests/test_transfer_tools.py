from datetime import UTC, datetime
from typing import Any

import pytest

from parlio_voice.models import (
    AssistantConfig,
    CallEventType,
    Destination,
    Schedule,
    TransferConfig,
    TransferMode,
    TransferOutcome,
)
from parlio_voice.tools import ReceptionistTools, after_hours_instruction
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
    assert "Office" in rec.said[0] and "leaking tap" in rec.said[0]
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
    assert rec.said == []


async def test_out_of_hours_is_unavailable_and_prompts_ticket() -> None:
    c = cfg([office()])
    tools, _ = make_tools(c, SimulatedBridge(), SUNDAY_10)
    assert tools.availability()["someone_available"] is False
    res = await tools.transfer(None, "quote")
    assert res.outcome == TransferOutcome.UNAVAILABLE and not res.attempts
    assert "ticket" in after_hours_instruction(c, False).lower()
    assert after_hours_instruction(c, True) == ""


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
    [("hello there", None), ("a BURST PIPE upstairs", "burst pipe"), ("chest pain", "chest pain")],
)
def test_urgent_keyword_matching(text: str, hit: str | None) -> None:
    assert TransferConfig().matches_urgent(text) == hit
