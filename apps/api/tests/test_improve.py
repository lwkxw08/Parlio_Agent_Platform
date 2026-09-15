"""Regression pack gate, auto-improve proposals and the Studio Speaking style prompt."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from parlio_api.drafting import Drafter
from parlio_api.improve import ImproveService
from parlio_api.inbox import AgentTurn, Thread
from parlio_api.qa import Expectation, HeuristicScorer, Scenario, SimulationService
from parlio_api.store import CallRecord, MemoryStore
from parlio_voice.models import AssistantConfig, Faq, SpeakingStyle

from .test_phase13_14 import Q, _call, _ingest


class KnowledgeAgent:
    """Answers from FAQs; hands off when a rule mentions 'transfer'; otherwise apologises."""

    name = "knowledge"

    async def respond(self, cfg: AssistantConfig, thread: Thread, history: list[Any]) -> AgentTurn:
        low = " ".join(m.text.lower() for m in history if m.author == "contact")
        for f in cfg.faqs:
            if any(w in low for w in f.question.lower().split() if len(w) > 4):
                return AgentTurn(reply=f.answer)
        if "person" in low and any("transfer" in r.instruction.lower() for r in cfg.rules):
            return AgentTurn(reply="Of course, connecting you now.", handoff=True)
        return AgentTurn(reply="Sorry, I'm not sure about that. Can I take a message?")


def _drafter() -> Drafter:
    return Drafter(None, fetch_sites=False)


async def _setup() -> tuple[MemoryStore, SimulationService, ImproveService, AssistantConfig]:
    store = MemoryStore(None)
    cfg = AssistantConfig(
        tenant_id="demo",
        company_id="demo",
        assistant_id="demo",
        business_name="Demo",
        faqs=[Faq(question="opening hours today", answer="We are open until 6pm today.")],
    )
    await store.upsert_assistant(cfg, [])
    sim = SimulationService(store, KnowledgeAgent(), HeuristicScorer())
    return store, sim, ImproveService(store, sim, _drafter()), cfg


async def test_publish_blocked_when_pack_regresses_and_force_records_it() -> None:
    store, sim, imp, _cfg = await _setup()
    hours = await sim.save_scenario(
        Scenario(
            tenant_id="demo",
            name="Hours",
            turns=["What are your opening hours today?"],
            expect=Expectation(mentions=["open"], min_overall=0),
            regression=True,
        )
    )
    assert [s.id for s in await imp.pack("demo")] == [hours.id]

    live = await store.get_assistant("demo")
    assert live is not None
    broken = live.model_copy(deep=True)
    broken.faqs = []
    check, saved = await imp.publish("demo", broken, [])
    assert check.blocked and saved is None
    assert check.baseline_passed == 1 and check.candidate_passed == 0
    assert check.regressions and "Hours" in check.regressions[0]
    assert (await store.get_assistant("demo")).assistant_version == 1  # type: ignore[union-attr]

    ok = live.model_copy(deep=True)
    ok.greeting = "Hello from Demo"
    check, saved = await imp.publish("demo", ok, [])
    assert not check.blocked and saved is not None and saved.assistant_version == 2

    check, saved = await imp.publish("demo", broken, [], force=True)
    assert check.blocked and check.forced and saved is not None
    assert check.published_version == 3
    assert next(c.forced for c in await imp.checks("demo")) is True
    assert await imp.checks("other") == []


async def test_propose_keeps_only_fixes_that_improve_without_regression() -> None:
    store, sim, imp, _cfg = await _setup()
    human = await sim.save_scenario(
        Scenario(
            tenant_id="demo",
            name="Wants a person",
            turns=["I need to speak to a person about an invoice."],
            expect=Expectation(handoff=True, min_overall=0),
        )
    )
    hours = await sim.save_scenario(
        Scenario(
            tenant_id="demo",
            name="Hours",
            turns=["What are your opening hours today?"],
            expect=Expectation(mentions=["open"], handoff=False, min_overall=0),
            regression=True,
        )
    )
    run = await sim.run("demo", "demo", [human.id, hours.id])
    assert [r.passed for r in run.results] == [False, True]

    props = await imp.propose("demo", "demo", run.id)
    assert len(props) == 1
    p = props[0]
    assert p.kind == "rule" and p.scenario_id == human.id
    assert p.delta.before_passed is False and p.delta.after_passed is True
    assert p.check.pack_size == 2 and not p.check.regressions
    assert p.failures_before == ["expected a human handoff"]

    # idempotent: an open proposal for the same scenario/kind is not duplicated
    assert await imp.propose("demo", "demo", run.id) == []

    approved = await imp.approve("demo", p.id, text="Transfer callers who ask for a person.")
    assert approved is not None and approved.status == "approved"
    assert approved.applied_version == 2
    live = await store.get_assistant("demo")
    assert live is not None and live.rules[-1].instruction.startswith("Transfer callers")
    assert (await sim.run("demo", "demo", [human.id])).results[0].passed


async def test_regression_from_run_and_from_call() -> None:
    store, sim, imp, _cfg = await _setup()
    bad = await sim.save_scenario(
        Scenario(
            tenant_id="demo",
            name="Price",
            turns=["How much is a boiler?"],
            expect=Expectation(mentions=["£"], min_overall=0),
        )
    )
    run = await sim.run("demo", "demo", [bad.id])
    assert not run.results[0].passed
    added = await imp.add_from_run("demo", run.id)
    assert [s.id for s in added] == [bad.id] and added[0].origin == f"run:{run.id}"
    assert await imp.add_from_run("demo", run.id) == []

    call: CallRecord = _call(
        ("assistant", "Hi"), ("user", "Do you fit smart meters?"), ("assistant", "Yes we do.")
    )
    await _ingest(store, call)
    sc = await imp.add_from_call("demo", call.call_id)
    assert sc is not None and sc.regression and sc.turns == ["Do you fit smart meters?"]
    assert sc.origin == f"call:{call.call_id}"
    assert await imp.add_from_call("other", call.call_id) is None


async def test_regression_routes(client: AsyncClient) -> None:
    r = await client.put(
        "/v1/quality/scenarios",
        params=Q,
        json={"name": "Hours", "turns": ["What time do you open?"], "expect": {"min_overall": 0}},
    )
    sid = r.json()["id"]
    r = await client.post(f"/v1/quality/scenarios/{sid}/regression", params=Q)
    assert r.status_code == 200 and r.json()["regression"] is True
    r = await client.get("/v1/quality/regression", params=Q)
    assert [s["id"] for s in r.json()["pack"]] == [sid]

    cfg = next(
        a for a in (await client.get("/v1/assistants")).json() if a["assistant_id"] == "demo"
    )
    cfg["greeting"] = "Hello there"
    r = await client.post("/v1/quality/publish", params=Q, json={"config": cfg})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["check"]["pack_size"] == 1 and body["config"]["greeting"] == "Hello there"

    r = await client.get("/v1/quality/improve/proposals", params=Q)
    assert r.status_code == 200 and r.json() == []
    r = await client.post(
        "/v1/quality/improve/propose", params=Q, json={"assistant_id": "demo", "run_id": "nope"}
    )
    assert r.status_code == 400


def test_speaking_style_prompt_reflects_toggles_and_custom_rules() -> None:
    default = SpeakingStyle().prompt()
    assert "one detail at a time" in default.lower()
    assert "0 7 9 3 0" in default and "M 2 1, 2 D F" in default
    assert "Is all of that correct?" in default

    custom = SpeakingStyle(
        digits_individually=False,
        spell_postcodes=False,
        confirm_phrase="have I got that right?",
        extra_rules=["Read job references as pairs of digits.", "  "],
    ).prompt()
    assert "0 7 9 3 0" not in custom and "M 2 1" not in custom
    assert "have I got that right?" in custom
    assert "- Read job references as pairs of digits." in custom
    assert "transfer_to_human" in custom

    cfg = AssistantConfig(tenant_id="t", company_id="t", assistant_id="a", speaking=SpeakingStyle())
    assert cfg.rendered_instructions().endswith(SpeakingStyle().prompt())
