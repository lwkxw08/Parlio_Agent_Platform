"""Phase 19b: white-glove onboarding, first-week digest, FAQ import, announcements/roadmap."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from parlio_api.adoption import merge_faqs, parse_faq_csv, parse_faq_text, review_import
from parlio_api.auth import DEV_TENANT
from parlio_api.billing import SubscriptionStatus
from parlio_api.journey import CHECKIN_KIND, QUESTIONNAIRE_KIND, CheckInLoop
from parlio_voice.models import AssistantConfig, Faq

from .test_platform import _run_call

Q = {"tenant_id": DEV_TENANT}
OWNER = {"X-Parlio-User": "owner@demo.parlio.local"}
TENANT_USER = {"X-Parlio-User": "member@example.com"}

# -- FAQ parsing (pure) --------------------------------------------------------------------------


def test_parse_faq_text_handles_qa_blocks_and_heading_style() -> None:
    text = """
Q: Do you open on Sundays?
A: Yes, 10am to 4pm.

What is your address?
12 High Street, Leeds LS1 4AB.

## Do you take card payments?
We accept all major cards and Apple Pay.

Do you offer parking?

Yes, there is free parking behind the building.
"""
    faqs = parse_faq_text(text)
    qs = [f.question for f in faqs]
    assert qs == [
        "Do you open on Sundays?",
        "What is your address?",
        "Do you take card payments?",
        "Do you offer parking?",
    ]
    assert faqs[0].answer == "Yes, 10am to 4pm." and faqs[3].answer.startswith("Yes, there is")
    assert all(f.source == "import" for f in faqs)


def test_parse_faq_csv_with_and_without_header() -> None:
    csv_text = (
        "question,answer,category\nOpening hours,Mon-Fri 9-5,hours\n"
        "Do you deliver?,Yes within 10 miles,\n"
    )
    faqs = parse_faq_csv(csv_text)
    assert [f.question for f in faqs] == ["Opening hours?", "Do you deliver?"]
    assert faqs[0].category == "hours" and faqs[1].category == "imported"
    bare = parse_faq_csv("Is there wifi?;Yes free wifi\nIs there wifi?;dupe\n")
    assert len(bare) == 1 and bare[0].answer == "Yes free wifi"


def test_review_and_merge_skip_existing_questions() -> None:
    cfg = AssistantConfig(
        tenant_id="t",
        company_id="c",
        assistant_id="a",
        faqs=[Faq(category="x", question="Do you deliver?", answer="Yes")],
    )
    new = [
        Faq(category="i", question="do you deliver", answer="Maybe"),
        Faq(category="i", question="Do you take cards?", answer="Yes"),
    ]
    res = review_import(new, cfg.faqs)
    assert [f.question for f in res.suggested] == ["Do you take cards?"]
    assert len(res.duplicates) == 1
    out, added = merge_faqs(cfg, new)
    assert added == 1 and len(out.faqs) == 2 and len(cfg.faqs) == 1


# -- API ------------------------------------------------------------------------------------------


async def test_faq_import_and_apply_creates_new_version(client: AsyncClient) -> None:
    cfgs = (await client.get("/v1/assistants", params=Q)).json()
    aid = cfgs[0]["assistant_id"]
    before = cfgs[0]["assistant_version"]
    r = await client.post(
        f"/v1/assistants/{aid}/faqs/import",
        params=Q,
        json={"source": "text", "content": "Q: Do you do refunds?\nA: Within 14 days."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "text" and len(r.json()["suggested"]) == 1
    r = await client.post(
        f"/v1/assistants/{aid}/faqs/apply", params=Q, json={"faqs": r.json()["suggested"]}
    )
    assert r.status_code == 200 and r.json()["added"] == 1
    assert r.json()["config"]["assistant_version"] == before + 1
    # re-applying is a no-op
    r = await client.post(
        f"/v1/assistants/{aid}/faqs/apply",
        params=Q,
        json={"faqs": [{"category": "x", "question": "Do you do refunds?", "answer": "again"}]},
    )
    assert r.json()["added"] == 0
    # users outside the owning tenant cannot import into this assistant
    r = await client.post(
        f"/v1/assistants/{aid}/faqs/import",
        headers={"X-Parlio-User": "stranger@example.com"},
        json={"source": "text", "content": "x?"},
    )
    assert r.status_code == 403


async def test_whiteglove_requires_growth_and_single_open_request(client: AsyncClient) -> None:
    app = client._transport.app  # type: ignore[attr-defined]
    billing = app.state.billing
    body = {
        "contact_name": "Keith",
        "contact_email": "keith@example.com",
        "areas": ["config_review", "forwarding"],
        "preferred_slots": ["Tue 10:00"],
    }
    # trial unlocks everything -> eligible
    r = await client.get("/v1/whiteglove", params=Q)
    assert r.status_code == 200 and r.json()["eligible"] is True
    r = await client.post("/v1/whiteglove", params=Q, json=body)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["status"] == "requested"
    r = await client.post("/v1/whiteglove", params=Q, json=body)
    assert r.status_code == 409
    assert (await client.get("/v1/whiteglove", params=Q)).json()["open_request"]["id"] == rid

    # staff queue + schedule
    r = await client.get("/v1/admin/whiteglove", headers=OWNER)
    assert r.status_code == 200 and [x["id"] for x in r.json()] == [rid]
    r = await client.patch(
        f"/v1/admin/whiteglove/{rid}",
        headers=OWNER,
        json={
            "scheduled_at": datetime.now(UTC).isoformat(),
            "assigned_to": "owner@demo.parlio.local",
            "note": "Booked a Teams call",
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "scheduled"
    assert r.json()["staff_notes"][0]["text"] == "Booked a Teams call"
    r = await client.patch(
        f"/v1/admin/whiteglove/{rid}", headers=OWNER, json={"status": "completed"}
    )
    assert r.json()["status"] == "completed"
    # non-staff cannot touch the queue
    r = await client.get("/v1/admin/whiteglove", headers=TENANT_USER)
    assert r.status_code == 403

    # a converted Starter tenant is not entitled
    await billing.change_plan(DEV_TENANT, "starter")
    await billing.set_status(DEV_TENANT, SubscriptionStatus.ACTIVE)
    try:
        r = await client.get("/v1/whiteglove", params=Q)
        assert r.json()["eligible"] is False and "Growth" in r.json()["reason"]
        assert (await client.post("/v1/whiteglove", params=Q, json=body)).status_code == 403
    finally:
        await billing.set_status(DEV_TENANT, SubscriptionStatus.TRIALING)


async def test_first_week_report_and_checkin_enrichment(client: AsyncClient) -> None:
    await _run_call(client, "fw-1", 90, text="Hello, I'd like to book a table")
    r = await client.get("/v1/setup/first-week", params=Q)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["calls"] >= 1 and body["highlights"] and body["checklist_total"] > 0
    assert (await client.get("/v1/setup/first-week", params={"tenant_id": "x"})).status_code == 403

    # the day-7 check-in carries the digest text
    app = client._transport.app  # type: ignore[attr-defined]
    store = app.state.store
    r = await client.post(
        "/v1/onboarding",
        json={"organisation_name": "Week Co", "questionnaire": {"monthly_calls": "0-50"}},
    )
    tid = r.json()["tenant_id"]
    doc = await store.get_doc(QUESTIONNAIRE_KIND, tid)
    doc.data["signed_up_at"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    await store.put_doc(doc)
    seen: list[tuple[str, int]] = []

    async def enrich(tenant_id: str, day: int) -> str:
        seen.append((tenant_id, day))
        return "DIGEST"

    loop = CheckInLoop(
        store,
        app.state.billing,
        app.state.sip,
        app.state.calendar,
        app.state.notifications,
        enrich=enrich,
    )
    await loop.sweep()
    assert (tid, 7) in seen
    assert await store.get_doc(CHECKIN_KIND, f"{tid}:7") is not None


async def test_announcements_roadmap_feedback(client: AsyncClient) -> None:
    # tenants cannot author
    r = await client.post(
        "/v1/admin/announcements", headers=TENANT_USER, json={"title": "x", "body": "y"}
    )
    assert r.status_code == 403
    r = await client.post(
        "/v1/admin/announcements",
        headers=OWNER,
        json={
            "title": "Grouped sidebar",
            "body": "Navigation is now grouped.",
            "kind": "improvement",
        },
    )
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    draft = await client.post(
        "/v1/admin/announcements",
        headers=OWNER,
        json={"title": "Draft", "body": "hidden", "published": False},
    )
    assert draft.status_code == 201

    feed = (await client.get("/v1/announcements", params=Q)).json()
    assert feed["unread"] == 1 and [i["id"] for i in feed["items"]] == [aid]
    feed = (
        await client.post("/v1/announcements/read", params=Q, json={"announcement_id": aid})
    ).json()
    assert feed["unread"] == 0 and feed["items"][0]["read"] is True
    # public changelog excludes drafts
    pub = (await client.get("/v1/public/changelog")).json()
    assert [a["id"] for a in pub] == [aid]
    # staff sees drafts
    assert len((await client.get("/v1/admin/announcements", headers=OWNER)).json()) == 2

    r = await client.post(
        "/v1/admin/roadmap",
        headers=OWNER,
        json={"title": "Zendesk connector", "status": "planned", "category": "integrations"},
    )
    assert r.status_code == 201
    item = r.json()["id"]
    r = await client.post(f"/v1/roadmap/{item}/vote", params=Q)
    assert r.status_code == 200 and r.json()["votes"] == 1 and r.json()["voted"] is True
    r = await client.post(f"/v1/roadmap/{item}/vote", params=Q)
    assert r.json()["votes"] == 1  # one vote per tenant
    r = await client.post(f"/v1/roadmap/{item}/vote", params={"tenant_id": "someone"})
    assert r.status_code == 403
    assert (await client.get("/v1/public/roadmap")).json()[0]["votes"] == 1

    r = await client.post(
        "/v1/feedback",
        params=Q,
        json={"kind": "idea", "text": "Please add Xero", "roadmap_item_id": item},
    )
    assert r.status_code == 201
    fb = r.json()["id"]
    rows = (await client.get("/v1/admin/feedback", headers=OWNER)).json()
    assert rows[0]["id"] == fb and rows[0]["tenant_id"] == DEV_TENANT
    r = await client.patch(f"/v1/admin/feedback/{fb}", headers=OWNER, json={"status": "planned"})
    assert r.json()["status"] == "planned"
    assert (await client.delete(f"/v1/admin/roadmap/{item}", headers=OWNER)).status_code == 204
