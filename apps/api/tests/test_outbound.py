"""Phase 9 outbound & speed-to-lead — policy gates, scheduling, retries, automations, routes.

Everything runs against the simulated dialer (Telnyx approval pending): the dialer records the
request it would have sent and the tests drive call.started / call.ended through the worker event
route exactly as the voice worker does.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.outbound import (
    OutboundCall,
    OutboundPolicy,
    OutboundService,
    OutboundStatus,
    Outcome,
    Purpose,
    SimulatedDialer,
    build_script,
    jurisdiction_for,
    normalise_phone,
    parse_callback_window,
)
from parlio_voice.models import CallEventType

from .test_api import HEADERS, ev

Q = {"tenant_id": "demo"}
LEAD = {"name": "Lena Lead", "phone": "07700 900123", "interest": "rewire quote"}


def _svc(app: FastAPI) -> OutboundService:
    svc: OutboundService = app.state.outbound
    return svc


def _dialer(app: FastAPI) -> SimulatedDialer:
    d = _svc(app).dialer
    assert isinstance(d, SimulatedDialer)
    return d


async def _open_window(app: FastAPI) -> OutboundPolicy:
    """Policy that is always inside the calling window so tests do not depend on wall-clock."""
    svc = _svc(app)
    p = await svc.policy("demo")
    p.window_start, p.window_end = datetime.min.time(), datetime.max.time().replace(microsecond=0)
    p.days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    return await svc.save_policy(p)


async def _end_call(
    client: AsyncClient, call_id: str, *, answered: bool, reason: str = "hangup"
) -> None:
    await client.post(
        "/v1/worker/events",
        headers=HEADERS,
        json=ev(
            CallEventType.CALL_STARTED,
            call_id,
            {"caller": "+442046206823", "dialed": "+447700900123", "direction": "outbound"},
        ),
    )
    if answered:
        await client.post(
            "/v1/worker/events", headers=HEADERS, json=ev(CallEventType.CALL_ANSWERED, call_id, {})
        )
    r = await client.post(
        "/v1/worker/events",
        headers=HEADERS,
        json=ev(CallEventType.CALL_ENDED, call_id, {"reason": reason}),
    )
    assert r.status_code == 202, r.text


# -- pure rules ------------------------------------------------------------------------------------


def test_phone_normalisation_and_jurisdiction() -> None:
    assert normalise_phone("07700 900123") == "+447700900123"
    assert normalise_phone("0044 20 4620 6823") == "+442046206823"
    assert normalise_phone("+1 (212) 555-0100") == "+12125550100"
    assert jurisdiction_for("+447700900123") == "GB"
    assert jurisdiction_for("+35312345678") == "IE"
    assert jurisdiction_for("+4930123456") is None


def test_uk_calling_window_defers_sunday_and_night() -> None:
    p = OutboundPolicy(tenant_id="t")  # GB default: 08:00-20:00 Mon-Sat, Europe/London
    sunday_noon = datetime(2026, 9, 13, 11, 0, tzinfo=UTC)
    assert not p.in_window(sunday_noon)
    nxt = p.next_window(sunday_noon)
    assert nxt.astimezone(p.tz()).strftime("%a %H:%M") == "Mon 08:00"
    late = datetime(2026, 9, 11, 20, 30, tzinfo=UTC)  # 21:30 BST Friday
    assert not p.in_window(late)
    assert p.next_window(late).astimezone(p.tz()).strftime("%a %H:%M") == "Sat 08:00"
    ok = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
    assert p.in_window(ok) and p.next_window(ok) == ok


def test_parse_callback_window() -> None:
    p = OutboundPolicy(tenant_id="t")
    now = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
    assert parse_callback_window("asap", p, now) == now
    assert parse_callback_window("this afternoon", p, now) > now
    tomorrow = parse_callback_window("tomorrow morning", p, now)
    assert tomorrow.date() == (now + timedelta(days=1)).date()


# -- speed to lead ---------------------------------------------------------------------------------


async def test_lead_captured_and_dialled_within_target(client: AsyncClient, app: FastAPI) -> None:
    await _open_window(app)
    r = await client.post("/v1/outbound/leads", params=Q, json=LEAD)
    assert r.status_code == 201, r.text
    lead, job = r.json()["lead"], r.json()["call"]
    assert lead["phone"] == "+447700900123" and lead["status"] == "new"
    assert job["status"] == "scheduled" and job["purpose"] == "lead_followup"
    assert job["context"]["interest"] == "rewire quote"

    n = await app.state.outbound_loop.tick()
    assert n == 1
    dialer = _dialer(app)
    assert len(dialer.dials) == 1
    sent, script = dialer.dials[0]
    assert sent.to == "+447700900123" and "Lena" in script["opening"]

    r = await client.get("/v1/outbound/leads", params=Q)
    lead = r.json()[0]
    assert lead["status"] == "calling" and lead["speed_to_lead_s"] is not None
    assert lead["speed_to_lead_s"] < 60

    call_id = f"sim-{sent.id}-1"
    r = await client.post(
        f"/v1/worker/outbound/{sent.id}/outcome",
        headers=HEADERS,
        json={"outcome": "booked", "detail": "Tue 10:00 survey"},
    )
    assert r.status_code == 200
    await _end_call(client, call_id, answered=True)

    r = await client.get("/v1/outbound/calls", params=Q)
    job = r.json()[0]
    assert job["status"] == "completed" and job["outcome"] == "booked"
    lead = (await client.get("/v1/outbound/leads", params=Q)).json()[0]
    assert lead["status"] == "booked"

    stats = (await client.get("/v1/outbound/summary", params=Q)).json()
    assert stats["leads"] == 1 and stats["by_outcome"]["booked"] == 1
    assert stats["speed_to_lead_within_target"] == 1.0

    # the call shows up as outbound in the regular calls list
    r = await client.get("/v1/calls", params=Q)
    assert any(c["call_id"] == call_id and c["direction"] == "outbound" for c in r.json())


async def test_no_answer_retries_then_exhausts_into_ticket(
    client: AsyncClient, app: FastAPI
) -> None:
    p = await _open_window(app)
    p.retry_gap_min, p.daily_cap_per_number = 0, 10
    await _svc(app).save_policy(p)
    r = await client.post("/v1/outbound/leads", params=Q, json=LEAD)
    job_id = r.json()["call"]["id"]

    for attempt in range(1, 4):
        assert await app.state.outbound_loop.tick() == 1
        await _end_call(client, f"sim-{job_id}-{attempt}", answered=False, reason="no_answer")
        job = (await client.get("/v1/outbound/calls", params=Q)).json()[0]
        assert len(job["attempts"]) == attempt
        assert job["status"] == ("retry" if attempt < 3 else "exhausted")

    lead = (await client.get("/v1/outbound/leads", params=Q)).json()[0]
    assert lead["status"] == "unreachable"
    tickets = (await client.get("/v1/tickets", params=Q)).json()
    assert any(
        t["source"] == "outbound_unreachable" and "Lena" in (t["caller_name"] or "")
        for t in tickets
    )
    assert (await client.get("/v1/outbound/calls", params=Q)).json()[0]["id"] == job_id


async def test_opt_out_suppresses_and_blocks_future_leads(
    client: AsyncClient, app: FastAPI
) -> None:
    await _open_window(app)
    r = await client.post("/v1/outbound/leads", params=Q, json=LEAD)
    job_id = r.json()["call"]["id"]
    await app.state.outbound_loop.tick()
    r = await client.post(
        f"/v1/worker/outbound/{job_id}/outcome", headers=HEADERS, json={"outcome": "opt_out"}
    )
    assert r.status_code == 200
    await _end_call(client, f"sim-{job_id}-1", answered=True)

    sup = (await client.get("/v1/outbound/suppressions", params=Q)).json()
    assert [s["phone"] for s in sup] == ["+447700900123"] and sup[0]["source"] == "call"
    lead = (await client.get("/v1/outbound/leads", params=Q)).json()[0]
    assert lead["status"] == "opted_out"

    # a fresh lead for the same number never dials
    r = await client.post("/v1/outbound/leads", params=Q, json=LEAD)
    assert r.json()["call"]["status"] == "suppressed"
    assert r.json()["lead"]["status"] == "opted_out"
    assert await app.state.outbound_loop.tick() == 0

    # admin can remove it again
    r = await client.delete("/v1/outbound/suppressions", params={**Q, "phone": "07700900123"})
    assert r.status_code == 204
    assert (await client.get("/v1/outbound/suppressions", params=Q)).json() == []


async def test_consent_and_jurisdiction_gates(client: AsyncClient, app: FastAPI) -> None:
    r = await client.post("/v1/outbound/leads", params=Q, json={**LEAD, "consent": False})
    assert r.json()["call"]["status"] == "cancelled"
    assert "consent" in r.json()["call"]["reason"]
    r = await client.post("/v1/outbound/leads", params=Q, json={**LEAD, "phone": "+4930123456"})
    assert r.json()["call"]["status"] == "cancelled"
    assert "country" in r.json()["call"]["reason"]
    r = await client.post("/v1/outbound/leads", params=Q, json={**LEAD, "call_now": False})
    assert r.json()["call"] is None and r.json()["lead"]["status"] == "new"


async def test_window_and_daily_cap_defer_dialling(client: AsyncClient, app: FastAPI) -> None:
    svc = _svc(app)
    p = await svc.policy("demo")
    p.days = ["mon"]  # only Mondays 08:00-20:00
    await svc.save_policy(p)
    tue = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
    job = await svc.schedule(
        tenant_id="demo", assistant_id="demo", purpose=Purpose.REMINDER, to="07700900123", when=tue
    )
    assert job.status == OutboundStatus.SCHEDULED
    assert job.scheduled_at.astimezone(p.tz()).strftime("%a %H:%M") == "Mon 08:00"
    assert await svc.due(now=tue) == []
    # dispatch outside the window re-queues rather than dialling
    out = await svc.dispatch(job, now=tue)
    assert out.status == OutboundStatus.SCHEDULED and not _dialer(app).dials

    p = await _open_window(app)
    p.daily_cap_per_number = 1
    await svc.save_policy(p)
    j1 = await svc.schedule(
        tenant_id="demo", assistant_id="demo", purpose=Purpose.REMINDER, to="07700900123"
    )
    j2 = await svc.schedule(
        tenant_id="demo", assistant_id="demo", purpose=Purpose.CONFIRMATION, to="07700900123"
    )
    j1 = await svc.dispatch(j1)
    assert j1.status == OutboundStatus.DIALING
    j2 = await svc.dispatch(j2)
    assert j2.status == OutboundStatus.SCHEDULED and j2.reason and "cap" in j2.reason
    assert j2.scheduled_at > datetime.now(UTC) + timedelta(hours=23)


async def test_policy_disables_purpose_and_validation(client: AsyncClient, app: FastAPI) -> None:
    p = (await client.get("/v1/outbound/policy", params=Q)).json()
    assert p["jurisdiction"] == "GB" and p["form_token"].startswith("lf_")
    p["purposes"] = ["ticket_callback"]
    r = await client.put("/v1/outbound/policy", params=Q, json=p)
    assert r.status_code == 200
    r = await client.post("/v1/outbound/leads", params=Q, json=LEAD)
    assert r.json()["call"]["status"] == "cancelled" and "disabled" in r.json()["call"]["reason"]
    r = await client.put(
        "/v1/outbound/policy", params=Q, json={**p, "window_start": "21:00", "window_end": "09:00"}
    )
    assert r.status_code == 400
    r = await client.put("/v1/outbound/policy", params=Q, json={**p, "tenant_id": "other"})
    assert r.status_code == 400


# -- intake channels -------------------------------------------------------------------------------


async def test_public_form_and_inbound_api_intake(client: AsyncClient, app: FastAPI) -> None:
    await _open_window(app)
    token = (await client.get("/v1/outbound/policy", params=Q)).json()["form_token"]
    r = await client.post("/v1/public/leads/demo", json={**LEAD, "token": "wrong"})
    assert r.status_code == 401
    r = await client.post("/v1/public/leads/demo", json={**LEAD, "token": token})
    assert r.status_code == 201 and r.json()["call_scheduled"] is True

    r = await client.post("/v1/api-keys", params=Q, json={"name": "CRM"})
    key = r.json()["key"]
    r = await client.post(
        "/v1/inbound/leads",
        headers={"Authorization": f"Bearer {key}"},
        json={**LEAD, "phone": "07700900124", "source": "hubspot"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["lead"]["source"] == "hubspot" and r.json()["call"]["status"] == "scheduled"
    leads = (await client.get("/v1/outbound/leads", params=Q)).json()
    assert {x["source"] for x in leads} == {"web_form", "hubspot"}
    assert await app.state.outbound_loop.tick() == 2


# -- automations -----------------------------------------------------------------------------------


async def test_ticket_callback_scheduled_from_intake(client: AsyncClient, app: FastAPI) -> None:
    p = await _open_window(app)
    ticket = {
        "caller_name": "Tom",
        "caller_number": "07700900555",
        "reason": "boiler leak",
        "callback_window": "asap",
    }
    # default: callbacks stay with the team, nothing is queued for the assistant
    r = await client.post(
        "/v1/worker/tickets",
        headers=HEADERS,
        params={"tenant_id": "demo", "company_id": "demo", "call_id": "c-tk0"},
        json=ticket,
    )
    assert r.status_code == 201, r.text
    assert (await client.get("/v1/outbound/calls", params=Q)).json() == []

    p.auto_ticket_callbacks = True
    await _svc(app).save_policy(p)
    r = await client.post(
        "/v1/worker/tickets",
        headers=HEADERS,
        params={"tenant_id": "demo", "company_id": "demo", "call_id": "c-tk1"},
        json={
            "caller_name": "Tom",
            "caller_number": "07700900555",
            "reason": "boiler leak",
            "callback_window": "asap",
        },
    )
    assert r.status_code == 201, r.text
    jobs = (await client.get("/v1/outbound/calls", params=Q)).json()
    assert len(jobs) == 1 and jobs[0]["purpose"] == "ticket_callback"
    assert jobs[0]["ticket_id"] == r.json()["id"] and jobs[0]["context"]["reason"] == "boiler leak"

    # manual callback from the tickets board: refused without a resolution for the customer
    tid = jobs[0]["ticket_id"]
    r = await client.post(
        "/v1/outbound/calls",
        params=Q,
        json={"purpose": "ticket_callback", "to": "07700900555", "ticket_id": tid},
    )
    assert r.status_code == 400
    r = await client.post(
        "/v1/outbound/calls",
        params=Q,
        json={
            "purpose": "ticket_callback",
            "to": "07700900555",
            "ticket_id": tid,
            "resolution_kind": "transfer",
        },
    )
    assert r.status_code == 400
    r = await client.post(
        "/v1/outbound/calls",
        params=Q,
        json={
            "purpose": "ticket_callback",
            "to": "07700900555",
            "ticket_id": tid,
            "resolution": "An engineer is booked for 9am tomorrow; no call-out fee.",
        },
    )
    assert r.status_code == 201, r.text
    ctx = r.json()["context"]
    assert ctx["reason"] == "boiler leak" and ctx["resolution_kind"] == "answer"
    assert ctx["caller_name"] == "Tom" and ctx["callback_number"] == "07700900555"
    assert ctx["ticket_ref"].startswith("#")
    r = await client.post(f"/v1/outbound/calls/{r.json()['id']}/cancel", params=Q)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


async def test_ticket_callback_script_and_existing_ticket_updated(
    client: AsyncClient, app: FastAPI
) -> None:
    await _open_window(app)
    r = await client.post(
        "/v1/worker/tickets",
        headers=HEADERS,
        params={"tenant_id": "demo", "company_id": "demo", "call_id": "c-tk2"},
        json={"caller_name": "Keith", "caller_number": "07700900777", "reason": "Invoice query"},
    )
    tid = r.json()["id"]
    r = await client.post(
        "/v1/outbound/calls",
        params=Q,
        json={
            "purpose": "ticket_callback",
            "to": "07700900777",
            "name": "Keith",
            "ticket_id": tid,
            "resolution": "The invoice has been corrected to £340.",
        },
    )
    assert r.status_code == 201, r.text
    job = OutboundCall.model_validate(r.json())
    cfg = await app.state.store.get_assistant(job.assistant_id)
    script = build_script(job, cfg)
    assert "regarding invoice query" in script["opening"]
    ins = script["instructions"]
    assert "NEVER call create_ticket" in ins and "£340" in ins and "Keith" in ins
    assert "record_outcome('resolved')" in ins

    svc = _svc(app)
    await app.state.outbound_loop.tick()
    job = (await svc.get("demo", job.id)) or job
    assert job.status == OutboundStatus.DIALING and job.call_id
    await client.post(
        f"/v1/worker/outbound/{job.id}/outcome",
        headers=HEADERS,
        json={"outcome": "resolved", "detail": "customer happy with corrected invoice"},
    )
    await _end_call(client, job.call_id, answered=True)
    job = (await svc.get("demo", job.id)) or job
    assert job.outcome == Outcome.RESOLVED
    detail = (await client.get(f"/v1/tickets/{tid}")).json()
    assert detail["ticket"]["status"] == "resolved"
    notes = [e["note"] for e in detail["events"] if e["note"]]
    assert any("AI call back" in n and "resolved" in n for n in notes)
    # no second ticket for the same customer
    tickets = (await client.get("/v1/tickets", params=Q)).json()
    assert sum(1 for t in tickets if t["caller_number"].endswith("900777")) == 1


async def test_stale_dial_times_out(client: AsyncClient, app: FastAPI) -> None:
    p = await _open_window(app)
    svc = _svc(app)
    job = await svc.schedule(
        tenant_id="demo",
        assistant_id="demo",
        purpose=Purpose.TICKET_CALLBACK,
        to="07700900555",
        when=datetime.now(UTC),
    )
    job = await svc.dispatch(job)
    assert job.status == OutboundStatus.DIALING
    assert await svc.expire_stale() == []
    late = datetime.now(UTC) + timedelta(minutes=p.stale_dial_min + 1)
    (expired,) = await svc.expire_stale(late)
    assert expired.id == job.id and expired.status == OutboundStatus.RETRY
    assert expired.attempts[-1].outcome == Outcome.FAILED and expired.reason == "dial timed out"


async def test_dial_now_overrides_window_and_cap(client: AsyncClient, app: FastAPI) -> None:
    svc = _svc(app)
    p = await svc.policy("demo")
    today = datetime.now(UTC).strftime("%a").lower()
    p.timezone = "UTC"
    p.days = [d for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] if d != today]
    p.daily_cap_per_number = 0
    await svc.save_policy(p)
    job = await svc.schedule(
        tenant_id="demo",
        assistant_id="demo",
        purpose=Purpose.TICKET_CALLBACK,
        to="07700900555",
        when=datetime.now(UTC),
    )
    assert (await svc.dispatch(job)).status == OutboundStatus.SCHEDULED  # loop defers
    r = await client.post(f"/v1/outbound/calls/{job.id}/dial-now", params=Q)
    assert r.status_code == 200 and r.json()["status"] == "dialing"

    await svc.suppress("demo", "07700900556", reason="asked", source="test")
    job = await svc.schedule(
        tenant_id="demo",
        assistant_id="demo",
        purpose=Purpose.TICKET_CALLBACK,
        to="07700900556",
        when=datetime.now(UTC),
    )
    assert job.status == OutboundStatus.SUPPRESSED
    r = await client.post(f"/v1/outbound/calls/{job.id}/dial-now", params=Q)
    assert r.status_code == 409


async def test_booking_schedules_reminder_and_review_then_no_show(
    client: AsyncClient, app: FastAPI
) -> None:
    p = await _open_window(app)
    svc = _svc(app)
    p.purposes.append(Purpose.REVIEW_REQUEST)
    await svc.save_policy(p)
    start = datetime.now(UTC) + timedelta(days=3)
    jobs = await svc.on_booking(
        tenant_id="demo",
        assistant_id="demo",
        booking_id="bk1",
        phone="07700900777",
        name="Rita",
        start=start,
    )
    kinds = {j.purpose for j in jobs}
    assert kinds == {Purpose.REMINDER, Purpose.REVIEW_REQUEST}
    reminder = next(j for j in jobs if j.purpose == Purpose.REMINDER)
    assert start - timedelta(hours=25) < reminder.scheduled_at <= start - timedelta(hours=23)
    review = next(j for j in jobs if j.purpose == Purpose.REVIEW_REQUEST)
    assert review.scheduled_at > start

    ns = await svc.on_no_show(
        tenant_id="demo", booking_id="bk1", phone="07700900777", name="Rita", start=start
    )
    assert ns is not None and ns.purpose == Purpose.NO_SHOW
    remaining = await svc.list_calls("demo")
    by_id: dict[str, OutboundCall] = {j.id: j for j in remaining}
    assert by_id[review.id].status == OutboundStatus.CANCELLED
    assert by_id[ns.id].status == OutboundStatus.SCHEDULED


async def test_sms_stop_keyword_suppresses(client: AsyncClient, app: FastAPI) -> None:
    svc = _svc(app)
    assert await svc.check_opt_out("demo", "07700900123", "please STOP calling me") is True
    assert await svc.is_suppressed("demo", "+447700900123")
    assert await svc.check_opt_out("demo", "07700900124", "thanks see you tuesday") is False


async def test_tenant_isolation(client: AsyncClient, app: FastAPI) -> None:
    await client.post("/v1/outbound/leads", params=Q, json=LEAD)
    r = await client.get("/v1/outbound/leads", params={"tenant_id": "other"})
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        assert r.json() == []
    job_id = (await client.get("/v1/outbound/calls", params=Q)).json()[0]["id"]
    r = await client.post(f"/v1/outbound/calls/{job_id}/cancel", params={"tenant_id": "other"})
    assert r.status_code in (403, 404)
