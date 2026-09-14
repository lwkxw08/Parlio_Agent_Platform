"""Phase 13 (QA scoring, insights, simulation, voice-clone seam) and Phase 14 (business value,
white-label, compliance pack, 2FA / sessions / SSO / SCIM seams)."""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from parlio_api import qa as qa_mod
from parlio_api.auth import DEV_TENANT
from parlio_api.calendar import BOOKING_KIND, Booking
from parlio_api.inbox import AgentTurn, Thread
from parlio_api.notifications import NotificationEvent, NotifyEvent
from parlio_api.qa import (
    Expectation,
    HeuristicScorer,
    InsightKind,
    InsightStatus,
    OpenAIScorer,
    QAFlag,
    QAService,
    QASettings,
    Scenario,
    SimulationService,
    cluster_questions,
    unanswered_questions,
)
from parlio_api.security import (
    Require2FA,
    SecurityPolicy,
    SsoConfig,
    SsoProvider,
    totp_code,
    verify_totp,
)
from parlio_api.store import CallRecord, Member, MemoryStore, TenantDoc
from parlio_api.value import (
    TrackingNumber,
    ValueService,
    ValueSettings,
    lead_score,
)
from parlio_api.whitelabel import Branding, WhiteLabelService, audit_csv
from parlio_voice.models import AssistantConfig, CallEventType, Faq, RegionProfile

from .test_api import HEADERS, ev

Q = {"tenant_id": DEV_TENANT}
OTHER = {"X-Parlio-User": "intruder@other.example"}


def _call(
    *lines: tuple[str, str],
    call_id: str = "c1",
    tenant: str = "demo",
    caller: str = "+447700900001",
    status: str = "completed",
    answered: bool = True,
    duration: float = 120.0,
    **extra: Any,
) -> CallRecord:
    now = datetime.now(UTC)
    return CallRecord(
        call_id=call_id,
        tenant_id=tenant,
        company_id=tenant,
        assistant_id="demo",
        caller=caller,
        status=status,
        started_at=now - timedelta(seconds=duration),
        answered_at=now - timedelta(seconds=duration - 1) if answered else None,
        ended_at=now,
        duration_s=duration,
        transcript=[{"role": r, "text": t} for r, t in lines],
        **extra,
    )


async def _ingest(store: MemoryStore, c: CallRecord) -> None:
    from parlio_voice.models import CallEvent

    def mk(t: CallEventType, p: dict[str, Any]) -> CallEvent:
        return CallEvent(
            type=t,
            call_id=c.call_id,
            tenant_id=c.tenant_id,
            company_id=c.company_id,
            assistant_id=c.assistant_id,
            payload=p,
        )

    await store.apply_event(
        mk(CallEventType.CALL_STARTED, {"caller": c.caller, "dialed": c.dialed})
    )
    if c.answered_at:
        await store.apply_event(mk(CallEventType.CALL_ANSWERED, {"answer_latency_s": 0.5}))
    await store.apply_event(
        mk(CallEventType.CALL_ENDED, {"duration_s": c.duration_s, "transcript": c.transcript})
    )


GOOD = [
    ("assistant", "Hello, Demo Plumbing, how can I help?"),
    ("user", "Hi, I'd like to book a boiler service next week."),
    ("assistant", "Certainly, I can book that for you. What day suits you?"),
    ("user", "Tuesday morning please."),
    ("assistant", "Great, that's booked for Tuesday morning. Anything else?"),
    ("user", "No that's all, thanks a lot, goodbye."),
]
BAD = [
    ("assistant", "Hello."),
    ("user", "Do you do gas safety certificates?"),
    ("assistant", "I'm not sure about that."),
    ("user", "This is ridiculous, useless. What are your prices for a landlord certificate?"),
    ("assistant", "I don't have that information."),
]


# -- QA scoring ------------------------------------------------------------------------------------


async def test_heuristic_scorer_dimensions() -> None:
    good = await HeuristicScorer().score(_call(*GOOD), None)
    bad = await HeuristicScorer().score(_call(*BAD, call_id="c2"), None)
    assert good.overall > bad.overall
    assert good.resolution >= 7 and good.tone >= 7
    assert bad.resolution < good.resolution
    assert bad.tone < good.tone
    assert QAFlag.UNANSWERED in bad.flags
    assert QAFlag.UNRESOLVED in bad.flags
    assert bad.unanswered  # extracted questions the AI could not answer
    assert good.scorer == "heuristic"


