"""Phase 21c/21d/21e + 21b leftovers: advisor, guardrails, contact intelligence, CSV/reports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.advisor import (
    AdviceStatus,
    AdvisorService,
    Evidence,
    Recommendation,
    Reworder,
    detect,
    metric_value,
    validate_claims,
    validate_tone,
)
from parlio_api.billing import PLANS, CostRates
from parlio_api.contacts import ContactRules, caller_context_instruction
from parlio_api.insights import Gap, InsightInputs, TransferRow, build_insights
from parlio_api.reports import SECTIONS, ReportSchedule, insights_csv
from parlio_api.store import Contact, ContactUpdate, MemoryStore
from parlio_voice.models import CallEvent, CallEventType

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
HEADERS = {"X-Worker-Key": "dev-worker-key"}
DEMO = {"tenant_id": "demo"}


def _report(**patch: Any) -> Any:
    base = build_insights(InsightInputs(tenant_id="demo"), days=28, now=NOW)
    return base.model_copy(update=patch, deep=True)


# -- 21c: deterministic detection -----------------------------------------------------------------


def test_detect_transfer_hotspot_and_faq_gap() -> None:
    rep = _report()
    rep.transfers.by_department = [
        TransferRow(label="Accounts", attempts=10, answered=3, answer_rate=0.3),
        TransferRow(label="Sales", attempts=10, answered=9, answer_rate=0.9),
    ]
    rep.intents.gaps = [Gap(question="Do you do gas safety certificates?", count=4, status="open")]
    rep.intents.unanswered_questions = 4
    recs = detect(rep, run_id="r1")
    rules = {r.rule for r in recs}
    assert "transfer_hotspot:Accounts" in rules and "transfer_hotspot:Sales" not in rules
    assert any(r.rule.startswith("faq_gap:") for r in recs)
    hot = next(r for r in recs if r.rule == "transfer_hotspot:Accounts")
    assert hot.priority == 1 and hot.actions[0].kind == "rule"
    assert {e.label for e in hot.evidence} >= {"Accounts answer rate", "Missed"}
    # every number in the rule-based summary is backed by evidence
    assert validate_claims(hot.summary, hot.evidence) == []
    assert metric_value(rep, hot.metric or "") == 0.3
    assert metric_value(rep, "sla.breach_rate") is None
    assert metric_value(rep, "nope.nothing") is None


def test_detect_is_quiet_for_an_empty_tenant() -> None:
    assert detect(_report(), run_id="r0") == []


# -- 21d: guardrails -------------------------------------------------------------------------------


def test_numeric_claims_must_come_from_evidence() -> None:
    ev = [Evidence(label="Answer rate", value="30%"), Evidence(label="Missed", value="7")]
    assert validate_claims("Only 30% answered, 7 missed.", ev) == []
    assert validate_claims("Only 30% answered; you lost £1,200.", ev) == ["1200"]
    assert (
        validate_claims("Roughly 7 calls, £1,200", [*ev, Evidence(label="v", value="£1,200")]) == []
    )


def test_tone_rejects_promises_and_pii() -> None:
    assert validate_tone("Add cover on Tuesdays.") == []
    assert validate_tone("This will guarantee more sales!") != []
    assert validate_tone("Call Dave on 07700 900123.") != []
    assert validate_tone("Email jo@example.com") != []


async def test_reworder_rejects_unsupported_wording() -> None:
    replies = iter(
        [
            "You lost 42 customers last month.",  # unsupported number -> rejected
            "Only 30% of Accounts transfers were answered; add cover.",  # ok
        ]
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": next(replies)}}],
                "usage": {"prompt_tokens": 400, "completion_tokens": 40},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://llm")
    rw = Reworder("key", client=client)
    rec = Recommendation(
        tenant_id="demo",
        run_id="r",
        rule="x",
        area="transfers",
        title="Accounts misses transfers",
        summary="s",
        evidence=[Evidence(label="Accounts answer rate", value="30%")],
        expected_impact="i",
        confidence=0.5,
    )
    text, cost = await rw.reword(rec)
    assert text is None and cost > 0
    text, _ = await rw.reword(rec)
    assert text and text.startswith("Only 30%")
    assert Reworder(None).available is False


async def test_advisor_run_lifecycle_cap_and_isolation(client: AsyncClient, app: FastAPI) -> None:
    advisor: AdvisorService = app.state.advisor
    rep = _report()
    rep.transfers.by_department = [
        TransferRow(label="Accounts", attempts=10, answered=3, answer_rate=0.3)
    ]
    run = await advisor.run("demo", report=rep)
    assert run.generated == 1 and run.llm_calls == 0  # no LLM configured in tests
    recs = await advisor.recommendations("demo")
    assert len(recs) == 1 and recs[0].status == AdviceStatus.NEW
    # a second run doesn't duplicate an open recommendation
    assert (await advisor.run("demo", report=rep)).generated == 0
    # other tenants never see it
    assert await advisor.recommendations("acme") == []
    assert await advisor.get("acme", recs[0].id) is None

    # LLM budget: cap 0 means no rewording even if a reworder is present
    s = await advisor.settings("demo")
    s.llm_monthly_cap_pence = 0
    await advisor.save_settings("demo", s)
    assert await advisor.llm_spent_this_month("demo") == 0.0

    # routes: overview, snooze, dismiss, apply (rule -> new assistant version)
    r = await client.get("/v1/advisor", params=DEMO)
    assert r.status_code == 200 and len(r.json()["recommendations"]) == 1
    rid = recs[0].id
    r = await client.post(f"/v1/advisor/{rid}/snooze", params=DEMO, json={"days": 7})
    assert r.status_code == 200 and r.json()["status"] == "snoozed"
    r = await client.post(f"/v1/advisor/{rid}/apply", params=DEMO, json={"action_index": 0})
    assert r.status_code == 200 and r.json()["status"] == "applied"
    cfg = await app.state.store.get_assistant("demo")
    assert cfg is not None and any(
        r.instruction.startswith("Before transferring") for r in cfg.rules
    )
    assert (await client.post(f"/v1/advisor/{rid}/dismiss", params=DEMO)).status_code == 200
    assert (await client.post("/v1/advisor/nope/dismiss", params=DEMO)).status_code == 404
    r = await client.get("/v1/audit", params=DEMO)
    if r.status_code == 200:
        actions = {e["action"] for e in r.json()}
        assert {"advisor.apply", "advisor.dismiss", "advisor.snooze"} <= actions


async def test_advisor_is_growth_plus(client: AsyncClient, app: FastAPI) -> None:
    billing = app.state.billing
    sub = await billing.change_plan("demo", "starter")
    sub.status = "active"
    sub.trial_ends_at = None
    await billing._save(sub)
    r = await client.get("/v1/advisor", params=DEMO)
    assert r.status_code == 403
    await billing.change_plan("demo", "growth")
    assert (await client.get("/v1/advisor", params=DEMO)).status_code == 200


async def test_weekly_digest_due_and_dispatch(client: AsyncClient, app: FastAPI) -> None:
    advisor: AdvisorService = app.state.advisor
    s = await advisor.settings("demo")
    s.digest_weekday, s.digest_hour = 0, 8
    await advisor.save_settings("demo", s)
    monday_8 = datetime(2026, 9, 14, 7, 0, tzinfo=UTC)  # 08:00 London
    assert await advisor.due("demo", monday_8) is True
    assert await advisor.due("demo", monday_8 + timedelta(hours=1)) is False
    r = await client.post(
        "/v1/notifications/rules",
        params=DEMO,
        json={"channel": "email", "target": "owner@demo.test", "events": ["advisor.digest"]},
    )
    assert r.status_code == 201
    assert await advisor.sweep(monday_8) >= 1
    assert await advisor.due("demo", monday_8) is False  # already ran this week
    log = (await client.get("/v1/notifications/log", params=DEMO)).json()
    assert any(n["event"] == "advisor.digest" for n in log)


# -- 21b leftovers: CSV + scheduled report ---------------------------------------------------------


def test_csv_every_section() -> None:
    rep = _report()
    rep.transfers.by_department = [TransferRow(label="Accounts", attempts=2, answered=1)]
    rep.intents.gaps = [Gap(question="Weekend rates?", count=2, status="open")]
    for section in SECTIONS:
        out = insights_csv(rep, section)
        lines = out.strip().splitlines()
        assert len(lines) >= 1 and "," in lines[0], section
    assert "Accounts" in insights_csv(rep, "transfers")
    assert "Weekend rates?" in insights_csv(rep, "gaps")


async def test_csv_export_and_report_schedule_routes(client: AsyncClient, app: FastAPI) -> None:
    r = await client.get("/v1/analytics/insights.csv", params={**DEMO, "section": "summary"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    r = await client.get("/v1/analytics/insights.csv", params={**DEMO, "section": "bogus"})
    assert r.status_code == 422

    r = await client.get("/v1/analytics/reports", params=DEMO)
    assert r.status_code == 200 and r.json()["schedule"]["enabled"] is False
    r = await client.put(
        "/v1/analytics/reports/schedule",
        params=DEMO,
        json={
            "tenant_id": "demo",
            "enabled": True,
            "cadence": "weekly",
            "weekday": 0,
            "hour": 8,
            "sections": ["demand", "sla"],
        },
    )
    assert r.status_code == 200 and r.json()["sections"] == ["demand", "sla"]
    await client.post(
        "/v1/notifications/rules",
        params=DEMO,
        json={"channel": "email", "target": "owner@demo.test", "events": ["analytics.report"]},
    )
    r = await client.post("/v1/analytics/reports/send", params={**DEMO, "days": 7})
    assert r.status_code == 200, r.text
    r = await client.get("/v1/analytics/reports", params=DEMO)
    assert len(r.json()["history"]) == 1
    reports = app.state.reports
    monday_8 = datetime.now(UTC).replace(hour=7, minute=0, second=0, microsecond=0)
    monday_8 += timedelta(days=(7 - monday_8.weekday()) % 7 or 7, weeks=1)  # a Monday > 6d away
    assert await reports.due("demo", monday_8) is True
    assert await reports.due("acme", monday_8) is False
    assert await reports.sweep(monday_8) >= 1
    assert len((await client.get("/v1/analytics/reports", params=DEMO)).json()["history"]) == 2
    assert ReportSchedule(tenant_id="t", cadence="monthly").day_of_month == 1
    with pytest.raises(ValueError):
        ReportSchedule(tenant_id="t", cadence="daily")


# -- 21e: contact intelligence ---------------------------------------------------------------------


def _ev(t: CallEventType, call_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return CallEvent(
        type=t,
        call_id=call_id,
        tenant_id="demo",
        company_id="demo",
        assistant_id="demo",
        payload=payload,
    ).model_dump(mode="json")


async def _call(client: AsyncClient, call_id: str, caller: str) -> None:
    for e in (
        _ev(CallEventType.CALL_STARTED, call_id, {"caller": caller, "dialed": "+4400"}),
        _ev(CallEventType.CALL_ANSWERED, call_id, {"answer_latency_s": 0.4}),
        _ev(CallEventType.CALL_ENDED, call_id, {"reason": "hangup", "duration_s": 30}),
    ):
        r = await client.post("/v1/worker/events", json=e, headers=HEADERS)
        assert r.status_code == 202, r.text


async def test_auto_promotion_vip_rules_and_manual_pin(client: AsyncClient, app: FastAPI) -> None:
    ci = app.state.contacts
    await _call(client, "c1", "+447700900010")
    await _call(client, "c2", "+447700900011")
    contacts = {c.e164: c for c in await app.state.store.list_contacts("demo")}
    a, b = contacts["+447700900010"], contacts["+447700900011"]
    assert a.status == "prospect" and not a.status_pinned

    # booking promotes A and adds value
    await ci.on_booking("demo", "+447700900010", name="Ann", value_pence=12_000)
    a2 = await app.state.store.get_contact(a.id)
    assert a2 and a2.status == "customer" and a2.status_source == "auto:booking"
    assert a2.lifetime_value_pence == 12_000 and a2.name == "Ann"

    # manual choice pins B; automation must not override it
    r = await client.patch(f"/v1/contacts/{b.id}", json={"status": "blocked"})
    assert r.status_code == 200 and r.json()["status_pinned"] is True
    await ci.on_payment_paid("demo", "+447700900011", 50_000)
    b2 = await app.state.store.get_contact(b.id)
    assert b2 and b2.status == "blocked" and b2.lifetime_value_pence == 50_000
    assert await ci.on_crm_status("demo", "+447700900011", "customer") is not None
    assert (await app.state.store.get_contact(b.id)).status == "blocked"

    # VIP rule by lifetime value; pinned contacts are skipped
    r = await client.put(
        "/v1/contacts/rules",
        params=DEMO,
        json={"vip_min_value_pence": 10_000, "vip_department": "Accounts"},
    )
    assert r.status_code == 200
    await ci.after_call(a.id)
    a3 = await app.state.store.get_contact(a.id)
    assert a3 and a3.vip is True
    await ci.after_call(b.id)
    assert (await app.state.store.get_contact(b.id)).vip is False

    # rules are disabled when auto_promote is off
    r = await client.put(
        "/v1/contacts/rules",
        params=DEMO,
        json={"auto_promote": False, "vip_department": "Accounts"},
    )
    await _call(client, "c3", "+447700900012")
    await ci.on_booking("demo", "+447700900012", name=None, value_pence=0)
    c = next(x for x in await app.state.store.list_contacts("demo") if x.e164 == "+447700900012")
    assert c.status == "prospect"

    # caller context for the worker (A is a VIP customer)
    r = await client.get(
        "/v1/worker/caller-context",
        params={"assistant_id": "demo", "caller": "+447700900010"},
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    ctx = r.json()
    assert ctx["known"] and ctx["name"] == "Ann" and ctx["vip"] and ctx["status"] == "customer"
    assert ctx["vip_department"] == "Accounts"
    assert "Greet them by name" in ctx["instruction"] and "VIP handling" in ctx["instruction"]
    assert "07700" not in ctx["instruction"]  # never recites the number
    r = await client.get(
        "/v1/worker/caller-context",
        params={"assistant_id": "demo", "caller": "+447700999999"},
        headers=HEADERS,
    )
    assert r.json()["known"] is False and r.json()["instruction"] == ""
    # cross-tenant: acme can't read demo's contact rules
    assert (await client.get("/v1/contacts/rules", params={"tenant_id": "acme"})).status_code in (
        403,
        404,
    )


def test_caller_context_instruction_is_empty_for_strangers() -> None:
    from parlio_api.contacts import CallerContext

    assert caller_context_instruction(CallerContext()) is None
    txt = caller_context_instruction(CallerContext(known=True, name="Ann", call_count=2))
    assert txt and '"Ann"' in txt and "2 previous call(s)" in txt


async def test_named_account_vip_rule() -> None:
    from parlio_api.contacts import ContactIntelligence

    ci = ContactIntelligence(MemoryStore(None))
    rules = ContactRules(vip_named_accounts=["Acme Ltd", "+44 7700 900 020"])
    c = Contact(id="k1", tenant_id="demo", company_id="demo", e164="+447700900020", name="Someone")
    assert ci.vip_rule_hit(c, rules) == "named account"
    c2 = Contact(
        id="k2", tenant_id="demo", company_id="demo", e164="+447700900021", name="acme  ltd"
    )
    assert ci.vip_rule_hit(c2, rules) == "named account"
    c3 = Contact(id="k3", tenant_id="demo", company_id="demo", e164="+447700900022", name="Bob")
    assert ci.vip_rule_hit(c3, rules) is None
    assert ContactUpdate(status_pinned=True).status_pinned is True


# -- economics -------------------------------------------------------------------------------------


def test_cost_rates_per_provider_and_plan_defaults() -> None:
    r = CostRates()
    cart = r.per_minute(provider="cartesia", browser=False, recorded=False)
    el = r.per_minute(provider="elevenlabs", browser=False, recorded=False)
    assert cart == pytest.approx(3.3) and el == pytest.approx(6.3)
    assert r.per_minute(provider=None, browser=True, recorded=True) == pytest.approx(2.6)
    assert r.tts_for("ELEVENLABS") == 4.5 and r.tts_for("unknown") == 1.5
    plans = {p.id: p for p in PLANS}
    assert (plans["starter"].monthly_pence, plans["starter"].included_minutes) == (7900, 350)
    assert (plans["starter"].included_sms, plans["starter"].sms_overage_pence) == (75, 6)
    assert (plans["growth"].monthly_pence, plans["growth"].included_minutes) == (19900, 1500)
    assert (plans["growth"].included_sms, plans["growth"].overage_pence_per_minute) == (400, 10)
    assert (plans["scale"].monthly_pence, plans["scale"].included_minutes) == (49900, 4000)
    assert (plans["scale"].included_sms, plans["scale"].overage_pence_per_minute) == (1000, 8)
    assert plans["enterprise"].overage_pence_per_minute == 14
    assert plans["enterprise"].sms_overage_pence == 5
