"""Phase 10: supervisor control channel + owner-approval tool inside the worker."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from parlio_voice.models import AssistantConfig, CallEventType
from parlio_voice.supervisor import CONTROL_TOPIC, Command, Supervisor, parse_control
from parlio_voice.tools import ReceptionistTools, build_tools, request_owner_approval
from parlio_voice.transfer import SimulatedBridge, TransferEngine


class FakeSession:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.notes: list[str] = []
        self.audio = True
        self.interrupts = 0

    async def say(self, text: str) -> None:
        self.said.append(text)

    async def add_system_note(self, text: str) -> None:
        self.notes.append(text)

    def set_audio(self, enabled: bool) -> None:
        self.audio = enabled

    async def interrupt(self) -> None:
        self.interrupts += 1


class Rec:
    def __init__(self) -> None:
        self.events: list[tuple[CallEventType, dict[str, object]]] = []
        self.hangups: list[str] = []

    def emit(self, t: CallEventType, p: dict[str, object]) -> None:
        self.events.append((t, p))

    async def hangup(self, reason: str) -> None:
        self.hangups.append(reason)


def _frame(cmd: str, text: str | None = None, by: str = "Keith") -> bytes:
    return json.dumps({"cmd": cmd, "text": text, "by": by}).encode()


async def test_supervisor_commands_drive_the_session() -> None:
    s, rec = FakeSession(), Rec()
    sup = Supervisor(s, rec.emit, rec.hangup)

    await sup.handle(parse_control(_frame("whisper", "offer the discount")))  # type: ignore[arg-type]
    assert sup.whispers == ["offer the discount"]
    assert "offer the discount" in s.notes[0] and "cannot hear" in s.notes[0]
    assert s.said == []

    await sup.handle(parse_control(_frame("say", "One moment please.")))  # type: ignore[arg-type]
    assert s.said == ["One moment please."]

    await sup.handle(parse_control(_frame("takeover")))  # type: ignore[arg-type]
    assert sup.taken_over and sup.taken_over_by == "Keith"
    assert s.audio is False and s.interrupts == 1
    await sup.handle(parse_control(_frame("takeover")))  # type: ignore[arg-type]
    assert s.interrupts == 1  # idempotent

    await sup.handle(parse_control(_frame("handback")))  # type: ignore[arg-type]
    assert not sup.taken_over and s.audio is True
    assert "handed the call back" in s.notes[-1]

    await sup.handle(parse_control(_frame("hangup")))  # type: ignore[arg-type]
    assert rec.hangups == ["supervisor_hangup"]

    cmds = [p["cmd"] for t, p in rec.events if t == CallEventType.SUPERVISOR]
    assert cmds == ["whisper", "say", "takeover", "takeover", "handback", "hangup"]
    # whisper text never leaves the worker (it would end up in the caller-visible transcript)
    assert rec.events[0][1]["text"] is None
    assert rec.events[1][1]["text"] == "One moment please."


async def test_supervisor_ignores_foreign_and_malformed_frames() -> None:
    s, rec = FakeSession(), Rec()
    sup = Supervisor(s, rec.emit, rec.hangup)
    sup.on_data(_frame("hangup"), "other.topic", None)
    sup.on_data(_frame("hangup"), CONTROL_TOPIC, "supervisor-abc")  # participants can't command
    sup.on_data(b"not json", CONTROL_TOPIC, None)
    sup.on_data(json.dumps({"cmd": "explode"}).encode(), CONTROL_TOPIC, None)
    await asyncio.sleep(0)
    assert rec.hangups == [] and rec.events == []

    sup.on_data(_frame("say", "hi"), CONTROL_TOPIC, None)
    await asyncio.sleep(0.01)
    assert s.said == ["hi"]
    assert parse_control(_frame("whisper", "x")).cmd is Command.WHISPER  # type: ignore[union-attr]


# -- approval tool ----------------------------------------------------------------------------


class FakeApprovalsApi:
    def __init__(self, decisions: list[str]) -> None:
        self.decisions = decisions
        self.requests: list[dict[str, Any]] = []
        self.polls = 0

    async def request_approval(self, cfg: AssistantConfig, req: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(req)
        return {"id": "ap-1", "status": "pending", **req}

    async def poll_approval(
        self, cfg: AssistantConfig, approval_id: str, wait_s: float
    ) -> dict[str, Any]:
        self.polls += 1
        status = self.decisions.pop(0) if self.decisions else "pending"
        return {
            "id": approval_id,
            "status": status,
            "note": "fine" if status != "pending" else None,
        }


def _tools(api: Any) -> tuple[ReceptionistTools, list[str], list[tuple[CallEventType, Any]]]:
    c = AssistantConfig(assistant_id="a", tenant_id="t", company_id="c", business_name="Acme")
    said: list[str] = []
    events: list[tuple[CallEventType, Any]] = []

    async def say(text: str) -> None:
        said.append(text)

    t = ReceptionistTools(
        c,
        "call-1",
        "+447700900000",
        TransferEngine(c.transfer, SimulatedBridge({})),
        api,
        lambda k, p: events.append((k, p)),
        say,
    )
    return t, said, events


async def test_request_owner_approval_waits_for_decision() -> None:
    api = FakeApprovalsApi(["pending", "approved"])
    t, said, events = _tools(api)
    res = await request_owner_approval(t, "quote", "Rewire", "3 bed", 4200.0, wait_s=5)
    assert res == {"status": "approved", "note": "fine", "approval_id": "ap-1"}
    assert api.requests[0]["kind"] == "quote" and api.requests[0]["call_id"] == "call-1"
    assert api.requests[0]["caller"] == "+447700900000"
    assert api.polls == 2
    assert said and "check that with the team" in said[0]
    assert events[0][0] is CallEventType.APPROVAL_REQUESTED
    assert t.approvals[0]["id"] == "ap-1"
    assert "request_owner_approval" in {x.info.name for x in build_tools(t)}


async def test_request_owner_approval_times_out_and_degrades() -> None:
    api = FakeApprovalsApi([])
    t, _, _ = _tools(api)
    res = await request_owner_approval(t, "weird", "x", "", None, wait_s=0.01)
    assert res["status"] == "timed_out" and api.requests[0]["kind"] == "other"

    offline, _, _ = _tools(None)
    res = await request_owner_approval(offline, "refund", "x", "", 10.0)
    assert res["status"] == "unavailable"
    assert "request_owner_approval" not in {x.info.name for x in build_tools(offline)}