async def test_openai_scorer_uses_llm_then_falls_back() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            content = json.dumps(
                {
                    "resolution": 9,
                    "tone": 8,
                    "accuracy": 3,
                    "hallucination_risk": 8,
                    "hallucinations": ["invented a 24h emergency line"],
                    "unanswered": [],
                    "notes": ["confident but wrong"],
                }
            )
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        return httpx.Response(500, text="boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x")
    scorer = OpenAIScorer("k", client=client)
    s1 = await scorer.score(_call(*GOOD), None)
    assert s1.scorer == "openai"
    assert s1.hallucination_risk == 8 and QAFlag.HALLUCINATION in s1.flags
    s2 = await scorer.score(_call(*GOOD), None)
    assert s2.scorer == "heuristic"  # deterministic fallback when the vendor errors


async def test_qa_service_scores_and_alerts_low_scores() -> None:
    store = MemoryStore(None)
    sent: list[NotificationEvent] = []

    class FakeNotifications:
        async def dispatch(self, e: NotificationEvent) -> Any:
            sent.append(e)

    qa = QAService(store, HeuristicScorer(), FakeNotifications())  # type: ignore[arg-type]
    await qa.save_settings(QASettings(tenant_id="demo", alert_below=6))
    assert await qa.score_call(_call(*GOOD)) is not None
    assert not [e for e in sent if e.event == NotifyEvent.QA_LOW_SCORE]
    bad = await qa.score_call(_call(*BAD, call_id="c2"))
    assert bad is not None and bad.overall < 6
    alerts = [e for e in sent if e.event == NotifyEvent.QA_LOW_SCORE]
    assert len(alerts) == 1 and alerts[0].context["call_id"] == "c2"
    # idempotent: re-scoring without force reuses the stored score
    assert await qa.get_score("demo", "c2") is not None
    again = await qa.score_call(_call(*BAD, call_id="c2"))
    assert again is not None and again.created_at == bad.created_at  # cached, not rescored
    stats = await qa.stats("demo")
    assert stats.scored == 2 and stats.avg_overall is not None
    # too-short calls are skipped
    assert await qa.score_call(_call(("user", "hi"), call_id="c3")) is None


# -- insight engine --------------------------------------------------------------------------------


def test_unanswered_extraction_and_clustering() -> None:
    qs = unanswered_questions(_call(*BAD))
    assert any("gas safety" in q.lower() for q in qs)
    clusters = cluster_questions(
        [
            ("Do you do gas safety certificates?", "c1"),
            ("do you do gas safety certificate", "c2"),
            ("Can I get a gas safety certificate?", "c3"),
            ("What are your opening hours on Sunday?", "c4"),
        ]
    )
    assert len(clusters) == 2
    rep, examples, ids = clusters[0]
    assert set(ids) == {"c1", "c2", "c3"} and len(examples) == 3 and "gas" in rep.lower()


async def test_insights_rebuild_apply_and_persist(client: AsyncClient) -> None:
    for i in range(3):
        cid = f"qa-{i}"
        for e in [
            ev(CallEventType.CALL_STARTED, cid, {"caller": f"+44770090010{i}"}),
            ev(CallEventType.CALL_ANSWERED, cid, {"answer_latency_s": 0.4}),
            ev(
                CallEventType.CALL_ENDED,
                cid,
                {"transcript": [{"role": r, "text": t} for r, t in BAD]},
            ),
        ]:
            r = await client.post("/v1/worker/events", json=e, headers=HEADERS)
            assert r.status_code in (200, 202), r.text
    r = await client.get("/v1/quality/overview", params=Q)
    assert r.status_code == 200, r.text
    assert r.json()["stats"]["scored"] == 3
    r = await client.post("/v1/quality/insights/rebuild", params=Q)
    assert r.status_code == 200, r.text
    insights = r.json()
    assert insights, "expected suggested FAQs from repeated unanswered questions"
    faq = next(i for i in insights if i["kind"] == InsightKind.FAQ)
    assert faq["count"] == 3 and faq["status"] == InsightStatus.OPEN

    before = (await client.get("/v1/assistants")).json()[0]["assistant_version"]
    r = await client.post(
        f"/v1/quality/insights/{faq['id']}/apply",
        params=Q,
        json={"answer": "Yes — landlord gas safety certificates from £75, booked within 3 days."},
    )
    assert r.status_code == 200, r.text
    cfg = AssistantConfig.model_validate((await client.get("/v1/assistants")).json()[0])
    assert cfg.assistant_version > before
    assert any("gas safety" in f.answer.lower() for f in cfg.faqs)
    r = await client.get("/v1/quality/insights", params=Q)
    applied = next(i for i in r.json() if i["id"] == faq["id"])
    assert (
        applied["status"] == InsightStatus.APPLIED
        and applied["applied_version"] == cfg.assistant_version
    )

    # tenant isolation
    r = await client.get("/v1/quality/overview", params=Q, headers=OTHER)
    assert r.status_code == 403
    r = await client.post(f"/v1/quality/insights/{faq['id']}/dismiss", params=Q, headers=OTHER)
    assert r.status_code == 403


