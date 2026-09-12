"""Phase 6 — billing, numbers, observability, compliance, audit. Simulated providers only:
no Stripe or Telnyx traffic, and nothing here depends on the (still pending) live UK number.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from parlio_api.billing import PLAN_BY_ID, StripeBilling, billable_minutes
from parlio_api.compliance import mask_e164, redact_text, redact_transcript
from parlio_api.observability import RateLimiter, action_for
from parlio_api.store import CallRecord, CallRedaction
from parlio_voice.models import CallEventType

from .test_api import HEADERS, ev

pytestmark = pytest.mark.anyio

DEMO = {"tenant_id": "demo"}
STRANGER = {"X-Parlio-User": "who@example.com"}


async def _run_call(
    client: AsyncClient,
    call_id: str,
    duration_s: float,
    caller: str = "+447700900001",
    tenant: str = "demo",
    text: str = "Hi, my card is 4111 1111 1111 1111 and email bob@example.com",
    answer_latency_s: float = 0.4,
    latency: dict[str, Any] | None = None,
) -> None:
    events = [
        ev(
            CallEventType.CALL_STARTED,
            call_id,
            {"caller": caller, "dialed": "+440000000000"},
            tenant,
        ),
        ev(CallEventType.CALL_ANSWERED, call_id, {"answer_latency_s": answer_latency_s}, tenant),
        ev(CallEventType.TRANSCRIPT_ITEM, call_id, {"role": "user", "text": text}, tenant),
        ev(
            CallEventType.CALL_ENDED,
            call_id,
            {
                "reason": "hangup",
                "duration_s": duration_s,
                "latency": latency or {"p50_s": 0.9, "p95_s": 1.3, "turns": 6},
            },
            tenant,
        ),
    ]
    for e in events:
        r = await client.post("/v1/worker/events", json=e, headers=HEADERS)
        assert r.status_code == 202, r.text


# -- unit: metering & plans --------------------------------------------------------------------


def _call(duration: float | None, answered: bool = True) -> CallRecord:
    now = datetime.now(UTC)
    return CallRecord(
        call_id="x",
        tenant_id="t",
        company_id="t",
        assistant_id="a",
        answered_at=now if answered else None,
        duration_s=duration,
        status="completed",
    )


def test_billable_minutes_rounds_to_six_second_blocks() -> None:
    assert billable_minutes(_call(0)) == 0.0
    assert billable_minutes(_call(1)) == 0.1
    assert billable_minutes(_call(6)) == 0.1
    assert billable_minutes(_call(6.01)) == 0.2
    assert billable_minutes(_call(60)) == 1.0
    assert billable_minutes(_call(61)) == 1.1
    assert billable_minutes(_call(42, answered=False)) == 0.0


def test_plans_are_priced_sanely() -> None:
    assert PLAN_BY_ID["starter"].monthly_pence < PLAN_BY_ID["growth"].monthly_pence
    assert PLAN_BY_ID["growth"].monthly_pence < PLAN_BY_ID["scale"].monthly_pence
    assert PLAN_BY_ID["enterprise"].enterprise


def test_stripe_signature_verification() -> None:
    sb = StripeBilling("sk_test_x", "whsec_test")
    payload = json.dumps({"type": "invoice.paid", "data": {"object": {}}}).encode()
    ts = int(time.time())
    sig = hmac.new(b"whsec_test", f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    assert sb.verify_webhook(payload, f"t={ts},v1={sig}")["type"] == "invoice.paid"
    with pytest.raises(ValueError):
        sb.verify_webhook(payload, f"t={ts},v1={'0' * 64}")
    with pytest.raises(ValueError):
        sb.verify_webhook(payload, None)
    old = ts - 3600
    old_sig = hmac.new(b"whsec_test", f"{old}.".encode() + payload, hashlib.sha256).hexdigest()
    with pytest.raises(ValueError):
        sb.verify_webhook(payload, f"t={old},v1={old_sig}")


# -- unit: redaction / rate limiter / audit naming -------------------------------------------


def test_redaction_patterns() -> None:
    s = redact_text(
        "Card 4111 1111 1111 1111, email bob@example.com, call 07700 900123, NI AB123456C, "
        "sort code 12-34-56, postcode SW1A 1AA"
    )
    assert "4111" not in s and "[card]" in s
    assert "bob@" not in s and "[email]" in s
    assert "07700" not in s and "[phone]" in s
    assert "AB123456C" not in s
    assert "12-34-56" not in s
    assert "SW1A" not in s
    assert redact_text("Just a normal sentence about plumbing.") == (
        "Just a normal sentence about plumbing."
    )
    items = redact_transcript([{"role": "user", "text": "my email is a@b.co", "t": 1.0}])
    assert items[0]["text"] == "my email is [email]" and items[0]["t"] == 1.0
    assert mask_e164("+447700900123") == "+447700******"
    assert mask_e164(None) is None


def test_rate_limiter_window() -> None:
    rl = RateLimiter(per_minute=3)
    assert [rl.check("t", now=0.0)[0] for _ in range(3)] == [True, True, True]
    allowed, remaining = rl.check("t", now=1.0)
    assert not allowed and remaining == 0
    assert rl.check("other", now=1.0)[0]
    assert rl.check("t", now=61.0)[0]


def test_action_for_names() -> None:
    assert action_for("POST", "/v1/assistants") == "assistants.create"
    assert action_for("DELETE", "/v1/telephony/trunks/trk_0123456789") == "telephony.trunks.delete"
    assert action_for("POST", "/v1/assistants/a1/rollback") == "assistants.rollback"
    assert action_for("PUT", "/v1/compliance/retention") == "compliance.retention.update"


# -- API: billing & usage --------------------------------------------------------------------


async def test_plans_subscription_and_usage(client: AsyncClient) -> None:
    r = await client.get("/v1/billing/plans")
    assert r.status_code == 200 and [p["id"] for p in r.json()][:2] == ["starter", "growth"]

    r = await client.get("/v1/billing/subscription", params=DEMO)
    assert r.status_code == 200
    assert r.json()["plan_id"] == "starter" and r.json()["status"] == "trialing"

    await _run_call(client, "b1", 61)  # 1.1 min
    await _run_call(client, "b2", 600)  # 10 min
    r = await client.get("/v1/billing/usage", params=DEMO)
    assert r.status_code == 200
    u = r.json()
    assert u["calls"] == 2 and u["minutes_used"] == pytest.approx(11.1)
    assert u["minutes_overage"] == 0 and u["estimated_total_pence"] == u["base_pence"]
    assert u["vendor_cost_pence"] > 0 and u["gross_margin_pct"] is not None
    assert len(u["top_calls"]) == 2 and u["top_calls"][0]["call_id"] == "b2"


async def test_coupon_and_plan_change(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/billing/coupon", params=DEMO, json={"code": "launch50", "plan_id": "growth"}
    )
    assert r.status_code == 200 and r.json()["percent_off"] == 50
    r = await client.post(
        "/v1/billing/coupon", params=DEMO, json={"code": "NOPE", "plan_id": "growth"}
    )
    assert r.status_code == 404

    r = await client.post(
        "/v1/billing/subscription", params=DEMO, json={"plan_id": "growth", "coupon": "LAUNCH50"}
    )
    assert (
        r.status_code == 200
        and r.json()["plan_id"] == "growth"
        and r.json()["coupon"] == "LAUNCH50"
    )
    u = (await client.get("/v1/billing/usage", params=DEMO)).json()
    assert u["base_pence"] == 14900 and u["discount_pence"] == 7450
    assert u["estimated_total_pence"] == 7450

    r = await client.post("/v1/billing/subscription", params=DEMO, json={"plan_id": "enterprise"})
    assert r.status_code == 422
    r = await client.post("/v1/billing/subscription", params=DEMO, json={"plan_id": "nope"})
    assert r.status_code == 422

    # audit trail written, admin-only
    r = await client.get("/v1/audit", params=DEMO)
    assert r.status_code == 200
    assert "billing.plan.change" in {a["action"] for a in r.json()}
    r = await client.get("/v1/audit", params=DEMO, headers=STRANGER)
    assert r.status_code == 403


async def test_overage_is_charged_on_starter(client: AsyncClient) -> None:
    included = PLAN_BY_ID["starter"].included_minutes
    await _run_call(client, "o1", (included + 10) * 60)
    u = (await client.get("/v1/billing/usage", params=DEMO)).json()
    assert u["minutes_overage"] == pytest.approx(10.0)
    assert u["overage_pence"] == 10 * PLAN_BY_ID["starter"].overage_pence_per_minute
    assert u["estimated_total_pence"] == u["base_pence"] + u["overage_pence"]


async def test_simulated_checkout_activates(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/billing/checkout",
        params=DEMO,
        json={"plan_id": "growth", "return_url": "https://app.example/billing"},
    )
    assert r.status_code == 200
    assert r.json()["provider"] == "simulated" and r.json()["url"].startswith("https://")
    sub = (await client.get("/v1/billing/subscription", params=DEMO)).json()
    assert sub["status"] == "active" and sub["plan_id"] == "growth"

    # simulated webhook path (no signature needed in simulated mode)
    r = await client.post(
        "/v1/public/billing/webhook",
        content=json.dumps(
            {
                "type": "customer.subscription.deleted",
                "data": {
                    "object": {"customer": sub["customer_ref"], "id": sub["subscription_ref"]}
                },
            }
        ),
    )
    assert r.status_code == 200 and r.json()["handled"] == "customer.subscription.deleted"
    assert (await client.get("/v1/billing/subscription", params=DEMO)).json()["status"] == (
        "cancelled"
    )


async def test_billing_is_tenant_scoped(client: AsyncClient) -> None:
    for path in ("/v1/billing/subscription", "/v1/billing/usage", "/v1/numbers"):
        r = await client.get(path, params={"tenant_id": "someone-else"})
        assert r.status_code == 403, path


# -- API: numbers --------------------------------------------------------------------------------


async def test_number_provisioning_simulated(client: AsyncClient) -> None:
    r = await client.get("/v1/numbers/search", params={**DEMO, "country": "GB", "limit": 3})
    assert r.status_code == 200 and len(r.json()) == 3
    cand = r.json()[0]["e164"]
    assert cand.startswith("+4420")

    r = await client.post(
        "/v1/numbers", params=DEMO, json={"e164": cand, "assistant_id": "demo", "label": "Main"}
    )
    assert r.status_code == 201, r.text
    num = r.json()
    assert num["provider"] == "simulated" and num["tenant_id"] == "demo"

    # routed: worker can now resolve the number to the assistant
    r = await client.get("/v1/worker/assistants/resolve", params={"number": cand}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["assistant_id"] == "demo"

    # duplicate + starter cap
    r = await client.post("/v1/numbers", params=DEMO, json={"e164": cand, "assistant_id": "demo"})
    assert r.status_code == 422
    r = await client.post(
        "/v1/numbers", params=DEMO, json={"e164": "+442079460999", "assistant_id": "demo"}
    )
    assert r.status_code == 422 and "upgrade" in r.json()["detail"].lower()

    r = await client.get("/v1/numbers", params=DEMO)
    assert [n["id"] for n in r.json()] == [num["id"]]

    # cannot release from another tenant; can release from own
    r = await client.delete(f"/v1/numbers/{num['id']}", params={"tenant_id": "other"})
    assert r.status_code == 403
    r = await client.delete(f"/v1/numbers/{num['id']}", params=DEMO)
    assert r.status_code == 204
    assert (await client.get("/v1/numbers", params=DEMO)).json() == []
    r = await client.get("/v1/worker/assistants/resolve", params={"number": cand}, headers=HEADERS)
    assert r.status_code == 404


# -- API: observability ----------------------------------------------------------------------


async def test_latency_report_and_metrics(client: AsyncClient) -> None:
    await _run_call(client, "l1", 30, answer_latency_s=0.3, latency={"p50_s": 0.8, "p95_s": 1.2})
    await _run_call(client, "l2", 30, answer_latency_s=0.9, latency={"p50_s": 1.9, "p95_s": 2.4})
    await _run_call(client, "l3", 30, tenant="other-tenant")

    r = await client.get("/v1/observability/latency", params={**DEMO, "days": 7})
    assert r.status_code == 200
    rep = r.json()
    assert rep["overall"]["calls"] == 2
    assert rep["overall"]["answer_p50_s"] == 0.3 and rep["overall"]["answer_p95_s"] == 0.9
    assert rep["overall"]["slow_calls"] == 1
    assert rep["target_turn_s"] == 1.5
    assert list(rep["per_assistant"]) == ["demo"]

    r = await client.get("/v1/observability/latency", params={"tenant_id": "other-tenant"})
    assert r.status_code == 403

    r = await client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert 'parlio_calls_total{outcome="completed",tenant="demo"} 2.0' in body
    assert "parlio_answer_latency_seconds" in body


async def test_rate_limit_headers(client: AsyncClient) -> None:
    r = await client.get("/v1/billing/plans")
    assert "X-RateLimit-Remaining" in r.headers
    r = await client.get("/healthz")
    assert "X-RateLimit-Remaining" not in r.headers


# -- API: compliance -------------------------------------------------------------------------


async def test_retention_policy_and_sweep(client: AsyncClient) -> None:
    r = await client.get("/v1/compliance/retention", params=DEMO)
    assert r.status_code == 200 and r.json()["policy"]["transcript_days"] == 90
    assert r.json()["last_run"] is None

    r = await client.put(
        "/v1/compliance/retention",
        params=DEMO,
        json={"transcript_days": 10, "recording_days": 20, "call_days": 30},
    )
    assert r.status_code == 422  # recording <= transcript violated
    r = await client.put(
        "/v1/compliance/retention",
        params=DEMO,
        json={"transcript_days": 1, "recording_days": 1, "call_days": 3650},
    )
    assert r.status_code == 200

    await _run_call(client, "r1", 30)
    state = client._transport.app.state  # type: ignore[attr-defined]
    store = state.store
    old = await store.get_call("r1")
    assert old is not None and old.transcript
    await store.redact_call("r1", CallRedaction(summary="Talked about card 4111 1111 1111 1111"))
    # sweep "two days from now" so the call is older than the 1-day transcript window
    run = await state.compliance.sweep_tenant("demo", now=datetime.now(UTC) + timedelta(days=2))
    assert run.transcripts_redacted == 1 and run.calls_purged == 0
    after = await store.get_call("r1")
    assert after is not None and after.transcript == []
    assert after.summary == "[redacted by retention policy]"

    r = await client.post("/v1/compliance/retention/run", params=DEMO)
    assert r.status_code == 200 and r.json()["tenant_id"] == "demo"
    assert (await client.get("/v1/compliance/retention", params=DEMO)).json()["last_run"]


async def test_redact_on_write(client: AsyncClient) -> None:
    r = await client.put(
        "/v1/compliance/retention",
        params=DEMO,
        json={
            "transcript_days": 90,
            "recording_days": 30,
            "call_days": 365,
            "redact_on_write": True,
            "redact_caller_number": True,
        },
    )
    assert r.status_code == 200
    await _run_call(client, "w1", 30, caller="+447700900555")
    r = await client.get("/v1/calls/w1")
    assert r.status_code == 200
    call = r.json()
    assert "4111" not in json.dumps(call["transcript"])
    assert "[card]" in call["transcript"][0]["text"]
    assert call["caller"] == "+447700******"


async def test_gdpr_export_and_erase(client: AsyncClient) -> None:
    subject = "+447700900777"
    await _run_call(client, "g1", 30, caller=subject)
    await _run_call(client, "g2", 30, caller=subject)
    await _run_call(client, "g3", 30, caller="+447700900888")
    await _run_call(client, "g4", 30, caller=subject, tenant="other-tenant")

    r = await client.post("/v1/compliance/export", params=DEMO, json={"e164": subject})
    assert r.status_code == 200
    out = r.json()
    assert sorted(c["call_id"] for c in out["calls"]) == ["g1", "g2"]
    assert all(c["tenant_id"] == "demo" for c in out["calls"])

    r = await client.post("/v1/compliance/export", params=DEMO, json={"e164": "bad"})
    assert r.status_code == 422
    r = await client.post(
        "/v1/compliance/erase", params={"tenant_id": "other-tenant"}, json={"e164": subject}
    )
    assert r.status_code == 403

    r = await client.post("/v1/compliance/erase", params=DEMO, json={"e164": subject})
    assert r.status_code == 200
    assert r.json()["calls_purged"] == 2
    assert (await client.get("/v1/calls/g1")).status_code == 404
    assert (await client.get("/v1/calls/g3")).status_code == 200
    # other tenant's data untouched
    store = client._transport.app.state.store  # type: ignore[attr-defined]
    assert await store.get_call("g4") is not None

    acts = {a["action"] for a in (await client.get("/v1/audit", params=DEMO)).json()}
    assert {"compliance.subject.export", "compliance.subject.erase"} <= acts
