"""Part F — end-to-end scenario suite.

Each test drives a whole customer journey through the real components, in-process: the voice
worker's `ReceptionistTools` (the same code the LiveKit agent calls) talk to the Core API over an
ASGI `httpx` client, the API persists to the tenant-scoped store (memory + Postgres backends via
the shared `client` fixture), the post-call pipeline / notifications / reminders / outbound
services run, and the dashboard read models are asserted at the end — exactly what the live-call
pass on real numbers checks by hand, minus the audio. Only the telephony (SIP/PSTN) and TTS/STT
legs are simulated; see docs/runbooks/live-call-test-script.md for the human-driven part.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from parlio_api.auth import DEV_TENANT
from parlio_api.messaging import LogSmsProvider, MessageService
from parlio_api.outbound import OutboundCall, OutboundService, OutboundStatus, SimulatedDialer
from parlio_api.postcall import PostCallProcessor
from parlio_api.reminders import ReminderService
from parlio_api.sip import DdiRoute, TrunkInput, TrunkMode
from parlio_voice.models import (
    AssistantConfig,
    CallEvent,
    CallEventType,
    ScreeningMode,
    TransferOutcome,
)
from parlio_voice.tools import CoreApiClient, ReceptionistTools
from parlio_voice.transfer import SimulatedBridge, TransferEngine

from .test_api import HEADERS
from .test_inbox import meta_wa, telnyx_sms

Q = {"tenant_id": DEV_TENANT}
OWNER = {"X-Parlio-User": "owner@demo.parlio.local"}
FORWARDED_NUMBER = "+440000000000"  # the Parlio number the customer's phone diverts to
DDI = "+442046206823"  # a number on the customer's own trunk
CALLER = "+447700900321"
OWNER_MOBILE = "+447700900001"
MONDAY_10 = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)  # 10:00 BST, office open
SUNDAY_03 = datetime(2026, 9, 13, 2, 0, tzinfo=UTC)  # 03:00 BST, closed


# -- harness ----------------------------------------------------------------------------------


class LiveCall:
    """One inbound call as the voice worker experiences it: resolve/admit, screen, greet, use
    tools, hang up. Events go through the same `/v1/worker/events` endpoint the worker uses."""

    def __init__(
        self,
        client: AsyncClient,
        app: FastAPI,
        cfg: AssistantConfig,
        call_id: str,
        caller: str | None,
        *,
        now: datetime = MONDAY_10,
        outcomes: dict[str, TransferOutcome] | None = None,
    ) -> None:
        self.client = client
        self.app = app
        self.cfg = cfg
        self.call_id = call_id
        self.caller = caller
        self.said: list[str] = []
        self.pending: list[Awaitable[Any]] = []
        self.bridge = SimulatedBridge(outcomes)
        engine = TransferEngine(cfg.transfer, self.bridge, now=now)
        worker_http = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://worker", headers=HEADERS
        )
        self.tools = ReceptionistTools(
            cfg, call_id, caller, engine, CoreApiClient(worker_http), self._emit, self._say
        )

    def _emit(self, t: CallEventType, payload: dict[str, Any]) -> None:
        self.pending.append(self.event(t, payload))

    async def _say(self, text: str) -> None:
        self.said.append(text)

    async def event(self, t: CallEventType, payload: dict[str, Any]) -> None:
        body = CallEvent(
            type=t,
            call_id=self.call_id,
            tenant_id=self.cfg.tenant_id,
            company_id=self.cfg.company_id,
            assistant_id=self.cfg.assistant_id,
            payload=payload,
        ).model_dump(mode="json")
        r = await self.client.post("/v1/worker/events", json=body, headers=HEADERS)
        assert r.status_code == 202, r.text

    async def flush(self) -> None:
        for p in self.pending:
            await p
        self.pending.clear()

    async def start(self, dialed: str, **extra: Any) -> None:
        await self.event(
            CallEventType.CALL_STARTED, {"caller": self.caller, "dialed": dialed, **extra}
        )

    async def answer(self, latency_s: float = 0.4) -> None:
        await self.event(CallEventType.CALL_ANSWERED, {"answer_latency_s": latency_s})

    async def turn(self, user: str, assistant: str) -> None:
        await self.event(CallEventType.TRANSCRIPT_ITEM, {"role": "user", "text": user})
        await self.event(CallEventType.TRANSCRIPT_ITEM, {"role": "assistant", "text": assistant})

    async def end(
        self, reason: str = "caller_hangup", duration_s: float = 60, **extra: Any
    ) -> None:
        await self.flush()
        await self.event(
            CallEventType.CALL_ENDED, {"reason": reason, "duration_s": duration_s, **extra}
        )
        proc: PostCallProcessor = self.app.state.postcall
        await proc.drain()


async def _cfg(client: AsyncClient) -> AssistantConfig:
    return AssistantConfig.model_validate((await client.get("/v1/assistants")).json()[0])


async def _save(client: AsyncClient, cfg: AssistantConfig, numbers: list[str]) -> AssistantConfig:
    r = await client.put(
        f"/v1/assistants/{cfg.assistant_id}",
        json={"config": cfg.model_dump(mode="json"), "numbers": numbers},
    )
    assert r.status_code == 200, r.text
    return AssistantConfig.model_validate(r.json())


async def _resolve(client: AsyncClient, number: str) -> AssistantConfig:
    r = await client.get(
        "/v1/worker/assistants/resolve", params={"number": number}, headers=HEADERS
    )
    assert r.status_code == 200, r.text
    return AssistantConfig.model_validate(r.json())


def _sms(app: FastAPI) -> LogSmsProvider:
    sms: MessageService = app.state.sms
    sms.from_number = DDI
    prov = sms.provider
    assert isinstance(prov, LogSmsProvider)
    return prov


async def _owner_sms_rule(client: AsyncClient) -> dict[str, Any]:
    r = await client.post(
        "/v1/notifications/rules",
        params=Q,
        json={
            "name": "Owner SMS",
            "channel": "sms",
            "target": OWNER_MOBILE,
            "events": ["call.completed"],
        },
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


async def _trunk(client: AsyncClient, body: TrunkInput) -> dict[str, Any]:
    r = await client.post("/v1/telephony/trunks", params=Q, json=body.model_dump(mode="json"))
    assert r.status_code == 201, r.text
    return dict(r.json())


async def _admit(client: AsyncClient, number: str, call_id: str) -> dict[str, Any]:
    r = await client.post(
        "/v1/worker/telephony/admit",
        params={"number": number, "call_id": call_id},
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _overview(client: AsyncClient, tz: str = "Europe/London") -> dict[str, Any]:
    r = await client.get("/v1/analytics/overview", params={**Q, "days": 7, "timezone": tz})
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _insights(client: AsyncClient, tz: str = "Europe/London") -> dict[str, Any]:
    r = await client.get("/v1/analytics/insights", params={**Q, "days": 30, "timezone": tz})
    assert r.status_code == 200, r.text
    return dict(r.json())


# -- inbound via call forwarding (mode 1) ------------------------------------------------------


async def test_forwarding_faq_ticket_owner_sms_and_dashboard(
    client: AsyncClient, app: FastAPI
) -> None:
    """Customer's phone diverts to the Parlio number; caller asks a question, then leaves a
    callback request. Owner gets one summary text; Calls, Tickets, Analytics and Usage agree."""
    prov = _sms(app)
    await _owner_sms_rule(client)
    cfg = await _resolve(client, FORWARDED_NUMBER)
    assert cfg.tenant_id == DEV_TENANT

    call = LiveCall(client, app, cfg, "e2e-fwd-1", CALLER)
    await call.start(FORWARDED_NUMBER, sip={"mode": "forward"})
    await call.answer(0.35)
    await call.turn("Do you do boiler servicing?", "Yes, we service all makes of boiler.")
    await call.turn("Can someone call me back about a quote?", "Of course, I'll take some details.")
    ticket = await call.tools.create_ticket(
        reason="Boiler service quote",
        caller_name="Keith Wilson",
        callback_number="07700 900321",
        urgency="normal",
        callback_window="tomorrow morning",
        department="bookings",
    )
    assert ticket["id"] and ticket["status"] == "open"
    assert ticket["caller_number"] == CALLER  # 07700 -> +447700 normalised on the worker
    await call.end(duration_s=95)

    # Calls list + detail
    rec = (await client.get(f"/v1/calls/{call.call_id}", params=Q)).json()
    assert rec["status"] == "completed" and rec["kind"] in ("ticketed", "answered")
    assert ticket["id"] in rec["ticket_ids"]
    assert len(rec["transcript"]) == 4
    calls = (await client.get("/v1/calls", params={**Q, "kind": "ticketed"})).json()
    assert any(c["call_id"] == call.call_id for c in calls)

    # one ticket, classified, linked to the call
    tickets = (await client.get("/v1/tickets", params=Q)).json()
    mine = [t for t in tickets if t["call_id"] == call.call_id]
    assert len(mine) == 1 and mine[0]["department"] == "bookings"
    detail = (await client.get(f"/v1/tickets/{ticket['id']}", params=Q)).json()
    assert detail["ticket"]["caller_name"] == "Keith Wilson"

    # exactly one owner summary text, naming the caller and the ticket
    owner_msgs = [m for m in prov.sent if m[1] == OWNER_MOBILE]
    assert len(owner_msgs) == 1
    assert "Keith" in owner_msgs[0][2] or "boiler" in owner_msgs[0][2].lower()

    # analytics + usage reflect the call
    ov = await _overview(client)
    assert ov["current"]["total_calls"] >= 1 and ov["current"]["ticketed"] >= 1
    assert ov["tickets"]["total"] >= 1 and ov["usage"]["calls"] >= 1
    assert ov["usage"]["tickets"] >= 1 and ov["usage"]["minutes"] > 0
    ins = await _insights(client)
    assert ins["resolution"]["ticketed"] >= 1
    assert ins["demand"]["heatmap"] and sum(map(sum, ins["demand"]["heatmap"])) >= 1


async def test_forwarding_after_hours_takes_message_and_flags_missed_without_ai(
    client: AsyncClient, app: FastAPI
) -> None:
    cfg = await _resolve(client, FORWARDED_NUMBER)
    # office is closed on Sunday night; the on-call engineer is rung but doesn't pick up
    call = LiveCall(
        client,
        app,
        cfg,
        "e2e-fwd-night",
        CALLER,
        now=SUNDAY_03,
        outcomes={"oncall": TransferOutcome.NO_ANSWER},
    )
    await call.start(FORWARDED_NUMBER)
    await call.answer()
    res = await call.tools.transfer("general", "wants to speak to someone")
    assert res.outcome in (TransferOutcome.NO_ANSWER, TransferOutcome.UNAVAILABLE)
    assert not res.succeeded
    assert "office" not in call.bridge.dialed
    ticket = await call.tools.create_ticket(
        reason="No hot water",
        caller_name="Sam",
        callback_number=None,
        urgency="high",
        callback_window="first thing",
        department=None,
    )
    assert ticket["id"]
    await call.end(duration_s=70)
    rec = (await client.get(f"/v1/calls/{call.call_id}", params=Q)).json()
    assert ticket["id"] in rec["ticket_ids"]
    assert not any(t["outcome"] == "answered" for t in rec["transfers"])
    tickets = (await client.get("/v1/tickets", params=Q)).json()
    t = next(t for t in tickets if t["id"] == ticket["id"])
    assert t["caller_number"] == CALLER and t["priority"] in ("high", "urgent")


# -- BYO SIP: PBX mode (Parlio issues credentials) -----------------------------------------------


async def test_pbx_trunk_ddi_routes_warm_transfer_and_records_human_leg(
    client: AsyncClient, app: FastAPI
) -> None:
    """Customer's PBX sends the DDI to Parlio; caller asks for accounts; warm transfer answered;
    the human leg is timed and recorded; concurrency cap enforced per trunk."""
    view = await _trunk(
        client,
        TrunkInput(
            mode=TrunkMode.PBX,
            pbx_address="pbx.acme.test",
            ddis=[DdiRoute(e164=DDI, assistant_id="demo", department="accounts")],
            max_concurrent_calls=1,
        ),
    )
    assert view["credentials"]["password"]
    admit = await _admit(client, DDI, "e2e-pbx-1")
    assert admit["allowed"] and admit["assistant_id"] == "demo"
    assert admit.get("department") in ("accounts", None)
    second = await _admit(client, DDI, "e2e-pbx-2")
    assert second["allowed"] is False and "limit" in second["reason"]

    cfg = await _resolve(client, FORWARDED_NUMBER)
    cfg.recording.record_transfers = True
    cfg = await _save(client, cfg, [FORWARDED_NUMBER])
    call = LiveCall(client, app, cfg, "e2e-pbx-1", CALLER)
    await call.start(DDI, sip={"mode": "pbx", "trunk_id": view["trunk"]["id"]})
    await call.answer(0.5)
    await call.turn("I need to talk to accounts about an invoice", "Let me connect you.")
    res = await call.tools.transfer("accounts", "invoice query")
    assert res.succeeded and res.connected is not None
    assert "accounts" in call.bridge.dialed
    assert call.bridge.left is True  # warm: assistant briefed the human and stepped out
    assert any("Connecting you" in s for s in call.said)
    assert any("invoice query" in s for s in call.said)
    tid = call.tools.connected_transfer_id
    assert tid
    await call.end(
        duration_s=240,
        human_leg={"transfer_id": tid, "duration_s": 180.0, "recorded": True},
    )
    r = await client.post(
        "/v1/worker/telephony/release", params={"call_id": "e2e-pbx-1"}, headers=HEADERS
    )
    assert r.status_code == 204
    assert (await _admit(client, DDI, "e2e-pbx-2"))["allowed"] is True

    transfers = (await client.get("/v1/transfers", params=Q)).json()
    mine = [t for t in transfers if t["call_id"] == call.call_id]
    assert len(mine) == 1
    assert mine[0]["outcome"] == "answered" and mine[0]["department"] == "accounts"
    assert mine[0]["human_duration_s"] == 180.0 and mine[0]["recorded"] is True
    rec = (await client.get(f"/v1/calls/{call.call_id}", params=Q)).json()
    assert rec["kind"] == "transferred"
    ov = await _overview(client)
    assert ov["transfers"]["total"] >= 1 and ov["transfers"]["recorded"] >= 1
    assert ov["transfers"]["human_talk_s"] >= 180
    ins = await _insights(client)
    assert ins["transfers"]["answer_rate"] is not None
    assert ins["cost"]["human_minutes"] >= 3


# -- BYO SIP: registration mode (customer's own SIP account) ------------------------------------


async def test_byo_register_trunk_human_managed_callback_then_ai_callback_resolves(
    client: AsyncClient, app: FastAPI
) -> None:
    """Inbound on a registered trunk -> callback ticket. A human claims it, then sends it to the
    AI with a resolution; the AI call back closes the same ticket and never opens a second one."""
    view = await _trunk(
        client,
        TrunkInput(
            mode=TrunkMode.BYO_REGISTER,
            registrar="sip.voipfone.net",
            username="123456",
            password="hunter22",
            ddis=[DdiRoute(e164=DDI, assistant_id="demo")],
        ),
    )
    assert view["credentials"] is None and view["trunk"]["registration"]["state"] == "pending"
    assert (await _admit(client, DDI, "e2e-byo-1"))["allowed"]

    cfg = await _resolve(client, FORWARDED_NUMBER)
    call = LiveCall(client, app, cfg, "e2e-byo-1", CALLER, now=SUNDAY_03)
    await call.start(DDI, sip={"mode": "byo_register"})
    await call.answer()
    ticket = await call.tools.create_ticket(
        reason="Query about invoice 1042",
        caller_name="Keith",
        callback_number=CALLER,
        urgency="normal",
        callback_window=None,
        department="accounts",
    )
    await call.end()
    tid = ticket["id"]

    # callbacks do NOT auto-queue for the AI: the ticket sits open for a human
    jobs = (await client.get("/v1/outbound/calls", params=Q)).json()
    assert not [j for j in jobs if j.get("ticket_id") == tid]
    r = await client.post(f"/v1/tickets/{tid}/claim", params=Q, json={"actor": "dave"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "claimed"
    r = await client.post(f"/v1/tickets/{tid}/callback", params=Q, json={"actor": "dave"})
    assert r.status_code == 200 and r.json()["tel_uri"] == f"tel:{CALLER}"

    # human decides the AI can deliver the answer
    svc: OutboundService = app.state.outbound
    pol = await svc.policy(DEV_TENANT)
    pol.window_start, pol.window_end = (
        datetime.min.time(),
        datetime.max.time().replace(microsecond=0),
    )
    pol.days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    await svc.save_policy(pol)
    r = await client.post(
        "/v1/outbound/calls",
        params=Q,
        json={
            "purpose": "ticket_callback",
            "to": CALLER,
            "name": "Keith",
            "ticket_id": tid,
            "resolution": "Invoice 1042 has been corrected to £340 and re-sent by email.",
        },
    )
    assert r.status_code == 201, r.text
    job = OutboundCall.model_validate(r.json())
    await app.state.outbound_loop.tick()
    job = (await svc.get(DEV_TENANT, job.id)) or job
    assert job.status == OutboundStatus.DIALING and job.call_id
    dialer = svc.dialer
    assert isinstance(dialer, SimulatedDialer)

    # the worker fetches the job, gets the ticket details + "never re-collect / never re-ticket"
    r = await client.get(f"/v1/worker/outbound/{job.id}", headers=HEADERS)
    assert r.status_code == 200 and r.json()["ticket_id"] == tid
    out = LiveCall(client, app, cfg, job.call_id, DDI)
    await out.start(CALLER, direction="outbound", job_id=job.id)
    await out.answer()
    await out.turn("Hello?", "Hi Keith, it's about invoice 1042 — it's been corrected to £340.")
    r = await client.post(
        f"/v1/worker/outbound/{job.id}/outcome",
        headers=HEADERS,
        json={"outcome": "resolved", "detail": "customer happy with corrected invoice"},
    )
    assert r.status_code == 200
    await out.end(duration_s=50)

    detail = (await client.get(f"/v1/tickets/{tid}", params=Q)).json()
    assert detail["ticket"]["status"] == "resolved"
    notes = [e["note"] for e in detail["events"] if e.get("note")]
    assert any("AI call back" in n for n in notes)
    tickets = (await client.get("/v1/tickets", params=Q)).json()
    assert sum(1 for t in tickets if t["caller_number"] == CALLER) == 1
    calls = (await client.get("/v1/calls", params={**Q, "kind": "outbound"})).json()
    assert any(c["call_id"] == job.call_id for c in calls)
    ins = await _insights(client)
    assert ins["resolution"]["ai_callbacks"] >= 1
    assert ins["resolution"]["ai_callbacks_resolved"] >= 1


# -- booking on a live call + SMS reminders ----------------------------------------------------


async def test_live_booking_reminders_confirm_reschedule_and_stop(
    client: AsyncClient, app: FastAPI
) -> None:
    prov = _sms(app)
    r = await client.post(
        "/v1/calendar/connections", params=Q, json={"provider": "simulated", "name": "Diary"}
    )
    assert r.status_code == 201, r.text
    r = await client.put(
        "/v1/reminders/policy", params=Q, json={"enabled": True, "hours_before": [2, 1]}
    )
    assert r.status_code == 200

    cfg = await _resolve(client, FORWARDED_NUMBER)
    call = LiveCall(client, app, cfg, "e2e-book-1", CALLER)
    await call.start(FORWARDED_NUMBER)
    await call.answer()
    avail = await call.tools.calendar_availability(days=7)
    # far enough out that both reminders are still in the future
    slots = [
        s
        for s in avail["slots"]
        if datetime.fromisoformat(s) > datetime.now(UTC) + timedelta(hours=3)
    ]
    assert len(slots) >= 2, avail
    start = slots[0]
    booked = await call.tools.book_appointment(start, "Sam Patel", None, "annual boiler service")
    assert booked["status"] == "booked" and booked["booking_id"]
    # the same slot cannot be double-booked by the next caller
    other = LiveCall(client, app, cfg, "e2e-book-2", "+447700900322")
    clash = await other.tools.book_appointment(start, "Jo", None, None)
    assert clash["status"] == "failed"
    await call.end()

    bookings = (await client.get("/v1/calendar/bookings", params=Q)).json()
    assert any(b["id"] == booked["booking_id"] and b["phone"] == CALLER for b in bookings)
    rems = (await client.get("/v1/reminders", params=Q)).json()
    mine = [x for x in rems if x["booking_id"] == booked["booking_id"]]
    assert [x["status"] for x in mine] == ["scheduled", "scheduled"]

    # first reminder goes out at the configured offset; "1" confirms
    svc: ReminderService = app.state.reminders
    when = datetime.fromisoformat(start)
    assert await svc.sweep(when - timedelta(hours=1, minutes=59)) == 1
    txt = [m for m in prov.sent if m[1] == CALLER][-1][2]
    assert "Reply 1 to confirm or 2 to reschedule" in txt
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("1", frm=CALLER))
    assert r.status_code == 202
    rems = (await client.get("/v1/reminders", params=Q)).json()
    assert "confirmed" in [x["status"] for x in rems if x["booking_id"] == booked["booking_id"]]
    bookings = (await client.get("/v1/calendar/bookings", params=Q)).json()
    assert next(b for b in bookings if b["id"] == booked["booking_id"])["status"] == "confirmed"

    # a second booking: "2" raises a reschedule ticket and cancels the remaining reminder
    start2 = slots[1]
    second = LiveCall(client, app, cfg, "e2e-book-3", "+447700900333")
    b2 = await second.tools.book_appointment(start2, "Ann", None, None)
    assert b2["status"] == "booked"
    when2 = datetime.fromisoformat(start2)
    await svc.sweep(when2 - timedelta(hours=1, minutes=59))
    before = len((await client.get("/v1/tickets", params=Q)).json())
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("2", frm="+447700900333"))
    assert r.status_code == 202
    tickets = (await client.get("/v1/tickets", params=Q)).json()
    assert len(tickets) == before + 1
    assert any("reschedul" in (t["reason"] or "").lower() for t in tickets)
    rems = (await client.get("/v1/reminders", params=Q)).json()
    st = {x["status"] for x in rems if x["booking_id"] == b2["booking_id"]}
    assert "cancelled" in st and "scheduled" not in st

    # STOP suppresses every further text to that number
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("STOP", frm=CALLER))
    assert r.status_code == 202
    n = len([m for m in prov.sent if m[1] == CALLER])
    assert await svc.sweep(when - timedelta(minutes=59)) == 0
    assert len([m for m in prov.sent if m[1] == CALLER]) == n
    ov = await _overview(client)
    assert ov["usage"]["bookings"] >= 2 and ov["usage"]["sms"] >= 1


# -- screening & spam ---------------------------------------------------------------------------


async def test_screening_blocks_spam_screens_unknown_and_passes_known(
    client: AsyncClient, app: FastAPI
) -> None:
    cfg = await _resolve(client, FORWARDED_NUMBER)
    cfg.screening.mode = ScreeningMode.UNKNOWN
    cfg.screening.block_withheld = True
    cfg = await _save(client, cfg, [FORWARDED_NUMBER])

    async def verdict(caller: str | None) -> dict[str, Any]:
        params: dict[str, str] = {"assistant_id": cfg.assistant_id}
        if caller is not None:
            params["caller"] = caller
        r = await client.get("/v1/worker/screening", params=params, headers=HEADERS)
        assert r.status_code == 200, r.text
        return dict(r.json())

    # withheld -> rejected before the assistant answers
    v = await verdict("anonymous")
    assert v["action"] == "reject"
    c = LiveCall(client, app, cfg, "e2e-scr-withheld", None)
    await c.start(FORWARDED_NUMBER)
    await c.end(reason="screened", duration_s=0)
    assert (await client.get("/v1/calls/e2e-scr-withheld", params=Q)).json()["kind"] == "blocked"

    # tenant spam list -> rejected
    r = await client.post(
        "/v1/screening/spam", params=Q, json={"e164": "+447700900666", "reason": "sales calls"}
    )
    assert r.status_code == 201
    assert (await verdict("+447700900666"))["action"] == "reject"

    # unknown caller -> screened first, then helped
    v = await verdict(CALLER)
    assert v["action"] == "screen" and v["known_name"] is None
    c = LiveCall(client, app, cfg, "e2e-scr-unknown", CALLER)
    await c.start(FORWARDED_NUMBER, screening={"action": "screen"})
    await c.answer()
    await c.turn("It's Keith about a boiler quote", "Thanks Keith, putting you through.")
    await c.end(duration_s=40)
    rec = (await client.get("/v1/calls/e2e-scr-unknown", params=Q)).json()
    assert rec["status"] == "completed" and rec["kind"] != "blocked"

    # a nameless prospect is still screened next time; once the team marks them a customer
    # (manual override on Contacts) they go straight through
    assert (await verdict(CALLER))["action"] == "screen"
    contacts = (await client.get("/v1/contacts", params={**Q, "q": CALLER[-6:]})).json()
    assert len(contacts) == 1 and contacts[0]["status"] == "prospect"
    r = await client.patch(
        f"/v1/contacts/{contacts[0]['id']}", json={"name": "Keith Wilson", "status": "customer"}
    )
    assert r.status_code == 200
    v = await verdict(CALLER)
    assert v["action"] == "allow" and v["known_name"] == "Keith Wilson"
    assert v["contact_status"] == "customer"
    # allow-listed number -> straight through even when unknown
    cfg.screening.allow_numbers = ["+447700900777"]
    cfg = await _save(client, cfg, [FORWARDED_NUMBER])
    assert (await verdict("+447700900777"))["action"] == "allow"
    # tenant blocked list still wins
    cfg.blocked_numbers = ["+447700900777"]
    cfg = await _save(client, cfg, [FORWARDED_NUMBER])
    assert (await verdict("+447700900777"))["action"] == "reject"

    blocked = (await client.get("/v1/calls", params={**Q, "kind": "blocked"})).json()
    assert any(b["call_id"] == "e2e-scr-withheld" for b in blocked)
    ov = await _overview(client)
    assert ov["current"]["blocked"] >= 1


# -- text channels: SMS, WhatsApp, web chat -----------------------------------------------------


async def test_sms_whatsapp_webchat_threads_handoff_ticket_and_usage(
    client: AsyncClient, app: FastAPI
) -> None:
    _sms(app)
    cfg = await _resolve(client, FORWARDED_NUMBER)
    cfg.faqs.append(
        cfg.faqs[0].model_copy(
            update={
                "question": "Do you offer free parking?",
                "answer": "Yes, free parking on site.",
            }
        )
    )
    await _save(client, cfg, [FORWARDED_NUMBER])

    # SMS
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("do you have free parking?"))
    assert r.status_code == 202, r.text
    # WhatsApp
    r = await client.put(
        "/v1/inbox/whatsapp",
        params=Q,
        json={"phone_number_id": "1234567890", "display_number": DDI},
    )
    assert r.status_code == 200
    r = await client.post("/v1/inbound/whatsapp", json=meta_wa("is parking free?"))
    assert r.status_code == 200 and r.json()["handled"] == 1
    # web chat -> AI answers, then visitor asks for a person -> handoff -> human replies
    token = (await client.get("/v1/inbox/widget", params=Q)).json()["token"]
    visitor = "visitor-e2e000000001"
    r = await client.post(
        f"/v1/public/chat/{token}/messages",
        json={"visitor": visitor, "text": "is parking free?", "name": "Jo"},
    )
    assert r.status_code == 200 and [m["author"] for m in r.json()] == ["contact", "ai"]
    r = await client.post(
        f"/v1/public/chat/{token}/messages",
        json={"visitor": visitor, "text": "can I speak to a person about a leak please"},
    )
    assert r.status_code == 200
    threads = (await client.get("/v1/inbox/threads", params=Q)).json()
    by_channel = {t["channel"]: t for t in threads}
    assert {"sms", "whatsapp", "webchat"} <= set(by_channel)
    wc = by_channel["webchat"]
    assert wc["status"] in ("waiting", "open")
    r = await client.post(
        f"/v1/inbox/threads/{wc['id']}/reply", params=Q, json={"text": "Hi Jo, Dave here."}
    )
    assert r.status_code == 200
    r = await client.get(f"/v1/public/chat/{token}/messages", params={"visitor": visitor})
    assert r.json()[-1]["author"] == "agent" and "Dave" in r.json()[-1]["text"]

    stats = (await client.get("/v1/inbox/stats", params=Q)).json()
    assert stats["by_channel"].get("sms") == 1 and stats["by_channel"].get("whatsapp") == 1
    assert stats["by_channel"].get("webchat") == 1
    ov = await _overview(client)
    assert ov["usage"]["sms"] >= 1 and ov["usage"]["whatsapp"] >= 1
    assert ov["usage"]["web_chats"] >= 1

    # tenant isolation: another tenant sees none of it
    r = await client.get("/v1/inbox/threads", params={"tenant_id": "other"})
    assert r.status_code == 403 or r.json() == []


# -- region profiles: GB and US ---------------------------------------------------------------


async def test_us_region_profile_number_voice_timezone_and_analytics(
    client: AsyncClient, app: FastAPI
) -> None:
    """Platform owner puts the tenant in the US market: a US number routes to it, the voice
    catalogue leads with American voices, and the assistant's opening hours / analytics use the
    tenant's timezone."""
    r = await client.put(
        f"/v1/admin/tenants/{DEV_TENANT}/locale", json={"market": "us"}, headers=OWNER
    )
    assert r.status_code == 200 and r.json()["market"] == "US"
    voices = (await client.get("/v1/voices")).json()
    assert voices["market"] == "US" and voices["accents"][0] == "American"

    us_number = "+12125550100"
    cfg = await _cfg(client)
    cfg.hours.timezone = "America/New_York"
    cfg = await _save(client, cfg, [FORWARDED_NUMBER, us_number])
    assert cfg.hours.timezone == "America/New_York"
    resolved = await _resolve(client, us_number)
    assert resolved.assistant_id == cfg.assistant_id

    # 14:00 UTC Monday = 10:00 New York: open; 02:00 UTC = 22:00 New York the night before: closed
    assert cfg.hours.is_open(datetime(2026, 9, 14, 14, 0, tzinfo=UTC))
    assert not cfg.hours.is_open(datetime(2026, 9, 14, 2, 0, tzinfo=UTC))

    call = LiveCall(
        client,
        app,
        resolved,
        "e2e-us-1",
        "+13105550199",
        now=datetime(2026, 9, 14, 14, 0, tzinfo=UTC),
    )
    await call.start(us_number)
    await call.answer(0.45)
    await call.turn("Hi, do you service water heaters?", "We do — I can book that for you.")
    ticket = await call.tools.create_ticket(
        reason="Water heater service",
        caller_name="Alex",
        callback_number="(310) 555-0199",
        urgency="normal",
        callback_window=None,
        department=None,
    )
    assert ticket["caller_number"] == "+13105550199"
    await call.end(duration_s=80)

    ov = await _overview(client, "America/New_York")
    assert ov["timezone"] == "America/New_York"
    ins = await _insights(client, "America/New_York")
    assert ins["timezone"] == "America/New_York"
    # the call lands in the New York local hour/weekday bucket, not the UTC one
    local = datetime.now(ZoneInfo("America/New_York"))
    assert ins["demand"]["heatmap"][local.weekday()][local.hour] >= 1

    # back to GB: British voices lead again
    r = await client.put(
        f"/v1/admin/tenants/{DEV_TENANT}/locale", json={"market": "gb"}, headers=OWNER
    )
    assert r.status_code == 200
    voices = (await client.get("/v1/voices")).json()
    assert voices["market"] == "GB" and voices["accents"][0] == "British"