# -- simulation sandbox ----------------------------------------------------------------------------


class EchoAgent:
    """Deterministic TextAgent: answers from FAQs, otherwise apologises."""

    name = "echo"

    async def respond(self, cfg: AssistantConfig, thread: Thread, history: list[Any]) -> AgentTurn:
        low = " ".join(m.text.lower() for m in history if m.author == "contact")
        for f in cfg.faqs:
            if any(w in low for w in f.question.lower().split() if len(w) > 4):
                return AgentTurn(reply=f.answer)
        return AgentTurn(
            reply="Sorry, I'm not sure about that, I'll ask a colleague.", handoff=True
        )


async def test_simulation_scripted_callers_and_ab() -> None:
    store = MemoryStore(None)
    base = AssistantConfig(
        tenant_id="demo", company_id="demo", assistant_id="demo", business_name="Demo"
    )
    await store.upsert_assistant(base, [])
    sim = SimulationService(store, EchoAgent(), HeuristicScorer())
    sc = await sim.save_scenario(
        Scenario(
            tenant_id="demo",
            name="Landlord cert",
            turns=["Hello, do you do landlord gas safety certificates?", "How much is it?"],
            expect=Expectation(mentions=["certificate"], handoff=False, min_overall=5),
        )
    )
    variant = base.model_copy(deep=True)
    variant.faqs.append(
        Faq(question="landlord gas safety certificate", answer="Yes, certificates from £75.")
    )
    run = await sim.run("demo", "demo", [sc.id], draft=base, variant_b=variant)
    assert len(run.results) == 2
    a, b = run.results
    assert a.label == "A" and b.label == "B"
    assert a.config_source == "draft"
    assert not a.passed and b.passed
    assert run.winner == "B"
    assert (await sim.runs("demo"))[0].id == run.id
    assert await sim.runs("other") == []


