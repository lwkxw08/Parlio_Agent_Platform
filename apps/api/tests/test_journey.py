"""Phase 19: guided journey, plan recommendation, playbooks, checklist, explainability, trust."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from parlio_api.billing import PLANS
from parlio_api.journey import (
    Questionnaire,
    apply_playbook,
    explain_call,
    needed_entitlements,
    recommend_plan,
)
from parlio_api.store import CallRecord
from parlio_voice.models import AssistantConfig, CallEvent, CallEventType, Faq

# -- pure logic ----------------------------------------------------------------------------------


def test_small_business_gets_starter() -> None:
    q = Questionnaire(monthly_calls="0-50", tasks=["faqs", "messages"])
    rec = recommend_plan(q, PLANS, 14)
    assert rec.plan_id == "starter"
    assert rec.options[0].fits and rec.trial_days == 14


def test_booking_and_transfers_need_growth() -> None:
    q = Questionnaire(monthly_calls="50-200", tasks=["faqs", "book", "transfer"], team_size=6)
    assert "calendar_booking" in needed_entitlements(q)
    rec = recommend_plan(q, PLANS, 14)
    plan = next(p for p in PLANS if p.id == rec.plan_id)
    assert not plan.enterprise
    assert {"calendar_booking", "warm_transfers"} <= set(plan.entitlements)
    starter = next(o for o in rec.options if o.plan_id == "starter")
    assert not starter.fits and starter.missing


def test_high_volume_or_sovereign_goes_to_scale_or_enterprise() -> None:
    rec = recommend_plan(Questionnaire(monthly_calls="500+", tasks=["faqs"]), PLANS, 14)
    assert rec.plan_id in ("scale", "enterprise")
    rec = recommend_plan(Questionnaire(sovereign_uk=True), PLANS, 14)
    assert rec.plan_id == "enterprise"
    assert rec.estimated_minutes > 0


def test_playbook_adds_faqs_rules_and_greeting_without_clobbering() -> None:
    cfg = AssistantConfig(tenant_id="t", company_id="c", assistant_id="a")
    out = apply_playbook(cfg, "trades")
    assert any(f.source == "playbook" for f in out.faqs)
    assert out.rules and "new job" in out.greeting
    custom = cfg.model_copy(
        update={
            "greeting": "Custom hello",
            "faqs": [Faq(question="Do you give free quotes?", answer="No")],
        }
    )
    out2 = apply_playbook(custom, "trades")
    assert out2.greeting == "Custom hello"
    assert sum(1 for f in out2.faqs if f.question.lower() == "do you give free quotes?") == 1


def test_explain_call_matches_faq_and_rule() -> None:
    cfg = AssistantConfig(
        tenant_id="t",
        company_id="c",
        assistant_id="a",
        business_name="Acme",
        faqs=[Faq(question="Do you do gas boilers?", answer="Yes, we are Gas Safe registered.")],
    )
    call = CallRecord(
        call_id="c1",
        tenant_id="t",
        company_id="c",
        assistant_id="a",
        transcript=[
            {"role": "assistant", "text": cfg.rendered_greeting()},
            {"role": "user", "text": "Can you fix gas boilers?"},
            {
                "role": "assistant",
                "text": "Yes — we're Gas Safe registered so we can help with boilers.",
            },
        ],
    )
    ex = explain_call(call, cfg)
    assert ex.turns[0].evidence[0].kind == "greeting"
    assert ex.turns[1].evidence[0].kind == "faq"
    assert explain_call(call, None).turns == []


# -- API -----------------------------------------------------------------------------------------


async def test_recommend_and_verticals_endpoints(client: AsyncClient) -> None:
    r = await client.get("/v1/onboarding/verticals")
    assert r.status_code == 200 and {v["id"] for v in r.json()} >= {"trades", "salon", "general"}
    r = await client.post(
        "/v1/onboarding/recommend",
        json={"monthly_calls": "200-500", "tasks": ["faqs", "book"], "channels": ["phone", "sms"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_id"] in {p.id for p in PLANS}
    assert len(body["options"]) == len(PLANS) and body["reasons"]


async def test_guided_onboarding_sets_plan_playbook_and_checklist(client: AsyncClient) -> None:
    rec = (
        await client.post(
            "/v1/onboarding/recommend", json={"monthly_calls": "50-200", "tasks": ["book"]}
        )
    ).json()
    r = await client.post(
        "/v1/onboarding",
        json={
            "organisation_name": "Glow Salon",
            "vertical": "salon",
            "plan_id": rec["plan_id"],
            "questionnaire": {"monthly_calls": "50-200", "tasks": ["book"], "vertical": "salon"},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    tid = body["tenant_id"]
    assert body["plan_id"] == rec["plan_id"]
    assert body["trial_ends_at"] is not None
    assert any(f["source"] == "playbook" for f in body["assistant"]["faqs"])

    r = await client.get("/v1/billing/subscription", params={"tenant_id": tid})
    assert r.status_code == 200 and r.json()["plan_id"] == rec["plan_id"]
    assert r.json()["status"] == "trialing"

    r = await client.get("/v1/setup/questionnaire", params={"tenant_id": tid})
    assert r.json()["questionnaire"]["monthly_calls"] == "50-200"

    r = await client.get("/v1/setup/checklist", params={"tenant_id": tid})
    assert r.status_code == 200, r.text
    cl = r.json()
    keys = {i["key"]: i for i in cl["items"]}
    assert keys["assistant"]["done"] is True  # playbook FAQs count as published content
    assert keys["number"]["done"] is False and keys["test_call"]["done"] is False
    assert cl["live"] is False and cl["next_step"]["key"] == "number"
    assert cl["completed"] < cl["total"]


async def test_enterprise_plan_not_self_served(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/onboarding", json={"organisation_name": "Big Corp", "plan_id": "enterprise"}
    )
    assert r.status_code == 201
    assert r.json()["plan_id"] is None
    r = await client.get("/v1/billing/subscription", params={"tenant_id": r.json()["tenant_id"]})
    assert r.json()["plan_id"] == "starter"


async def test_checklist_and_explain_are_tenant_scoped(client: AsyncClient) -> None:
    r = await client.get("/v1/setup/checklist", params={"tenant_id": "someone-else"})
    assert r.status_code == 403
    r = await client.get("/v1/calls/nope/explain")
    assert r.status_code == 404


async def test_explain_endpoint_uses_pinned_version(client: AsyncClient) -> None:
    for t, payload in [
        ("call.started", {"caller": "+447700900111", "dialed": "+4400"}),
        ("call.answered", {"answer_latency_s": 0.4}),
        ("call.transcript_item", {"role": "assistant", "text": "Hi, thanks for calling."}),
        ("call.transcript_item", {"role": "user", "text": "Do you open on Saturdays?"}),
        ("call.transcript_item", {"role": "assistant", "text": "We are open Monday to Friday."}),
        ("call.ended", {"reason": "hangup", "duration_s": 20}),
    ]:
        e = CallEvent(
            type=CallEventType(t),
            call_id="c-exp",
            tenant_id="demo",
            company_id="demo",
            assistant_id="demo",
            payload=payload,
        ).model_dump(mode="json")
        r = await client.post(
            "/v1/worker/events", json=e, headers={"X-Worker-Key": "dev-worker-key"}
        )
        assert r.status_code == 202, r.text
    r = await client.get("/v1/calls/c-exp/explain")
    assert r.status_code == 200, r.text
    assert r.json()["call_id"] == "c-exp" and "note" in r.json()


async def test_checkin_sweep_sends_once(client: AsyncClient) -> None:
    from parlio_api.journey import CHECKIN_KIND, QUESTIONNAIRE_KIND, CheckInLoop

    r = await client.post(
        "/v1/onboarding",
        json={"organisation_name": "Old Co", "questionnaire": {"monthly_calls": "0-50"}},
    )
    tid = r.json()["tenant_id"]
    app = client._transport.app  # type: ignore[attr-defined]
    store = app.state.store
    doc = await store.get_doc(QUESTIONNAIRE_KIND, tid)
    doc.data["signed_up_at"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    await store.put_doc(doc)
    loop = CheckInLoop(
        store, app.state.billing, app.state.sip, app.state.calendar, app.state.notifications
    )
    await loop.sweep()
    assert await store.get_doc(CHECKIN_KIND, f"{tid}:7") is not None
    assert await store.get_doc(CHECKIN_KIND, f"{tid}:30") is None
    assert f"{tid}:7" not in await loop.sweep()  # idempotent
    r = await client.post("/v1/setup/checkins/sweep", params={"tenant_id": tid})
    assert r.status_code == 200 and r.json()["sent"] == []


async def test_public_trust_centre(client: AsyncClient) -> None:
    r = await client.get("/v1/public/trust")
    assert r.status_code == 200
    body = r.json()
    assert body["data_residency"].startswith("UK")
    assert {s["id"] for s in body["sections"]} >= {"dpa", "incident", "telephony"}
    assert any(sp["name"] == "Telnyx" for sp in body["sub_processors"])