# -- compliance: the whole journey can be exported and erased per caller ----------------------


async def test_gdpr_export_and_erase_cover_calls_tickets_and_messages(
    client: AsyncClient, app: FastAPI
) -> None:
    _sms(app)
    cfg = await _resolve(client, FORWARDED_NUMBER)
    call = LiveCall(client, app, cfg, "e2e-gdpr-1", CALLER)
    await call.start(FORWARDED_NUMBER)
    await call.answer()
    await call.turn("It's Keith, I need a quote", "Sure Keith.")
    await call.tools.create_ticket("Quote", "Keith", CALLER, "normal", None, None)
    await call.end()
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("hello", frm=CALLER))
    assert r.status_code == 202

    r = await client.post("/v1/compliance/export", params=Q, json={"e164": CALLER})
    assert r.status_code == 200, r.text
    exp = r.json()
    assert exp["calls"] and exp["tickets"]
    r = await client.post("/v1/compliance/erase", params=Q, json={"e164": CALLER})
    assert r.status_code == 200, r.text
    assert r.json()["calls_purged"] >= 1 and r.json()["tickets_anonymised"] >= 1
    r = await client.post("/v1/compliance/export", params=Q, json={"e164": CALLER})
    assert r.json()["calls"] == [] and r.json()["tickets"] == []
    audit = (await client.get("/v1/audit", params=Q)).json()
    assert any(a["action"] == "compliance.subject.erase" for a in audit)


# -- latency budget: the worker's answer latency is metered per call ---------------------------


@pytest.mark.parametrize("backend", ["memory"], indirect=True)
async def test_answer_latency_is_reported_per_region_profile(
    client: AsyncClient, app: FastAPI
) -> None:
    cfg = await _resolve(client, FORWARDED_NUMBER)
    for i, lat in enumerate((0.31, 0.42, 0.38)):
        c = LiveCall(client, app, cfg, f"e2e-lat-{i}", f"+44770090040{i}")
        await c.start(FORWARDED_NUMBER)
        await c.answer(lat)
        await c.end(duration_s=20, latency={"p50_s": 0.9, "p95_s": 1.4})
    r = await client.get("/v1/observability/latency", params=Q)
    assert r.status_code == 200, r.text
    body = r.json()["overall"]
    assert body["answered"] >= 3
    # Part F budget: pick-up under 1s, turn p95 under 2.5s (runbook P2 threshold)
    assert body["answer_p95_s"] is not None and body["answer_p95_s"] < 1.0
    assert body["turn_p95_s"] is not None and body["turn_p95_s"] < 2.5
    assert body["slow_calls"] == 0