async def test_simulation_route_uses_draft_and_is_tenant_scoped(client: AsyncClient) -> None:
    r = await client.put(
        "/v1/quality/scenarios",
        params=Q,
        json={"name": "Hours", "turns": ["What time do you open?"], "expect": {"min_overall": 0}},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    r = await client.post(
        "/v1/quality/simulate", params=Q, json={"assistant_id": "demo", "scenario_ids": [sid]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["turns"]
    r = await client.post(
        "/v1/quality/simulate",
        params=Q,
        headers=OTHER,
        json={"assistant_id": "demo", "scenario_ids": [sid]},
    )
    assert r.status_code == 403
    assert (await client.get("/v1/quality/scenarios", params=Q, headers=OTHER)).status_code == 403


async def test_voice_clone_requires_consent_and_stores_no_audio(client: AsyncClient) -> None:
    sample = base64.b64encode(b"RIFF" + b"\0" * 64).decode()
    body = {
        "assistant_id": "demo",
        "name": "Keith",
        "consent": False,
        "sample_base64": sample,
        "sample_seconds": 42,
    }
    r = await client.post("/v1/quality/voice-clones", params=Q, json=body)
    assert r.status_code == 400
    body["consent"] = True
    r = await client.post("/v1/quality/voice-clones", params=Q, json=body)
    assert r.status_code == 201, r.text
    clone = r.json()
    assert clone["status"] == "ready" and clone["provider"] == "simulated"
    assert clone["consent_by"] and clone["consent_statement"]
    assert sample not in r.text and "RIFF" not in r.text
    r = await client.post(f"/v1/quality/voice-clones/{clone['id']}/activate", params=Q)
    assert r.status_code == 200, r.text
    cfg = AssistantConfig.model_validate((await client.get("/v1/assistants")).json()[0])
    assert cfg.voice.voice_id == clone["provider_voice_id"]
    r = await client.delete(f"/v1/quality/voice-clones/{clone['id']}", params=Q)
    assert r.status_code == 204
    cfg = AssistantConfig.model_validate((await client.get("/v1/assistants")).json()[0])
    assert cfg.voice.voice_id != clone["provider_voice_id"]
    assert (
        await client.get("/v1/quality/voice-clones", params=Q, headers=OTHER)
    ).status_code == 403


# -- Phase 14: business value ----------------------------------------------------------------------


def test_lead_score_grades() -> None:
    hot = lead_score(
        _call(
            (
                "user",
                "I need an emergency plumber, my kitchen is flooding, can you book someone today?",
            ),
            ("assistant", "Yes, booking now."),
            extracted={"name": "Ann", "phone": "07700900123", "postcode": "SW1A 1AA"},
            caller_type="new",
            duration=240,
        )
    )
    cold = lead_score(_call(("user", "wrong number sorry"), duration=8, caller_type="returning"))
    missed = lead_score(_call(status="completed", answered=False, duration=0))
    assert hot.grade == "hot" and hot.intent == "emergency"
    assert cold.grade == "cold"
    assert missed.score < hot.score and any("missed" in r.lower() for r in missed.reasons)
    assert lead_score(_call(*GOOD), booked=True).score > lead_score(_call(*GOOD)).score


async def test_revenue_attribution_and_missed_revenue() -> None:
    store = MemoryStore(None)
    value = ValueService(store)
    await value.save_settings(
        ValueSettings(
            tenant_id="demo",
            avg_job_value_pence=10_000,
            missed_call_lead_rate=0.5,
            lead_to_sale_rate=0.5,
        )
    )
    await store.upsert_assistant(
        AssistantConfig(
            tenant_id="demo", company_id="demo", assistant_id="demo", business_name="Demo"
        ),
        [],
    )
    tn = await value.save_tracking_number(
        TrackingNumber(
            tenant_id="demo", e164="+442046200001", channel="Google Ads", monthly_cost_pence=5000
        )
    )
    calls = [
        _call(*GOOD, call_id="v1", dialed="+442046200001"),
        _call(*GOOD, call_id="v2", dialed="+442046200001", caller="+447700900002"),
        _call(call_id="v3", answered=False, duration=0, dialed="+442046209999"),
        _call(
            call_id="v4", answered=False, duration=0, dialed="+442046209999", caller="+447700900003"
        ),
    ]
    for c in calls:
        await _ingest(store, c)
    now = datetime.now(UTC)
    bk = Booking(
        tenant_id="demo",
        company_id="demo",
        connection_id="x",
        call_id="v1",
        start=now,
        end=now,
        name="Ann",
    )
    await store.put_doc(
        TenantDoc(kind=BOOKING_KIND, id=bk.id, tenant_id="demo", data=bk.model_dump(mode="json"))
    )

    rep = await value.attribute("demo", days=7)
    assert rep.calls == 4 and rep.answered == 2 and rep.missed == 2
    assert rep.bookings == 1 and rep.attributed_pence == 10_000
    assert rep.missed_revenue_pence == int(2 * 0.5 * 0.5 * 10_000)
    ch = next(c for c in rep.channels if c.channel == "Google Ads")
    assert ch.calls == 2 and ch.bookings == 1 and ch.attributed_pence == 10_000
    assert ch.cost_pence == int(5000 * 7 / 30)  # prorated to the report window
    # tracking numbers are tenant scoped
    assert await value.tracking_numbers("other") == []
    assert await value.delete_tracking_number("other", tn.id) is False
    assert await value.delete_tracking_number("demo", tn.id) is True


async def test_value_routes_and_digest(client: AsyncClient) -> None:
    r = await client.put(
        "/v1/value/tracking-numbers",
        params=Q,
        json={"e164": "+442046200002", "channel": "Website", "monthly_cost_pence": 0},
    )
    assert r.status_code == 200, r.text
    r = await client.get("/v1/value", params=Q)
    assert r.status_code == 200, r.text
    assert r.json()["report"]["currency"] == "GBP"
    assert r.json()["tracking_numbers"][0]["channel"] == "Website"
    r = await client.post("/v1/value/digest/send", params=Q)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["tenant_id"] == DEV_TENANT and "missed" in d["body"].lower()
    r = await client.get("/v1/value", params=Q)
    assert len(r.json()["digests"]) == 1
    assert (await client.get("/v1/value", params=Q, headers=OTHER)).status_code == 403
    assert (await client.post("/v1/value/digest/send", params=Q, headers=OTHER)).status_code == 403


# -- Phase 14: white-label / agency ----------------------------------------------------------------


async def test_whitelabel_branding_domain_and_agency(client: AsyncClient) -> None:
    r = await client.put(
        "/v1/whitelabel",
        params=Q,
        json={
            "brand_name": "Acme Answering",
            "primary_colour": "#112233",
            "accent_colour": "#445566",
            "hide_powered_by": True,
            "custom_domain": "app.acme-answering.co.uk",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["domain_verified"] is False
    r = await client.get("/v1/whitelabel", params=Q)
    ins = r.json()["domain"]
    assert ins["txt_name"].startswith("_parlio.") and ins["txt_value"].startswith("parlio-verify=")
    r = await client.post("/v1/whitelabel/verify-domain", params=Q, json={"txt_records": ["nope"]})
    assert r.status_code == 200 and r.json()["domain_verified"] is False
    r = await client.get("/v1/public/branding", params={"host": "app.acme-answering.co.uk"})
    assert r.json()["brand_name"] == "Parlio"  # unverified domains never serve tenant branding
    r = await client.post(
        "/v1/whitelabel/verify-domain", params=Q, json={"txt_records": [ins["txt_value"]]}
    )
    assert r.status_code == 200 and r.json()["domain_verified"] is True
    # unauthenticated public branding by host
    r = await client.get("/v1/public/branding", params={"host": "app.acme-answering.co.uk"})
    assert r.status_code == 200 and r.json()["brand_name"] == "Acme Answering"
    assert r.json()["hide_powered_by"] is True
    r = await client.get("/v1/public/branding", params={"host": "unknown.example"})
    assert r.json()["brand_name"] == "Parlio"

    # agency: create a client tenant; parent members can manage it, client data is isolated
    r = await client.post(
        "/v1/whitelabel/clients",
        params=Q,
        json={"client_name": "Bob's Boilers", "business_name": "Bob's Boilers Ltd"},
    )
    assert r.status_code == 201, r.text
    link = r.json()
    child = link["child_tenant_id"]
    assert child != DEV_TENANT
    r = await client.get("/v1/whitelabel/clients", params=Q)
    assert r.json()[0]["link"]["client_name"] == "Bob's Boilers"
    assert r.json()[0]["assistants"] == 1 and r.json()[0]["members"] >= 1
    # child inherits agency branding
    r = await client.get("/v1/whitelabel", params={"tenant_id": child})
    assert r.status_code == 200 and r.json()["branding"]["brand_name"] == "Acme Answering"
    assert r.json()["parent"]["parent_tenant_id"] == DEV_TENANT
    # child calls/billing are separate from the parent
    r = await client.get("/v1/billing/subscription", params={"tenant_id": child})
    assert r.status_code == 200
    assert (await client.get("/v1/whitelabel/clients", params=Q, headers=OTHER)).status_code == 403
    assert (
        await client.patch(
            f"/v1/whitelabel/clients/{link['id']}", params=Q, headers=OTHER, json={"notes": "x"}
        )
    ).status_code == 403


async def test_whitelabel_service_isolation() -> None:
    store = MemoryStore(None)
    wl = WhiteLabelService(store, None, dashboard_host="app.parlio.test", verify_salt="s")
    await wl.save_branding(Branding(tenant_id="a", brand_name="A", custom_domain="a.example"))
    await wl.save_branding(Branding(tenant_id="b", brand_name="B"))
    assert (await wl.branding("b")).brand_name == "B"
    assert (await wl.branding("c")).brand_name == "Parlio"
    ins = await wl.domain_instructions("a")
    assert ins is not None
    with pytest.raises(ValueError):
        await wl.verify_domain("b", [ins.txt_value])  # b has no custom domain
    await wl.save_branding(Branding(tenant_id="b", brand_name="B", custom_domain="b.example"))
    assert (await wl.verify_domain("b", [ins.txt_value])).domain_verified is False  # bound to a
    assert (await wl.verify_domain("a", [ins.txt_value])).domain_verified is True
    assert (await wl.public_branding("a.example")).brand_name == "A"


# -- Phase 14: compliance pack ---------------------------------------------------------------------


async def test_compliance_pack_and_audit_export(client: AsyncClient) -> None:
    r = await client.get("/v1/compliance/pack", params=Q)
    assert r.status_code == 200, r.text
    pack = r.json()
    assert pack["tenant_id"] == DEV_TENANT
    assert pack["assistants"][0]["region_profile"] == "standard"
    assert not pack["notes"]
    cfg = AssistantConfig.model_validate((await client.get("/v1/assistants")).json()[0])
    cfg.region_profile = RegionProfile.SOVEREIGN_UK
    r = await client.put("/v1/assistants/demo", json={"config": cfg.model_dump(mode="json")})
    assert r.status_code == 200, r.text
    pack = (await client.get("/v1/compliance/pack", params=Q)).json()
    assert pack["assistants"][0]["region_profile"] == "sovereign-uk"
    assert any(sp["region"] != "UK" for sp in pack["sub_processors"])
    assert any("sovereign" in n.lower() for n in pack["notes"])
    assert pack["retention"] and pack["security"]["require_2fa"] == "off"
    r = await client.get("/v1/compliance/audit.csv", params=Q)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].startswith("at,actor,action")
    assert (await client.get("/v1/compliance/pack", params=Q, headers=OTHER)).status_code == 403


def test_audit_csv_escapes() -> None:
    from parlio_api.observability import AuditEntry

    out = audit_csv([AuditEntry(tenant_id="t", actor="a@b", action="x,y", target='q"z')])
    assert '"x,y"' in out and '"q""z"' in out


# -- Phase 14: 2FA / sessions / SSO / SCIM ---------------------------------------------------------


def test_totp_roundtrip() -> None:
    secret = "JBSWY3DPEHPK3PXP"
    now = time.time()
    code = totp_code(secret, now)
    assert len(code) == 6 and verify_totp(secret, code, now)
    assert verify_totp(secret, code, now + 30)  # one step of drift allowed
    assert not verify_totp(secret, code, now + 120)
    assert not verify_totp(secret, "000000", now) or code == "000000"


async def test_two_factor_enrol_enforce_sessions(client: AsyncClient) -> None:
    r = await client.get("/v1/account/2fa")
    assert r.status_code == 200 and r.json()["confirmed"] is False
    # cannot enforce before enrolling
    r = await client.put("/v1/security", params=Q, json={"require_2fa": "all", "session_hours": 8})
    assert r.status_code == 400
    r = await client.post("/v1/account/2fa/enrol")
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/")
    r = await client.post("/v1/account/2fa/confirm", json={"code": "000000"})
    assert r.status_code == 400
    r = await client.post("/v1/account/2fa/confirm", json={"code": totp_code(secret)})
    assert r.status_code == 200, r.text
    codes = r.json()["codes"]
    assert len(codes) == 8
    assert (await client.get("/v1/account/2fa")).json()["confirmed"] is True

    r = await client.put("/v1/security", params=Q, json={"require_2fa": "all", "session_hours": 8})
    assert r.status_code == 200, r.text
    assert r.json()["require_2fa"] == Require2FA.ALL
    # tenant routes are now blocked until an MFA session is presented
    r = await client.get("/v1/quality/overview", params=Q)
    assert r.status_code == 403 and r.headers.get("x-parlio-mfa-required") == "1"
    r = await client.post("/v1/account/2fa/verify", json={"code": totp_code(secret)})
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    mfa = {"X-Parlio-MFA": tok}
    r = await client.get("/v1/quality/overview", params=Q, headers=mfa)
    assert r.status_code == 200
    # recovery code works once
    r = await client.post("/v1/account/2fa/verify", json={"code": codes[0]})
    assert r.status_code == 200
    tok2, sid2 = r.json()["token"], r.json()["session_id"]
    r = await client.post("/v1/account/2fa/verify", json={"code": codes[0]})
    assert r.status_code == 401
    # sessions: list, revoke one, revoke others
    r = await client.get("/v1/account/sessions", headers=mfa)
    sess = r.json()
    assert len(sess) >= 2 and sum(1 for s in sess if s["current"]) == 1
    other = next(s for s in sess if s["id"] == sid2)
    assert not other["current"] and other["mfa"]
    r = await client.delete(f"/v1/account/sessions/{other['id']}", headers=mfa)
    assert r.status_code == 204
    r = await client.get("/v1/quality/overview", params=Q, headers={"X-Parlio-MFA": tok2})
    assert r.status_code == 403
    r = await client.post("/v1/account/sessions/revoke-others", headers=mfa)
    assert r.status_code == 200
    assert (await client.get("/v1/quality/overview", params=Q, headers=mfa)).status_code == 200
    # another user cannot see or revoke these sessions
    r = await client.get("/v1/account/sessions", headers=OTHER)
    assert r.status_code == 200 and all(s["id"] != other["id"] for s in r.json())
    # wrong code fails; right code clears enrolment
    assert (
        await client.post("/v1/account/2fa/disable", json={"code": "999999"}, headers=mfa)
    ).status_code == 400
    r = await client.post("/v1/account/2fa/disable", json={"code": totp_code(secret)}, headers=mfa)
    assert r.status_code == 204
    assert (await client.get("/v1/account/2fa")).json()["confirmed"] is False


async def test_sso_config_status_and_scim(client: AsyncClient) -> None:
    r = await client.get("/v1/security", params=Q)
    assert r.status_code == 200 and r.json()["sso_status"] == "off"
    r = await client.put(
        "/v1/security",
        params=Q,
        json={
            "require_2fa": "off",
            "session_hours": 12,
            "sso": {
                "provider": "microsoft_entra",
                "protocol": "oidc",
                "domains": ["acme.co.uk"],
                "issuer": "https://login.microsoftonline.com/tid/v2.0",
                "client_id": "abc",
                "supabase_provider_id": "must-not-be-settable",
            },
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["sso"]["supabase_provider_id"] is None
    r = await client.get("/v1/security", params=Q)
    assert r.json()["sso_status"] == "configured_not_live"
    assert r.json()["policy"]["sso"]["provider"] == "microsoft_entra"

    # SCIM: issue token, provision users with it, tenant bound to token
    assert (await client.get("/scim/v2/Users")).status_code == 401
    r = await client.post("/v1/security/scim/token", params=Q)
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    r = await client.post(
        "/scim/v2/Users",
        headers=hdr,
        json={"userName": "Jo@Acme.co.uk", "displayName": "Jo Bloggs", "active": True},
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    r = await client.get("/scim/v2/Users", headers=hdr)
    assert r.json()["totalResults"] >= 2
    members = (await client.get(f"/v1/organisations/{DEV_TENANT}/members")).json()
    jo = next(m for m in members if m["email"] == "jo@acme.co.uk")
    assert jo["role"] == "member" and jo["user_id"] == uid
    r = await client.patch(
        "/scim/v2/Users/" + uid,
        headers=hdr,
        json={"Operations": [{"op": "replace", "value": {"active": False}}]},
    )
    assert r.status_code == 200 and r.json()["active"] is False
    members = (await client.get(f"/v1/organisations/{DEV_TENANT}/members")).json()
    assert not any(m["email"] == "jo@acme.co.uk" and m["status"] == "active" for m in members)
    r = await client.delete("/v1/security/scim/token", params=Q)
    assert r.status_code == 204
    assert (await client.get("/scim/v2/Users", headers=hdr)).status_code == 401
    assert (await client.get("/v1/security", params=Q, headers=OTHER)).status_code == 403


def test_sso_status_model() -> None:
    assert SsoConfig().status == "off"
    cfg = SsoConfig(provider=SsoProvider.OKTA, issuer="https://x.okta.com", client_id="c")
    assert cfg.status == "configured_not_live"
    assert cfg.model_copy(update={"supabase_provider_id": "p"}).status == "live"
    p = SecurityPolicy(tenant_id="t", require_2fa=Require2FA.ADMINS)
    assert p.require_2fa == "admins"


async def test_scim_member_model_roundtrip() -> None:
    m = Member(tenant_id="t", user_id="u", email="e@x", role="member")
    assert m.status == "active"
    assert qa_mod.SCORE_KIND == "qa_score"
