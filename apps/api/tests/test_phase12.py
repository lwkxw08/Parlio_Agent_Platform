"""Phase 11b (browser voice + channel metering) and Phase 12 (payment links, verification)."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.auth import DEV_TENANT
from parlio_api.browser_voice import BrowserVoiceService, SimulatedDispatcher
from parlio_api.payments import (
    PaymentService,
    PaymentStatus,
    VerificationOutcome,
    payment_idempotency_key,
)
from parlio_voice.models import AssistantConfig, CallEventType, VerificationField

from .test_api import HEADERS, ev

Q = {"tenant_id": DEV_TENANT}
VISITOR = "visitor-abcdef123456"
MOBILE = "+447700900321"


async def _cfg(client: AsyncClient) -> AssistantConfig:
    return AssistantConfig.model_validate((await client.get("/v1/assistants")).json()[0])


async def _save(client: AsyncClient, cfg: AssistantConfig) -> None:
    r = await client.put("/v1/assistants/demo", json={"config": cfg.model_dump(mode="json")})
    assert r.status_code == 200, r.text


async def _enable_payments(client: AsyncClient, *, verify: bool = False) -> None:
    cfg = await _cfg(client)
    cfg.payments.enabled = True
    cfg.payments.max_pence = 20_000
    cfg.verification.enabled = verify
    cfg.verification.required_matches = 1
    cfg.verification.max_attempts = 2
    await _save(client, cfg)


async def _run_browser_call(client: AsyncClient, call_id: str, duration_s: float) -> None:
    caller = f"web:{VISITOR}"
    for e in [
        ev(
            CallEventType.CALL_STARTED,
            call_id,
            {"caller": caller, "dialed": "web", "direction": "inbound", "source": "browser"},
        ),
        ev(CallEventType.CALL_ANSWERED, call_id, {"answer_latency_s": 0.3}),
        ev(
            CallEventType.TRANSCRIPT_ITEM,
            call_id,
            {"role": "user", "text": "Hi, do you have parking?"},
        ),
        ev(CallEventType.CALL_ENDED, call_id, {"reason": "hangup", "duration_s": duration_s}),
    ]:
        r = await client.post("/v1/worker/events", json=e, headers=HEADERS)
        assert r.status_code == 202, r.text


async def _postcall(app: FastAPI) -> None:
    await app.state.postcall.drain()


# -- 11b: browser voice --------------------------------------------------------------------------


async def test_browser_voice_session_from_widget_token(client: AsyncClient, app: FastAPI) -> None:
    token = (await client.get("/v1/inbox/widget", params=Q)).json()["token"]
    pub = (await client.get(f"/v1/public/chat/{token}")).json()
    assert pub["voice_enabled"] is True

    r = await client.post(
        f"/v1/public/chat/{token}/voice",
        json={"visitor": VISITOR, "name": "Jo", "page_url": "https://example.com/pricing"},
    )
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["simulated"] is True and s["identity"] == f"web:{VISITOR}"
    assert s["room"] == s["call_id"] and s["call_id"].startswith("web-")
    assert s["token"]

    svc: BrowserVoiceService = app.state.browser_voice
    assert isinstance(svc.dispatcher, SimulatedDispatcher)
    room, meta = svc.dispatcher.dispatched[-1]
    job = json.loads(meta)["web"]
    assert room == s["call_id"]
    assert job == {
        "call_id": s["call_id"],
        "tenant_id": DEV_TENANT,
        "assistant_id": "demo",
        "visitor": VISITOR,
        "name": "Jo",
        "page_url": "https://example.com/pricing",
    }

    # bad / rotated token and disabled voice are refused
    r = await client.post("/v1/public/chat/nope/voice", json={"visitor": VISITOR})
    assert r.status_code == 404
    r = await client.patch("/v1/inbox/widget", params=Q, json={"voice_enabled": False})
    assert r.json()["voice_enabled"] is False
    r = await client.post(f"/v1/public/chat/{token}/voice", json={"visitor": VISITOR})
    assert r.status_code == 404
    r = await client.post(f"/v1/public/chat/{token}/voice", json={"visitor": "x"})
    assert r.status_code in (404, 422)


async def test_browser_call_lands_in_inbox_and_metering(client: AsyncClient, app: FastAPI) -> None:
    await _run_browser_call(client, "web-abc", 125)
    await _postcall(app)

    r = await client.get("/v1/calls/web-abc")
    assert r.status_code == 200
    call = r.json()
    assert call["caller"] == f"web:{VISITOR}" and call["dialed"] == "web"
    assert call["summary"]

    r = await client.get("/v1/inbox/threads", params={**Q, "channel": "webchat"})
    threads = r.json()
    assert len(threads) == 1 and threads[0]["identity"] == VISITOR
    msgs = (await client.get(f"/v1/inbox/threads/{threads[0]['id']}", params=Q)).json()["messages"]
    assert any("call" in m["text"].lower() for m in msgs)

    # no phone contact is created for a browser visitor
    contacts = (await client.get("/v1/contacts", params=Q)).json()
    assert all(c["e164"] != f"web:{VISITOR}" for c in contacts)

    u = (await client.get("/v1/billing/usage", params=Q)).json()
    assert u["browser_voice_calls"] == 1
    assert u["browser_voice_minutes"] == pytest.approx(2.1)
    assert u["minutes_used"] == pytest.approx(u["browser_voice_minutes"])
    assert u["phone_minutes"] == 0


async def test_webchat_messages_metered(client: AsyncClient) -> None:
    token = (await client.get("/v1/inbox/widget", params=Q)).json()["token"]
    for t in ("hello", "opening hours?"):
        r = await client.post(
            f"/v1/public/chat/{token}/messages", json={"visitor": VISITOR, "text": t}
        )
        assert r.status_code == 200, r.text
    u = (await client.get("/v1/billing/usage", params=Q)).json()
    assert u["chat_messages_used"] == 2 and u["chat_by_channel"] == {"webchat": 2}
    assert u["chat_messages_included"] > 2 and u["chat_overage_pence"] == 0
    plans = (await client.get("/v1/billing/plans")).json()
    assert "browser_voice" in plans[0]["channels"]


# -- 12: payment links ---------------------------------------------------------------------------


def _worker_link(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "assistant_id": "demo",
        "call_id": "c-pay",
        "to": MOBILE,
        "amount_pence": 5000,
        "description": "Deposit for boiler service",
        "consent": True,
    }
    body.update(over)
    return body


async def test_payment_link_requires_enabled_and_consent(client: AsyncClient) -> None:
    r = await client.post("/v1/worker/payments/link", json=_worker_link(), headers=HEADERS)
    assert r.status_code == 403
    await _enable_payments(client)
    r = await client.post(
        "/v1/worker/payments/link", json=_worker_link(consent=False), headers=HEADERS
    )
    assert r.status_code == 400 and "consent" in r.json()["detail"]
    r = await client.post(
        "/v1/worker/payments/link", json=_worker_link(amount_pence=999_999), headers=HEADERS
    )
    assert r.status_code == 400 and "limit" in r.json()["detail"]
    r = await client.post("/v1/worker/payments/link", json=_worker_link())
    assert r.status_code in (401, 403)


async def test_payment_link_sms_idempotency_and_webhook(client: AsyncClient, app: FastAPI) -> None:
    await _enable_payments(client)
    app.state.sms.from_number = "+440000000000"
    r = await client.post("/v1/worker/payments/link", json=_worker_link(), headers=HEADERS)
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["status"] == "sent" and first["amount_display"] == "£50.00"
    assert first["sms_status"] == "sent"

    # same call/customer/amount/description => same request, no second SMS
    r = await client.post("/v1/worker/payments/link", json=_worker_link(), headers=HEADERS)
    assert r.json()["id"] == first["id"]
    r = await client.post(
        "/v1/worker/payments/link", json=_worker_link(amount_pence=7500), headers=HEADERS
    )
    assert r.json()["id"] != first["id"]

    rows = (await client.get("/v1/payments", params={**Q, "call_id": "c-pay"})).json()
    assert len(rows) == 2
    p = next(x for x in rows if x["id"] == first["id"])
    assert p["provider"] == "simulated" and p["url"].startswith("http")
    assert p["call_id"] == "c-pay" and p["assistant_id"] == "demo" and p["to"] == MOBILE

    sms = (await client.get("/v1/messages", params={**Q, "call_id": "c-pay"})).json()
    links = [m for m in sms if m["trigger"] == "payment_link"]
    assert len(links) == 2 and p["url"] in links[0]["body"] + links[1]["body"]

    # other tenants cannot see it
    r = await client.get("/v1/payments", params={"tenant_id": "other"})
    assert r.status_code in (403, 404) or r.json() == []

    # provider webhook marks it paid; refund goes through the provider seam
    hook = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": p["provider_ref"],
                "payment_intent": "pi_123",
                "metadata": {"payment_id": p["id"]},
            }
        },
    }
    r = await client.post("/v1/public/payments/webhook", content=json.dumps(hook).encode())
    assert r.status_code == 200 and r.json()["status"] == "paid"
    r = await client.post(f"/v1/payments/{p['id']}/refund", params=Q, json={"amount_pence": 2000})
    assert r.status_code == 200, r.text
    assert r.json()["refunded_pence"] == 2000 and r.json()["status"] == "paid"
    r = await client.post(f"/v1/payments/{p['id']}/refund", params=Q, json={})
    assert r.json()["status"] == "refunded"
    svc: PaymentService = app.state.payments
    assert svc.provider.name == "simulated"
    r = await client.post(f"/v1/payments/{p['id']}/refund", params=Q, json={})
    assert r.status_code == 400

    # cancel an unpaid one; stale links expire
    other = next(x for x in rows if x["id"] != first["id"])
    r = await client.post(f"/v1/payments/{other['id']}/cancel", params=Q)
    assert r.json()["status"] == PaymentStatus.CANCELLED

    # no card data anywhere in what we store
    # random ids/hashes may legitimately contain "4111"; only user-facing fields are checked
    opaque = {"id", "provider_ref", "idempotency_key", "sms_message_id", "url"}
    for row in rows:
        for key, value in row.items():
            assert "card" not in key.lower() and key.lower() != "pan"
            if isinstance(value, str) and key not in opaque:
                assert "4111" not in value, (key, value)


def test_idempotency_key_context() -> None:
    base = payment_idempotency_key("t", "c", "+44", 100, "Deposit")
    assert base == payment_idempotency_key("t", "c", "+44", 100, "  deposit ")
    assert base != payment_idempotency_key("t", "c2", "+44", 100, "Deposit")
    assert base != payment_idempotency_key("t", "c", "+44", 100, "Deposit", contact_id="k")
    assert base != payment_idempotency_key("t", "c", "+44", 100, "Deposit", currency="eur")
    assert base != payment_idempotency_key("t2", "c", "+44", 100, "Deposit")


async def test_payment_requires_verified_caller_when_configured(client: AsyncClient) -> None:
    await _enable_payments(client, verify=True)
    r = await client.post("/v1/worker/payments/link", json=_worker_link(), headers=HEADERS)
    assert r.status_code == 403 and "verified" in r.json()["detail"]
    r = await client.post(
        "/v1/worker/payments/link", json=_worker_link(verified_caller=True), headers=HEADERS
    )
    assert r.status_code == 200


# -- 12: caller verification ---------------------------------------------------------------------


async def _contact(client: AsyncClient, e164: str = MOBILE) -> str:
    # a completed call creates the contact
    for e in [
        ev(CallEventType.CALL_STARTED, "c-v0", {"caller": e164, "dialed": "+440000000000"}),
        ev(CallEventType.CALL_ENDED, "c-v0", {"reason": "hangup", "duration_s": 30}),
    ]:
        await client.post("/v1/worker/events", json=e, headers=HEADERS)
    contacts = (await client.get("/v1/contacts", params=Q)).json()
    cid = next(c["id"] for c in contacts if c["e164"] == e164)
    return str(cid)


async def test_verification_flow_redacted(
    client: AsyncClient, app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    await _enable_payments(client, verify=True)
    cid = await _contact(client)
    caplog.set_level(logging.INFO)

    r = await client.put(
        f"/v1/payments/verification/contacts/{cid}",
        params=Q,
        json={"answers": {"dob": "14/03/1985", "postcode": "SW1A 1AA", "reference": "ACC-7781"}},
    )
    assert r.status_code == 200, r.text
    assert sorted(r.json()["fields"]) == ["dob", "postcode", "reference"]

    def check(answers: dict[str, str], call_id: str = "c-v1") -> dict[str, Any]:
        return {
            "assistant_id": "demo",
            "call_id": call_id,
            "caller": MOBILE,
            "answers": answers,
        }

    # wrong postcode -> failed, one attempt left
    r = await client.post(
        "/v1/worker/verification/check", json=check({"postcode": "E1 6AN"}), headers=HEADERS
    )
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == VerificationOutcome.FAILED and r.json()["attempts_left"] == 1
    # normalised match (spacing/case/date format) -> verified
    r = await client.post(
        "/v1/worker/verification/check",
        json=check({"postcode": "sw1a1aa", "dob": "1985-03-14"}),
        headers=HEADERS,
    )
    assert r.json()["outcome"] == VerificationOutcome.VERIFIED and r.json()["matched"] == 2
    # locked after max attempts on another call
    for _ in range(2):
        r = await client.post(
            "/v1/worker/verification/check",
            json=check({"dob": "01/01/1990"}, "c-v2"),
            headers=HEADERS,
        )
    assert r.json()["outcome"] == VerificationOutcome.LOCKED
    r = await client.post(
        "/v1/worker/verification/check",
        json=check({"dob": "14/03/1985"}, "c-v2"),
        headers=HEADERS,
    )
    assert r.json()["outcome"] == VerificationOutcome.LOCKED
    # unknown caller -> no_record
    r = await client.post(
        "/v1/worker/verification/check",
        json={**check({"postcode": "SW1A 1AA"}, "c-v3"), "caller": "+447700900999"},
        headers=HEADERS,
    )
    assert r.json()["outcome"] == VerificationOutcome.NO_RECORD
    # unpermitted field rejected
    r = await client.post(
        "/v1/worker/verification/check",
        json=check({"reference": "ACC-7781"}, "c-v4"),
        headers=HEADERS,
    )
    assert r.status_code == 400
    cfg = await _cfg(client)
    assert VerificationField.REFERENCE not in cfg.verification.fields

    # audit trail is redacted: fields + outcome only
    rows = (await client.get("/v1/payments/verification/calls/c-v1", params=Q)).json()
    assert [a["outcome"] for a in rows] == ["failed", "verified"]
    assert rows[1]["fields"] == ["postcode", "dob"] and rows[1]["matched"] == 2

    # raw answers are nowhere: not in stored docs, not in logs, not in the identity response
    store = app.state.store
    # random hex (ids, salt, hmac digests) and timestamps can coincidentally contain the digit
    # runs below, so only the human-readable fields are scanned for leaked raw answers
    opaque = {"id", "created_at", "updated_at", "call_id", "contact_id", "salt", "hashes"}
    blob = json.dumps(
        [
            {key: v for key, v in d.data.items() if key not in opaque}
            for k in ("contact_identity", "verification_attempt")
            for d in await store.list_docs(k, DEV_TENANT, 1000)
        ],
        default=str,
    )
    for raw in ("1985", "SW1A", "sw1a1aa", "7781", "E1 6AN"):
        assert raw not in blob
        assert raw not in caplog.text
    ident = (await client.get(f"/v1/payments/verification/contacts/{cid}", params=Q)).json()
    assert "hashes" not in ident and "salt" not in ident

    # erase
    r = await client.delete(f"/v1/payments/verification/contacts/{cid}", params=Q)
    assert r.status_code == 204
    ident = (await client.get(f"/v1/payments/verification/contacts/{cid}", params=Q)).json()
    assert ident["fields"] == []
    r = await client.put(
        f"/v1/payments/verification/contacts/{cid}",
        params={"tenant_id": "other"},
        json={"answers": {"postcode": "X"}},
    )
    assert r.status_code in (403, 404)
