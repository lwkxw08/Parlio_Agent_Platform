from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.tickets import LogNotifier, TicketService, build_ticket, classify_category
from parlio_voice.models import CallEventType, TicketIntake, TicketPriority, TransferConfig

from .test_api import HEADERS, ev

INTAKE = {
    "caller_name": "Sam Jones",
    "caller_number": "+447700900123",
    "reason": "Boiler not working, no heating since this morning",
}


async def create(client: AsyncClient, **over: Any) -> dict[str, Any]:
    r = await client.post(
        "/v1/worker/tickets",
        params={"tenant_id": "demo", "company_id": "demo"},
        json={**INTAKE, **over},
        headers=HEADERS,
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def test_classifier_and_sla() -> None:
    assert classify_category("I need a quote for a new bathroom") == "quote"
    assert classify_category("hello") == "general"
    t = build_ticket(
        "t", "c", TicketIntake(reason="gas leak in the kitchen"), TransferConfig(), now=NOW
    )
    assert t.priority == TicketPriority.URGENT and t.category == "emergency"
    assert t.sla_due_at == NOW + timedelta(minutes=15)
    t = build_ticket("t", "c", TicketIntake(reason="please call me back today"), None, now=NOW)
    assert t.priority == TicketPriority.HIGH and t.sla_due_at == NOW + timedelta(minutes=60)


NOW = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)


async def test_worker_intake_creates_classified_ticket_with_alert(
    client: AsyncClient, app: FastAPI
) -> None:
    await client.post(
        "/v1/worker/events",
        json=ev(CallEventType.CALL_STARTED, "call-1", {"caller": "+447700900123"}),
        headers=HEADERS,
    )
    t = await create(client, call_id="call-1")
    assert t["status"] == "open"
    assert t["category"] == "emergency" and t["priority"] == "urgent"
    assert t["department"] == "emergencies"  # routed via the call's assistant config
    assert t["contact_id"] is not None
    assert t["sla_due_at"] is not None
    notifier = app.state.tickets.notifier
    assert isinstance(notifier, LogNotifier)
    assert notifier.sent and notifier.sent[0][1] == "urgent"

    r = await client.get("/v1/tickets", params={"tenant_id": "demo"})
    assert [x["id"] for x in r.json()] == [t["id"]]
    r = await client.get(f"/v1/tickets/{t['id']}")
    detail = r.json()
    assert detail["sla_remaining_s"] > 0
    assert [e["type"] for e in detail["events"]] == ["created"]


async def test_ticket_lifecycle_claim_note_resolve(client: AsyncClient) -> None:
    t = await create(client, reason="Would like a quote for a bathroom refit")
    tid = t["id"]
    assert t["priority"] == "normal" and t["category"] == "quote"

    r = await client.post(f"/v1/tickets/{tid}/claim", json={"actor": "keith"})
    assert r.json()["status"] == "claimed" and r.json()["assigned_to"] == "keith"

    r = await client.post(f"/v1/tickets/{tid}/notes", json={"actor": "keith", "note": "rang"})
    assert [e["type"] for e in r.json()] == ["created", "assigned", "claimed", "note"]

    r = await client.post(f"/v1/tickets/{tid}/callback", json={"actor": "keith"})
    assert r.json()["tel_uri"] == "tel:+447700900123"

    r = await client.post(f"/v1/tickets/{tid}/resolve", json={"actor": "keith", "note": "done"})
    body = r.json()
    assert body["status"] == "resolved" and body["resolved_at"] is not None

    r = await client.get("/v1/tickets", params={"tenant_id": "demo", "status": "open"})
    assert r.json() == []

    r = await client.get("/v1/analytics/handoff", params={"tenant_id": "demo"})
    stats = r.json()["tickets"]
    assert stats["total"] == 1 and stats["resolved"] == 1
    assert stats["avg_time_to_claim_s"] is not None
    assert stats["avg_time_to_resolve_s"] is not None
    assert stats["by_category"] == {"quote": 1}


