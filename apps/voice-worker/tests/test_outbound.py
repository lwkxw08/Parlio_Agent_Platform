"""Outbound worker mode: dispatch metadata, SIP dial result mapping, outcome reporting."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from livekit import api

from parlio_voice.config_client import DEMO_CONFIG
from parlio_voice.outbound import (
    OutboundJob,
    OutcomeReporter,
    dial_callee,
    outcome_tool,
    parse_outbound,
)
from parlio_voice.tools import ReceptionistTools, build_tools
from parlio_voice.transfer import SimulatedBridge, TransferEngine

META = {
    "outbound": {
        "call_id": "out-ob-1-1",
        "job_id": "ob-1",
        "tenant_id": "demo",
        "assistant_id": "demo",
        "purpose": "lead_followup",
        "to": "+447700900123",
        "from": "+442046206823",
        "name": "Lena",
        "trunk_id": "ST_1",
        "script": {
            "opening": "Hi, is that Lena?",
            "goal": "Qualify the lead.",
            "common": "Be brief.",
        },
        "context": {"interest": "rewire"},
    }
}


def test_parse_outbound_metadata() -> None:
    assert parse_outbound(None) is None
    assert parse_outbound("not json") is None
    assert parse_outbound(json.dumps({"other": 1})) is None
    job = parse_outbound(json.dumps(META))
    assert job is not None
    assert job.from_number == "+442046206823" and job.to == "+447700900123"
    assert job.opening == "Hi, is that Lena?"
    text = job.instructions()
    assert "Qualify the lead." in text and "interest: rewire" in text and "record_outcome" in text
    bad = {"outbound": {"job_id": "x"}}
    assert parse_outbound(json.dumps(bad)) is None


class FakeSip:
    def __init__(self, error: str | None = None, hang: bool = False) -> None:
        self.error, self.hang = error, hang
        self.requests: list[Any] = []

    async def create_sip_participant(self, req: Any, **_: Any) -> Any:
        self.requests.append(req)
        if self.hang:
            raise TimeoutError
        if self.error:
            raise api.TwirpError(code="unavailable", msg=self.error, status=503)
        return object()


class FakeLK:
    def __init__(self, sip: FakeSip) -> None:
        self.sip = sip


def _job() -> OutboundJob:
    j = parse_outbound(json.dumps(META))
    assert j is not None
    return j


@pytest.mark.parametrize(
    ("error", "hang", "expected"),
    [
        (None, False, "answered"),
        ("486 Busy Here", False, "busy"),
        ("call declined", False, "busy"),
        ("ringing timeout", False, "no_answer"),
        (None, True, "no_answer"),
        ("503 service unavailable", False, "failed"),
    ],
)
async def test_dial_callee_maps_sip_results(error: str | None, hang: bool, expected: str) -> None:
    sip = FakeSip(error, hang)
    result = await dial_callee(FakeLK(sip), "out-ob-1-1", _job(), ring_timeout_s=1)  # type: ignore[arg-type]
    assert result == expected
    req = sip.requests[0]
    assert req.sip_trunk_id == "ST_1" and req.sip_call_to == "+447700900123"
    assert req.sip_number == "+442046206823" and req.wait_until_answered is True
    assert req.participant_identity == "callee"


async def test_dial_without_trunk_fails_fast() -> None:
    job = _job().model_copy(update={"trunk_id": None})
    sip = FakeSip()
    assert await dial_callee(FakeLK(sip), "r", job) == "failed"  # type: ignore[arg-type]
    assert sip.requests == []


async def test_outcome_reporter_posts_and_tolerates_api_down() -> None:
    posted: list[tuple[str, dict[str, Any]]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        posted.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"id": "ob-1"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api")
    rep = OutcomeReporter(http, _job())
    assert await rep.record("booked", "Tue 10:00") == {"ok": True}
    assert posted == [
        ("/v1/worker/outbound/ob-1/outcome", {"outcome": "booked", "detail": "Tue 10:00"})
    ]
    assert rep.outcome == "booked"
    bad = await rep.record("made_up")
    assert bad["ok"] is False and "unknown outcome" in bad["error"]
    assert posted[-1][1]["outcome"] == "booked"  # nothing new posted

    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    rep2 = OutcomeReporter(httpx.AsyncClient(transport=httpx.MockTransport(down)), _job())
    assert await rep2.record("opt_out") == {"ok": False, "recorded_locally": True}
    assert rep2.outcome == "opt_out"


async def test_record_outcome_tool_only_in_outbound_mode() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})), base_url="http://api"
    )
    emitted: list[Any] = []

    async def say(_: str) -> None:
        return None

    def tools(rep: OutcomeReporter | None) -> ReceptionistTools:
        return ReceptionistTools(
            DEMO_CONFIG,
            "c1",
            "+447700900123",
            TransferEngine(DEMO_CONFIG.transfer, SimulatedBridge()),
            None,
            lambda t, p: emitted.append((t, p)),
            say,
            reporter=rep,
        )

    names = {t.info.name for t in build_tools(tools(None))}
    assert "record_outcome" not in names
    rep = OutcomeReporter(http, _job())
    names = {t.info.name for t in build_tools(tools(rep))}
    assert "record_outcome" in names
    tool = outcome_tool(rep)
    assert (await tool("voicemail", "left message"))["ok"] is True
    assert rep.outcome == "voicemail"
