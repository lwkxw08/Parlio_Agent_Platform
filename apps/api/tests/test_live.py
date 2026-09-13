"""Phase 10 live monitoring, supervisor takeover & approvals."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from starlette.testclient import TestClient

from parlio_api.auth import DEV_TENANT
from parlio_api.live import (
    ApprovalService,
    ApprovalStatus,
    Command,
    LiveCallHub,
    SimulatedRoomControl,
)
from parlio_voice.models import CallEvent, CallEventType

from .test_api import HEADERS, ev

Q = {"tenant_id": "demo"}


def _hub(app: FastAPI) -> LiveCallHub:
    h: LiveCallHub = app.state.live
    return h


def _control(app: FastAPI) -> SimulatedRoomControl:
    c = app.state.room_control
    assert isinstance(c, SimulatedRoomControl)
    return c


async def _add_member(client: AsyncClient, app: FastAPI, email: str, role: str) -> dict[str, str]:
    r = await client.post(
        f"/v1/organisations/{DEV_TENANT}/members", json={"email": email, "role": role}
    )
    assert r.status_code == 201, r.text
    # dev-mode auth doesn't auto-accept invitations the way the JWT path does
    for m in await app.state.store.memberships_for_email(email):
        await app.state.store.upsert_member(m.model_copy(update={"status": "active"}))
    return {"X-Parlio-User": email}


async def _start_call(client: AsyncClient, call_id: str = "c-live-1") -> None:
    for t, p in [
        (
            CallEventType.CALL_STARTED,
            {"caller": "+447700900001", "dialed": "+442046206823", "room": f"room-{call_id}"},
        ),
        (CallEventType.CALL_ANSWERED, {"answer_latency_s": 0.4}),
        (CallEventType.TRANSCRIPT_ITEM, {"role": "assistant", "text": "Hello, Parlio."}),
        (CallEventType.TRANSCRIPT_ITEM, {"role": "user", "text": "I'd like a quote."}),
    ]:
        r = await client.post("/v1/worker/events", json=ev(t, call_id, p), headers=HEADERS)
        assert r.status_code == 202, r.text


# -- hub --------------------------------------------------------------------------------------


def test_hub_folds_events_and_fans_out() -> None:
    hub = LiveCallHub()
    q = hub.subscribe("demo")
    snap = q.get_nowait()
    assert snap.type == "snapshot" and snap.calls == []

    def _ev(t: CallEventType, payload: dict[str, object]) -> CallEvent:
        return CallEvent(
            type=t,
            call_id="c1",
            tenant_id="demo",
            company_id="demo",
            assistant_id="a",
            payload=payload,
        )

    hub.on_event(_ev(CallEventType.CALL_STARTED, {"caller": "+44770", "room": "r1"}))
    hub.on_event(_ev(CallEventType.CALL_ANSWERED, {}))
    hub.on_event(_ev(CallEventType.TRANSCRIPT_ITEM, {"role": "user", "text": "hi"}))
    assert hub.count("demo") == 1 and hub.count("other") == 0
    call = hub.active("demo")[0]
    assert call.status == "in_progress" and call.room == "r1" and len(call.transcript) == 1
    types = [q.get_nowait().type for _ in range(3)]
    assert types == ["call.started", "call.answered", "call.transcript_item"]

    hub.on_event(_ev(CallEventType.CALL_ENDED, {"reason": "hangup"}))
    assert hub.count() == 0
    assert q.get_nowait().type == "call.ended"
    # late transcript for an ended call is ignored
    hub.on_event(_ev(CallEventType.TRANSCRIPT_ITEM, {"role": "user", "text": "late"}))
    assert hub.count() == 0 and q.empty()
    hub.unsubscribe("demo", q)


def test_hub_drops_oldest_when_subscriber_is_slow() -> None:
    hub = LiveCallHub(max_queue=2)
    q = hub.subscribe("demo")  # snapshot occupies one slot
    for i in range(5):
        hub.on_event(
            CallEvent(
                type=CallEventType.TRANSCRIPT_ITEM,
                call_id="c1",
                tenant_id="demo",
                company_id="demo",
                assistant_id="a",
                payload={"role": "user", "text": str(i)},
            )
        )
    frames = [q.get_nowait() for _ in range(2)]
    assert [f.payload.get("text") for f in frames] == ["3", "4"]


# -- routes -----------------------------------------------------------------------------------


async def test_active_calls_are_tenant_scoped(client: AsyncClient) -> None:
    r = await client.get("/v1/live/calls", params=Q)
    assert r.status_code == 200 and r.json() == []
    await _start_call(client)
    r = await client.get("/v1/live/calls", params=Q)
    assert r.status_code == 200
    (call,) = r.json()
    assert call["call_id"] == "c-live-1" and call["status"] == "in_progress"
    assert [t["text"] for t in call["transcript"]] == ["Hello, Parlio.", "I'd like a quote."]
    r = await client.get("/v1/live/calls/c-live-1", params=Q)
    assert r.status_code == 200 and call["room"] == "room-c-live-1"
    # strangers see nothing
    r = await client.get("/v1/live/calls", params=Q, headers={"X-Parlio-User": "who@example.com"})
    assert r.status_code == 403
    # ended calls leave the live list
    r = await client.post(
        "/v1/worker/events",
        json=ev(CallEventType.CALL_ENDED, "c-live-1", {"reason": "hangup"}),
        headers=HEADERS,
    )
    assert r.status_code == 202
    r = await client.get("/v1/live/calls", params=Q)
    assert r.json() == []
    r = await client.get("/v1/live/calls/c-live-1", params=Q)
    assert r.status_code == 404


async def test_listen_whisper_takeover_handback(client: AsyncClient, app: FastAPI) -> None:
    await _start_call(client)
    r = await client.post("/v1/live/calls/c-live-1/join", params=Q)
    assert r.status_code == 200, r.text
    join = r.json()
    assert join["mode"] == "listening" and join["token"].endswith("-sub")
    assert join["room"] == "room-c-live-1"

    r = await client.post(
        "/v1/live/calls/c-live-1/command", params=Q, json={"cmd": "whisper", "text": ""}
    )
    assert r.status_code == 422
    r = await client.post(
        "/v1/live/calls/c-live-1/command",
        params=Q,
        json={"cmd": "whisper", "text": "Offer the 10% discount"},
    )
    assert r.status_code == 200 and r.json() is None
    r = await client.post("/v1/live/calls/c-live-1/command", params=Q, json={"cmd": "takeover"})
    assert r.status_code == 200
    assert r.json()["mode"] == "taken_over" and r.json()["token"].endswith("-pub")
    call = _hub(app).get("c-live-1")
    assert call is not None and call.supervisor_mode == "taken_over"

    sent = _control(app).sent
    assert [(room, m.cmd) for room, m in sent] == [
        ("room-c-live-1", Command.WHISPER),
        ("room-c-live-1", Command.TAKEOVER),
    ]
    assert sent[0][1].text == "Offer the 10% discount"

    # a second supervisor cannot interfere while the call is taken over
    other = await _add_member(client, app, "colleague@example.com", "admin")
    r = await client.post(
        "/v1/live/calls/c-live-1/command",
        params=Q,
        json={"cmd": "say", "text": "hi"},
        headers=other,
    )
    assert r.status_code == 409

    r = await client.post("/v1/live/calls/c-live-1/command", params=Q, json={"cmd": "handback"})
    assert r.status_code == 200
    assert _hub(app).get("c-live-1").supervisor_mode == "listening"  # type: ignore[union-attr]
    r = await client.post("/v1/live/calls/c-live-1/leave", params=Q)
    assert r.status_code == 204
    assert _hub(app).get("c-live-1").supervisor is None  # type: ignore[union-attr]

    # the worker echoes what happened; it lands in the transcript as a supervisor line
    r = await client.post(
        "/v1/worker/events",
        json=ev(CallEventType.SUPERVISOR, "c-live-1", {"cmd": "say", "by": "Keith", "text": "hi"}),
        headers=HEADERS,
    )
    assert r.status_code == 202
    r = await client.get("/v1/live/calls/c-live-1", params=Q)
    assert r.json()["transcript"][-1] == {
        "role": "supervisor",
        "text": "hi",
        "at": r.json()["transcript"][-1]["at"],
    }

    # audit trail
    r = await client.get("/v1/compliance/audit", params=Q)
    if r.status_code == 200:
        actions = [a["action"] for a in r.json()]
        assert {"live.listen", "live.whisper", "live.takeover", "live.handback"} <= set(actions)

    r = await client.post("/v1/live/calls/nope/join", params=Q)
    assert r.status_code == 404


async def test_viewer_cannot_take_over(client: AsyncClient, app: FastAPI) -> None:
    await _start_call(client)
    viewer = await _add_member(client, app, "viewer@example.com", "viewer")
    r = await client.post("/v1/live/calls/c-live-1/join", params=Q, headers=viewer)
    assert r.status_code == 200  # listening is fine
    r = await client.post(
        "/v1/live/calls/c-live-1/command", params=Q, json={"cmd": "takeover"}, headers=viewer
    )
    assert r.status_code == 403
    r = await client.post(
        "/v1/live/calls/c-live-1/command",
        params=Q,
        json={"cmd": "whisper", "text": "try this"},
        headers=viewer,
    )
    assert r.status_code == 200


def test_websocket_stream(app: FastAPI, backend: str) -> None:
    if backend != "memory":
        pytest.skip("websocket smoke test runs on the memory store only")
    with TestClient(app) as tc:
        tc.headers.update(HEADERS)
        with tc.websocket_connect("/v1/live/ws?tenant_id=demo") as ws:
            snap = json.loads(ws.receive_text())
            assert snap["type"] == "snapshot" and snap["calls"] == []
            r = tc.post(
                "/v1/worker/events",
                json=ev(CallEventType.CALL_STARTED, "c-ws", {"caller": "+4477", "room": "r"}),
            )
            assert r.status_code == 202
            frame = json.loads(ws.receive_text())
            assert frame["type"] == "call.started" and frame["call"]["call_id"] == "c-ws"
            r = tc.post(
                "/v1/worker/events",
                json=ev(CallEventType.TRANSCRIPT_ITEM, "c-ws", {"role": "user", "text": "hi"}),
            )
            frame = json.loads(ws.receive_text())
            assert frame["type"] == "call.transcript_item" and frame["payload"]["text"] == "hi"
        # wrong tenant is rejected before accept
        with (
            pytest.raises(Exception),  # noqa: B017 - starlette raises WebSocketDisconnect
            tc.websocket_connect("/v1/live/ws?tenant_id=other") as ws,
        ):
            ws.receive_text()


# -- approvals --------------------------------------------------------------------------------


async def test_approval_roundtrip_via_dashboard(client: AsyncClient, app: FastAPI) -> None:
    await _start_call(client)
    r = await client.post(
        "/v1/worker/approvals",
        params={"tenant_id": "demo", "company_id": "demo"},
        headers=HEADERS,
        json={
            "call_id": "c-live-1",
            "kind": "quote",
            "title": "Rewire quote",
            "details": "3-bed semi, full rewire",
            "amount": 4200,
            "caller": "+447700900001",
            "timeout_s": 120,
        },
    )
    assert r.status_code == 201, r.text
    ap = r.json()
    assert ap["status"] == "pending" and ap["token"]
    assert _hub(app).get("c-live-1").pending_approval_id == ap["id"]  # type: ignore[union-attr]

    r = await client.get("/v1/approvals", params={**Q, "status": "pending"})
    assert [a["id"] for a in r.json()] == [ap["id"]]

    r = await client.post(
        f"/v1/approvals/{ap['id']}/decide", params=Q, json={"approve": True, "note": "go ahead"}
    )
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["decided_by"] and r.json()["note"] == "go ahead"
    # idempotent guard
    r = await client.post(f"/v1/approvals/{ap['id']}/decide", params=Q, json={"approve": False})
    assert r.status_code == 409
    # worker sees the decision
    r = await client.get(
        f"/v1/worker/approvals/{ap['id']}", params={"tenant_id": "demo"}, headers=HEADERS
    )
    assert r.json()["status"] == "approved"
    assert _hub(app).get("c-live-1").pending_approval_id is None  # type: ignore[union-attr]
    # other tenants can't see it
    r = await client.get("/v1/approvals", params={"tenant_id": "other"})
    assert r.status_code == 403


async def test_approval_tap_link_and_expiry(client: AsyncClient, app: FastAPI) -> None:
    r = await client.post(
        "/v1/worker/approvals",
        params={"tenant_id": "demo"},
        headers=HEADERS,
        json={"kind": "refund", "title": "Refund £40 deposit", "amount": 40, "timeout_s": 60},
    )
    ap = r.json()
    r = await client.get(f"/v1/public/approvals/{ap['token']}")
    assert r.status_code == 200 and "token" not in r.json()
    assert r.json()["title"] == "Refund £40 deposit"
    r = await client.get("/v1/public/approvals/not-a-token")
    assert r.status_code == 404
    r = await client.post(
        f"/v1/public/approvals/{ap['token']}", json={"approve": False, "name": "Keith"}
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert r.json()["decided_by"] == "Keith"

    # expiry: force the deadline into the past
    r = await client.post(
        "/v1/worker/approvals",
        params={"tenant_id": "demo"},
        headers=HEADERS,
        json={"kind": "booking", "title": "Sunday booking", "timeout_s": 15},
    )
    svc: ApprovalService = app.state.approvals
    stale = await svc.get("demo", r.json()["id"])
    assert stale is not None
    stale.expires_at = stale.requested_at
    await app.state.store.put_doc(stale.to_doc())
    got = await svc.get("demo", stale.id)
    assert got is not None and got.status == ApprovalStatus.EXPIRED
    r = await client.post(f"/v1/approvals/{stale.id}/decide", params=Q, json={"approve": True})
    assert r.status_code == 409


async def test_worker_long_poll_returns_on_decision(client: AsyncClient, app: FastAPI) -> None:
    import asyncio

    r = await client.post(
        "/v1/worker/approvals",
        params={"tenant_id": "demo"},
        headers=HEADERS,
        json={"kind": "discount", "title": "10% off", "timeout_s": 60},
    )
    ap_id = r.json()["id"]
    svc: ApprovalService = app.state.approvals

    async def _decide() -> None:
        await asyncio.sleep(0.05)
        ap = await svc.get("demo", ap_id)
        assert ap is not None
        await svc.decide(ap, True, "owner@example.com")

    task = asyncio.create_task(_decide())
    got = await svc.wait("demo", ap_id, timeout_s=5)
    await task
    assert got is not None and got.status == ApprovalStatus.APPROVED


async def test_approval_notification_goes_through_rules(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/notifications/rules",
        params=Q,
        json={
            "tenant_id": "demo",
            "company_id": "demo",
            "channel": "sms",
            "target": "+447700900999",
            "events": ["approval.requested"],
        },
    )
    assert r.status_code in (200, 201), r.text
    r = await client.post(
        "/v1/worker/approvals",
        params={"tenant_id": "demo"},
        headers=HEADERS,
        json={"kind": "quote", "title": "Boiler quote", "amount": 1800},
    )
    assert r.status_code == 201
    r = await client.get("/v1/notifications/log", params=Q)
    assert r.status_code == 200
    hits = [n for n in r.json() if n["event"] == "approval.requested"]
    assert hits and "/approve/" in hits[0]["body"] and "1,800.00" in hits[0]["body"]
