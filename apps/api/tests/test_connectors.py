"""Phase 7 connectors — contract tests against a mocked HTTP transport plus API round-trips.

No vendor is contacted: every backend runs against `httpx.MockTransport`, so the tests assert the
exact request shape each vendor API expects (auth header, endpoint, body) and the retry/back-off
state machine, without credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.connectors import (
    MAX_ATTEMPTS,
    PROVIDERS,
    Connector,
    ConnectorService,
    HubSpotBackend,
    JobStatus,
    Payload,
    PipedriveBackend,
    Provider,
    SalesforceBackend,
    ServiceM8Backend,
    SimulatedBackend,
    TeamsBackend,
    Trigger,
    WebhookBackend,
    ZohoBackend,
    build_backends,
    calls_csv,
    payload_from_call,
    payload_from_ticket,
)
from parlio_api.store import CallRecord, MemoryStore, Ticket
from parlio_api.vault import LocalVault
from parlio_voice.models import CallEventType, TicketPriority

from .test_api import HEADERS, ev

Responder = Callable[[httpx.Request], httpx.Response | None]


class Recorder:
    def __init__(self, responder: Responder | None = None) -> None:
        self.calls: list[httpx.Request] = []
        self.responder = responder

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if self.responder is not None:
            out = self.responder(req)
            if out is not None:
                return out
        return httpx.Response(200, json={"id": "ext-1"})

    def body(self, i: int = -1) -> dict[str, Any]:
        return dict(json.loads(self.calls[i].content))


def http(rec: Recorder) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(rec))


def sample(event: Trigger = Trigger.LEAD_QUALIFIED) -> Payload:
    return Payload(
        event=event,
        tenant_id="t",
        occurred_at=datetime(2026, 9, 11, 10, 0, tzinfo=UTC),
        business_name="Acme Plumbing",
        caller_name="Sam Sample",
        caller_phone="+447700900123",
        caller_email="sam@example.com",
        summary="Wants a boiler service next week.",
        duration_s=94,
        qualified=True,
        call_id="c1",
        extracted={"postcode": "SW1A 1AA"},
    )


def conn(provider: Provider, **kw: Any) -> Connector:
    return Connector(tenant_id="t", company_id="c", provider=provider, **kw)


# -- payload ----------------------------------------------------------------------------------


def test_payload_mapping_and_builders() -> None:
    p = sample()
    flat = p.flat()
    assert flat["caller_phone"] == "+447700900123" and flat["extracted.postcode"] == "SW1A 1AA"
    mapped = p.mapped({"Phone": "caller_phone", "Postcode": "extracted.postcode", "X": "nope"})
    assert mapped == {"Phone": "+447700900123", "Postcode": "SW1A 1AA", "X": None}
    assert p.title().startswith("Qualified lead: Sam")

    call = CallRecord(
        call_id="k",
        tenant_id="t",
        company_id="c",
        assistant_id="a",
        caller="+447700900000",
        summary="s",
        extracted={"name": "Jo", "email": "jo@x.test", "reason": "quote", "size": "3 bed"},
        share_token="tok",
    )
    cp = payload_from_call(call, "Acme", True, "https://api.test/")
    assert cp.event == Trigger.LEAD_QUALIFIED and cp.caller_name == "Jo"
    assert cp.transcript_url == "https://api.test/v1/public/share/tok"
    assert cp.extracted == {"size": "3 bed"}
    t = Ticket(
        id="tk-1",
        tenant_id="t",
        company_id="c",
        reason="Leak",
        priority=TicketPriority.URGENT,
        caller_number="+44",
    )
    tp = payload_from_ticket(t, "Acme")
    assert tp.event == Trigger.TICKET_CREATED and tp.priority == "urgent" and tp.ticket_id == t.id


def test_connector_wants_filters_triggers_and_qualified() -> None:
    c = conn(Provider.SIMULATED, triggers=[Trigger.CALL_COMPLETED], qualified_only=True)
    assert not c.wants(sample(Trigger.LEAD_QUALIFIED))
    unq = sample(Trigger.CALL_COMPLETED).model_copy(update={"qualified": False})
    assert not c.wants(unq)
    assert c.wants(sample(Trigger.CALL_COMPLETED))
    c.enabled = False
    assert not c.wants(sample(Trigger.CALL_COMPLETED))


# -- backend contracts ------------------------------------------------------------------------


async def test_webhook_signs_body_with_hmac() -> None:
    rec = Recorder()
    be = WebhookBackend(http(rec))
    c = conn(Provider.WEBHOOK, target_url="https://hooks.test/x", field_map={"tel": "caller_phone"})
    ref = await be.push(c, "shh", sample())
    assert ref == "ext-1"
    req = rec.calls[0]
    assert str(req.url) == "https://hooks.test/x"
    expected = hmac.new(b"shh", req.content, hashlib.sha256).hexdigest()
    assert req.headers["X-Parlio-Signature"] == f"sha256={expected}"
    body = rec.body()
    assert body["event"] == "lead.qualified" and body["data"] == {"tel": "+447700900123"}
    assert await be.test(c, None) == "HTTP 200"
    assert "X-Parlio-Signature" not in rec.calls[-1].headers


async def test_teams_posts_adaptive_card() -> None:
    rec = Recorder()
    c = conn(Provider.TEAMS, target_url="https://prod.westeurope.logic.azure.com/hook")
    await TeamsBackend(http(rec)).push(c, None, sample())
    card = rec.body()["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["body"][0]["text"] == "Qualified lead: Sam Sample"
    facts = {f["title"]: f["value"] for f in card["body"][2]["facts"]}
    assert facts["Phone"] == "+447700900123"


async def test_hubspot_creates_contact_when_missing_and_logs_call() -> None:
    def respond(req: httpx.Request) -> httpx.Response | None:
        if req.url.path.endswith("/contacts/search"):
            return httpx.Response(200, json={"results": []})
        if req.url.path.endswith("/objects/contacts"):
            return httpx.Response(201, json={"id": "501"})
        if req.url.path.endswith("/objects/calls"):
            return httpx.Response(201, json={"id": "9001"})
        if req.url.path.endswith("/account-info/v3/details"):
            return httpx.Response(200, json={"portalId": 42, "uiDomain": "app-eu1.hubspot.com"})
        return None

    rec = Recorder(respond)
    be = HubSpotBackend(http(rec))
    c = conn(Provider.HUBSPOT)
    assert await be.test(c, "pat-x") == "portal 42 (app-eu1.hubspot.com)"
    ref = await be.push(c, "pat-x", sample())
    assert ref == "contact:501 call:9001"
    assert all(r.headers["Authorization"] == "Bearer pat-x" for r in rec.calls)
    created = rec.body(2)["properties"]
    assert created["firstname"] == "Sam" and created["lastname"] == "Sample"
    assert created["lifecyclestage"] == "lead"
    call = rec.body(3)
    assert call["properties"]["hs_call_direction"] == "INBOUND"
    assert call["properties"]["hs_call_duration"] == 94_000
    assert call["associations"][0]["to"]["id"] == "501"


async def test_hubspot_updates_existing_contact() -> None:
    def respond(req: httpx.Request) -> httpx.Response | None:
        if req.url.path.endswith("/contacts/search"):
            return httpx.Response(200, json={"results": [{"id": "77"}]})
        return None

    rec = Recorder(respond)
    ref = await HubSpotBackend(http(rec)).push(conn(Provider.HUBSPOT), "pat", sample())
    assert ref.startswith("contact:77")  # type: ignore[union-attr]
    methods = [r.method for r in rec.calls]
    assert methods == ["POST", "PATCH", "POST"]  # search, update, log call


async def test_salesforce_client_credentials_then_lead_and_task() -> None:
    def respond(req: httpx.Request) -> httpx.Response | None:
        p = req.url.path
        if p.endswith("/oauth2/token"):
            return httpx.Response(
                200,
                json={"access_token": "sf-tok", "instance_url": "https://acme.my.salesforce.com"},
            )
        if "/search/" in p:
            return httpx.Response(200, json={"searchRecords": []})
        if p.endswith("/sobjects/Lead"):
            return httpx.Response(201, json={"id": "00Q1"})
        if p.endswith("/sobjects/Task"):
            return httpx.Response(201, json={"id": "00T1"})
        return None

    rec = Recorder(respond)
    c = conn(
        Provider.SALESFORCE,
        options={"instance_url": "https://login.salesforce.com", "client_id": "cid"},
    )
    ref = await SalesforceBackend(http(rec)).push(c, "csecret", sample())
    assert ref == "Lead:00Q1 task:00T1"
    tok = dict(httpx.QueryParams(rec.calls[0].content.decode()))
    assert tok["grant_type"] == "client_credentials" and tok["client_secret"] == "csecret"
    lead = rec.body(2)
    assert lead["LastName"] == "Sample" and lead["LeadSource"] == "Phone Inquiry"
    task = rec.body(3)
    assert task["WhoId"] == "00Q1" and task["CallType"] == "Inbound"


async def test_pipedrive_upserts_person_and_logs_activity() -> None:
    def respond(req: httpx.Request) -> httpx.Response | None:
        p = req.url.path
        if p.endswith("/persons/search"):
            return httpx.Response(200, json={"data": {"items": [{"item": {"id": 5}}]}})
        if p.endswith("/activities"):
            return httpx.Response(201, json={"data": {"id": 88}})
        return None

    rec = Recorder(respond)
    c = conn(Provider.PIPEDRIVE, options={"company_domain": "acme"})
    ref = await PipedriveBackend(http(rec)).push(c, "tok", sample())
    assert ref == "person:5 activity:88"
    assert rec.calls[0].url.host == "acme.pipedrive.com"
    assert rec.calls[0].url.params["api_token"] == "tok"
    act = rec.body(1)
    assert act["type"] == "call" and act["person_id"] == 5 and act["duration"] == "00:01"


async def test_zoho_refreshes_token_and_creates_lead_note() -> None:
    def respond(req: httpx.Request) -> httpx.Response | None:
        p = req.url.path
        if p.endswith("/oauth/v2/token"):
            return httpx.Response(200, json={"access_token": "z-tok"})
        if p.endswith("/Leads/search"):
            return httpx.Response(204)
        if p.endswith("/Leads"):
            return httpx.Response(201, json={"data": [{"details": {"id": "L1"}}]})
        if p.endswith("/Notes"):
            return httpx.Response(201, json={"data": [{"details": {"id": "N1"}}]})
        return None

    rec = Recorder(respond)
    c = conn(Provider.ZOHO, options={"client_id": "a", "client_secret": "b"})
    ref = await ZohoBackend(http(rec)).push(c, "refresh", sample())
    assert ref == "lead:L1"
    assert rec.calls[0].url.host == "accounts.zoho.eu"
    assert rec.calls[1].url.host == "www.zohoapis.eu"
    assert rec.calls[1].headers["Authorization"] == "Zoho-oauthtoken z-tok"


async def test_servicem8_creates_client_contact_and_job() -> None:
    def respond(req: httpx.Request) -> httpx.Response | None:
        p = req.url.path
        if p.endswith("/company.json") and req.method == "GET":
            return httpx.Response(200, json=[])
        if p.endswith("/company.json"):
            return httpx.Response(200, headers={"x-record-uuid": "co-1"})
        if p.endswith("/job.json"):
            return httpx.Response(200, headers={"x-record-uuid": "job-1"})
        return None

    rec = Recorder(respond)
    ref = await ServiceM8Backend(http(rec)).push(conn(Provider.SERVICEM8), "k", sample())
    assert ref == "client:co-1 job:job-1"
    assert all(r.headers["X-Api-Key"] == "k" for r in rec.calls)
    job = rec.body(3)
    assert job["company_uuid"] == "co-1" and job["status"] == "Quote"
    assert job["job_address"] == ""


def test_build_backends_covers_every_provider_except_oauth_without_creds() -> None:
    bes = build_backends(httpx.AsyncClient())
    for p in PROVIDERS:
        if p.auth == "oauth":
            assert p.provider not in bes
        else:
            assert p.provider in bes
    with_google = build_backends(
        httpx.AsyncClient(), google_client_id="i", google_client_secret="s"
    )
    assert Provider.GOOGLE_SHEETS in with_google


# -- service: dispatch, retry, api keys ---------------------------------------------------------


async def test_dispatch_retry_backoff_and_failure_cap() -> None:
    store = MemoryStore(None)
    sim = SimulatedBackend()
    svc = ConnectorService(store, LocalVault("k"), {Provider.SIMULATED: sim})
    c = await svc.put(conn(Provider.SIMULATED, triggers=[Trigger.LEAD_QUALIFIED]))
    await svc.put(conn(Provider.SIMULATED, triggers=[Trigger.TICKET_CREATED]))  # not matching

    sim.fail_next = 1
    jobs = await svc.dispatch(sample())
    assert len(jobs) == 1 and jobs[0].status == JobStatus.RETRY and jobs[0].attempts == 1
    assert jobs[0].next_attempt_at is not None
    c2 = await svc.get("t", c.id)
    assert c2 is not None and c2.status == "error" and c2.last_error == "simulated outage"

    # not due yet -> untouched; due -> re-run and succeed
    assert await svc.retry_due(datetime.now(UTC)) == 0
    assert await svc.retry_due(jobs[0].next_attempt_at) == 1
    job = await svc.get_job("t", jobs[0].id)
    assert job is not None and job.status == JobStatus.SENT and job.external_ref == "sim-1"
    c3 = await svc.get("t", c.id)
    assert c3 is not None and c3.status == "connected" and c3.last_error is None

    sim.fail_next = MAX_ATTEMPTS
    (failing,) = await svc.dispatch(sample())
    for _ in range(MAX_ATTEMPTS - 1):
        assert failing.next_attempt_at is not None
        failing = (await svc.retry("t", failing.id)) or failing
    assert failing.status == JobStatus.FAILED and failing.attempts == MAX_ATTEMPTS
    forced = await svc.retry("t", failing.id, force=True)
    assert forced is not None and forced.status == JobStatus.SENT and forced.attempts == 1


async def test_secrets_are_sealed_and_never_public() -> None:
    store = MemoryStore(None)
    svc = ConnectorService(
        store, LocalVault("k"), {Provider.WEBHOOK: WebhookBackend(http(Recorder()))}
    )
    c = await svc.put(conn(Provider.HUBSPOT), secret="pat-secret")
    doc = await store.get_doc("connector", c.id)
    assert doc is not None and doc.data["secret_sealed"].startswith("enc:")
    assert "pat-secret" not in json.dumps(doc.data)
    assert "secret_sealed" not in c.public() and c.public()["has_secret"] is True
    w = await svc.put(conn(Provider.WEBHOOK, target_url="https://h.test"))
    assert svc.signing_secret(w) and len(svc.signing_secret(w) or "") > 20


async def test_api_keys_hash_and_revoke() -> None:
    svc = ConnectorService(MemoryStore(None), LocalVault("k"), {})
    k, raw = await svc.create_api_key("t", "crm")
    assert raw.startswith("pk_") and k.key_hash != raw
    found = await svc.resolve_api_key(raw)
    assert found is not None and found.id == k.id and found.last_used_at is not None
    assert await svc.resolve_api_key("pk_nope") is None
    assert await svc.revoke_api_key("t", k.id)
    assert await svc.resolve_api_key(raw) is None


def test_calls_csv() -> None:
    out = calls_csv(
        [
            CallRecord(
                call_id="k",
                tenant_id="t",
                company_id="c",
                assistant_id="a",
                caller="+44",
                summary="s, quoted",
            )
        ]
    )
    lines = out.strip().splitlines()
    assert lines[0].startswith("call_id,started_at,caller") and '"s, quoted"' in lines[1]


# -- API ----------------------------------------------------------------------------------------


async def test_connectors_api_roundtrip_and_event_wiring(client: AsyncClient, app: FastAPI) -> None:
    r = await client.get("/v1/connectors/providers")
    assert r.status_code == 200
    provs = {p["provider"]: p for p in r.json()["providers"]}
    assert provs["zapier"]["available"] and provs["google_sheets"]["available"] is False
    assert "caller_phone" in r.json()["payload_fields"]

    # validation
    r = await client.post(
        "/v1/connectors", params={"tenant_id": "demo"}, json={"provider": "hubspot"}
    )
    assert r.status_code == 400 and "secret" in r.text
    r = await client.post(
        "/v1/connectors", params={"tenant_id": "demo"}, json={"provider": "webhook"}
    )
    assert r.status_code == 400 and "target_url" in r.text
    r = await client.post(
        "/v1/connectors", params={"tenant_id": "demo"}, json={"provider": "google_sheets"}
    )
    assert r.status_code == 400 and "oauth" in r.text

    r = await client.post(
        "/v1/connectors",
        params={"tenant_id": "demo"},
        json={
            "provider": "simulated",
            "name": "Demo CRM",
            "triggers": ["call.completed", "ticket.created", "booking.created"],
        },
    )
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["status"] == "connected" and c["account_label"] == "demo account"
    assert "secret_sealed" not in c

    r = await client.patch(
        f"/v1/connectors/{c['id']}",
        params={"tenant_id": "demo"},
        json={"field_map": {"Phone": "caller_phone"}, "qualified_only": True},
    )
    assert r.status_code == 200 and r.json()["field_map"] == {"Phone": "caller_phone"}

    r = await client.post(f"/v1/connectors/{c['id']}/send-sample", params={"tenant_id": "demo"})
    assert r.status_code == 200 and r.json()["status"] == "sent"

    # a completed call runs post-call and fans out through the hub -> connector
    for t, payload in [
        (CallEventType.CALL_STARTED, {"caller": "+447700900555"}),
        (CallEventType.TRANSCRIPT_ITEM, {"role": "user", "text": "I need a quote please"}),
        (CallEventType.CALL_ENDED, {"reason": "hangup", "duration_s": 40}),
    ]:
        r = await client.post("/v1/worker/events", json=ev(t, "cx-call", payload), headers=HEADERS)
        assert r.status_code == 202, r.text
    await app.state.postcall.drain()

    # ticket intake -> hub.on_ticket -> connector
    r = await client.post(
        "/v1/worker/tickets",
        params={"tenant_id": "demo", "company_id": "demo"},
        json={"reason": "Callback about a leak", "caller_number": "+447700900555"},
        headers=HEADERS,
    )
    assert r.status_code == 201, r.text

    r = await client.get("/v1/connectors/jobs", params={"tenant_id": "demo"})
    assert r.status_code == 200
    events = sorted(j["event"] for j in r.json() if j["status"] == "sent")
    assert "ticket.created" in events and "lead.qualified" in events
    job = r.json()[0]
    r = await client.post(f"/v1/connectors/jobs/{job['id']}/retry", params={"tenant_id": "demo"})
    assert r.status_code == 200 and r.json()["attempts"] == 1

    # tenant isolation
    r = await client.get("/v1/connectors", params={"tenant_id": "other"})
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        assert r.json() == []

    r = await client.delete(f"/v1/connectors/{c['id']}", params={"tenant_id": "demo"})
    assert r.status_code == 204
    r = await client.get("/v1/connectors", params={"tenant_id": "demo"})
    assert r.json() == []


async def test_api_keys_and_inbound_api(client: AsyncClient) -> None:
    r = await client.get("/v1/inbound/me")
    assert r.status_code == 401
    r = await client.post("/v1/api-keys", params={"tenant_id": "demo"}, json={"name": "Zapier"})
    assert r.status_code == 201, r.text
    key = r.json()
    assert key["key"].startswith("pk_") and "key_hash" not in key
    auth = {"Authorization": f"Bearer {key['key']}"}

    r = await client.get("/v1/inbound/me", headers=auth)
    assert r.status_code == 200 and r.json()["tenant_id"] == "demo"
    r = await client.post(
        "/v1/inbound/contacts",
        headers={"X-Api-Key": key["key"]},
        json={"phone": "+447700900777", "name": "Inbound Ian", "vip": True},
    )
    assert r.status_code == 201, r.text
    assert r.json()["contact"]["name"] == "Inbound Ian" and r.json()["contact"]["vip"] is True
    r = await client.post(
        "/v1/inbound/tickets",
        headers=auth,
        json={"reason": "Please call back", "caller_number": "+447700900777", "priority": "high"},
    )
    assert (
        r.status_code == 201 and r.json()["priority"] == "high" and r.json()["source"] == "manual"
    )
    r = await client.get("/v1/inbound/calls", headers=auth)
    assert r.status_code == 200

    r = await client.get("/v1/api-keys", params={"tenant_id": "demo"})
    assert [k["id"] for k in r.json()] == [key["id"]] and "key" not in r.json()[0]
    r = await client.delete(f"/v1/api-keys/{key['id']}", params={"tenant_id": "demo"})
    assert r.status_code == 204
    r = await client.get("/v1/inbound/me", headers=auth)
    assert r.status_code == 401


async def test_csv_export(client: AsyncClient) -> None:
    for what in ("calls", "contacts", "tickets"):
        r = await client.get(f"/v1/export/{what}.csv", params={"tenant_id": "demo"})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")
        assert f"parlio-{what}.csv" in r.headers["content-disposition"]
        assert r.text.splitlines()[0]
    r = await client.get("/v1/export/nope.csv", params={"tenant_id": "demo"})
    assert r.status_code == 404
