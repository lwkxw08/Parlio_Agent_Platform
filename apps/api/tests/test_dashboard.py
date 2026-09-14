"""Phase 4: dashboard auth, call filters/actions, contacts, members, versions, analytics."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.auth import DEV_TENANT, DEV_USER_EMAIL, encode_supabase_jwt
from parlio_api.onboarding import analyse_html, suggest_faqs
from parlio_api.postcall import PostCallProcessor
from parlio_api.settings import get_settings
from parlio_api.store import CallRecord
from parlio_voice.models import AssistantConfig, BusinessRule, CallEvent, CallEventType, Faq

HEADERS = {"X-Worker-Key": "dev-worker-key"}


def ev(t: CallEventType, call_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return CallEvent(
        type=t,
        call_id=call_id,
        tenant_id="demo",
        company_id="demo",
        assistant_id="demo",
        payload=payload,
    ).model_dump(mode="json")


async def _call(
    client: AsyncClient,
    call_id: str,
    caller: str,
    *,
    answered: bool = True,
    reason: str = "hangup",
    duration: float = 30.0,
) -> None:
    events = [ev(CallEventType.CALL_STARTED, call_id, {"caller": caller, "dialed": "+4400"})]
    if answered:
        events.append(ev(CallEventType.CALL_ANSWERED, call_id, {"answer_latency_s": 0.4}))
        events.append(
            ev(
                CallEventType.TRANSCRIPT_ITEM,
                call_id,
                {"role": "user", "text": "Hi there. Do you open on Saturdays?"},
            )
        )
    events.append(ev(CallEventType.CALL_ENDED, call_id, {"reason": reason, "duration_s": duration}))
    for e in events:
        r = await client.post("/v1/worker/events", json=e, headers=HEADERS)
        assert r.status_code == 202, r.text


# -- auth --------------------------------------------------------------------------------------


async def test_dev_mode_me_is_seeded_owner(client: AsyncClient) -> None:
    r = await client.get("/v1/me")
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == DEV_USER_EMAIL
    assert body["mode"] == "dev"
    assert [m["tenant_id"] for m in body["memberships"]] == [DEV_TENANT]
    assert body["memberships"][0]["role"] == "owner"


@pytest.mark.parametrize("backend", ["memory"], indirect=True)
async def test_supabase_mode_requires_valid_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PARLIO_AUTH_MODE", "supabase")
    monkeypatch.setenv("PARLIO_SUPABASE_JWT_SECRET", "test-secret")
    get_settings.cache_clear()
    try:
        assert (await client.get("/v1/calls")).status_code == 401
        bad = {"Authorization": "Bearer nope.nope.nope"}
        assert (await client.get("/v1/calls", headers=bad)).status_code == 401
        expired = encode_supabase_jwt(
            {"sub": "u1", "email": "a@b.co", "exp": time.time() - 10}, "test-secret"
        )
        r = await client.get("/v1/me", headers={"Authorization": f"Bearer {expired}"})
        assert r.status_code == 401 and "expired" in r.text
        wrong_key = encode_supabase_jwt({"sub": "u1", "email": "a@b.co"}, "other")
        r = await client.get("/v1/me", headers={"Authorization": f"Bearer {wrong_key}"})
        assert r.status_code == 401
        good = encode_supabase_jwt(
            {
                "sub": "u1",
                "email": "Ann@Example.com",
                "exp": time.time() + 60,
                "user_metadata": {"full_name": "Ann"},
            },
            "test-secret",
        )
        r = await client.get("/v1/me", headers={"Authorization": f"Bearer {good}"})
        assert r.status_code == 200
        assert r.json()["email"] == "ann@example.com"
        assert r.json()["name"] == "Ann"
        assert r.json()["memberships"] == []  # no org yet -> onboarding
    finally:
        get_settings.cache_clear()


# -- onboarding --------------------------------------------------------------------------------


async def test_onboarding_creates_org_owner_and_assistant(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/onboarding",
        json={
            "organisation_name": "Acme Plumbing",
            "business": {"description": "Emergency plumbers", "services": ["Boilers"]},
            "faqs": [{"question": "Do you do gas?", "answer": "Yes, Gas Safe registered."}],
            "languages": ["en", "pl"],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    tid = body["tenant_id"]
    assert tid.startswith("acme-plumbing-")
    assert body["member"]["role"] == "owner"
    assert body["assistant"]["languages"] == ["en", "pl"]
    r = await client.get("/v1/me")
    assert tid in [m["tenant_id"] for m in r.json()["memberships"]]
    r = await client.get("/v1/assistants", params={"tenant_id": tid})
    assert len(r.json()) == 1 and r.json()[0]["business_name"] == "Acme Plumbing"


def test_website_analysis_heuristics() -> None:
    html = """
    <html><head><title>Acme Plumbing | Leeds</title>
    <meta name="description" content="24/7 emergency plumbers in Leeds"></head>
    <body><script>var x = 1;</script>
    <h1>Acme Plumbing</h1><h2>Boiler Repairs</h2><h2>Bathroom Fitting</h2>
    <p>Call us on <a href="tel:+441134960000">0113 496 0000</a>
    or <a href="mailto:hi@acme.co.uk">email</a></p>
    <p>Opening hours: Monday to Friday 8am - 6pm</p>
    <p>12 High Street, Leeds LS1 4AP</p>
    <h3>Do you offer free quotes?</h3><p>Yes, all quotes are free and there is no obligation.</p>
    </body></html>
    """
    a = analyse_html("https://acme.example", html)
    assert a.business_name == "Acme Plumbing"
    assert a.business.description == "24/7 emergency plumbers in Leeds"
    assert a.business.phone == "+441134960000"
    assert a.business.email == "hi@acme.co.uk"
    assert "Boiler Repairs" in a.business.services
    assert a.business.address and "LS1 4AP" in a.business.address
    assert a.opening_hours_text and "8am - 6pm" in a.opening_hours_text[0]
    assert a.faqs[0].question == "Do you offer free quotes?" and a.faqs[0].source == "website"


async def test_places_returns_501_without_key(client: AsyncClient) -> None:
    r = await client.get("/v1/onboarding/places", params={"query": "plumber leeds"})
    assert r.status_code == 501


# -- assistant studio / versions ---------------------------------------------------------------


async def test_assistant_versions_and_rollback(client: AsyncClient) -> None:
    r = await client.get("/v1/assistants/demo/versions")
    assert [v["version"] for v in r.json()] == [1]
    cfg = AssistantConfig.model_validate((await client.get("/v1/assistants")).json()[0])
    n_rules, n_faqs = len(cfg.rules), len(cfg.faqs)
    cfg.greeting = "Hello from v2"
    cfg.rules.append(BusinessRule(name="No prices", instruction="Never quote prices."))
    cfg.faqs.append(Faq(question="Parking?", answer="Free parking on site."))
    cfg.blocked_numbers.append("+447700900999")
    r = await client.put("/v1/assistants/demo", json={"config": cfg.model_dump(mode="json")})
    assert r.status_code == 200, r.text

    versions = (await client.get("/v1/assistants/demo/versions")).json()
    assert [v["version"] for v in versions] == [2, 1]  # newest first
    assert versions[0]["greeting"] == "Hello from v2"
    assert versions[0]["rule_count"] == n_rules + 1 and versions[0]["faq_count"] == n_faqs + 1
    live = (await client.get("/v1/assistants")).json()[0]
    assert live["assistant_version"] == 2 and live["blocked_numbers"] == ["+447700900999"]
    assert "Never quote prices" in AssistantConfig.model_validate(live).rendered_instructions()

    r = await client.post("/v1/assistants/demo/rollback/1")
    assert r.status_code == 200
    live = (await client.get("/v1/assistants")).json()[0]
    assert live["assistant_version"] == 3
    assert live["greeting"] != "Hello from v2" and len(live["rules"]) == n_rules
    versions = (await client.get("/v1/assistants/demo/versions")).json()
    assert [v["version"] for v in versions] == [3, 2, 1]
    assert (await client.get("/v1/assistants/demo/versions/9")).status_code == 404


async def test_faq_suggestions_from_transcripts(client: AsyncClient) -> None:
    await _call(client, "s1", "+447700900001")
    await _call(client, "s2", "+447700900002")
    r = await client.get("/v1/assistants/demo/faqs/suggest")
    assert r.status_code == 200
    assert r.json()[0]["question"] == "Do you open on saturdays?"
    assert r.json()[0]["source"] == "suggested"


def test_suggest_faqs_skips_known_questions() -> None:
    call = CallRecord(
        call_id="x",
        tenant_id="t",
        company_id="t",
        assistant_id="a",
        transcript=[{"role": "user", "text": "What are your prices like?"}],
    )
    assert suggest_faqs([call], [Faq(question="What are your prices like?", answer="...")]) == []


# -- calls: filters, read, feedback, share -----------------------------------------------------


async def test_call_filters_and_kinds(client: AsyncClient) -> None:
    await _call(client, "a1", "+447700900001")
    await _call(client, "m1", "+447700900002", answered=False, reason="no_answer", duration=0)
    await _call(client, "b1", "+447700900999", answered=False, reason="blocked", duration=0)

    kinds = {c["call_id"]: c["kind"] for c in (await client.get("/v1/calls")).json()}
    assert kinds == {"a1": "answered", "m1": "missed", "b1": "blocked"}

    for kind, expected in [("answered", ["a1"]), ("missed", ["m1"]), ("blocked", ["b1"])]:
        r = await client.get("/v1/calls", params={"tenant_id": "demo", "kind": kind})
        assert [c["call_id"] for c in r.json()] == expected, kind
    r = await client.get("/v1/calls", params={"kind": "unread"})
    assert len(r.json()) == 3
    r = await client.get("/v1/calls", params={"q": "900002"})
    assert [c["call_id"] for c in r.json()] == ["m1"]
    r = await client.get("/v1/calls", params={"since": "2999-01-01T00:00:00Z"})
    assert r.json() == []
    assert (await client.get("/v1/calls", params={"hour": 24})).status_code == 422


async def test_mark_read_feedback_and_share(client: AsyncClient) -> None:
    await _call(client, "c1", "+447700900001")
    r = await client.post("/v1/calls/c1/read")
    assert r.status_code == 200 and r.json()["read"] is True
    assert (await client.get("/v1/calls", params={"kind": "unread"})).json() == []
    r = await client.post("/v1/calls/c1/read", params={"read": False})
    assert r.json()["read"] is False

    r = await client.post(
        "/v1/calls/c1/feedback", json={"type": "incorrect_response", "note": "wrong hours"}
    )
    assert r.status_code == 200
    fb = r.json()["feedback"]
    assert fb[0]["type"] == "incorrect_response" and fb[0]["actor"] == DEV_USER_EMAIL

    r = await client.post("/v1/calls/c1/share")
    assert r.status_code == 200
    token = r.json()["token"]
    assert r.json()["url"].endswith(f"/share/{token}")
    assert (await client.post("/v1/calls/c1/share")).json()["token"] == token  # stable
    r = await client.get(f"/v1/public/share/{token}")
    assert r.status_code == 200
    assert r.json()["call_id"] == "c1"
    assert r.json()["caller"] == "+44770090****"
    assert (await client.get("/v1/public/share/nope")).status_code == 404
    assert (await client.post("/v1/calls/missing/share")).status_code == 404


# -- contacts ----------------------------------------------------------------------------------


async def test_contacts_are_created_by_calls_and_editable(client: AsyncClient) -> None:
    await _call(client, "k1", "+447700900001")
    await _call(client, "k2", "+447700900001")
    await _call(client, "k3", "+447700900002")
    r = await client.get("/v1/contacts", params={"tenant_id": "demo"})
    assert r.status_code == 200
    by_num = {c["e164"]: c for c in r.json()}
    assert by_num["+447700900001"]["call_count"] == 2
    assert by_num["+447700900002"]["status"] == "prospect"

    cid = by_num["+447700900001"]["id"]
    r = await client.patch(
        f"/v1/contacts/{cid}", json={"name": "Sam", "vip": True, "status": "customer"}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Sam" and r.json()["vip"] is True
    assert (await client.get(f"/v1/contacts/{cid}")).json()["status"] == "customer"
    r = await client.get("/v1/contacts", params={"q": "sam"})
    assert [c["id"] for c in r.json()] == [cid]
    assert (await client.patch(f"/v1/contacts/{cid}", json={"status": "x"})).status_code == 400
    assert (await client.get("/v1/contacts/nope")).status_code == 404


# -- members -----------------------------------------------------------------------------------


async def test_member_invites_roles_and_removal(client: AsyncClient) -> None:
    r = await client.get(f"/v1/organisations/{DEV_TENANT}/members")
    assert [m["role"] for m in r.json()] == ["owner"]
    r = await client.post(
        f"/v1/organisations/{DEV_TENANT}/members",
        json={"email": "new@example.com", "name": "New", "role": "admin"},
    )
    assert r.status_code == 201, r.text
    uid = r.json()["user_id"]
    assert r.json()["status"] == "invited" and r.json()["invited_at"]
    r = await client.post(
        f"/v1/organisations/{DEV_TENANT}/members", json={"email": "bad", "role": "member"}
    )
    assert r.status_code == 422
    r = await client.post(
        f"/v1/organisations/{DEV_TENANT}/members", json={"email": "o@x.com", "role": "owner"}
    )
    assert r.status_code == 400

    r = await client.patch(f"/v1/organisations/{DEV_TENANT}/members/{uid}", json={"role": "viewer"})
    assert r.status_code == 200 and r.json()["role"] == "viewer"

    # invited user signs in (dev mode: X-Parlio-User) and sees the org
    r = await client.get("/v1/me", headers={"X-Parlio-User": "new@example.com"})
    assert [m["tenant_id"] for m in r.json()["memberships"]] == [DEV_TENANT]
    # ...but as a viewer cannot invite
    r = await client.post(
        f"/v1/organisations/{DEV_TENANT}/members",
        json={"email": "x@example.com"},
        headers={"X-Parlio-User": "new@example.com"},
    )
    assert r.status_code == 403
    # strangers cannot list members
    r = await client.get(
        f"/v1/organisations/{DEV_TENANT}/members", headers={"X-Parlio-User": "who@example.com"}
    )
    assert r.status_code == 403

    r = await client.delete(f"/v1/organisations/{DEV_TENANT}/members/{uid}")
    assert r.status_code == 204
    assert len((await client.get(f"/v1/organisations/{DEV_TENANT}/members")).json()) == 1
    owner = (await client.get(f"/v1/organisations/{DEV_TENANT}/members")).json()[0]
    r = await client.delete(f"/v1/organisations/{DEV_TENANT}/members/{owner['user_id']}")
    assert r.status_code == 400


# -- analytics ---------------------------------------------------------------------------------


async def test_analytics_overview(client: AsyncClient, app: FastAPI) -> None:
    await _call(client, "a1", "+447700900001", duration=60)
    await _call(client, "a2", "+447700900001", duration=120)
    await _call(client, "m1", "+447700900002", answered=False, reason="no_answer", duration=0)
    proc: PostCallProcessor = app.state.postcall
    await proc.drain()
    r = await client.get("/v1/analytics/overview", params={"tenant_id": "demo", "days": 7})
    assert r.status_code == 200, r.text
    a = r.json()
    cur = a["current"]
    assert cur["total_calls"] == 3 and cur["answered"] == 2 and cur["missed"] == 1
    assert cur["answer_rate"] == pytest.approx(0.667, abs=1e-3)
    assert cur["avg_duration_s"] == 90.0
    assert cur["unique_callers"] == 2 and cur["avg_calls_per_caller"] == 1.5
    assert sum(a["by_hour"]) == 3 and sum(a["by_weekday"]) == 3
    assert len(a["daily"]) == 7 and sum(p["calls"] for p in a["daily"]) == 3
    assert a["usage"]["calls"] == 3 and a["usage"]["minutes"] == 3.0
    assert a["prospects"]["contacts"] == 2 and a["prospects"]["returning_callers"] == 1
    assert a["previous"]["total_calls"] == 0 and a["change"]["total_calls"] is None
    assert (await client.get("/v1/analytics/overview", params={"days": 0})).status_code == 422


async def test_analytics_query_and_ask_ai(client: AsyncClient, app: FastAPI) -> None:
    await _call(client, "q1", "+447700900001", duration=60)
    await _call(client, "q2", "+447700900001", duration=120)
    await _call(client, "q3", "+447700900002", answered=False, reason="no_answer", duration=0)
    proc: PostCallProcessor = app.state.postcall
    await proc.drain()

    r = await client.post("/v1/analytics/query", json={"question": "last 7 days vs last month"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["question"]["source"] == "rules" and a["compare"] is not None
    assert a["current"]["summary"]["total_calls"] == 3
    assert a["current"]["first_time_callers"] == 2 and a["current"]["returning_callers"] == 1
    assert a["current"]["business_hours_calls"] + a["current"]["after_hours_calls"] == 3
    assert sum(a["current"]["by_weekday"]) == 3 and len(a["current"]["daily"]) == 7
    assert "total_calls" in a["change"]

    r = await client.post("/v1/analytics/query", json={"question": "after hours this week"})
    assert r.status_code == 200
    assert r.json()["question"]["period"]["hours"] == "after"
    assert r.json()["compare"] is None

    assert (await client.post("/v1/analytics/query", json={})).status_code == 422


async def test_create_assistant_plan_limit_clone_and_isolation(client: AsyncClient) -> None:
    from parlio_voice.config_client import DEMO_CONFIG

    assert DEMO_CONFIG.business.services and len(DEMO_CONFIG.faqs) >= 6
    assert set(DEMO_CONFIG.hours.hours) == {"mon", "tue", "wed", "thu", "fri", "sat"}
    assert {d.department for d in DEMO_CONFIG.transfer.destinations} >= {"general", "emergencies"}

    # default tenant is on Starter (1 assistant) -> blocked with an upgrade message
    r = await client.post("/v1/assistants", json={"name": "Aria", "business_name": "Demo Sales"})
    assert r.status_code == 403 and "upgrade" in r.json()["detail"]

    r = await client.post(
        "/v1/billing/subscription", params={"tenant_id": DEV_TENANT}, json={"plan_id": "growth"}
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        "/v1/assistants",
        json={"name": "Aria", "business_name": "Demo Sales", "copy_from": "demo"},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["assistant_id"] != "demo" and created["tenant_id"] == DEV_TENANT
    assert created["assistant_version"] == 1 and created["name"] == "Aria"
    assert created["business"]["services"] == DEMO_CONFIG.business.services  # cloned
    ids = {a["assistant_id"] for a in (await client.get("/v1/assistants")).json()}
    assert created["assistant_id"] in ids

    # viewers cannot create; strangers cannot target the tenant
    await client.post(
        f"/v1/organisations/{DEV_TENANT}/members",
        json={"email": "viewer@example.com", "role": "viewer"},
    )
    r = await client.post(
        "/v1/assistants",
        json={"name": "X", "business_name": "Y"},
        headers={"X-Parlio-User": "viewer@example.com"},
    )
    assert r.status_code == 403
    r = await client.post(
        "/v1/assistants",
        json={"name": "X", "business_name": "Y", "tenant_id": DEV_TENANT},
        headers={"X-Parlio-User": "stranger@example.com"},
    )
    assert r.status_code == 403