async def test_sla_breach_is_flagged_and_escalated(client: AsyncClient, app: FastAPI) -> None:
    t = await create(client, reason="invoice query")
    svc: TicketService = app.state.tickets
    assert await svc.escalate_overdue(datetime.now(UTC)) == []
    breached = await svc.escalate_overdue(datetime.now(UTC) + timedelta(days=2))
    assert [b.id for b in breached] == [t["id"]]
    assert await svc.escalate_overdue(datetime.now(UTC) + timedelta(days=2)) == []

    r = await client.get(f"/v1/tickets/{t['id']}")
    assert r.json()["ticket"]["sla_breached"] is True
    assert "sla_breached" in [e["type"] for e in r.json()["events"]]
    notifier = app.state.tickets.notifier
    assert notifier.sent[-1][1] == "escalation"
    r = await client.get("/v1/analytics/handoff", params={"tenant_id": "demo"})
    assert r.json()["tickets"]["sla_breached"] == 1


async def test_ticket_rebuilt_from_event_when_api_call_failed(client: AsyncClient) -> None:
    intake = TicketIntake(call_id="call-9", reason="broken tap", caller_number="+447700900999")
    e = ev(
        CallEventType.TICKET_CREATED,
        "call-9",
        {"ticket_id": None, "intake": intake.model_dump(mode="json")},
    )
    r = await client.post("/v1/worker/events", json=e, headers=HEADERS)
    assert r.status_code == 202
    r = await client.get("/v1/tickets", params={"tenant_id": "demo"})
    assert len(r.json()) == 1 and r.json()[0]["call_id"] == "call-9"
    # replaying the same event does not duplicate the ticket
    await client.post("/v1/worker/events", json=e, headers=HEADERS)
    r = await client.get("/v1/tickets", params={"tenant_id": "demo"})
    assert len(r.json()) == 1


async def test_transfer_events_are_recorded_and_aggregated(client: AsyncClient) -> None:
    c = "call-t"
    started = datetime.now(UTC)
    attempts = [
        ("tr-1", "office", "Office", "general", "no_answer"),
        ("tr-2", "oncall", "Dave", "emergencies", "answered"),
    ]
    await client.post(
        "/v1/worker/events",
        json=ev(CallEventType.ESCALATION, c, {"keyword": "gas leak"}),
        headers=HEADERS,
    )
    for tid, did, name, dept, outcome in attempts:
        e = ev(
            CallEventType.TRANSFER_COMPLETED,
            c,
            {
                "transfer_id": tid,
                "destination_id": did,
                "destination": name,
                "department": dept,
                "mode": "warm",
                "outcome": outcome,
                "started_at": started.isoformat(),
                "ended_at": (started + timedelta(seconds=20)).isoformat(),
            },
        )
        r = await client.post("/v1/worker/events", json=e, headers=HEADERS)
        assert r.status_code == 202

    r = await client.get(f"/v1/calls/{c}")
    call = r.json()
    assert call["escalated"] is True and call["escalation_keyword"] == "gas leak"

    r = await client.get("/v1/transfers", params={"tenant_id": "demo"})
    assert {t["id"] for t in r.json()} == {"tr-1", "tr-2"}
    r = await client.get("/v1/transfers", params={"tenant_id": "other"})
    assert r.json() == []

    r = await client.get("/v1/analytics/handoff", params={"tenant_id": "demo"})
    s = r.json()["transfers"]
    assert s["total"] == 2 and s["answer_rate"] == 0.5
    assert s["by_department"] == {"general": 1, "emergencies": 1}
    assert s["by_outcome"] == {"no_answer": 1, "answered": 1}


async def test_transfer_config_round_trip_and_validation(client: AsyncClient) -> None:
    r = await client.get("/v1/assistants/demo/transfer")
    cfg = r.json()
    assert [d["id"] for d in cfg["destinations"]] == ["office", "oncall"]
    r = await client.get("/v1/assistants/demo/availability")
    avail = {d["destination"]["id"]: d["available_now"] for d in r.json()}
    assert avail["oncall"] is True and isinstance(avail["office"], bool)

    cfg["destinations"][0]["fallback_id"] = "nope"
    r = await client.put("/v1/assistants/demo/transfer", json=cfg)
    assert r.status_code == 400

    cfg["destinations"][0]["fallback_id"] = "oncall"
    cfg["urgent_keywords"] = ["flood"]
    r = await client.put("/v1/assistants/demo/transfer", json=cfg)
    assert r.status_code == 200
    r = await client.get("/v1/assistants/demo/transfer")
    assert r.json()["urgent_keywords"] == ["flood"]
    # numbers still resolve after a transfer-config update
    r = await client.get(
        "/v1/worker/assistants/resolve", params={"number": "+440000000000"}, headers=HEADERS
    )
    assert r.status_code == 200 and r.json()["transfer"]["urgent_keywords"] == ["flood"]
