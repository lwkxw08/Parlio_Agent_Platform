"""Browser voice job metadata (11b) and payment / verification tools (12)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from parlio_voice.config_client import DEMO_CONFIG
from parlio_voice.models import CallEventType, VerificationField
from parlio_voice.tools import CoreApiClient, ReceptionistTools, build_tools
from parlio_voice.transfer import SimulatedBridge, TransferEngine
from parlio_voice.web import WEB_DIALED, parse_web

META = {
    "web": {
        "call_id": "web-abc",
        "tenant_id": "demo",
        "assistant_id": "demo",
        "visitor": "visitor-abcdef123456",
        "name": "Jo",
        "page_url": "https://example.com/pricing",
    }
}


def test_parse_web_metadata(caplog: pytest.LogCaptureFixture) -> None:
    assert parse_web(None) is None
    assert parse_web("not json") is None
    assert parse_web(json.dumps({"outbound": {}})) is None
    job = parse_web(json.dumps(META))
    assert job is not None
    assert job.caller == "web:visitor-abcdef123456" and WEB_DIALED == "web"
    text = job.instructions()
    assert "website" in text and "Jo" in text and "example.com/pricing" in text
    assert "mobile number" in text
    caplog.set_level(logging.WARNING)
    assert parse_web(json.dumps({"web": {"call_id": "x", "secret": "s3cr3t"}})) is None
    assert "s3cr3t" not in caplog.text


def _tools(handler: Any, *, caller: str = "+447700900123", cfg: Any = None) -> tuple[Any, list]:
    cfg = cfg or DEMO_CONFIG.model_copy(deep=True)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api")
    emitted: list[tuple[CallEventType, dict[str, Any]]] = []

    async def say(_: str) -> None:
        return None

    t = ReceptionistTools(
        cfg,
        "c1",
        caller,
        TransferEngine(cfg.transfer, SimulatedBridge()),
        CoreApiClient(http),
        lambda ty, p: emitted.append((ty, p)),
        say,
    )
    return t, emitted


def _paid_cfg(*, verify: bool = False) -> Any:
    cfg = DEMO_CONFIG.model_copy(deep=True)
    cfg.payments.enabled = True
    cfg.payments.max_pence = 10_000
    cfg.verification.enabled = verify
    cfg.verification.fields = [VerificationField.POSTCODE, VerificationField.DOB]
    return cfg


def test_tools_only_when_enabled() -> None:
    t, _ = _tools(lambda _: httpx.Response(200, json={}))
    names = {x.info.name for x in build_tools(t)}
    assert "send_payment_link" not in names and "verify_caller" not in names
    t, _ = _tools(lambda _: httpx.Response(200, json={}), cfg=_paid_cfg(verify=True))
    names = {x.info.name for x in build_tools(t)}
    assert {"send_payment_link", "verify_caller"} <= names


async def test_verify_caller_redacted(caplog: pytest.LogCaptureFixture) -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        ok = body["answers"].get("postcode") == "SW1A 1AA"
        return httpx.Response(
            200,
            json={
                "outcome": "verified" if ok else "failed",
                "matched": 1 if ok else 0,
                "attempts_left": 1,
            },
        )

    caplog.set_level(logging.DEBUG)
    t, emitted = _tools(handler, cfg=_paid_cfg(verify=True))
    # reference isn't a configured field -> dropped before the request
    res = await t.verify_caller({"postcode": "E1 6AN", "reference": "ACC-1"})
    assert res["outcome"] == "failed" and t.verified is False
    assert seen[-1]["answers"] == {"postcode": "E1 6AN"} and seen[-1]["caller"] == "+447700900123"
    res = await t.verify_caller({"postcode": "SW1A 1AA"})
    assert res["outcome"] == "verified" and t.verified is True
    assert [e[0] for e in emitted] == [CallEventType.CALLER_VERIFIED] * 2
    dump = json.dumps([e[1] for e in emitted]) + caplog.text
    assert "SW1A" not in dump and "E1 6AN" not in dump and "ACC-1" not in dump
    assert emitted[1][1]["fields"] == ["postcode"]

    locked, _ = _tools(
        lambda _: httpx.Response(200, json={"outcome": "locked", "attempts_left": 0}),
        cfg=_paid_cfg(verify=True),
    )
    assert (await locked.verify_caller({"postcode": "x"}))["outcome"] == "locked"
    assert (await locked.verify_caller({"postcode": "SW1A 1AA"}))["outcome"] == "locked"
    assert locked.verification_locked is True

    assert (await t.verify_caller({"reference": "only"}))["outcome"] == "failed"


async def test_send_payment_link_guards_and_success() -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        if body["amount_pence"] == 4200:
            return httpx.Response(403, json={"detail": "caller must be verified first"})
        return httpx.Response(
            200,
            json={
                "id": "pay-1",
                "status": "sent",
                "sms_status": "sent",
                "amount_display": "£25.00",
                "url": "https://pay.example/x",
            },
        )

    t, emitted = _tools(handler, cfg=_paid_cfg())
    assert (await t.send_payment_link(25, "Deposit", None, False))["status"] == "failed"
    assert (await t.send_payment_link(0, "Deposit", None, True))["status"] == "failed"
    assert "limit" in (await t.send_payment_link(500, "Deposit", None, True))["error"]
    assert seen == []

    res = await t.send_payment_link(25, "Deposit", None, True)
    assert res == {"status": "sent", "amount": "£25.00", "expires_at": None}
    assert seen[-1]["to"] == "+447700900123" and seen[-1]["amount_pence"] == 2500
    assert seen[-1]["consent"] is True and "card" not in json.dumps(seen[-1]).lower()
    assert t.payment_ids == ["pay-1"]
    assert emitted[-1][0] == CallEventType.PAYMENT_REQUESTED
    assert emitted[-1][1]["payment_id"] == "pay-1" and "url" not in emitted[-1][1]

    # API refusal surfaces its detail
    res = await t.send_payment_link(42, "Deposit", None, True)
    assert res["status"] == "failed" and "verified" in res["error"]

    # verification required locally before calling the API
    tv, _ = _tools(handler, cfg=_paid_cfg(verify=True))
    res = await tv.send_payment_link(25, "Deposit", None, True)
    assert res["status"] == "failed" and "verify_caller" in res["error"]

    # browser visitors have no caller ID: need a mobile number
    tw, _ = _tools(handler, caller="web:visitor-abcdef123456", cfg=_paid_cfg())
    res = await tw.send_payment_link(25, "Deposit", None, True)
    assert res["status"] == "failed" and "mobile" in res["error"]
    res = await tw.send_payment_link(25, "Deposit", "+447700900999", True)
    assert res["status"] == "sent" and seen[-1]["to"] == "+447700900999"

    off, _ = _tools(handler)
    assert (await off.send_payment_link(25, "Deposit", None, True))["status"] == "unavailable"
