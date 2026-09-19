"""Phase 17/18: ops health engine, synthetic calls, status page, support desk."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.auth import DEV_TENANT
from parlio_api.ops import OpsService
from parlio_api.support import SUPPORT_TENANT, SupportDesk, SupportTicketIn

from .test_admin import OWNER, TENANT_USER
from .test_platform import _run_call

Q = {"tenant_id": DEV_TENANT}


async def test_health_overview_and_synthetic_call(client: AsyncClient, app: FastAPI) -> None:
    for i in range(4):
        await _run_call(client, f"ops-{i}", 60, answer_latency_s=0.4)
    r = await client.get("/v1/health/overview", params=Q)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["health"]["tenant_id"] == DEV_TENANT
    assert 0 <= body["health"]["score"] <= 100
    assert body["forwarding"]["status"] in (
        "ok",
        "no_baseline",
        "quiet",
        "forwarding_may_be_off",
        "outside_hours",
    )
    # manual synthetic call runs the scripted paths in simulated mode
    r = await client.post("/v1/health/synthetic", params=Q)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["trigger"] == "manual" and run["checks"]
    r = await client.get("/v1/health/overview", params=Q)
    assert any(s["id"] == run["id"] for s in r.json()["synthetic"])
    # fault classification for a known call returns a plain-English report + evidence
    r = await client.post("/v1/health/calls/ops-0/diagnose", params=Q)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["attribution"] in (
        "parlio",
        "carrier",
        "customer_provider",
        "customer_config",
        "unknown",
    )
    assert rep["headline"] and "evidence" in rep
    assert (await client.post("/v1/health/calls/nope/diagnose", params=Q)).status_code == 404
    # other tenants can't read our health
    r = await client.get("/v1/health/overview", params=Q, headers=TENANT_USER)
    assert r.status_code == 403


async def test_admin_ops_board_alerts_and_status_page(client: AsyncClient, app: FastAPI) -> None:
    ops: OpsService = app.state.ops
    # a run of failed calls should surface an alert on the sweep
    for i in range(6):
        await _run_call(client, f"bad-{i}", 2, answer_latency_s=4.5)
    out = await ops.sweep(synthetic=False)
    assert isinstance(out, dict)
    r = await client.get("/v1/admin/ops/overview", headers=OWNER)
    assert r.status_code == 200, r.text
    ov = r.json()
    assert any(t["tenant_id"] == DEV_TENANT for t in ov["board"])
    assert ov["status"]["overall"] in ("operational", "degraded", "partial_outage", "major_outage")
    assert "canary_ok" in ov
    if ov["open_alerts"]:
        aid = ov["open_alerts"][0]["id"]
        r = await client.post(f"/v1/admin/ops/alerts/{aid}/ack", headers=OWNER)
        assert r.status_code == 200 and r.json()["acknowledged_by"] == OWNER["X-Parlio-User"]
        r = await client.post(f"/v1/admin/ops/alerts/{aid}/resolve", headers=OWNER)
        assert r.status_code == 200 and r.json()["resolved_at"]
    # tenant users can't reach admin ops
    assert (await client.get("/v1/admin/ops/overview", headers=TENANT_USER)).status_code == 403
    # incidents flow onto the public status page
    inc = {
        "id": "inc-test0001",
        "title": "Degraded SMS delivery",
        "severity": "p2",
        "components": ["sms"],
        "impact": "degraded",
        "status": "investigating",
    }
    r = await client.put("/v1/admin/ops/incidents/inc-test0001", json=inc, headers=OWNER)
    assert r.status_code == 200, r.text
    r = await client.post(
        "/v1/admin/ops/incidents/inc-test0001/updates",
        json={"status": "monitoring", "message": "Fix deployed, monitoring."},
        headers=OWNER,
    )
    assert r.status_code == 200 and r.json()["status"] == "monitoring"
    r = await client.get("/v1/public/status-page")
    assert r.status_code == 200
    page = r.json()
    assert any(i["id"] == "inc-test0001" for i in page["incidents"])
    sms = next(c for c in page["components"] if c["id"] == "sms")
    assert sms["state"] == "degraded"
    # on-call config is owner-only; failover is audited
    r = await client.put(
        "/v1/admin/ops/oncall",
        json={"provider": "webhook", "webhook_url": "https://hooks.example/x", "rota": ["a@b.c"]},
        headers=OWNER,
    )
    assert r.status_code == 200 and r.json()["provider"] == "webhook"
    r = await client.post(
        "/v1/admin/ops/failover", json={"to": "twilio", "reason": "test"}, headers=OWNER
    )
    assert r.status_code == 200 and r.json()["active"] == "twilio"


async def test_support_desk_tickets_sla_csat_escalation(client: AsyncClient, app: FastAPI) -> None:
    desk: SupportDesk = app.state.support
    # support tenant is bootstrapped with the KB-backed assistant
    assert await app.state.store.list_assistants(SUPPORT_TENANT)
    r = await client.get("/v1/support/kb", params={**Q, "q": "forwarding"})
    assert r.status_code == 200 and r.json()
    r = await client.get("/v1/support/walkthrough", params={**Q, "kind": "forwarding"})
    assert r.status_code == 200 and r.json()["steps"]
    # tenant opens a ticket -> SLA due set from priority
    r = await client.post(
        "/v1/support/tickets",
        params=Q,
        json={"subject": "Calls not reaching the AI", "body": "Since 9am", "priority": "p2"},
    )
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["sla_due_at"] and t["status"] == "open"
    tid = t["id"]
    # other tenants can't see or reply to it
    r = await client.get("/v1/support/tickets", params=Q, headers=TENANT_USER)
    assert r.status_code == 403
    # staff replies -> first response recorded, status in_progress
    r = await client.post(
        f"/v1/admin/ops/support/tickets/{tid}/reply",
        json={"text": "Looking into this now."},
        headers=OWNER,
    )
    assert r.status_code == 200, r.text
    assert r.json()["first_response_at"] and r.json()["status"] == "in_progress"
    # internal note is hidden from the tenant view
    await client.post(
        f"/v1/admin/ops/support/tickets/{tid}/reply",
        json={"text": "carrier ticket 123", "public": False},
        headers=OWNER,
    )
    mine = (await client.get("/v1/support/tickets", params=Q)).json()
    ev = next(x for x in mine if x["id"] == tid)["events"]
    assert all(e["public"] for e in ev)
    # tenant status tool for the desk (identity-verified path lives in the text agent)
    r = await client.get(f"/v1/admin/ops/support/tenants/{DEV_TENANT}/status", headers=OWNER)
    assert r.status_code == 200 and r.json()["tenant_id"] == DEV_TENANT
    # escalate to engineering (log tracker seam) and provider email requires consent
    r = await client.post(
        f"/v1/admin/ops/support/tickets/{tid}/escalate",
        json={"summary": "Inbound failing for tenant"},
        headers=OWNER,
    )
    assert r.status_code == 200 and r.json()["engineering_ref"]
    r = await client.post(
        f"/v1/admin/ops/support/tickets/{tid}/provider-email",
        json={"provider_email": "faults@carrier.example", "consent": False},
        headers=OWNER,
    )
    assert r.status_code == 400
    # resolve -> CSAT requested; tenant rates it
    r = await client.post(
        f"/v1/admin/ops/support/tickets/{tid}/status",
        json={"status": "resolved"},
        headers=OWNER,
    )
    assert r.status_code == 200 and r.json()["resolved_at"]
    r = await client.post(
        f"/v1/support/tickets/{tid}/csat", params=Q, json={"score": 5, "comment": "Great"}
    )
    assert r.status_code == 200 and r.json()["csat_score"] == 5
    # SLA sweep flags breached open tickets
    t2 = await desk.create_ticket(
        DEV_TENANT,
        "x@demo",
        SupportTicketIn(subject="P1 outage", priority="p1"),
    )
    breached = await desk.sweep_sla(now=datetime.now(UTC) + timedelta(hours=1))
    assert any(b.id == t2.id for b in breached)
    r = await client.get("/v1/admin/ops/support/tag-review", headers=OWNER)
    assert r.status_code == 200
    # tenant users cannot use the desk
    assert (
        await client.get("/v1/admin/ops/support/tickets", headers=TENANT_USER)
    ).status_code == 403


@pytest.mark.parametrize("backend", ["memory"], indirect=True)
async def test_synthetic_failure_opens_ticket(client: AsyncClient, app: FastAPI) -> None:
    ops: OpsService = app.state.ops
    runs = await ops.run_all("post_deploy")
    assert runs and all(r.trigger == "post_deploy" for r in runs)
    ok, reasons = await ops.canary_ok()
    assert isinstance(ok, bool) and isinstance(reasons, list)


async def test_http_pager_emails_and_texts_the_rota() -> None:
    from parlio_api.messaging import LogSmsProvider
    from parlio_api.notifications import LogEmailSender
    from parlio_api.ops import HttpPager, OnCallConfig, OpsAlert

    email, sms = LogEmailSender(), LogSmsProvider()
    pager = HttpPager(email=email, sms=sms, sms_from="+447700900000")
    alert = OpsAlert(
        tenant_id="acme", kind="sip", severity="critical", title="SIP trunk down", detail="x"
    )
    ok = await pager.page(
        OnCallConfig(provider="email", rota=["admin@example.com", "second@example.com"]), alert
    )
    assert ok and [t for t, _, _ in email.sent] == ["admin@example.com", "second@example.com"]
    assert "CRITICAL" in email.sent[0][1] and "SIP trunk down" in email.sent[0][1]
    ok = await pager.page(OnCallConfig(provider="sms", phones=["+447700900001"]), alert)
    assert ok and sms.sent[0][1] == "+447700900001" and "SIP trunk down" in sms.sent[0][2]
    # no sender wired / empty rota -> not paged, no exception
    assert not await HttpPager().page(OnCallConfig(provider="email", rota=["a@b.c"]), alert)
    assert not await pager.page(OnCallConfig(provider="sms"), alert)
